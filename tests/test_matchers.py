"""匹配器单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from uniscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    OrMatch,
)
from uniscan.scanner.context import FileEntry, MatchContext
from uniscan.scanner.matchers import (
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
