"""扫描结果详情展示与操作纯函数。

将 :class:`ScanController` 中与选中结果详情展示、内容替换、移至暂存相关的
纯逻辑抽离到模块级，便于独立测试。``build_detail_hits_model`` 构造 QML
ListView 绑定的命中详情 dict 列表，``can_replace_result`` 判断当前结果是否
可执行替换，``replace_selected`` 与 ``move_to_staging`` 执行实际文件操作并
返回操作消息供 QML 显示。

公共 API：

- :func:`build_detail_hits_model`：构造命中详情 dict 列表
- :func:`can_replace_result`：判断结果是否可执行替换
- :func:`replace_selected`：执行替换并返回消息
- :func:`move_to_staging`：复制到暂存区隔离目录并标记跳过
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.config import default_backup_dir, detect_default_staging_dir
from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.replacer import ReplaceStatus, replace_in_file

if TYPE_CHECKING:
    from fuscan.rules.model import RuleSet
    from fuscan.scanner.result import ScanResult
    from fuscan.skip_store import SkipStore

__all__ = [
    "build_detail_hits_model",
    "can_replace_result",
    "move_to_staging",
    "replace_selected",
]

logger = logging.getLogger(__name__)


def build_detail_hits_model(result: ScanResult | None) -> list[dict[str, object]]:
    """构造选中结果的命中详情列表（QML 直接 ListView 绑定）。

    每条命中包含：规则名、严重度文本/色值、上下文（detail）、匹配文本、
    匹配条数、匹配目标（filename/content/path）、规则描述（供详情面板展示）。

    :param result: 选中结果；``None`` 返回空列表
    :return: 命中详情 dict 列表
    """
    if result is None:
        return []
    return [
        {
            "ruleName": hit.rule_name,
            "severityText": severity_text(hit.severity),
            "severityColor": severity_color_hex(hit.severity),
            "context": hit.detail,
            "matchText": hit.match_text,
            "matchCount": hit.match_count,
            "target": hit.target,
            "description": hit.match_description,
        }
        for hit in result.hits
    ]


def can_replace_result(result: ScanResult | None, ruleset: RuleSet | None) -> bool:
    """判断当前结果是否可执行替换。

    条件：已选中结果、规则集已加载、非压缩包内部条目、命中规则中存在
    ``replace=True`` 的规则（否则按钮无意义）。

    :param result: 选中结果
    :param ruleset: 当前规则集
    :return: 可替换返回 ``True``
    """
    if result is None or ruleset is None or result.archive_path is not None:
        return False
    rule_map = {r.name: r for r in ruleset.rules}
    return any(rule_map.get(h.rule_name) is not None and rule_map[h.rule_name].replace for h in result.hits)


def replace_selected(
    result: ScanResult | None,
    ruleset: RuleSet | None,
    backup_dir_str: str | None,
    backup_preserve_relative: bool,
    last_report_root: Path | None,
) -> str:
    """替换当前选中结果的命中内容。

    调用 :func:`fuscan.replacer.replace_in_file` 执行备份 + 原子替换。
    返回操作消息供 QML 显示（成功/失败原因）。

    :param result: 选中结果；``None`` 返回 ``未选中结果``
    :param ruleset: 当前规则集；``None`` 返回 ``规则集未加载``
    :param backup_dir_str: 备份目录字符串（``None`` 或空字符串用默认目录）
    :param backup_preserve_relative: 是否保留相对路径备份
    :param last_report_root: 上次扫描报告根路径（用于相对路径计算）
    :return: 操作消息字符串

    返回值语义：

    - 未选中结果 → ``未选中结果``
    - 规则集未加载 → ``规则集未加载``
    - 压缩包内部条目 → ``压缩包内部条目不支持替换``
    - 无 ``replace=True`` 规则 → ``未启用替换的规则``
    - 其他状态 → :class:`ReplaceResult.message`
    """
    if result is None:
        return "未选中结果"
    if ruleset is None:
        return "规则集未加载"
    if result.archive_path is not None:
        return "压缩包内部条目不支持替换"

    backup_dir = Path(backup_dir_str) if backup_dir_str else default_backup_dir()
    scan_root = last_report_root if last_report_root is not None else result.path.parent

    replace_result = replace_in_file(
        src=result.path,
        hits=result.hits,
        ruleset=ruleset,
        backup_root=backup_dir,
        scan_root=scan_root,
        preserve_relative=backup_preserve_relative,
    )
    if replace_result.status == ReplaceStatus.SUCCESS:
        logger.info(
            "已替换 %s 中 %d 条规则命中，备份: %s",
            result.path,
            replace_result.replaced_count,
            replace_result.backup_path,
        )
        return replace_result.message or f"替换成功（{replace_result.replaced_count} 条）"
    logger.warning("替换失败: %s", replace_result.message)
    return replace_result.message or "替换失败"


def move_to_staging(
    result: ScanResult | None,
    staging_dir_str: str | None,
    last_report_root: Path | None,
    skip_store: SkipStore,
) -> str:
    """将当前选中结果文件复制到暂存区隔离目录并标记为跳过。

    流程：

    1. 校验选中结果、非压缩包内部条目
    2. 计算暂存区隔离目录：``<staging_dir>/quarantine/`` 或
       ``<默认暂存区>/quarantine/``
    3. 保留源文件相对扫描根目录的目录结构，复制到隔离目录
    4. 调用 :meth:`SkipStore.add` 标记为跳过，后续扫描自动跳过
    5. 返回操作消息供 QML 显示

    :param result: 选中结果；``None`` 返回 ``未选中结果``
    :param staging_dir_str: 暂存区目录字符串（``None`` 或空字符串用默认目录）
    :param last_report_root: 上次扫描报告根路径（用于相对路径计算）
    :param skip_store: 跳过存储实例
    :return: 操作消息字符串

    返回值语义：

    - 未选中结果 → ``未选中结果``
    - 压缩包内部条目 → ``压缩包内部条目不支持移至暂存``
    - 复制成功 → ``已移至暂存: <隔离路径>`` 并标记跳过
    - 复制失败 → ``移至暂存失败: <错误>``
    """
    if result is None:
        return "未选中结果"
    if result.archive_path is not None:
        return "压缩包内部条目不支持移至暂存"

    # 计算暂存区隔离目录
    staging_root = Path(staging_dir_str) if staging_dir_str else detect_default_staging_dir()
    quarantine_dir = staging_root / "quarantine"
    scan_root = last_report_root if last_report_root is not None else result.path.parent

    # 保留相对扫描根目录的目录结构
    try:
        rel_path = result.path.relative_to(scan_root)
    except ValueError:
        # 不在扫描根下（如绝对路径跨盘符），仅保留文件名
        rel_path = Path(result.path.name)

    dest = quarantine_dir / rel_path
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.path, dest)
    except OSError as exc:
        logger.warning("移至暂存失败: %s -> %s", result.path, dest, exc_info=True)
        return f"移至暂存失败: {exc}"

    # 标记为跳过，后续扫描自动跳过该文件
    skip_store.add(str(result.path))
    logger.info("已移至暂存: %s -> %s（已标记跳过）", result.path, dest)
    return f"已移至暂存: {dest}（已标记跳过）"
