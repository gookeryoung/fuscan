"""规则示例 YAML 文件加载与结构测试。

冒烟测试：遍历 ``rules/examples/*.yaml`` 全部加载一遍，确保所有示例文件
都能被 :func:`fuscan.rules.load_ruleset` 正确解析为 :class:`RuleSet`。

专项测试：针对 ``development-taboos.yaml`` 验证关键结构与典型规则布局。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules import (
    AndMatch,
    LeafMatch,
    NotMatch,
    RuleSet,
    Severity,
    load_ruleset,
)

# 示例规则文件目录（仓库根 rules/examples/）
_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "rules" / "examples"


def _list_example_yamls() -> list[Path]:
    """枚举 examples 目录下所有 .yaml 文件（不含 README）。"""
    if not _EXAMPLES_DIR.exists():
        return []
    return sorted(_EXAMPLES_DIR.glob("*.yaml"))


@pytest.fixture(scope="class")
def development_taboos_ruleset() -> RuleSet:
    """加载 development-taboos.yaml 规则集（类级共享，避免每个测试重复加载）。"""
    path = _EXAMPLES_DIR / "development-taboos.yaml"
    assert path.exists(), f"规则文件不存在: {path}"
    return load_ruleset(path)


# ----------------------------- 冒烟测试 -----------------------------


class TestExampleYamlSmoke:
    """所有示例 YAML 应能被解析器加载。"""

    @pytest.mark.parametrize("yaml_path", _list_example_yamls(), ids=lambda p: p.name)
    def test_load_all_examples(self, yaml_path: Path) -> None:
        """每个示例文件应成功加载为非空 RuleSet。"""
        rs = load_ruleset(yaml_path)
        assert isinstance(rs, RuleSet)
        assert rs.version == "1.0"
        assert len(rs.rules) > 0
        # 所有规则 name 唯一（避免示例文件中规则重名）
        names = [r.name for r in rs.rules]
        assert len(names) == len(set(names)), f"规则名重复: {yaml_path.name}"

    def test_examples_dir_has_files(self) -> None:
        """示例目录应包含 YAML 文件。"""
        files = _list_example_yamls()
        assert len(files) >= 10, f"示例文件过少: {len(files)}"


# ----------------------------- development-taboos.yaml 专项测试 -----------------------------


class TestDevelopmentTaboos:
    """``development-taboos.yaml`` 结构与内容测试。"""

    def test_version(self, development_taboos_ruleset: RuleSet) -> None:
        """规则集版本应为 1.0。"""
        assert development_taboos_ruleset.version == "1.0"

    def test_rules_count(self, development_taboos_ruleset: RuleSet) -> None:
        """规则数应为 31（覆盖 Python/JS/Java/通用/配置五类禁忌项）。"""
        assert len(development_taboos_ruleset.rules) == 31

    def test_scan_extensions(self, development_taboos_ruleset: RuleSet) -> None:
        """scan_extensions 应覆盖主流源码后缀。"""
        exts = set(development_taboos_ruleset.scan_extensions or ())
        expected = {"py", "js", "ts", "java", "go", "rs", "c", "cpp", "cs", "php", "rb", "sh", "sql"}
        assert expected.issubset(exts)

    def test_ignore_paths_excludes_vendor(self, development_taboos_ruleset: RuleSet) -> None:
        """ignore_paths 应排除 vendor/third_party/node_modules/build/dist。"""
        paths = " ".join(development_taboos_ruleset.ignore_paths)
        for keyword in ("vendor", "third_party", "node_modules", "build", "dist"):
            assert keyword in paths, f"ignore_paths 缺少 {keyword}"

    def test_scan_params(self, development_taboos_ruleset: RuleSet) -> None:
        """scan_params 应配置 max_workers=8, max_depth=20。"""
        assert development_taboos_ruleset.scan_params is not None
        assert development_taboos_ruleset.scan_params.max_workers == 8
        assert development_taboos_ruleset.scan_params.max_depth == 20

    def test_severity_distribution(self, development_taboos_ruleset: RuleSet) -> None:
        """规则应覆盖 critical/warning/info 三级（不只有单一等级）。"""
        severities = {r.severity for r in development_taboos_ruleset.rules}
        assert Severity.CRITICAL in severities
        assert Severity.WARNING in severities
        assert Severity.INFO in severities

    def test_all_rules_have_match(self, development_taboos_ruleset: RuleSet) -> None:
        """所有规则都应有非空 match 结构。"""
        for rule in development_taboos_ruleset.rules:
            assert rule.match is not None
            assert rule.name  # name 非空

    def test_python_bare_except_rule(self, development_taboos_ruleset: RuleSet) -> None:
        """Python 裸 except 规则应为 warning 级 LeafMatch(content, regex)。"""
        rule = next(r for r in development_taboos_ruleset.rules if r.name == "Python 裸 except 捕获")
        assert rule.severity == Severity.WARNING
        assert isinstance(rule.match, LeafMatch)

    def test_python_assert_uses_and_not(self, development_taboos_ruleset: RuleSet) -> None:
        """Python 生产 assert 规则应使用 and + not 组合排除测试目录。"""
        rule = next(r for r in development_taboos_ruleset.rules if "assert" in r.name)
        assert isinstance(rule.match, AndMatch)
        assert len(rule.match.children) == 2
        # 第二个子条件应为 NotMatch（排除测试目录）
        assert isinstance(rule.match.children[1], NotMatch)

    def test_env_file_rule(self, development_taboos_ruleset: RuleSet) -> None:
        """.env 文件入库规则应为 critical 级 filename regex 匹配。"""
        rule = next(r for r in development_taboos_ruleset.rules if ".env" in r.name)
        assert rule.severity == Severity.CRITICAL
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target.value == "filename"
        assert rule.match.mode.value == "regex"

    def test_ide_config_rule_uses_path(self, development_taboos_ruleset: RuleSet) -> None:
        """IDE 配置入库规则应使用 path 匹配（.idea/.vscode 路径）。"""
        rule = next(r for r in development_taboos_ruleset.rules if "IDE" in r.name)
        assert isinstance(rule.match, LeafMatch)
        assert rule.match.target.value == "path"

    def test_rule_names_no_duplicate(self, development_taboos_ruleset: RuleSet) -> None:
        """所有规则名应唯一。"""
        names = [r.name for r in development_taboos_ruleset.rules]
        assert len(names) == len(set(names))

    def test_rule_names_use_chinese(self, development_taboos_ruleset: RuleSet) -> None:
        """规则名应使用中文（与示例规则集风格一致）。"""
        for rule in development_taboos_ruleset.rules:
            # 至少包含一个中文字符
            assert any("\u4e00" <= ch <= "\u9fff" for ch in rule.name), f"规则名非中文: {rule.name}"

    def test_critical_rules_count(self, development_taboos_ruleset: RuleSet) -> None:
        """critical 级规则应不少于 8 条（覆盖 pickle/MD5/SHA1/os.system/eval/exec/硬编码密码/.env 等）。"""
        critical = [r for r in development_taboos_ruleset.rules if r.severity == Severity.CRITICAL]
        assert len(critical) >= 8
