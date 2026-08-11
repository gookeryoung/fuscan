"""提取器抽象基类与注册表。

设计要点：

- :class:`Extractor` 抽象基类定义 ``extract(path)`` 与 ``extract_from_bytes(data)``
  两套接口：前者从磁盘路径提取，后者从内存字节提取（避免双重 I/O）
- :class:`ExtractorRegistry` 按扩展名分发，支持注册与查找
- 依赖第三方库的提取器在 ``extract`` 方法内部懒加载 import，避免模块导入时强依赖
- :func:`get_extractor` 提供默认注册表查询，未注册返回 ``None``（由调用方回退到纯文本）
- :class:`SpeedTier` 枚举划分 5 档解析速度，GUI 勾选树展示档次
  便于用户按需选择文件类型
- :class:`ExtractorRegistry` 新增 ``extract_from_bytes_with_retry`` /
  ``extract_with_retry`` 方法，对瞬时 ``OSError``（Windows AV 文件锁、网络盘抖动）
  执行一次退避重试；:class:`ExtractorFailure` 聚合诊断信息供调用方统计
"""

from __future__ import annotations

import enum
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Extractor",
    "ExtractorError",
    "ExtractorFailure",
    "ExtractorRegistry",
    "SpeedTier",
    "default_registry",
    "extract_content",
    "extract_content_from_bytes",
    "extract_content_from_bytes_with_retry",
    "extract_content_with_fallback",
    "extract_content_with_fallback_and_retry",
    "get_extractor",
    "is_retriable_error",
]

logger = logging.getLogger(__name__)


class SpeedTier(enum.Enum):
    """提取器解析速度档次（5 档）。

    档次依据实现复杂度划分，与典型文件大小（1MB）下的解析耗时对应：

    - ``VERY_FAST`` (T1 极速)：< 10ms/MB，纯字节解码，无第三方库
    - ``FAST`` (T2 快速)：10-50ms/MB，标准库解析
    - ``MEDIUM`` (T3 中速)：50-200ms/MB，单次 XML 解析 + 树遍历
    - ``SLOW`` (T4 慢速)：200-1000ms/MB，单元格遍历或字节级扫描
    - ``VERY_SLOW`` (T5 极慢)：> 1000ms/MB，复杂页面布局分析或解压+条目提取

    档次用于 GUI 勾选树展示，帮助用户预估勾选某类文件类型后的扫描耗时。
    实际耗时受文件大小、内容复杂度、磁盘缓存等影响，档次仅为数量级参考。
    """

    VERY_FAST = 1
    FAST = 2
    MEDIUM = 3
    SLOW = 4
    VERY_SLOW = 5

    @property
    def label(self) -> str:
        """返回档次短标签，如 ``T1 极速``（用于树形展示）。"""
        mapping = {
            SpeedTier.VERY_FAST: "T1 极速",
            SpeedTier.FAST: "T2 快速",
            SpeedTier.MEDIUM: "T3 中速",
            SpeedTier.SLOW: "T4 慢速",
            SpeedTier.VERY_SLOW: "T5 极慢",
        }
        return mapping[self]

    @property
    def description(self) -> str:
        """返回档次说明（用于 tooltip）。"""
        mapping = {
            SpeedTier.VERY_FAST: "纯字节解码，无第三方库（< 10ms/MB）",
            SpeedTier.FAST: "标准库解析（10-50ms/MB）",
            SpeedTier.MEDIUM: "单次 XML 解析 + 树遍历（50-200ms/MB）",
            SpeedTier.SLOW: "单元格遍历或字节级扫描（200-1000ms/MB）",
            SpeedTier.VERY_SLOW: "复杂布局分析或解压+条目提取（> 1000ms/MB）",
        }
        return mapping[self]

    @property
    def color(self) -> str:
        """返回档次对应的十六进制色值（从绿到红，用于 GUI 勾选树着色）。

        色值与 ``scan_stats_label`` 内联 HTML 风格一致，属于 rule-12 例外
        （程序化着色无法引用 QSS 令牌，在 docstring 注明）：

        - T1 极速：``#28A745`` 绿色
        - T2 快速：``#17A2B8`` 青色
        - T3 中速：``#FFC107`` 琥珀
        - T4 慢速：``#FD7E14`` 橙色
        - T5 极慢：``#DC3545`` 红色
        """
        mapping = {
            SpeedTier.VERY_FAST: "#28A745",
            SpeedTier.FAST: "#17A2B8",
            SpeedTier.MEDIUM: "#FFC107",
            SpeedTier.SLOW: "#FD7E14",
            SpeedTier.VERY_SLOW: "#DC3545",
        }
        return mapping[self]


class ExtractorError(Exception):
    """提取器相关错误。

    子类抛出此异常表示「文件损坏/加密/格式不支持」等不可恢复错误，
    :func:`is_retriable_error` 视为不可重试，调用方应直接降级到纯文本。
    """


@dataclass(frozen=True)
class ExtractorFailure:
    """提取器失败诊断信息。

    由 :meth:`ExtractorRegistry.extract_from_bytes_with_retry` /
    :meth:`ExtractorRegistry.extract_with_retry` 在每次失败（含重试）时
    通过 ``on_failure`` 回调上报，调用方（Scanner）可聚合统计「N 个文件
    提取失败，其中 M 个瞬时错误、K 个格式错误」并展示给用户。

    :ivar extractor_name: 提取器类名（如 ``"PdfExtractor"``）
    :ivar extension: 文件扩展名（不含点，小写）
    :ivar error_type: 异常类型名（如 ``"OSError"`` / ``"ExtractorError"``）
    :ivar error_message: 异常消息前 200 字符（避免大 traceback 撑爆统计）
    :ivar retried: 是否触发了重试（仅 ``OSError`` 等可重试异常为 True）
    :ivar succeeded_after_retry: 重试后是否成功（仅在 ``retried=True`` 时有意义）
    """

    extractor_name: str
    extension: str
    error_type: str
    error_message: str
    retried: bool
    succeeded_after_retry: bool


def is_retriable_error(exc: Exception) -> bool:
    """判断异常是否值得重试。

    仅 ``OSError`` 视为可重试瞬时错误：Windows 文件锁（AV 扫描、共享冲突）、
    网络盘抖动、磁盘瞬时 I/O 错误等。重试一次通常能成功。

    :class:`ExtractorError`（文件损坏/加密）与其他异常（``ValueError``/
    ``KeyError`` 等数据问题）视为不可重试，重试只会浪费 CPU 时间。

    :param exc: 提取器抛出的异常
    :return: True 表示可重试，False 表示应直接降级
    """
    return isinstance(exc, OSError)


class Extractor(ABC):
    """文件内容提取器抽象基类。

    子类须实现 :meth:`extract_from_bytes`（从内存字节提取），用于缓存模式：
    调用方一次 ``read_bytes`` 既算哈希又提取内容，避免双重磁盘 I/O。

    :meth:`extract` 提供默认实现：``read_bytes`` → ``OSError`` 包装为
    :class:`ExtractorError` → 调用 :meth:`extract_from_bytes`。子类仅当需要
    自定义读取逻辑（如大文件流式读取、文件大小预检）时才覆盖 ``extract``，
    否则直接复用基类实现以消除样板代码。

    子类还须声明 :attr:`speed_tier` 标识解析速度档次，
    供 GUI 勾选树展示。档次依据实现复杂度划分，详见 :class:`SpeedTier`。
    """

    @property
    @abstractmethod
    def supported_extensions(self) -> tuple[str, ...]:
        """该提取器支持的文件扩展名列表（不含点，小写）。"""

    @property
    @abstractmethod
    def speed_tier(self) -> SpeedTier:
        """该提取器的解析速度档次。

        子类须按实现复杂度返回对应 :class:`SpeedTier`：
        纯文本解码 → ``VERY_FAST``，标准库解析 → ``FAST``，
        XML 解析 → ``MEDIUM``，单元格遍历/字节扫描 → ``SLOW``，
        页面布局分析/解压+条目提取 → ``VERY_SLOW``。
        """

    @property
    def display_name(self) -> str:
        """提取器的中文显示名称，供 GUI 勾选区展示。默认返回类名，子类可覆盖。"""
        return type(self).__name__

    @property
    def engine_info(self) -> str:
        """提取器使用的解析引擎名称（供 GUI tooltip 展示）。

        如 ``"pypdfium2"`` / ``"lxml"`` / ``"python-calamine"`` /
        ``"fuscan-core (cfb)"`` / ``"fuscan-core"`` 等。默认返回空字符串，
        子类应覆盖。用于 SettingsPage 解析速度 tooltip 中展示具体引擎，
        便于用户排查依赖缺失或性能问题。
        """
        return ""

    def extract(self, path: Path) -> str:
        """提取文件文本内容（默认实现：读字节 + 调用 ``extract_from_bytes``）。

        默认实现用 :meth:`pathlib.Path.read_bytes` 读取文件全部字节，
        将 ``OSError`` 包装为 :class:`ExtractorError`（保留原始异常链），
        再委托 :meth:`extract_from_bytes`。子类仅当需要自定义读取逻辑
        （如 :class:`fuscan.extractors.text.TextExtractor` 的大小预检
        与大文件流式读取）时才覆盖此方法。

        :param path: 文件路径
        :return: 提取的文本内容
        :raises ExtractorError: 文件读取失败或提取失败（依赖缺失、文件损坏、加密等）
        """
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ExtractorError(f"文件读取失败: {path}: {exc}") from exc
        return self.extract_from_bytes(data)

    @abstractmethod
    def extract_from_bytes(self, data: bytes) -> str:
        """从内存字节提取文本内容，避免重复读磁盘。

        :param data: 文件完整字节内容
        :return: 提取的文本内容
        :raises ExtractorError: 提取失败
        """


class ExtractorRegistry:
    """提取器注册表：按扩展名分发到对应提取器实例。"""

    def __init__(self) -> None:
        self._extractors: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> None:
        """注册提取器，按其 supported_extensions 建立映射。"""
        for ext in extractor.supported_extensions:
            normalized = ext.lower().lstrip(".")
            if normalized in self._extractors:
                logger.debug(
                    "扩展名 %s 提取器被覆盖: %s -> %s",
                    normalized,
                    type(self._extractors[normalized]).__name__,
                    type(extractor).__name__,
                )
            self._extractors[normalized] = extractor

    def get(self, extension: str) -> Extractor | None:
        """按扩展名查找提取器，未注册返回 None。"""
        normalized = extension.lower().lstrip(".")
        return self._extractors.get(normalized)

    @property
    def registered_extensions(self) -> tuple[str, ...]:
        """已注册的所有扩展名。"""
        return tuple(sorted(self._extractors.keys()))

    def list_extractors(self) -> list[tuple[str, str, tuple[str, ...], SpeedTier, str]]:
        """列出所有已注册的提取器信息，供 GUI 勾选区展示。

        :return: ``[(class_name, display_name, supported_extensions, speed_tier, engine_info), ...]``
                 列表，按 display_name 排序。同一提取器实例支持多个扩展名时合并为一项。
                 ``speed_tier`` 为 :class:`SpeedTier` 枚举值。
                 ``engine_info`` 为解析引擎名称字符串（供 GUI tooltip 展示）。
        """
        seen: dict[int, tuple[str, str, tuple[str, ...], SpeedTier, str]] = {}
        for _ext, extractor in self._extractors.items():
            obj_id = id(extractor)
            if obj_id not in seen:
                exts = extractor.supported_extensions
                seen[obj_id] = (
                    type(extractor).__name__,
                    extractor.display_name,
                    tuple(sorted(e.lower().lstrip(".") for e in exts)),
                    extractor.speed_tier,
                    extractor.engine_info,
                )
        return sorted(seen.values(), key=lambda x: x[1])

    def extract(self, path: Path, extension: str | None = None) -> str:
        """按扩展名提取文件内容。

        :param path: 文件路径
        :param extension: 显式指定扩展名（默认从路径推断）
        :return: 提取的文本；无提取器时返回空字符串
        :raises ExtractorError: 提取失败
        """
        ext = extension if extension is not None else path.suffix.lower().lstrip(".")
        extractor = self.get(ext)
        if extractor is None:
            logger.debug("扩展名 %s 无注册提取器，返回空内容", ext)
            return ""
        return extractor.extract(path)

    def extract_from_bytes(self, data: bytes, extension: str) -> str:
        """按扩展名从内存字节提取文件内容。

        :param data: 文件完整字节内容
        :param extension: 扩展名（不含点，小写）
        :return: 提取的文本；无提取器时返回空字符串
        :raises ExtractorError: 提取失败
        """
        normalized = extension.lower().lstrip(".")
        extractor = self.get(normalized)
        if extractor is None:
            logger.debug("扩展名 %s 无注册提取器，返回空内容", normalized)
            return ""
        return extractor.extract_from_bytes(data)

    def extract_from_bytes_with_retry(
        self,
        data: bytes,
        extension: str,
        *,
        max_retries: int = 1,
        backoff_ms: float = 50.0,
        on_failure: Callable[[ExtractorFailure], None] | None = None,
    ) -> str:
        """按扩展名从内存字节提取，对瞬时 ``OSError`` 执行退避重试。

        与 :meth:`extract_from_bytes` 的区别：

        - ``OSError`` 视为瞬时错误（Windows AV 文件锁、网络盘抖动），重试 ``max_retries`` 次，
          每次重试前 ``time.sleep(backoff_ms / 1000)`` 秒
        - :class:`ExtractorError`（文件损坏/加密）与其他异常不重试，直接抛出
        - 每次失败（含重试）通过 ``on_failure`` 回调上报 :class:`ExtractorFailure`，
          供调用方聚合统计

        无注册提取器时返回空字符串（与 :meth:`extract_from_bytes` 一致，不触发回调）。

        :param data: 文件完整字节内容
        :param extension: 扩展名（不含点，小写）
        :param max_retries: 最大重试次数（默认 1）；0 表示不重试，退化为 :meth:`extract_from_bytes`
        :param backoff_ms: 重试前退避等待时长（毫秒，默认 50ms）
        :param on_failure: 失败回调，每次失败（含重试）调用一次；None 表示不回调
        :return: 提取的文本；无提取器时返回空字符串
        :raises ExtractorError: 提取失败（不可重试或重试后仍失败）
        :raises OSError: 重试后仍失败的瞬时 I/O 错误（由调用方降级处理）
        """
        normalized = extension.lower().lstrip(".")
        extractor = self.get(normalized)
        if extractor is None:
            logger.debug("扩展名 %s 无注册提取器，返回空内容", normalized)
            return ""
        return self._retry_loop(
            lambda: extractor.extract_from_bytes(data),
            extractor_name=type(extractor).__name__,
            extension=normalized,
            context_label=normalized,
            max_retries=max_retries,
            backoff_ms=backoff_ms,
            on_failure=on_failure,
        )

    def extract_with_retry(
        self,
        path: Path,
        extension: str | None = None,
        *,
        max_retries: int = 1,
        backoff_ms: float = 50.0,
        on_failure: Callable[[ExtractorFailure], None] | None = None,
    ) -> str:
        """按扩展名从磁盘路径提取，对瞬时 ``OSError`` 执行退避重试。

        与 :meth:`extract` 的区别：同 :meth:`extract_from_bytes_with_retry`，
        对 ``OSError`` 重试，其他异常直接抛出；通过 ``on_failure`` 回调上报诊断。

        :param path: 文件路径
        :param extension: 显式指定扩展名（默认从路径推断）
        :param max_retries: 最大重试次数（默认 1）
        :param backoff_ms: 重试前退避等待时长（毫秒，默认 50ms）
        :param on_failure: 失败回调，每次失败（含重试）调用一次；None 表示不回调
        :return: 提取的文本；无提取器时返回空字符串
        :raises ExtractorError: 提取失败（不可重试或重试后仍失败）
        :raises OSError: 重试后仍失败的瞬时 I/O 错误
        """
        ext = extension if extension is not None else path.suffix.lower().lstrip(".")
        extractor = self.get(ext)
        if extractor is None:
            logger.debug("扩展名 %s 无注册提取器，返回空内容", ext)
            return ""
        return self._retry_loop(
            lambda: extractor.extract(path),
            extractor_name=type(extractor).__name__,
            extension=ext,
            context_label=str(path),
            max_retries=max_retries,
            backoff_ms=backoff_ms,
            on_failure=on_failure,
        )

    def _retry_loop(
        self,
        action: Callable[[], str],
        *,
        extractor_name: str,
        extension: str,
        context_label: str,
        max_retries: int,
        backoff_ms: float,
        on_failure: Callable[[ExtractorFailure], None] | None,
    ) -> str:
        """重试循环骨架（内部复用）。

        :param action: 实际执行提取的可调用对象
        :param extractor_name: 提取器类名（诊断用）
        :param extension: 文件扩展名（诊断用）
        :param context_label: 日志中展示的上下文（扩展名或路径）
        :param max_retries: 最大重试次数
        :param backoff_ms: 重试前退避时长（毫秒）
        :param on_failure: 失败回调
        :return: 提取的文本
        :raises Exception: 不可重试或重试后仍失败的原始异常
        """
        attempt = 0
        while True:
            try:
                return action()
            except Exception as exc:
                retriable = is_retriable_error(exc)
                # 不可重试或已达重试上限：上报后抛出
                if not retriable or attempt >= max_retries:
                    if on_failure is not None:
                        on_failure(
                            ExtractorFailure(
                                extractor_name=extractor_name,
                                extension=extension,
                                error_type=type(exc).__name__,
                                error_message=str(exc)[:200],
                                retried=attempt > 0,
                                succeeded_after_retry=False,
                            )
                        )
                    raise
                # 可重试且未达上限：上报「准备重试」后 sleep 并重试
                if on_failure is not None:
                    on_failure(
                        ExtractorFailure(
                            extractor_name=extractor_name,
                            extension=extension,
                            error_type=type(exc).__name__,
                            error_message=str(exc)[:200],
                            retried=False,
                            succeeded_after_retry=False,
                        )
                    )
                logger.debug(
                    "提取器 %s 提取 %s 失败（%s），%dms 后重试（第 %d/%d 次）",
                    extractor_name,
                    context_label,
                    type(exc).__name__,
                    backoff_ms,
                    attempt + 1,
                    max_retries,
                    exc_info=True,
                )
                time.sleep(backoff_ms / 1000.0)
                attempt += 1


default_registry = ExtractorRegistry()


def get_extractor(extension: str) -> Extractor | None:
    """从默认注册表查找提取器。"""
    return default_registry.get(extension)


def extract_content(path: Path, extension: str | None = None) -> str:
    """使用默认注册表从磁盘路径提取文件内容。"""
    return default_registry.extract(path, extension=extension)


def extract_content_from_bytes(data: bytes, extension: str) -> str:
    """使用默认注册表从内存字节提取文件内容。

    用于缓存模式：调用方一次 ``read_bytes`` 后既算哈希又提取内容，
    避免提取器内部重复读磁盘。

    :param data: 文件完整字节内容
    :param extension: 扩展名（不含点，小写）
    :return: 提取的文本；无提取器时返回空字符串
    """
    return default_registry.extract_from_bytes(data, extension)


def extract_content_with_fallback(path: Path) -> str:
    """提取文件内容，提取器失败时回退到纯文本读取。

    优先通过 :func:`extract_content` 提取（支持 PDF/DOCX 等格式），
    提取器抛出任何异常时回退到 UTF-8 纯文本读取（``errors="ignore"``）。
    纯文本读取失败时抛出 :class:`OSError`，由调用方处理。

    :param path: 文件路径
    :return: 提取的文本内容；提取器失败时返回纯文本内容
    :raises OSError: 纯文本回退读取失败
    """
    try:
        return extract_content(path)
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", path, exc_info=True)
        return path.read_text(encoding="utf-8", errors="ignore")


def extract_content_from_bytes_with_retry(
    data: bytes,
    extension: str,
    *,
    max_retries: int = 1,
    backoff_ms: float = 50.0,
    on_failure: Callable[[ExtractorFailure], None] | None = None,
) -> str:
    """使用默认注册表从内存字节提取，对瞬时 ``OSError`` 执行退避重试。

    与 :func:`extract_content_from_bytes` 的区别：见
    :meth:`ExtractorRegistry.extract_from_bytes_with_retry`。

    用于扫描热路径（``default_extract_content_with_hash`` / ``extract_with_cache``）：
    Windows AV 文件锁或网络盘抖动导致提取器内部 I/O 失败时，重试一次通常能成功，
    避免不必要的纯文本降级（PDF/DOCX 降级到纯文本会读到乱码）。

    :param data: 文件完整字节内容
    :param extension: 扩展名（不含点，小写）
    :param max_retries: 最大重试次数（默认 1）
    :param backoff_ms: 重试前退避等待时长（毫秒，默认 50ms）
    :param on_failure: 失败回调，每次失败（含重试）调用一次；None 表示不回调
    :return: 提取的文本；无提取器时返回空字符串
    :raises ExtractorError: 提取失败（不可重试或重试后仍失败）
    :raises OSError: 重试后仍失败的瞬时 I/O 错误
    """
    return default_registry.extract_from_bytes_with_retry(
        data,
        extension,
        max_retries=max_retries,
        backoff_ms=backoff_ms,
        on_failure=on_failure,
    )


def extract_content_with_fallback_and_retry(
    path: Path,
    *,
    max_retries: int = 1,
    backoff_ms: float = 50.0,
    on_failure: Callable[[ExtractorFailure], None] | None = None,
) -> str:
    """带重试的提取+纯文本回退。

    与 :func:`extract_content_with_fallback` 的区别：先通过
    :meth:`ExtractorRegistry.extract_with_retry` 带重试地提取，重试后仍失败
    才回退到 UTF-8 纯文本读取。失败时通过 ``on_failure`` 回调上报诊断信息。

    :param path: 文件路径
    :param max_retries: 最大重试次数（默认 1）
    :param backoff_ms: 重试前退避等待时长（毫秒，默认 50ms）
    :param on_failure: 失败回调，每次失败（含重试）调用一次；None 表示不回调
    :return: 提取的文本内容；提取器失败时返回纯文本内容
    :raises OSError: 纯文本回退读取失败
    """
    try:
        return default_registry.extract_with_retry(
            path,
            max_retries=max_retries,
            backoff_ms=backoff_ms,
            on_failure=on_failure,
        )
    except Exception:
        logger.debug("提取器提取失败，回退到纯文本: %s", path, exc_info=True)
        return path.read_text(encoding="utf-8", errors="ignore")
