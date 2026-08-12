"""增量扫描清单：文件指纹与上次扫描状态持久化。

从 :mod:`fuscan.scanner.result` 拆出：``FileFingerprint`` 与
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
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any


@lru_cache(maxsize=1)
def _get_orjson() -> ModuleType | None:
    """惰性加载 orjson；不可用时返回 ``None``。

    orjson 为可选 C 扩展，仅用于增量清单 / 误报白名单 / 扫描报告序列化加速。
    模块级**不**触发 ``import orjson``，避免 CLI/GUI 启动期加载 C 扩展；
    首次序列化时才尝试加载并经 ``lru_cache`` 缓存结果（只导入一次，
    后续为 O(1) 字典查找）。回退到标准库 ``json`` 时语义等价，仅性能差异。
    """
    try:
        import orjson

        return orjson
    except ImportError:  # pragma: no cover - orjson 为 fuscan 必需依赖，回退路径不期望触发
        return None


def _json_dumps(data: dict[str, Any]) -> str:
    """JSON 序列化为字符串（orjson 优先，回退标准库 json，带缩进）。"""
    orjson = _get_orjson()
    if orjson is not None:
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode("utf-8")
    return json.dumps(data, ensure_ascii=False, indent=2)  # pragma: no cover


def _json_dumps_bytes(data: dict[str, Any]) -> bytes:
    """JSON 序列化为字节串（orjson 优先，回退标准库 json）。

    orjson 路径直接返回 ``bytes``，避免 str→bytes 解码开销；
    标准库路径将 str 结果编码为 UTF-8 字节串。
    """
    orjson = _get_orjson()
    if orjson is not None:
        return orjson.dumps(data, option=orjson.OPT_INDENT_2)
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")  # pragma: no cover


def _json_loads(data: str | bytes) -> dict[str, Any]:
    """JSON 反序列化（orjson 优先，回退标准库 json，接受 str 或 bytes）。"""
    orjson = _get_orjson()
    if orjson is not None:
        result = orjson.loads(data)
    else:
        result = json.loads(data)  # pragma: no cover
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

    用 ``(mtime, size)`` 二元组判断文件是否变更（快速路径，默认使用）：
    - mtime 变化 → 文件被修改
    - size 变化 → 文件被修改
    - 两者都相同 → 文件未变更（跳过 I/O）

    可选 ``sha1_prefix``（16 字节 hex 前缀，约等于前 8 字节 sha1 的十六进制）
    为三元组的第三维，用于"mtime+size 碰撞"的篡改防御场景
    （如恶意程序 touch -r 保持 mtime 不变但改内容，罕见但可按需开启）。
    为 ``None`` 时表示未启用 hash 校验，**不破坏与旧 JSON 的向后兼容**。

    mtime 为 ``os.stat().st_mtime``（浮点秒），精度足以区分常规编辑；
    size 为字节数，捕获内容增删。两者均相同的文件视为未变更。
    """

    mtime: float
    size: int
    sha1_prefix: str | None = None


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
        """序列化为 JSON 字符串。

        ``sha1_prefix`` 为 ``None`` 时省略该键（保证老版本 fuscan 可读），
        为非 None 时写入，实现**向前兼容**：新格式 manifest 读入老版本
        fuscan 会忽略未知键，指纹仍可正常比对 mtime+size。
        """
        fps_out: dict[str, dict[str, object]] = {}
        for k, v in self.fingerprints.items():
            entry: dict[str, object] = {"mtime": v.mtime, "size": v.size}
            if v.sha1_prefix is not None:
                entry["sha1_prefix"] = v.sha1_prefix
            fps_out[k] = entry
        data = {
            "root": str(self.root),
            "fingerprints": fps_out,
        }
        return _json_dumps(data)

    @classmethod
    def from_json(cls, json_str: str | bytes) -> IncrementalManifest:
        """从 JSON 反序列化。

        :param json_str: :meth:`to_json` 输出的 JSON 字符串或字节串
        :return: :class:`IncrementalManifest` 实例
        :raises ValueError: JSON 格式非法

        向后兼容：旧格式 JSON 无 ``sha1_prefix`` 键时读入为 ``None``，
        新格式有则填入。不合法的值（非字符串/非 None）回退为 ``None``。
        """
        data = _json_loads(json_str)
        root = Path(data.get("root", ""))
        fps_data = data.get("fingerprints", {})
        fingerprints: dict[str, FileFingerprint] = {}
        for k, v in fps_data.items():
            if not isinstance(v, dict):
                continue
            raw_sha = v.get("sha1_prefix", None)
            sha1_prefix: str | None = raw_sha if isinstance(raw_sha, str) and raw_sha else None
            fingerprints[str(k)] = FileFingerprint(
                mtime=float(v.get("mtime", 0.0)),
                size=int(v.get("size", 0)),
                sha1_prefix=sha1_prefix,
            )
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
