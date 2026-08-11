"""规则数据模型。

定义匹配条件、规则、规则集合的不可变数据结构。
所有模型为 frozen dataclass，可哈希、可作为字典键、线程安全。

匹配条件层次：

- 叶子匹配 (LeafMatch)：基于文件名/内容/路径的单字段匹配
- 逻辑组合：
  - AndMatch：全部子条件命中
  - OrMatch：任一子条件命中
  - NotMatch：子条件不命中

YAML 中通过 ``type`` 字段区分：filename/content/path 为叶子，and/or/not 为组合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.rules.whitelist import WhitelistEntry

__all__ = [
    "AndMatch",
    "LeafMatch",
    "MatchMode",
    "MatchSpec",
    "MatchTarget",
    "NotMatch",
    "OrMatch",
    "Rule",
    "RuleSet",
    "ScanParams",
    "Severity",
]


class MatchTarget(str, Enum):
    """叶子匹配的目标字段。"""

    FILENAME = "filename"
    CONTENT = "content"
    PATH = "path"


class MatchMode(str, Enum):
    """叶子匹配的模式。"""

    CONTAINS = "contains"
    EQUALS = "equals"
    STARTSWITH = "startswith"
    ENDSWITH = "endswith"
    REGEX = "regex"


class Severity(str, Enum):
    """规则严重等级。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class LeafMatch:
    """叶子匹配条件：对文件名/内容/路径应用单字段匹配。

    ``description`` 为该匹配项的可选描述，便于用户理解匹配规则含义，
    在 GUI 详情表与导出结果中展示。空字符串表示未提供描述。
    """

    target: MatchTarget
    mode: MatchMode
    pattern: str
    case_sensitive: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.pattern:
            raise ValueError("匹配模式 pattern 不能为空")


@dataclass(frozen=True)
class AndMatch:
    """逻辑与：所有子条件均命中才算命中。

    ``description`` 为该组合匹配项的可选描述，便于用户理解组合规则含义。
    """

    children: tuple[MatchSpec, ...] = field(default_factory=tuple)
    description: str = ""


@dataclass(frozen=True)
class OrMatch:
    """逻辑或：任一子条件命中即算命中。

    ``description`` 为该组合匹配项的可选描述，便于用户理解组合规则含义。
    """

    children: tuple[MatchSpec, ...] = field(default_factory=tuple)
    description: str = ""


@dataclass(frozen=True)
class NotMatch:
    """逻辑非：子条件不命中才算命中。

    ``description`` 为该组合匹配项的可选描述，便于用户理解组合规则含义。
    """

    child: MatchSpec
    description: str = ""


MatchSpec = LeafMatch | AndMatch | OrMatch | NotMatch


@dataclass(frozen=True)
class Rule:
    """单条扫描规则。

    ``replace`` 为 True 时表示命中后允许用户在详情区点击「替换内容」按钮
    将该规则命中的文本替换为 ``replace_with``。``replace_with`` 为空字符串
    表示未定义替换内容，触发替换时向用户提示「规则 X 未定义替换内容」。

    替换流程在 :mod:`fuscan.processing.replacer` 中实现：先备份源文件到备份区（重命名
    为 ``.bak``），再对原文件按规则逐条执行 ``match_texts → replace_with``
    的文本替换。仅支持纯文本文件，二进制格式（PDF/DOCX 等）在替换入口拒绝。
    """

    name: str
    match: MatchSpec
    description: str = ""
    severity: Severity = Severity.INFO
    # 是否启用命中内容替换（用户在详情区点击「替换内容」按钮时生效）
    replace: bool = False
    # 替换为的内容；空字符串表示未定义，触发替换时提示用户补充
    replace_with: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("规则 name 不能为空")


@dataclass(frozen=True)
class ScanParams:
    """扫描参数：线程数/深度/大文件阈值/压缩包/缓存/性能日志开关。

    所有字段为 ``None`` 表示「未设置」——合并时由后者非 ``None`` 字段覆盖前者，
    最终生效值在 :class:`ScanController` 中按优先级（RuleSet.scan_params >
    内置规则默认值）解析。扫描参数**不再**支持任务级覆盖，统一由全局规则集决定。
    ``None`` 与默认值的区别见各字段说明。

    - ``max_workers``：``None`` 时由 :class:`Scanner` 回退到内置默认 5
    - ``max_depth``：``None`` 表示无限深度（与 ``0`` 等价，walker 层归一化）
    - ``max_file_size``：``None`` 或 ``0`` 表示不限制
    - ``scan_archives``/``cache_enabled``/``perf_log_enabled``：``None`` 表示
      未设置，回退到内置默认（True/True/False）
    """

    max_workers: int | None = None
    max_depth: int | None = None
    max_file_size: int | None = None
    scan_archives: bool | None = None
    cache_enabled: bool | None = None
    perf_log_enabled: bool | None = None


@dataclass(frozen=True)
class RuleSet:
    """规则集合：版本、忽略路径、规则列表，以及顶层扫描配置。

    顶层扫描配置（取代旧 :class:`~fuscan.config.Config` 中的对应字段）：

    - ``ignore_paths``：路径级 glob 过滤（如 ``*/vendor/*``），跳过目录及其子目录
    - ``ignore_dirs``：目录名级忽略（任意层级、大小写不敏感），与
      :class:`~fuscan.config.Config.ignore_dirs` 同语义，合并时取并集
    - ``scan_extensions``：文件后缀白名单。

      - ``None``：全选默认（所有注册提取器支持的后缀）
      - 空 tuple：都不扫描
      - 非空 tuple：仅扫描指定后缀（小写、无前导点）

      合并时后者非 ``None`` 覆盖前者（``None`` 表未设置，由内置规则集提供默认）。
    - ``scan_params``：扫描参数（线程/深度/大文件阈值/压缩包/缓存/性能日志），
      合并时字段级覆盖（后者非 ``None`` 字段覆盖前者）
    - ``whitelist``：误报白名单条目（路径 glob + 规则名 + 备注），合并时取并集
    """

    version: str
    rules: tuple[Rule, ...] = field(default_factory=tuple)
    ignore_paths: tuple[str, ...] = field(default_factory=tuple)
    # 顶层扫描配置：默认空，由 builtin.yaml + 用户规则文件合并填充
    ignore_dirs: tuple[str, ...] = field(default_factory=tuple)
    scan_extensions: tuple[str, ...] | None = None
    scan_params: ScanParams | None = None
    whitelist: tuple[WhitelistEntry, ...] = field(default_factory=tuple)
