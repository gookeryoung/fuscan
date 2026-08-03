"""扫描历史条目构建纯函数。

将 :class:`ScanController.build_history_entry` 的纯逻辑抽离到模块级，
便于独立测试。``ScanController`` 对应方法改为薄包装：传入
``ScanReport`` 与工作区信息后委托本模块构造 :class:`ScanHistoryEntry`。

公共 API：

- :func:`build_history_entry`：从 :class:`ScanReport` 构建扫描历史条目
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fuscan.history import STATUS_CANCELLED, STATUS_COMPLETED, ScanHistoryEntry

if TYPE_CHECKING:
    from fuscan.scanner import ScanReport

__all__ = ["build_history_entry"]


def build_history_entry(
    report: ScanReport | None,
    workspace_id: str,
    workspace_name: str,
    status_summary: str,
) -> ScanHistoryEntry | None:
    """从最近一次 :class:`ScanReport` 构建扫描历史条目。

    在扫描完成/取消后由 :class:`WorkspaceController` 调用，将本次扫描关键指标
    归档到 :class:`fuscan.history.HistoryStore`。无 ``report`` 时返回 ``None``。

    :param report: 本次扫描报告（``ScanController._last_report``）；``None`` 返回 ``None``
    :param workspace_id: 工作区 ID
    :param workspace_name: 工作区名称快照
    :param status_summary: 状态摘要文本（``ScanController._status_summary``）
    :return: :class:`ScanHistoryEntry` 或 ``None``
    """
    if report is None:
        return None
    stats = report.stats
    status = STATUS_CANCELLED if report.cancelled else STATUS_COMPLETED
    # 命中文件路径排序元组（用于对比）
    hit_paths = tuple(sorted(str(r.path) for r in report.hits))
    # 规则名排序元组
    rule_names = tuple(sorted(report.rule_names))
    return ScanHistoryEntry(
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        status=status,
        total_files=stats.total_files,
        scanned_files=stats.scanned_files,
        matched_files=stats.matched_files,
        skipped_files=stats.skipped_files,
        error_count=stats.errors,
        duration_seconds=stats.duration_seconds,
        hit_paths=hit_paths,
        rule_names=rule_names,
        summary=status_summary,
    )
