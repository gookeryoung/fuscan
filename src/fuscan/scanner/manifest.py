"""增量扫描清单：文件指纹与上次扫描状态持久化。

从 :mod:`fuscan.scanner.result` 拆出（iter-142）：``FileFingerprint`` 与
:class:`IncrementalManifest` 是"增量扫描"概念的核心数据结构，与
:class:`fuscan.scanner.result.ScanReport` 等"扫描结果"数据结构职责正交。
拆分后 ``result`` 模块聚焦"扫描结果表示"，本模块聚焦"增量状态持久化"，
两者通过 :class:`fuscan.scanner.result.WalkResult.manifest` 字段关联。

模块级 JSON 助手（``_json_dumps`` / ``_json_dumps_bytes`` / ``_json_loads``）
为 :class:`IncrementalManifest` 与 :class:`fuscan.scanner.result.ScanReport`
共用，优先使用 ``orjson``（性能更优），回退到标准库 ``json``。

公共 API：

- :class:`FileFingerprint`：文件指纹（mtime, size）二元组
- :class:`IncrementalManifest`：增量扫描清单（相对路径 → 指纹 映射）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import orjson

    def _json_dumps(data: dict[str, Any]) -> str:
        """高性能 JSON 序列化（orjson，带缩进）。"""
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode("utf-8")

    def _json_dumps_bytes(data: dict[str, Any]) -> bytes:
        """高性能 JSON 序列化为字节串（避免 str 解码开销，直接写文件）。"""
        return orjson.dumps(data, option=orjson.OPT_INDENT_2)

    def _json_loads(data: str | bytes) -> dict[str, Any]:
        """高性能 JSON 反序列化（orjson，接受 str 或 bytes）。"""
        result = orjson.loads(data)
        if not isinstance(result, dict):
            raise ValueError("JSON 顶层必须是字典")
        return result

except ImportError:  # pragma: no cover

    def _json_dumps(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _json_dumps_bytes(data: dict[str, Any]) -> bytes:
        return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    def _json_loads(data: str | bytes) -> dict[str, Any]:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError("JSON 顶层必须是字典")
        return result


__all__ = [
    "FileFingerprint",
    "IncrementalManifest",
]


@dataclass(frozen=True)
class FileFingerprint:
    """文件指纹：增量扫描变更检测。

    用 ``(mtime, size)`` 二元组判断文件是否变更：
    - mtime 变化 → 文件被修改
    - size 变化 → 文件被修改
    - 两者都相同 → 文件未变更（跳过 I/O）

    mtime 为 ``os.stat().st_mtime``（浮点秒），精度足以区分常规编辑；
    size 为字节数，捕获内容增删。两者均相同的文件视为未变更。
    """

    mtime: float
    size: int


class IncrementalManifest:
    """增量扫描清单：上次扫描的文件指纹映射。

    持久化到 ``~/.fuscan/manifests/<ws_id>.json``，记录每个已扫描文件的
    ``(相对路径 → FileFingerprint)`` 映射。增量扫描时加载，walk 阶段对比
    指纹跳过未变更文件，scan 阶段合并未变更文件的命中结果。

    相对路径以扫描根为基准（``path.relative_to(root)``），确保根路径迁移
    （如挂载点变化）不影响指纹匹配。路径分隔符统一为正斜杠（跨平台一致）。

    :param root: 扫描根路径（仅用于记录，不参与指纹匹配）
    :param fingerprints: 相对路径 → 文件指纹映射
    """

    def __init__(self, root: Path = Path(), fingerprints: dict[str, FileFingerprint] | None = None) -> None:
        self.root = root
        self.fingerprints: dict[str, FileFingerprint] = fingerprints or {}

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        data = {
            "root": str(self.root),
            "fingerprints": {k: {"mtime": v.mtime, "size": v.size} for k, v in self.fingerprints.items()},
        }
        return _json_dumps(data)

    @classmethod
    def from_json(cls, json_str: str | bytes) -> IncrementalManifest:
        """从 JSON 反序列化。

        :param json_str: :meth:`to_json` 输出的 JSON 字符串或字节串
        :return: :class:`IncrementalManifest` 实例
        :raises ValueError: JSON 格式非法
        """
        data = _json_loads(json_str)
        root = Path(data.get("root", ""))
        fps_data = data.get("fingerprints", {})
        fingerprints = {
            str(k): FileFingerprint(mtime=float(v.get("mtime", 0.0)), size=int(v.get("size", 0)))
            for k, v in fps_data.items()
            if isinstance(v, dict)
        }
        return cls(root=root, fingerprints=fingerprints)

    @staticmethod
    def rel_key(path: Path, root: Path) -> str:
        """计算相对路径键（正斜杠分隔，跨平台一致）。"""
        try:
            rel = path.relative_to(root)
        except ValueError:
            # 路径不在 root 下时用绝对路径（不应发生，容错处理）
            rel = path
        return str(rel).replace("\\", "/")
