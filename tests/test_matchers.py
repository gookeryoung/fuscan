"""匹配器单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
)
from fuscan.scanner.context import FileEntry, MatchContext
from fuscan.scanner.matchers import (
    AndMatcher,
    ContentMatcher,
    ContentRegexPool,
    FileNameMatcher,
    Matcher,
    NotMatcherImpl,
    OrMatcher,
    PathMatcher,
    _build_content_composite_groups,
    _evaluate_composite_group,
    build_matcher,
)


def _make_context(path: Path, content: str = "") -> MatchContext:
    """构造测试上下文，使用自定义内容提供器。"""
    entry = (
        FileEntry.from_path(path)
        if path.exists()
        else FileEntry(
            path=path, name=path.name, size=len(content), mtime=0.0, extension=path.suffix.lower().lstrip(".")
        )
    )
    return MatchContext(entry, content_provider=lambda e: content)


class TestFileNameMatcher:
    @pytest.mark.parametrize(
        "mode,pattern,name,case_sensitive,expected",
        [
            (MatchMode.CONTAINS, "password", "my_password.txt", False, True),
            (MatchMode.CONTAINS, "PASSWORD", "my_password.txt", False, True),
            (MatchMode.CONTAINS, "PASSWORD", "my_password.txt", True, False),
            (MatchMode.EQUALS, "secret.txt", "secret.txt", False, True),
            (MatchMode.EQUALS, "secret.txt", "SECRET.TXT", False, True),
            (MatchMode.STARTSWITH, "test_", "test_file.txt", False, True),
            (MatchMode.STARTSWITH, "TEST_", "test_file.txt", False, True),
            (MatchMode.ENDSWITH, ".conf", "config.conf", False, True),
            (MatchMode.ENDSWITH, ".CONF", "config.conf", False, True),
        ],
    )
    def test_modes(
        self,
        mode: MatchMode,
        pattern: str,
        name: str,
        case_sensitive: bool,
        expected: bool,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=mode, pattern=pattern, case_sensitive=case_sensitive)
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is expected

    def test_regex_match(self, tmp_path: Path) -> None:
        path = tmp_path / "AKIA12345.txt"
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"AKIA\d+", case_sensitive=True)
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is True

    def test_regex_compile_error(self) -> None:
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"[unclosed", case_sensitive=False)
        with pytest.raises(ValueError, match="正则表达式编译失败"):
            FileNameMatcher(spec)


class TestContentMatcher:
    def test_content_contains(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret", case_sensitive=False)
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="the secret value")
        assert matcher.matches(ctx).matched is True

    def test_content_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "ak.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"AKIA[0-9A-Z]{16}",
            case_sensitive=True,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="key=AKIAABCDEFGHIJKLMNOP")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "AKIA" in result.detail

    def test_content_not_matched(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing", case_sensitive=False)
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        assert matcher.matches(ctx).matched is False


class TestPathMatcher:
    def test_path_contains(self, tmp_path: Path) -> None:
        path = tmp_path / "backup" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup", case_sensitive=False)
        matcher = PathMatcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is True


class TestCompositeMatchers:
    def test_and_all_match(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$", case_sensitive=False),
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password", case_sensitive=False
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        assert matcher.matches(ctx).matched is True

    def test_and_partial_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.txt"
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="doc.conf", case_sensitive=False),
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password", case_sensitive=False
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        assert matcher.matches(ctx).matched is False

    def test_or_any_match(self, tmp_path: Path) -> None:
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token", case_sensitive=False),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key", case_sensitive=False),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="has api_key here")
        assert matcher.matches(ctx).matched is True

    def test_or_none_match(self, tmp_path: Path) -> None:
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token", case_sensitive=False),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key", case_sensitive=False),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="nothing relevant")
        assert matcher.matches(ctx).matched is False

    def test_not_inverts(self, tmp_path: Path) -> None:
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(
            child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup", case_sensitive=False)
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is True  # path 不含 backup → not 命中

    def test_not_inverts_to_false(self, tmp_path: Path) -> None:
        path = tmp_path / "backup" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(
            child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup", case_sensitive=False)
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is False


class TestBuildMatcher:
    def test_build_filename(self) -> None:
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        matcher = build_matcher(spec)
        assert isinstance(matcher, FileNameMatcher)

    def test_build_content(self) -> None:
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="x")
        matcher = build_matcher(spec)
        assert isinstance(matcher, ContentMatcher)

    def test_build_path(self) -> None:
        spec = LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="x")
        matcher = build_matcher(spec)
        assert isinstance(matcher, PathMatcher)

    def test_build_and(self) -> None:
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="a"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="b"),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        assert len(matcher.children) == 2

    def test_build_or(self) -> None:
        spec = OrMatch(children=(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="a"),))
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)

    def test_build_not(self) -> None:
        spec = NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="x"))
        matcher = build_matcher(spec)
        assert isinstance(matcher, NotMatcherImpl)

    def test_match_all_collects(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="pwd"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_pwd=x")
        results = matcher.match_all(ctx)
        assert len(results) == 2


def test_matcher_abstract() -> None:
    """Matcher 是抽象基类，不能直接实例化。"""
    with pytest.raises(TypeError):
        Matcher()  # type: ignore[abstract]


class TestMatcherEdgeCases:
    """匹配器边界条件与异常路径覆盖。"""

    def test_and_match_all_collects_children(self, tmp_path: Path) -> None:
        """AndMatcher.match_all 应收集所有子匹配器的结果。"""
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="doc"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="pwd"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_pwd=x")
        results = matcher.match_all(ctx)
        assert len(results) == 2

    def test_or_match_all_collects_children(self, tmp_path: Path) -> None:
        """OrMatcher.match_all 应收集所有子匹配器的结果。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="has token here")
        results = matcher.match_all(ctx)
        assert len(results) == 2

    def test_and_matches_with_no_detail(self, tmp_path: Path) -> None:
        """AndMatcher 全部命中但无 detail 时返回默认"全部命中"。"""
        path = tmp_path / "x.txt"
        # EQUALS 模式命中时 detail 为"完全相等"，但如果都命中应合并 detail
        spec = AndMatch(children=(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="x.txt"),))
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True

    def test_apply_leaf_endswith_not_matched(self, tmp_path: Path) -> None:
        """ENDSWITH 模式不匹配时返回 matched=False。"""
        path = tmp_path / "config.txt"
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.ENDSWITH, pattern=".conf")
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is False

    def test_build_matcher_unknown_target_raises(self) -> None:
        """build_matcher 对未知 target 应抛出 TypeError。"""
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="x")
        # frozen dataclass 需用 object.__setattr__ 绕过冻结限制
        object.__setattr__(spec, "target", "UNKNOWN")
        with pytest.raises(TypeError, match="未知匹配目标"):
            build_matcher(spec)

    def test_build_matcher_unknown_spec_type_raises(self) -> None:
        """build_matcher 对未知规格类型应抛出 TypeError。"""
        with pytest.raises(TypeError, match="未知匹配规格类型"):
            build_matcher("not_a_spec")  # type: ignore[arg-type]

    def test_or_matcher_match_all_empty(self, tmp_path: Path) -> None:
        """OrMatcher.match_all 无子匹配器时返回空列表。"""
        matcher = OrMatcher(OrMatch(children=()))
        path = tmp_path / "x.txt"
        ctx = _make_context(path)
        results = matcher.match_all(ctx)
        assert results == []

    def test_and_matcher_match_all_empty(self, tmp_path: Path) -> None:
        """AndMatcher.match_all 无子匹配器时返回空列表。"""
        matcher = AndMatcher(AndMatch(children=()))
        path = tmp_path / "x.txt"
        ctx = _make_context(path)
        results = matcher.match_all(ctx)
        assert results == []


class TestMatchText:
    """``match_text`` 字段测试：确保原始匹配文本无 repr 转义地传递到 GUI 高亮层。

    覆盖场景：
    - regex/contains/equals/startswith/endswith 各模式均填充 ``match_text``
    - 特殊字符（反斜杠、单引号、双引号、换行）原样保留
    - AndMatcher 取首个子匹配文本；OrMatcher 透传命中分支的文本
    """

    def test_regex_match_text_is_raw_group0(self, tmp_path: Path) -> None:
        """regex 模式 ``match_text`` 应为 ``m.group(0)`` 原始文本，而非 repr 转义。"""
        path = tmp_path / "db.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"(?i)mongodb://\S+:\S+@",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="url=mongodb://user:pass123@host")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "mongodb://user:pass123@"

    def test_regex_match_text_preserves_backslash(self, tmp_path: Path) -> None:
        """密码含反斜杠时 ``match_text`` 应原样保留，不经过 repr 转义。"""
        path = tmp_path / "db.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"(?i)mongodb://\S+:\S+@",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=r"url=mongodb://user:pass\123@host")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == r"mongodb://user:pass\123@"
        assert "\\" in result.match_text

    def test_regex_match_text_preserves_single_quote(self, tmp_path: Path) -> None:
        """密码含单引号时 ``match_text`` 应原样保留。"""
        path = tmp_path / "db.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"(?i)mongodb://\S+:\S+@",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="url=mongodb://user:pa'ss@host")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "mongodb://user:pa'ss@"
        assert "'" in result.match_text

    def test_regex_match_text_preserves_newline(self, tmp_path: Path) -> None:
        """跨行 Bearer 令牌的 ``match_text`` 应保留换行符。"""
        path = tmp_path / "auth.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"(?i)bearer\s+[A-Za-z0-9._\-]+",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="Authorization: Bearer\n  eyJhbGci.token")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "\n" in result.match_text
        assert result.match_text.startswith("Bearer")

    def test_contains_match_text(self, tmp_path: Path) -> None:
        """CONTAINS 模式 ``match_text`` 应为 pattern 本身。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password", case_sensitive=False)
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="the password here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "password"

    def test_equals_match_text(self, tmp_path: Path) -> None:
        """EQUALS 模式 ``match_text`` 应为 pattern 本身。"""
        path = tmp_path / "secret.txt"
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="secret.txt", case_sensitive=False)
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "secret.txt"

    def test_startswith_match_text(self, tmp_path: Path) -> None:
        """STARTSWITH 模式 ``match_text`` 应为 pattern 本身。"""
        path = tmp_path / "test_file.txt"
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.STARTSWITH, pattern="test_", case_sensitive=False)
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "test_"

    def test_endswith_match_text(self, tmp_path: Path) -> None:
        """ENDSWITH 模式 ``match_text`` 应为 pattern 本身。"""
        path = tmp_path / "config.conf"
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.ENDSWITH, pattern=".conf", case_sensitive=False)
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == ".conf"

    def test_not_matched_has_empty_match_text(self, tmp_path: Path) -> None:
        """未命中时 ``match_text`` 应为空字符串。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing", case_sensitive=False)
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_text == ""

    def test_and_matcher_uses_first_child_match_text(self, tmp_path: Path) -> None:
        """AndMatcher 应取首个子匹配器的 ``match_text`` 作为高亮关键词。"""
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is True
        # 第一个子匹配器是 FileNameMatcher，regex 模式 match_text 为 m.group(0)
        assert result.match_text == ".conf"

    def test_or_matcher_passes_through_match_text(self, tmp_path: Path) -> None:
        """OrMatcher 应透传命中分支的 ``match_text``。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"AKIA\d+"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="key=AKIA12345")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "AKIA12345"

    def test_not_matcher_has_empty_match_text(self, tmp_path: Path) -> None:
        """NotMatcher 命中时 ``match_text`` 应为空（无原始匹配文本）。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == ""


class TestMatchCount:
    """``match_count`` 字段测试：确保实际匹配文本条数正确传递。

    区分"命中规则数"（一条规则对一个文件命中一次）与"匹配条数"
    （同一规则在同一文件中匹配到多处文本，如多处密码）。
    """

    def test_regex_single_match_count_is_1(self, tmp_path: Path) -> None:
        """regex 模式单次命中 match_count 应为 1。"""
        path = tmp_path / "file.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\w+")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="password=secret")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1

    def test_regex_multiple_matches_count(self, tmp_path: Path) -> None:
        """regex 模式多处命中 match_count 应为匹配条数。"""
        path = tmp_path / "file.txt"
        content = "mongodb://user:pass1@host\nmongodb://user:pass2@host\nmongodb://user:pass3@host"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"mongodb://user:\w+@")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3
        # match_text 仍为首个匹配文本
        assert result.match_text == "mongodb://user:pass1@"

    def test_regex_no_match_count_is_default(self, tmp_path: Path) -> None:
        """regex 模式未命中 match_count 应为默认值 1（matched=False 时无意义）。"""
        path = tmp_path / "file.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\w+")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_count == 1

    def test_contains_multiple_occurrences_count(self, tmp_path: Path) -> None:
        """contains 模式多处出现 match_count 应为非重叠出现次数。"""
        path = tmp_path / "file.txt"
        content = "password=abc\npassword=def\npassword=ghi"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3

    def test_contains_case_insensitive_count(self, tmp_path: Path) -> None:
        """contains 模式大小写不敏感时统计所有变体出现次数。"""
        path = tmp_path / "file.txt"
        content = "Password=abc\nPASSWORD=def\npassword=ghi"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3

    def test_equals_match_count_is_1(self, tmp_path: Path) -> None:
        """equals 模式命中时 match_count 固定为 1。"""
        path = tmp_path / "secret.txt"
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="secret.txt")
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1

    def test_startswith_match_count_is_1(self, tmp_path: Path) -> None:
        """startswith 模式命中时 match_count 固定为 1。"""
        path = tmp_path / "test_file.txt"
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.STARTSWITH, pattern="test_")
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1

    def test_endswith_match_count_is_1(self, tmp_path: Path) -> None:
        """endswith 模式命中时 match_count 固定为 1。"""
        path = tmp_path / "config.conf"
        path.write_text("", encoding="utf-8")
        spec = LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.ENDSWITH, pattern=".conf")
        matcher = FileNameMatcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1

    def test_and_matcher_sums_child_counts(self, tmp_path: Path) -> None:
        """AndMatcher 的 match_count 应为所有子匹配器 match_count 之和。"""
        path = tmp_path / "test_file.conf"
        content = "password=abc\npassword=def"
        # 子1：内容包含 password（2 次），子2：文件名以 test_ 开头（1 次）
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.STARTSWITH, pattern="test_"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3  # 2 + 1

    def test_or_matcher_sums_matched_child_counts(self, tmp_path: Path) -> None:
        """OrMatcher 的 match_count 应为所有命中子匹配器 match_count 之和。"""
        path = tmp_path / "file.txt"
        content = "password=abc\npassword=def\npassword=ghi"
        # 子1：内容包含 password（3 次），子2：内容包含 secret（0 次）
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3

    def test_not_matcher_count_is_1(self, tmp_path: Path) -> None:
        """NotMatcher 命中时 match_count 固定为 1。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1


class TestMatchTarget:
    """``target`` 字段测试：确保叶子匹配器设置正确的匹配目标类型。

    GUI 根据 ``target=="filename"`` 判断是否在内容预览中搜索高亮位置——
    文件名匹配不应在内容中搜索高亮，否则可能产生误导。
    """

    def test_filename_matcher_sets_target(self, tmp_path: Path) -> None:
        """FileNameMatcher 命中时 target 应为 'filename'。"""
        path = tmp_path / "password.txt"
        path.write_text("content", encoding="utf-8")
        matcher = FileNameMatcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password"))
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == "filename"

    def test_content_matcher_sets_target(self, tmp_path: Path) -> None:
        """ContentMatcher 命中时 target 应为 'content'。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("password=123", encoding="utf-8")
        matcher = ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"))
        ctx = _make_context(path, "password=123")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == "content"

    def test_path_matcher_sets_target(self, tmp_path: Path) -> None:
        """PathMatcher 命中时 target 应为 'path'。"""
        path = tmp_path / "data" / "backup.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        matcher = PathMatcher(LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == "path"

    def test_not_matched_has_empty_target(self, tmp_path: Path) -> None:
        """未命中时 target 应为空字符串。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("hello", encoding="utf-8")
        matcher = ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing"))
        ctx = _make_context(path, "hello")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.target == ""

    def test_or_matcher_passes_through_target(self, tmp_path: Path) -> None:
        """OrMatcher 应透传命中分支的 target。"""
        path = tmp_path / "password.txt"
        path.write_text("nothing here", encoding="utf-8")
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, "nothing here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == "filename"

    def test_and_matcher_has_empty_target(self, tmp_path: Path) -> None:
        """AndMatcher 为组合规则，target 应为空字符串。"""
        path = tmp_path / "password.txt"
        path.write_text("password=123", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, "password=123")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == ""


class TestMatchTexts:
    """``match_texts`` 字段测试：覆盖需求3（AND/OR 标记所有命中内容）。

    AND/OR 组合规则应收集所有子匹配器命中的文本到 ``match_texts`` 元组，
    去重保序后供 GUI 高亮所有命中关键词。叶子匹配器命中时填入单元素元组，
    未命中时为空元组。
    """

    def test_leaf_matcher_fills_match_texts_on_hit(self, tmp_path: Path) -> None:
        """叶子匹配器命中时 match_texts 应为单元素元组。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="the password here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_texts == ("password",)

    def test_leaf_matcher_match_texts_empty_on_miss(self, tmp_path: Path) -> None:
        """叶子匹配器未命中时 match_texts 应为空元组。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_texts == ()

    def test_and_matcher_collects_all_child_match_texts(self, tmp_path: Path) -> None:
        """AndMatcher 应收集所有子匹配器命中的文本。"""
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is True
        # 第一个子匹配器 match_text=".conf"，第二个 match_text="password"
        assert result.match_texts == (".conf", "password")

    def test_and_matcher_dedup_preserve_order(self, tmp_path: Path) -> None:
        """AndMatcher 收集的 match_texts 应去重保序。"""
        path = tmp_path / "password.conf"
        path.write_text("", encoding="utf-8")
        # 两个子匹配器都匹配 "password" 文本（一个文件名，一个内容）
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=123")
        result = matcher.matches(ctx)
        assert result.matched is True
        # 去重后只剩一个 "password"
        assert result.match_texts == ("password",)

    def test_and_matcher_partial_fail_returns_empty_match_texts(self, tmp_path: Path) -> None:
        """AndMatcher 任一子匹配器未命中时 match_texts 应为空元组。"""
        path = tmp_path / "doc.txt"
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="doc.conf"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_texts == ()

    def test_or_matcher_collects_all_matched_children_match_texts(self, tmp_path: Path) -> None:
        """OrMatcher 应收集所有命中子匹配器的 match_texts（不止首个）。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="has token and key here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_texts == ("token", "key")

    def test_or_matcher_collects_only_matched_children(self, tmp_path: Path) -> None:
        """OrMatcher 仅收集命中子匹配器的文本，未命中的不进入 match_texts。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="has token here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_texts == ("token",)

    def test_or_matcher_none_match_returns_empty_match_texts(self, tmp_path: Path) -> None:
        """OrMatcher 所有子匹配器均未命中时 match_texts 应为空元组。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="nothing relevant")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_texts == ()

    def test_or_matcher_dedup_preserve_order(self, tmp_path: Path) -> None:
        """OrMatcher 收集的 match_texts 应去重保序。"""
        path = tmp_path / "x.txt"
        # 两个子匹配器都匹配 "password"（一个内容 contains，一个内容 regex）
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\w+"),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=secret")
        result = matcher.matches(ctx)
        assert result.matched is True
        # 第一个 match_text="password"，第二个 match_text="password=secret"
        # 均不同，去重后按出现顺序保留
        assert result.match_texts == ("password", "password=secret")

    def test_not_matcher_match_texts_empty_on_hit(self, tmp_path: Path) -> None:
        """NotMatcher 命中时 match_texts 应为空元组（无原始匹配文本）。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_texts == ()


class TestMatchDescription:
    """``match_description`` 字段测试：覆盖需求4（match 项描述字段）。

    所有匹配器均应将 spec.description 透传到 MatchResult，未设置时为空字符串。
    GUI 与导出层据此展示描述列。
    """

    def test_leaf_matcher_fills_description_from_spec(self, tmp_path: Path) -> None:
        """叶子匹配器命中时应填充 match_description 来自 spec.description。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
            description="敏感凭证关键词",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="the password here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_description == "敏感凭证关键词"

    def test_leaf_matcher_description_empty_by_default(self, tmp_path: Path) -> None:
        """叶子匹配器未设置 description 时 match_description 应为空字符串。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="the password here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_description == ""

    def test_leaf_matcher_description_preserved_on_miss(self, tmp_path: Path) -> None:
        """叶子匹配器未命中时也填充 description，便于调用方区分组合规则的描述。"""
        path = tmp_path / "f.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="missing",
            description="未命中描述",
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_description == "未命中描述"

    def test_and_matcher_fills_description_from_spec(self, tmp_path: Path) -> None:
        """AndMatcher 命中时应填充 match_description 来自 spec.description。"""
        path = tmp_path / "doc.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
            description="配置文件含密码",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_description == "配置文件含密码"

    def test_and_matcher_description_preserved_on_partial_fail(self, tmp_path: Path) -> None:
        """AndMatcher 部分未命中时也填充 description。"""
        path = tmp_path / "doc.txt"
        spec = AndMatch(
            children=(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="doc.conf"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            ),
            description="配置文件含密码",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_description == "配置文件含密码"

    def test_or_matcher_fills_description_from_spec(self, tmp_path: Path) -> None:
        """OrMatcher 命中时应填充 match_description 来自 spec.description。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key"),
            ),
            description="凭证关键词命中",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="has token here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_description == "凭证关键词命中"

    def test_or_matcher_description_preserved_on_no_match(self, tmp_path: Path) -> None:
        """OrMatcher 全部未命中时也填充 description。"""
        path = tmp_path / "x.txt"
        spec = OrMatch(
            children=(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing1"),
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing2"),
            ),
            description="凭证关键词命中",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="nothing relevant")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_description == "凭证关键词命中"

    def test_not_matcher_fills_description_from_spec(self, tmp_path: Path) -> None:
        """NotMatcher 命中时应填充 match_description 来自 spec.description。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(
            child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"),
            description="非备份目录文件",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_description == "非备份目录文件"

    def test_not_matcher_description_preserved_on_invert_false(self, tmp_path: Path) -> None:
        """NotMatcher 子条件命中（NotMatcher 自身未命中）时也填充 description。"""
        path = tmp_path / "backup" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        spec = NotMatch(
            child=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"),
            description="非备份目录文件",
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path)
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_description == "非备份目录文件"


class TestContainsOptimization:
    """CONTAINS 大小写不敏感优化测试。

    优化点：不区分大小写时用 ``re.finditer(re.escape(pattern), text, re.IGNORECASE)``
    替代 ``text.lower().count(pattern.lower())``，避免对整个大文本做 ``lower()``
    创建临时字符串。
    """

    def test_contains_case_insensitive_multiple_variants(self, tmp_path: Path) -> None:
        """不区分大小写时统计 Password/PASSWORD/password 等所有变体。"""
        path = tmp_path / "file.txt"
        content = "Password=abc\nPASSWORD=def\npassword=ghi\nPaSsWoRd=xyz"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 4

    def test_contains_case_sensitive_counts_exact_only(self, tmp_path: Path) -> None:
        """区分大小写时只统计精确匹配。"""
        path = tmp_path / "file.txt"
        content = "Password=abc\npassword=def\nPASSWORD=ghi"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
            case_sensitive=True,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1

    def test_contains_empty_pattern_no_match(self) -> None:
        """空 pattern 不应匹配（str.count 会返回 len+1，语义错误）。

        LeafMatch 模型层已禁止空 pattern，此处直接测试 _apply_contains 防御逻辑。
        """
        from fuscan.scanner.matchers import _apply_contains

        result = _apply_contains("some content", "", case_sensitive=False, compiled_ci=None)
        assert result.matched is False

    def test_contains_empty_pattern_no_match_case_sensitive(self) -> None:
        """空 pattern 区分大小写时也不应匹配。"""
        from fuscan.scanner.matchers import _apply_contains

        result = _apply_contains("some content", "", case_sensitive=True, compiled_ci=None)
        assert result.matched is False

    def test_contains_regex_special_chars_escaped(self, tmp_path: Path) -> None:
        """pattern 含正则特殊字符时应按字面量匹配，而非正则解释。"""
        path = tmp_path / "file.txt"
        # pattern 含 . * + ? 等，应作为字面量
        content = "key.a.b\nkey.a.b\nother*x"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="key.a.b",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 2  # "key.a.b" 出现 2 次，"other*x" 不匹配

    def test_contains_regex_special_chars_case_insensitive(self, tmp_path: Path) -> None:
        """含正则特殊字符的 pattern 不区分大小写时仍按字面量匹配。"""
        path = tmp_path / "file.txt"
        content = "KEY.A.B\nkey.a.b\nKey.A.B"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="key.a.b",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3

    def test_contains_non_overlapping_count(self, tmp_path: Path) -> None:
        """CONTAINS 统计非重叠出现次数（与 str.count 语义一致）。"""
        path = tmp_path / "file.txt"
        # "aa" 在 "aaaa" 中非重叠出现 2 次（位置 0 和 2）
        content = "aaaa"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="aa",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 2

    def test_contains_no_match_returns_default_count(self, tmp_path: Path) -> None:
        """CONTAINS 未命中时 match_count 应为默认值 1。"""
        path = tmp_path / "file.txt"
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="missing",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content="nothing here")
        result = matcher.matches(ctx)
        assert result.matched is False
        assert result.match_count == 1

    def test_contains_large_text_case_insensitive(self, tmp_path: Path) -> None:
        """大文本不区分大小写 CONTAINS 计数正确（验证 re.finditer 路径）。"""
        path = tmp_path / "large.txt"
        # 构造 1000 个混合大小写的 pattern 出现
        parts = []
        for _ in range(500):
            parts.append("Password")
        for _ in range(500):
            parts.append("PASSWORD")
        content = "\n".join(parts)
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.CONTAINS,
            pattern="password",
            case_sensitive=False,
        )
        matcher = ContentMatcher(spec)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 1000


class TestCompositeGroupOptimization:
    """组合规则复合 CONTENT REGEX 子项组优化测试。

    覆盖 :class:`AndMatcher` / :class:`OrMatcher` 构造期把同 case_sensitive 的
    CONTENT REGEX 子项合并为复合 OR 正则的路径（:class:`_ContentCompositeGroup`），
    以及无复合组时回退到原逐子项路径的行为一致性。
    """

    def test_and_two_content_regex_all_match(self, tmp_path: Path) -> None:
        """AND 含 2 个同 case_sensitive 的 CONTENT REGEX 子项 → 复合组，全部命中。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        assert len(matcher._composite_groups) == 1
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "password=abc" in result.match_texts
        assert "api_key=xyz" in result.match_texts

    def test_and_two_content_regex_partial_fail(self, tmp_path: Path) -> None:
        """AND 含 2 个 CONTENT REGEX 子项，其中一个不命中 → 复合组短路返回 False。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=abc but no api key here")
        result = matcher.matches(ctx)
        assert result.matched is False

    def test_and_mixed_filename_content_regex(self, tmp_path: Path) -> None:
        """AND 含 FILENAME + 2 个 CONTENT REGEX → 仅 CONTENT REGEX 进复合组。"""
        path = tmp_path / "config.conf"
        path.write_text("", encoding="utf-8")
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.FILENAME,
                    mode=MatchMode.ENDSWITH,
                    pattern=".conf",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        assert len(matcher._composite_groups) == 1
        # FILENAME 子项（下标 0）不在复合组中
        assert 0 not in matcher._composite_child_indices
        assert 1 in matcher._composite_child_indices
        assert 2 in matcher._composite_child_indices
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True

    def test_and_single_content_regex_no_composite(self, tmp_path: Path) -> None:
        """AND 含 1 个 CONTENT REGEX → 无复合组（单子项无合并收益），走原路径。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.FILENAME,
                    mode=MatchMode.EQUALS,
                    pattern="file.txt",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        assert len(matcher._composite_groups) == 0
        ctx = _make_context(path, content="password=abc")
        result = matcher.matches(ctx)
        assert result.matched is True

    def test_and_different_case_sensitive_separate_groups(self, tmp_path: Path) -> None:
        """AND 含 case_sensitive=True 和 =False 各 2 个 → 2 个复合组。"""
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"Password\s*=\s*\S+",
                    case_sensitive=True,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"Api_key\s*=\s*\S+",
                    case_sensitive=True,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        assert len(matcher._composite_groups) == 2

    def test_and_content_regex_case_sensitive(self, tmp_path: Path) -> None:
        """case_sensitive=True 的复合组：大小写不匹配时不命中。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"Password\s*=\s*\S+",
                    case_sensitive=True,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"Api_key\s*=\s*\S+",
                    case_sensitive=True,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        # case_sensitive=True，内容为小写，模式首字母大写 → 不命中
        assert result.matched is False
        # 大小写匹配时命中
        ctx2 = _make_context(path, content="Password=abc\nApi_key=xyz")
        result2 = matcher.matches(ctx2)
        assert result2.matched is True

    def test_and_content_regex_inline_ignorecase(self, tmp_path: Path) -> None:
        """含 (?i) 内联标志的复合组：case_sensitive=True 但内联 (?i) → 大小写不敏感。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"(?i)password\s*=\s*\S+",
                    case_sensitive=True,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"(?i)api_key\s*=\s*\S+",
                    case_sensitive=True,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        group = matcher._composite_groups[0]
        # 内联 (?i) → prefilter_case_insensitive=True
        assert group.prefilter_case_insensitive is True
        ctx = _make_context(path, content="PASSWORD=abc\nAPI_KEY=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True

    def test_and_prefilter_short_circuit(self, tmp_path: Path) -> None:
        """复合组预筛短路：内容中无任何关键字 → 整组不命中。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="nothing relevant here at all")
        result = matcher.matches(ctx)
        assert result.matched is False

    def test_or_two_content_regex_some_match(self, tmp_path: Path) -> None:
        """OR 含 2 个 CONTENT REGEX 子项，其中一个命中 → 复合组，OR 命中。"""
        path = tmp_path / "file.txt"
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)
        assert len(matcher._composite_groups) == 1
        ctx = _make_context(path, content="password=abc but no api key")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "password=abc" in result.match_texts

    def test_or_two_content_regex_none_match(self, tmp_path: Path) -> None:
        """OR 含 2 个 CONTENT REGEX 子项，均不命中 → 复合组返回 False。"""
        path = tmp_path / "file.txt"
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="nothing relevant here")
        result = matcher.matches(ctx)
        assert result.matched is False

    def test_or_mixed_filename_content_regex(self, tmp_path: Path) -> None:
        """OR 含 FILENAME + 2 个 CONTENT REGEX → 仅 CONTENT REGEX 进复合组。"""
        path = tmp_path / "config.conf"
        path.write_text("", encoding="utf-8")
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.FILENAME,
                    mode=MatchMode.ENDSWITH,
                    pattern=".txt",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)
        assert len(matcher._composite_groups) == 1
        # FILENAME 子项（下标 0）不在复合组中
        assert 0 not in matcher._composite_child_indices
        ctx = _make_context(path, content="password=abc")
        result = matcher.matches(ctx)
        # FILENAME 不匹配（.conf != .txt）但 CONTENT 匹配 → OR 命中
        assert result.matched is True
        assert result.target == "content"

    def test_or_collects_all_matched_children(self, tmp_path: Path) -> None:
        """OR 复合组中多个子项命中时收集所有命中文本。"""
        path = tmp_path / "file.txt"
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "password=abc" in result.match_texts
        assert "api_key=xyz" in result.match_texts

    def test_composite_group_detail_format(self, tmp_path: Path) -> None:
        """复合组命中的 detail 格式与 _apply_regex 一致。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True
        # detail 由 " AND ".join 各子项的 "正则命中: {first_txt!r}" 组成
        assert "正则命中:" in result.detail
        assert " AND " in result.detail

    def test_composite_group_match_count(self, tmp_path: Path) -> None:
        """复合组命中时 match_count 为各子项非重叠出现次数之和。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=a\npassword=b\napi_key=x")
        result = matcher.matches(ctx)
        assert result.matched is True
        # password 出现 2 次 + api_key 出现 1 次 = 3
        assert result.match_count == 3

    def test_build_composite_groups_no_regex_children(self) -> None:
        """无 REGEX 子项时返回空列表。"""
        children = (
            build_matcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="abc")),
            build_matcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="x.txt")),
        )
        assert _build_content_composite_groups(children) == []

    def test_build_composite_groups_single_regex_child(self) -> None:
        """仅 1 个 REGEX 子项时不创建复合组（单子项无合并收益）。"""
        children = (
            build_matcher(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"abc",
                    case_sensitive=False,
                )
            ),
            build_matcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="x.txt")),
        )
        assert _build_content_composite_groups(children) == []

    def test_build_composite_groups_non_content_target(self) -> None:
        """FILENAME/PATH 目标的 REGEX 子项不进复合组。"""
        children = (
            build_matcher(
                LeafMatch(
                    target=MatchTarget.FILENAME,
                    mode=MatchMode.REGEX,
                    pattern=r"abc\d+",
                    case_sensitive=False,
                )
            ),
            build_matcher(
                LeafMatch(
                    target=MatchTarget.PATH,
                    mode=MatchMode.REGEX,
                    pattern=r"def\d+",
                    case_sensitive=False,
                )
            ),
        )
        assert _build_content_composite_groups(children) == []

    def test_evaluate_composite_group_no_compiled(self, tmp_path: Path) -> None:
        """compiled 为 None 时返回空字典（安全降级）。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        group = matcher._composite_groups[0]
        group.compiled = None  # 模拟编译失败
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        results = _evaluate_composite_group(group, ctx)
        assert results == {}

    def test_evaluate_composite_group_no_active_children(self, tmp_path: Path) -> None:
        """所有子项关键字均不在内容中 → 无活跃子项 → 返回空字典。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        group = matcher._composite_groups[0]
        ctx = _make_context(path, content="nothing relevant here")
        results = _evaluate_composite_group(group, ctx)
        assert results == {}

    def test_content_lower_cached(self, tmp_path: Path) -> None:
        """MatchContext.content_lower 懒加载并缓存，多次访问返回同一对象。"""
        path = tmp_path / "file.txt"
        ctx = _make_context(path, content="Hello World")
        # 首次访问触发计算
        lower1 = ctx.content_lower
        assert lower1 == "hello world"
        # 再次访问返回缓存（同一对象）
        lower2 = ctx.content_lower
        assert lower2 is lower1

    def test_content_lower_reset(self, tmp_path: Path) -> None:
        """reset() 后 content_lower 缓存失效，重新计算。"""
        path = tmp_path / "file.txt"
        ctx = _make_context(path, content="Hello")
        assert ctx.content_lower == "hello"
        ctx.reset()
        # reset 后 _content_lower_loaded 为 False
        assert ctx._content_lower_loaded is False

    def test_and_preserves_child_order_in_detail(self, tmp_path: Path) -> None:
        """AND 复合组命中的 detail 按 children 原顺序拼接。"""
        path = tmp_path / "file.txt"
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"password\s*=\s*\S+",
                    case_sensitive=False,
                    description="密码赋值",
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=r"api_key\s*=\s*\S+",
                    case_sensitive=False,
                    description="API 密钥",
                ),
            )
        )
        matcher = build_matcher(spec)
        ctx = _make_context(path, content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True
        # detail 顺序：password 在前，api_key 在后
        detail_lower = result.detail.lower()
        assert detail_lower.index("password") < detail_lower.index("api_key")


class TestContentRegexPool:
    """跨规则 CONTENT REGEX 子项共享池测试。

    覆盖 :class:`ContentRegexPool` 的注册去重、编译、求值，以及
    :class:`AndMatcher` / :class:`OrMatcher` 注入池后 ``matches()`` 行为
    与未注入时的一致性。
    """

    def test_register_dedup_same_pattern_case(self) -> None:
        """相同 (pattern, case_sensitive) 注册返回同一 child_id。"""
        pool = ContentRegexPool()
        spec = LeafMatch(
            target=MatchTarget.CONTENT,
            mode=MatchMode.REGEX,
            pattern=r"password\s*=\s*\S+",
            case_sensitive=False,
        )
        id1 = pool.register(spec)
        id2 = pool.register(spec)
        assert id1 == id2

    def test_register_distinct_pattern_different_id(self) -> None:
        """不同 pattern 注册返回不同 child_id。"""
        pool = ContentRegexPool()
        id1 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password", case_sensitive=False)
        )
        id2 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key", case_sensitive=False)
        )
        assert id1 != id2

    def test_register_after_compile_raises(self) -> None:
        """compile 后再 register 触发断言。"""
        pool = ContentRegexPool()
        pool.compile()
        with pytest.raises(AssertionError):
            pool.register(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"x", case_sensitive=False)
            )

    def test_evaluate_returns_matched_children(self, tmp_path: Path) -> None:
        """evaluate 对命中子项返回 MatchResult，未命中不在结果中。"""
        pool = ContentRegexPool()
        id_pwd = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False)
        )
        id_api = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key=\S+", case_sensitive=False)
        )
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="password=abc")
        results = pool.evaluate(ctx)
        assert id_pwd in results
        assert results[id_pwd].matched is True
        assert "password=abc" in results[id_pwd].match_texts
        assert id_api not in results

    def test_evaluate_caches_per_context(self, tmp_path: Path) -> None:
        """同 context 多次 evaluate 只跑一次 finditer（结果缓存）。"""
        pool = ContentRegexPool()
        pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False)
        )
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="password=abc")
        r1 = pool.evaluate(ctx)
        r2 = pool.evaluate(ctx)
        assert r1 is r2  # 同一缓存字典对象

    def test_evaluate_prefilter_skips(self, tmp_path: Path) -> None:
        """预筛关键字均不出现时返回空结果（短路）。"""
        pool = ContentRegexPool()
        pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False)
        )
        pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key=\S+", case_sensitive=False)
        )
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="nothing relevant here")
        assert pool.evaluate(ctx) == {}

    def test_and_pool_matches_consistent_with_no_pool(self, tmp_path: Path) -> None:
        """AND 注入池后 matches() 与未注入结果一致（命中场景）。"""
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key=\S+", case_sensitive=False
                ),
            )
        )
        matcher_no_pool = build_matcher(spec)
        matcher_pool = build_matcher(spec)
        assert isinstance(matcher_pool, AndMatcher)
        pool = ContentRegexPool()
        matcher_pool.attach_pool(pool)
        pool.compile()
        ctx1 = _make_context(tmp_path / "f.txt", content="password=abc\napi_key=xyz")
        ctx2 = _make_context(tmp_path / "f.txt", content="password=abc\napi_key=xyz")
        r1 = matcher_no_pool.matches(ctx1)
        r2 = matcher_pool.matches(ctx2)
        assert r1.matched == r2.matched is True
        assert set(r1.match_texts) == set(r2.match_texts)

    def test_and_pool_partial_fail(self, tmp_path: Path) -> None:
        """AND 注入池后部分子项未命中 → 整体不命中。"""
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key=\S+", case_sensitive=False
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        pool = ContentRegexPool()
        matcher.attach_pool(pool)
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="password=abc")
        assert matcher.matches(ctx).matched is False

    def test_or_pool_matches_all_hits(self, tmp_path: Path) -> None:
        """OR 注入池后收集所有命中子项（不短路）。"""
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key=\S+", case_sensitive=False
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)
        pool = ContentRegexPool()
        matcher.attach_pool(pool)
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="password=abc\napi_key=xyz")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert "password=abc" in result.match_texts
        assert "api_key=xyz" in result.match_texts

    def test_or_pool_no_hit(self, tmp_path: Path) -> None:
        """OR 注入池后无任何子项命中 → 不命中。"""
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)
        pool = ContentRegexPool()
        matcher.attach_pool(pool)
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="nothing here")
        assert matcher.matches(ctx).matched is False

    def test_pool_shares_subitem_across_rules(self, tmp_path: Path) -> None:
        """多条 AND 规则引用同一子项 → 池去重，evaluate 只跑一次。"""
        shared = LeafMatch(
            target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
        )
        spec1 = AndMatch(
            children=(
                shared,
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"token=\S+", case_sensitive=False),
            )
        )
        spec2 = AndMatch(
            children=(
                shared,
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"secret=\S+", case_sensitive=False
                ),
            )
        )
        m1 = build_matcher(spec1)
        m2 = build_matcher(spec2)
        assert isinstance(m1, AndMatcher)
        assert isinstance(m2, AndMatcher)
        pool = ContentRegexPool()
        m1.attach_pool(pool)
        m2.attach_pool(pool)
        pool.compile()
        # shared 子项应去重为同一 child_id（_pool_child_ids 中两 matcher 的对应项相同）
        shared_id_m1 = m1._pool_child_ids[0]
        shared_id_m2 = m2._pool_child_ids[0]
        assert shared_id_m1 == shared_id_m2
        ctx = _make_context(tmp_path / "f.txt", content="password=abc\ntoken=xyz\nsecret=def")
        r1 = m1.matches(ctx)
        r2 = m2.matches(ctx)
        assert r1.matched is True
        assert r2.matched is True

    def test_and_pool_with_non_pooled_child(self, tmp_path: Path) -> None:
        """AND 含非 CONTENT REGEX 子项（如 FILENAME）时混合求值。"""
        spec = AndMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=".env", case_sensitive=False),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, AndMatcher)
        pool = ContentRegexPool()
        matcher.attach_pool(pool)
        pool.compile()
        # 只有 CONTENT REGEX 子项入池
        assert 0 in matcher._pool_child_ids
        assert 1 not in matcher._pool_child_ids
        ctx = _make_context(tmp_path / ".env", content="password=abc")
        assert matcher.matches(ctx).matched is True

    def test_pool_case_sensitive_group(self, tmp_path: Path) -> None:
        """case_sensitive=True 的子项独立成组并正确命中。"""
        pool = ContentRegexPool()
        id1 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"Password", case_sensitive=True)
        )
        id2 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"APIKey", case_sensitive=True)
        )
        pool.compile()
        ctx = _make_context(tmp_path / "f.txt", content="Password field\nAPIKey here")
        results = pool.evaluate(ctx)
        assert id1 in results and id2 in results
        # case_sensitive=True → 不匹配小写
        ctx_lower = _make_context(tmp_path / "f.txt", content="password field")
        assert pool.evaluate(ctx_lower) == {}

    def test_pool_compile_failure_fallback(self, tmp_path: Path) -> None:
        """复合正则编译失败时组丢弃，is_compiled 返回 False。

        直接在 pool 层面注入无效 pattern（绕过 build_matcher 的编译检查），
        验证 compile 不抛异常且组被标记为未编译。
        """
        pool = ContentRegexPool()
        id1 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password", case_sensitive=False)
        )
        id2 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"api_key", case_sensitive=False)
        )
        # 破坏 specs 使复合正则编译失败：注入未闭合的分组
        for group in pool._groups.values():
            group.specs_by_child_id[id1] = LeafMatch(
                target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"(?P<bad", case_sensitive=False
            )
        pool.compile()
        # 编译失败 → is_compiled 返回 False
        assert pool.is_compiled(id1) is False
        assert pool.is_compiled(id2) is False
        # evaluate 返回空（组被丢弃）
        ctx = _make_context(tmp_path / "f.txt", content="password=abc")
        assert pool.evaluate(ctx) == {}

    def test_pool_single_child_group_not_compiled(self) -> None:
        """单子项组（len < 2）跳过编译，is_compiled 返回 False。"""
        pool = ContentRegexPool()
        id1 = pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password", case_sensitive=False)
        )
        pool.compile()
        # 单子项组 → 未编译 → is_compiled=False
        assert pool.is_compiled(id1) is False

    def test_or_pool_with_non_pooled_child_sets_target(self, tmp_path: Path) -> None:
        """OR 含非池化 FILENAME 子项命中时设置 first_target。"""
        spec = OrMatch(
            children=(
                LeafMatch(
                    target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"password=\S+", case_sensitive=False
                ),
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=".env", case_sensitive=False),
            )
        )
        matcher = build_matcher(spec)
        assert isinstance(matcher, OrMatcher)
        pool = ContentRegexPool()
        matcher.attach_pool(pool)
        pool.compile()
        ctx = _make_context(tmp_path / ".env", content="nothing")
        result = matcher.matches(ctx)
        assert result.matched is True
        # FILENAME 子项命中 → first_target 为 filename
        assert result.target == "filename"

    def test_pool_inline_ignorecase_flag(self, tmp_path: Path) -> None:
        """内联 (?i) 标志的子项 → prefilter_case_insensitive=True。"""
        pool = ContentRegexPool()
        pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"(?i)Password", case_sensitive=True)
        )
        pool.register(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"(?i)APIKey", case_sensitive=True)
        )
        pool.compile()
        # case_sensitive=True 但内联 (?i) → 大小写不敏感匹配
        ctx = _make_context(tmp_path / "f.txt", content="password here")
        results = pool.evaluate(ctx)
        assert len(results) == 1
