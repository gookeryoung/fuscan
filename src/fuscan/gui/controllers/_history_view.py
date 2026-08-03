"""扫描历史 JSON 视图构建纯函数。

将 :class:`WorkspaceController` 中 ``workspaceHistoryJson`` /
``compareWithPreviousScan`` 的纯序列化逻辑抽离到模块级，便于独立
测试。``WorkspaceController`` 对应 ``@Slot`` 改为薄包装：从 :class:`HistoryStore`
取出 ``workspace_history`` 后委托本模块构造 JSON 字符串。

公共 API：

- :func:`build_workspace_history_json`：构造历史列表 JSON 数组字符串
- :func:`build_scan_comparison_json`：构造最近两次扫描对比 JSON 字符串
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

from fuscan.history import compare_scans

if TYPE_CHECKING:
    from fuscan.history import ScanHistoryEntry

__all__ = [
    "build_scan_comparison_json",
    "build_workspace_history_json",
]


def build_workspace_history_json(entries: tuple[ScanHistoryEntry, ...]) -> str:
    """构造扫描历史列表 JSON 数组字符串（供 QML 解析展示）。

    :param entries: 按 ``finished_at`` 倒序的历史条目元组
        （``HistoryStore.workspace_history`` 返回值）
    :return: JSON 数组字符串；空历史返回 ``"[]"``

    每个元素包含字段：``scan_id``/``workspace_name``/``started_at``/
    ``finished_at``/``status``/``total_files``/``scanned_files``/
    ``matched_files``/``skipped_files``/``error_count``/``duration_seconds``
    （保留两位小数）/``rule_names``/``summary``。
    """
    payload = [
        {
            "scan_id": e.scan_id,
            "workspace_name": e.workspace_name,
            "started_at": e.started_at,
            "finished_at": e.finished_at,
            "status": e.status,
            "total_files": e.total_files,
            "scanned_files": e.scanned_files,
            "matched_files": e.matched_files,
            "skipped_files": e.skipped_files,
            "error_count": e.error_count,
            "duration_seconds": round(e.duration_seconds, 2),
            "rule_names": list(e.rule_names),
            "summary": e.summary,
        }
        for e in entries
    ]
    return _json.dumps(payload, ensure_ascii=False)


def build_scan_comparison_json(entries: tuple[ScanHistoryEntry, ...]) -> str:
    """构造最近两次扫描对比 JSON 字符串。

    :param entries: 按 ``finished_at`` 倒序的历史条目元组（最多取前 2 条）
    :return: JSON 对象字符串；无历史返回 ``"{}"``

    payload 包含字段：``current``/``previous``/``summary``/``trend``/
    ``matched_delta``/``new_hits_count``/``resolved_hits_count``/
    ``persistent_hits_count``/``new_hits``（限 50 条）/``resolved_hits``
    （限 50 条）/``new_rules``/``dropped_rules``。
    """
    if not entries:
        return "{}"
    current = entries[0]
    previous = entries[1] if len(entries) >= 2 else None
    comparison = compare_scans(current, previous)
    payload = {
        "current": {
            "scan_id": comparison.current.scan_id,
            "finished_at": comparison.current.finished_at,
            "matched_files": comparison.current.matched_files,
            "status": comparison.current.status,
        },
        "previous": (
            {
                "scan_id": comparison.previous.scan_id,
                "finished_at": comparison.previous.finished_at,
                "matched_files": comparison.previous.matched_files,
                "status": comparison.previous.status,
            }
            if comparison.previous is not None
            else None
        ),
        "summary": comparison.summary(),
        "trend": comparison.trend,
        "matched_delta": comparison.matched_delta,
        "new_hits_count": len(comparison.new_hits),
        "resolved_hits_count": len(comparison.resolved_hits),
        "persistent_hits_count": len(comparison.persistent_hits),
        "new_hits": list(comparison.new_hits[:50]),  # 限制返回数量避免过大
        "resolved_hits": list(comparison.resolved_hits[:50]),
        "new_rules": list(comparison.new_rules),
        "dropped_rules": list(comparison.dropped_rules),
    }
    return _json.dumps(payload, ensure_ascii=False)
