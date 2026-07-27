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
    from fuscan.rules.model import (
        LeafMatch,
        MatchMode,
        MatchTarget,
        Rule,
        RuleSet,
        Severity,
    )
    from fuscan.scanner.result import RuleHit, ScanResult
    from fuscan.skip_store import SkipStore

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

    def test_none_ruleset_returns_false(self, tmp_path: Path) -> None:
        """规则集未加载不可替换。"""
        result = _make_result(tmp_path / "a.txt")
        assert can_replace_result(result, None) is False

    def test_archive_entry_returns_false(
        self,
        tmp_path: Path,
    ) -> None:
        """压缩包内部条目不可替换。"""
        hit = RuleHit(rule_name="可替换规则", severity=Severity.WARNING, detail="匹配")
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=tmp_path / "a.zip",
        )
        assert can_replace_result(result, _make_ruleset_with_replace()) is False

    def test_no_replace_rule_returns_false(self, tmp_path: Path) -> None:
        """命中规则中无 replace=True 的规则不可替换。"""
        hit = RuleHit(rule_name="只检测规则", severity=Severity.CRITICAL, detail="匹配")
        result = _make_result(tmp_path / "a.txt", hits=(hit,))
        assert can_replace_result(result, _make_ruleset_no_replace()) is False

    def test_replace_rule_returns_true(self, tmp_path: Path) -> None:
        """命中 replace=True 规则可替换。"""
        hit = RuleHit(rule_name="可替换规则", severity=Severity.WARNING, detail="匹配")
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

    def test_archive_entry_returns_message(self, tmp_path: Path) -> None:
        """压缩包内部条目返回提示消息。"""
        hit = RuleHit(rule_name="可替换规则", severity=Severity.WARNING, detail="匹配")
        result = _make_result(
            tmp_path / "a.zip!inner.txt",
            hits=(hit,),
            archive_path=tmp_path / "a.zip",
        )
        skip_store = SkipStore()
        assert (
            move_to_staging(
                result=result,
                staging_dir_str="",
                last_report_root=None,
                skip_store=skip_store,
            )
            == "压缩包内部条目不支持移至暂存"
        )

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
