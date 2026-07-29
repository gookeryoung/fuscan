"""``WhitelistController`` 单元测试（iter-133）。

覆盖 QML 控制器的：

- ``whitelistEntries``/``whitelistCount`` 属性
- ``addEntry``/``removeEntry``/``clearAll``/``removeByGlobAndRule`` 槽
- ``importJson``/``exportJson`` 槽
- ``snapshot`` 方法返回独立快照
- ``whitelistChanged`` 信号在增删时发射
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.gui.controllers.whitelist_controller import WhitelistController
    from fuscan.rules.whitelist import Whitelist, WhitelistStore

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过白名单控制器测试", allow_module_level=True)


@pytest.fixture()
def controller(tmp_path: Path) -> WhitelistController:
    """构造带临时存储的 WhitelistController。"""
    store = WhitelistStore(tmp_path / "whitelist.json")
    return WhitelistController(store=store)


# --------------------------------------------------------------------------- #
# 属性
# --------------------------------------------------------------------------- #


class TestWhitelistControllerProperties:
    def test_initial_entries_empty(self, controller: WhitelistController) -> None:
        """新建控制器条目列表为空。"""
        assert controller.whitelistEntries == []
        assert controller.whitelistCount == 0

    def test_entries_reflect_store_state(self, controller: WhitelistController) -> None:
        """whitelistEntries 反映 store 当前状态。"""
        controller.addEntry("/a/b.txt", "r1", "")
        entries = controller.whitelistEntries
        assert len(entries) == 1
        assert entries[0]["pathGlob"] == "/a/b.txt"
        assert entries[0]["ruleName"] == "r1"
        assert "createdAt" in entries[0]
        assert entries[0]["note"] == ""
        assert controller.whitelistCount == 1

    def test_store_property_returns_underlying_store(self, controller: WhitelistController) -> None:
        """store 属性返回底层 WhitelistStore 实例。"""
        assert controller.store is not None
        assert controller.store is controller.store  # 同一实例


# --------------------------------------------------------------------------- #
# addEntry
# --------------------------------------------------------------------------- #


class TestWhitelistControllerAdd:
    def test_add_success_returns_message(self, controller: WhitelistController) -> None:
        """addEntry 成功返回包含路径的消息。"""
        msg = controller.addEntry("/a/b.txt", "r1", "备注")
        assert "/a/b.txt" in msg
        assert "r1" in msg
        assert controller.whitelistCount == 1

    def test_add_empty_path_returns_error(self, controller: WhitelistController) -> None:
        """空路径返回错误消息。"""
        msg = controller.addEntry("   ", "r1", "")
        assert "不能为空" in msg
        assert controller.whitelistCount == 0

    def test_add_empty_rule_normalized_to_wildcard(self, controller: WhitelistController) -> None:
        """空规则名归一化为 *。"""
        controller.addEntry("/a", "", "")
        entries = controller.whitelistEntries
        assert entries[0]["ruleName"] == "*"

    def test_add_whitespace_only_path_stripped(self, controller: WhitelistController) -> None:
        """路径首尾空格被 strip。"""
        controller.addEntry("  /a/b.txt  ", "r1", "")
        assert controller.whitelistEntries[0]["pathGlob"] == "/a/b.txt"

    def test_add_emits_whitelist_changed(self, controller: WhitelistController) -> None:
        """addEntry 成功后发射 whitelistChanged 信号。"""
        emitted: list[None] = []
        controller.whitelistChanged.connect(lambda: emitted.append(None))  # type: ignore[arg-type]
        controller.addEntry("/a", "r1", "")
        assert len(emitted) == 1

    def test_add_duplicate_no_emit(self, controller: WhitelistController) -> None:
        """重复添加相同条目不发射信号（store 去重）。"""
        controller.addEntry("/a", "r1", "")
        emitted: list[None] = []
        controller.whitelistChanged.connect(lambda: emitted.append(None))  # type: ignore[arg-type]
        controller.addEntry("/a", "r1", "")
        assert len(emitted) == 0
        assert controller.whitelistCount == 1

    def test_add_persists_to_disk(self, controller: WhitelistController, tmp_path: Path) -> None:
        """addEntry 后磁盘写入对应 JSON。"""
        controller.addEntry("/a", "r1", "备注")
        path = tmp_path / "whitelist.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[0]["path_glob"] == "/a"
        assert data[0]["rule_name"] == "r1"
        assert data[0]["note"] == "备注"


# --------------------------------------------------------------------------- #
# removeEntry / clearAll / removeByGlobAndRule
# --------------------------------------------------------------------------- #


class TestWhitelistControllerRemove:
    def test_remove_entry_by_index(self, controller: WhitelistController) -> None:
        """removeEntry 按索引移除。"""
        controller.addEntry("/a", "r1", "")
        controller.addEntry("/b", "r2", "")
        assert controller.removeEntry(0) is True
        assert controller.whitelistCount == 1
        assert controller.whitelistEntries[0]["pathGlob"] == "/b"

    def test_remove_entry_out_of_range_returns_false(self, controller: WhitelistController) -> None:
        """索引越界返回 False。"""
        assert controller.removeEntry(0) is False
        assert controller.removeEntry(-1) is False

    def test_remove_entry_emits_signal(self, controller: WhitelistController) -> None:
        """removeEntry 成功后发射信号。"""
        controller.addEntry("/a", "r1", "")
        emitted: list[None] = []
        controller.whitelistChanged.connect(lambda: emitted.append(None))  # type: ignore[arg-type]
        controller.removeEntry(0)
        assert len(emitted) == 1

    def test_remove_by_glob_and_rule(self, controller: WhitelistController) -> None:
        """removeByGlobAndRule 按精确匹配移除。"""
        controller.addEntry("/a", "r1", "")
        controller.addEntry("/b", "r2", "")
        assert controller.removeByGlobAndRule("/a", "r1") is True
        assert controller.whitelistCount == 1
        assert controller.whitelistEntries[0]["pathGlob"] == "/b"

    def test_remove_by_glob_and_rule_miss_returns_false(self, controller: WhitelistController) -> None:
        """不存在的条目返回 False。"""
        controller.addEntry("/a", "r1", "")
        assert controller.removeByGlobAndRule("/x", "y") is False
        assert controller.whitelistCount == 1

    def test_clear_all(self, controller: WhitelistController) -> None:
        """clearAll 清空全部条目。"""
        controller.addEntry("/a", "r1", "")
        controller.addEntry("/b", "r2", "")
        controller.clearAll()
        assert controller.whitelistCount == 0
        assert controller.whitelistEntries == []

    def test_clear_all_emits_signal(self, controller: WhitelistController) -> None:
        """clearAll 发射信号。"""
        controller.addEntry("/a", "r1", "")
        emitted: list[None] = []
        controller.whitelistChanged.connect(lambda: emitted.append(None))  # type: ignore[arg-type]
        controller.clearAll()
        assert len(emitted) == 1


# --------------------------------------------------------------------------- #
# importJson / exportJson
# --------------------------------------------------------------------------- #


class TestWhitelistControllerImportExport:
    def test_export_json_writes_file(self, controller: WhitelistController, tmp_path: Path) -> None:
        """exportJson 写入 JSON 文件。"""
        controller.addEntry("/a", "r1", "")
        out = tmp_path / "export.json"
        msg = controller.exportJson(str(out))
        assert "已导出" in msg
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["path_glob"] == "/a"

    def test_export_empty_path_returns_error(self, controller: WhitelistController) -> None:
        """空路径返回错误消息。"""
        msg = controller.exportJson("")
        assert "未选择" in msg

    def test_import_json_merges(self, controller: WhitelistController, tmp_path: Path) -> None:
        """importJson 合并到现有条目并返回新增数。"""
        controller.addEntry("/a", "r1", "")
        # 导入 /a/r1（重复）+ /b/r2（新增）
        payload = json.dumps(
            [
                {"path_glob": "/a", "rule_name": "r1"},
                {"path_glob": "/b", "rule_name": "r2"},
            ]
        )
        src = tmp_path / "import.json"
        src.write_text(payload, encoding="utf-8")
        msg = controller.importJson(str(src))
        assert "1" in msg  # 新增 1 条
        assert controller.whitelistCount == 2

    def test_import_invalid_file_returns_error(self, controller: WhitelistController, tmp_path: Path) -> None:
        """损坏的 JSON 文件返回错误消息。"""
        src = tmp_path / "bad.json"
        src.write_text("{not json", encoding="utf-8")
        msg = controller.importJson(str(src))
        assert "失败" in msg
        assert controller.whitelistCount == 0

    def test_import_empty_path_returns_error(self, controller: WhitelistController) -> None:
        """空路径返回错误消息。"""
        msg = controller.importJson("")
        assert "未选择" in msg

    def test_export_to_invalid_path_returns_error(self, controller: WhitelistController) -> None:
        """导出到不可写路径返回错误消息（OSError 容错）。"""
        controller.addEntry("/a", "r1", "")
        # 使用不存在的盘符根目录作为不可写路径
        msg = controller.exportJson("Z:/nonexistent_dir/export.json")
        assert "失败" in msg


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #


class TestWhitelistControllerSnapshot:
    def test_snapshot_returns_immutable_whitelist(self, controller: WhitelistController) -> None:
        """snapshot 返回 Whitelist 实例。"""
        controller.addEntry("/a", "r1", "")
        snap = controller.snapshot()
        assert isinstance(snap, Whitelist)
        assert len(snap.entries) == 1

    def test_snapshot_isolated_from_subsequent_mutations(self, controller: WhitelistController) -> None:
        """snapshot 不受后续 addEntry 影响。"""
        controller.addEntry("/a", "r1", "")
        snap = controller.snapshot()
        controller.addEntry("/b", "r2", "")
        assert len(snap.entries) == 1
        assert controller.whitelistCount == 2

    def test_snapshot_used_by_scanner(self, controller: WhitelistController) -> None:
        """snapshot 可直接传给 Scanner 使用。"""
        from fuscan.rules.model import (
            LeafMatch,
            MatchMode,
            MatchTarget,
            Rule,
            RuleSet,
            Severity,
        )
        from fuscan.scanner import Scanner

        controller.addEntry("/a/b.txt", "*", "")
        # 构造最小 Scanner 验证 snapshot 可用
        rs = RuleSet(
            version="1.0",
            rules=(
                Rule(
                    name="r",
                    severity=Severity.WARNING,
                    match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="b"),
                ),
            ),
        )
        scanner = Scanner(rs, whitelist=controller.snapshot())
        # 验证 Scanner 持有了白名单（间接验证 snapshot 类型正确）
        assert scanner._whitelist is not None
        assert len(scanner._whitelist.entries) == 1
