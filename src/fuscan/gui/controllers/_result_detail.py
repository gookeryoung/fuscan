"""扫描结果详情展示与操作纯函数。

将 :class:`ScanController` 中与选中结果详情展示、内容替换、移至暂存相关的
纯逻辑抽离到模块级，便于独立测试。``build_detail_hits_model`` 构造 QML
ListView 绑定的命中详情 dict 列表，``can_replace_result`` 判断当前结果是否
可执行替换，``replace_selected`` 与 ``move_to_staging`` 执行实际文件操作并
返回操作消息供 QML 显示。

公共 API：

- :func:`build_detail_hits_model`：构造命中详情 dict 列表
- :func:`can_replace_result`：判断结果是否可执行替换
- :func:`move_to_staging`：复制到暂存区隔离目录并标记跳过
- :func:`replace_selected`：执行替换并返回消息
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.gui.severity_utils import severity_color_hex, severity_text
from fuscan.processing.replacer import ReplaceStatus, is_text_file, replace_in_file
from fuscan.processing.storage import default_backup_dir, detect_default_staging_dir

if TYPE_CHECKING:
    from fuscan.processing.skip_store import SkipStore
    from fuscan.rules.model import RuleSet
    from fuscan.scanner.result import ScanResult

__all__ = [
    "build_detail_hits_model",
    "can_replace_result",
    "move_to_staging",
    "replace_selected",
]

logger = logging.getLogger(__name__)

# 上下文提取：匹配行前后各保留的行数
_CONTEXT_LINES = 2
# 上下文读取的文件大小上限（1MB），超过则跳过上下文提取
_MAX_CONTEXT_FILE_SIZE = 1024 * 1024


def _extract_context(path: Path, match_text: str) -> str:
    """从文件中提取匹配文本的上下文（前后各 ``_CONTEXT_LINES`` 行）。

    匹配行用 ``>>> `` 前缀标记，便于 QML 高亮显示。文件过大或非文本文件
    时返回空字符串。

    :param path: 文件路径
    :param match_text: 匹配文本（在文件中搜索该文本所在行）
    :return: 上下文文本（多行），无匹配或读取失败返回空字符串
    """
    if not match_text:
        return ""
    try:
        if not path.exists():
            return ""
        # iter-127：先 stat 检查大小（O(1)），再 is_text_file（可能读内容），
        # 避免对超大文件先触发 is_text_file 的内容读取
        size = path.stat().st_size
        if size > _MAX_CONTEXT_FILE_SIZE:
            return ""
        if not is_text_file(path):
            return ""
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    lines = content.splitlines()
    # 找到第一个包含 match_text 的行
    match_idx = -1
    for i, line in enumerate(lines):
        if match_text in line:
            match_idx = i
            break
    if match_idx < 0:
        return ""

    start = max(0, match_idx - _CONTEXT_LINES)
    end = min(len(lines), match_idx + _CONTEXT_LINES + 1)
    parts = []
    for i in range(start, end):
        prefix = ">>> " if i == match_idx else "    "
        parts.append(f"{prefix}{lines[i]}")
    return "\n".join(parts)


def build_detail_hits_model(result: ScanResult | None) -> list[dict[str, object]]:
    """构造选中结果的命中详情列表（QML 直接 ListView 绑定）。

    每条命中包含：规则名、严重度文本/色值、上下文（iter-124 起为文件内容
    上下文，前后各 2 行，匹配行用 ``>>>`` 标记）、匹配文本、匹配条数、
    匹配目标（filename/content/path）、规则描述（供详情面板展示）。

    :param result: 选中结果；``None`` 返回空列表
    :return: 命中详情 dict 列表
    """
    if result is None:
        return []
    # 压缩包内部条目无法读取文件内容，context 用 hit.detail 兜底
    is_archive = result.archive_path is not None
    file_path = result.path
    model: list[dict[str, object]] = []
    for hit in result.hits:
        # iter-124：实时读取文件内容上下文（非压缩包条目）
        if is_archive:
            context = hit.detail
        else:
            context = _extract_context(file_path, hit.match_text) or hit.detail
        model.append(
            {
                "ruleName": hit.rule_name,
                "severityText": severity_text(hit.severity),
                "severityColor": severity_color_hex(hit.severity),
                "context": context,
                "matchText": hit.match_text,
                "matchCount": hit.match_count,
                "target": hit.target,
                "description": hit.match_description,
            }
        )
    return model


def can_replace_result(result: ScanResult | None, ruleset: RuleSet | None) -> bool:  # noqa: ARG001
    """判断当前结果是否可执行替换。

    iter-124：放宽条件——只要选中结果且非压缩包内部条目即可替换
    （用户自定义替换文本 ``override_replace_with`` 模式不要求规则
    ``replace=True``）。``ruleset`` 参数保留向后兼容，实际不再要求规则集加载。

    :param result: 选中结果
    :param ruleset: 当前规则集（iter-124 起可为 ``None``，不影响判断）
    :return: 可替换返回 ``True``
    """
    if result is None or result.archive_path is not None:
        return False
    # iter-124：用户自定义替换模式不要求规则 replace=True，
    # 只要命中规则有 match_texts 即可替换（无匹配文本则无法替换）
    return any(hit.match_texts for hit in result.hits)


def replace_selected(
    result: ScanResult | None,
    ruleset: RuleSet | None,
    backup_dir_str: str | None,
    backup_preserve_relative: bool,
    last_report_root: Path | None,
    override_replace_with: str | None = None,
) -> str:
    """替换当前选中结果的命中内容。

    调用 :func:`fuscan.replacer.replace_in_file` 执行备份 + 原子替换。
    返回操作消息供 QML 显示（成功/失败原因）。

    :param result: 选中结果；``None`` 返回 ``未选中结果``
    :param ruleset: 当前规则集；``override_replace_with`` 非空时可为 ``None``
    :param backup_dir_str: 备份目录字符串（``None`` 或空字符串用默认目录）
    :param backup_preserve_relative: 是否保留相对路径备份
    :param last_report_root: 上次扫描报告根路径（用于相对路径计算）
    :param override_replace_with: 用户自定义替换文本（iter-124）。非空时覆盖
        所有规则的 ``replace_with``，不要求规则 ``replace=True``。默认 ``None``
        走规则驱动模式（要求 ``replace=True`` + ``replace_with``）
    :return: 操作消息字符串

    返回值语义：

    - 未选中结果 → ``未选中结果``
    - 压缩包内部条目 → ``压缩包内部条目不支持替换``
    - 其他状态 → :class:`ReplaceResult.message`
    """
    if result is None:
        return "未选中结果"
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
        override_replace_with=override_replace_with,
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

    1. 校验选中结果
    2. 计算暂存区隔离目录：``<staging_dir>/quarantine/`` 或
       ``<默认暂存区>/quarantine/``
    3. 保留源文件相对扫描根目录的目录结构，复制到隔离目录
    4. 调用 :meth:`SkipStore.add` 标记为跳过，后续扫描自动跳过
    5. 返回操作消息供 QML 显示

    iter-133：压缩包内部条目（``archive_path`` 非 None）时，移至暂存的是
    压缩包文件本身（``archive_path``），并标记 ``archive_path`` 为跳过——
    压缩包内含敏感文件时隔离整个压缩包是合理的，且内部条目无法直接复制。

    :param result: 选中结果；``None`` 返回 ``未选中结果``
    :param staging_dir_str: 暂存区目录字符串（``None`` 或空字符串用默认目录）
    :param last_report_root: 上次扫描报告根路径（用于相对路径计算）
    :param skip_store: 跳过存储实例
    :return: 操作消息字符串

    返回值语义：

    - 未选中结果 → ``未选中结果``
    - 复制成功 → ``已移至暂存: <隔离路径>`` 并标记跳过
    - 复制失败 → ``移至暂存失败: <错误>``
    """
    if result is None:
        return "未选中结果"

    # iter-133：压缩包内部条目时操作 archive_path（压缩包文件本身）
    source_path = result.archive_path if result.archive_path is not None else result.path

    # 计算暂存区隔离目录
    staging_root = Path(staging_dir_str) if staging_dir_str else detect_default_staging_dir()
    quarantine_dir = staging_root / "quarantine"
    scan_root = last_report_root if last_report_root is not None else source_path.parent

    # 保留相对扫描根目录的目录结构
    try:
        rel_path = source_path.relative_to(scan_root)
    except ValueError:
        # 不在扫描根下（如绝对路径跨盘符），仅保留文件名
        rel_path = Path(source_path.name)

    dest = quarantine_dir / rel_path
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
    except OSError as exc:
        logger.warning("移至暂存失败: %s -> %s", source_path, dest, exc_info=True)
        return f"移至暂存失败: {exc}"

    # 标记为跳过，后续扫描自动跳过该文件（压缩包条目标记 archive_path）
    skip_store.add(str(source_path))
    logger.info("已移至暂存: %s -> %s（已标记跳过）", source_path, dest)
    return f"已移至暂存: {dest}（已标记跳过）"
