"""``ScanController`` 结果详情与替换功能测试（iter-101）。

验证：

- :meth:`ScanController.selectNextResult` / :meth:`selectPrevResult`：上一条/下一条切换
- :attr:`ScanController.canSelectNext` / :attr:`canSelectPrev`：边界条件
- :attr:`ScanController.canReplaceSelected`：不同场景下的可替换判断
- :meth:`ScanController.replaceSelectedResult`：未选中/无规则集/压缩包条目/无 replace=True/成功替换
- :attr:`ScanController.detailFileSize` / :attr:`detailIsArchiveEntry`：文件元信息
- :attr:`ScanController.detailHitsModel`：扩展字段（matchText/matchCount/target/description）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.gui.controllers.config_controller import ConfigController
    from fuscan.gui.controllers.rules_controller import RulesController
    from fuscan.gui.controllers.scan_controller import ScanController
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner import ScanReport, ScanResult, ScanStats
    from fuscan.scanner.result import RuleHit

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过结果详情测试", allow_module_level=True)


def _build_replace_ruleset() -> RuleSet:
    """构造带 replace=True 规则的 RuleSet。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="可替换规则",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
                replace=True,
                replace_with="[REDACTED]",
            ),
            Rule(
                name="不可替换规则",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="debug"),
            ),
        ),
    )


def _make_scan_result(
    path: Path = Path("/tmp/test.txt"),
    hits: int = 1,
    archive_path: Path | None = None,
) -> ScanResult:
    """构造测试用 ScanResult。"""
    rule_hits = tuple(
        RuleHit(
            rule_name="可替换规则",
            severity=Severity.CRITICAL,
            detail=f"命中 {i}",
            match_text="secret" if i == 0 else "",
            match_texts=("secret",) if i == 0 else (),
            match_count=1,
            target="content",
            match_description="敏感密钥检测",
        )
        for i in range(hits)
    )
    return ScanResult(path=path, size=1024, hits=rule_hits, archive_path=archive_path)


def _make_scan_report(results: tuple[ScanResult, ...]) -> ScanReport:
    """构造测试用 ScanReport。"""
    return ScanReport(
        root=Path("/tmp"),
        results=results,
        stats=ScanStats(total_files=10, scanned_files=10, matched_files=len(results)),
    )


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
def config_controller(config_dir: Path) -> ConfigController:
    return ConfigController()


@pytest.fixture()
def rules_controller(config_controller: ConfigController) -> RulesController:
    return RulesController(config_controller)


@pytest.fixture()
def controller(config_controller: ConfigController, rules_controller: RulesController) -> ScanController:
    return ScanController(config_controller, rules_controller)


@pytest.fixture()
def controller_with_results(controller: ScanController) -> ScanController:
    """构造带 3 条结果 + 可替换 ruleset 的 controller。"""
    controller._ruleset = _build_replace_ruleset()
    results = tuple(_make_scan_result(Path(f"/tmp/file{i}.txt")) for i in range(3))
    controller._result_model.set_results(results)
    controller._last_report = _make_scan_report(results)
    return controller


class TestSelectNextPrev:
    """测试 selectNextResult / selectPrevResult 与 canSelect 属性。"""

    def test_can_select_prev_false_initially(self, controller_with_results: ScanController) -> None:
        """未选中时 canSelectPrev 为 False。"""
        assert controller_with_results.canSelectPrev is False

    def test_can_select_next_false_when_no_selection(self, controller_with_results: ScanController) -> None:
        """未选中（-1）时 canSelectNext 为 False（-1 不在 0..count-2 范围）。"""
        assert controller_with_results.canSelectNext is False

    def test_select_next_advances_index(self, controller_with_results: ScanController) -> None:
        """从第 0 条 selectNextResult 应到第 1 条。"""
        controller_with_results.setSelectedResultIndex(0)
        controller_with_results.selectNextResult()
        assert controller_with_results.selectedResultIndex == 1

    def test_select_next_at_last_noop(self, controller_with_results: ScanController) -> None:
        """最后一条 selectNextResult 应被忽略。"""
        controller_with_results.setSelectedResultIndex(2)
        controller_with_results.selectNextResult()
        assert controller_with_results.selectedResultIndex == 2
        assert controller_with_results.canSelectNext is False

    def test_select_prev_decreases_index(self, controller_with_results: ScanController) -> None:
        """从第 2 条 selectPrevResult 应到第 1 条。"""
        controller_with_results.setSelectedResultIndex(2)
        controller_with_results.selectPrevResult()
        assert controller_with_results.selectedResultIndex == 1

    def test_select_prev_at_first_noop(self, controller_with_results: ScanController) -> None:
        """第 0 条 selectPrevResult 应被忽略。"""
        controller_with_results.setSelectedResultIndex(0)
        controller_with_results.selectPrevResult()
        assert controller_with_results.selectedResultIndex == 0
        assert controller_with_results.canSelectPrev is False

    def test_can_select_next_true_in_middle(self, controller_with_results: ScanController) -> None:
        """第 1 条（共 3 条）时 canSelectNext 与 canSelectPrev 均为 True。"""
        controller_with_results.setSelectedResultIndex(1)
        assert controller_with_results.canSelectNext is True
        assert controller_with_results.canSelectPrev is True


class TestCanReplaceSelected:
    """测试 canReplaceSelected 属性。"""

    def test_false_when_no_selection(self, controller_with_results: ScanController) -> None:
        """未选中结果时 canReplaceSelected 为 False。"""
        assert controller_with_results.canReplaceSelected is False

    def test_false_when_archive_entry(self, controller: ScanController) -> None:
        """压缩包内部条目不可替换。"""
        controller._ruleset = _build_replace_ruleset()
        result = _make_scan_result(Path("/tmp/archive.zip!inner.txt"), archive_path=Path("/tmp/archive.zip"))
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report((result,))
        controller.setSelectedResultIndex(0)
        assert controller.canReplaceSelected is False

    def test_false_when_no_replace_rule(self, controller: ScanController) -> None:
        """命中规则均未启用 replace 时不可替换。"""
        no_replace_ruleset = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="不可替换规则",
                    severity=Severity.WARNING,
                    match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="debug"),
                ),
            ),
        )
        controller._ruleset = no_replace_ruleset
        result = ScanResult(
            path=Path("/tmp/test.txt"),
            size=100,
            hits=(RuleHit(rule_name="不可替换规则", severity=Severity.WARNING, detail="命中"),),
        )
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report((result,))
        controller.setSelectedResultIndex(0)
        assert controller.canReplaceSelected is False

    def test_true_when_has_replace_rule(self, controller_with_results: ScanController) -> None:
        """命中规则中存在 replace=True 时可替换。"""
        controller_with_results.setSelectedResultIndex(0)
        assert controller_with_results.canReplaceSelected is True


class TestReplaceSelectedResult:
    """测试 replaceSelectedResult Slot。"""

    def test_returns_message_when_no_selection(self, controller_with_results: ScanController) -> None:
        """未选中结果时返回提示。"""
        msg = controller_with_results.replaceSelectedResult()
        assert "未选中" in msg

    def test_returns_message_when_no_ruleset(self, controller: ScanController) -> None:
        """规则集未加载时返回提示。"""
        result = _make_scan_result()
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report((result,))
        controller.setSelectedResultIndex(0)
        controller._ruleset = None
        msg = controller.replaceSelectedResult()
        assert "规则集" in msg

    def test_returns_message_when_archive_entry(self, controller: ScanController) -> None:
        """压缩包内部条目返回不可替换提示。"""
        controller._ruleset = _build_replace_ruleset()
        result = _make_scan_result(Path("/tmp/archive.zip!inner.txt"), archive_path=Path("/tmp/archive.zip"))
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report((result,))
        controller.setSelectedResultIndex(0)
        msg = controller.replaceSelectedResult()
        assert "压缩包" in msg

    def test_replace_success_returns_message(
        self,
        controller_with_results: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功替换应返回成功消息并创建备份。"""
        # 准备真实文件
        src_file = tmp_path / "test.txt"
        src_file.write_text("this is a secret value", encoding="utf-8")
        result = ScanResult(
            path=src_file,
            size=src_file.stat().st_size,
            hits=(
                RuleHit(
                    rule_name="可替换规则",
                    severity=Severity.CRITICAL,
                    detail="命中 secret",
                    match_text="secret",
                    match_texts=("secret",),
                    match_count=1,
                    target="content",
                ),
            ),
        )
        controller_with_results._result_model.set_results((result,))
        controller_with_results._last_report = ScanReport(
            root=tmp_path,
            results=(result,),
            stats=ScanStats(total_files=1, scanned_files=1, matched_files=1),
        )
        controller_with_results.setSelectedResultIndex(0)

        # 备份目录重定向到 tmp_path
        backup_dir = tmp_path / "backup"
        monkeypatch.setattr(
            controller_with_results._config,
            "backup_dir",
            str(backup_dir),
        )

        msg = controller_with_results.replaceSelectedResult()
        assert "成功" in msg or "替换" in msg
        # 验证文件内容已替换
        new_content = src_file.read_text(encoding="utf-8")
        assert "secret" not in new_content
        assert "[REDACTED]" in new_content
        # 验证备份已创建
        assert backup_dir.exists()
        bak_files = list(backup_dir.rglob("*.bak"))
        assert len(bak_files) == 1


class TestDetailPropertiesExtended:
    """测试 detailFileSize / detailIsArchiveEntry / detailHitsModel 扩展字段。"""

    def test_detail_file_size_empty_when_no_selection(self, controller_with_results: ScanController) -> None:
        """未选中时 detailFileSize 为空。"""
        assert controller_with_results.detailFileSize == ""

    def test_detail_file_size_formatted(self, controller_with_results: ScanController) -> None:
        """选中结果时 detailFileSize 返回格式化大小。"""
        controller_with_results.setSelectedResultIndex(0)
        size_str = controller_with_results.detailFileSize
        assert size_str != ""
        assert "1024" in size_str or "1.0" in size_str

    def test_detail_is_archive_entry_false_for_normal(self, controller_with_results: ScanController) -> None:
        """普通文件 detailIsArchiveEntry 为 False。"""
        controller_with_results.setSelectedResultIndex(0)
        assert controller_with_results.detailIsArchiveEntry is False

    def test_detail_is_archive_entry_true_for_archive(self, controller: ScanController) -> None:
        """压缩包内部条目 detailIsArchiveEntry 为 True。"""
        result = _make_scan_result(Path("/tmp/archive.zip!inner.txt"), archive_path=Path("/tmp/archive.zip"))
        controller._result_model.set_results((result,))
        controller.setSelectedResultIndex(0)
        assert controller.detailIsArchiveEntry is True

    def test_detail_hits_model_includes_extended_fields(self, controller_with_results: ScanController) -> None:
        """detailHitsModel 应包含 matchText/matchCount/target/description 字段。"""
        controller_with_results.setSelectedResultIndex(0)
        hits_model = controller_with_results.detailHitsModel
        assert len(hits_model) == 1
        hit = hits_model[0]
        assert hit["matchText"] == "secret"
        assert hit["matchCount"] == 1
        assert hit["target"] == "content"
        assert hit["description"] == "敏感密钥检测"


class TestMoveSelectedToStaging:
    """测试 moveSelectedToStaging Slot。"""

    def test_returns_message_when_no_selection(self, controller_with_results: ScanController) -> None:
        """未选中结果时返回提示。"""
        msg = controller_with_results.moveSelectedToStaging()
        assert "未选中" in msg

    def test_returns_message_when_archive_entry(self, controller: ScanController, tmp_path: Path) -> None:
        """iter-133：压缩包内部条目移至暂存操作的是压缩包文件本身。"""
        controller._ruleset = _build_replace_ruleset()
        # 创建真实的压缩包文件，使复制操作成功
        archive_file = tmp_path / "archive.zip"
        archive_file.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        result = _make_scan_result(
            Path(str(archive_file) + "!inner.txt"),
            archive_path=archive_file,
        )
        controller._result_model.set_results((result,))
        controller._last_report = _make_scan_report((result,))
        controller.setSelectedResultIndex(0)
        msg = controller.moveSelectedToStaging()
        # 应成功移至暂存（操作的是压缩包文件本身）
        assert "已移至暂存" in msg
        assert "archive.zip" in msg

    def test_move_success_copies_and_marks_skipped(
        self,
        controller: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功移至暂存应复制文件到 quarantine 目录并标记跳过。"""
        controller._ruleset = _build_replace_ruleset()

        # 准备扫描根目录与源文件
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        src_file = scan_root / "secret.txt"
        src_file.write_text("this is a secret value", encoding="utf-8")

        result = ScanResult(
            path=src_file,
            size=src_file.stat().st_size,
            hits=(
                RuleHit(
                    rule_name="可替换规则",
                    severity=Severity.CRITICAL,
                    detail="命中 secret",
                    match_text="secret",
                    match_texts=("secret",),
                    match_count=1,
                    target="content",
                ),
            ),
        )
        controller._result_model.set_results((result,))
        controller._last_report = ScanReport(
            root=scan_root,
            results=(result,),
            stats=ScanStats(total_files=1, scanned_files=1, matched_files=1),
        )
        controller.setSelectedResultIndex(0)

        # 暂存区重定向到 tmp_path
        staging_dir = tmp_path / "staging"
        monkeypatch.setattr(controller._config, "staging_dir", str(staging_dir))

        msg = controller.moveSelectedToStaging()
        assert "已移至暂存" in msg

        # 验证文件已复制到 quarantine 目录，保留相对路径
        dest = staging_dir / "quarantine" / "secret.txt"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "this is a secret value"

        # 验证已标记为跳过
        assert controller._skip_store.contains(str(src_file))

    def test_move_failure_returns_error_message(
        self,
        controller: ScanController,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """源文件不存在时移至暂存应返回失败消息。"""
        controller._ruleset = _build_replace_ruleset()

        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        # 源文件不存在
        src_file = scan_root / "nonexistent.txt"

        result = ScanResult(
            path=src_file,
            size=100,
            hits=(
                RuleHit(
                    rule_name="可替换规则",
                    severity=Severity.CRITICAL,
                    detail="命中 secret",
                    match_text="secret",
                    match_texts=("secret",),
                    match_count=1,
                    target="content",
                ),
            ),
        )
        controller._result_model.set_results((result,))
        controller._last_report = ScanReport(
            root=scan_root,
            results=(result,),
            stats=ScanStats(total_files=1, scanned_files=1, matched_files=1),
        )
        controller.setSelectedResultIndex(0)

        staging_dir = tmp_path / "staging"
        monkeypatch.setattr(controller._config, "staging_dir", str(staging_dir))

        msg = controller.moveSelectedToStaging()
        assert "失败" in msg
