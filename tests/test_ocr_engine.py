"""``fuscan.extractors.ocr`` OCR 引擎管理测试（RapidOCR-json 预编译 exe 子进程后端）。

覆盖：

- :func:`is_ocr_available` / :func:`get_ocr_status`：exe 与模型文件就位探测（不启动子进程）
- :class:`OcrEngine` 生命周期：init 成功/失败、:meth:`recognize` 通信协议
  （code 100/101/错误码）、子进程崩溃检测、:meth:`stop` 幂等
- :func:`get_ocr_engine`：全局单例 + 崩溃恢复 + exe/模型缺失报错
- :func:`recognize` 模块级封装

通过注入 :class:`_FakeProc`（模拟 stdin/stdout 管道逐行 json 通信）替代真实
:func:`subprocess.Popen`，不依赖真实 exe/模型文件。
"""

from __future__ import annotations

import base64
import collections
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from fuscan.extractors import ocr as ocr_mod
from fuscan.extractors.base import ExtractorError

# exe 启动后默认输出的初始化行（版本行 + 就绪标志）
_DEFAULT_INIT_LINES: list[bytes] = [b"RapidOCR-json v0.2.0\n", b"OCR init completed.\n"]


class _FakeStdin:
    """模拟子进程 stdin：累计写入，flush 时触发响应准备。"""

    def __init__(self, proc: _FakeProc) -> None:
        self._proc = proc
        self._buf = bytearray()

    def write(self, data: bytes) -> int:
        self._buf.extend(data)
        return len(data)

    def flush(self) -> None:
        req = bytes(self._buf)
        self._buf.clear()
        self._proc._on_request(req)

    def close(self) -> None:
        self._buf.clear()


class _FakeStdout:
    """模拟子进程 stdout：按队列返回预置行，队列空时返回 EOF（b""）。"""

    def __init__(self, proc: _FakeProc) -> None:
        self._proc = proc

    def readline(self) -> bytes:
        return self._proc._next_line()

    def close(self) -> None:
        pass


class _FakeProc:
    """模拟 RapidOCR-json exe 子进程的 stdin/stdout 通信。

    :param init_lines: 启动后输出的行（默认版本行 + ``OCR init completed.``）
    :param respond: 请求响应回调，接收请求字节返回响应行字节；None 表示无响应（EOF）
    :param poll_value: ``poll()`` 返回值；None 表示存活（默认）
    """

    def __init__(
        self,
        *,
        init_lines: list[bytes] | None = None,
        respond: Callable[[bytes], bytes] | None = None,
        poll_value: int | None = None,
    ) -> None:
        self._lines: collections.deque[bytes] = collections.deque(
            init_lines if init_lines is not None else _DEFAULT_INIT_LINES
        )
        self._respond = respond
        self._poll_value = poll_value
        self.killed = False
        self.requests: list[bytes] = []
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)

    def _on_request(self, req: bytes) -> None:
        self.requests.append(req)
        if self._respond is not None:
            self._lines.append(self._respond(req))

    def _next_line(self) -> bytes:
        if self._lines:
            return self._lines.popleft()
        return b""  # EOF

    def poll(self) -> int | None:
        return self._poll_value

    def kill(self) -> None:
        self.killed = True
        self._poll_value = -9


def _patch_assets_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """让 OcrEngine 认为 exe 与模型均已就位（绕过文件存在性检查）。

    在 ``tmp_path`` 创建假 exe 文件并 patch ``_exe_path`` 指向它，
    同时 patch ``_has_models`` 返回 True。返回假 exe 路径。
    """
    exe = tmp_path / "RapidOCR-json.exe"
    exe.write_bytes(b"fake")
    monkeypatch.setattr(ocr_mod, "_exe_path", lambda: exe)
    monkeypatch.setattr(ocr_mod, "_has_models", lambda: True)
    return exe


def _install_fake_popen(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc) -> None:
    """注入 FakeProc 替代 subprocess.Popen。"""
    monkeypatch.setattr(ocr_mod.subprocess, "Popen", lambda *a, **k: proc)


@pytest.fixture(autouse=True)
def _reset_engine() -> Iterator[None]:
    """每测试后清理全局引擎缓存，避免跨测试污染。"""
    yield
    ocr_mod._engine = None


@pytest.fixture(autouse=True)
def _force_windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制 Windows 平台，使 OCR 平台检查通过。

    RapidOCR-json 仅提供 Windows exe，:func:`is_ocr_available` /
    :class:`OcrEngine` 在非 Windows 直接返回不可用。测试用 FakeProc 模拟
    子进程通信，不依赖真实平台，故统一 patch 为 win32 绕过平台门禁。
    """
    monkeypatch.setattr(ocr_mod.sys, "platform", "win32")


def _make_responder(response: bytes) -> Callable[[bytes], bytes]:
    """构造固定响应回调。"""
    return lambda _req: response


# ---- is_ocr_available ----


class TestIsOcrAvailable:
    def test_false_when_exe_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 不存在时返回 False。"""
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "nope.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path / "models")
        assert ocr_mod.is_ocr_available() is False

    def test_false_when_models_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 存在但模型不完整时返回 False。"""
        (tmp_path / "RapidOCR-json.exe").write_bytes(b"fake")
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "RapidOCR-json.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path / "models")  # 不存在
        assert ocr_mod.is_ocr_available() is False

    def test_true_when_all_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 与 4 个模型文件均存在时返回 True。"""
        (tmp_path / "RapidOCR-json.exe").write_bytes(b"fake")
        models = tmp_path / "models"
        models.mkdir()
        for name in (ocr_mod._DET_MODEL, ocr_mod._CLS_MODEL, ocr_mod._REC_MODEL, ocr_mod._REC_KEYS):
            (models / name).write_bytes(b"fake")
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "RapidOCR-json.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: models)
        assert ocr_mod.is_ocr_available() is True


# ---- get_ocr_status ----


class TestGetOcrStatus:
    def test_exe_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 缺失时 unavailable，原因指向引擎，依赖明细引擎项未就位。"""
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "nope.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path / "models")
        status = ocr_mod.get_ocr_status()
        assert status.available is False
        assert "引擎" in status.reason
        assert status.version == ""
        assert len(status.dependencies) == 2
        assert status.dependencies[0].installed is False  # 引擎
        assert status.dependencies[1].installed is False  # 模型（目录不存在）

    def test_models_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 存在但模型缺失时 unavailable，原因指向模型文件。"""
        (tmp_path / "RapidOCR-json.exe").write_bytes(b"fake")
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "RapidOCR-json.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: tmp_path / "models")
        status = ocr_mod.get_ocr_status()
        assert status.available is False
        assert "模型" in status.reason
        assert status.dependencies[0].installed is True  # 引擎就位
        assert status.dependencies[1].installed is False  # 模型未就位

    def test_all_available(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 与模型均就位时 available=True + 版本号 + 两项依赖就位。"""
        (tmp_path / "RapidOCR-json.exe").write_bytes(b"fake")
        models = tmp_path / "models"
        models.mkdir()
        for name in (ocr_mod._DET_MODEL, ocr_mod._CLS_MODEL, ocr_mod._REC_MODEL, ocr_mod._REC_KEYS):
            (models / name).write_bytes(b"fake")
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "RapidOCR-json.exe")
        monkeypatch.setattr(ocr_mod, "_models_dir", lambda: models)
        status = ocr_mod.get_ocr_status()
        assert status.available is True
        assert status.reason == ""
        assert status.version == ocr_mod._EXE_VERSION
        assert all(d.installed for d in status.dependencies)
        assert len(status.dependencies) == 2
        # 模型依赖 version 为「内置」
        assert status.dependencies[1].version == "内置"


# ---- OcrEngine 生命周期 ----


class TestOcrEngineInit:
    def test_init_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """正常初始化：读取版本行 + init ok 后就绪。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc()
        _install_fake_popen(monkeypatch, proc)
        engine = ocr_mod.OcrEngine()
        assert engine.is_alive() is True
        engine.stop()

    def test_init_failure_eof_before_ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """init 期间 stdout EOF（未输出就绪标志）→ 抛 ExtractorError。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc(init_lines=[b"RapidOCR-json v0.2.0\n"])  # 无 init ok，随后 EOF
        _install_fake_popen(monkeypatch, proc)
        with pytest.raises(ExtractorError, match="初始化失败"):
            ocr_mod.OcrEngine()

    def test_init_failure_proc_dead(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程启动即退出（poll 非 None）→ 抛 ExtractorError。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc(poll_value=1)  # 已退出
        _install_fake_popen(monkeypatch, proc)
        with pytest.raises(ExtractorError, match="初始化失败"):
            ocr_mod.OcrEngine()

    def test_exe_missing_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 不存在时抛 ExtractorError（不启动子进程）。"""
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "nope.exe")
        monkeypatch.setattr(ocr_mod, "_has_models", lambda: True)
        with pytest.raises(ExtractorError, match="OCR 引擎不存在"):
            ocr_mod.OcrEngine()

    def test_models_missing_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """模型不完整时抛 ExtractorError（不启动子进程）。"""
        (tmp_path / "RapidOCR-json.exe").write_bytes(b"fake")
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "RapidOCR-json.exe")
        monkeypatch.setattr(ocr_mod, "_has_models", lambda: False)
        with pytest.raises(ExtractorError, match="OCR 模型不完整"):
            ocr_mod.OcrEngine()

    def test_popen_oserror_wrapped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Popen 抛 OSError 时包装为 ExtractorError。"""
        _patch_assets_present(monkeypatch, tmp_path)

        def _raise(*a: object, **k: object) -> Any:
            raise OSError("启动失败")

        monkeypatch.setattr(ocr_mod.subprocess, "Popen", _raise)
        with pytest.raises(ExtractorError, match="启动失败"):
            ocr_mod.OcrEngine()


class TestOcrEngineRecognize:
    """``OcrEngine.recognize`` 通信协议测试。"""

    @staticmethod
    def _make_engine(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, respond: Callable[[bytes], bytes] | None
    ) -> tuple[ocr_mod.OcrEngine, _FakeProc]:
        """构造已初始化的引擎 + 关联 FakeProc。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc(respond=respond)
        _install_fake_popen(monkeypatch, proc)
        engine = ocr_mod.OcrEngine()
        return engine, proc

    def test_code_100_joins_text(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """code=100 时拼接 data 中各项 text。"""
        resp = (
            json.dumps({"code": 100, "data": [{"text": "第一行", "score": 0.9}, {"text": "第二行"}]}).encode() + b"\n"
        )
        engine, proc = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        assert engine.recognize(b"fake image") == "第一行\n第二行"
        # 校验请求格式：base64 编码图片字节
        req = json.loads(proc.requests[0])
        assert base64.b64decode(req["image_base64"]) == b"fake image"
        engine.stop()

    def test_code_101_returns_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """code=101（未识别到文字）返回空字符串。"""
        resp = b'{"code": 101, "data": "no text"}\n'
        engine, _ = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        assert engine.recognize(b"blank") == ""
        engine.stop()

    def test_code_100_empty_data(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """code=100 但 data 为空列表时返回空字符串。"""
        resp = b'{"code": 100, "data": []}\n'
        engine, _ = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        assert engine.recognize(b"x") == ""
        engine.stop()

    def test_code_100_filters_empty_text(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """data 中 text 为空字符串的项被过滤。"""
        resp = json.dumps({"code": 100, "data": [{"text": "有效"}, {"text": ""}, {"text": "另一段"}]}).encode() + b"\n"
        engine, _ = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        assert engine.recognize(b"x") == "有效\n另一段"
        engine.stop()

    def test_error_code_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """非 100/101 的错误码抛 ExtractorError。"""
        resp = b'{"code": 200, "data": "image decode failed"}\n'
        engine, _ = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        with pytest.raises(ExtractorError, match="OCR 失败"):
            engine.recognize(b"x")
        engine.stop()

    def test_bad_json_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """响应非合法 json 时抛 ExtractorError。"""
        resp = b"not a json\n"
        engine, _ = self._make_engine(monkeypatch, tmp_path, _make_responder(resp))
        with pytest.raises(ExtractorError, match="响应解析失败"):
            engine.recognize(b"x")
        engine.stop()

    def test_proc_dead_before_send_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """发送前子进程已退出 → 抛 ExtractorError。"""
        engine, proc = self._make_engine(monkeypatch, tmp_path, _make_responder(b'{"code":101}\n'))
        proc._poll_value = 1  # 模拟子进程退出
        with pytest.raises(ExtractorError, match="子进程已退出"):
            engine.recognize(b"x")
        engine.stop()

    def test_eof_response_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程无响应（readline 返回 EOF）→ 抛 ExtractorError。"""
        engine, _ = self._make_engine(monkeypatch, tmp_path, respond=None)  # 无响应 → EOF
        with pytest.raises(ExtractorError, match="无响应"):
            engine.recognize(b"x")
        engine.stop()


class TestOcrEngineStop:
    def test_stop_kills_proc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """stop 终止存活子进程。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc()
        _install_fake_popen(monkeypatch, proc)
        engine = ocr_mod.OcrEngine()
        engine.stop()
        assert proc.killed is True

    def test_stop_idempotent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """重复 stop 安全（不重复 kill）。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc()
        _install_fake_popen(monkeypatch, proc)
        engine = ocr_mod.OcrEngine()
        engine.stop()
        engine.stop()  # 第二次不应报错
        assert proc.killed is True

    def test_stop_already_dead_proc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程已退出时 stop 不调用 kill（幂等）。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc(poll_value=1)
        # 绕过 __init__ 直接构造 engine 验证 stop 对已退出进程安全
        engine = ocr_mod.OcrEngine.__new__(ocr_mod.OcrEngine)
        engine._proc = proc  # pyrefly: ignore [bad-assignment]
        engine._stopped = False
        engine._lock = threading.Lock()
        engine.stop()
        assert proc.killed is False  # 已退出，无需 kill


# ---- get_ocr_engine 单例 + 崩溃恢复 ----


class TestGetOcrEngine:
    def test_singleton_when_alive(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程存活时多次调用返回同一实例。"""
        _patch_assets_present(monkeypatch, tmp_path)
        _install_fake_popen(monkeypatch, _FakeProc())
        e1 = ocr_mod.get_ocr_engine()
        e2 = ocr_mod.get_ocr_engine()
        assert e1 is e2
        e1.stop()

    def test_crash_recovery_rebuilds(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """子进程退出后再次调用重建新实例。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc1 = _FakeProc()
        _install_fake_popen(monkeypatch, proc1)
        e1 = ocr_mod.get_ocr_engine()
        proc1._poll_value = 1  # 模拟崩溃
        # 第二次调用：e1 已死，重建
        proc2 = _FakeProc()
        _install_fake_popen(monkeypatch, proc2)
        e2 = ocr_mod.get_ocr_engine()
        assert e2 is not e1
        assert e2.is_alive() is True
        e2.stop()

    def test_exe_missing_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """exe 缺失时抛 ExtractorError。"""
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "nope.exe")
        monkeypatch.setattr(ocr_mod, "_has_models", lambda: True)
        with pytest.raises(ExtractorError, match="OCR 引擎不存在"):
            ocr_mod.get_ocr_engine()


# ---- 模块级 recognize ----


class TestRecognizeModule:
    def test_delegates_to_engine(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """模块级 recognize 委托给全局引擎。"""
        _patch_assets_present(monkeypatch, tmp_path)
        proc = _FakeProc(respond=_make_responder(b'{"code":100,"data":[{"text":"hi"}]}\n'))
        _install_fake_popen(monkeypatch, proc)
        assert ocr_mod.recognize(b"img") == "hi"
        ocr_mod.get_ocr_engine().stop()

    def test_engine_missing_propagates(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """引擎缺失时 recognize 透传 ExtractorError。"""
        monkeypatch.setattr(ocr_mod, "_exe_path", lambda: tmp_path / "nope.exe")
        monkeypatch.setattr(ocr_mod, "_has_models", lambda: True)
        with pytest.raises(ExtractorError, match="OCR 引擎不存在"):
            ocr_mod.recognize(b"img")
