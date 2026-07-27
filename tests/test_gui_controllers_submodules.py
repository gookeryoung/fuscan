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
    from fuscan.gui.controllers._persistence import (
        coerce_int,
        coerce_str,
        coerce_str_tuple,
        deserialize_task_overrides,
        serialize_task_overrides,
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
            '{"version": %d, "workspaces": []}' % (PERSIST_VERSION + 100),
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
