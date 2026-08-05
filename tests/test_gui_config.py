"""``ConfigController`` 单元测试。

验证 ``@Property``/``@Slot`` 桥接的配置读写、扫描路径历史与持久化行为。
扫描参数（scan_archives/max_workers/ignore_dirs/disabled_extractors 等）已
迁移到 RuleSet 顶层，由 ``RulesController.effectiveConfigPreview`` 暴露，
本测试仅覆盖 ConfigController 保留的"应用级"配置（字体、扫描路径、盘符）。
使用 ``tmp_path`` + ``monkeypatch`` 隔离配置文件，避免污染用户主目录。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.config import MAX_HISTORY, Config
    from fuscan.gui.controllers.config_controller import ConfigController

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过配置控制器测试", allow_module_level=True)


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ~/.fuscan 重定向到 tmp_path，避免污染用户配置。"""
    fake_home = tmp_path / "fuscan_home"
    fake_home.mkdir()
    config_dir = fake_home / ".fuscan"
    config_dir.mkdir()
    monkeypatch.setattr("fuscan.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("fuscan.config.CONFIG_PATH", config_dir / "config.yaml")
    return config_dir


@pytest.fixture()
def controller(config_dir: Path) -> ConfigController:
    return ConfigController()


class TestConstruction:
    def test_construct_loads_default_config(self, controller: ConfigController) -> None:
        assert isinstance(controller.config, Config)

    def test_migrated_fields_not_in_config(self, controller: ConfigController) -> None:
        """迁移字段不应存在于 Config 数据类。"""
        assert not hasattr(controller.config, "ignore_dirs")
        assert not hasattr(controller.config, "cache_enabled")
        assert not hasattr(controller.config, "scan_archives")
        assert not hasattr(controller.config, "max_workers")
        assert not hasattr(controller.config, "disabled_extractors")


class TestFontSettings:
    """字体配置（通用设置）测试。"""

    def test_font_size_default_14(self, controller: ConfigController) -> None:
        """默认字号为 14。"""
        assert controller.fontSize == 14

    def test_font_family_default_empty(self, controller: ConfigController) -> None:
        """默认字体族为空串（表示平台默认）。"""
        assert controller.fontFamily == ""

    def test_font_bold_default_false(self, controller: ConfigController) -> None:
        """默认不加粗。"""
        assert controller.fontBold is False

    def test_set_font_family_persists(self, controller: ConfigController) -> None:
        """设置字体族应持久化到 Config。"""
        controller.setFontFamily("Microsoft YaHei UI")
        assert controller.fontFamily == "Microsoft YaHei UI"
        assert controller.config.font_family == "Microsoft YaHei UI"

    def test_set_font_family_empty_means_platform_default(self, controller: ConfigController) -> None:
        """空串字体族表示平台默认（Config 中存储为 None）。"""
        controller.setFontFamily("Arial")
        controller.setFontFamily("")
        assert controller.fontFamily == ""
        assert controller.config.font_family is None

    def test_set_font_size_clamps_to_range(self, controller: ConfigController) -> None:
        """字号应钳制到 8-32 范围。"""
        controller.setFontSize(4)
        assert controller.fontSize == 8
        controller.setFontSize(100)
        assert controller.fontSize == 32

    def test_set_font_size_normal(self, controller: ConfigController) -> None:
        """正常字号设置应持久化。"""
        controller.setFontSize(16)
        assert controller.fontSize == 16
        assert controller.config.font_size == 16

    def test_set_font_bold(self, controller: ConfigController) -> None:
        """设置加粗应持久化。"""
        controller.setFontBold(True)
        assert controller.fontBold is True
        assert controller.config.font_bold is True

    def test_min_font_size_default_12(self, controller: ConfigController) -> None:
        """默认最小字号为 12。"""
        assert controller.minFontSize == 12

    def test_set_min_font_size_persists(self, controller: ConfigController) -> None:
        """设置最小字号应持久化到 Config。"""
        controller.setMinFontSize(14)
        assert controller.minFontSize == 14
        assert controller.config.min_font_size == 14

    def test_set_min_font_size_clamps_to_range(self, controller: ConfigController) -> None:
        """最小字号应钳制到 8-24 范围。"""
        controller.setMinFontSize(2)
        assert controller.minFontSize == 8
        controller.setMinFontSize(100)
        assert controller.minFontSize == 24

    def test_set_min_font_size_noop_when_same(self, controller: ConfigController) -> None:
        """相同最小字号不应触发持久化。"""
        controller.setMinFontSize(12)
        controller.setMinFontSize(12)
        assert controller.minFontSize == 12

    def test_set_font_family_noop_when_same(self, controller: ConfigController) -> None:
        """相同字体族不应触发持久化。"""
        controller.setFontFamily("Arial")
        # 再次设置相同值，不应报错
        controller.setFontFamily("Arial")
        assert controller.fontFamily == "Arial"

    def test_reset_to_defaults_resets_font(self, controller: ConfigController) -> None:
        """resetToDefaults 应重置字体设置为默认值。"""
        controller.setFontFamily("Arial")
        controller.setFontSize(20)
        controller.setFontBold(True)
        controller.setMinFontSize(16)
        controller.resetToDefaults()
        assert controller.fontFamily == ""
        assert controller.fontSize == 14
        assert controller.fontBold is False
        assert controller.minFontSize == 12


class TestScanPaths:
    def test_scan_paths_default_empty(self, controller: ConfigController) -> None:
        assert controller.scanPaths == []

    def test_add_scan_path(self, controller: ConfigController) -> None:
        controller.add_scan_path("/tmp/test1")
        assert controller.scanPaths == ["/tmp/test1"]

    def test_add_scan_path_dedup(self, controller: ConfigController) -> None:
        controller.add_scan_path("/tmp/test1")
        controller.add_scan_path("/tmp/test1")
        assert controller.scanPaths == ["/tmp/test1"]

    def test_add_scan_path_recent_first(self, controller: ConfigController) -> None:
        controller.add_scan_path("/tmp/test1")
        controller.add_scan_path("/tmp/test2")
        assert controller.scanPaths == ["/tmp/test2", "/tmp/test1"]

    def test_add_scan_path_empty_noop(self, controller: ConfigController) -> None:
        controller.add_scan_path("")
        assert controller.scanPaths == []

    def test_add_scan_path_capped_to_max_history(self, controller: ConfigController) -> None:
        for i in range(MAX_HISTORY + 5):
            controller.add_scan_path(f"/tmp/path_{i}")
        assert len(controller.scanPaths) == MAX_HISTORY

    def test_clear_scan_paths(self, controller: ConfigController) -> None:
        controller.add_scan_path("/tmp/test1")
        controller.clearScanPaths()
        assert controller.scanPaths == []


class TestSave:
    def test_save_emits_config_changed(self, controller: ConfigController) -> None:
        emitted: list[None] = []
        controller.configChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.save()
        assert len(emitted) == 1

    def test_save_debounces_disk_write(self, controller: ConfigController, config_dir: Path) -> None:
        """多次 save 合并为一次磁盘写入：timer 未触发前磁盘不应被写入。"""
        from fuscan.config import load_config

        controller.config.font_size = 20
        controller.save()
        # 300ms debounce 未到，磁盘上应仍是旧值（默认 14）
        assert load_config().font_size == 14
        # 第二次 save 重启 timer，仍不应写入
        controller.config.font_size = 24
        controller.save()
        assert load_config().font_size == 14

    def test_flush_save_writes_immediately(self, controller: ConfigController, config_dir: Path) -> None:
        """flush_save 取消 debounce timer 并立即写入磁盘。"""
        from fuscan.config import load_config

        controller.config.font_size = 20
        controller.save()
        assert load_config().font_size == 14  # 未 flush
        controller.flush_save()
        assert load_config().font_size == 20  # flush 后立即写入

    def test_flush_save_noop_when_no_pending(self, controller: ConfigController, config_dir: Path) -> None:
        """无待写入时 flush_save 为 no-op，不抛异常。"""
        controller.flush_save()  # 不应抛异常


class TestDrives:
    def test_drives_returns_list(self, controller: ConfigController) -> None:
        """drives 属性返回 list[str]，至少不抛异常。"""
        drives = controller.drives
        assert isinstance(drives, list)

    def test_refresh_drives_emits_signal(self, controller: ConfigController) -> None:
        emitted: list[None] = []
        controller.drivesChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.refresh_drives()
        assert len(emitted) == 1

    def test_drives_cached(self, controller: ConfigController, monkeypatch: pytest.MonkeyPatch) -> None:
        """连续访问 drives 不重复调用 list_drives（缓存生效）。"""
        calls: list[int] = []

        def fake_list_drives(include_network: bool = False) -> list[str]:
            calls.append(1)
            return ["C:\\", "D:\\"]

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives)
        # 首次访问触发枚举
        assert controller.drives == ["C:\\", "D:\\"]
        # 二次访问命中缓存，不再枚举
        assert controller.drives == ["C:\\", "D:\\"]
        assert len(calls) == 1

    def test_refresh_drives_clears_cache(self, controller: ConfigController, monkeypatch: pytest.MonkeyPatch) -> None:
        """refresh_drives 清空缓存，下次访问重新枚举。"""
        calls: list[int] = []

        def fake_list_drives(include_network: bool = False) -> list[str]:
            calls.append(1)
            return ["C:\\"]

        monkeypatch.setattr("fuscan.scanner.walker.list_drives", fake_list_drives)
        _ = controller.drives  # 触发首次枚举
        controller.refresh_drives()
        _ = controller.drives  # 应重新枚举
        assert len(calls) == 2


class TestResetToDefaults:
    """``resetToDefaults`` Slot（仅重置字体配置）。"""

    def test_resets_font_settings(self, controller: ConfigController) -> None:
        """resetToDefaults 应重置字体设置为默认值。"""
        controller.setFontFamily("Arial")
        controller.setFontSize(20)
        controller.setFontBold(True)
        controller.setMinFontSize(16)
        controller.resetToDefaults()
        assert controller.fontFamily == ""
        assert controller.fontSize == 14
        assert controller.fontBold is False
        assert controller.minFontSize == 12

    def test_emits_config_changed(self, controller: ConfigController) -> None:
        emitted: list[None] = []
        controller.configChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.resetToDefaults()
        assert len(emitted) >= 1

    def test_persists_to_disk(self, controller: ConfigController, config_dir: Path) -> None:
        """重置后应保存到磁盘（debounce 路径需 flush_save 强制写入）。"""
        from fuscan.config import load_config

        controller.setFontSize(20)
        controller.flush_save()
        # 验证 20 已写入磁盘（避免断言与默认值巧合相等）
        assert load_config().font_size == 20
        controller.resetToDefaults()
        controller.flush_save()
        assert load_config().font_size == 14


class TestGetConfigValue:
    """``get_config_value`` 按 task_override 字段名读取全局配置值。

    扫描参数字段已迁移到 RuleSet，此处返回 None；仅 rules_paths/use_builtin
    返回实际值。
    """

    def test_rules_paths_returns_tuple(self, controller: ConfigController) -> None:
        """rules_paths 应返回 tuple 类型。"""
        controller.config.rules_paths = ["/rules/r1.yaml", "/rules/r2.yaml"]
        result = controller.get_config_value("rules_paths")
        assert isinstance(result, tuple)
        assert result == ("/rules/r1.yaml", "/rules/r2.yaml")

    def test_use_builtin_returns_bool(self, controller: ConfigController) -> None:
        """use_builtin 应返回布尔值。"""
        result = controller.get_config_value("use_builtin")
        assert result is True

    def test_migrated_field_returns_none(self, controller: ConfigController) -> None:
        """已迁移字段应返回 None（由调用方从 ruleset 读取）。"""
        assert controller.get_config_value("scan_archives") is None
        assert controller.get_config_value("max_workers") is None
        assert controller.get_config_value("max_file_size") is None
        assert controller.get_config_value("max_depth") is None
        assert controller.get_config_value("ignore_dirs") is None
        assert controller.get_config_value("cache_enabled") is None
        assert controller.get_config_value("perf_log_enabled") is None
        assert controller.get_config_value("disabled_extractors") is None

    def test_unknown_key_returns_none(self, controller: ConfigController) -> None:
        """未知字段应返回 None。"""
        assert controller.get_config_value("nonexistent_field") is None


class TestCpuCount:
    """``cpuCount`` 属性。"""

    def test_cpu_count_positive(self, controller: ConfigController) -> None:
        """cpuCount 应返回正整数（≥1）。"""
        assert controller.cpuCount >= 1
