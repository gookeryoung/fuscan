"""扫描结果缓存持久化纯函数。

将 :class:`WorkspaceController` 中 ``_save_cached_results`` /
``_delete_cached_results`` 的纯 I/O 逻辑抽离到模块级（iter-142），便于独立
测试。``WorkspaceController`` 对应方法改为薄包装：解析缓存路径后委托本模块。

公共 API：

- :func:`save_cached_results`：将 :class:`ScanReport` 持久化到缓存文件
- :func:`delete_cached_results`：删除指定缓存文件
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.scanner import ScanReport

__all__ = [
    "delete_cached_results",
    "save_cached_results",
]

logger = logging.getLogger(__name__)


def save_cached_results(
    report: ScanReport | None,
    cache_file: Path,
    cached_results_dir: Path,
) -> None:
    """将 :class:`ScanReport` 持久化到 ``cache_file``。

    扫描结束（含取消）后调用，重启后通过 ``_try_load_cached_results`` 恢复。
    持久化失败仅记录日志，不影响主流程。

    iter-135：本次结果无命中但缓存文件中已有非空结果时不覆盖，避免增量扫描
    回退全量后空结果覆盖之前的完整结果，导致重启后无法恢复且后续增量扫描
    因 ``prev_report.hits`` 为空而无法合并旧命中（恶性循环）。

    :param report: 本次扫描报告；``None`` 直接返回
    :param cache_file: 缓存文件路径（``<cached_results_dir>/<ws_id>.json``）
    :param cached_results_dir: 缓存目录（用于 ``mkdir(parents=True)``）
    """
    if report is None:
        return
    # 防御：本次无命中但缓存已有非空结果时跳过覆盖
    if not report.hits and cache_file.exists():
        try:
            from fuscan.scanner.result import ScanReport as _ScanReport

            prev = _ScanReport.from_json(cache_file.read_bytes())
            if prev.hits:
                logger.info(
                    "扫描结果缓存已有 %d 条命中，本次无命中，保留已有缓存",
                    len(prev.hits),
                )
                return
        except (OSError, ValueError):
            pass  # 缓存读取失败时正常覆盖
    try:
        cached_results_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(report.to_json_bytes())
        logger.debug("扫描结果已缓存（%d 条命中）", len(report.hits))
    except (OSError, ValueError) as exc:
        logger.warning("扫描结果缓存失败: %s", exc)


def delete_cached_results(cache_file: Path) -> None:
    """删除指定缓存文件。

    :param cache_file: 缓存文件路径（``<cached_results_dir>/<ws_id>.json``）
    """
    try:
        if cache_file.exists():
            cache_file.unlink()
            logger.debug("缓存结果已删除")
    except OSError as exc:
        logger.warning("缓存结果删除失败: %s", exc)
