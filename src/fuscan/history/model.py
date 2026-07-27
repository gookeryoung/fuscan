"""扫描历史归档数据模型。

定义 :class:`ScanHistoryEntry`，单次扫描的归档条目。包含足够信息用于：
- 在历史列表中展示（状态/计数/耗时/摘要）
- 与其他扫描对比（命中文件路径集合、规则名集合）

设计要点：
- ``frozen=True``：归档条目不可变，避免误修改后与磁盘数据不一致
- ``hit_paths``：排序去重的命中文件路径元组，用于集合运算（新增/已解决/持续）
- ``rule_names``：排序去重的规则名元组，便于对比规则覆盖变化
- ``status``：枚举式字符串（``completed``/``cancelled``/``failed``），
  避免引入 ``enum.Enum`` 增加 JSON 序列化复杂度
"""

from __future__ import annotations

import datetime as _dt
import secrets
from dataclasses import dataclass, field

__all__ = ["STATUS_CANCELLED", "STATUS_COMPLETED", "STATUS_FAILED", "ScanHistoryEntry"]

# 扫描状态字符串常量
STATUS_COMPLETED: str = "completed"
STATUS_CANCELLED: str = "cancelled"
STATUS_FAILED: str = "failed"


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串（含时区后缀 ``Z``）。"""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_scan_id() -> str:
    """生成扫描 ID：``YYYYMMDDHHMMSS-<8hex>``，时间前缀便于人眼排序。"""
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{ts}-{secrets.token_hex(4)}"


@dataclass(frozen=True)
class ScanHistoryEntry:
    """单次扫描的归档条目。

    :param scan_id: 扫描唯一标识（时间前缀 + 随机后缀）
    :param workspace_id: 所属工作区 ID
    :param workspace_name: 工作区名称快照（工作区可能被删除，快照用于历史列表展示）
    :param started_at: 扫描开始时间（ISO 格式 UTC）
    :param finished_at: 扫描完成时间（ISO 格式 UTC）
    :param status: 扫描状态（``completed``/``cancelled``/``failed``）
    :param total_files: walk 阶段发现的总文件数
    :param scanned_files: scan 阶段实际解析的文件数
    :param matched_files: 命中文件数
    :param skipped_files: 按扩展名/目录过滤跳过的文件数
    :param error_count: 解析错误文件数
    :param duration_seconds: 扫描耗时（秒）
    :param hit_paths: 命中文件路径排序元组（用于对比）
    :param rule_names: 命中规则名排序元组（用于对比规则覆盖变化）
    :param summary: 状态栏摘要文本快照
    """

    scan_id: str = field(default_factory=_new_scan_id)
    workspace_id: str = ""
    workspace_name: str = ""
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = field(default_factory=_now_iso)
    status: str = STATUS_COMPLETED
    total_files: int = 0
    scanned_files: int = 0
    matched_files: int = 0
    skipped_files: int = 0
    error_count: int = 0
    duration_seconds: float = 0.0
    hit_paths: tuple[str, ...] = field(default_factory=tuple)
    rule_names: tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        """序列化为可 JSON 持久化的 dict。"""
        return {
            "scan_id": self.scan_id,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "total_files": self.total_files,
            "scanned_files": self.scanned_files,
            "matched_files": self.matched_files,
            "skipped_files": self.skipped_files,
            "error_count": self.error_count,
            "duration_seconds": self.duration_seconds,
            "hit_paths": list(self.hit_paths),
            "rule_names": list(self.rule_names),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ScanHistoryEntry:
        """从 dict 反序列化（容错：跳过类型不符字段）。

        :param raw: 从 JSON 解析得到的原始数据
        :return: 类型安全的 :class:`ScanHistoryEntry`
        """
        if not isinstance(raw, dict):
            return cls()
        data: dict[str, object] = raw

        def _str(value: object, default: str = "") -> str:
            if isinstance(value, str):
                return value
            return default

        def _float(value: object, default: float = 0.0) -> float:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return default

        def _int(value: object, default: int = 0) -> int:
            if isinstance(value, bool):
                return default
            if isinstance(value, int):
                return value
            return default

        def _str_tuple(value: object) -> tuple[str, ...]:
            if isinstance(value, (list, tuple)):
                return tuple(str(x) for x in value)
            return ()

        status = _str(data.get("status"), STATUS_COMPLETED)
        if status not in (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_FAILED):
            status = STATUS_COMPLETED

        return cls(
            scan_id=_str(data.get("scan_id"), _new_scan_id()),
            workspace_id=_str(data.get("workspace_id")),
            workspace_name=_str(data.get("workspace_name")),
            started_at=_str(data.get("started_at")),
            finished_at=_str(data.get("finished_at")),
            status=status,
            total_files=_int(data.get("total_files")),
            scanned_files=_int(data.get("scanned_files")),
            matched_files=_int(data.get("matched_files")),
            skipped_files=_int(data.get("skipped_files")),
            error_count=_int(data.get("error_count")),
            duration_seconds=_float(data.get("duration_seconds")),
            hit_paths=_str_tuple(data.get("hit_paths")),
            rule_names=_str_tuple(data.get("rule_names")),
            summary=_str(data.get("summary")),
        )
