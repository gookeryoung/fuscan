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
from typing import Union

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


MatchSpec = Union[LeafMatch, AndMatch, OrMatch, NotMatch]


@dataclass(frozen=True)
class Rule:
    """单条扫描规则。"""

    name: str
    match: MatchSpec
    description: str = ""
    severity: Severity = Severity.INFO

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("规则 name 不能为空")


@dataclass(frozen=True)
class RuleSet:
    """规则集合：版本、忽略路径、规则列表。

    ``ignore_paths`` 按相对路径 glob 通配符匹配（如 ``*/vendor/*``），
    可跳过目录及其子目录。``ignore_dirs`` 和 ``ignore_extensions`` 已迁移至
    全局 :class:`~fuscan.config.Config`，规则文件中这两个字段被静默忽略。
    """

    version: str
    rules: tuple[Rule, ...] = field(default_factory=tuple)
    ignore_paths: tuple[str, ...] = field(default_factory=tuple)
