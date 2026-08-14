"""扫描历史 JSON 视图构建纯函数。

将 :class:`WorkspaceController` 中 ``workspaceHistoryJson`` /
``compareWithPreviousScan`` 的纯序列化逻辑抽离到模块级，便于独立
测试。``WorkspaceController`` 对应 ``@Slot`` 改为薄包装：从 :class:`HistoryStore`
取出 ``workspace_history`` 后委托本模块构造 JSON 字符串。

公共 API：

- :func:`build_workspace_history_json`：构造历史列表 JSON 数组字符串
- :func:`build_scan_comparison_json`：构造最近两次扫描对比 JSON 字符串
- :func:`build_scan_trend_json`：构造扫描趋势 JSON 数组字符串（供趋势图）
- :func:`build_arbitrary_comparison_json`：构造任意两次扫描对比 JSON 字符串
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

from fuscan.history import compare_scans

if TYPE_CHECKING:
    from fuscan.history import ScanComparison, ScanHistoryEntry

__all__ = [
    "build_arbitrary_comparison_json",
    "build_scan_comparison_json",
    "build_scan_trend_json",
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


def _comparison_to_payload(comparison: ScanComparison) -> dict[str, object]:
    """将 :class:`ScanComparison` 转为 JSON 可序列化的 dict（内部复用）。

    供 :func:`build_scan_comparison_json` 与
    :func:`build_arbitrary_comparison_json` 共享同一字段结构，避免两处
    构造逻辑漂移。``new_hits``/``resolved_hits`` 截断为前 50 条以控制体积。
    """
    previous_payload: dict[str, object] | None = None
    if comparison.previous is not None:
        previous_payload = {
            "scan_id": comparison.previous.scan_id,
            "finished_at": comparison.previous.finished_at,
            "matched_files": comparison.previous.matched_files,
            "status": comparison.previous.status,
        }
    return {
        "current": {
            "scan_id": comparison.current.scan_id,
            "finished_at": comparison.current.finished_at,
            "matched_files": comparison.current.matched_files,
            "status": comparison.current.status,
        },
        "previous": previous_payload,
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
    return _json.dumps(_comparison_to_payload(comparison), ensure_ascii=False)


def build_arbitrary_comparison_json(
    entries: tuple[ScanHistoryEntry, ...],
    scan_id_a: str,
    scan_id_b: str,
) -> str:
    """构造任意两次扫描对比 JSON 字符串。

    在 ``entries`` 中按 ``scan_id`` 定位两条，``finished_at`` 较新者为
    ``current``、较旧者为 ``previous``，复用 :func:`compare_scans` 计算差异。
    与 :func:`build_scan_comparison_json` 输出结构完全一致，便于 QML 复用
    同一对比摘要展示组件。

    :param entries: 历史条目元组（顺序不限，内部按 scan_id 查找）
    :param scan_id_a: 第一次扫描 ID
    :param scan_id_b: 第二次扫描 ID
    :return: JSON 对象字符串；``scan_id`` 相同、为空或任一未找到返回 ``"{}"``
    """
    if not scan_id_a or not scan_id_b or scan_id_a == scan_id_b:
        return "{}"
    found: dict[str, ScanHistoryEntry] = {e.scan_id: e for e in entries if e.scan_id in (scan_id_a, scan_id_b)}
    if scan_id_a not in found or scan_id_b not in found:
        return "{}"
    entry_a = found[scan_id_a]
    entry_b = found[scan_id_b]
    # finished_at 字符串 ISO 格式可直接字典序比较确定新旧
    if entry_a.finished_at >= entry_b.finished_at:
        current, previous = entry_a, entry_b
    else:
        current, previous = entry_b, entry_a
    comparison = compare_scans(current, previous)
    return _json.dumps(_comparison_to_payload(comparison), ensure_ascii=False)


def build_scan_trend_json(
    entries: tuple[ScanHistoryEntry, ...],
    top_n: int = 20,
) -> str:
    """构造扫描趋势 JSON 数组字符串（供趋势图 X 轴从左到右时间递增）。

    :param entries: 按 ``finished_at`` 倒序的历史条目元组（最新在前），
        取前 ``top_n`` 条后反转为正序（最旧在前），便于图表从左到右绘制
    :param top_n: 最多取多少条；实际取 ``min(len(entries), max(1, top_n))``
    :return: JSON 数组字符串；空历史返回 ``"[]"``

    每个元素包含字段：``finished_at``/``matched_files``/``total_files``/
    ``scanned_files``/``error_count``/``duration_seconds``（保留两位小数）/
    ``status``。
    """
    if not entries:
        return "[]"
    limit = max(1, top_n)
    recent = list(entries[:limit])
    recent.reverse()  # 倒序→正序，图表从左到右时间递增
    payload = [
        {
            "finished_at": e.finished_at,
            "matched_files": e.matched_files,
            "total_files": e.total_files,
            "scanned_files": e.scanned_files,
            "error_count": e.error_count,
            "duration_seconds": round(e.duration_seconds, 2),
            "status": e.status,
        }
        for e in recent
    ]
    return _json.dumps(payload, ensure_ascii=False)
