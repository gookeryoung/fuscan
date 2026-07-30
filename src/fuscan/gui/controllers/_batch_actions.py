"""批量替换与撤销纯函数。

将 :class:`ScanController` 中与批量替换、撤销、误报标记相关的纯逻辑抽离到
模块级（iter-142），便于独立测试。``ScanController`` 对应 ``@Slot`` 改为薄
包装：解析自身状态后调用本模块函数，再按返回值更新撤销状态或执行副作用。

公共 API：

- :func:`replace_all_filtered_results`：对过滤后结果执行批量替换，返回消息与
  可供撤销的 (源, 备份) 配对（前置校验失败时配对为 ``None``）
- :func:`undo_last_batch_replace`：按 (源, 备份) 配对从 ``.bak`` 批量恢复
- :func:`undo_selected_replace`：从 ``.bak`` 恢复当前选中结果
- :func:`mark_as_false_positive`：校验选中结果并计算误报白名单条目字段
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.processing.replacer import ReplaceStatus, replace_batch, restore_from_backup

if TYPE_CHECKING:
    from fuscan.rules.model import RuleSet
    from fuscan.scanner.result import ScanResult

__all__ = [
    "mark_as_false_positive",
    "replace_all_filtered_results",
    "undo_last_batch_replace",
    "undo_selected_replace",
]

logger = logging.getLogger(__name__)


def replace_all_filtered_results(
    filtered: tuple[ScanResult, ...],
    ruleset: RuleSet | None,
    backup_dir: Path,
    scan_root: Path,
    backup_preserve_relative: bool,
    override_replace_with: str | None,
) -> tuple[str, tuple[tuple[Path, Path], ...] | None]:
    """对过滤后的所有结果执行批量替换。

    调用 :func:`fuscan.replacer.replace_batch`，返回 :class:`BatchReplaceResult.message`
    与可供撤销的 ``(源, 备份)`` 配对。前置校验失败（规则集未加载或无待替换结果）
    时配对返回 ``None``，调用方据此跳过撤销状态更新，保留既有可撤销记录。

    :param filtered: 过滤后的结果元组（``ResultListModel.filtered_results``）
    :param ruleset: 当前规则集；``override_replace_with`` 非空时可为 ``None``
    :param backup_dir: 备份根目录
    :param scan_root: 扫描根目录（用于相对路径计算）
    :param backup_preserve_relative: 是否保留相对路径备份
    :param override_replace_with: 用户自定义替换文本；非空时覆盖所有规则的
        ``replace_with``，不要求规则 ``replace=True``。``None`` 走规则驱动模式
    :return: ``(消息, 撤销配对)``。前置校验失败时撤销配对为 ``None``

    返回值语义：

    - 规则集未加载且无自定义替换 → ``("规则集未加载", None)``
    - 无待替换结果 → ``("无待替换的结果", None)``
    - 其他 → ``(BatchReplaceResult.message, ((src, backup), ...))``
    """
    if ruleset is None and not override_replace_with:
        return "规则集未加载", None
    if not filtered:
        return "无待替换的结果", None

    batch_result = replace_batch(
        results=filtered,
        ruleset=ruleset,
        backup_root=backup_dir,
        scan_root=scan_root,
        preserve_relative=backup_preserve_relative,
        override_replace_with=override_replace_with,
    )
    logger.info(
        "批量替换完成: 成功 %d/%d, 跳过 %d, 失败 %d",
        batch_result.succeeded,
        batch_result.total,
        batch_result.skipped,
        batch_result.failed,
    )
    # 从 batch_result.details 提取成功项的 (源, 备份) 配对，供 undoLastBatchReplace 撤销
    last_batch_backup_paths = tuple(
        (src, result.backup_path)
        for src, result in batch_result.details
        if result.status == ReplaceStatus.SUCCESS and result.backup_path is not None
    )
    return batch_result.message, last_batch_backup_paths


def undo_last_batch_replace(last_batch_backup_paths: tuple[tuple[Path, Path], ...]) -> str:
    """撤销最近一次批量替换，从 ``.bak`` 备份恢复所有文件。

    逐个调用 :func:`fuscan.replacer.restore_from_backup`，按 ``(源, 备份)`` 配对
    从备份恢复到原源文件路径。无可撤销操作时返回提示。

    :param last_batch_backup_paths: 上次批量替换记录的 ``(源, 备份)`` 配对元组
    :return: 操作消息字符串

    返回值语义：

    - 无可撤销记录 → ``无可撤销的批量替换``
    - 其他 → ``批量撤销完成：恢复 N 个文件``（有失败时追加 ``，M 个失败``）
    """
    if not last_batch_backup_paths:
        return "无可撤销的批量替换"

    succeeded = 0
    failed = 0
    for src_path, backup_path in last_batch_backup_paths:
        msg = restore_from_backup(backup_path, src_path)
        if msg.startswith("已从备份恢复"):
            succeeded += 1
        else:
            failed += 1
            logger.warning("撤销失败: %s", msg)

    summary = f"批量撤销完成：恢复 {succeeded} 个文件"
    if failed:
        summary += f"，{failed} 个失败"
    return summary


def undo_selected_replace(
    result: ScanResult | None,
    backup_dir: Path,
    scan_root: Path,
    backup_preserve_relative: bool,
) -> str:
    """撤销当前选中结果的最近一次替换（从 ``.bak`` 恢复）。

    根据选中结果路径反推备份路径（``{src}.bak``），调用
    :func:`fuscan.replacer.restore_from_backup` 恢复。

    :param result: 选中结果；``None`` 返回 ``未选中结果``
    :param backup_dir: 备份根目录
    :param scan_root: 扫描根目录（用于相对路径计算）
    :param backup_preserve_relative: 是否保留相对路径备份
    :return: 操作消息字符串
    """
    if result is None:
        return "未选中结果"
    # 复用 _resolve_backup_path 计算备份路径
    from fuscan.processing.replacer import _resolve_backup_path

    backup_path = _resolve_backup_path(
        src=result.path,
        backup_root=backup_dir,
        scan_root=scan_root,
        preserve_relative=backup_preserve_relative,
    )
    return restore_from_backup(backup_path, result.path)


def mark_as_false_positive(
    result: ScanResult | None,
    rule_filter: str,
) -> tuple[str, str, str | None]:
    """校验选中结果并计算误报白名单条目字段。

    纯校验 + 计算函数：不执行白名单写入或清单失效，仅返回白名单条目所需的
    ``path_glob`` 与 ``rule_name``。调用方（``ScanController`` Slot）据此调用
    :meth:`WhitelistController.addEntry` 与 :meth:`invalidate_manifest`。

    :param result: 选中结果；``None`` 时返回校验失败消息
    :param rule_filter: 指定规则名（精确匹配）；空字符串表示该文件全部命中
        规则均标记为误报（``*`` 通配）
    :return: ``(path_glob, rule_name, error_msg)``。``error_msg`` 为 ``None``
        表示校验通过，调用方应使用 ``path_glob``/``rule_name`` 执行白名单写入；
        ``error_msg`` 非 ``None`` 表示校验失败，调用方应直接返回该消息

    返回值语义：

    - 未选中结果 → ``("", "", "未选中结果")``
    - 压缩包内部条目 → ``("", "", "压缩包内部条目不支持标记误报")``（路径含
      ``!`` 无法 glob）
    - 校验通过 → ``(str(result.path), rule_name, None)``
    """
    if result is None:
        return "", "", "未选中结果"
    if result.archive_path is not None:
        return "", "", "压缩包内部条目不支持标记误报"
    rule_name = rule_filter.strip() or "*"
    # 路径 glob 用绝对路径字符串（与 Scanner 中 str(Path) 一致）
    path_glob = str(result.path)
    return path_glob, rule_name, None
