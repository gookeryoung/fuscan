"""内置通用规则加载与合并测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules import (
    BUILTIN_PATTERNS_PATH,
    BUILTIN_RULES_PATH,
    RuleError,
    RuleSet,
    load_builtin_ruleset,
    load_with_builtin,
)
from fuscan.rules.builtin import recommended_max_workers


class TestBuiltinRuleset:
    def test_builtin_rules_path_exists(self) -> None:
        """内置规则文件应随包分发。"""
        assert BUILTIN_RULES_PATH.exists()

    def test_builtin_patterns_path_exists(self) -> None:
        """内置匹配规则文件应随包分发。"""
        assert BUILTIN_PATTERNS_PATH.exists()

    def test_load_builtin_ruleset(self) -> None:
        """加载内置规则集应返回非空 RuleSet。"""
        rs = load_builtin_ruleset()
        assert isinstance(rs, RuleSet)
        assert len(rs.rules) > 0
        # 内置规则应包含通用密码赋值检测
        names = {r.name for r in rs.rules}
        assert "P0101-通用密码赋值" in names

    def test_builtin_ruleset_has_ignore_paths(self) -> None:
        """内置规则集应包含 ignore_paths 配置。"""
        rs = load_builtin_ruleset()
        assert len(rs.ignore_paths) > 0
        # 应包含 vendor、cache 等常见忽略路径
        assert any("vendor" in p for p in rs.ignore_paths)


class TestBuiltinPatternsFields:
    """``builtin-patterns.yaml`` 字段覆盖测试。"""

    def test_scan_extensions_defaults_to_all(self) -> None:
        """内置 scan_extensions 应为 None（全选默认，用户可用非空 list 覆盖）。

        密钥/敏感信息典型载体（.env/.py/.yaml/.pem 等）远不止 txt，
        收窄默认会让文件监控与工作区扫描静默漏报。
        """
        rs = load_builtin_ruleset()
        assert rs.scan_extensions is None

    def test_rules_count(self) -> None:
        """内置规则集应包含 P0101/P0102/P0103 三条规则。"""
        rs = load_builtin_ruleset()
        names = [r.name for r in rs.rules]
        assert names == [
            "P0101-通用密码赋值",
            "P0102-敏感配置文件名",
            "P0103-邮件信息包含敏感词",
        ]

    def test_rule_names_unique(self) -> None:
        """内置规则名应唯一。"""
        rs = load_builtin_ruleset()
        names = [r.name for r in rs.rules]
        assert len(names) == len(set(names))

    def test_p0101_password_assignment_is_content_regex(self) -> None:
        """P0101 应为 content regex 模式、warning 级别。"""
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Severity

        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0101-通用密码赋值")
        assert rule.severity == Severity.WARNING
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target == MatchTarget.CONTENT
        assert rule.match.mode == MatchMode.REGEX

    def test_p0102_sensitive_filename_is_filename_regex(self) -> None:
        """P0102 应为 filename regex 模式、info 级别。"""
        from fuscan.rules.model import LeafMatch, MatchMode, MatchTarget, Severity

        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0102-敏感配置文件名")
        assert rule.severity == Severity.INFO
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target == MatchTarget.FILENAME
        assert rule.match.mode == MatchMode.REGEX

    def test_p0103_email_sensitive_is_and_combination(self) -> None:
        """P0103 应为 and 组合（filename .eml + content 敏感词）、critical 级别。"""
        from fuscan.rules.model import AndMatch, LeafMatch, MatchTarget, Severity

        rs = load_builtin_ruleset()
        rule = next(r for r in rs.rules if r.name == "P0103-邮件信息包含敏感词")
        assert rule.severity == Severity.CRITICAL
        assert isinstance(rule.match, AndMatch)
        assert len(rule.match.children) == 2
        children = rule.match.children
        assert isinstance(children[0], LeafMatch)
        assert children[0].target == MatchTarget.FILENAME
        assert isinstance(children[1], LeafMatch)
        assert children[1].target == MatchTarget.CONTENT


class TestBuiltinConfigFields:
    """``builtin.yaml`` 字段覆盖测试。"""

    def test_ignore_dirs_includes_vcs_and_caches(self) -> None:
        """ignore_dirs 应涵盖版本控制、Python、Node 等常见缓存目录。"""
        rs = load_builtin_ruleset()
        required = {".git", "__pycache__", "node_modules", ".venv", "target", "dist", "build"}
        actual = set(rs.ignore_dirs)
        missing = required - actual
        assert not missing, f"ignore_dirs 缺少: {missing}"

    def test_ignore_dirs_includes_windows_system(self) -> None:
        """ignore_dirs 应涵盖 Windows 系统目录。"""
        rs = load_builtin_ruleset()
        actual = set(rs.ignore_dirs)
        assert "Program Files" in actual
        assert "Windows" in actual
        assert "$Recycle.Bin" in actual

    def test_ignore_dirs_includes_fuscan_cache(self) -> None:
        """ignore_dirs 应包含 fuscan 自身缓存目录。"""
        rs = load_builtin_ruleset()
        assert ".fuscan-cache" in rs.ignore_dirs

    def test_ignore_paths_includes_vendor_cache_venv(self) -> None:
        """ignore_paths 应包含 vendor/.cache/third_party/.venv* 四类。"""
        rs = load_builtin_ruleset()
        paths = list(rs.ignore_paths)
        assert "*/vendor/*" in paths
        assert "*/.cache/*" in paths
        assert "*/third_party/*" in paths
        assert "*/.venv*/*" in paths

    def test_scan_params_defaults(self) -> None:
        """scan_params 默认值：max_workers 由 CPU 计算，max_depth=None，max_file_size=50MB。"""
        rs = load_builtin_ruleset()
        assert rs.scan_params is not None
        # max_workers 由 recommended_max_workers 按 CPU 核数动态计算，覆盖 builtin.yaml 中的 None
        assert rs.scan_params.max_workers == recommended_max_workers()
        assert rs.scan_params.max_depth is None
        assert rs.scan_params.max_file_size == 52428800  # 50 * 1024 * 1024
        assert rs.scan_params.scan_archives is True
        assert rs.scan_params.cache_enabled is True
        assert rs.scan_params.perf_log_enabled is False

    def test_whitelist_empty_by_default(self) -> None:
        """内置 whitelist 应为空（误报条目由运行时用户标记追加到用户规则文件）。"""
        rs = load_builtin_ruleset()
        assert rs.whitelist == ()

    def test_version_is_1_0(self) -> None:
        """内置规则集版本应为 1.0。"""
        rs = load_builtin_ruleset()
        assert rs.version == "1.0"


class TestLoadWithBuiltin:
    def test_load_with_builtin_no_user_path(self) -> None:
        """无用户规则时返回纯内置规则集。"""
        rs = load_with_builtin(None)
        builtin = load_builtin_ruleset()
        assert rs.rules == builtin.rules
        assert rs.ignore_paths == builtin.ignore_paths

    def test_load_with_builtin_merges_user_rules(self, tmp_path: Path) -> None:
        """用户规则应合并到内置规则之上。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\n'
            "rules:\n"
            "  - name: 用户自定义规则\n"
            "    severity: warning\n"
            "    match:\n"
            "      type: filename\n"
            "      mode: contains\n"
            "      pattern: secret\n",
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        builtin = load_builtin_ruleset()
        # 合并后规则数 = 内置规则数 + 用户新增规则数
        assert len(rs.rules) == len(builtin.rules) + 1
        names = {r.name for r in rs.rules}
        assert "用户自定义规则" in names
        # 内置规则仍保留
        assert "P0101-通用密码赋值" in names

    def test_load_with_builtin_user_overrides_builtin(self, tmp_path: Path) -> None:
        """用户规则中同名规则覆盖内置规则。"""
        builtin = load_builtin_ruleset()
        builtin_rule_name = builtin.rules[0].name

        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            f"""version: "1.0"
rules:
  - name: {builtin_rule_name}
    severity: critical
    match:
      type: filename
      mode: contains
      pattern: overridden
""",
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        # 同名规则应被覆盖，总数不变
        assert len(rs.rules) == len(builtin.rules)
        overridden_rule = next(r for r in rs.rules if r.name == builtin_rule_name)
        assert overridden_rule.match.pattern == "overridden"  # pyrefly: ignore [missing-attribute]

    def test_load_with_builtin_unions_ignore_paths(self, tmp_path: Path) -> None:
        """用户与内置的 ignore_paths 取并集。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            "version: \"1.0\"\nignore_paths:\n  - '*/my_exclude/*'\nrules: []\n",
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert "*/my_exclude/*" in rs.ignore_paths
        # 内置的 ignore_paths 也应保留
        builtin = load_builtin_ruleset()
        for p in builtin.ignore_paths:
            assert p in rs.ignore_paths

    def test_load_with_builtin_unions_ignore_dirs(self, tmp_path: Path) -> None:
        """用户与内置的 ignore_dirs 取并集。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nignore_dirs:\n  - my_custom_cache\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert "my_custom_cache" in rs.ignore_dirs
        # 内置的 ignore_dirs 也应保留（如 .git）
        assert ".git" in rs.ignore_dirs

    def test_load_with_builtin_user_scan_extensions_overrides(self, tmp_path: Path) -> None:
        """用户规则中 scan_extensions 非 None 时覆盖内置的全选默认。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nscan_extensions:\n  - py\n  - js\n  - yaml\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert rs.scan_extensions == ("py", "js", "yaml")

    def test_load_with_builtin_user_scan_extensions_empty_overrides(self, tmp_path: Path) -> None:
        """用户规则中 scan_extensions 为空列表时覆盖内置（都不扫描语义）。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nscan_extensions: []\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert rs.scan_extensions == ()

    def test_load_with_builtin_user_scan_params_field_override(self, tmp_path: Path) -> None:
        """用户 scan_params 非 None 字段覆盖内置，None 字段保留内置默认。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nscan_params:\n  max_workers: 2\n  max_depth: 10\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert rs.scan_params is not None
        # 用户显式值覆盖内置
        assert rs.scan_params.max_workers == 2
        assert rs.scan_params.max_depth == 10
        # 用户未设置的字段保留内置默认
        assert rs.scan_params.max_file_size == 52428800
        assert rs.scan_params.scan_archives is True

    def test_load_with_builtin_user_max_workers_none_keeps_builtin(self, tmp_path: Path) -> None:
        """用户 scan_params.max_workers 为 None 时保留内置推荐值（按 CPU 核数计算）。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nscan_params:\n  max_depth: 5\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        assert rs.scan_params is not None
        assert rs.scan_params.max_workers == recommended_max_workers()
        assert rs.scan_params.max_depth == 5

    def test_load_with_builtin_invalid_user_file_raises(self, tmp_path: Path) -> None:
        """无效用户规则文件应抛出 RuleError。"""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            'version: "1.0"\nrules:\n  - name: bad\n    match:\n      type: unknown\n',
            encoding="utf-8",
        )

        with pytest.raises(RuleError):
            load_with_builtin([bad_yaml])

    def test_load_with_builtin_nonexistent_user_file_raises(self, tmp_path: Path) -> None:
        """不存在的用户规则文件应抛出 RuleError。"""
        with pytest.raises(RuleError):
            load_with_builtin([tmp_path / "missing.yaml"])
