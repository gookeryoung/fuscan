"""配置持久化。

在用户主目录 ``~/.fuscan/config.yaml`` 存储窗口状态、历史扫描路径、
规则文件列表、通用规则开关等配置，应用启动时自动恢复，关闭时自动保存。

公共 API：

- :func:`load_config`：从 YAML 加载配置
- :func:`save_config`：保存配置到 YAML
- :data:`CONFIG_PATH`：默认配置文件路径
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Sequence

import yaml

from fuscan.rules import RuleSet, load_ruleset, merge_multiple_rulesets

__all__ = [
    "BUILTIN_RULES_PATH",
    "CONFIG_DIR",
    "CONFIG_PATH",
    "MANUAL_PDF_PATH",
    "Config",
    "default_backup_dir",
    "detect_default_staging_dir",
    "load_builtin_ruleset",
    "load_config",
    "load_with_builtin",
    "save_config",
]

logger = logging.getLogger(__name__)

# 文件目录配置
CONFIG_DIR = Path.home() / ".fuscan"
CONFIG_PATH = CONFIG_DIR / "config.yaml"
ASSETS_DIR = Path(__file__).parent / "assets"
BUILTIN_RULES_PATH = ASSETS_DIR / "rules" / "builtin.yaml"
MANUAL_PDF_PATH = ASSETS_DIR / "docs" / "fuscan-用户手册.pdf"

# 历史记录最大保留条数
MAX_HISTORY = 15


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
    max_file_size: int = 50 * 1024 * 1024
    # 是否启用扫描结果缓存（基于内容哈希跳过未变化文件，提升二次扫描速度）
    cache_enabled: bool = True
    # 是否启用性能详细日志（PerfTimer 持久化到缓存数据库）
    perf_log_enabled: bool = False
    # 已禁用的提取器类名列表：默认空列表表示全部启用，
    # 用户在主界面勾选区取消的提取器类名追加到此列表，对应文件类型不扫描。
    disabled_extractors: list[str] = field(default_factory=list)
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
    # 含版本控制元数据、语言工具链缓存、构建输出、IDE 配置、临时/日志目录，
    # 以及 Windows 系统目录（含大量二进制/系统文件，扫描无意义且拖慢速度）。
    # 用户可在设置对话框「忽略项」Tab 中增删。
    ignore_dirs: list[str] = field(
        default_factory=lambda: [
            # 版本控制
            ".git",
            ".svn",
            ".hg",
            # Python
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "env",
            ".tox",
            ".eggs",
            # Node / JavaScript
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
            # Rust / Cargo
            "target",
            ".cargo",
            ".rustup",
            # Java
            ".gradle",
            ".m2",
            ".ivy",
            # .NET / Visual Studio
            ".vs",
            "packages",
            ".nuget",
            # PHP
            "vendor",
            # Apple
            "Pods",
            "DerivedData",
            # Flutter / Dart
            ".dart_tool",
            # 构建输出
            "dist",
            "build",
            "out",
            # IDE
            ".idea",
            ".vscode",
            # 缓存 / 临时 / 日志
            ".cache",
            "tmp",
            "temp",
            "logs",
            "log",
            ".Trash",
            "Trash",
            # Windows 系统目录（含大量二进制/系统文件，扫描无意义）
            "Program Files",
            "Program Files (x86)",
            "Windows",
            "WinSxS",
            "ProgramData",
            "System Volume Information",
            "$Recycle.Bin",
            # fuscan 暂存区目录：避免扫描被移动到暂存区的文件
            ".fuscan-cache",
        ]
    )
    # ----------------------------- 通用设置（字体） -----------------------------
    # 字体族：None 表示使用平台默认字体（detect_font_families() 探测）
    font_family: str | None = None
    # 字体大小（基准字号，ThemeController 基于 base 计算其他字号）：默认 14
    font_size: int = 14
    # 最小字体大小（caption 等小字号的下限，避免在高 DPI 屏幕上过小）：默认 12
    min_font_size: int = 12
    # 是否加粗
    font_bold: bool = False


def detect_default_staging_dir() -> Path:
    """探测默认暂存区目录：剩余空间最大的盘符下 ``.fuscan-cache``。

    遍历本机所有本地盘符（不含网络映射盘），选择 ``shutil.disk_usage().free``
    最大的盘符，返回 ``<drive>/.fuscan-cache``。盘符枚举失败或无可用盘符时
    回退到用户主目录下的 ``~/.fuscan-cache``。

    :return: 默认暂存区目录路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    # 延迟导入避免顶层依赖：walker 依赖 scanner.context，与 config 无循环依赖，
    # 但保留惰性导入使 config 模块在无 scanner 包时仍可独立用于配置读写测试。
    from fuscan.scanner.walker import list_drives

    fallback = Path.home() / ".fuscan-cache"
    try:
        drives = list_drives(include_network=False)
    except OSError:
        logger.warning("盘符枚举失败，暂存区回退到主目录", exc_info=True)
        return fallback
    if not drives:
        return fallback

    best_drive = drives[0]
    best_free = -1
    for drive in drives:
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            continue
        if free > best_free:
            best_free = free
            best_drive = drive
    return best_drive / ".fuscan-cache"


def default_backup_dir() -> Path:
    """返回默认备份区目录：``~/.fuscan/backup``。

    与暂存区不同，备份区存放的是「替换内容」前的源文件副本（``.bak`` 后缀），
    体量较小且用户事后可手动清理，故无需探测剩余空间最大的盘符，
    直接放在用户主目录下的 ``.fuscan`` 配置目录中，便于统一管理。

    :return: 默认备份区目录路径（路径可能尚不存在，调用方按需 ``mkdir``）
    """
    return CONFIG_DIR / "backup"


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


def load_builtin_ruleset() -> RuleSet:
    """加载内置通用规则集。

    :return: 内置 RuleSet 实例
    :raises RuleError: 内置规则文件加载或解析失败
    """
    return load_ruleset(BUILTIN_RULES_PATH)


def load_with_builtin(user_paths: Sequence[Path] | None = None) -> RuleSet:
    """加载内置规则并与一个或多个用户规则按顺序合并。

    内置规则作为基础，用户规则按列表顺序依次合并覆盖（后面的覆盖前面的同名规则）。
    ignore_paths 取并集。
    若 ``user_paths`` 为 None 或空，仅返回内置规则集。

    :param user_paths: 用户规则文件路径列表（按优先级从低到高排列）
    :return: 合并后的 RuleSet
    :raises RuleError: 规则文件加载或解析失败
    """
    builtin = load_builtin_ruleset()
    if not user_paths:
        logger.debug("仅加载内置规则集")
        return builtin

    user_rulesets = [load_ruleset(p) for p in user_paths]
    logger.debug("合并规则: 内置 %d 条 + 用户 %d 个文件", len(builtin.rules), len(user_rulesets))
    return merge_multiple_rulesets(builtin, *user_rulesets)
