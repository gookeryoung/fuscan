"""``fuscan.extractors.ocr`` OCR 引擎管理测试。

覆盖线程局部单例（同线程复用、跨线程隔离）、懒加载、rapidocr/模型文件缺失降级、
:func:`recognize` 封装（文本拼接、空 txts、推理失败）。

rapidocr 未安装时通过注入 ``sys.modules`` 假模块与 mock ``_models_dir`` 覆盖逻辑分支，
不依赖真实模型文件（模型下载见 ocr-support-plan.md 遗留事项）。
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import Iterator

import pytest

from fuscan.extractors import ocr as ocr_mod
from fuscan.extractors.base import ExtractorError


@pytest.fixture(autouse=True)
def _reset_thread_local() -> Iterator[None]:
    """每测试后清理主线程局部引擎缓存，避免跨测试污染。

    线程局部存储（:data:`ocr_mod._thread_local`）跨测试复用会污染后续断言，
    故 autouse 清理。访问私有属性仅用于测试隔离（生产代码不应依赖）。
    """
    yield
    if hasattr(ocr_mod._thread_local, "engine"):
        delattr(ocr_mod._thread_local, "engine")


def _install_fake_rapidocr(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """注入假 rapidocr 模块，记录每次 RapidOCR 实例化。

    :return: 实例化记录列表（验证单例/线程隔离时断言长度）
    """
    instances: list[object] = []

    class _FakeRapidOCR:
        def __init__(self, params: dict[str, str]) -> None:
            self.params = params
            self.tid = threading.get_ident()
            instances.append(self)

    mod = types.ModuleType("rapidocr")
    mod.RapidOCR = _FakeRapidOCR  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    return instances


def _create_fake_models(tmp_path: object) -> None:
    """在临时目录创建 4 个空模型文件（det/cls/rec/keys）。"""
    for name in (ocr_mod._DET_MODEL, ocr_mod._CLS_MODEL, ocr_mod._REC_MODEL, ocr_mod._REC_KEYS):
        (tmp_path / name).write_bytes(b"fake")  # type: ignore[operator]


class TestIsOcrAvailable:
    def test_returns_false_when_rapidocr_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rapidocr 未安装时 :func:`is_ocr_available` 返回 False。"""
        monkeypatch.setitem(sys.modules, "rapidocr", None)
        assert ocr_mod.is_ocr_available() is False

    def test_returns_true_when_rapidocr_importable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rapidocr 可导入时 :func:`is_ocr_available` 返回 True。"""
        mod = types.ModuleType("rapidocr")
        monkeypatch.setitem(sys.modules, "rapidocr", mod)
        assert ocr_mod.is_ocr_available() is True


class TestGetOcrEngine:
    def test_rapidocr_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rapidocr 未安装时 :func:`get_ocr_engine` 抛 ExtractorError。"""
        monkeypatch.setitem(sys.modules, "rapidocr", None)
        with pytest.raises(ExtractorError, match="无可用 OCR 引擎"):
            ocr_mod.get_ocr_engine()

    def test_model_missing_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """rapidocr 可导入但模型文件缺失时抛 ExtractorError。"""
        _install_fake_rapidocr(monkeypatch)
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)
        with pytest.raises(ExtractorError, match="OCR 模型文件缺失"):
            ocr_mod.get_ocr_engine()

    def test_partial_model_missing_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """仅部分模型文件存在时仍抛 ExtractorError。"""
        _install_fake_rapidocr(monkeypatch)
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)
        # 仅创建 det 模型，其余缺失
        (tmp_path / ocr_mod._DET_MODEL).write_bytes(b"fake")  # type: ignore[operator]
        with pytest.raises(ExtractorError, match="OCR 模型文件缺失"):
            ocr_mod.get_ocr_engine()

    def test_thread_local_singleton(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """同线程多次调用返回同一实例（线程局部单例）。"""
        instances = _install_fake_rapidocr(monkeypatch)
        _create_fake_models(tmp_path)
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)

        e1 = ocr_mod.get_ocr_engine()
        e2 = ocr_mod.get_ocr_engine()
        assert e1 is e2
        assert len(instances) == 1  # 仅构建一次

    def test_engine_built_with_model_paths(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """RapidOCR 构造时接收正确的模型路径参数。"""
        _install_fake_rapidocr(monkeypatch)
        _create_fake_models(tmp_path)
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)

        engine = ocr_mod.get_ocr_engine()
        params = engine.params  # type: ignore[attr-defined]
        assert params["Det.model_path"].endswith(ocr_mod._DET_MODEL)  # type: ignore[arg-type]
        assert params["Cls.model_path"].endswith(ocr_mod._CLS_MODEL)  # type: ignore[arg-type]
        assert params["Rec.model_path"].endswith(ocr_mod._REC_MODEL)  # type: ignore[arg-type]
        assert params["Rec.rec_keys_path"].endswith(ocr_mod._REC_KEYS)  # type: ignore[arg-type]

    def test_failure_not_cached(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """引擎构建失败不缓存异常，下次调用重新尝试（便于用户装依赖后重试）。"""
        _install_fake_rapidocr(monkeypatch)
        # 模型缺失 → 抛异常
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)
        with pytest.raises(ExtractorError, match="OCR 模型文件缺失"):
            ocr_mod.get_ocr_engine()
        # 仍未缓存，再次调用仍抛
        with pytest.raises(ExtractorError, match="OCR 模型文件缺失"):
            ocr_mod.get_ocr_engine()

    def test_thread_local_isolation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        """不同线程获取不同引擎实例（线程局部隔离）。"""
        _install_fake_rapidocr(monkeypatch)
        _create_fake_models(tmp_path)
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path)

        results: dict[int, object] = {}

        def worker() -> None:
            results[threading.get_ident()] = ocr_mod.get_ocr_engine()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        engines = list(results.values())
        assert len(engines) == 2
        assert engines[0] is not engines[1]


class TestRecognize:
    """``recognize`` 封装测试：mock ``get_ocr_engine`` 避免 real 模型加载。"""

    @staticmethod
    def _make_engine(txts: tuple[str, ...] | None, *, fail: bool = False) -> object:
        class _FakeResult:
            def __init__(self) -> None:
                self.txts = txts

        class _FakeEngine:
            def __call__(self, img: object) -> object:
                if fail:
                    raise RuntimeError("onnxruntime 推理崩溃")
                return _FakeResult()

        return _FakeEngine()

    def test_recognize_returns_joined_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """recognize 拼接 txts 返回多行文本。"""
        monkeypatch.setattr(ocr_mod, "get_ocr_engine", lambda: self._make_engine(("第一行", "第二行")))
        assert ocr_mod.recognize("fake_img") == "第一行\n第二行"

    def test_recognize_empty_txts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """txts 为空元组时返回空字符串。"""
        monkeypatch.setattr(ocr_mod, "get_ocr_engine", lambda: self._make_engine(()))
        assert ocr_mod.recognize("fake_img") == ""

    def test_recognize_filters_empty_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """txts 含空字符串时过滤，仅拼接非空文本。"""
        monkeypatch.setattr(ocr_mod, "get_ocr_engine", lambda: self._make_engine(("有效", "", "另一段", "")))
        assert ocr_mod.recognize("fake_img") == "有效\n另一段"

    def test_recognize_no_txts_attr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """结果对象无 txts 属性（``getattr`` 返回 None）时返回空字符串。"""
        monkeypatch.setattr(ocr_mod, "get_ocr_engine", lambda: self._make_engine(None))
        assert ocr_mod.recognize("fake_img") == ""

    def test_recognize_inference_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """推理异常包装为 ExtractorError。"""
        monkeypatch.setattr(ocr_mod, "get_ocr_engine", lambda: self._make_engine((), fail=True))
        with pytest.raises(ExtractorError, match="OCR 推理失败"):
            ocr_mod.recognize("fake_img")

    def test_recognize_engine_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_ocr_engine 抛 ExtractorError 时 recognize 透传。"""

        def _raise() -> None:
            raise ExtractorError("无可用 OCR 引擎（rapidocr 未安装）")

        monkeypatch.setattr(ocr_mod, "get_ocr_engine", _raise)
        with pytest.raises(ExtractorError, match="无可用 OCR 引擎"):
            ocr_mod.recognize("fake_img")


class TestModelsDir:
    def test_models_dir_returns_path(self) -> None:
        """``_models_dir`` 返回 pathlib.Path 且为 fuscan.assets.ocr.models 资源目录。"""
        result = ocr_mod._models_dir()
        from pathlib import Path

        assert isinstance(result, Path)
        # 资源目录名应为 models（位于 fuscan/assets/ocr/models）
        assert result.name == "models"
