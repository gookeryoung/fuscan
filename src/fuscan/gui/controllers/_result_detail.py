"""扫描结果详情展示与操作纯函数。

将 :class:`ScanController` 中与选中结果详情展示、内容替换、移至暂存相关的
纯逻辑抽离到模块级，便于独立测试。命中详情 dict 列表分两级构建：
``build_detail_hits_light`` 纯内存不读文件（context 取 ``hit.detail`` 占位，
供主线程即时展示），``build_detail_hits_full`` 读文件补齐上下文（供后台
worker 调用）。``can_replace_result`` 判断当前结果是否可执行替换，
``replace_selected`` 与 ``move_to_staging`` 执行实际文件操作并返回操作消息
供 QML 显示。

公共 API：

- :func:`build_detail_hits_light`：构造命中详情 dict 列表（不读文件，即时）
- :func:`build_detail_hits_full`：构造命中详情 dict 列表（读文件补上下文）
- :func:`build_detail_hits_model`：``build_detail_hits_full`` 的向后兼容别名
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
from fuscan.processing.replacer import ReplaceStatus, replace_in_file
from fuscan.processing.storage import default_backup_dir, detect_default_staging_dir

if TYPE_CHECKING:
    from fuscan.processing.skip_store import SkipStore
    from fuscan.rules.model import RuleSet
    from fuscan.scanner.result import ScanResult

__all__ = [
    "build_detail_hits_full",
    "build_detail_hits_light",
    "build_detail_hits_model",
    "can_replace_result",
    "move_to_staging",
    "replace_selected",
]

logger = logging.getLogger(__name__)

# 上下文提取：匹配行前后各保留的行数
_CONTEXT_LINES = 2
# 上下文读取的文件大小上限（1MB），超过则跳过上下文提取。
# context 仅取匹配行前后各 2 行，超 1MB 的文件多为日志/数据转储，
# 读全文收益低而代价高，故用阈值控制单文件读盘/分行开销。
# DetailWorker 在后台线程执行，1MB 读盘+分行不影响 UI 响应。
_MAX_CONTEXT_FILE_SIZE = 1024 * 1024


def _extract_contexts_batch(path: Path, match_texts: list[str]) -> dict[str, str]:
    """批量提取多个匹配文本的上下文（同一文件只读一次、只分行一次）。

    对 ``match_texts`` 去空去重后，一遍 ``enumerate`` 扫描文件行，为每个
    匹配文本定位其**首个**命中行，随后按 ``_CONTEXT_LINES`` 窗口切片生成
    带 ``>>> `` 前缀标记的上下文文本。相比逐条 :func:`_extract_context`
    重复读盘/分行/搜索，复杂度从 O(命中数 × 行数) 降至 O(行数)。

    文件不存在、超过 :data:`_MAX_CONTEXT_FILE_SIZE`、读取失败时返回空 dict
    （调用方回退 ``hit.detail``）。不按扩展名白名单过滤——上下文提取只读
    不写，不会破坏文件；二进制文件经 ``errors="replace"`` 解码后
    ``match_text in line`` 通常找不到匹配，自然返回空。

    :param path: 文件路径
    :param match_texts: 待定位的匹配文本列表（可含空串/重复，内部去空去重）
    :return: ``{match_text: context}`` 映射；未定位或整体失败的项不出现在结果中
    """
    pending = {mt for mt in match_texts if mt}
    if not pending:
        return {}
    try:
        if not path.exists():
            return {}
        # stat 检查大小（O(1)），超过阈值则跳过，避免读大文件
        size = path.stat().st_size
        if size > _MAX_CONTEXT_FILE_SIZE:
            return {}
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    lines = content.splitlines()
    # 一遍扫描定位每个 match_text 的首个命中行；命中即移出 pending，pending 空则提前结束
    match_indices: dict[str, int] = {}
    for i, line in enumerate(lines):
        if not pending:
            break
        # 遍历 pending 快照，命中即记录并移出
        for mt in [mt for mt in pending if mt in line]:
            match_indices[mt] = i
            pending.discard(mt)

    contexts: dict[str, str] = {}
    for mt, match_idx in match_indices.items():
        start = max(0, match_idx - _CONTEXT_LINES)
        end = min(len(lines), match_idx + _CONTEXT_LINES + 1)
        parts = []
        for i in range(start, end):
            prefix = ">>> " if i == match_idx else "    "
            parts.append(f"{prefix}{lines[i]}")
        contexts[mt] = "\n".join(parts)
    return contexts


def _extract_context(path: Path, match_text: str) -> str:
    """从文件中提取匹配文本的上下文（前后各 ``_CONTEXT_LINES`` 行）。

    薄封装 :func:`_extract_contexts_batch`——匹配行用 ``>>> `` 前缀标记，
    便于 QML 高亮显示。文件过大或非文本文件时返回空字符串。

    :param path: 文件路径
    :param match_text: 匹配文本（在文件中搜索该文本所在行）
    :return: 上下文文本（多行），无匹配或读取失败返回空字符串
    """
    return _extract_contexts_batch(path, [match_text]).get(match_text, "")


def build_detail_hits_light(result: ScanResult | None) -> list[dict[str, object]]:
    """构造选中结果的命中详情列表（纯内存，不读文件，主线程即时可用）。

    键集与 :func:`build_detail_hits_full` **完全一致**，仅 ``context`` 字段
    取 ``hit.detail`` 占位（不读文件补上下文），供主线程在选中变化时即时
    渲染规则名/匹配文本，完整上下文由后台 worker 调 ``build_detail_hits_full``
    补齐后替换。

    :param result: 选中结果；``None`` 返回空列表
    :return: 命中详情 dict 列表
    """
    if result is None:
        return []
    model: list[dict[str, object]] = []
    for hit in result.hits:
        model.append(
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
        )
    return model


def build_detail_hits_full(result: ScanResult | None) -> list[dict[str, object]]:
    """构造选中结果的命中详情列表（读文件补齐上下文，供后台 worker 调用）。

    每条命中包含：规则名、严重度文本/色值、上下文（文件内容
    上下文，前后各 2 行，匹配行用 ``>>>`` 标记）、匹配文本、匹配条数、
    匹配目标（filename/content/path）、规则描述（供详情面板展示）。

    压缩包内部条目无法读取文件内容，context 取 ``hit.detail`` 兜底（等价
    :func:`build_detail_hits_light`）；非压缩包条目对同一文件的所有命中
    走 :func:`_extract_contexts_batch` 一次性定位，避免逐条重复读盘/分行。

    :param result: 选中结果；``None`` 返回空列表
    :return: 命中详情 dict 列表
    """
    if result is None:
        return []
    # 压缩包内部条目无法读取文件内容，等价 light（context 用 detail 兜底）
    if result.archive_path is not None:
        return build_detail_hits_light(result)

    file_path = result.path
    # 同文件所有命中一次性批量定位上下文（去空去重内部处理）
    contexts = _extract_contexts_batch(file_path, [hit.match_text for hit in result.hits])
    model: list[dict[str, object]] = []
    for hit in result.hits:
        context = contexts.get(hit.match_text) or hit.detail
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


# build_detail_hits_model 保留为 build_detail_hits_full 的向后兼容别名，
# QML 绑定与既有测试无需改动即可复用完整构建逻辑。
build_detail_hits_model = build_detail_hits_full


def can_replace_result(result: ScanResult | None, ruleset: RuleSet | None) -> bool:  # noqa: ARG001
    """判断当前结果是否可执行替换。

    放宽条件——只要选中结果且非压缩包内部条目即可替换
    （用户自定义替换文本 ``override_replace_with`` 模式不要求规则
    ``replace=True``）。``ruleset`` 参数保留向后兼容，实际不再要求规则集加载。

    :param result: 选中结果
    :param ruleset: 当前规则集（可为 ``None``，不影响判断）
    :return: 可替换返回 ``True``
    """
    if result is None or result.archive_path is not None:
        return False
    # 用户自定义替换模式不要求规则 replace=True，
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
    :param override_replace_with: 用户自定义替换文本。非空时覆盖
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

    压缩包内部条目（``archive_path`` 非 None）时，移至暂存的是
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

    # 压缩包内部条目时操作 archive_path（压缩包文件本身）
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
