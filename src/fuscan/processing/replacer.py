"""命中内容替换引擎。

用户在结果详情区点击「替换内容」按钮时，先将源文件复制到备份区（重命名为
``.bak``），再对原文件按规则逐条执行 ``match_texts → replace_with`` 的文本替换。

替换规则由 :class:`fuscan.rules.model.Rule` 的 ``replace`` / ``replace_with``
字段驱动：

- ``replace=True`` 且 ``replace_with`` 非空：执行替换
- ``replace=True`` 但 ``replace_with`` 为空：返回 :class:`ReplaceResult` 提示
  「规则 X 未定义替换内容」，不进行任何文件修改
- ``replace=False``（默认）：跳过该规则的替换

仅支持纯文本文件。二进制格式（PDF/DOCX 等）在 :func:`replace_in_file` 入口
通过扩展名白名单拒绝，避免破坏文件结构。

iter-113 起支持批量替换与撤销：

- :func:`replace_batch`：对一组 :class:`ScanResult` 批量执行替换，聚合结果
- :func:`restore_from_backup`：从 ``.bak`` 备份恢复源文件，支持撤销最近替换
- :class:`BatchReplaceResult`：批量替换聚合结果（成功/失败计数 + 详情列表）

公共 API：

- :class:`ReplaceResult`：单文件替换结果（成功/失败/提示三类状态）
- :class:`BatchReplaceResult`：批量替换聚合结果（iter-113）
- :func:`replace_in_file`：单文件备份+替换的原子操作
- :func:`replace_batch`：批量替换（iter-113）
- :func:`restore_from_backup`：从备份撤销替换（iter-113）
- :func:`is_text_file`：判断文件扩展名是否在可替换的纯文本白名单内
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet
from fuscan.scanner.result import RuleHit

if TYPE_CHECKING:
    from fuscan.scanner.result import ScanResult

__all__ = [
    "BatchReplaceResult",
    "ReplaceResult",
    "ReplaceStatus",
    "is_text_file",
    "replace_batch",
    "replace_in_file",
    "restore_from_backup",
]

logger = logging.getLogger(__name__)

# 可替换的纯文本扩展名白名单（小写，不含前导点）。
# 二进制格式（PDF/DOCX/XLSX/PPT 等）不在此列，避免破坏文件结构。
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        # 纯文本
        "txt",
        "log",
        "md",
        "rst",
        # 配置/数据
        "ini",
        "conf",
        "cfg",
        "properties",
        "env",
        "yaml",
        "yml",
        "toml",
        "json",
        "xml",
        "csv",
        "tsv",
        # 源代码
        "py",
        "js",
        "ts",
        "jsx",
        "tsx",
        "java",
        "kt",
        "c",
        "h",
        "cpp",
        "hpp",
        "cc",
        "cs",
        "go",
        "rs",
        "rb",
        "php",
        "pl",
        "sh",
        "bash",
        "zsh",
        "ps1",
        "bat",
        "cmd",
        # 标记/样式
        "html",
        "htm",
        "css",
        "scss",
        "sass",
        "less",
        "svg",
        # 邮件
        "eml",
        # 脚本/其他
        "sql",
        "gradle",
        "makefile",
    }
)


def is_text_file(path: Path) -> bool:
    """判断文件扩展名是否在可替换的纯文本白名单内。

    :param path: 文件路径
    :return: ``True`` 表示扩展名在白名单内，可安全做文本替换
    """
    return path.suffix.lower().lstrip(".") in _TEXT_EXTENSIONS


@dataclass(frozen=True)
class ReplaceStatus:
    """替换操作状态枚举（字符串常量）。"""

    SUCCESS = "success"
    NO_REPLACE_RULES = "no_replace_rules"
    MISSING_REPLACE_WITH = "missing_replace_with"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    BACKUP_FAILED = "backup_failed"
    REPLACE_FAILED = "replace_failed"


@dataclass(frozen=True)
class ReplaceResult:
    """替换操作结果。

    - ``status == SUCCESS``：替换成功，``backup_path`` 指向 .bak 备份文件，
      ``replaced_count`` 为实际替换的规则条数
    - ``status == NO_REPLACE_RULES``：当前文件命中的规则均未启用 ``replace``，
      不进行任何操作（``message`` 提示用户）
    - ``status == MISSING_REPLACE_WITH``：存在 ``replace=True`` 的规则但
      ``replace_with`` 为空，``missing_rules`` 列出未定义替换内容的规则名
    - ``status == UNSUPPORTED_FILE_TYPE``：文件扩展名不在纯文本白名单
    - ``status == BACKUP_FAILED`` / ``REPLACE_FAILED``：备份或替换过程发生
      OSError，``message`` 包含错误详情
    """

    status: str
    backup_path: Path | None = None
    replaced_count: int = 0
    missing_rules: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""


def replace_in_file(  # noqa: PLR0912
    src: Path,
    hits: tuple[RuleHit, ...],
    ruleset: RuleSet | None,
    backup_root: Path,
    scan_root: Path,
    preserve_relative: bool = True,
    override_replace_with: str | None = None,
) -> ReplaceResult:
    """对单文件执行备份 + 命中内容替换的原子操作。

    流程：

    1. 扩展名白名单校验（二进制格式直接拒绝）
    2. 从 ``hits`` 与 ``ruleset`` 中筛选可替换规则：

       - ``override_replace_with`` 非空（用户自定义替换文本，iter-124）：
         不检查 ``replace`` 标志，对所有有 ``match_texts`` 的命中执行替换，
         统一使用 ``override_replace_with`` 作为替换文本
       - ``override_replace_with`` 为空（规则驱动模式）：仅替换 ``replace=True``
         的规则，使用规则的 ``replace_with`` 字段

    3. 计算备份路径（保留相对路径或仅文件名）并复制源文件为 ``.bak``
    4. 读取源文件 → 按规则逐条替换 → 原子写回

    :param src: 源文件路径
    :param hits: 该文件的规则命中记录
    :param ruleset: 当前规则集（``override_replace_with`` 为空时用于反查
        ``replace`` / ``replace_with``；非空时仅用于规则名查找，可为 ``None``）
    :param backup_root: 备份区根目录（已存在或可创建）
    :param scan_root: 扫描根目录（用于计算相对路径）
    :param preserve_relative: ``True`` 在备份区保留相对扫描根目录的目录结构；
        ``False`` 仅保留文件名，冲突时追加序号
    :param override_replace_with: 用户自定义替换文本（iter-124）。非空时覆盖
        所有规则的 ``replace_with``，且不要求规则 ``replace=True``。默认 ``None``
        走规则驱动模式
    :return: :class:`ReplaceResult` 描述操作结果
    """
    if not is_text_file(src):
        return ReplaceResult(
            status=ReplaceStatus.UNSUPPORTED_FILE_TYPE,
            message=f"不支持的文件类型: {src.suffix or '(无扩展名)'}，仅支持纯文本文件",
        )

    if override_replace_with is not None and override_replace_with != "":
        # 用户自定义替换模式（iter-124）：不检查 replace 标志，对所有命中执行替换
        # 规则集可为 None（仅用于规则名查找，此处构造占位 Rule 供 _apply_replace_text 使用）
        replace_specs: list[tuple[Rule, RuleHit]] = []
        for hit in hits:
            # 仅要求命中规则有 match_texts（无匹配文本则无法替换）
            if not hit.match_texts:
                continue
            # 构造占位 Rule（replace_with 由 override 覆盖，实际不读取 match）
            # LeafMatch pattern 不能为空（__post_init__ 校验），用占位符 " "
            placeholder = Rule(
                name=hit.rule_name,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern=" "),
                replace=True,
                replace_with=override_replace_with,
            )
            replace_specs.append((placeholder, hit))
        if not replace_specs:
            return ReplaceResult(
                status=ReplaceStatus.NO_REPLACE_RULES,
                message="当前文件无匹配文本可替换",
            )
    else:
        # 规则驱动模式：仅替换 replace=True 的规则
        if ruleset is None:
            return ReplaceResult(
                status=ReplaceStatus.NO_REPLACE_RULES,
                message="规则集未加载",
            )
        # 按 rule_name 索引规则集，便于从 RuleHit 反查 Rule.replace / replace_with
        rule_map: dict[str, Rule] = {r.name: r for r in ruleset.rules}
        replace_specs = []
        for hit in hits:
            rule = rule_map.get(hit.rule_name)
            if rule is not None and rule.replace:
                replace_specs.append((rule, hit))

        if not replace_specs:
            return ReplaceResult(
                status=ReplaceStatus.NO_REPLACE_RULES,
                message="当前文件命中的规则均未启用替换（replace: true）",
            )

        missing = [rule.name for rule, _ in replace_specs if not rule.replace_with]
        if missing:
            return ReplaceResult(
                status=ReplaceStatus.MISSING_REPLACE_WITH,
                missing_rules=tuple(missing),
                message=f"规则 {', '.join(missing)} 未定义替换内容（replace_with 为空）",
            )

    # 计算备份路径
    backup_path = _resolve_backup_path(src, backup_root, scan_root, preserve_relative)
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, backup_path)
    except OSError as exc:
        logger.error("备份文件失败: %s -> %s", src, backup_path, exc_info=True)
        return ReplaceResult(
            status=ReplaceStatus.BACKUP_FAILED,
            message=f"备份文件失败: {exc}",
        )

    # 读取源文件内容（UTF-8，失败则尝试二进制替换）
    try:
        content = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 非 UTF-8 文件按二进制读写，避免编码问题导致数据丢失
        try:
            raw = src.read_bytes()
            new_raw, count = _apply_replace_bytes(raw, replace_specs)
            if count == 0:
                # 未替换任何内容：仍保留备份，但返回成功 0 次
                return ReplaceResult(
                    status=ReplaceStatus.SUCCESS,
                    backup_path=backup_path,
                    replaced_count=0,
                    message="未找到可替换的命中内容（备份已保留）",
                )
            _atomic_write_bytes(src, new_raw)
            logger.info("已替换 %s 中 %d 条规则命中（二进制模式）", src, count)
            return ReplaceResult(
                status=ReplaceStatus.SUCCESS,
                backup_path=backup_path,
                replaced_count=count,
            )
        except OSError as exc:
            logger.error("二进制替换失败: %s", src, exc_info=True)
            return ReplaceResult(
                status=ReplaceStatus.REPLACE_FAILED,
                message=f"替换失败: {exc}",
                backup_path=backup_path,
            )

    # 文本模式替换
    new_content, count = _apply_replace_text(content, replace_specs)
    try:
        _atomic_write_text(src, new_content)
    except OSError as exc:
        logger.error("写回文件失败: %s", src, exc_info=True)
        return ReplaceResult(
            status=ReplaceStatus.REPLACE_FAILED,
            message=f"写回文件失败: {exc}",
            backup_path=backup_path,
        )

    logger.info("已替换 %s 中 %d 条规则命中，备份: %s", src, count, backup_path)
    return ReplaceResult(
        status=ReplaceStatus.SUCCESS,
        backup_path=backup_path,
        replaced_count=count,
    )


# ----------------------------- iter-113 批量替换与撤销 -----------------------------


@dataclass(frozen=True)
class BatchReplaceResult:
    """批量替换聚合结果。

    - ``total``：传入的结果总数
    - ``succeeded``：实际执行替换且成功的文件数（``status == SUCCESS``）
    - ``skipped``：跳过的文件数（无 replace=True 规则 / 非文本文件 / 缺 replace_with）
    - ``failed``：失败的文件数（备份/替换 OSError）
    - ``total_replaced_count``：所有成功文件的实际替换规则条数总和
    - ``details``：每个文件的 ``(path, ReplaceResult)`` 元组列表，便于 UI 展示
    - ``backup_paths``：所有成功替换的备份路径列表，供 :func:`restore_from_backup` 撤销
    """

    total: int
    succeeded: int
    skipped: int
    failed: int
    total_replaced_count: int
    details: tuple[tuple[Path, ReplaceResult], ...] = field(default_factory=tuple)
    backup_paths: tuple[Path, ...] = field(default_factory=tuple)

    @property
    def message(self) -> str:
        """聚合消息供 UI 显示。"""
        return (
            f"批量替换完成：成功 {self.succeeded}/{self.total}，"
            f"跳过 {self.skipped}，失败 {self.failed}，"
            f"共替换 {self.total_replaced_count} 条规则"
        )


def replace_batch(
    results: tuple[ScanResult, ...],
    ruleset: RuleSet | None,
    backup_root: Path,
    scan_root: Path,
    preserve_relative: bool = True,
    override_replace_with: str | None = None,
) -> BatchReplaceResult:
    """对一组 :class:`ScanResult` 批量执行备份+替换，返回聚合结果。

    单个文件失败不影响其他文件，最终汇总为 :class:`BatchReplaceResult`。
    适合 UI「全部替换」按钮调用，传入过滤后的结果列表。

    :param results: 待替换的结果元组（通常来自 ``ResultListModel.filtered_results``）
    :param ruleset: 当前规则集（``override_replace_with`` 非空时可为 ``None``）
    :param backup_root: 备份区根目录
    :param scan_root: 扫描根目录（用于相对路径计算）
    :param preserve_relative: ``True`` 在备份区保留相对目录结构
    :param override_replace_with: 用户自定义替换文本（iter-124）。非空时覆盖
        所有规则的 ``replace_with``，不要求规则 ``replace=True``。默认 ``None``
    :return: :class:`BatchReplaceResult` 含每个文件的详情
    """
    details: list[tuple[Path, ReplaceResult]] = []
    backup_paths: list[Path] = []
    succeeded = 0
    skipped = 0
    failed = 0
    total_replaced = 0

    for result in results:
        # 压缩包内部条目不支持替换
        if result.archive_path is not None:
            skipped += 1
            details.append(
                (
                    result.path,
                    ReplaceResult(
                        status=ReplaceStatus.UNSUPPORTED_FILE_TYPE,
                        message="压缩包内部条目不支持替换",
                    ),
                )
            )
            continue

        replace_result = replace_in_file(
            src=result.path,
            hits=result.hits,
            ruleset=ruleset,
            backup_root=backup_root,
            scan_root=scan_root,
            preserve_relative=preserve_relative,
            override_replace_with=override_replace_with,
        )
        details.append((result.path, replace_result))

        if replace_result.status == ReplaceStatus.SUCCESS:
            succeeded += 1
            total_replaced += replace_result.replaced_count
            if replace_result.backup_path is not None:
                backup_paths.append(replace_result.backup_path)
        elif replace_result.status in (
            ReplaceStatus.NO_REPLACE_RULES,
            ReplaceStatus.MISSING_REPLACE_WITH,
            ReplaceStatus.UNSUPPORTED_FILE_TYPE,
        ):
            skipped += 1
        else:
            failed += 1

    return BatchReplaceResult(
        total=len(results),
        succeeded=succeeded,
        skipped=skipped,
        failed=failed,
        total_replaced_count=total_replaced,
        details=tuple(details),
        backup_paths=tuple(backup_paths),
    )


def restore_from_backup(backup_path: Path, dest: Path) -> str:
    """从 ``.bak`` 备份恢复源文件，撤销最近一次替换。

    流程：

    1. 校验备份文件存在
    2. ``shutil.copy2`` 覆盖源文件（保留备份文件本身，便于多次撤销）
    3. 返回操作消息供 UI 显示

    :param backup_path: ``.bak`` 备份文件路径
    :param dest: 源文件路径（被恢复的目标）
    :return: 操作消息字符串
    """
    if not backup_path.exists():
        return f"备份文件不存在: {backup_path}"
    try:
        shutil.copy2(backup_path, dest)
    except OSError as exc:
        logger.error("从备份恢复失败: %s -> %s", backup_path, dest, exc_info=True)
        return f"恢复失败: {exc}"
    logger.info("已从备份恢复: %s -> %s", backup_path, dest)
    return f"已从备份恢复: {dest}"


def _resolve_backup_path(
    src: Path,
    backup_root: Path,
    scan_root: Path,
    preserve_relative: bool,
) -> Path:
    """计算备份文件路径。

    ``preserve_relative=True`` 时保留源文件相对扫描根目录的目录结构，
    备份文件名为 ``{原名}.bak``；``preserve_relative=False`` 时仅保留文件名，
    同名冲突时追加 ``.1`` / ``.2`` 序号避免覆盖。

    :param src: 源文件路径
    :param backup_root: 备份区根目录
    :param scan_root: 扫描根目录（用于计算相对路径）
    :param preserve_relative: 是否保留相对路径
    :return: 备份文件路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    bak_name = f"{src.name}.bak"
    if preserve_relative:
        try:
            rel = src.relative_to(scan_root)
            # 相对路径的父目录结构原样保留，文件名加 .bak 后缀
            return backup_root / rel.parent / bak_name
        except ValueError:
            # src 不在 scan_root 下（跨盘符或绝对路径），回退到仅文件名
            logger.debug("src 不在 scan_root 下，回退到仅文件名: %s", src)
    # 仅文件名模式：冲突时追加序号
    candidate = backup_root / bak_name
    if not candidate.exists():
        return candidate
    for i in range(1, 10000):
        candidate = backup_root / f"{src.stem}.{i}{src.suffix}.bak"
        if not candidate.exists():
            return candidate
    # 理论上不可达；防御性返回首个候选
    return backup_root / bak_name  # pragma: no cover


def _apply_replace_text(
    content: str,
    specs: list[tuple[Rule, RuleHit]],
) -> tuple[str, int]:
    """对文本内容按规则逐条替换 ``match_texts → replace_with``。

    同一条规则命中的多个文本依次替换；不同规则按 ``specs`` 顺序应用。
    已替换的区间不会再次匹配后续规则（避免链式替换导致内容损坏）。

    :param content: 原始文本
    :param specs: ``(Rule, RuleHit)`` 列表，按规则集顺序
    :return: ``(新内容, 实际替换的规则条数)``
    """
    # 收集所有 (关键词, 替换文本) 对，按关键词长度降序避免短词先替换破坏长词
    replacements: list[tuple[str, str]] = []
    for rule, hit in specs:
        for kw in hit.match_texts:
            if kw:  # 跳过空字符串
                replacements.append((kw, rule.replace_with))
    if not replacements:
        return content, 0
    # 按关键词长度降序：长关键词优先，避免短关键词破坏长关键词匹配
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    new_content = content
    replaced_rule_count = 0
    for rule, hit in specs:
        rule_replaced = False
        for kw in hit.match_texts:
            if kw and kw in new_content:
                new_content = new_content.replace(kw, rule.replace_with)
                rule_replaced = True
        if rule_replaced:
            replaced_rule_count += 1
    return new_content, replaced_rule_count


def _apply_replace_bytes(
    raw: bytes,
    specs: list[tuple[Rule, RuleHit]],
) -> tuple[bytes, int]:
    """对二进制内容按规则逐条替换（UTF-8 编码关键词）。

    与 :func:`_apply_replace_text` 类似，但操作 bytes。关键词与替换文本
    统一编码为 UTF-8 bytes 进行 ``bytes.replace``。

    :param raw: 原始字节
    :param specs: ``(Rule, RuleHit)`` 列表
    :return: ``(新字节, 实际替换的规则条数)``
    """
    replacements: list[tuple[bytes, bytes]] = []
    for rule, hit in specs:
        for kw in hit.match_texts:
            if kw:
                try:
                    replacements.append((kw.encode("utf-8"), rule.replace_with.encode("utf-8")))
                except UnicodeEncodeError:  # pragma: no cover - Python 字符串均可 UTF-8 编码
                    continue
    if not replacements:
        return raw, 0
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    new_raw = raw
    replaced_rule_count = 0
    for rule, hit in specs:
        rule_replaced = False
        for kw in hit.match_texts:
            if not kw:  # pragma: no cover - 外层 replacements 收集已过滤空 kw
                continue
            try:
                kw_bytes = kw.encode("utf-8")
            except UnicodeEncodeError:  # pragma: no cover - Python 字符串均可 UTF-8 编码
                continue
            if kw_bytes in new_raw:
                new_raw = new_raw.replace(kw_bytes, rule.replace_with.encode("utf-8"))
                rule_replaced = True
        if rule_replaced:
            replaced_rule_count += 1
    return new_raw, replaced_rule_count


def _atomic_write_text(path: Path, content: str) -> None:
    """原子写入文本文件：写入临时文件后 ``Path.replace`` 覆盖。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    """原子写入二进制文件：写入临时文件后 ``Path.replace`` 覆盖。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
