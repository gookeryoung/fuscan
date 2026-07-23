"""配置持久化模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.config import Config, detect_default_staging_dir, load_config, save_config


class TestConfig:
    def test_default_config(self) -> None:
        """默认配置字段值。"""
        config = Config()
        assert config.window_geometry == [300, 300, 720, 960]
        assert config.window_state == "normal"
        assert config.splitter_sizes == []
        assert config.scan_paths == []
        assert config.rules_paths == []
        assert config.use_builtin is True
        assert config.scan_mode == "folder"
        assert config.last_drive is None

    def test_default_ignore_dirs(self) -> None:
        """默认 ignore_dirs 应包含常见开发/构建目录及系统/缓存目录（iter-76 扩充）。"""
        config = Config()
        # 版本控制 / Python / Node
        assert ".git" in config.ignore_dirs
        assert "node_modules" in config.ignore_dirs
        assert "__pycache__" in config.ignore_dirs
        # Rust / Cargo 缓存
        assert ".cargo" in config.ignore_dirs
        assert ".rustup" in config.ignore_dirs
        assert "target" in config.ignore_dirs
        # .NET / Visual Studio
        assert ".vs" in config.ignore_dirs
        assert "packages" in config.ignore_dirs
        assert ".nuget" in config.ignore_dirs
        # 其他语言生态
        assert "vendor" in config.ignore_dirs
        assert "Pods" in config.ignore_dirs
        assert ".m2" in config.ignore_dirs
        # Windows 系统目录（含大量二进制/系统文件，扫描无意义）
        assert "Program Files" in config.ignore_dirs
        assert "Program Files (x86)" in config.ignore_dirs
        assert "Windows" in config.ignore_dirs
        assert "WinSxS" in config.ignore_dirs
        assert "$Recycle.Bin" in config.ignore_dirs
        # 默认列表应足够全面（iter-76 由 21 项扩充至 50+ 项）
        assert len(config.ignore_dirs) >= 50

    def test_default_ignore_extensions(self) -> None:
        """默认 ignore_extensions 应包含常见二进制/临时文件扩展名。"""
        config = Config()
        assert "pyc" in config.ignore_extensions
        assert "exe" in config.ignore_extensions
        assert "zip" in config.ignore_extensions
        assert len(config.ignore_extensions) >= 25

    def test_default_cache_fields(self) -> None:
        """默认启用缓存，路径为 None 表示使用默认路径。"""
        config = Config()
        assert config.cache_enabled is True
        assert config.cache_path is None

    def test_default_staging_dir(self) -> None:
        """默认 staging_dir 为 None，由调用方按需探测（iter-77）。"""
        config = Config()
        assert config.staging_dir is None

    def test_fuscan_cache_in_ignore_dirs(self) -> None:
        """``.fuscan-cache`` 应在默认忽略列表中，避免扫描被移动到暂存区的文件（iter-77）。"""
        config = Config()
        assert ".fuscan-cache" in config.ignore_dirs

    def test_default_perf_log_enabled(self) -> None:
        """默认不启用性能详细日志（iter-69）。"""
        config = Config()
        assert config.perf_log_enabled is False

    def test_default_disabled_extractors(self) -> None:
        """默认不禁用任何提取器（iter-72）。"""
        config = Config()
        assert config.disabled_extractors == []


class TestLoadConfig:
    def test_load_nonexistent_returns_default(self, tmp_path: Path) -> None:
        """文件不存在时返回默认配置。"""
        config = load_config(tmp_path / "missing.yaml")
        assert config.window_geometry == [300, 300, 720, 960]
        assert config.scan_paths == []
        assert config.use_builtin is True

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """加载合法 YAML 配置。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "window_geometry: [100, 200, 800, 600]\n"
            'window_state: "maximized"\n'
            "splitter_sizes: [300, 700]\n"
            "scan_paths:\n"
            "  - /path/a\n"
            "  - /path/b\n"
            "rules_paths:\n"
            "  - /rules/r1.yaml\n"
            "use_builtin: false\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.window_geometry == [100, 200, 800, 600]
        assert config.window_state == "maximized"
        assert config.splitter_sizes == [300, 700]
        assert config.scan_paths == ["/path/a", "/path/b"]
        assert config.rules_paths == ["/rules/r1.yaml"]
        assert config.use_builtin is False

    def test_load_invalid_yaml_returns_default(self, tmp_path: Path) -> None:
        """非法 YAML 返回默认配置。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(":::not valid yaml:::\n  - broken", encoding="utf-8")
        config = load_config(config_file)
        assert config.use_builtin is True
        assert config.scan_paths == []

    def test_load_non_dict_returns_default(self, tmp_path: Path) -> None:
        """顶层非字典时返回默认配置。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
        config = load_config(config_file)
        assert config.use_builtin is True

    def test_load_ignores_unknown_keys(self, tmp_path: Path) -> None:
        """未知字段被忽略，不报错。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "use_builtin: false\nunknown_field: hello\nanother: 123\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.use_builtin is False

    def test_load_ignores_none_values(self, tmp_path: Path) -> None:
        """None 值字段使用默认值。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "window_geometry: null\nuse_builtin: false\nscan_paths: null\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        # None 值被过滤，使用默认值
        assert config.window_geometry == [300, 300, 720, 960]
        assert config.use_builtin is False
        assert config.scan_paths == []

    def test_load_partial_config(self, tmp_path: Path) -> None:
        """部分字段缺失时其余字段正常加载。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("use_builtin: false\n", encoding="utf-8")
        config = load_config(config_file)
        assert config.use_builtin is False
        assert config.scan_paths == []
        assert config.rules_paths == []

    def test_load_cache_fields(self, tmp_path: Path) -> None:
        """从 YAML 加载缓存字段。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "cache_enabled: false\ncache_path: /tmp/custom_cache.db\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.cache_enabled is False
        assert config.cache_path == "/tmp/custom_cache.db"


class TestSaveConfig:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """保存后重新加载应得到相同配置。"""
        config_file = tmp_path / "config.yaml"
        original = Config(
            window_geometry=[10, 20, 300, 400],
            window_state="normal",
            splitter_sizes=[200, 800],
            scan_paths=["/a", "/b", "/c"],
            rules_paths=["/rules/r1.yaml", "/rules/r2.yaml"],
            use_builtin=False,
            ignore_dirs=["custom_dir", ".git"],
            ignore_extensions=["pyc", "custom_ext"],
        )
        save_config(original, config_file)
        assert config_file.exists()

        loaded = load_config(config_file)
        assert loaded.window_geometry == [10, 20, 300, 400]
        assert loaded.window_state == "normal"
        assert loaded.splitter_sizes == [200, 800]
        assert loaded.scan_paths == ["/a", "/b", "/c"]
        assert loaded.rules_paths == ["/rules/r1.yaml", "/rules/r2.yaml"]
        assert loaded.use_builtin is False
        assert loaded.ignore_dirs == ["custom_dir", ".git"]
        assert loaded.ignore_extensions == ["pyc", "custom_ext"]

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        """保存时自动创建父目录。"""
        config_file = tmp_path / "subdir" / "nested" / "config.yaml"
        save_config(Config(), config_file)
        assert config_file.exists()

    def test_save_default_config(self, tmp_path: Path) -> None:
        """保存默认配置不报错。"""
        config_file = tmp_path / "config.yaml"
        save_config(Config(), config_file)
        loaded = load_config(config_file)
        assert loaded.use_builtin is True
        assert loaded.scan_paths == []

    def test_save_unicode_paths(self, tmp_path: Path) -> None:
        """保存含中文路径的配置。"""
        config_file = tmp_path / "config.yaml"
        original = Config(scan_paths=["/用户/文档/扫描目录"])
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.scan_paths == ["/用户/文档/扫描目录"]

    def test_save_and_load_cache_fields(self, tmp_path: Path) -> None:
        """缓存字段保存后重新加载应一致。"""
        config_file = tmp_path / "config.yaml"
        original = Config(
            cache_enabled=False,
            cache_path="/tmp/test_cache.db",
        )
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.cache_enabled is False
        assert loaded.cache_path == "/tmp/test_cache.db"

    def test_save_and_load_staging_dir(self, tmp_path: Path) -> None:
        """暂存区目录持久化（iter-77）。"""
        config_file = tmp_path / "config.yaml"
        original = Config(staging_dir="D:/custom-staging")
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.staging_dir == "D:/custom-staging"

    def test_save_and_load_perf_log_enabled(self, tmp_path: Path) -> None:
        """性能日志开关持久化（iter-69）。"""
        config_file = tmp_path / "config.yaml"
        original = Config(perf_log_enabled=True)
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.perf_log_enabled is True

    def test_save_and_load_disabled_extractors(self, tmp_path: Path) -> None:
        """已禁用提取器列表持久化（iter-72）。"""
        config_file = tmp_path / "config.yaml"
        original = Config(disabled_extractors=["PdfExtractor", "DocxExtractor"])
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.disabled_extractors == ["PdfExtractor", "DocxExtractor"]

    def test_load_config_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """文件打开失败时返回默认配置。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("use_builtin: false\n", encoding="utf-8")

        original_open = Path.open

        def mock_open(self: Path, *args: object, **kwargs: object) -> object:
            if self == config_file:
                raise OSError("模拟权限错误")
            return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", mock_open)
        config = load_config(config_file)
        assert config.use_builtin is True

    def test_save_config_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """保存失败时记录日志不抛异常。"""
        config_file = tmp_path / "config.yaml"

        original_open = Path.open

        def mock_open(self: Path, *args: object, **kwargs: object) -> object:
            if self == config_file:
                raise OSError("模拟写入错误")
            return original_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", mock_open)
        save_config(Config(), config_file)  # 不应抛异常


class TestDetectDefaultStagingDir:
    """默认暂存区目录探测（iter-77）。"""

    def test_returns_path_under_drive_with_most_free_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """应返回剩余空间最大盘符下的 ``.fuscan-cache``。"""
        fake_drives = [Path("C:\\"), Path("D:\\")]

        def fake_list_drives(include_network: bool = False) -> list[Path]:
            return list(fake_drives)

        def fake_disk_usage(path: Path) -> object:
            class _Usage:
                def __init__(self, free: int) -> None:
                    self.free = free

            if str(path) == "C:\\":
                return _Usage(free=10 * 1024 * 1024)
            return _Usage(free=500 * 1024 * 1024)

        # 延迟导入路径与 config.detect_default_staging_dir 内一致
        import fuscan.config as config_mod

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)
        monkeypatch.setattr(config_mod.shutil, "disk_usage", fake_disk_usage)

        result = detect_default_staging_dir()
        assert result == Path("D:\\") / ".fuscan-cache"

    def test_fallback_to_home_when_no_drives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无可用盘符时回退到 ``~/.fuscan-cache``。"""

        def fake_list_drives(include_network: bool = False) -> list[Path]:
            return []

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)

        result = detect_default_staging_dir()
        assert result == Path.home() / ".fuscan-cache"

    def test_fallback_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """盘符枚举抛 OSError 时回退到 ``~/.fuscan-cache``。"""

        def fake_list_drives(include_network: bool = False) -> list[Path]:
            raise OSError("模拟枚举失败")

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)

        result = detect_default_staging_dir()
        assert result == Path.home() / ".fuscan-cache"

    def test_skips_drives_with_disk_usage_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """某盘符 disk_usage 抛 OSError 时跳过该盘符，选择剩余可用的。"""

        def fake_list_drives(include_network: bool = False) -> list[Path]:
            return [Path("C:\\"), Path("D:\\")]

        def fake_disk_usage(path: Path) -> object:
            class _Usage:
                def __init__(self, free: int) -> None:
                    self.free = free

            if str(path) == "C:\\":
                raise OSError("C: 不可访问")
            return _Usage(free=500 * 1024 * 1024)

        import fuscan.config as config_mod

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)
        monkeypatch.setattr(config_mod.shutil, "disk_usage", fake_disk_usage)

        result = detect_default_staging_dir()
        assert result == Path("D:\\") / ".fuscan-cache"
