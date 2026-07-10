"""内置通用规则加载与合并测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from uniscan.builtin import BUILTIN_RULES_PATH, load_builtin_ruleset, load_with_builtin
from uniscan.rules import RuleError, RuleSet


class TestBuiltinRuleset:
    def test_builtin_rules_path_exists(self) -> None:
        """内置规则文件应随包分发。"""
        assert BUILTIN_RULES_PATH.exists()

    def test_load_builtin_ruleset(self) -> None:
        """加载内置规则集应返回非空 RuleSet。"""
        rs = load_builtin_ruleset()
        assert isinstance(rs, RuleSet)
        assert len(rs.rules) > 0
        # 内置规则应包含常见密钥检测
        names = {r.name for r in rs.rules}
        assert "AWS访问密钥" in names

    def test_builtin_ruleset_has_ignore_paths(self) -> None:
        """内置规则集应包含 ignore_paths 配置。"""
        rs = load_builtin_ruleset()
        assert len(rs.ignore_paths) > 0
        # 应包含 vendor、cache 等常见忽略路径
        assert any("vendor" in p for p in rs.ignore_paths)

    def test_builtin_ruleset_has_ignore_dirs(self) -> None:
        """内置规则集应包含 ignore_dirs 配置。"""
        rs = load_builtin_ruleset()
        assert ".git" in rs.ignore_dirs


class TestLoadWithBuiltin:
    def test_load_with_builtin_no_user_path(self) -> None:
        """无用户规则时返回纯内置规则集。"""
        rs = load_with_builtin(None)
        builtin = load_builtin_ruleset()
        assert rs.rules == builtin.rules
        assert rs.ignore_dirs == builtin.ignore_dirs

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
        assert "AWS访问密钥" in names

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
        assert overridden_rule.match.pattern == "overridden"

    def test_load_with_builtin_unions_ignore_dirs(self, tmp_path: Path) -> None:
        """用户与内置的 ignore_dirs 取并集。"""
        user_yaml = tmp_path / "user.yaml"
        user_yaml.write_text(
            'version: "1.0"\nignore_dirs:\n  - my_custom_dir\nrules: []\n',
            encoding="utf-8",
        )

        rs = load_with_builtin([user_yaml])
        builtin = load_builtin_ruleset()
        assert "my_custom_dir" in rs.ignore_dirs
        # 内置的 .git 也应保留
        assert ".git" in rs.ignore_dirs
        # 总数应大于内置
        assert len(rs.ignore_dirs) > len(builtin.ignore_dirs)

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
