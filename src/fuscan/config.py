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
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

__all__ = ["CONFIG_DIR", "CONFIG_PATH", "Config", "load_config", "save_config"]

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".fuscan"
CONFIG_PATH = CONFIG_DIR / "config.yaml"

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
    # 最大工作线程数
    max_workers: int = 8
    # 最大扫描深度（None 表示无限制）
    max_depth: int | None = None
    # 跳过大于此大小的文件（字节），避免大文件读取导致卡死；0 表示不限制
    max_file_size: int = 100 * 1024 * 1024
    # 是否启用扫描结果缓存（基于内容哈希跳过未变化文件，提升二次扫描速度）
    cache_enabled: bool = True
    # 是否启用性能详细日志（PerfTimer，iter-69 起持久化）
    perf_log_enabled: bool = False
    # 全局扫描后缀过滤（iter-71）：None 或空列表表示扫描所有文件，
    # 非空列表表示只扫描指定后缀的文件。替代原规则级 file_extensions。
    scan_extensions: list[str] | None = None
    # 缓存数据库路径（None 表示默认 ~/.fuscan/cache.db）
    cache_path: str | None = None
    # 忽略目录名（按目录名匹配任意层级，大小写不敏感）
    ignore_dirs: list[str] = field(
        default_factory=lambda: [
            ".git",
            ".svn",
            ".hg",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            "target",
            "out",
            ".idea",
            ".vscode",
            ".cache",
            ".gradle",
            ".tox",
            ".eggs",
            ".sass-cache",
        ]
    )
    # 忽略扩展名（不含点，大小写不敏感）
    ignore_extensions: list[str] = field(
        default_factory=lambda: [
            "pyc",
            "pyo",
            "pyd",
            "so",
            "dll",
            "exe",
            "bin",
            "obj",
            "o",
            "a",
            "lib",
            "class",
            "jar",
            "war",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "bmp",
            "ico",
            "svg",
            "mp3",
            "mp4",
            "avi",
            "mov",
            "zip",
            "rar",
            "7z",
            "tar",
            "gz",
            "bz2",
        ]
    )


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
