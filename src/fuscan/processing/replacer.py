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

支持批量替换与撤销：

- :func:`replace_batch`：对一组 :class:`ScanResult` 批量执行替换，聚合结果
- :func:`restore_from_backup`：从 ``.bak`` 备份恢复源文件，支持撤销最近替换
- :class:`BatchReplaceResult`：批量替换聚合结果（成功/失败计数 + 详情列表）

公共 API：

- :class:`ReplaceResult`：单文件替换结果（成功/失败/提示三类状态）
- :class:`BatchReplaceResult`：批量替换聚合结果
- :func:`replace_in_file`：单文件备份+替换的原子操作
- :func:`replace_batch`：批量替换
- :func:`restore_from_backup`：从备份撤销替换
- :func:`is_text_file`：判断文件扩展名是否在可替换的纯文本白名单内
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from fuscan.processing.backup_manifest import BackupManifest
from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Rule, RuleSet
from fuscan.scanner.result import RuleHit
from fuscan.utils.io import atomic_write_bytes, atomic_write_text

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

# 排序键辅助：按元组首元素（关键词）长度排序，约束为 str/bytes 两种类型。
# 用命名函数替代 lambda，让 pyrefly 能推断参数类型（lambda 参数无法标注）。
_K = TypeVar("_K", str, bytes)


def _by_first_len(item: tuple[_K, _K, int]) -> int:
    """返回元组首元素长度，供 ``sort(key=...)`` 按关键词长度降序。"""
    return len(item[0])


def _compute_sha256(data: bytes) -> str:
    """计算字节流的 SHA-256 十六进制摘要（64 字符）。

    与 :func:`fuscan.processing.backup_manifest._sha256_bytes` 算法一致，
    用于重复扫描检测时计算当前源文件 sha256，与 manifest 中 ``post_sha256`` 比对。
    统一用 SHA-256（而非按大小分流）以保证与 manifest 校验算法一致。

    :param data: 任意字节流
    :return: 64 字符十六进制字符串
    """
    return hashlib.sha256(data).hexdigest()


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
    # 备份文件 .bak 完整性校验失败（size/sha256 与 manifest 不一致），
    # 撤销操作前由 :func:`restore_from_backup` 返回此状态，避免恢复出损坏文件
    BACKUP_CORRUPTED = "backup_corrupted"
    # 重复扫描检测命中：当前源文件 sha256 与 manifest 中 post_sha256 一致，
    # 表示文件已被替换且未修改 → 跳过替换，保留原始 .bak 避免覆盖
    ALREADY_REPLACED = "already_replaced"


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
    manifest: BackupManifest | None = None,
) -> ReplaceResult:
    """对单文件执行备份 + 命中内容替换的原子操作。

    流程：

    1. 扩展名白名单校验（二进制格式直接拒绝）
    2. 从 ``hits`` 与 ``ruleset`` 中筛选可替换规则：

       - ``override_replace_with`` 非空（用户自定义替换文本）：
         不检查 ``replace`` 标志，对所有有 ``match_texts`` 的命中执行替换，
         统一使用 ``override_replace_with`` 作为替换文本
       - ``override_replace_with`` 为空（规则驱动模式）：仅替换 ``replace=True``
         的规则，使用规则的 ``replace_with`` 字段

    3. ``manifest`` 非空时执行重复扫描检测：读取当前源文件 sha256，与 manifest
       中记录的 ``post_sha256``（上次替换后 sha256）比对，一致 → 返回
       :data:`ReplaceStatus.ALREADY_REPLACED`，跳过替换避免覆盖原始 ``.bak``
    4. 计算备份路径（保留相对路径或仅文件名）并复制源文件为 ``.bak``
    5. 读取源文件 → 按规则逐条替换 → 原子写回
    6. ``manifest`` 非空时记录 :class:`BackupEntry`（src/backup 路径 +
       替换前后 sha256 + 时间戳），供撤销前完整性校验与后续重复扫描检测

    :param src: 源文件路径
    :param hits: 该文件的规则命中记录
    :param ruleset: 当前规则集（``override_replace_with`` 为空时用于反查
        ``replace`` / ``replace_with``；非空时仅用于规则名查找，可为 ``None``）
    :param backup_root: 备份区根目录（已存在或可创建）
    :param scan_root: 扫描根目录（用于计算相对路径）
    :param preserve_relative: ``True`` 在备份区保留相对扫描根目录的目录结构；
        ``False`` 仅保留文件名，冲突时追加序号
    :param override_replace_with: 用户自定义替换文本。非空时覆盖
        所有规则的 ``replace_with``，且不要求规则 ``replace=True``。默认 ``None``
        走规则驱动模式
    :param manifest: 备份元数据存储。非空时启用重复扫描检测与完整性校验记录；
        ``None`` 时跳过 manifest 相关逻辑（向后兼容）
    :return: :class:`ReplaceResult` 描述操作结果
    """
    if not is_text_file(src):
        return ReplaceResult(
            status=ReplaceStatus.UNSUPPORTED_FILE_TYPE,
            message=f"不支持的文件类型: {src.suffix or '(无扩展名)'}，仅支持纯文本文件",
        )

    if override_replace_with is not None and override_replace_with != "":
        # 用户自定义替换模式：不检查 replace 标志，对所有命中执行替换
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

    # 重复扫描检测（manifest 非空时）—— 在备份前执行，避免覆盖原始 .bak。
    # 读取当前源文件字节并计算 sha256，与 manifest 中 post_sha256（上次替换后
    # sha256）比对：一致 → 文件已被替换且未修改 → 跳过替换，保留原始 .bak
    src_bytes_cached: bytes | None = None
    if manifest is not None:
        try:
            src_bytes_cached = src.read_bytes()
        except OSError as exc:
            logger.error("读取源文件失败（重复扫描检测）: %s", src, exc_info=True)
            return ReplaceResult(
                status=ReplaceStatus.REPLACE_FAILED,
                message=f"读取源文件失败: {exc}",
            )
        existing_entry = manifest.find_by_src(src)
        if existing_entry is not None:
            current_sha = _compute_sha256(src_bytes_cached)
            if current_sha == existing_entry.post_sha256:
                logger.info("跳过重复替换（文件已替换且未修改）: %s", src)
                return ReplaceResult(
                    status=ReplaceStatus.ALREADY_REPLACED,
                    message="文件已被替换且未修改，跳过替换以保留原始备份",
                    backup_path=Path(existing_entry.backup_path),
                    replaced_count=0,
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
    # 复用重复扫描检测时读取的 src_bytes_cached，避免二次 I/O
    try:
        if src_bytes_cached is not None:
            content = src_bytes_cached.decode("utf-8")
        else:
            content = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # 非 UTF-8 文件按二进制读写，避免编码问题导致数据丢失
        try:
            raw = src_bytes_cached if src_bytes_cached is not None else src.read_bytes()
            new_raw, count = _apply_replace_bytes(raw, replace_specs)
            if count == 0:
                # 未替换任何内容：仍保留备份，但返回成功 0 次
                return ReplaceResult(
                    status=ReplaceStatus.SUCCESS,
                    backup_path=backup_path,
                    replaced_count=0,
                    message="未找到可替换的命中内容（备份已保留）",
                )
            atomic_write_bytes(src, new_raw)
            # 记录 manifest（替换前后字节内容，供撤销前校验与重复扫描检测）
            if manifest is not None:
                manifest.record(src, backup_path, raw, new_raw)
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
        atomic_write_text(src, new_content)
    except OSError as exc:
        logger.error("写回文件失败: %s", src, exc_info=True)
        return ReplaceResult(
            status=ReplaceStatus.REPLACE_FAILED,
            message=f"写回文件失败: {exc}",
            backup_path=backup_path,
        )

    # 记录 manifest（文本模式：优先用 src_bytes_cached 保证 sha256 与磁盘一致；
    # 缓存缺失时用 content.encode("utf-8")，与 atomic_write_text 的 UTF-8 编码一致）
    if manifest is not None:
        src_bytes_for_manifest = src_bytes_cached if src_bytes_cached is not None else content.encode("utf-8")
        post_bytes = new_content.encode("utf-8")
        manifest.record(src, backup_path, src_bytes_for_manifest, post_bytes)

    logger.info("已替换 %s 中 %d 条规则命中，备份: %s", src, count, backup_path)
    return ReplaceResult(
        status=ReplaceStatus.SUCCESS,
        backup_path=backup_path,
        replaced_count=count,
    )


# ----------------------------- 批量替换与撤销 -----------------------------


@dataclass(frozen=True)
class BatchReplaceResult:
    """批量替换聚合结果。

    - ``total``：传入的结果总数
    - ``succeeded``：实际执行替换且成功的文件数（``status == SUCCESS``）
    - ``skipped``：跳过的文件数（无 replace=True 规则 / 非文本文件 / 缺 replace_with /
      已替换且未修改的 ``ALREADY_REPLACED``）
    - ``failed``：失败的文件数（备份/替换 OSError）
    - ``total_replaced_count``：所有成功文件的实际替换规则条数总和
    - ``details``：每个文件的 ``(path, ReplaceResult)`` 元组列表，便于 UI 展示
    - ``backup_paths``：所有成功替换的备份路径列表，供 :func:`restore_from_backup` 撤销
    - ``rolled_back``：``atomic=True`` 模式下任一文件失败触发回滚时为 ``True``，
      此时 ``succeeded=0`` 且所有已替换文件已恢复到 ``.bak``，``backup_paths`` 为空
    """

    total: int
    succeeded: int
    skipped: int
    failed: int
    total_replaced_count: int
    details: tuple[tuple[Path, ReplaceResult], ...] = field(default_factory=tuple)
    backup_paths: tuple[Path, ...] = field(default_factory=tuple)
    rolled_back: bool = False

    @property
    def message(self) -> str:
        """聚合消息供 UI 显示。"""
        base = (
            f"批量替换完成：成功 {self.succeeded}/{self.total}，"
            f"跳过 {self.skipped}，失败 {self.failed}，"
            f"共替换 {self.total_replaced_count} 条规则"
        )
        if self.rolled_back:
            return f"{base}（已自动回滚所有替换）"
        return base


def replace_batch(
    results: tuple[ScanResult, ...],
    ruleset: RuleSet | None,
    backup_root: Path,
    scan_root: Path,
    preserve_relative: bool = True,
    override_replace_with: str | None = None,
    manifest: BackupManifest | None = None,
    atomic: bool = False,
) -> BatchReplaceResult:
    """对一组 :class:`ScanResult` 批量执行备份+替换，返回聚合结果。

    单个文件失败不影响其他文件（``atomic=False`` 默认行为），最终汇总为
    :class:`BatchReplaceResult`。适合 UI「全部替换」按钮调用。

    事务模式（``atomic=True``）：任一文件发生 ``BACKUP_FAILED`` / ``REPLACE_FAILED``
    时自动回滚所有已成功替换的文件（从对应 ``.bak`` 恢复），返回
    :class:`BatchReplaceResult` 含 ``rolled_back=True``。跳过类状态
    （``NO_REPLACE_RULES`` / ``MISSING_REPLACE_WITH`` / ``UNSUPPORTED_FILE_TYPE`` /
    ``ALREADY_REPLACED``）不触发回滚。

    :param results: 待替换的结果元组（通常来自 ``ResultListModel.filtered_results``）
    :param ruleset: 当前规则集（``override_replace_with`` 非空时可为 ``None``）
    :param backup_root: 备份区根目录
    :param scan_root: 扫描根目录（用于相对路径计算）
    :param preserve_relative: ``True`` 在备份区保留相对目录结构
    :param override_replace_with: 用户自定义替换文本。非空时覆盖
        所有规则的 ``replace_with``，不要求规则 ``replace=True``。默认 ``None``
    :param manifest: 备份元数据存储。非空时传给 :func:`replace_in_file` 启用
        重复扫描检测与完整性记录；``None`` 时跳过 manifest 相关逻辑
    :param atomic: ``True`` 启用事务模式，任一文件失败自动回滚所有已替换文件。
        默认 ``False`` 保持向后兼容
    :return: :class:`BatchReplaceResult` 含每个文件的详情
    """
    details: list[tuple[Path, ReplaceResult]] = []
    backup_paths: list[Path] = []
    succeeded = 0
    skipped = 0
    failed = 0
    total_replaced = 0
    # 事务模式下记录已成功替换的 (src, backup) 配对，用于回滚
    replaced_pairs: list[tuple[Path, Path]] = []

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
            manifest=manifest,
        )
        details.append((result.path, replace_result))

        if replace_result.status == ReplaceStatus.SUCCESS:
            succeeded += 1
            total_replaced += replace_result.replaced_count
            if replace_result.backup_path is not None:
                backup_paths.append(replace_result.backup_path)
                replaced_pairs.append((result.path, replace_result.backup_path))
        elif replace_result.status in (
            ReplaceStatus.NO_REPLACE_RULES,
            ReplaceStatus.MISSING_REPLACE_WITH,
            ReplaceStatus.UNSUPPORTED_FILE_TYPE,
            ReplaceStatus.ALREADY_REPLACED,
        ):
            skipped += 1
        else:
            failed += 1
            # 事务模式下任一失败立即回滚所有已替换文件
            if atomic and replaced_pairs:
                logger.warning(
                    "事务模式检测到失败 (%s)，回滚 %d 个已替换文件",
                    result.path,
                    len(replaced_pairs),
                )
                for src_path, backup_path in replaced_pairs:
                    rollback_msg = restore_from_backup(backup_path, src_path, manifest=manifest)
                    if not rollback_msg.startswith("已从备份恢复"):
                        logger.error("回滚失败: %s -> %s: %s", backup_path, src_path, rollback_msg)
                # 回滚后：succeeded=0，backup_paths 清空，标记 rolled_back
                return BatchReplaceResult(
                    total=len(results),
                    succeeded=0,
                    skipped=skipped,
                    failed=failed,
                    total_replaced_count=0,
                    details=tuple(details),
                    backup_paths=(),
                    rolled_back=True,
                )

    return BatchReplaceResult(
        total=len(results),
        succeeded=succeeded,
        skipped=skipped,
        failed=failed,
        total_replaced_count=total_replaced,
        details=tuple(details),
        backup_paths=tuple(backup_paths),
    )


def restore_from_backup(
    backup_path: Path,
    dest: Path,
    manifest: BackupManifest | None = None,
) -> str:
    """从 ``.bak`` 备份恢复源文件，撤销最近一次替换。

    流程：

    1. 校验备份文件存在
    2. ``manifest`` 非空时校验 ``.bak`` 完整性（size + sha256 与 manifest 一致），
       校验失败 → 返回 ``备份文件损坏: <path>``，拒绝恢复以避免文件损坏
    3. ``shutil.copy2`` 覆盖源文件（保留备份文件本身，便于多次撤销）
    4. ``manifest`` 非空时从 manifest 删除该 src 条目（撤销后不再需要校验信息）
    5. 返回操作消息供 UI 显示

    :param backup_path: ``.bak`` 备份文件路径
    :param dest: 源文件路径（被恢复的目标）
    :param manifest: 备份元数据存储。非空时启用完整性校验与条目清理；
        ``None`` 时跳过校验（向后兼容）
    :return: 操作消息字符串

    返回值语义：

    - ``备份文件不存在: <path>`` —— ``.bak`` 不存在
    - ``备份文件损坏: <path>`` —— manifest 校验失败（size/sha256 不匹配）
    - ``备份元数据缺失: <path>`` —— manifest 中无对应条目（manifest 非空但找不到记录）
    - ``恢复失败: <error>`` —— ``shutil.copy2`` 抛 OSError
    - ``已从备份恢复: <dest>`` —— 成功
    """
    if not backup_path.exists():
        return f"备份文件不存在: {backup_path}"

    # manifest 完整性校验：撤销前确认 .bak 未被外部修改/损坏
    if manifest is not None and not manifest.verify(backup_path):
        # 区分"manifest 无条目"与"size/sha256 不匹配"两种情况
        entry = manifest.find_by_src(dest)
        if entry is None:
            logger.warning("manifest 中无备份条目: %s", backup_path)
            return f"备份元数据缺失: {backup_path}"
        logger.warning("备份文件完整性校验失败: %s", backup_path)
        return f"备份文件损坏: {backup_path}"

    try:
        shutil.copy2(backup_path, dest)
    except OSError as exc:
        logger.error("从备份恢复失败: %s -> %s", backup_path, dest, exc_info=True)
        return f"恢复失败: {exc}"

    # 撤销成功后从 manifest 删除条目（撤销后不再需要校验信息）
    if manifest is not None:
        manifest.remove(dest)

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

    所有 (关键词, 替换文本) 对按关键词长度降序排列后统一应用，
    确保长关键词优先替换，避免短关键词先替换破坏长关键词匹配。

    :param content: 原始文本
    :param specs: ``(Rule, RuleHit)`` 列表，按规则集顺序
    :return: ``(新内容, 实际替换的规则条数)``
    """
    # 收集所有 (关键词, 替换文本, 规则索引) 三元组
    indexed: list[tuple[str, str, int]] = []
    for rule_idx, (rule, hit) in enumerate(specs):
        for kw in hit.match_texts:
            if kw:
                indexed.append((kw, rule.replace_with, rule_idx))
    if not indexed:
        return content, 0
    # 按关键词长度降序：长关键词优先，避免短关键词破坏长关键词匹配
    indexed.sort(key=_by_first_len, reverse=True)

    new_content = content
    replaced_rule_indices: set[int] = set()
    for kw, replace_with, rule_idx in indexed:
        if kw in new_content:
            new_content = new_content.replace(kw, replace_with)
            replaced_rule_indices.add(rule_idx)
    return new_content, len(replaced_rule_indices)


def _apply_replace_bytes(
    raw: bytes,
    specs: list[tuple[Rule, RuleHit]],
) -> tuple[bytes, int]:
    """对二进制内容按规则逐条替换（UTF-8 编码关键词）。

    与 :func:`_apply_replace_text` 逻辑一致：所有 (关键词, 替换文本) 对
    按长度降序排列后统一应用，确保长关键词优先替换。

    :param raw: 原始字节
    :param specs: ``(Rule, RuleHit)`` 列表
    :return: ``(新字节, 实际替换的规则条数)``
    """
    indexed: list[tuple[bytes, bytes, int]] = []
    for rule_idx, (rule, hit) in enumerate(specs):
        for kw in hit.match_texts:
            if not kw:
                continue
            try:
                indexed.append((kw.encode("utf-8"), rule.replace_with.encode("utf-8"), rule_idx))
            except UnicodeEncodeError:  # pragma: no cover - Python 字符串均可 UTF-8 编码
                continue
    if not indexed:
        return raw, 0
    indexed.sort(key=_by_first_len, reverse=True)

    new_raw = raw
    replaced_rule_indices: set[int] = set()
    for kw_bytes, replace_bytes, rule_idx in indexed:
        if kw_bytes in new_raw:
            new_raw = new_raw.replace(kw_bytes, replace_bytes)
            replaced_rule_indices.add(rule_idx)
    return new_raw, len(replaced_rule_indices)
