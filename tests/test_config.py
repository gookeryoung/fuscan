"""配置持久化模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from uniscan.config import Config, load_config, save_config


class TestConfig:
    def test_default_config(self) -> None:
        """默认配置字段值。"""
        config = Config()
        assert config.window_geometry == [300, 300, 1200, 900]
        assert config.window_state == "maximized"
        assert config.splitter_sizes == []
        assert config.scan_paths == []
        assert config.rules_paths == []
        assert config.use_builtin is True
        assert config.scan_mode == "folder"
        assert config.last_drive is None


class TestLoadConfig:
    def test_load_nonexistent_returns_default(self, tmp_path: Path) -> None:
        """文件不存在时返回默认配置。"""
        config = load_config(tmp_path / "missing.yaml")
        assert config.window_geometry == [300, 300, 1200, 900]
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
        assert config.window_geometry == [300, 300, 1200, 900]
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
            window_geometry=[10, 20, 300, 400],
            window_state="normal",
            splitter_sizes=[200, 800],
            scan_paths=["/a", "/b", "/c"],
            rules_paths=["/rules/r1.yaml", "/rules/r2.yaml"],
            use_builtin=False,
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
