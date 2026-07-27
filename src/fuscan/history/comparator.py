"""扫描对比逻辑：计算两次扫描之间的命中变化。

核心是 :func:`compare_scans`，对两次 :class:`ScanHistoryEntry` 的 ``hit_paths``
做集合运算，产出 :class:`ScanComparison`，用于在 UI 中展示：

- 新增命中：本次有但上次没有的文件路径
- 已解决命中：上次有但本次没有的文件路径
- 持续命中：两次都命中的文件路径
- 命中数差值：本次 - 上次（负数表示改善）

设计要点：
- 集合运算在 ``set[str]`` 上完成，O(n) 复杂度
- ``previous=None`` 时表示首次扫描，所有命中都视为新增
- 排序输出便于 UI 稳定展示
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fuscan.history.model import ScanHistoryEntry

__all__ = ["ScanComparison", "compare_scans"]


@dataclass(frozen=True)
class ScanComparison:
    """两次扫描对比结果。

    :param current: 当前扫描条目
    :param previous: 上次扫描条目；``None`` 表示无更早历史（首次扫描）
    :param new_hits: 本次新增命中文件路径（本次有、上次无），按路径排序
    :param resolved_hits: 已解决命中文件路径（上次有、本次无），按路径排序
    :param persistent_hits: 持续命中文件路径（两次都有），按路径排序
    :param matched_delta: 命中数差值（本次 - 上次），负数表示改善
    :param new_rules: 本次新增命中的规则名（本次有、上次无）
    :param dropped_rules: 本次不再命中的规则名（上次有、本次无）
    """

    current: ScanHistoryEntry
    previous: ScanHistoryEntry | None
    new_hits: tuple[str, ...] = field(default_factory=tuple)
    resolved_hits: tuple[str, ...] = field(default_factory=tuple)
    persistent_hits: tuple[str, ...] = field(default_factory=tuple)
    matched_delta: int = 0
    new_rules: tuple[str, ...] = field(default_factory=tuple)
    dropped_rules: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_first_scan(self) -> bool:
        """是否为首次扫描（无更早历史）。"""
        return self.previous is None

    @property
    def trend(self) -> str:
        """趋势文本：``改善`` / ``恶化`` / ``持平`` / ``首次``。"""
        if self.previous is None:
            return "首次"
        if self.matched_delta < 0:
            return "改善"
        if self.matched_delta > 0:
            return "恶化"
        return "持平"

    def summary(self) -> str:
        """返回对比摘要文本（供 UI 直接展示）。"""
        if self.previous is None:
            return f"首次扫描：命中 {self.current.matched_files} 个文件，涉及 {len(self.current.rule_names)} 条规则"
        return (
            f"本次命中 {self.current.matched_files} | 上次命中 {self.previous.matched_files} | "
            f"差值 {self.matched_delta:+d}（{self.trend}）\n"
            f"新增 {len(self.new_hits)} | 已解决 {len(self.resolved_hits)} | "
            f"持续 {len(self.persistent_hits)}"
        )


def compare_scans(current: ScanHistoryEntry, previous: ScanHistoryEntry | None) -> ScanComparison:
    """对比两次扫描，生成 :class:`ScanComparison`。

    :param current: 当前扫描条目
    :param previous: 上次扫描条目；``None`` 表示首次扫描
    :return: 对比结果
    """
    current_paths = set(current.hit_paths)
    if previous is None:
        # 首次扫描：所有命中都视为新增
        return ScanComparison(
            current=current,
            previous=None,
            new_hits=tuple(sorted(current_paths)),
            resolved_hits=(),
            persistent_hits=(),
            matched_delta=current.matched_files,
            new_rules=tuple(sorted(set(current.rule_names))),
            dropped_rules=(),
        )

    previous_paths = set(previous.hit_paths)
    new_hits = current_paths - previous_paths
    resolved_hits = previous_paths - current_paths
    persistent_hits = current_paths & previous_paths

    current_rules = set(current.rule_names)
    previous_rules = set(previous.rule_names)
    new_rules = current_rules - previous_rules
    dropped_rules = previous_rules - current_rules

    return ScanComparison(
        current=current,
        previous=previous,
        new_hits=tuple(sorted(new_hits)),
        resolved_hits=tuple(sorted(resolved_hits)),
        persistent_hits=tuple(sorted(persistent_hits)),
        matched_delta=current.matched_files - previous.matched_files,
        new_rules=tuple(sorted(new_rules)),
        dropped_rules=tuple(sorted(dropped_rules)),
    )
