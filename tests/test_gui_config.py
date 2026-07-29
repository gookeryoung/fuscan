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

    def test_cpu_count_positive(self, controller: ConfigController) -> None:
        """cpuCount 应返回正整数（≥1）。"""
        assert controller.cpuCount >= 1


class TestEntropySettings:
    """高熵字符串检测配置（iter-134）。"""

    def test_entropy_enabled_default(self, controller: ConfigController) -> None:
        assert controller.entropyEnabled is True

    def test_set_entropy_enabled(self, controller: ConfigController) -> None:
        controller.setEntropyEnabled(False)
        assert controller.entropyEnabled is False

    def test_entropy_threshold_default(self, controller: ConfigController) -> None:
        assert 3.0 <= controller.entropyThreshold <= 5.0

    def test_set_entropy_threshold_clamped(self, controller: ConfigController) -> None:
        """阈值应钳制到 3.0~5.0 范围。"""
        controller.setEntropyThreshold(10.0)
        assert controller.entropyThreshold == 5.0
        controller.setEntropyThreshold(1.0)
        assert controller.entropyThreshold == 3.0


class TestIgnoreDirs:
    def test_ignore_dir_categories_default_non_empty(self, controller: ConfigController) -> None:
        """默认 ignoreDirCategories 含多个分类，每个分类含目录项。"""
        cats = controller.ignoreDirCategories
        assert len(cats) > 0
        # 版本控制分类应存在且含 .git
        vc_cats = [c for c in cats if c["category"] == "版本控制"]
        assert len(vc_cats) == 1
        vc_dirs = vc_cats[0]["dirs"]
        assert any(d["name"] == ".git" and d["enabled"] for d in vc_dirs)

    def test_toggle_ignore_dir_disable_and_enable(self, controller: ConfigController) -> None:
        """toggleIgnoreDir 可取消勾选预设目录并重新勾选。"""
        # 初始 .git 在忽略列表中
        assert ".git" in controller.config.ignore_dirs
        # 取消勾选
        controller.toggleIgnoreDir(".git", False)
        assert ".git" not in controller.config.ignore_dirs
        # 重新勾选
        controller.toggleIgnoreDir(".git", True)
        assert ".git" in controller.config.ignore_dirs

    def test_toggle_ignore_dir_case_insensitive_removal(self, controller: ConfigController) -> None:
        """取消勾选时按大小写不敏感移除。"""
        controller.toggleIgnoreDir(".GIT", False)
        assert ".git" not in controller.config.ignore_dirs

    def test_set_ignore_dir_category_enabled_batch(self, controller: ConfigController) -> None:
        """setIgnoreDirCategoryEnabled 批量取消/勾选整个分类。"""
        # 取消整个 Python 分类
        controller.setIgnoreDirCategoryEnabled("Python", False)
        assert "__pycache__" not in controller.config.ignore_dirs
        assert ".venv" not in controller.config.ignore_dirs
        # 重新勾选
        controller.setIgnoreDirCategoryEnabled("Python", True)
        assert "__pycache__" in controller.config.ignore_dirs
        assert ".venv" in controller.config.ignore_dirs

    def test_add_custom_ignore_dir(self, controller: ConfigController) -> None:
        """addCustomIgnoreDir 添加自定义目录到忽略列表。"""
        controller.addCustomIgnoreDir("my_special_cache")
        assert "my_special_cache" in controller.config.ignore_dirs
        assert "my_special_cache" in controller.customIgnoreDirs

    def test_add_custom_ignore_dir_dedup_case_insensitive(self, controller: ConfigController) -> None:
        """addCustomIgnoreDir 大小写不敏感去重。"""
        controller.addCustomIgnoreDir(".git")
        # 已存在 .git（小写），不应重复添加
        count = controller.config.ignore_dirs.count(".git")
        assert count == 1

    def test_add_custom_ignore_dir_ignores_empty(self, controller: ConfigController) -> None:
        """addCustomIgnoreDir 忽略空字符串。"""
        before = len(controller.config.ignore_dirs)
        controller.addCustomIgnoreDir("   ")
        assert len(controller.config.ignore_dirs) == before

    def test_remove_custom_ignore_dir(self, controller: ConfigController) -> None:
        """removeCustomIgnoreDir 移除自定义目录。"""
        controller.addCustomIgnoreDir("temp_cache")
        assert "temp_cache" in controller.config.ignore_dirs
        controller.removeCustomIgnoreDir("temp_cache")
        assert "temp_cache" not in controller.config.ignore_dirs

    def test_custom_ignore_dirs_excludes_preset(self, controller: ConfigController) -> None:
        """customIgnoreDirs 不含预设分类中的目录。"""
        controller.addCustomIgnoreDir("custom_only")
        custom = controller.customIgnoreDirs
        assert "custom_only" in custom
        # 预设目录不出现在自定义列表中
        assert ".git" not in custom
        assert "node_modules" not in custom

    def test_large_software_category_exists(self, controller: ConfigController) -> None:
        """大型软件分类应存在并含 ANSYS/Autodesk/SolidWorks 等目录。"""
        cats = controller.ignoreDirCategories
        ls_cats = [c for c in cats if c["category"] == "大型软件"]
        assert len(ls_cats) == 1
        dir_names = [d["name"] for d in ls_cats[0]["dirs"]]
        assert "ANSYS Inc" in dir_names
        assert "Autodesk" in dir_names
        assert "SOLIDWORKS Corp" in dir_names
        assert "Kingsoft" in dir_names

    def test_select_all_ignore_dirs_adds_all_preset(self, controller: ConfigController) -> None:
        """selectAllIgnoreDirs 应将所有预设分类下的目录加入忽略列表。"""
        # 先清空所有预设目录
        controller.unselectAllIgnoreDirs()
        assert ".git" not in controller.config.ignore_dirs
        assert "node_modules" not in controller.config.ignore_dirs
        # 全选
        controller.selectAllIgnoreDirs()
        # 各分类代表目录应存在
        assert ".git" in controller.config.ignore_dirs
        assert "node_modules" in controller.config.ignore_dirs
        assert "__pycache__" in controller.config.ignore_dirs

    def test_select_all_ignore_dirs_preserves_custom(self, controller: ConfigController) -> None:
        """selectAllIgnoreDirs 不影响已存在的自定义目录。"""
        controller.addCustomIgnoreDir("my_custom_dir")
        controller.selectAllIgnoreDirs()
        assert "my_custom_dir" in controller.config.ignore_dirs

    def test_select_all_ignore_dirs_idempotent(self, controller: ConfigController) -> None:
        """selectAllIgnoreDirs 幂等：重复调用不产生重复条目。"""
        controller.selectAllIgnoreDirs()
        before = len(controller.config.ignore_dirs)
        controller.selectAllIgnoreDirs()
        after = len(controller.config.ignore_dirs)
        assert before == after

    def test_select_all_ignore_dirs_case_insensitive_dedup(self, controller: ConfigController) -> None:
        """selectAllIgnoreDirs 大小写不敏感去重（.GIT 不与 .git 重复）。"""
        controller.unselectAllIgnoreDirs()
        # 手动加入大写版本
        controller.config.ignore_dirs.append(".GIT")
        controller.selectAllIgnoreDirs()
        # 应只保留一个 .git（小写版本），大写版本被去重
        git_entries = [d for d in controller.config.ignore_dirs if d.lower() == ".git"]
        assert len(git_entries) == 1

    def test_unselect_all_ignore_dirs_removes_all_preset(self, controller: ConfigController) -> None:
        """unselectAllIgnoreDirs 应移除所有预设分类下的目录。"""
        # 确保默认有预设目录
        assert ".git" in controller.config.ignore_dirs
        controller.unselectAllIgnoreDirs()
        assert ".git" not in controller.config.ignore_dirs
        assert "node_modules" not in controller.config.ignore_dirs
        assert "__pycache__" not in controller.config.ignore_dirs

    def test_unselect_all_ignore_dirs_preserves_custom(self, controller: ConfigController) -> None:
        """unselectAllIgnoreDirs 保留自定义目录。"""
        controller.addCustomIgnoreDir("my_custom_dir")
        controller.unselectAllIgnoreDirs()
        assert "my_custom_dir" in controller.config.ignore_dirs
        # 预设目录被移除
        assert ".git" not in controller.config.ignore_dirs

    def test_unselect_all_ignore_dirs_idempotent(self, controller: ConfigController) -> None:
        """unselectAllIgnoreDirs 幂等：无预设目录时调用不抛异常。"""
        controller.unselectAllIgnoreDirs()
        # 再次调用应无副作用
        controller.unselectAllIgnoreDirs()
        assert ".git" not in controller.config.ignore_dirs

    def test_unselect_all_ignore_dirs_case_insensitive(self, controller: ConfigController) -> None:
        """unselectAllIgnoreDirs 大小写不敏感移除预设目录。"""
        # 手动加入大写版本
        controller.config.ignore_dirs.append(".GIT")
        controller.unselectAllIgnoreDirs()
        # 大写版本也应被移除
        git_entries = [d for d in controller.config.ignore_dirs if d.lower() == ".git"]
        assert len(git_entries) == 0


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
        controller.addCustomIgnoreDir("custom_dir")
        controller.addCustomIgnoreDir("another")
        assert "custom_dir" in controller.config.ignore_dirs
        controller.resetToDefaults()
        # 默认 ignore_dirs 非空（含 system volume information 等）
        assert "custom_dir" not in controller.config.ignore_dirs
        assert "another" not in controller.config.ignore_dirs

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


class TestGetConfigValue:
    """``get_config_value`` 按 task_override 字段名读取全局配置值（iter-127）。

    覆盖所有分支：scan_archives / max_workers / max_file_size / max_depth /
    ignore_dirs / 未知字段。
    """

    def test_scan_archives(self, controller: ConfigController) -> None:
        assert controller.get_config_value("scan_archives") == controller.config.scan_archives

    def test_max_workers(self, controller: ConfigController) -> None:
        assert controller.get_config_value("max_workers") == controller.config.max_workers

    def test_max_file_size(self, controller: ConfigController) -> None:
        assert controller.get_config_value("max_file_size") == controller.config.max_file_size

    def test_max_depth_default_zero(self, controller: ConfigController) -> None:
        """max_depth 默认 None 时应返回 0。"""
        assert controller.get_config_value("max_depth") == 0

    def test_max_depth_set_returns_value(self, controller: ConfigController) -> None:
        """max_depth 设置后应返回设置值。"""
        controller.config.max_depth = 5
        assert controller.get_config_value("max_depth") == 5

    def test_ignore_dirs_returns_tuple(self, controller: ConfigController) -> None:
        """ignore_dirs 应返回 tuple 类型。"""
        result = controller.get_config_value("ignore_dirs")
        assert isinstance(result, tuple)
        assert result == tuple(controller.config.ignore_dirs)

    def test_unknown_key_returns_none(self, controller: ConfigController) -> None:
        """未知字段应返回 None。"""
        assert controller.get_config_value("nonexistent_field") is None


class TestCategoryEnabled:
    """``setCategoryEnabled``/``categoryEnabledState`` 类别勾选测试。"""

    def test_set_category_enabled_updates_config(self, controller: ConfigController) -> None:
        """setCategoryEnabled 应更新提取器勾选状态并持久化到 config。"""
        model = controller.extractorModel
        # 取第一个提取器所在的类别
        first_category = model._rows[0].category  # type: ignore[attr-defined]
        # 先禁用该类别
        controller.setCategoryEnabled(first_category, False)
        # disabled_extractors 应包含该类别下的提取器
        assert len(controller.config.disabled_extractors) > 0

        # 再启用
        controller.setCategoryEnabled(first_category, True)
        # disabled_extractors 应不再包含该类别下的提取器
        assert len(controller.config.disabled_extractors) == 0

    def test_category_enabled_state_all_selected(self, controller: ConfigController) -> None:
        """全部启用时 categoryEnabledState 返回 1（全选）。"""
        model = controller.extractorModel
        first_category = model._rows[0].category  # type: ignore[attr-defined]
        assert controller.categoryEnabledState(first_category) == 1

    def test_category_enabled_state_none_selected(self, controller: ConfigController) -> None:
        """全部禁用时 categoryEnabledState 返回 0（全不选）。"""
        model = controller.extractorModel
        first_category = model._rows[0].category  # type: ignore[attr-defined]
        controller.setCategoryEnabled(first_category, False)
        assert controller.categoryEnabledState(first_category) == 0
