"""配置持久化。

在用户主目录 ``~/.fuscan/config.yaml`` 存储窗口状态、历史扫描路径、
规则文件列表、通用规则开关等配置，应用启动时自动恢复，关闭时自动保存。

扫描参数（线程数/大文件阈值/忽略目录/文件类型白名单等）已迁移到 RuleSet
顶层，由 ``~/.fuscan/rules/user-scan.yaml`` 与内置规则合并得到 effective 规则集。
:func:`migrate_config_to_rules` 在应用启动时自动将旧版 Config 中的扫描字段
搬到 ``user-scan.yaml``，迁移后这些字段从 ``config.yaml`` 中清除。

资产路径常量（``ASSETS_DIR`` / ``MANUAL_PDF_PATH`` / ``BUILTIN_RULES_PATH``）
见 :mod:`fuscan.paths`；内置规则加载便利函数见 :mod:`fuscan.rules.builtin`；
暂存/备份目录探测见 :mod:`fuscan.processing.storage`。

公共 API：

- :func:`load_config`：从 YAML 加载配置
- :func:`save_config`：保存配置到 YAML
- :func:`migrate_config_to_rules`：将旧 Config 扫描字段迁移到 user-scan.yaml
- :data:`CONFIG_PATH`：默认配置文件路径
- :data:`DEFAULT_MAX_FILE_SIZE`：大文件跳过默认阈值（唯一权威来源）
- :data:`IGNORE_DIR_CATEGORIES`：忽略目录分类预设（UI 层元数据）
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "CONFIG_DIR",
    "CONFIG_PATH",
    "DEFAULT_DISABLED_EXTRACTORS",
    "DEFAULT_MAX_FILE_SIZE",
    "IGNORE_DIR_CATEGORIES",
    "Config",
    "load_config",
    "migrate_config_to_rules",
    "save_config",
]

logger = logging.getLogger(__name__)

# 文件目录配置
CONFIG_DIR = Path.home() / ".fuscan"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

# 历史记录最大保留条数
MAX_HISTORY = 15

# 大文件跳过默认阈值（字节）：超过此值的文件不读取内容、不计哈希，
# 避免大文件独占 GIL 数秒冻结界面。0 表示不限制。
# 历史上该值曾在 scanner/context/archive/config_controller 多处重复硬编码，
# 此处为唯一权威来源，其他模块应引用本常量。
# 可通过 Config.max_file_size 与 Scanner(max_file_size=...) 覆盖。
DEFAULT_MAX_FILE_SIZE: int = 50 * 1024 * 1024

# 忽略目录预设分类：有序映射（分类名 → 该分类下的目录名元组）。
# 用于设置页「忽略目录」Tab 的分类展示与管理。
# Config.ignore_dirs 的默认值从本常量扁平化派生，保持 Scanner/FileWalker
# 的扁平 list[str] 接口不变，分类信息仅作为 UI 层元数据。
# 匹配规则：按目录名匹配（大小写不敏感，任意层级），见 FileWalker._ignore_dirs。
IGNORE_DIR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("版本控制", (".git", ".svn", ".hg")),
    (
        "Python",
        (
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "env",
            ".tox",
            ".eggs",
        ),
    ),
    (
        "Node / JavaScript",
        (
            "node_modules",
            ".sass-cache",
            ".npm",
            ".yarn",
            ".pnpm-store",
            ".next",
            ".nuxt",
            ".turbo",
            ".parcel-cache",
            ".svelte-kit",
        ),
    ),
    ("Rust / Cargo", ("target", ".cargo", ".rustup")),
    ("Java", (".gradle", ".m2", ".ivy")),
    (".NET / Visual Studio", (".vs", "packages", ".nuget")),
    ("PHP", ("vendor",)),
    ("Apple", ("Pods", "DerivedData")),
    ("Flutter / Dart", (".dart_tool",)),
    ("构建输出", ("dist", "build", "out")),
    ("IDE", (".idea", ".vscode")),
    (
        "缓存 / 临时 / 日志",
        (
            ".cache",
            "tmp",
            "temp",
            "logs",
            "log",
            ".Trash",
            "Trash",
        ),
    ),
    (
        "大型软件",
        (
            # ANSYS
            "ANSYS Inc",
            # AutoCAD
            "Autodesk",
            # SolidWorks
            "SOLIDWORKS Corp",
            "SolidWorks",
            # Microsoft Office
            "Microsoft Office",
            "Office16",
            "Office15",
            "Office14",
            # WPS Office
            "Kingsoft",
            "WPS Office",
            # MATLAB
            "MATLAB",
            "MathWorks",
            # Adobe
            "Adobe",
            # Corel
            "Corel",
            # 其他工程软件
            "TecPlot",
            "STK",
            "Altium",
            # JetBrains 系列 IDE（统一安装目录 + 各产品名覆盖自定义路径）
            "JetBrains",
            "IntelliJ IDEA",
            "PyCharm",
            "WebStorm",
            "CLion",
            "GoLand",
            "Rider",
            "PhpStorm",
            "DataGrip",
            "AppCode",
            "Android Studio",
            # CAD/工程软件
            "FreeCAD",
            "PTC",
            "Creo",
            "Siemens",
            "Dassault Systemes",
            "Bentley",
            "Inventor",
            "Revit",
            "SketchUp",
            "McNeel",
            "Rhino",
            # 设计/创意软件
            "Affinity",
            "Serif",
            "Blackmagic Design",
            # 科学/分析软件
            "National Instruments",
            "LabVIEW",
            "Maplesoft",
            "Wolfram Research",
            # 开发工具/数据库
            "Eclipse",
            "Microsoft Visual Studio",
            "Microsoft SQL Server",
        ),
    ),
    (
        "Windows 系统目录",
        (
            "Program Files",
            "Program Files (x86)",
            "Windows",
            "WinSxS",
            "ProgramData",
            "System Volume Information",
            "$Recycle.Bin",
        ),
    ),
    ("fuscan", (".fuscan-cache",)),
)


# 默认禁用的提取器类名列表：
# - 文本：仅勾选纯文本（PlainTextExtractor），源代码/HTML/XML 等（SourceCodeExtractor）默认关闭
# - 压缩包：仅勾选 ZIP、RAR，7z（SevenZArchiveExtractor）默认关闭
# - 邮件：仅勾选 EML（EmlExtractor）
# - Office 文档：全部默认勾选
# 保留作为迁移逻辑与历史 UI 的引用元数据（旧 Config.disabled_extractors 默认值）。
DEFAULT_DISABLED_EXTRACTORS: tuple[str, ...] = (
    "SourceCodeExtractor",
    "SevenZArchiveExtractor",
)


@dataclass
class Config:
    """应用配置。

    扫描参数（scan_archives/max_workers/max_depth/max_file_size/cache_enabled/
    perf_log_enabled/ignore_dirs/disabled_extractors）已迁移到 RuleSet 顶层，
    由 ``~/.fuscan/rules/user-scan.yaml`` 与内置规则合并得到 effective 规则集。
    本类仅保留扫描模式、路径历史、规则文件路径、字体等"应用级"配置。
    迁移由 :func:`migrate_config_to_rules` 在应用启动时自动执行。
    """

    # 扫描模式："drive"（盘符）、"folder"（文件夹）
    scan_mode: str = "folder"
    # 历史扫描路径（最近优先）
    scan_paths: list[str] = field(default_factory=list)
    # 上次选择的盘符（如 "C:\\"）
    last_drive: str | None = None
    # 规则文件路径列表（按优先级从低到高）
    rules_paths: list[str] = field(default_factory=list)
    # 是否使用通用规则
    use_builtin: bool = True
    # 被禁用的全局规则文件路径（rules_paths 中的文件可单独禁用，
    # 禁用后不参与规则集合并，但仍保留在 rules_paths 列表中以便重新启用）
    disabled_rules_paths: list[str] = field(default_factory=list)
    # 是否包含网络映射盘（默认不包含）
    include_network_drives: bool = False
    # 缓存数据库路径（None 表示默认 ~/.fuscan/cache.db）
    cache_path: str | None = None
    # 暂存区目录：用户点击「移动至暂存区」后文件被移动到此目录。
    # None 表示自动探测剩余空间最大的盘符下 ``.fuscan-cache``（见 detect_default_staging_dir）。
    staging_dir: str | None = None
    # 备份区目录：用户点击「替换内容」时源文件先复制到此目录（重命名为 .bak）。
    # None 表示使用 ``~/.fuscan/backup``（见 default_backup_dir）。
    backup_dir: str | None = None
    # 备份时是否保留源文件相对扫描根目录的目录结构（避免不同子目录同名文件冲突）。
    # False 时仅保留文件名，冲突时追加序号（如 b.1.txt.bak）。
    backup_preserve_relative_path: bool = True
    # ----------------------------- 通用设置（字体） -----------------------------
    # 字体族：None 表示使用平台默认字体（detect_font_families() 探测）
    font_family: str | None = None
    # 字体大小（基准字号，ThemeController 基于 base 计算其他字号）：默认 14
    font_size: int = 14
    # 最小字体大小（caption 等小字号的下限，避免在高 DPI 屏幕上过小）：默认 12
    min_font_size: int = 12
    # 是否加粗
    font_bold: bool = False


def load_config(path: Path | None = None) -> Config:
    """从 YAML 文件加载配置。

    文件不存在或解析失败时返回默认配置，不抛异常。

    :param path: 配置文件路径，默认为 :data:`CONFIG_PATH`
    :return: :class:`Config` 实例
    """
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        return Config()
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("配置加载失败，使用默认配置: %s", exc)
        return Config()

    if not isinstance(data, dict):
        logger.warning("配置文件格式异常，使用默认配置")
        return Config()

    known = {f.name for f in fields(Config)}
    filtered: dict[str, Any] = {k: v for k, v in data.items() if k in known and v is not None}
    return Config(**filtered)


def save_config(config: Config, path: Path | None = None) -> None:
    """保存配置到 YAML 文件。

    :param config: :class:`Config` 实例
    :param path: 配置文件路径，默认为 :data:`CONFIG_PATH`
    """
    config_path = path or CONFIG_PATH
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(config)
        with config_path.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except OSError as exc:
        logger.warning("配置保存失败: %s", exc)


# 已迁移到 RuleSet 顶层的 Config 字段名集合。
# :func:`migrate_config_to_rules` 检测 config.yaml 中是否存在这些字段，
# 存在则迁移到 user-scan.yaml 并从 config.yaml 中清除。
_MIGRATED_FIELDS: tuple[str, ...] = (
    "scan_archives",
    "max_workers",
    "max_depth",
    "max_file_size",
    "cache_enabled",
    "perf_log_enabled",
    "ignore_dirs",
    "disabled_extractors",
)


def migrate_config_to_rules() -> None:
    """将旧 Config 中已迁移字段搬到 ``~/.fuscan/rules/user-scan.yaml``。

    旧版 Config 包含 ``scan_archives``/``max_workers``/``max_depth``/
    ``max_file_size``/``cache_enabled``/``perf_log_enabled``/``ignore_dirs``/
    ``disabled_extractors`` 等扫描参数字段，重构后这些字段统一迁移到
    RuleSet 顶层（``scan_params``/``ignore_dirs``/``scan_extensions``）。

    本函数在应用启动时（ConfigController 构造前）调用，幂等：

    1. 检测 ``config.yaml`` 中是否存在已迁移字段；不存在则 no-op 返回。
    2. 读取 ``config.yaml`` 原始 dict，提取迁移字段值。
    3. 构造 ``user-scan.yaml`` 内容（``version``/``ignore_dirs``/
       ``scan_extensions``/``scan_params``/``whitelist``）。
    4. 写入 ``~/.fuscan/rules/user-scan.yaml``；若文件已存在则跳过写入
       （保留用户手工编辑的版本），仅清理 config.yaml 字段。
    5. 将 ``user-scan.yaml`` 路径追加到 ``config.rules_paths``（若未存在）。
    6. 从 ``config.yaml`` 删除已迁移字段并保存（保留其他字段）。
    7. 记录 info 日志。

    ``scan_extensions`` 反推逻辑：``disabled_extractors`` 中的类名对应
    提取器扩展名从全部注册提取器扩展名集合中排除；若 ``disabled_extractors``
    为空或不存在，则 ``scan_extensions`` 设为 ``None``（全选默认）。
    提取器扩展名映射从 :mod:`fuscan.extractors.registry` 的
    :data:`default_registry` 获取。
    """
    if not CONFIG_PATH.exists():
        return

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("迁移：config.yaml 读取失败，跳过: %s", exc)
        return

    if not isinstance(data, dict):
        return

    # 检测是否存在已迁移字段（任一存在即触发迁移）
    present = [k for k in _MIGRATED_FIELDS if k in data]
    if not present:
        return

    # 提取迁移字段值并构造 scan_params / scan_extensions
    ignore_dirs = data.get("ignore_dirs")
    disabled_extractors = data.get("disabled_extractors")
    scan_extensions = _compute_migrated_scan_extensions(disabled_extractors)
    scan_params = _extract_migrated_scan_params(data)

    user_scan_path = CONFIG_DIR / "rules" / "user-scan.yaml"

    # 若 user-scan.yaml 已存在则不覆盖（保留用户手工编辑版本），仅清理 config.yaml
    if not user_scan_path.exists() and not _write_user_scan_yaml(
        user_scan_path, ignore_dirs, scan_extensions, scan_params
    ):
        return

    # 将 user-scan.yaml 追加到 rules_paths（若未存在）
    rules_paths_raw = data.get("rules_paths")
    rules_paths: list[object] = rules_paths_raw if isinstance(rules_paths_raw, list) else []
    user_scan_str = str(user_scan_path)
    if user_scan_str not in rules_paths:
        rules_paths.append(user_scan_str)
        data["rules_paths"] = rules_paths

    # 从 config.yaml 删除已迁移字段
    for key in _MIGRATED_FIELDS:
        data.pop(key, None)

    # 保存清理后的 config.yaml
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except OSError as exc:
        logger.warning("迁移：config.yaml 保存失败: %s", exc)
        return

    logger.info("已迁移 %d 个扫描字段到 %s", len(present), user_scan_path)


def _extract_migrated_scan_params(data: dict[str, object]) -> dict[str, object]:
    """从旧 config dict 提取扫描参数字段，构造 ``scan_params`` dict。

    仅保留类型合法的字段（int 排除 bool、bool 直接采纳），过滤 None 值。

    :param data: ``config.yaml`` 解析得到的原始 dict
    :return: 可写入 ``user-scan.yaml`` 的 ``scan_params`` 字段 dict（可能为空）
    """
    scan_params: dict[str, object] = {}
    int_fields = ("max_workers", "max_depth", "max_file_size")
    for key in int_fields:
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            scan_params[key] = value
    bool_fields = ("scan_archives", "cache_enabled", "perf_log_enabled")
    for key in bool_fields:
        value = data.get(key)
        if isinstance(value, bool):
            scan_params[key] = value
    return scan_params


def _write_user_scan_yaml(
    path: Path,
    ignore_dirs: object,
    scan_extensions: tuple[str, ...] | None,
    scan_params: dict[str, object],
) -> bool:
    """写入 ``user-scan.yaml`` 初始内容。

    :param path: ``user-scan.yaml`` 路径
    :param ignore_dirs: 旧 ``ignore_dirs`` 字段值（list 时写入）
    :param scan_extensions: 反推得到的扩展名 tuple（None 表示全选默认）
    :param scan_params: 已过滤的扫描参数 dict
    :return: 写入成功返回 True，失败返回 False
    """
    user_scan_data: dict[str, object] = {"version": "1.0"}
    if isinstance(ignore_dirs, list):
        user_scan_data["ignore_dirs"] = [str(d) for d in ignore_dirs]
    if scan_extensions is not None:
        user_scan_data["scan_extensions"] = list(scan_extensions)
    if scan_params:
        user_scan_data["scan_params"] = scan_params
    whitelist_init: list[str] = []
    user_scan_data["whitelist"] = whitelist_init
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                user_scan_data,
                fh,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
    except OSError as exc:
        logger.warning("迁移：user-scan.yaml 写入失败: %s", exc)
        return False
    return True


def _compute_migrated_scan_extensions(disabled_extractors: object) -> tuple[str, ...] | None:
    """从 disabled_extractors 反推 scan_extensions 白名单。

    :param disabled_extractors: 旧 Config.disabled_extractors 列表（类名）
    :return: ``None`` 表示全选默认（disabled_extractors 为空/None 或注册表不可用）；
        非 None tuple 表示排除禁用提取器后的扩展名集合（小写、无前导点）。
    """
    if not disabled_extractors or not isinstance(disabled_extractors, list):
        return None

    try:
        from fuscan.extractors.registry import default_registry
    except ImportError:  # pragma: no cover - 注册表为内置模块，导入失败属异常路径
        logger.warning("迁移：extractors.registry 不可用，scan_extensions 设为 None")
        return None

    disabled_set = {str(name) for name in disabled_extractors}
    extensions: list[str] = []
    seen: set[str] = set()
    # list_extractors 返回 (class_name, display_name, supported_extensions, speed_tier, engine_info)
    for class_name, _display, supported_exts, _tier, _engine in default_registry.list_extractors():
        if class_name in disabled_set:
            continue
        for ext in supported_exts:
            normalized = ext.lower().lstrip(".")
            if normalized and normalized not in seen:
                seen.add(normalized)
                extensions.append(normalized)
    return tuple(extensions) if extensions else None
