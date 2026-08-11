"""扫描器纯函数与常量辅助模块。

从 :mod:`fuscan.scanner.scanner` 抽离的不依赖 :class:`Scanner` 实例状态的
纯函数与模块级常量，便于独立测试与复用。

公共 API：

- :data:`BATCH_THRESHOLD`：批量写入阈值
- :data:`PROGRESS_LIST_MAX`：进度收集列表上限
- :data:`GIL_YIELD_THRESHOLD_S`：GIL 让步时间阈值（秒）
- :func:`build_hit_from_match`：从 :class:`MatchResult` 构造 :class:`RuleHit`
- :func:`rebuild_hit_from_cache`：从缓存 :class:`RuleHit` 重建（填回 rule_name）
- :func:`default_extract_content`：默认内容提供器
- :func:`default_extract_content_with_hash`：带哈希的内容提供器
- :func:`empty_content_provider`：空内容提供器
- :func:`spec_needs_content`：检查 MatchSpec 是否包含 CONTENT 目标
- :func:`is_minified_content`：按内容特征识别压缩/打包产物（跳过 CONTENT 匹配）
- :func:`cancel_all_futures`：取消全部 future
- :func:`normalize_max_file_size`：规范化大文件跳过阈值
"""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Iterable
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from fuscan.cache.hashes import hash_bytes
from fuscan.config import DEFAULT_MAX_FILE_SIZE
from fuscan.extractors import (
    extract_content_from_bytes_with_retry,
    extract_content_with_fallback,
    get_extractor,
)
from fuscan.rules.model import MatchSpec, MatchTarget, Rule
from fuscan.scanner.result import MatchResult, RuleHit

if TYPE_CHECKING:
    from fuscan.scanner.context import FileEntry

# sre_parse / re._parser：正则 AST 解析（用于 CONTENT 桶与组合规则字面量提取）。
# Python 3.11+ 从 ``re._parser`` 暴露，3.10 仍为废弃的 ``sre_parse``。
try:
    from re import _parser as _sre_parse  # type: ignore[missing-module-attribute]  # Python 3.11+
except ImportError:
    # Python 3.10：sre_parse 仍可用但已废弃，屏蔽 DeprecationWarning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import sre_parse as _sre_parse  # type: ignore[no-redef,import-not-found]

__all__ = [
    "BATCH_THRESHOLD",
    "DEFAULT_MAX_FILE_SIZE",
    "GIL_YIELD_THRESHOLD_S",
    "PRE_SCAN_EMIT_INTERVAL_S",
    "PROGRESS_LIST_MAX",
    "PROGRESS_MIN_DELTA_FILES",
    "PROGRESS_MIN_DELTA_MATCHES",
    "PROGRESS_SNAPSHOT_TAIL",
    "build_hit_from_match",
    "cancel_all_futures",
    "default_extract_content",
    "default_extract_content_with_hash",
    "empty_content_provider",
    "engine_for_extension",
    "is_minified_content",
    "is_native_engine",
    "normalize_max_file_size",
    "rebuild_hit_from_cache",
    "spec_needs_content",
]

logger = logging.getLogger(__name__)

# 批量写入阈值：累积到该文件数后自动 flush 一次事务。
# 50 个文件 × 平均 2 条规则 = 100 行 scan_results + 50 行 scanned_files + 50 行 file_paths，
# 单次事务约 200 行写入，相比逐条 commit（200 次 fsync）减少 99% 提交开销。
BATCH_THRESHOLD: int = 50

# 默认大文件跳过阈值：引用 config 模块的权威常量，避免多模块重复硬编码。
# 0 表示不限制；可通过 Config.max_file_size 与 Scanner(max_file_size=...) 覆盖。
# DEFAULT_MAX_FILE_SIZE 已从 fuscan.config 重新导出，便于 scanner 子包内统一引用。

# 进度收集列表上限：_skipped_dirs 与 _matched_files 使用 deque(maxlen=) 防止
# 大规模扫描（如全盘跳过 node_modules）时列表无界增长导致内存膨胀。
# _emit_progress 取该上限条 recent 条目，足够 GUI 展示近期跳过/命中情况。
# 50 项已足够用户感知"近期"上下文，更大的值会导致高频进度回调时
# tuple 拷贝与信号槽分发占用主线程时间片引起 UI 卡滞。
PROGRESS_LIST_MAX: int = 50

# 进度双门限节流的增量阈值（时间窗 + 增量门）。
# 即便达到时间窗 elapsed >= _progress_interval，若自上次 emit 以来 scanned/matched
# 的增量都低于以下阈值，跳过本次 emit，避免 50k+ 小文件扫描时 UI 仍被高频刷新。
# - PROGRESS_MIN_DELTA_FILES：scanned 至少增加 50 个
# - PROGRESS_MIN_DELTA_MATCHES：matched 至少增加 10 个（命中率较高时的兜底）
# 原 200/50 对 300-1000 文件的中等清单过大，进度条卡在某值长时间不动；
# 下调到 50/10 后中等清单进度条更新更顺滑，超大清单仍受时间门限保护不至过频
PROGRESS_MIN_DELTA_FILES: int = 50
PROGRESS_MIN_DELTA_MATCHES: int = 10

# 进度快照保留的最近 N 条目（配合 _emit_progress 的 tuple 截断）。
# 避免大规模扫描时 matched_files/skipped_dirs 的 deque 元组转换 O(N) 拷贝开销。
PROGRESS_SNAPSHOT_TAIL: int = 50

# GIL 让步时间阈值（秒）：扫描循环中距上次让步超过此阈值才调一次 ``sleep(0)``，
# 让 UI 线程有机会处理 Qt 事件队列。改为时间判断替代原固定计数（20/200）：
# - 小文件密集场景：单文件耗时 < 1μs，原计数式每 20 个让步一次频率过高
#   （5 万文件 = 2500 次 sleep(0) 调用，纯系统调用开销约 2.5ms）；
#   时间式按实际墙钟判断，5ms 内多次让步被合并为一次
# - 大文件（PDF/DOCX）场景：单文件提取耗时 50ms+，原计数式让步过稀；
#   时间式确保每 5ms 至少让步一次，UI 调度更平稳
# 5ms 是经验值：人眼对 100ms 内的卡顿不敏感，5ms 让步频率远低于感知阈值
# 且对吞吐影响可忽略（sleep(0) 仅放弃剩余时间片，无 I/O 等待）
GIL_YIELD_THRESHOLD_S: float = 0.005

# 预扫描进度 emit 时间阈值（秒）：扫描循环中距上次 emit 超过此阈值时，
# 在开始提取下一个文件内容前先 emit 一次进度（force=True），
# 让用户立即看到"正在扫描 xxx.pdf..."而非上一个文件的陈旧信息。
# 0.5s 平衡实时性与开销：人眼对 500ms 以内的延迟不敏感，超过 500ms 则明显卡顿
PRE_SCAN_EMIT_INTERVAL_S: float = 0.5


def build_hit_from_match(rule: Rule, result: MatchResult) -> RuleHit:
    """从 :class:`MatchResult` 构造 :class:`RuleHit`，字段映射集中在此处。

    扫描器与压缩包扫描器在「匹配器命中后构造 RuleHit」路径共用本函数，
    避免字段遗漏（修复 archive 路径缺失 ``match_texts``/
    ``match_description`` 的 BUG）与字段名漂移。

    :param rule: 命中的规则（提供 ``name``/``severity``）
    :param result: 匹配器求值结果（提供 ``detail``/``match_text`` 等）
    :return: 完整字段的 :class:`RuleHit`
    """
    return RuleHit(
        rule_name=rule.name,
        severity=rule.severity,
        detail=result.detail,
        match_text=result.match_text,
        match_count=result.match_count,
        target=result.target,
        match_texts=result.match_texts,
        match_description=result.match_description,
    )


def rebuild_hit_from_cache(rule: Rule, cached: RuleHit) -> RuleHit:
    """从缓存 :class:`RuleHit` 重建并填回 ``rule_name``。

    缓存中 ``rule_name`` 存为空字符串（避免冗余存储，rule_hash 已唯一标识），
    重建时由当前规则集提供 ``rule_name``/``severity``，其余字段从缓存恢复。

    :param rule: 当前规则集中的规则（提供 ``name``/``severity``）
    :param cached: 缓存中读出的 :class:`RuleHit`
    :return: 填回 ``rule_name``/``severity`` 的 :class:`RuleHit`
    """
    return RuleHit(
        rule_name=rule.name,
        severity=rule.severity,
        detail=cached.detail,
        match_text=cached.match_text,
        match_count=cached.match_count,
        target=cached.target,
        match_texts=cached.match_texts,
        match_description=cached.match_description,
    )


# 无注册提取器时的回退引擎名：extract_content_with_fallback 对未注册扩展名
# 直接 read_text 纯文本读取，故引擎标注为「纯文本」，与提取器的 engine_info 区分。
_FALLBACK_ENGINE: str = "纯文本"


def engine_for_extension(extension: str) -> str:
    """按扩展名反查解析引擎名，供 GUI 明细行标注。

    引擎名由扩展名静态决定（同一扩展名固定映射到同一提取器），故无需在
    提取流程中传递，扫描完成后按 ``entry.extension`` 反查即可：

    - 已注册提取器：返回其 ``engine_info``（如 ``"pdf_oxide"``/``"lxml"``/
      ``"python-calamine"``/``"fuscan-core (cfb)"``）；``engine_info`` 为空串时
      回退到 :data:`_FALLBACK_ENGINE`（提取器未覆盖该属性的兜底）。
    - 无注册提取器：内容提供器走纯文本读取回退，返回 :data:`_FALLBACK_ENGINE`。

    :param extension: 扩展名（不含点，大小写不敏感；空串表示无扩展名）
    :return: 引擎名字符串，始终非空
    """
    extractor = get_extractor(extension)
    if extractor is None:
        return _FALLBACK_ENGINE
    return extractor.engine_info or _FALLBACK_ENGINE


# 会在解析期释放 GIL 的原生引擎（Rust/C/C++ 扩展）引擎名集合。
#
# 这些引擎用原生扩展做重活（PDF 页面布局、Excel 单元格转换、XML libxml2 解析、
# 文本编码检测、OLE 流解析），在原生代码内会主动释放 GIL，故多个 worker 线程
# 可真正并行、且不长时间独占 GIL，GUI 主线程仍能抢到锁保持响应。
# 判据来源（见各 extractor 的 engine_info）：
#
# - ``pdf_oxide``（Rust + PyO3）/ ``pypdfium2``（pdfium C++）→ PDF
# - ``python-calamine``（Rust + PyO3）→ XLSX/XLS
# - ``lxml``（libxml2 C）→ DOCX/PPTX/ODT/ODS
# - ``fuscan-core``（Rust + PyO3，encoding_rs+chardetng）→ 文本编码检测
# - ``fuscan-core (cfb)``（Rust + PyO3，cfb crate）→ DOC/PPT OLE 流解析
#
# 反之，纯 Python 引擎（fuscan-core 缺失时回退的 ``charset-normalizer`` 文本解码、
# ``olefile`` DOC/PPT、``email（标准库）``、``纯文本`` 回退读取）在解析期
# 持 GIL，与同样持 GIL 的 CONTENT 正则 ``re.finditer`` 争抢主线程 GIL，
# 是扫描期 GUI 冻结的主因。
_NATIVE_ENGINES: frozenset[str] = frozenset(
    {
        "pdf_oxide",
        "pypdfium2",
        "python-calamine",
        "lxml",
        "fuscan-core",
        "fuscan-core (cfb)",
    }
)


def is_native_engine(extension: str) -> bool:
    """按扩展名判断其提取器是否使用会释放 GIL 的原生引擎。

    用于扫描并发降档判据：若扫描目标主要落在**非原生引擎**（纯 Python 解析，
    持 GIL）且规则以 CONTENT 正则为主，多 worker 线程会长时间独占 GIL 令
    GUI 主线程冻结，此时应动态降低并发（见 :meth:`Scanner._compute_effective_max_workers`）。
    原生引擎（PDF/Excel/XML）在解析期释放 GIL，可保持高并发。

    :param extension: 扩展名（不含点，大小写不敏感；空串表示无扩展名）
    :return: True 表示该扩展名的提取器使用原生（GIL 释放）引擎
    """
    return engine_for_extension(extension) in _NATIVE_ENGINES


# 压缩/打包产物识别阈值（按内容特征判定，与文件名无关）。
#
# webpack/vite/rollup 等打包器生成的 chunk.js、bundle.js，以及 *.min.js/
# *.min.css/source map 等构建产物，共同特征是把大量代码压进极少数超长行
# （去掉了换行与缩进）。对这类内容跑 CONTENT 正则 finditer 会在单条超长行上
# 产生大量回溯，成为不可中断的 C 调用拖慢扫描——正是「花很多时间去解析」的根源。
#
# 检测不依赖文件名（避免误跳 config.js、漏跳无 .min 后缀的压缩产物），而在
# 内容提取后按两条内容特征判定，命中即跳过该文件的 CONTENT 匹配：
#
# - 存在任意一行字符数 >= _MINIFIED_MAX_LINE_LEN：压缩产物最鲜明的特征，
#   正常手写源码极少出现 5000 字符不换行的单行。
# - 内容总长 >= _MINIFIED_MIN_TOTAL_LEN：小文件即便单行也不构成性能问题，
#   设总长下限避免误判短小的正常单行文件（如压缩前的小型 JSON/一行配置）。
_MINIFIED_MAX_LINE_LEN: int = 5000
_MINIFIED_MIN_TOTAL_LEN: int = 2048


def is_minified_content(content: str) -> bool:
    """按内容特征判定是否为压缩/打包产物（如 min.js/chunk.js/bundle/map）。

    识别与文件名无关，仅看内容形态：内容总长达到下限
    （:data:`_MINIFIED_MIN_TOTAL_LEN`）且存在超长单行
    （长度 >= :data:`_MINIFIED_MAX_LINE_LEN`）时判定为压缩产物。

    实现按换行位置分段扫描各行长度，命中首个超长行即短路返回，
    避免 ``splitlines()`` 对超大内容的整体列表分配；对普通多行源码
    （行普遍较短）则遍历到末尾返回 False，开销为一次线性扫描。

    :param content: 已提取的文本内容
    :return: True 表示压缩/打包产物，扫描器据此跳过其 CONTENT 匹配
    """
    if len(content) < _MINIFIED_MIN_TOTAL_LEN:
        return False
    start = 0
    length = len(content)
    while start < length:
        nl = content.find("\n", start)
        if nl == -1:
            # 末行（无结尾换行）：从 start 到内容末尾
            if length - start >= _MINIFIED_MAX_LINE_LEN:
                return True
            break
        if nl - start >= _MINIFIED_MAX_LINE_LEN:
            return True
        start = nl + 1
    return False


def default_extract_content(entry: FileEntry) -> str:
    """默认内容提供器：通过提取器注册表按扩展名提取文本。

    无注册提取器时回退到纯文本读取；提取失败返回空字符串。
    """
    return extract_content_with_fallback(entry.path)


def empty_content_provider(_fe: FileEntry) -> str:
    """空内容提供器：返回空字符串，跳过所有文件 I/O。

    用于规则集不含 CONTENT 规则或文件超过大小上限的场景，
    使 FILENAME/PATH 规则仍可命中而无需读取文件内容。
    """
    return ""


def default_extract_content_with_hash(entry: FileEntry) -> tuple[str, str]:
    """带哈希的内容提供器：读字节算 BLAKE2b，再从同一份字节提取内容。

    一次 ``read_bytes`` 既算哈希又提取内容，避免提取器内部重复读磁盘。
    缓存模式下，``Scanner`` 用此函数替代 :func:`default_extract_content`，
    使文件哈希计算与内容提取共享一次磁盘 I/O。

    哈希算法由 :func:`fuscan.cache.hashes.hash_bytes` 决定（BLAKE2b，
    ``digest_size=32``，64 字符 hex）。算法变更需递增
    :data:`fuscan.cache.schema.CACHE_COMPAT_VERSION` 触发旧缓存失效。

    超过 :data:`DEFAULT_MAX_FILE_SIZE`（50MB）的文件跳过读取，
    返回空内容与空字节哈希；``Scanner`` 在缓存模式下走自己的
    :meth:`Scanner._extract_with_cache`，使用可配置的 ``max_file_size``。

    使用 :func:`extract_content_from_bytes_with_retry` 替代
    :func:`extract_content_from_bytes`，对瞬时 ``OSError``（Windows AV 文件锁、
    网络盘抖动）重试一次（退避 50ms），避免不必要的纯文本降级。

    :param entry: 文件元信息
    :return: ``(content, file_hash)`` 元组；``file_hash`` 为 64 字符十六进制摘要
    """
    if entry.is_dir or entry.size > DEFAULT_MAX_FILE_SIZE:
        return "", hash_bytes(b"")
    try:
        data = entry.path.read_bytes()
    except OSError:
        logger.debug("读取文件失败: %s", entry.path, exc_info=True)
        return "", hash_bytes(b"")
    file_hash = hash_bytes(data)
    try:
        content = extract_content_from_bytes_with_retry(data, entry.extension)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", entry.path, exc_info=True)
        content = data.decode("utf-8", errors="ignore")
    return content, file_hash


def spec_needs_content(spec: MatchSpec) -> bool:
    """递归检查 MatchSpec 是否包含 CONTENT 目标。

    若所有规则均不需要内容，扫描器可跳过文件 I/O（缓存与无缓存模式均适用）。
    """
    from fuscan.rules.model import AndMatch, LeafMatch, NotMatch, OrMatch

    if isinstance(spec, LeafMatch):
        return spec.target == MatchTarget.CONTENT
    if isinstance(spec, AndMatch):
        return any(spec_needs_content(c) for c in spec.children)
    if isinstance(spec, OrMatch):
        return any(spec_needs_content(c) for c in spec.children)
    if isinstance(spec, NotMatch):
        return spec_needs_content(spec.child)
    return False


def cancel_all_futures(futures: Iterable[Future[Any]]) -> None:
    """对全部 future 调 ``cancel()``。

    已启动的 future 调 ``cancel()`` 返回 False（无法中断），未启动的会成功取消。
    用于扫描取消时跳过 ``as_completed`` 阻塞等待（需求 req-13 R1）。
    """
    for future in futures:
        future.cancel()


def normalize_max_file_size(value: int | None) -> int:
    """规范化大文件跳过阈值：None 或负数退化为默认值，0 表示不限制。

    :param value: 调用方传入的原始值
    :return: 实际生效的阈值；0 表示不限制
    """
    if value is None or value < 0:
        return DEFAULT_MAX_FILE_SIZE
    return value


# ---------------------------------------------------------------------------
# 正则字面量提取与预筛工具
#
# 以下工具函数最初服务于 :mod:`fuscan.scanner._content_buckets` 的 CONTENT 桶
# 预筛；现共享给 :mod:`fuscan.scanner.matchers` 的组合规则复合正则构造。
# ---------------------------------------------------------------------------

_INLINE_FLAG_MAP: dict[str, int] = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}


def _extract_inline_flags(pattern: str) -> tuple[str, int]:
    """提取正则模式开头的内联标志（如 ``(?i)``、``(?im)``）。

    Python 3.11+ 对内联标志不在表达式开头的情况发出 DeprecationWarning，
    因为 ``(?i)`` 等在命名组内部时会影响后续所有内容而非仅当前组。
    本函数将其提取出来，供调用方用 ``(?flag:...)`` 非捕获组语法包装，
    使标志仅作用于目标子模式，避免污染同一 OR 复合正则中的其他分支。

    :param pattern: 原始正则模式
    :return: ``(清理后的模式, 提取的标志位组合)``
    """
    extracted = 0
    pos = 0
    while pos < len(pattern) and pattern[pos] == "(":
        m = re.match(r"\(\?([imsx]+)\)", pattern[pos:])
        if not m:
            break
        for ch in m.group(1):
            extracted |= _INLINE_FLAG_MAP.get(ch, 0)
        pos += m.end()
    return pattern[pos:], extracted


def _flags_to_chars(flags: int) -> str:
    """将标志位组合转换为内联标志字符串（如 ``re.IGNORECASE | re.DOTALL`` → ``is``）。"""
    chars: list[str] = []
    for ch, bit in _INLINE_FLAG_MAP.items():
        if flags & bit:
            chars.append(ch)
    return "".join(chars)


def _walk_sre_ast(nodes: Any, min_len: int, prefix: str = "") -> list[str]:
    """递归遍历 sre_parse AST 节点，提取长度 >= ``min_len`` 的字面量片段。

    用于 CONTENT 桶与组合规则复合正则的预筛：从正则 AST 中提取所有"必然出现在
    匹配文本中"的字面量。若这些字面量均不在内容中，则正则必然不命中，可安全
    跳过 ``finditer``，避免大文本上的不可中断 C 调用阻塞主线程。

    处理的节点类型：

    - ``LITERAL``：累积到当前字面串。
    - ``BRANCH``（``|``）：各分支独立递归，前缀继承（sre_parse 会把公共前缀
      提到 BRANCH 之外，例如 ``(password|passwd|pwd)`` 解析为 ``p`` + BRANCH。
      前缀递归确保正确还原 ``password``/``passwd``/``pwd``）。
    - ``SUBPATTERN``（捕获组）：递归内部，前缀继承。
    - ``MAX_REPEAT``（量词 ``*+?{n,m}``）：内部字面量可能不出现（如 ``a?``），
      前缀不传递，但内部仍递归以提取可保证出现的字面量。
    - ``IN``（字符类）：若全部为单字面量（如 ``[abc]``）则展开为各候选前缀组合；
      含 ``RANGE``/``CATEGORY``（如 ``[A-Z]``）的字符类无法提取确定字面量。

    :param nodes: sre_parse 解析后的节点列表（``list[(op, args), ...]``，运行期为
        ``SubPattern``，duck-type 为可迭代的 ``(op, args)`` 元组序列）
    :param min_len: 字面量最小长度
    :param prefix: 当前累积的字面前缀（用于 BRANCH/SUBPATTERN 共享前缀）
    :return: 字面量片段列表（可能含重复，由调用方去重）
    """
    literals: list[str] = []
    current = prefix
    for op, args in nodes:
        s = str(op)
        if s == "LITERAL":
            current += chr(args)
            continue
        # 非字面量操作：终结当前字面串
        prefix_for_recurse = current
        if current and len(current) >= min_len:
            literals.append(current)
        current = ""
        if s == "BRANCH":
            # | 分支：各分支独立，共享前缀
            for branch in args[1]:
                literals.extend(_walk_sre_ast(branch, min_len, prefix=prefix_for_recurse))
        elif s == "SUBPATTERN":
            # 捕获组：递归内部，前缀继承
            literals.extend(_walk_sre_ast(args[3], min_len, prefix=prefix_for_recurse))
        elif s == "MAX_REPEAT":
            # 量词：内部字面量可能不出现，前缀不传递
            literals.extend(_walk_sre_ast(args[2], min_len, prefix=""))
        elif s == "IN":
            # 字符类：若全为单字面量则展开为候选前缀组合
            sub = list(args)
            if sub and all(str(so) == "LITERAL" for so, _ in sub):
                for _so, sa in sub:
                    candidate = prefix_for_recurse + chr(sa)
                    if len(candidate) >= min_len:
                        literals.append(candidate)
            # 含 RANGE/CATEGORY（如 [A-Z]）的字符类不提取
    if current and len(current) >= min_len:
        literals.append(current)
    return literals


def _extract_literals(pattern: str, min_len: int = 3) -> list[str]:
    """从正则模式中提取字面量片段（长度 >= ``min_len``）。

    解析 sre_parse AST，提取所有"必然出现在匹配文本中"的字面量。
    内联标志（如 ``(?i)``）先剥离——它们不影响字面量提取，仅影响匹配大小写。

    用途：CONTENT 桶与组合规则复合正则的预筛关键字。若所有提取的字面量均不在
    内容中，则正则必然不命中，可安全跳过 ``finditer``。

    :param pattern: 正则模式（可能含内联标志 ``(?i)`` 等）
    :param min_len: 字面量最小长度（默认 3，避免过短关键字如单字母导致高误报率）
    :return: 去重后的字面量列表（保留首次出现顺序）
    """
    cleaned, _ignored = _extract_inline_flags(pattern)
    try:
        ast: Any = _sre_parse.parse(cleaned)
    except Exception:
        # 非法正则或解析失败：保守返回空列表（不预筛，仍走 finditer）
        return []
    seen: set[str] = set()
    result: list[str] = []
    for lit in _walk_sre_ast(ast, min_len):
        if lit not in seen:
            seen.add(lit)
            result.append(lit)
    return result


def _dedup_substrings(keywords: list[str]) -> list[str]:
    """去重并去子串：若 kw1 是 kw2 的子串，仅保留 kw2（kw2 命中时 kw1 必命中）。

    先按插入顺序去重，再按长度降序检查——若某关键字是已保留关键字的子串则丢弃。
    用于桶级与逐规则预筛关键字精简，避免冗余的 ``in`` 检查。

    :param keywords: 原始关键字列表（可能含重复与子串关系）
    :return: 精简后的关键字列表
    """
    unique = list(dict.fromkeys(keywords))
    kept: list[str] = []
    for kw in sorted(unique, key=len, reverse=True):
        if not any(kw in other for other in kept):
            kept.append(kw)
    return kept
