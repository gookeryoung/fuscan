"""本地 mock 环境：模拟 CI 中 OCR 模型下载与产物校验，验证脚本逻辑。

不依赖真实网络与真实模型文件，用本地 HTTP 服务器 + mock zip 完整复刻 CI 的
「下载 → SHA256 校验 → 原子替换」与「unzip -l | grep 产物校验」两条链路。

覆盖场景：

下载脚本（scripts/download_ocr_models.py）逻辑：
  S1 全新下载（4 文件就位 + SHA256 通过）
  S2 幂等跳过（已存在且校验通过）
  S3 --force 强制重下
  S4 --check 全部通过
  S5 SHA256 不匹配失败 + 既有文件原子保留（不破坏可用文件）
  S6 --check 缺失文件检出

CI 产物校验（release.yml 的 unzip -l | grep 片段）逻辑：
  W1/W2 wheel 正/负例（force-include 路径 grep 命中/漏检）
  P1/P2 fspack 便携包 正/负例（模型 + rapidocr + onnxruntime grep 命中/漏检）

可选 ``--build``：额外跑真实 ``uv build --wheel`` 端到端验证 force-include
（需真实模型就位，脚本会先调 download_ocr_models.py 下载）。

使用方式::

    uv run python scripts/mock_ci_ocr.py          # mock 全场景（默认，快速）
    uv run python scripts/mock_ci_ocr.py --build  # 额外真实 uv build 端到端

退出码：0 全部通过 / 1 任一场景失败。
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import importlib.util
import io
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typing_extensions import override

# 仓库根目录
ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_SCRIPT = ROOT / "scripts" / "download_ocr_models.py"
# 真实模型目录（仅 --build 模式使用）
REAL_MODELS_DIR = ROOT / "src" / "fuscan" / "assets" / "ocr" / "models"
# 占位文件大小阈值：<1KB 视为占位（真实 det 模型 ~4.7MB）
_PLACEHOLDER_SIZE = 1024


@dataclass
class Result:
    """单个验证场景结果。"""

    name: str
    passed: bool
    detail: str = ""


def _sha256_bytes(data: bytes) -> str:
    """计算字节串 SHA256（小写十六进制）。"""
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """计算文件 SHA256。"""
    return _sha256_bytes(path.read_bytes())


def _load_download_module() -> Any:
    """以模块形式加载 scripts/download_ocr_models.py（不触发 ``__main__``）。

    :return: 加载后的模块对象，可访问 ``_MODELS`` / ``ModelFile`` / ``main``
    """
    spec = importlib.util.spec_from_file_location("download_ocr_models", DOWNLOAD_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # 先注册到 sys.modules 再 exec：Python 3.10 dataclass 定义时需经 sys.modules
    # 解析 ``cls.__module__``，未注册会触发 AttributeError
    sys.modules["download_ocr_models"] = mod
    spec.loader.exec_module(mod)
    return mod


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """静默访问日志的 HTTP 请求处理器。"""

    @override
    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def _mock_server(serve_dir: Path) -> Iterator[int]:
    """启动本地 HTTP 服务器托管 serve_dir，yield 端口号，退出时关闭。

    :param serve_dir: 服务器根目录（文件名即 URL 末段）
    :yield: 监听端口号
    """
    handler = functools.partial(_SilentHandler, directory=str(serve_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _run_download(dl: Any, argv: list[str]) -> tuple[int, str]:
    """捕获 stdout 调用 ``dl.main(argv)``，返回 (退出码, 输出文本)。

    :param dl: download_ocr_models 模块
    :param argv: 传给 main 的参数列表
    :return: (exit_code, stdout)
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = dl.main(argv)
    return code, buf.getvalue()


def _ci_grep(zip_path: Path, needles: list[str]) -> dict[str, bool]:
    """复刻 CI ``unzip -l <zip> | grep -q <needle>`` 逻辑。

    zip 内任一条目名 **包含** needle 子串即视为命中（与 ``grep -q`` 子串匹配一致）。

    :return: ``{needle: 是否命中}``
    """
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    return {needle: any(needle in name for name in names) for needle in needles}


def phase_download_logic(dl: Any) -> list[Result]:
    """阶段一：下载脚本逻辑验证（本地 mock 服务器，无真实网络）。

    覆盖全新下载/幂等跳过/--force/--check/SHA 不匹配原子保留/缺失检出 6 个场景。
    """
    results: list[Result] = []
    # 4 个 mock 模型文件（确定性内容，避免真实模型体积）
    mock_contents: dict[str, bytes] = {
        "det.onnx": b"MOCK_DET_MODEL" * 64,
        "cls.onnx": b"MOCK_CLS" * 32,
        "rec.onnx": b"MOCK_REC_MODEL_DATA" * 128,
        "keys.txt": b"mock_dict_char\n" * 16,
    }

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        server_dir = tmp / "server"
        server_dir.mkdir()
        models_dir = tmp / "models"
        for name, data in mock_contents.items():
            (server_dir / name).write_bytes(data)

        with _mock_server(server_dir) as port:
            base = f"http://127.0.0.1:{port}"
            original = dl._MODELS
            # 替换 _MODELS 指向本地 mock 服务器（保留原始以供 --build 阶段使用）
            dl._MODELS = tuple(
                dl.ModelFile(name=name, url=f"{base}/{name}", sha256=_sha256_bytes(data), desc="mock")
                for name, data in mock_contents.items()
            )
            try:
                # S1 全新下载：4 文件就位 + SHA256 通过
                code, _ = _run_download(dl, ["--models-dir", str(models_dir)])
                ok = code == 0 and all(
                    (models_dir / n).exists() and _sha256_file(models_dir / n) == _sha256_bytes(d)
                    for n, d in mock_contents.items()
                )
                results.append(Result("S1 全新下载（4 文件就位 + SHA256 通过）", ok, f"exit={code}"))

                # S2 幂等跳过：已存在且校验通过
                code, out = _run_download(dl, ["--models-dir", str(models_dir)])
                results.append(Result("S2 幂等跳过（已存在且校验通过）", code == 0 and "跳过" in out, f"exit={code}"))

                # S3 --force 强制重下
                code, out = _run_download(dl, ["--models-dir", str(models_dir), "--force"])
                results.append(Result("S3 --force 强制重下", code == 0 and "完成" in out, f"exit={code}"))

                # S4 --check 全部通过
                code, _ = _run_download(dl, ["--models-dir", str(models_dir), "--check"])
                results.append(Result("S4 --check 全部通过（exit 0）", code == 0, f"exit={code}"))

                # S5 SHA256 不匹配：篡改 server 上 det 内容 → force 重下应失败，
                # 且本地 det 保留原始正确内容（原子替换：失败不破坏既有文件）
                det_expected = _sha256_bytes(mock_contents["det.onnx"])
                (server_dir / "det.onnx").write_bytes(b"TAMPERED_WRONG_BYTES_HERE")
                code, _ = _run_download(dl, ["--models-dir", str(models_dir), "--force"])
                local_det_ok = _sha256_file(models_dir / "det.onnx") == det_expected
                results.append(
                    Result(
                        "S5 SHA256 不匹配失败 + 既有文件原子保留",
                        code == 1 and local_det_ok,
                        f"exit={code} 本地det保留={local_det_ok}",
                    )
                )
                (server_dir / "det.onnx").write_bytes(mock_contents["det.onnx"])  # 恢复

                # S6 --check 缺失文件：删除本地 keys → 应 exit 1
                (models_dir / "keys.txt").unlink()
                code, _ = _run_download(dl, ["--models-dir", str(models_dir), "--check"])
                results.append(Result("S6 --check 缺失文件检出（exit 1）", code == 1, f"exit={code}"))
            finally:
                dl._MODELS = original
    return results


def phase_wheel_grep(real_model_names: list[str]) -> list[Result]:
    """阶段二：wheel 产物校验逻辑（mock wheel zip）。

    模拟 hatchling force-include 将模型置于 ``fuscan/assets/ocr/models/<name>``，
    验证 CI 的 ``unzip -l wheel | grep ocr/models/$m`` 正/负例。
    """
    results: list[Result] = []
    needles = [f"ocr/models/{n}" for n in real_model_names]
    det_name = next(n for n in real_model_names if "det" in n)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        # W1 正例：wheel 含全部 4 模型
        whl_ok = tmp / "fuscan-ok.whl"
        with zipfile.ZipFile(whl_ok, "w") as z:
            for n in real_model_names:
                z.writestr(f"fuscan/assets/ocr/models/{n}", b"mock")
            z.writestr("fuscan/__init__.py", b"")
        found = _ci_grep(whl_ok, needles)
        results.append(Result("W1 wheel 正例（4 模型 grep 命中）", all(found.values()), str(found)))

        # W2 负例：wheel 缺 det 模型 → grep 应漏检 det
        whl_miss = tmp / "fuscan-miss.whl"
        with zipfile.ZipFile(whl_miss, "w") as z:
            for n in real_model_names:
                if n != det_name:
                    z.writestr(f"fuscan/assets/ocr/models/{n}", b"mock")
        found2 = _ci_grep(whl_miss, needles)
        det_needle = f"ocr/models/{det_name}"
        ok = not found2[det_needle]  # det 未命中
        results.append(Result("W2 wheel 负例（缺 det 模型 grep 漏检）", ok, str(found2)))
    return results


def phase_pack_grep(det_model_name: str) -> list[Result]:
    """阶段三：fspack 便携包校验逻辑（mock zip）。

    模拟 fspack 便携包含模型 + rapidocr + onnxruntime，验证 CI 的
    ``unzip -l zip | grep <needle>`` 正/负例。
    """
    results: list[Result] = []
    needles = [det_model_name, "rapidocr", "onnxruntime"]

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        # P1 正例：便携包含模型 + 两个 OCR 依赖
        zip_ok = tmp / "pack-ok.zip"
        with zipfile.ZipFile(zip_ok, "w") as z:
            z.writestr(f"site-packages/fuscan/assets/ocr/models/{det_model_name}", b"mock")
            z.writestr("site-packages/rapidocr/__init__.py", b"")
            z.writestr("site-packages/onnxruntime/__init__.py", b"")
        found = _ci_grep(zip_ok, needles)
        results.append(Result("P1 便携包正例（模型 + 依赖 grep 命中）", all(found.values()), str(found)))

        # P2 负例：便携包缺 rapidocr → grep 应漏检 rapidocr
        zip_miss = tmp / "pack-miss.zip"
        with zipfile.ZipFile(zip_miss, "w") as z:
            z.writestr(f"site-packages/fuscan/assets/ocr/models/{det_model_name}", b"mock")
            z.writestr("site-packages/onnxruntime/__init__.py", b"")
        found2 = _ci_grep(zip_miss, needles)
        results.append(Result("P2 便携包负例（缺 rapidocr grep 漏检）", not found2["rapidocr"], str(found2)))
    return results


def phase_real_build(dl: Any, real_models: list[Any]) -> list[Result]:
    """阶段四（可选 --build）：真实 ``uv build --wheel`` 端到端验证 force-include。

    真实模型缺失/为占位时先调 download_ocr_models.py 下载，再构建 wheel 并 grep。
    """
    results: list[Result] = []
    det_path = REAL_MODELS_DIR / real_models[0].name

    # 模型缺失或为占位 → 先真实下载
    if not det_path.exists() or det_path.stat().st_size < _PLACEHOLDER_SIZE:
        code, _ = _run_download(dl, [])
        results.append(Result("B0 真实模型下载（构建前置）", code == 0, f"exit={code}"))
        if code != 0:
            return results

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        proc = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(tmp)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            results.append(Result("B1 真实 uv build --wheel", False, proc.stderr[-400:]))
            return results
        whls = list(tmp.glob("*.whl"))
        if not whls:
            results.append(Result("B1 真实 uv build --wheel", False, "无 wheel 产物"))
            return results
        needles = [f"ocr/models/{m.name}" for m in real_models]
        found = _ci_grep(whls[0], needles)
        results.append(Result("B1 真实 wheel 含 4 模型（force-include 端到端）", all(found.values()), str(found)))
    return results


def _print_report(results: list[Result]) -> None:
    """打印验证报告表。"""
    print("\n=== mock CI OCR 验证报告 ===")
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        line = f"[{tag}] {r.name}"
        if r.detail:
            line += f"  ({r.detail})"
        print(line)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n=== {passed}/{total} 通过 ===")


def main(argv: list[str] | None = None) -> int:
    """脚本入口：运行全部 mock 场景并报告。"""
    parser = argparse.ArgumentParser(description="本地 mock：模拟 CI OCR 下载与产物校验")
    parser.add_argument(
        "--build",
        action="store_true",
        help="额外跑真实 uv build --wheel 端到端验证（较慢，需网络下载真实模型）",
    )
    args = parser.parse_args(argv)

    dl = _load_download_module()
    real_models = list(dl._MODELS)
    real_names = [m.name for m in real_models]
    det_name = next(n for n in real_names if "det" in n)

    results: list[Result] = []
    results += phase_download_logic(dl)
    results += phase_wheel_grep(real_names)
    results += phase_pack_grep(det_name)
    if args.build:
        results += phase_real_build(dl, real_models)

    _print_report(results)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
