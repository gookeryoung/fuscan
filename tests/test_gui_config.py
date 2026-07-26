"""``ConfigController`` 单元测试。

验证 ``@Property``/``@Slot`` 桥接的配置读写、提取器勾选管理、扫描路径历史
与持久化行为。使用 ``tmp_path`` + ``monkeypatch`` 隔离配置文件，避免污染
用户主目录。
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
    from fuscan.gui.models.extractor_model import ExtractorListModel

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

    def test_extractor_model_loaded(self, controller: ConfigController) -> None:
        """构造时应加载提取器列表到 model。"""
        model = controller.extractorModel
        assert isinstance(model, ExtractorListModel)
        assert model.total_count > 0


class TestScanSettings:
    def test_scan_archives_default_true(self, controller: ConfigController) -> None:
        assert controller.scanArchives is True

    def test_set_scan_archives_persists(self, controller: ConfigController) -> None:
        controller.setScanArchives(False)
        assert controller.scanArchives is False
        assert controller.config.scan_archives is False

    def test_set_scan_archives_noop_when_same(self, controller: ConfigController) -> None:
        controller.setScanArchives(True)
        assert controller.scanArchives is True

    def test_max_workers_default_5(self, controller: ConfigController) -> None:
        assert controller.maxWorkers == 5

    def test_set_max_workers_valid_range(self, controller: ConfigController) -> None:
        controller.setMaxWorkers(8)
        assert controller.maxWorkers == 8

    def test_set_max_workers_rejects_invalid(self, controller: ConfigController) -> None:
        original = controller.maxWorkers
        controller.setMaxWorkers(0)
        assert controller.maxWorkers == original
        controller.setMaxWorkers(100)
        assert controller.maxWorkers == original

    def test_max_file_size_mb_default_50(self, controller: ConfigController) -> None:
        assert controller.maxFileSizeMB == 50

    def test_set_max_file_size_mb(self, controller: ConfigController) -> None:
        controller.setMaxFileSizeMB(100)
        assert controller.maxFileSizeMB == 100
        assert controller.config.max_file_size == 100 * 1024 * 1024

    def test_max_depth_default_0_means_unlimited(self, controller: ConfigController) -> None:
        assert controller.maxDepth == 0
        assert controller.config.max_depth is None

    def test_set_max_depth_positive(self, controller: ConfigController) -> None:
        controller.setMaxDepth(5)
        assert controller.maxDepth == 5
        assert controller.config.max_depth == 5

    def test_set_max_depth_zero_means_unlimited(self, controller: ConfigController) -> None:
        controller.setMaxDepth(10)
        controller.setMaxDepth(0)
        assert controller.config.max_depth is None


class TestCacheAndPerf:
    def test_cache_enabled_default_true(self, controller: ConfigController) -> None:
        assert controller.cacheEnabled is True

    def test_set_cache_enabled(self, controller: ConfigController) -> None:
        controller.setCacheEnabled(False)
        assert controller.cacheEnabled is False

    def test_perf_log_enabled_default_false(self, controller: ConfigController) -> None:
        assert controller.perfLogEnabled is False

    def test_set_perf_log_enabled(self, controller: ConfigController) -> None:
        controller.setPerfLogEnabled(True)
        assert controller.perfLogEnabled is True


class TestIgnoreDirs:
    def test_ignore_dirs_text_default_non_empty(self, controller: ConfigController) -> None:
        """默认 ignore_dirs 含版本控制/缓存等目录，文本应非空。"""
        assert controller.ignoreDirsText != ""
        assert ".git" in controller.ignoreDirsText

    def test_set_ignore_dirs_text(self, controller: ConfigController) -> None:
        text = ".git\n__pycache__\nvenv"
        controller.setIgnoreDirsText(text)
        assert controller.config.ignore_dirs == [".git", "__pycache__", "venv"]
        assert controller.ignoreDirsText == text

    def test_set_ignore_dirs_text_strips_empty_lines(self, controller: ConfigController) -> None:
        controller.setIgnoreDirsText("  .git  \n\n\n__pycache__\n")
        assert controller.config.ignore_dirs == [".git", "__pycache__"]


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


class TestExtractorSelection:
    def test_extractor_count_text_format(self, controller: ConfigController) -> None:
        text = controller.extractorCountText
        assert text.startswith("已勾选 ")
        assert "/" in text

    def test_select_all_extractors(self, controller: ConfigController) -> None:
        controller.unselectAllExtractors()
        assert controller.config.disabled_extractors
        controller.selectAllExtractors()
        assert controller.config.disabled_extractors == []

    def test_unselect_all_extractors(self, controller: ConfigController) -> None:
        controller.unselectAllExtractors()
        assert len(controller.config.disabled_extractors) > 0

    def test_set_extractor_enabled_persists(self, controller: ConfigController) -> None:
        first_class = controller.extractorModel.data(
            controller.extractorModel.index(0),
            0x0100 + 1,  # Qt.UserRole + 1
        )
        controller.setExtractorEnabled(first_class, False)
        assert first_class in controller.config.disabled_extractors
        controller.setExtractorEnabled(first_class, True)
        assert first_class not in controller.config.disabled_extractors

    def test_enabled_extensions_all_selected_returns_none(self, controller: ConfigController) -> None:
        """全部勾选时返回 None（扫描所有文件，与 Scanner scan_extensions 语义一致）。"""
        controller.selectAllExtractors()
        assert controller.enabled_extensions() is None


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


class TestResetToDefaults:
    """``resetToDefaults`` Slot。"""

    def test_resets_scan_archives(self, controller: ConfigController) -> None:
        controller.setScanArchives(False)
        assert controller.scanArchives is False
        controller.resetToDefaults()
        assert controller.scanArchives is True

    def test_resets_max_workers(self, controller: ConfigController) -> None:
        controller.setMaxWorkers(8)
        assert controller.maxWorkers == 8
        controller.resetToDefaults()
        assert controller.maxWorkers == 5

    def test_resets_max_file_size_mb(self, controller: ConfigController) -> None:
        controller.setMaxFileSizeMB(100)
        controller.resetToDefaults()
        assert controller.maxFileSizeMB == 50

    def test_resets_max_depth(self, controller: ConfigController) -> None:
        controller.setMaxDepth(10)
        controller.resetToDefaults()
        assert controller.maxDepth == 0  # 0 表示无限

    def test_resets_cache_enabled(self, controller: ConfigController) -> None:
        controller.setCacheEnabled(False)
        controller.resetToDefaults()
        assert controller.cacheEnabled is True

    def test_resets_perf_log_enabled(self, controller: ConfigController) -> None:
        controller.setPerfLogEnabled(True)
        controller.resetToDefaults()
        assert controller.perfLogEnabled is False

    def test_resets_ignore_dirs(self, controller: ConfigController) -> None:
        controller.setIgnoreDirsText("custom_dir\nanother")
        controller.resetToDefaults()
        # 默认 ignore_dirs 非空（含 system volume information 等）
        assert "custom_dir" not in controller.ignoreDirsText

    def test_emits_config_changed(self, controller: ConfigController) -> None:
        emitted: list[None] = []
        controller.configChanged.connect(lambda: emitted.append(None))  # pyrefly: ignore [missing-attribute]
        controller.resetToDefaults()
        assert len(emitted) >= 1

    def test_persists_to_disk(self, controller: ConfigController, config_dir: Path) -> None:
        """重置后应保存到磁盘。"""
        controller.setMaxWorkers(8)
        controller.resetToDefaults()
        # 重新加载配置应得到默认值
        from fuscan.config import load_config

        reloaded = load_config()
        assert reloaded.max_workers == 5
