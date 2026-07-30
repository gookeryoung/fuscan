"""``ScanController`` 子模块纯函数单元测试。

覆盖 iter-107 抽离的纯逻辑：

- :mod:`fuscan.gui.controllers._scan_roots`：扫描根构建
- :mod:`fuscan.gui.controllers._task_overrides`：任务级配置覆盖
- :mod:`fuscan.gui.controllers._result_detail`：结果详情展示与文件操作
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 设置离屏平台，避免无显示器环境报错
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.gui

try:
    from fuscan.config import Config
    from fuscan.gui.controllers._batch_actions import (
        mark_as_false_positive,
        replace_all_filtered_results,
        undo_last_batch_replace,
        undo_selected_replace,
    )
    from fuscan.gui.controllers._history import build_history_entry
    from fuscan.gui.controllers._history_view import (
        build_scan_comparison_json,
        build_workspace_history_json,
    )
    from fuscan.gui.controllers._manifest import (
        invalidate_manifest,
        load_manifest,
        save_manifest,
    )
    from fuscan.gui.controllers._persistence import (
        coerce_float,
        coerce_int,
        coerce_str,
        coerce_str_tuple,
        deserialize_task_overrides,
        serialize_task_overrides,
    )
    from fuscan.gui.controllers._restore import (
        delete_cached_results,
        save_cached_results,
    )
    from fuscan.gui.controllers._result_detail import (
        build_detail_hits_model,
        can_replace_result,
        move_to_staging,
        replace_selected,
    )
    from fuscan.gui.controllers._scan_roots import build_scan_roots, can_build_roots
    from fuscan.gui.controllers._task_overrides import (
        effective_ignore_dirs,
        effective_max_depth,
        effective_max_file_size,
        effective_max_workers,
        effective_scan_archives,
    )
    from fuscan.history import STATUS_CANCELLED, STATUS_COMPLETED, ScanHistoryEntry
    from fuscan.processing.skip_store import SkipStore
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner import ScanReport, ScanStats
    from fuscan.scanner.manifest import FileFingerprint, IncrementalManifest
    from fuscan.scanner.result import RuleHit, ScanResult

    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

if not PYSIDE_AVAILABLE:
    pytest.skip("PySide 未安装，跳过子模块测试", allow_module_level=True)


# ----------------------------- 测试夹具与工具 -----------------------------


def _make_config() -> Config:
    """构造默认配置实例。"""
    return Config()


def _make_ruleset_with_replace() -> RuleSet:
    """构造包含 replace 规则的规则集。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="可替换规则",
                severity=Severity.WARNING,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                replace=True,
                replace_with="***",
            ),
        ),
    )


def _make_ruleset_no_replace() -> RuleSet:
    """构造不含 replace 规则的规则集。"""
    return RuleSet(
        version="1.0",
        rules=(
            Rule(
                name="只检测规则",
                severity=Severity.CRITICAL,
                match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
            ),
        ),
    )


def _make_result(
    path: Path,
    hits: tuple[RuleHit, ...] = (),
    archive_path: Path | None = None,
) -> ScanResult:
    """构造 ScanResult 测试实例。"""
    return ScanResult(path=path, size=100, hits=hits, archive_path=archive_path)


# ----------------------------- _scan_roots -----------------------------


class TestCanBuildRoots:
    """测试 can_build_roots 判断扫描根可构建性。"""

    def test_full_mode_always_true(self) -> None:
        """full 模式无条件返回 True。"""
        assert can_build_roots(0, "", "") is True

    def test_drive_mode_with_selection(self) -> None:
        """drive 模式有选中盘符返回 True。"""
        assert can_build_roots(1, "C:", "") is True

    def test_drive_mode_no_selection(self) -> None:
        """drive 模式无选中盘符返回 False。"""
        assert can_build_roots(1, "", "") is False

    def test_folder_mode_with_root(self) -> None:
        """folder 模式有根路径返回 True。"""
        assert can_build_roots(2, "", "/tmp") is True

    def test_folder_mode_no_root(self) -> None:
        """folder 模式无根路径返回 False。"""
        assert can_build_roots(2, "", "") is False


class TestBuildScanRoots:
    """测试 build_scan_roots 构建扫描根路径列表。"""

    def test_full_mode_calls_list_drives(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """full 模式应调用 list_drives。"""
        monkeypatch.setattr(
            "fuscan.scanner.walker.list_drives",
            lambda include_network=False: [Path("C:"), Path("D:")],
        )
        roots = build_scan_roots(0, "", "", _make_config())
        assert roots == [Path("C:"), Path("D:")]

    def test_drive_mode_returns_selected(self) -> None:
        """drive 模式应返回选中盘符列表。"""
        roots = build_scan_roots(1, "C:", "", _make_config())
        assert roots == [Path("C:")]

    def test_drive_mode_no_selection_returns_empty(self) -> None:
        """drive 模式无选中盘符应返回空列表。"""
        roots = build_scan_roots(1, "", "", _make_config())
        assert roots == []

    def test_folder_mode_returns_root(self) -> None:
        """folder 模式应返回根路径列表。"""
        roots = build_scan_roots(2, "", "/tmp/scan", _make_config())
        assert roots == [Path("/tmp/scan")]

    def test_folder_mode_empty_returns_empty(self) -> None:
        """folder 模式空路径应返回空列表。"""
        roots = build_scan_roots(2, "", "", _make_config())
        assert roots == []


# ----------------------------- _task_overrides -----------------------------


class TestTaskOverrides:
    """测试任务级覆盖纯函数。"""

    def test_effective_scan_archives_uses_override(self) -> None:
        """覆盖值类型正确时优先使用覆盖值。"""
        overrides: dict[str, object] = {"scan_archives": False}
        assert effective_scan_archives(overrides, _make_config()) is False

    def test_effective_scan_archives_falls_back_to_config(self) -> None:
        """无覆盖或类型不符时回退到全局配置。"""
        config = _make_config()
        config.scan_archives = True
        assert effective_scan_archives({}, config) is True
        # 类型不符（int 而非 bool）也应回退
        assert effective_scan_archives({"scan_archives": 1}, config) is True

    def test_effective_max_workers_uses_override(self) -> None:
        """max_workers 覆盖值优先。"""
        overrides: dict[str, object] = {"max_workers": 16}
        assert effective_max_workers(overrides, _make_config()) == 16

    def test_effective_max_workers_falls_back(self) -> None:
        """max_workers 无覆盖回退到全局配置。"""
        config = _make_config()
        config.max_workers = 5
        assert effective_max_workers({}, config) == 5

    def test_effective_max_file_size_uses_override(self) -> None:
        """max_file_size 覆盖值优先。"""
        overrides: dict[str, object] = {"max_file_size": 1024}
        assert effective_max_file_size(overrides, _make_config()) == 1024

    def test_effective_max_depth_positive_override(self) -> None:
        """正数 max_depth 覆盖值原样返回。"""
        overrides: dict[str, object] = {"max_depth": 10}
        assert effective_max_depth(overrides, _make_config()) == 10

    def test_effective_max_depth_zero_normalized_to_none(self) -> None:
        """max_depth=0 归一化为 None（无限深度）。"""
        overrides: dict[str, object] = {"max_depth": 0}
        assert effective_max_depth(overrides, _make_config()) is None

    def test_effective_max_depth_falls_back(self) -> None:
        """max_depth 无覆盖回退到全局配置。"""
        config = _make_config()
        config.max_depth = 20
        assert effective_max_depth({}, config) == 20

    def test_effective_ignore_dirs_tuple_override(self) -> None:
        """ignore_dirs tuple 覆盖值优先。"""
        custom = (".git", "node_modules")
        overrides: dict[str, object] = {"ignore_dirs": custom}
        assert effective_ignore_dirs(overrides, _make_config()) == custom

    def test_effective_ignore_dirs_falls_back(self) -> None:
        """ignore_dirs 无覆盖回退到全局配置（list 转 tuple）。"""
        config = _make_config()
        config.ignore_dirs = [".git", "node_modules"]
        result = effective_ignore_dirs({}, config)
        assert result == (".git", "node_modules")
        assert isinstance(result, tuple)


# ----------------------------- _result_detail -----------------------------


class TestBuildDetailHitsModel:
    """测试 build_detail_hits_model 构造命中详情 dict。"""

    def test_none_result_returns_empty(self) -> None:
        """None 结果返回空列表。"""
        assert build_detail_hits_model(None) == []

    def test_empty_hits_returns_empty(self, tmp_path: Path) -> None:
        """无命中的结果返回空列表。"""
        result = _make_result(tmp_path / "a.txt")
        assert build_detail_hits_model(result) == []

    def test_hits_dict_structure(self, tmp_path: Path) -> None:
        """命中详情 dict 应包含所有字段。"""
        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="line 5: password=123",
            match_text="password",
            match_count=1,
            target="content",
            match_description="检测密码字段",
        )
        result = _make_result(tmp_path / "a.txt", hits=(hit,))
        model = build_detail_hits_model(result)
        assert len(model) == 1
        entry = model[0]
        assert entry["ruleName"] == "敏感内容"
        assert entry["severityText"] == "严重"
        assert entry["severityColor"] == "#D73A49"
        assert entry["context"] == "line 5: password=123"
        assert entry["matchText"] == "password"
        assert entry["matchCount"] == 1
        assert entry["target"] == "content"
        assert entry["description"] == "检测密码字段"


class TestCanReplaceResult:
    """测试 can_replace_result 判断结果可替换性。"""

    def test_none_result_returns_false(self) -> None:
        """None 结果不可替换。"""
        assert can_replace_result(None, _make_ruleset_with_replace()) is False

    def test_none_ruleset_with_match_texts_returns_true(self, tmp_path: Path) -> None:
        """iter-124：规则集未加载但命中含 match_texts 仍可替换（用户自定义模式）。"""
        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(tmp_path / "a.txt", hits=(hit,))
        assert can_replace_result(result, None) is True

    def test_no_hits_returns_false(self, tmp_path: Path) -> None:
        """无命中的结果不可替换。"""
        result = _make_result(tmp_path / "a.txt")
        assert can_replace_result(result, _make_ruleset_with_replace()) is False

    def test_archive_entry_returns_false(
        self,
        tmp_path: Path,
    ) -> None:
        """压缩包内部条目不可替换。"""
        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=tmp_path / "a.zip",
        )
        assert can_replace_result(result, _make_ruleset_with_replace()) is False

    def test_hits_without_match_texts_returns_false(self, tmp_path: Path) -> None:
        """iter-124：命中规则无 match_texts 不可替换（无文本可替换）。"""
        hit = RuleHit(rule_name="只检测规则", severity=Severity.CRITICAL, detail="匹配")
        result = _make_result(tmp_path / "a.txt", hits=(hit,))
        assert can_replace_result(result, _make_ruleset_no_replace()) is False

    def test_replace_rule_with_match_texts_returns_true(self, tmp_path: Path) -> None:
        """iter-124：命中含 match_texts 的规则可替换（用户自定义模式不要求 replace=True）。"""
        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(tmp_path / "a.txt", hits=(hit,))
        assert can_replace_result(result, _make_ruleset_with_replace()) is True


class TestReplaceSelected:
    """测试 replace_selected 替换执行。"""

    def test_none_result_returns_message(self) -> None:
        """未选中结果返回提示消息。"""
        assert (
            replace_selected(
                result=None,
                ruleset=_make_ruleset_with_replace(),
                backup_dir_str="",
                backup_preserve_relative=False,
                last_report_root=None,
            )
            == "未选中结果"
        )

    def test_none_ruleset_returns_message(self, tmp_path: Path) -> None:
        """规则集未加载返回提示消息。"""
        result = _make_result(tmp_path / "a.txt")
        assert (
            replace_selected(
                result=result,
                ruleset=None,
                backup_dir_str="",
                backup_preserve_relative=False,
                last_report_root=None,
            )
            == "规则集未加载"
        )

    def test_archive_entry_returns_message(self, tmp_path: Path) -> None:
        """压缩包内部条目返回提示消息。"""
        hit = RuleHit(rule_name="可替换规则", severity=Severity.WARNING, detail="匹配")
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=tmp_path / "a.zip",
        )
        assert (
            replace_selected(
                result=result,
                ruleset=_make_ruleset_with_replace(),
                backup_dir_str="",
                backup_preserve_relative=False,
                last_report_root=None,
            )
            == "压缩包内部条目不支持替换"
        )

    def test_replace_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """成功替换返回成功消息。"""
        # 准备源文件
        src = tmp_path / "a.txt"
        src.write_text("password=123", encoding="utf-8")

        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit,))

        msg = replace_selected(
            result=result,
            ruleset=_make_ruleset_with_replace(),
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
        )
        assert "替换成功" in msg
        # 源文件应被替换
        assert src.read_text(encoding="utf-8") == "***=123"


class TestReplaceSelectedOverride:
    """iter-124：测试 replace_selected 的 override_replace_with 参数（用户自定义替换文本）。"""

    def test_override_replaces_all_match_texts(
        self,
        tmp_path: Path,
    ) -> None:
        """override_replace_with 非空时不检查 replace 标志，对所有 match_texts 替换。"""
        # 准备源文件
        src = tmp_path / "a.txt"
        src.write_text("password=123\nsecret=abc\n", encoding="utf-8")

        # 命中规则无 replace=True，但 override 模式不要求
        hit = RuleHit(
            rule_name="只检测规则",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password", "secret"),
        )
        result = _make_result(src, hits=(hit,))

        msg = replace_selected(
            result=result,
            ruleset=None,  # override 模式 ruleset 可为 None
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="[REDACTED]",
        )
        assert "替换成功" in msg
        # 两个 match_texts 都应被替换为 [REDACTED]
        assert src.read_text(encoding="utf-8") == "[REDACTED]=123\n[REDACTED]=abc\n"

    def test_override_with_default_ellipsis(
        self,
        tmp_path: Path,
    ) -> None:
        """默认替换文本 ... 替换命中内容。"""
        src = tmp_path / "a.txt"
        src.write_text("password=123", encoding="utf-8")

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit,))

        msg = replace_selected(
            result=result,
            ruleset=None,
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="...",
        )
        assert "替换成功" in msg
        assert src.read_text(encoding="utf-8") == "...=123"

    def test_override_skips_hits_without_match_texts(
        self,
        tmp_path: Path,
    ) -> None:
        """override 模式下无 match_texts 的命中被跳过。"""
        src = tmp_path / "a.txt"
        src.write_text("password=123", encoding="utf-8")

        # 第一条命中无 match_texts，第二条有
        hit1 = RuleHit(rule_name="无文本命中", severity=Severity.INFO, detail="文件名匹配")
        hit2 = RuleHit(
            rule_name="内容命中",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit1, hit2))

        msg = replace_selected(
            result=result,
            ruleset=None,
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="***",
        )
        assert "替换成功" in msg
        assert src.read_text(encoding="utf-8") == "***=123"

    def test_override_no_match_texts_returns_no_replace_message(
        self,
        tmp_path: Path,
    ) -> None:
        """override 模式下所有命中均无 match_texts → 返回 NO_REPLACE_RULES 消息。"""
        src = tmp_path / "a.txt"
        src.write_text("content", encoding="utf-8")

        hit = RuleHit(rule_name="无文本命中", severity=Severity.INFO, detail="文件名匹配")
        result = _make_result(src, hits=(hit,))

        msg = replace_selected(
            result=result,
            ruleset=None,
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="...",
        )
        assert "无匹配文本可替换" in msg

    def test_override_empty_string_falls_back_to_rule_driven(
        self,
        tmp_path: Path,
    ) -> None:
        """override_replace_with="" 等价于 None，走规则驱动模式。"""
        src = tmp_path / "a.txt"
        src.write_text("password=123", encoding="utf-8")

        hit = RuleHit(
            rule_name="可替换规则",
            severity=Severity.WARNING,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit,))

        # 传空字符串等价于不传，走规则驱动模式
        msg = replace_selected(
            result=result,
            ruleset=_make_ruleset_with_replace(),
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="",
        )
        assert "替换成功" in msg
        # 规则驱动模式用 replace_with="***"
        assert src.read_text(encoding="utf-8") == "***=123"

    def test_override_creates_backup_file(
        self,
        tmp_path: Path,
    ) -> None:
        """override 模式成功替换后应创建 .bak 备份文件。"""
        src = tmp_path / "a.txt"
        src.write_text("password=123", encoding="utf-8")
        backup_dir = tmp_path / "backup"

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="匹配",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit,))

        replace_selected(
            result=result,
            ruleset=None,
            backup_dir_str=str(backup_dir),
            backup_preserve_relative=False,
            last_report_root=tmp_path,
            override_replace_with="***",
        )
        # 备份文件应存在且保留原始内容
        backup_path = backup_dir / "a.txt.bak"
        assert backup_path.exists()
        assert backup_path.read_text(encoding="utf-8") == "password=123"


class TestExtractContext:
    """iter-124：测试 build_detail_hits_model 实时读取文件上下文。"""

    def test_context_from_file_content(self, tmp_path: Path) -> None:
        """命中详情应从文件内容提取上下文（前后各 2 行，匹配行用 >>> 标记）。"""
        src = tmp_path / "a.txt"
        src.write_text(
            "line0\nline1\nline2\npassword=secret\nline4\nline5\nline6\n",
            encoding="utf-8",
        )

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="line 3: password=secret",
            match_text="password",
            match_texts=("password",),
        )
        result = _make_result(src, hits=(hit,))

        model = build_detail_hits_model(result)
        assert len(model) == 1
        context: str = str(model[0]["context"])
        # 应包含前后各 2 行 + 匹配行（共 5 行）：line1, line2, password=secret, line4, line5
        assert ">>> password=secret" in context
        assert "    line1" in context
        assert "    line2" in context
        assert "    line4" in context
        assert "    line5" in context
        # 应不包含 line0 和 line6（超出 _CONTEXT_LINES=2 范围）
        assert "line0" not in context
        assert "line6" not in context

    def test_context_falls_back_to_detail_when_file_missing(
        self,
        tmp_path: Path,
    ) -> None:
        """文件不存在时 context 回退到 hit.detail。"""
        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="line 3: password=secret",
            match_text="password",
        )
        result = _make_result(tmp_path / "missing.txt", hits=(hit,))

        model = build_detail_hits_model(result)
        assert len(model) == 1
        # 文件不存在 → context 回退到 hit.detail
        assert model[0]["context"] == "line 3: password=secret"

    def test_context_falls_back_to_detail_for_archive_entry(
        self,
        tmp_path: Path,
    ) -> None:
        """压缩包内部条目无法读取文件，context 用 hit.detail 兜底。"""
        # 创建压缩包文件（仅用于测试 archive_path 标记，不实际读取）
        archive = tmp_path / "a.zip"
        archive.write_bytes(b"PK\x03\x04")  # 最小 ZIP 头

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="内部条目命中: password",
            match_text="password",
        )
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=archive,
        )

        model = build_detail_hits_model(result)
        assert len(model) == 1
        # 压缩包条目 → context 用 hit.detail
        assert model[0]["context"] == "内部条目命中: password"

    def test_context_falls_back_when_file_too_large(
        self,
        tmp_path: Path,
    ) -> None:
        """文件超过 _MAX_CONTEXT_FILE_SIZE (1MB) 时跳过上下文提取。"""
        src = tmp_path / "large.txt"
        # 写入 1.5MB 内容（超过 1MB 限制）
        src.write_text("a" * (1024 * 1024 + 512), encoding="utf-8")

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="detail fallback",
            match_text="a",
        )
        result = _make_result(src, hits=(hit,))

        model = build_detail_hits_model(result)
        assert len(model) == 1
        # 文件过大 → context 回退到 hit.detail
        assert model[0]["context"] == "detail fallback"

    def test_context_falls_back_when_match_text_not_in_file(
        self,
        tmp_path: Path,
    ) -> None:
        """match_text 不在文件中时 context 回退到 hit.detail。"""
        src = tmp_path / "a.txt"
        src.write_text("hello world\n", encoding="utf-8")

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="detail fallback",
            match_text="password",  # 文件中不存在
        )
        result = _make_result(src, hits=(hit,))

        model = build_detail_hits_model(result)
        assert len(model) == 1
        # match_text 不在文件中 → context 回退到 hit.detail
        assert model[0]["context"] == "detail fallback"

    def test_context_skipped_for_non_text_file(
        self,
        tmp_path: Path,
    ) -> None:
        """非文本文件扩展名 → 跳过上下文提取。"""
        src = tmp_path / "a.pdf"
        src.write_bytes(b"%PDF-1.4 password")

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="detail fallback",
            match_text="password",
        )
        result = _make_result(src, hits=(hit,))

        model = build_detail_hits_model(result)
        assert len(model) == 1
        # PDF 不是文本文件 → context 回退到 hit.detail
        assert model[0]["context"] == "detail fallback"

    def test_context_at_file_start(self, tmp_path: Path) -> None:
        """匹配行在文件开头时上下文仅包含后续行。"""
        src = tmp_path / "a.txt"
        src.write_text("password=123\nline2\nline3\n", encoding="utf-8")

        hit = RuleHit(
            rule_name="敏感内容",
            severity=Severity.CRITICAL,
            detail="detail",
            match_text="password",
        )
        result = _make_result(src, hits=(hit,))

        model = build_detail_hits_model(result)
        context: str = str(model[0]["context"])
        # 匹配行在开头，start=max(0, -2)=0，end=min(3, 3)=3
        assert ">>> password=123" in context
        assert "    line2" in context
        assert "    line3" in context


class TestMoveToStaging:
    """测试 move_to_staging 移至暂存。"""

    def test_none_result_returns_message(self) -> None:
        """未选中结果返回提示消息。"""
        skip_store = SkipStore()
        assert (
            move_to_staging(
                result=None,
                staging_dir_str="",
                last_report_root=None,
                skip_store=skip_store,
            )
            == "未选中结果"
        )

    def test_archive_entry_moves_archive_file(self, tmp_path: Path) -> None:
        """iter-133：压缩包内部条目移至暂存压缩包文件本身。

        压缩包内含敏感文件时隔离整个压缩包——内部条目无法直接复制，
        移至暂存 archive_path 并标记跳过整个压缩包。
        """
        # 准备压缩包文件
        archive = tmp_path / "scan_root" / "a.zip"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fake zip content")

        hit = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="匹配")
        result = _make_result(
            archive.parent / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=archive,
        )

        staging_dir = tmp_path / "staging"
        skip_store = SkipStore()
        msg = move_to_staging(
            result=result,
            staging_dir_str=str(staging_dir),
            last_report_root=tmp_path / "scan_root",
            skip_store=skip_store,
        )
        assert "已移至暂存" in msg
        # 压缩包文件应被复制到隔离目录
        dest = staging_dir / "quarantine" / "a.zip"
        assert dest.exists()
        assert dest.read_bytes() == b"fake zip content"
        # 应标记压缩包路径为跳过（而非内部条目路径）
        assert str(archive) in skip_store.paths()

    def test_move_success(
        self,
        tmp_path: Path,
    ) -> None:
        """成功移至暂存应复制文件并标记跳过。"""
        # 准备源文件
        src = tmp_path / "scan_root" / "a.txt"
        src.parent.mkdir(parents=True)
        src.write_text("secret", encoding="utf-8")

        hit = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="匹配")
        result = _make_result(src, hits=(hit,))

        staging_dir = tmp_path / "staging"
        skip_store = SkipStore()
        msg = move_to_staging(
            result=result,
            staging_dir_str=str(staging_dir),
            last_report_root=tmp_path / "scan_root",
            skip_store=skip_store,
        )
        assert "已移至暂存" in msg
        # 文件应被复制到隔离目录
        dest = staging_dir / "quarantine" / "a.txt"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "secret"
        # 应标记跳过
        assert str(src) in skip_store.paths()

    def test_move_failed_returns_error(
        self,
        tmp_path: Path,
    ) -> None:
        """复制失败应返回错误消息。"""
        # 源文件不存在
        src = tmp_path / "missing.txt"
        hit = RuleHit(rule_name="敏感内容", severity=Severity.CRITICAL, detail="匹配")
        result = _make_result(src, hits=(hit,))

        skip_store = SkipStore()
        msg = move_to_staging(
            result=result,
            staging_dir_str=str(tmp_path / "staging"),
            last_report_root=tmp_path,
            skip_store=skip_store,
        )
        assert "移至暂存失败" in msg


# ----------------------------- _persistence（iter-113 coerce_* 辅助函数） -----------------------------


class TestCoerceStr:
    """coerce_str 安全字符串转换。"""

    def test_str_value_passthrough(self) -> None:
        assert coerce_str("hello") == "hello"

    def test_none_returns_default(self) -> None:
        assert coerce_str(None) == ""
        assert coerce_str(None, default="fallback") == "fallback"

    def test_int_converted_to_str(self) -> None:
        assert coerce_int(42) == 42  # sanity check coerce_int 行为
        assert coerce_str(42) == "42"

    def test_list_converted_to_str(self) -> None:
        assert coerce_str([1, 2]) == "[1, 2]"


class TestCoerceInt:
    """coerce_int 安全整数转换。"""

    def test_int_value_passthrough(self) -> None:
        assert coerce_int(42) == 42

    def test_none_returns_default(self) -> None:
        assert coerce_int(None) == 0
        assert coerce_int(None, default=-1) == -1

    def test_bool_returns_default(self) -> None:
        """bool 是 int 子类，但 coerce_int 视为非数字返回 default。"""
        assert coerce_int(True) == 0
        assert coerce_int(False, default=99) == 99

    def test_numeric_str_parsed(self) -> None:
        assert coerce_int("123") == 123

    def test_invalid_str_returns_default(self) -> None:
        assert coerce_int("abc") == 0
        assert coerce_int("abc", default=-5) == -5

    def test_float_returns_default(self) -> None:
        assert coerce_int(3.14) == 0

    def test_list_returns_default(self) -> None:
        assert coerce_int([1, 2]) == 0


class TestCoerceFloat:
    """coerce_float 安全浮点数转换。"""

    def test_float_value_passthrough(self) -> None:
        assert coerce_float(3.14) == 3.14

    def test_int_converted_to_float(self) -> None:
        assert coerce_float(42) == 42.0

    def test_none_returns_default(self) -> None:
        assert coerce_float(None) == 0.0
        assert coerce_float(None, default=-1.5) == -1.5

    def test_bool_returns_default(self) -> None:
        """bool 是 int 子类，但 coerce_float 视为非数字返回 default。"""
        assert coerce_float(True) == 0.0
        assert coerce_float(False, default=99.5) == 99.5

    def test_numeric_str_parsed(self) -> None:
        assert coerce_float("3.14") == 3.14

    def test_invalid_str_returns_default(self) -> None:
        assert coerce_float("abc") == 0.0
        assert coerce_float("abc", default=-5.5) == -5.5

    def test_list_returns_default(self) -> None:
        assert coerce_float([1.0, 2.0]) == 0.0


class TestCoerceStrTuple:
    """coerce_str_tuple 安全字符串元组转换。"""

    def test_list_of_str(self) -> None:
        assert coerce_str_tuple(["a", "b"]) == ("a", "b")

    def test_tuple_of_str(self) -> None:
        assert coerce_str_tuple(("x", "y")) == ("x", "y")

    def test_mixed_types_converted_to_str(self) -> None:
        assert coerce_str_tuple([1, 2, 3]) == ("1", "2", "3")

    def test_none_returns_empty(self) -> None:
        assert coerce_str_tuple(None) == ()

    def test_str_returns_empty(self) -> None:
        """单个 str 不是 list/tuple → 返回空元组。"""
        assert coerce_str_tuple("abc") == ()

    def test_int_returns_empty(self) -> None:
        assert coerce_str_tuple(42) == ()

    def test_empty_list_returns_empty(self) -> None:
        assert coerce_str_tuple([]) == ()


class TestSerializeTaskOverridesRoundtrip:
    """iter-113：serialize/deserialize task_overrides 往返一致性。"""

    def test_roundtrip_basic(self) -> None:
        """基本字段往返保持一致（ignore_dirs tuple <-> list）。"""
        original: dict[str, object] = {
            "scan_archives": True,
            "max_workers": 5,
            "max_file_size": 1024,
            "max_depth": 10,
            "ignore_dirs": ("/path/a", "/path/b"),
        }
        serialized = serialize_task_overrides(original)
        # ignore_dirs 应转为 list
        assert serialized["ignore_dirs"] == ["/path/a", "/path/b"]
        # 反序列化后应回到 tuple
        restored = deserialize_task_overrides(serialized)
        assert restored["ignore_dirs"] == ("/path/a", "/path/b")
        assert restored["max_workers"] == 5
        assert restored["scan_archives"] is True

    def test_roundtrip_drops_unknown_keys(self) -> None:
        """非白名单字段在序列化时被剔除。"""
        original: dict[str, object] = {"max_workers": 3, "unknown_field": "should be dropped"}
        serialized = serialize_task_overrides(original)
        assert "unknown_field" not in serialized
        assert serialized == {"max_workers": 3}


class TestDeserializeTaskOverridesFaultTolerance:
    """iter-113：deserialize_task_overrides 容错路径。"""

    def test_non_dict_input_returns_empty(self) -> None:
        """非 dict 输入返回空 dict。"""
        assert deserialize_task_overrides(None) == {}  # type: ignore[arg-type]
        assert deserialize_task_overrides("not a dict") == {}  # type: ignore[arg-type]
        assert deserialize_task_overrides([1, 2]) == {}  # type: ignore[arg-type]

    def test_unknown_key_skipped(self) -> None:
        """未知字段被跳过（不写入输出）。"""
        raw: dict[str, object] = {"unknown_field": "value", "max_workers": 5}
        result = deserialize_task_overrides(raw)
        assert "unknown_field" not in result
        assert result == {"max_workers": 5}

    def test_ignore_dirs_wrong_element_type_skipped(self) -> None:
        """ignore_dirs 含非 str 元素 → 跳过该字段。"""
        raw: dict[str, object] = {"ignore_dirs": [1, 2, 3]}
        result = deserialize_task_overrides(raw)
        assert "ignore_dirs" not in result

    def test_ignore_dirs_not_list_skipped(self) -> None:
        """ignore_dirs 非 list → 跳过该字段。"""
        raw: dict[str, object] = {"ignore_dirs": "not a list"}
        result = deserialize_task_overrides(raw)
        assert "ignore_dirs" not in result

    def test_int_field_wrong_type_skipped(self) -> None:
        """int 字段传入 str → 跳过该字段。"""
        raw: dict[str, object] = {"max_workers": "not a number"}
        result = deserialize_task_overrides(raw)
        assert "max_workers" not in result

    def test_bool_field_wrong_type_skipped(self) -> None:
        """bool 字段传入 str → 跳过该字段。"""
        raw: dict[str, object] = {"scan_archives": "yes"}
        result = deserialize_task_overrides(raw)
        assert "scan_archives" not in result


class TestSavePersistedWorkspacesFaultTolerance:
    """iter-113：save_persisted_workspaces 容错路径。"""

    def test_save_oserror_logged_not_raised(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError 时记录 warning 不抛异常。"""
        from fuscan.gui.controllers._persistence import save_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"

        def _raise_oserror(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "mkdir", _raise_oserror)
        # 不应抛异常
        save_persisted_workspaces(persist_file, {"version": 1, "workspaces": []}, tmp_path)
        # 文件不应被创建
        assert not persist_file.exists()


class TestLoadPersistedWorkspacesFaultTolerance:
    """iter-113：load_persisted_workspaces 容错路径。"""

    def test_file_not_exist_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在 → 返回空列表。"""
        from fuscan.gui.controllers._persistence import load_persisted_workspaces

        result = load_persisted_workspaces(tmp_path / "missing.json")
        assert result == []

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """JSON 解析失败 → 返回空列表。"""
        from fuscan.gui.controllers._persistence import load_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"
        persist_file.write_text("not a valid json {", encoding="utf-8")
        result = load_persisted_workspaces(persist_file)
        assert result == []

    def test_version_mismatch_returns_empty(self, tmp_path: Path) -> None:
        """版本不匹配 → 返回空列表。"""
        from fuscan.gui.controllers._persistence import PERSIST_VERSION, load_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"
        persist_file.write_text(
            f'{{"version": {PERSIST_VERSION + 100}, "workspaces": []}}',
            encoding="utf-8",
        )
        result = load_persisted_workspaces(persist_file)
        assert result == []

    def test_payload_not_dict_returns_empty(self, tmp_path: Path) -> None:
        """payload 非 dict → 返回空列表。"""
        from fuscan.gui.controllers._persistence import load_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"
        persist_file.write_text('"just a string"', encoding="utf-8")
        result = load_persisted_workspaces(persist_file)
        assert result == []

    def test_workspaces_not_list_returns_empty(self, tmp_path: Path) -> None:
        """workspaces 字段非 list → 返回空列表。"""
        from fuscan.gui.controllers._persistence import load_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"
        persist_file.write_text('{"version": 1, "workspaces": "not a list"}', encoding="utf-8")
        result = load_persisted_workspaces(persist_file)
        assert result == []

    def test_workspaces_filter_non_dict_items(self, tmp_path: Path) -> None:
        """workspaces 含非 dict 元素 → 仅保留 dict 项。"""
        from fuscan.gui.controllers._persistence import load_persisted_workspaces

        persist_file = tmp_path / "workspaces.json"
        persist_file.write_text(
            '{"version": 1, "workspaces": [{"id": "ws1"}, "not a dict", 42, {"id": "ws2"}]}',
            encoding="utf-8",
        )
        result = load_persisted_workspaces(persist_file)
        assert len(result) == 2
        assert result[0] == {"id": "ws1"}
        assert result[1] == {"id": "ws2"}


# ----------------------------- _batch_actions -----------------------------


def _make_replaceable_result(src: Path, rule_name: str = "可替换规则") -> ScanResult:
    """构造含可替换命中规则的结果（写入源文件含 password 关键词）。"""
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("password=abc\n", encoding="utf-8")
    hit = RuleHit(
        rule_name=rule_name,
        severity=Severity.WARNING,
        detail="匹配",
        match_texts=("password",),
    )
    return ScanResult(path=src, size=src.stat().st_size, hits=(hit,))


def _make_scan_report_for_history(
    results: tuple[ScanResult, ...] = (),
    cancelled: bool = False,
) -> ScanReport:
    """构造测试用 ScanReport（用于 build_history_entry 测试）。"""
    return ScanReport(
        root=Path("/tmp"),
        results=results,
        stats=ScanStats(
            total_files=10,
            scanned_files=10,
            matched_files=len(results),
            skipped_files=0,
            errors=0,
            duration_seconds=1.0,
            total_matches=len(results),
        ),
        cancelled=cancelled,
    )


class TestReplaceAllFilteredResults:
    """测试 replace_all_filtered_results 批量替换纯函数。"""

    def test_no_ruleset_no_override_returns_message_none(self) -> None:
        """规则集未加载且无自定义替换 → 返回提示与 None（不更新撤销状态）。"""
        msg, last_batch = replace_all_filtered_results(
            filtered=(),
            ruleset=None,
            backup_dir=Path("/tmp/backup"),
            scan_root=Path("/tmp"),
            backup_preserve_relative=False,
            override_replace_with=None,
        )
        assert msg == "规则集未加载"
        assert last_batch is None

    def test_empty_filtered_returns_message_none(self) -> None:
        """有规则集但无待替换结果 → 返回提示与 None。"""
        msg, last_batch = replace_all_filtered_results(
            filtered=(),
            ruleset=_make_ruleset_with_replace(),
            backup_dir=Path("/tmp/backup"),
            scan_root=Path("/tmp"),
            backup_preserve_relative=False,
            override_replace_with=None,
        )
        assert msg == "无待替换的结果"
        assert last_batch is None

    def test_success_returns_message_and_backup_paths(
        self,
        tmp_path: Path,
    ) -> None:
        """成功批量替换返回聚合消息与非空撤销配对。"""
        src1 = tmp_path / "scan" / "a.txt"
        src2 = tmp_path / "scan" / "b.txt"
        results = (
            _make_replaceable_result(src1),
            _make_replaceable_result(src2),
        )
        msg, last_batch = replace_all_filtered_results(
            filtered=results,
            ruleset=_make_ruleset_with_replace(),
            backup_dir=tmp_path / "backup",
            scan_root=tmp_path / "scan",
            backup_preserve_relative=False,
            override_replace_with=None,
        )
        assert "成功 2/2" in msg
        assert last_batch is not None
        assert len(last_batch) == 2
        # 源文件应被替换
        assert src1.read_text(encoding="utf-8") == "***=abc\n"
        assert src2.read_text(encoding="utf-8") == "***=abc\n"

    def test_override_mode_works_without_ruleset(
        self,
        tmp_path: Path,
    ) -> None:
        """override_replace_with 非空时 ruleset 可为 None。"""
        src = tmp_path / "scan" / "a.txt"
        results = (_make_replaceable_result(src, rule_name="只检测规则"),)
        msg, last_batch = replace_all_filtered_results(
            filtered=results,
            ruleset=None,
            backup_dir=tmp_path / "backup",
            scan_root=tmp_path / "scan",
            backup_preserve_relative=False,
            override_replace_with="[REDACTED]",
        )
        assert "替换成功" in msg or "成功 1/1" in msg
        assert last_batch is not None
        assert len(last_batch) == 1
        assert src.read_text(encoding="utf-8") == "[REDACTED]=abc\n"


class TestUndoLastBatchReplace:
    """测试 undo_last_batch_replace 批量撤销纯函数。"""

    def test_empty_paths_returns_message(self) -> None:
        """无可撤销记录 → 返回提示消息。"""
        assert undo_last_batch_replace(()) == "无可撤销的批量替换"

    def test_success_restores_files(
        self,
        tmp_path: Path,
    ) -> None:
        """成功从 .bak 恢复所有文件。"""
        src1 = tmp_path / "scan" / "a.txt"
        src2 = tmp_path / "scan" / "b.txt"
        results = (
            _make_replaceable_result(src1),
            _make_replaceable_result(src2),
        )
        # 先批量替换生成备份
        _msg, last_batch = replace_all_filtered_results(
            filtered=results,
            ruleset=_make_ruleset_with_replace(),
            backup_dir=tmp_path / "backup",
            scan_root=tmp_path / "scan",
            backup_preserve_relative=False,
            override_replace_with=None,
        )
        assert last_batch is not None
        assert src1.read_text(encoding="utf-8") == "***=abc\n"

        # 撤销
        summary = undo_last_batch_replace(last_batch)
        assert "恢复 2" in summary
        assert "失败" not in summary
        # 源文件应恢复
        assert src1.read_text(encoding="utf-8") == "password=abc\n"
        assert src2.read_text(encoding="utf-8") == "password=abc\n"

    def test_partial_failure_includes_failed_count(
        self,
        tmp_path: Path,
    ) -> None:
        """备份文件丢失 → 部分失败消息。"""
        src1 = tmp_path / "scan" / "a.txt"
        src2 = tmp_path / "scan" / "b.txt"
        results = (
            _make_replaceable_result(src1),
            _make_replaceable_result(src2),
        )
        _msg, last_batch = replace_all_filtered_results(
            filtered=results,
            ruleset=_make_ruleset_with_replace(),
            backup_dir=tmp_path / "backup",
            scan_root=tmp_path / "scan",
            backup_preserve_relative=False,
            override_replace_with=None,
        )
        assert last_batch is not None
        # 删除第一个备份，模拟撤销失败
        last_batch[0][1].unlink()

        summary = undo_last_batch_replace(last_batch)
        assert "恢复 1" in summary
        assert "1 个失败" in summary


class TestUndoSelectedReplace:
    """测试 undo_selected_replace 单文件撤销纯函数。"""

    def test_none_result_returns_message(self) -> None:
        """未选中结果 → 返回提示消息。"""
        assert (
            undo_selected_replace(
                result=None,
                backup_dir=Path("/tmp/backup"),
                scan_root=Path("/tmp"),
                backup_preserve_relative=False,
            )
            == "未选中结果"
        )

    def test_success_restores_selected(
        self,
        tmp_path: Path,
    ) -> None:
        """成功从 .bak 恢复当前选中结果。"""
        src = tmp_path / "scan" / "a.txt"
        result = _make_replaceable_result(src)
        # 先单文件替换生成备份（preserve_relative=True 使备份路径确定，
        # 避免 _resolve_backup_path 的同名冲突序号分支导致两次解析路径不一致）
        replace_selected(
            result=result,
            ruleset=_make_ruleset_with_replace(),
            backup_dir_str=str(tmp_path / "backup"),
            backup_preserve_relative=True,
            last_report_root=tmp_path / "scan",
        )
        assert src.read_text(encoding="utf-8") == "***=abc\n"

        # 撤销当前选中
        msg = undo_selected_replace(
            result=result,
            backup_dir=tmp_path / "backup",
            scan_root=tmp_path / "scan",
            backup_preserve_relative=True,
        )
        assert msg.startswith("已从备份恢复")
        assert src.read_text(encoding="utf-8") == "password=abc\n"


class TestMarkAsFalsePositive:
    """测试 mark_as_false_positive 误报标记校验纯函数。"""

    def test_none_result_returns_error(self) -> None:
        """未选中结果 → 返回空字段与错误消息。"""
        path_glob, rule_name, error_msg = mark_as_false_positive(result=None, rule_filter="")
        assert path_glob == ""
        assert rule_name == ""
        assert error_msg == "未选中结果"

    def test_archive_result_returns_error(self, tmp_path: Path) -> None:
        """压缩包内部条目 → 返回错误消息（路径含 ! 无法 glob）。"""
        hit = RuleHit(rule_name="r", severity=Severity.WARNING, detail="匹配")
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=tmp_path / "a.zip",
        )
        _path, _rule, error_msg = mark_as_false_positive(result=result, rule_filter="")
        assert error_msg == "压缩包内部条目不支持标记误报"

    def test_empty_rule_filter_defaults_star(self, tmp_path: Path) -> None:
        """rule_filter 为空 → rule_name 默认 *（全部规则标记误报）。"""
        src = tmp_path / "scan" / "a.txt"
        result = _make_replaceable_result(src)
        path_glob, rule_name, error_msg = mark_as_false_positive(result=result, rule_filter="")
        assert error_msg is None
        assert rule_name == "*"
        assert path_glob == str(src)

    def test_specific_rule_filter(self, tmp_path: Path) -> None:
        """rule_filter 指定规则名 → rule_name 为该规则名（strip 后）。"""
        src = tmp_path / "scan" / "a.txt"
        result = _make_replaceable_result(src)
        path_glob, rule_name, error_msg = mark_as_false_positive(result=result, rule_filter="  敏感规则  ")
        assert error_msg is None
        assert rule_name == "敏感规则"
        assert path_glob == str(src)


# ----------------------------- _manifest -----------------------------


def _make_manifest(root: Path | None = None) -> IncrementalManifest:
    """构造测试用 IncrementalManifest。"""
    return IncrementalManifest(
        root=root or Path("/tmp"),
        fingerprints={
            "a.txt": FileFingerprint(mtime=1000.0, size=10),
            "b.txt": FileFingerprint(mtime=2000.0, size=20),
        },
    )


class TestLoadManifest:
    """测试 load_manifest 清单加载纯函数。"""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """文件不存在 → 返回 None。"""
        assert load_manifest("nonexistent", tmp_path / "manifests") is None

    def test_loads_instance(self, tmp_path: Path) -> None:
        """文件存在 → 返回 IncrementalManifest 实例。"""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir(parents=True)
        manifest = _make_manifest(root=tmp_path)
        (manifests_dir / "ws-ok.json").write_text(manifest.to_json(), encoding="utf-8")

        loaded = load_manifest("ws-ok", manifests_dir)
        assert loaded is not None
        assert isinstance(loaded, IncrementalManifest)
        assert loaded.fingerprints["a.txt"].mtime == 1000.0
        assert loaded.fingerprints["a.txt"].size == 10
        assert loaded.fingerprints["b.txt"].size == 20

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        """JSON 解析失败 → 返回 None（不抛异常）。"""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir(parents=True)
        (manifests_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

        assert load_manifest("bad", manifests_dir) is None


class TestSaveManifest:
    """测试 save_manifest 清单持久化纯函数。"""

    def test_writes_file_roundtrip(self, tmp_path: Path) -> None:
        """保存后可用 load_manifest 读回相同指纹。"""
        manifests_dir = tmp_path / "manifests"
        manifest = _make_manifest(root=tmp_path)

        save_manifest("ws-1", manifest, manifests_dir)
        assert (manifests_dir / "ws-1.json").exists()

        loaded = load_manifest("ws-1", manifests_dir)
        assert loaded is not None
        assert loaded.fingerprints["a.txt"].mtime == 1000.0
        assert loaded.fingerprints["b.txt"].size == 20

    def test_creates_missing_dir(self, tmp_path: Path) -> None:
        """manifests_dir 不存在时自动创建。"""
        manifests_dir = tmp_path / "nested" / "manifests"
        assert not manifests_dir.exists()

        save_manifest("ws-2", _make_manifest(), manifests_dir)
        assert manifests_dir.exists()
        assert (manifests_dir / "ws-2.json").exists()


class TestInvalidateManifest:
    """测试 invalidate_manifest 清单删除纯函数。"""

    def test_missing_file_noop(self, tmp_path: Path) -> None:
        """文件不存在 → 不抛异常。"""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir(parents=True)
        # 不应抛异常
        invalidate_manifest("nonexistent", manifests_dir)

    def test_deletes_file(self, tmp_path: Path) -> None:
        """文件存在 → 删除。"""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir(parents=True)
        manifest_file = manifests_dir / "ws-del.json"
        manifest_file.write_text("{}", encoding="utf-8")
        assert manifest_file.exists()

        invalidate_manifest("ws-del", manifests_dir)
        assert not manifest_file.exists()

    def test_unlink_failure_logs_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """unlink 抛 OSError → 记录警告日志，不抛异常。"""
        manifests_dir = tmp_path / "manifests"
        manifests_dir.mkdir(parents=True)
        manifest_file = manifests_dir / "ws-fail.json"
        manifest_file.write_text("{}", encoding="utf-8")

        def _raise_oserror(_path: Path) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", _raise_oserror)

        with caplog.at_level("WARNING", logger="fuscan.gui.controllers._manifest"):
            invalidate_manifest("ws-fail", manifests_dir)
        assert any("增量清单清除失败" in r.message for r in caplog.records)


# ----------------------------- _history -----------------------------


class TestBuildHistoryEntry:
    """测试 build_history_entry 历史条目构建纯函数。"""

    def test_none_report_returns_none(self) -> None:
        """report 为 None → 返回 None。"""
        assert build_history_entry(None, "ws-1", "工作区A", "已完成") is None

    def test_completed_report_returns_entry(self, tmp_path: Path) -> None:
        """未取消的 report → STATUS_COMPLETED 条目。"""
        result = _make_replaceable_result(tmp_path / "secret.txt")
        report = _make_scan_report_for_history(results=(result,), cancelled=False)

        entry = build_history_entry(report, "ws-2", "工作区B", "已完成")
        assert entry is not None
        assert isinstance(entry, ScanHistoryEntry)
        assert entry.workspace_id == "ws-2"
        assert entry.workspace_name == "工作区B"
        assert entry.status == STATUS_COMPLETED
        assert entry.total_files == 10
        assert entry.matched_files == 1
        assert entry.summary == "已完成"
        # hit_paths 为排序后的路径元组
        assert entry.hit_paths == (str(tmp_path / "secret.txt"),)
        # rule_names 为排序后的规则名元组
        assert entry.rule_names == ("可替换规则",)

    def test_cancelled_report_returns_cancelled_status(self) -> None:
        """取消的 report → STATUS_CANCELLED 条目。"""
        report = _make_scan_report_for_history(results=(), cancelled=True)

        entry = build_history_entry(report, "ws-3", "工作区C", "已取消")
        assert entry is not None
        assert entry.status == STATUS_CANCELLED
        assert entry.hit_paths == ()
        assert entry.rule_names == ()
        assert entry.summary == "已取消"

    def test_hit_paths_sorted(self, tmp_path: Path) -> None:
        """多个命中路径应排序后归档。"""
        src_b = tmp_path / "b.txt"
        src_a = tmp_path / "a.txt"
        results = (
            _make_replaceable_result(src_b, rule_name="规则B"),
            _make_replaceable_result(src_a, rule_name="规则A"),
        )
        report = _make_scan_report_for_history(results=results)

        entry = build_history_entry(report, "ws-4", "工作区D", "已完成")
        assert entry is not None
        assert entry.hit_paths == (str(tmp_path / "a.txt"), str(tmp_path / "b.txt"))
        assert entry.rule_names == ("规则A", "规则B")


# ----------------------------- _restore -----------------------------


def _make_report_with_hits(
    root: Path,
    hits_count: int = 1,
) -> ScanReport:
    """构造测试用 ScanReport（含指定数量的命中结果）。"""
    results = tuple(
        ScanResult(
            path=root / f"hit_{i}.txt",
            size=10,
            hits=(
                RuleHit(
                    rule_name="敏感规则",
                    severity=Severity.WARNING,
                    detail="匹配",
                    match_texts=("password",),
                ),
            ),
        )
        for i in range(hits_count)
    )
    return ScanReport(
        root=root,
        results=results,
        stats=ScanStats(
            total_files=hits_count,
            scanned_files=hits_count,
            matched_files=hits_count,
            total_matches=hits_count,
        ),
    )


class TestSaveCachedResults:
    """测试 save_cached_results 缓存持久化纯函数。"""

    def test_none_report_no_op(
        self,
        tmp_path: Path,
    ) -> None:
        """report 为 None → 直接返回，不创建目录或文件。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        save_cached_results(report=None, cache_file=cache_file, cached_results_dir=tmp_path / "results")
        assert not cache_file.exists()
        # 不应触发 mkdir
        assert not (tmp_path / "results").exists()

    def test_save_creates_dir_and_file(
        self,
        tmp_path: Path,
    ) -> None:
        """有 hits 的 report → 自动创建目录并写入 JSON。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        report = _make_report_with_hits(root=tmp_path, hits_count=2)

        save_cached_results(
            report=report,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        assert cache_file.exists()
        # 文件内容应为合法 JSON
        import json as _json

        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        assert len(data["hits"]) == 2

    def test_save_empty_hits_skips_when_cache_has_hits(
        self,
        tmp_path: Path,
    ) -> None:
        """iter-135：本次无命中但缓存已有非空结果 → 跳过覆盖。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        # 先写一份有命中的缓存
        report_with_hits = _make_report_with_hits(root=tmp_path, hits_count=3)
        save_cached_results(
            report=report_with_hits,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        original_bytes = cache_file.read_bytes()

        # 再保存空结果
        empty_report = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=10, scanned_files=10),
        )
        save_cached_results(
            report=empty_report,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        # 文件应保持不变
        assert cache_file.read_bytes() == original_bytes

    def test_save_empty_hits_overwrites_when_cache_empty(
        self,
        tmp_path: Path,
    ) -> None:
        """本次无命中且缓存也无命中 → 正常覆盖。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        # 先写一份无命中的缓存
        empty_report_1 = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=5, scanned_files=5),
        )
        save_cached_results(
            report=empty_report_1,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        assert cache_file.exists()

        # 再保存另一份无命中结果（root 不同）
        empty_report_2 = ScanReport(
            root=tmp_path / "another",
            results=(),
            stats=ScanStats(total_files=10, scanned_files=10),
        )
        save_cached_results(
            report=empty_report_2,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        # 应被覆盖（root 不同）
        import json as _json

        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["root"] == str(tmp_path / "another")

    def test_save_overwrites_when_cache_corrupted(
        self,
        tmp_path: Path,
    ) -> None:
        """缓存文件损坏 → 正常覆盖（异常被吞掉）。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("{not a valid json", encoding="utf-8")

        report = _make_report_with_hits(root=tmp_path, hits_count=1)
        save_cached_results(
            report=report,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        # 文件应被覆盖为合法 JSON
        import json as _json

        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        assert len(data["hits"]) == 1

    def test_save_empty_hits_overwrites_when_cache_corrupted(
        self,
        tmp_path: Path,
    ) -> None:
        """iter-135：本次无命中且缓存文件损坏 → 异常被吞掉，正常覆盖。

        此测试覆盖 ``except (OSError, ValueError): pass`` 分支：缓存读取失败时
        不应阻塞写入流程，按正常覆盖逻辑写入新的空结果。
        """
        cache_file = tmp_path / "results" / "ws-1.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("{not a valid json", encoding="utf-8")

        empty_report = ScanReport(
            root=tmp_path,
            results=(),
            stats=ScanStats(total_files=5, scanned_files=5),
        )
        save_cached_results(
            report=empty_report,
            cache_file=cache_file,
            cached_results_dir=tmp_path / "results",
        )
        # 文件应被覆盖为合法 JSON（空结果）
        import json as _json

        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["root"] == str(tmp_path)
        assert data["hits"] == []

    def test_save_failure_logs_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """write_bytes 抛 OSError → 记录警告日志，不抛异常。"""
        cache_file = tmp_path / "results" / "ws-1.json"
        report = _make_report_with_hits(root=tmp_path, hits_count=1)

        def _raise_oserror(_self: Path, _data: bytes) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", _raise_oserror)

        with caplog.at_level("WARNING", logger="fuscan.gui.controllers._restore"):
            save_cached_results(
                report=report,
                cache_file=cache_file,
                cached_results_dir=tmp_path / "results",
            )
        assert any("扫描结果缓存失败" in r.message for r in caplog.records)


class TestDeleteCachedResults:
    """测试 delete_cached_results 缓存清理纯函数。"""

    def test_missing_file_no_op(self, tmp_path: Path) -> None:
        """文件不存在 → 不抛异常。"""
        delete_cached_results(tmp_path / "nonexistent.json")

    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        """文件存在 → 删除。"""
        cache_file = tmp_path / "ws-del.json"
        cache_file.write_text("{}", encoding="utf-8")
        assert cache_file.exists()

        delete_cached_results(cache_file)
        assert not cache_file.exists()

    def test_unlink_failure_logs_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """unlink 抛 OSError → 记录警告日志，不抛异常。"""
        cache_file = tmp_path / "ws-fail.json"
        cache_file.write_text("{}", encoding="utf-8")

        def _raise_oserror(_path: Path) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", _raise_oserror)

        with caplog.at_level("WARNING", logger="fuscan.gui.controllers._restore"):
            delete_cached_results(cache_file)
        assert any("缓存结果删除失败" in r.message for r in caplog.records)


# ----------------------------- _history_view -----------------------------


def _make_history_entry(
    scan_id: str = "s1",
    workspace_name: str = "任务A",
    finished_at: str = "2026-07-27T10:00:00Z",
    matched_files: int = 3,
    hit_paths: tuple[str, ...] = ("/a", "/b", "/c"),
    rule_names: tuple[str, ...] = ("rule1",),
    status: str = STATUS_COMPLETED,
    summary: str = "命中 3 个文件",
) -> ScanHistoryEntry:
    """构造测试用 ScanHistoryEntry。"""
    return ScanHistoryEntry(
        scan_id=scan_id,
        workspace_id="ws-1",
        workspace_name=workspace_name,
        finished_at=finished_at,
        matched_files=matched_files,
        hit_paths=hit_paths,
        rule_names=rule_names,
        status=status,
        summary=summary,
    )


class TestBuildWorkspaceHistoryJson:
    """测试 build_workspace_history_json 历史列表 JSON 构造纯函数。"""

    def test_empty_entries_returns_empty_array(self) -> None:
        """空历史 → 返回 ``"[]"``。"""
        assert build_workspace_history_json(()) == "[]"

    def test_single_entry_payload_fields(self) -> None:
        """单条历史 → 字段完整、类型正确。"""
        import json as _json

        entry = _make_history_entry()
        result = build_workspace_history_json((entry,))
        payload = _json.loads(result)
        assert len(payload) == 1
        item = payload[0]
        assert item["scan_id"] == "s1"
        assert item["workspace_name"] == "任务A"
        assert item["started_at"] == entry.started_at
        assert item["finished_at"] == "2026-07-27T10:00:00Z"
        assert item["status"] == STATUS_COMPLETED
        assert item["total_files"] == 0
        assert item["scanned_files"] == 0
        assert item["matched_files"] == 3
        assert item["skipped_files"] == 0
        assert item["error_count"] == 0
        assert item["duration_seconds"] == 0.0
        assert item["rule_names"] == ["rule1"]
        assert item["summary"] == "命中 3 个文件"

    def test_multiple_entries_preserve_order(self) -> None:
        """多条历史 → 按输入顺序输出（调用方负责倒序）。"""
        import json as _json

        e1 = _make_history_entry(scan_id="s1", finished_at="2026-07-27T10:00:00Z")
        e2 = _make_history_entry(scan_id="s2", finished_at="2026-07-27T11:00:00Z")
        payload = _json.loads(build_workspace_history_json((e2, e1)))
        assert len(payload) == 2
        assert payload[0]["scan_id"] == "s2"
        assert payload[1]["scan_id"] == "s1"

    def test_duration_seconds_rounded_to_two_decimals(self) -> None:
        """duration_seconds 应保留两位小数。"""
        import json as _json

        entry = ScanHistoryEntry(
            scan_id="s1",
            workspace_id="ws-1",
            duration_seconds=1.23456,
            summary="",
        )
        payload = _json.loads(build_workspace_history_json((entry,)))
        assert payload[0]["duration_seconds"] == 1.23

    def test_rule_names_tuple_to_list(self) -> None:
        """rule_names 元组应转为列表。"""
        import json as _json

        entry = _make_history_entry(rule_names=("ruleB", "ruleA"))
        payload = _json.loads(build_workspace_history_json((entry,)))
        assert payload[0]["rule_names"] == ["ruleB", "ruleA"]

    def test_chinese_summary_not_ascii_escaped(self) -> None:
        r"""中文 summary 不应被 \uXXXX 转义。"""
        entry = _make_history_entry(summary="命中 3 个文件")
        result = build_workspace_history_json((entry,))
        assert "命中 3 个文件" in result
        assert "\\u" not in result


class TestBuildScanComparisonJson:
    """测试 build_scan_comparison_json 对比 JSON 构造纯函数。"""

    def test_empty_entries_returns_empty_object(self) -> None:
        """无历史 → 返回 ``"{}"``。"""
        assert build_scan_comparison_json(()) == "{}"

    def test_single_entry_treats_as_first_scan(self) -> None:
        """仅一条历史 → previous 为 None，trend 为 ``首次``。"""
        import json as _json

        entry = _make_history_entry(scan_id="s1", matched_files=3)
        result = build_scan_comparison_json((entry,))
        payload = _json.loads(result)
        assert payload["current"]["scan_id"] == "s1"
        assert payload["previous"] is None
        assert payload["trend"] == "首次"
        assert payload["matched_delta"] == 3
        assert payload["new_hits_count"] == 3
        assert payload["resolved_hits_count"] == 0
        assert payload["persistent_hits_count"] == 0
        assert "首次扫描" in payload["summary"]

    def test_two_entries_returns_delta(
        self,
    ) -> None:
        """两条历史 → 计算新增/已解决/持续命中。"""
        import json as _json

        prev = _make_history_entry(
            scan_id="s1",
            finished_at="2026-07-27T10:00:00Z",
            matched_files=3,
            hit_paths=("/a", "/b", "/c"),
            rule_names=("rule1",),
        )
        curr = _make_history_entry(
            scan_id="s2",
            finished_at="2026-07-27T11:00:00Z",
            matched_files=2,
            hit_paths=("/a", "/d"),
            rule_names=("rule1", "rule2"),
        )
        payload = _json.loads(build_scan_comparison_json((curr, prev)))
        assert payload["current"]["scan_id"] == "s2"
        assert payload["previous"]["scan_id"] == "s1"
        assert payload["matched_delta"] == -1  # 2 - 3
        assert payload["trend"] == "改善"
        assert payload["new_hits_count"] == 1  # /d
        assert payload["resolved_hits_count"] == 2  # /b, /c
        assert payload["persistent_hits_count"] == 1  # /a
        assert "/d" in payload["new_hits"]
        assert set(payload["resolved_hits"]) == {"/b", "/c"}
        assert "rule2" in payload["new_rules"]
        assert payload["dropped_rules"] == []

    def test_new_hits_truncated_to_50(
        self,
    ) -> None:
        """new_hits 超过 50 条应截断。"""
        prev_hit_paths: tuple[str, ...] = ()
        curr_hit_paths = tuple(f"/file_{i}" for i in range(60))
        prev = _make_history_entry(scan_id="s1", hit_paths=prev_hit_paths)
        curr = _make_history_entry(scan_id="s2", hit_paths=curr_hit_paths, matched_files=60)
        import json as _json

        payload = _json.loads(build_scan_comparison_json((curr, prev)))
        assert payload["new_hits_count"] == 60
        assert len(payload["new_hits"]) == 50

    def test_chinese_summary_not_ascii_escaped(
        self,
    ) -> None:
        r"""中文 summary 不应被 \uXXXX 转义。"""
        prev = _make_history_entry(scan_id="s1", hit_paths=("/a",))
        curr = _make_history_entry(scan_id="s2", hit_paths=("/a", "/b"))
        result = build_scan_comparison_json((curr, prev))
        # summary 中包含中文（"本次命中" / "上次命中"）
        assert "本次命中" in result
        assert "\\u" not in result
