"""配置持久化模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fuscan.config import (
    DEFAULT_DISABLED_EXTRACTORS,
    Config,
    load_config,
    migrate_config_to_rules,
    save_config,
)
from fuscan.processing.storage import detect_default_staging_dir


class TestConfig:
    def test_default_config(self) -> None:
        """默认配置字段值。"""
        config = Config()
        assert config.scan_paths == []
        assert config.rules_paths == []
        assert config.use_builtin is True
        assert config.scan_mode == "folder"
        assert config.last_drive is None

    def test_default_staging_dir(self) -> None:
        """默认 staging_dir 为 None，由调用方按需探测（iter-77）。"""
        config = Config()
        assert config.staging_dir is None

    def test_default_disabled_rules_paths_empty(self) -> None:
        """iter-138：默认 disabled_rules_paths 为空列表。"""
        config = Config()
        assert config.disabled_rules_paths == []

    def test_disabled_rules_paths_roundtrip(self, tmp_path: Path) -> None:
        """iter-138：disabled_rules_paths 可持久化与加载。"""
        config_file = tmp_path / "config.yaml"
        original = Config(
            rules_paths=["/rules/r1.yaml", "/rules/r2.yaml"],
            disabled_rules_paths=["/rules/r1.yaml"],
            use_builtin=True,
        )
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.disabled_rules_paths == ["/rules/r1.yaml"]
        assert loaded.rules_paths == ["/rules/r1.yaml", "/rules/r2.yaml"]

    def test_migrated_fields_not_in_config(self) -> None:
        """迁移字段不应存在于 Config 数据类。"""
        config = Config()
        # 这些字段已迁移到 RuleSet 顶层
        assert not hasattr(config, "ignore_dirs")
        assert not hasattr(config, "cache_enabled")
        assert not hasattr(config, "perf_log_enabled")
        assert not hasattr(config, "disabled_extractors")
        assert not hasattr(config, "scan_archives")
        assert not hasattr(config, "max_workers")
        assert not hasattr(config, "max_depth")
        assert not hasattr(config, "max_file_size")

    def test_default_disabled_extractors_constant_exists(self) -> None:
        """DEFAULT_DISABLED_EXTRACTORS 常量仍保留（迁移逻辑引用）。"""
        assert "SourceCodeExtractor" in DEFAULT_DISABLED_EXTRACTORS
        assert "SevenZArchiveExtractor" in DEFAULT_DISABLED_EXTRACTORS


class TestLoadConfig:
    def test_load_nonexistent_returns_default(self, tmp_path: Path) -> None:
        """文件不存在时返回默认配置。"""
        config = load_config(tmp_path / "missing.yaml")
        assert config.scan_paths == []
        assert config.use_builtin is True

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """加载合法 YAML 配置。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "scan_mode: drive\n"
            "scan_paths:\n"
            "  - /path/a\n"
            "  - /path/b\n"
            "rules_paths:\n"
            "  - /rules/r1.yaml\n"
            "use_builtin: false\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.scan_mode == "drive"
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
        """未知字段被忽略，不报错（含已迁移字段）。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "use_builtin: false\n"
            "unknown_field: hello\n"
            "ignore_dirs: ['.git']\n"  # 已迁移字段视为未知键被忽略
            "max_workers: 8\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        assert config.use_builtin is False

    def test_load_ignores_none_values(self, tmp_path: Path) -> None:
        """None 值字段使用默认值。"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "scan_mode: null\nuse_builtin: false\nscan_paths: null\n",
            encoding="utf-8",
        )
        config = load_config(config_file)
        # None 值被过滤，使用默认值
        assert config.scan_mode == "folder"
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


class TestSaveConfig:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """保存后重新加载应得到相同配置。"""
        config_file = tmp_path / "config.yaml"
        original = Config(
            scan_mode="drive",
            scan_paths=["/a", "/b", "/c"],
            rules_paths=["/rules/r1.yaml", "/rules/r2.yaml"],
            use_builtin=False,
        )
        save_config(original, config_file)
        assert config_file.exists()

        loaded = load_config(config_file)
        assert loaded.scan_mode == "drive"
        assert loaded.scan_paths == ["/a", "/b", "/c"]
        assert loaded.rules_paths == ["/rules/r1.yaml", "/rules/r2.yaml"]
        assert loaded.use_builtin is False

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

    def test_save_and_load_staging_dir(self, tmp_path: Path) -> None:
        """暂存区目录持久化（iter-77）。"""
        config_file = tmp_path / "config.yaml"
        original = Config(staging_dir="D:/custom-staging")
        save_config(original, config_file)
        loaded = load_config(config_file)
        assert loaded.staging_dir == "D:/custom-staging"

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


class TestMigrateConfigToRules:
    """``migrate_config_to_rules`` 迁移函数测试。"""

    def test_no_config_file_is_noop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """config.yaml 不存在时 no-op。"""
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", tmp_path / "missing.yaml")
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", tmp_path)
        # 不应抛异常
        migrate_config_to_rules()

    def test_no_migrated_fields_is_noop(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """config.yaml 中无迁移字段时 no-op。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("use_builtin: true\nscan_mode: folder\n", encoding="utf-8")
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()

        # config.yaml 内容应未变
        loaded = load_config(config_path)
        assert loaded.use_builtin is True
        assert loaded.scan_mode == "folder"
        # user-scan.yaml 不应被创建
        assert not (config_dir / "rules" / "user-scan.yaml").exists()

    def test_migrates_scan_params_to_user_scan_yaml(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """迁移 scan_archives/max_workers 等字段到 user-scan.yaml。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(
            "use_builtin: true\n"
            "scan_archives: false\n"
            "max_workers: 8\n"
            "max_depth: 10\n"
            "max_file_size: 104857600\n"
            "cache_enabled: false\n"
            "perf_log_enabled: true\n"
            "ignore_dirs:\n  - .git\n  - node_modules\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()

        # config.yaml 中迁移字段应被清除
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert "scan_archives" not in data
        assert "max_workers" not in data
        assert "max_depth" not in data
        assert "max_file_size" not in data
        assert "cache_enabled" not in data
        assert "perf_log_enabled" not in data
        assert "ignore_dirs" not in data
        # 非迁移字段应保留
        assert data["use_builtin"] is True

        # user-scan.yaml 应被创建并含迁移字段
        user_scan_path = config_dir / "rules" / "user-scan.yaml"
        assert user_scan_path.exists()
        with user_scan_path.open("r", encoding="utf-8") as fh:
            user_data = yaml.safe_load(fh)
        assert user_data["version"] == "1.0"
        assert user_data["ignore_dirs"] == [".git", "node_modules"]
        assert user_data["scan_params"]["scan_archives"] is False
        assert user_data["scan_params"]["max_workers"] == 8
        assert user_data["scan_params"]["max_depth"] == 10
        assert user_data["scan_params"]["max_file_size"] == 104857600
        assert user_data["scan_params"]["cache_enabled"] is False
        assert user_data["scan_params"]["perf_log_enabled"] is True
        assert user_data["whitelist"] == []

        # user-scan.yaml 应被追加到 rules_paths
        assert str(user_scan_path) in data["rules_paths"]

    def test_disabled_extractors_migrated_to_scan_extensions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """disabled_extractors 反推为 scan_extensions 白名单。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(
            "use_builtin: true\ndisabled_extractors:\n  - PdfExtractor\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()

        user_scan_path = config_dir / "rules" / "user-scan.yaml"
        with user_scan_path.open("r", encoding="utf-8") as fh:
            user_data = yaml.safe_load(fh)
        # scan_extensions 应为排除 PdfExtractor 后的扩展名集合
        assert "scan_extensions" in user_data
        exts = user_data["scan_extensions"]
        assert isinstance(exts, list)
        assert "pdf" not in exts  # PdfExtractor 被禁用，扩展名被排除
        # 其他提取器扩展名应存在
        assert "docx" in exts or "doc" in exts

    def test_empty_disabled_extractors_means_none_scan_extensions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """disabled_extractors 为空列表时 scan_extensions=None（全选默认）。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(
            "use_builtin: true\ndisabled_extractors: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()

        user_scan_path = config_dir / "rules" / "user-scan.yaml"
        with user_scan_path.open("r", encoding="utf-8") as fh:
            user_data = yaml.safe_load(fh)
        # 空 disabled_extractors → scan_extensions 不写入（None 全选默认）
        assert "scan_extensions" not in user_data

    def test_existing_user_scan_yaml_not_overwritten(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """user-scan.yaml 已存在时不覆盖（保留用户手工编辑版本）。"""
        config_dir = tmp_path / ".fuscan"
        rules_dir = config_dir / "rules"
        rules_dir.mkdir(parents=True)
        config_path = config_dir / "config.yaml"
        config_path.write_text(
            "use_builtin: true\nscan_archives: false\n",
            encoding="utf-8",
        )
        user_scan_path = rules_dir / "user-scan.yaml"
        # 用户手工编辑的 user-scan.yaml
        user_scan_path.write_text(
            "version: '1.0'\nignore_dirs: [custom_dir]\nwhitelist: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()

        # user-scan.yaml 内容应未被覆盖
        with user_scan_path.open("r", encoding="utf-8") as fh:
            user_data = yaml.safe_load(fh)
        assert user_data["ignore_dirs"] == ["custom_dir"]
        # 不应含迁移字段写入的 scan_params
        assert "scan_params" not in user_data

        # 但 config.yaml 仍应清除迁移字段
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert "scan_archives" not in data
        # user-scan.yaml 仍应被追加到 rules_paths
        assert str(user_scan_path) in data["rules_paths"]

    def test_idempotent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """迁移幂等：二次调用无迁移字段时 no-op。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(
            "use_builtin: true\nscan_archives: false\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()
        # 二次调用：迁移字段已清除，应 no-op
        user_scan_path = config_dir / "rules" / "user-scan.yaml"
        mtime_before = user_scan_path.stat().st_mtime_ns
        migrate_config_to_rules()
        mtime_after = user_scan_path.stat().st_mtime_ns
        # user-scan.yaml 未被重写
        assert mtime_before == mtime_after

    def test_invalid_yaml_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """config.yaml 解析失败时跳过迁移（不抛异常）。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text(":::not valid yaml:::\n  - broken", encoding="utf-8")
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        # 不应抛异常
        migrate_config_to_rules()
        assert not (config_dir / "rules" / "user-scan.yaml").exists()

    def test_non_dict_yaml_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """config.yaml 顶层非 dict 时跳过迁移。"""
        config_dir = tmp_path / ".fuscan"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_path)
        monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)

        migrate_config_to_rules()
        assert not (config_dir / "rules" / "user-scan.yaml").exists()


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

        # 延迟导入路径与 processing.storage.detect_default_staging_dir 内一致
        import fuscan.processing.storage as storage_mod

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)
        monkeypatch.setattr(storage_mod.shutil, "disk_usage", fake_disk_usage)

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

        import fuscan.processing.storage as storage_mod

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives, raising=False)
        monkeypatch.setattr(storage_mod.shutil, "disk_usage", fake_disk_usage)

        result = detect_default_staging_dir()
        assert result == Path("D:\\") / ".fuscan-cache"
