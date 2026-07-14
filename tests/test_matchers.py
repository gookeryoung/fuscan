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
    FileNameMatcher,
    Matcher,
    NotMatcherImpl,
    OrMatcher,
    PathMatcher,
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
        children = (
            FileNameMatcher(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$", case_sensitive=False)
            ),
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password", case_sensitive=False)
            ),
        )
        matcher = AndMatcher(children)
        ctx = _make_context(path, content="db_password=x")
        assert matcher.matches(ctx).matched is True

    def test_and_partial_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.txt"
        children = (
            FileNameMatcher(
                LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="doc.conf", case_sensitive=False)
            ),
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password", case_sensitive=False)
            ),
        )
        matcher = AndMatcher(children)
        ctx = _make_context(path, content="db_password=x")
        assert matcher.matches(ctx).matched is False

    def test_or_any_match(self, tmp_path: Path) -> None:
        path = tmp_path / "x.txt"
        children = (
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token", case_sensitive=False)
            ),
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key", case_sensitive=False)
            ),
        )
        matcher = OrMatcher(children)
        ctx = _make_context(path, content="has api_key here")
        assert matcher.matches(ctx).matched is True

    def test_or_none_match(self, tmp_path: Path) -> None:
        path = tmp_path / "x.txt"
        children = (
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token", case_sensitive=False)
            ),
            ContentMatcher(
                LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="api_key", case_sensitive=False)
            ),
        )
        matcher = OrMatcher(children)
        ctx = _make_context(path, content="nothing relevant")
        assert matcher.matches(ctx).matched is False

    def test_not_inverts(self, tmp_path: Path) -> None:
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        child = PathMatcher(
            LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup", case_sensitive=False)
        )
        matcher = NotMatcherImpl(child)
        ctx = _make_context(path)
        assert matcher.matches(ctx).matched is True  # path 不含 backup → not 命中

    def test_not_inverts_to_false(self, tmp_path: Path) -> None:
        path = tmp_path / "backup" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        child = PathMatcher(
            LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup", case_sensitive=False)
        )
        matcher = NotMatcherImpl(child)
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
        children = (
            FileNameMatcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="doc")),
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="pwd")),
        )
        matcher = AndMatcher(children)
        ctx = _make_context(path, content="db_pwd=x")
        results = matcher.match_all(ctx)
        assert len(results) == 2

    def test_or_match_all_collects_children(self, tmp_path: Path) -> None:
        """OrMatcher.match_all 应收集所有子匹配器的结果。"""
        path = tmp_path / "x.txt"
        children = (
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="token")),
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="key")),
        )
        matcher = OrMatcher(children)
        ctx = _make_context(path, content="has token here")
        results = matcher.match_all(ctx)
        assert len(results) == 2

    def test_and_matches_with_no_detail(self, tmp_path: Path) -> None:
        """AndMatcher 全部命中但无 detail 时返回默认"全部命中"。"""
        path = tmp_path / "x.txt"
        # EQUALS 模式命中时 detail 为"完全相等"，但如果都命中应合并 detail
        children = (FileNameMatcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.EQUALS, pattern="x.txt")),)
        matcher = AndMatcher(children)
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
        matcher = OrMatcher(())
        path = tmp_path / "x.txt"
        ctx = _make_context(path)
        results = matcher.match_all(ctx)
        assert results == []

    def test_and_matcher_match_all_empty(self, tmp_path: Path) -> None:
        """AndMatcher.match_all 无子匹配器时返回空列表。"""
        matcher = AndMatcher(())
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
        children = (
            FileNameMatcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$")),
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")),
        )
        matcher = AndMatcher(children)
        ctx = _make_context(path, content="db_password=x")
        result = matcher.matches(ctx)
        assert result.matched is True
        # 第一个子匹配器是 FileNameMatcher，regex 模式 match_text 为 m.group(0)
        assert result.match_text == ".conf"

    def test_or_matcher_passes_through_match_text(self, tmp_path: Path) -> None:
        """OrMatcher 应透传命中分支的 ``match_text``。"""
        path = tmp_path / "x.txt"
        children = (
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.REGEX, pattern=r"AKIA\d+")),
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing")),
        )
        matcher = OrMatcher(children)
        ctx = _make_context(path, content="key=AKIA12345")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_text == "AKIA12345"

    def test_not_matcher_has_empty_match_text(self, tmp_path: Path) -> None:
        """NotMatcher 命中时 ``match_text`` 应为空（无原始匹配文本）。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        child = PathMatcher(LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        matcher = NotMatcherImpl(child)
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
        children = (
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")),
            FileNameMatcher(LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.STARTSWITH, pattern="test_")),
        )
        matcher = AndMatcher(children)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3  # 2 + 1

    def test_or_matcher_uses_first_matched_count(self, tmp_path: Path) -> None:
        """OrMatcher 的 match_count 应为首个命中分支的 match_count。"""
        path = tmp_path / "file.txt"
        content = "password=abc\npassword=def\npassword=ghi"
        # 子1：内容包含 password（3 次），子2：内容包含 secret（0 次）
        children = (
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")),
            ContentMatcher(LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="secret")),
        )
        matcher = OrMatcher(children)
        ctx = _make_context(path, content=content)
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.match_count == 3

    def test_not_matcher_count_is_1(self, tmp_path: Path) -> None:
        """NotMatcher 命中时 match_count 固定为 1。"""
        path = tmp_path / "data" / "file.txt"
        path.parent.mkdir()
        path.write_text("", encoding="utf-8")
        child = PathMatcher(LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="backup"))
        matcher = NotMatcherImpl(child)
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
        filename_child = FileNameMatcher(
            LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password")
        )
        content_child = ContentMatcher(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="missing")
        )
        matcher = OrMatcher((filename_child, content_child))
        ctx = _make_context(path, "nothing here")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == "filename"

    def test_and_matcher_has_empty_target(self, tmp_path: Path) -> None:
        """AndMatcher 为组合规则，target 应为空字符串。"""
        path = tmp_path / "password.txt"
        path.write_text("password=123", encoding="utf-8")
        filename_child = FileNameMatcher(
            LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern="password")
        )
        content_child = ContentMatcher(
            LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password")
        )
        matcher = AndMatcher((filename_child, content_child))
        ctx = _make_context(path, "password=123")
        result = matcher.matches(ctx)
        assert result.matched is True
        assert result.target == ""


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
