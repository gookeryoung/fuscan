"""配置持久化。

在用户主目录 ``~/.fuscan/config.yaml`` 存储窗口状态、历史扫描路径、
规则文件列表、通用规则开关等配置，应用启动时自动恢复，关闭时自动保存。

资产路径常量（``ASSETS_DIR`` / ``MANUAL_PDF_PATH`` / ``BUILTIN_RULES_PATH``）
见 :mod:`fuscan.paths`；内置规则加载便利函数见 :mod:`fuscan.rules.builtin`；
暂存/备份目录探测见 :mod:`fuscan.processing.storage`。

公共 API：

- :func:`load_config`：从 YAML 加载配置
- :func:`save_config`：保存配置到 YAML
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


def _default_ignore_dirs() -> list[str]:
    """从 IGNORE_DIR_CATEGORIES 扁平化派生默认忽略目录列表。"""
    return [d for _, dirs in IGNORE_DIR_CATEGORIES for d in dirs]


# 默认禁用的提取器类名列表：
# - 文本：仅勾选纯文本（PlainTextExtractor），源代码/HTML/XML 等（SourceCodeExtractor）默认关闭
# - 压缩包：仅勾选 ZIP、RAR，7z（SevenZArchiveExtractor）默认关闭
# - 邮件：仅勾选 EML（EmlExtractor），MSG（MsgExtractor）默认关闭
# - Office 文档：全部默认勾选
# - PDF/RTF：全部默认勾选
DEFAULT_DISABLED_EXTRACTORS: tuple[str, ...] = (
    "SourceCodeExtractor",
    "SevenZArchiveExtractor",
    "MsgExtractor",
)


def _default_disabled_extractors() -> list[str]:
    """返回默认禁用提取器的可变列表副本。"""
    return list(DEFAULT_DISABLED_EXTRACTORS)


@dataclass
class Config:
    """应用配置。"""

    # 窗口几何：[x, y, width, height]
    window_geometry: list[int] | None = field(default_factory=lambda: [300, 300, 720, 960])
    # 窗口状态："maximized" 或 "normal"
    window_state: str | None = field(default_factory=lambda: "normal")
    # 盘符图标大小（像素）
    drive_icon_size: int = 32
    # 主分割器大小：[left_width, right_width]
    splitter_sizes: list[int] | None = field(default_factory=list)
    # 扫描模式："full"（全盘）、"drive"（盘符）、"folder"（文件夹）
    scan_mode: str = "folder"
    # 历史扫描路径（最近优先）
    scan_paths: list[str] = field(default_factory=list)
    # 上次选择的盘符（如 "C:\\"）
    last_drive: str | None = None
    # 规则文件路径列表（按优先级从低到高）
    rules_paths: list[str] = field(default_factory=list)
    # 是否使用通用规则
    use_builtin: bool = True
    # 是否包含网络映射盘（默认不包含）
    include_network_drives: bool = False
    # 是否扫描压缩包
    scan_archives: bool = True
    # 最大工作线程数：PyO3 提取器（pdf_oxide/calamine）在 Rust 侧解析时释放 GIL，
    # 主线程（Qt 事件循环）有足够时间片处理事件，可提升至 5 线程改善 I/O 并行度。
    # 纯 Python 提取器（如 ods）仍受 GIL 限制，但已通过 max_file_size 跳过大文件缓解。
    max_workers: int = 5
    # 最大扫描深度（None 表示无限制）
    max_depth: int | None = None
    # 跳过大于此大小的文件（字节），避免单个大文件独占 GIL 数秒冻结界面；0 表示不限制
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    # 是否启用扫描结果缓存（基于内容哈希跳过未变化文件，提升二次扫描速度）
    cache_enabled: bool = True
    # 是否启用性能详细日志（PerfTimer 持久化到缓存数据库）
    perf_log_enabled: bool = False
    # 已禁用的提取器类名列表：默认按用户预设（仅启用 TXT/ZIP/RAR/EML/Office/PDF），
    # 用户在主界面勾选区取消的提取器类名追加到此列表，对应文件类型不扫描。
    disabled_extractors: list[str] = field(default_factory=_default_disabled_extractors)
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
    # 忽略目录名（按目录名匹配任意层级，大小写不敏感）。
    # 默认值从 IGNORE_DIR_CATEGORIES 扁平化派生，含版本控制、语言工具链缓存、
    # 构建输出、IDE 配置、临时/日志目录、大型软件安装目录（ANSYS/AutoCAD/
    # SolidWorks/Office/WPS/MATLAB/Adobe 等）以及 Windows 系统目录。
    # 用户可在设置对话框「忽略目录」Tab 中按分类增删。
    ignore_dirs: list[str] = field(default_factory=_default_ignore_dirs)
    # ----------------------------- 通用设置（字体） -----------------------------
    # 字体族：None 表示使用平台默认字体（detect_font_families() 探测）
    font_family: str | None = None
    # 字体大小（基准字号，ThemeController 基于 base 计算其他字号）：默认 14
    font_size: int = 14
    # 最小字体大小（caption 等小字号的下限，避免在高 DPI 屏幕上过小）：默认 12
    min_font_size: int = 12
    # 是否加粗
    font_bold: bool = False
    # ----------------------------- 凭证检测（iter-134） -----------------------------
    # 是否启用高熵字符串检测（识别疑似密钥/令牌的随机串，作为正则规则的兜底）
    entropy_enabled: bool = True
    # 高熵检测的 Shannon 熵阈值（比特/字符）：默认 4.5 捕获 Base64（~6.0）与
    # 混合大小写 Hex（~4.46），过滤自然语言（<4.0）。范围 3.0~5.0，值越低越敏感
    # （误报增多），值越高越严格（漏报增多）。可在设置页实时调节。
    entropy_threshold: float = 4.5


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
