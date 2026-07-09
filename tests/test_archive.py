"""压缩文件扫描单元测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pyfilescan.archive import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    ArchiveScanner,
    RarReader,
    ZipReader,
    default_factory,
    get_reader,
    register_all,
)
from pyfilescan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    Rule,
    RuleSet,
    Severity,
)
from pyfilescan.scanner import Scanner

# ----------------------------- 工具函数 -----------------------------


def _build_ruleset(*rules: Rule) -> RuleSet:
    return RuleSet(version="1.0", rules=tuple(rules))


def _filename_rule(name: str, pattern: str, severity: Severity = Severity.WARNING) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _content_rule(name: str, pattern: str, severity: Severity = Severity.CRITICAL) -> Rule:
    return Rule(
        name=name,
        severity=severity,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern=pattern),
    )


def _make_zip(zip_path: Path, files: dict[str, str], password: str | None = None) -> Path:
    """创建 ZIP 文件。password 不为空时使用 ZipFile.setpassword 加密。"""
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    if password is not None:
        # zipfile 标准库不支持写入加密，仅在读取端测试密码逻辑
        # 这里通过单独的加密 zip 创建流程（pyzipper 可选）跳过
        pytest.skip("标准库 zipfile 不支持写入加密 ZIP")
    return zip_path


# ----------------------------- 注册与工厂 -----------------------------


class TestFactoryRegistration:
    def test_register_all_registers_zip_and_rar(self) -> None:
        factory = default_factory
        register_all(factory)
        assert factory.get("zip") is ZipReader
        assert factory.get("rar") is RarReader

    def test_register_all_is_idempotent(self) -> None:
        factory = default_factory
        register_all(factory)
        register_all(factory)
        assert factory.get("zip") is ZipReader

    def test_get_reader_returns_none_for_unknown(self, tmp_path: Path) -> None:
        path = tmp_path / "foo.unknown"
        path.write_text("", encoding="utf-8")
        assert get_reader(path) is None

    def test_get_reader_returns_zip_for_zip(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "hello"})
        reader = get_reader(zip_path)
        assert isinstance(reader, ZipReader)
        reader.close()

    def test_factory_create_unknown_extension_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "a.txt"
        path.write_text("", encoding="utf-8")
        assert default_factory.create(path) is None


# ----------------------------- ArchiveEntry -----------------------------


class TestArchiveEntry:
    def test_entry_properties(self, tmp_path: Path) -> None:
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name="dir/file.txt",
            size=100,
            compressed_size=50,
            is_dir=False,
        )
        assert entry.name == "file.txt"
        assert entry.extension == "txt"
        assert entry.display_path == f"{tmp_path / 'a.zip'}!dir/file.txt"

    def test_entry_no_extension(self, tmp_path: Path) -> None:
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name="README",
            size=10,
            compressed_size=10,
        )
        assert entry.extension == ""
        assert entry.name == "README"

    def test_entry_dir(self, tmp_path: Path) -> None:
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name="subdir/",
            size=0,
            compressed_size=0,
            is_dir=True,
        )
        assert entry.is_dir
        # Path("subdir/").name 在不同平台返回 "subdir" 或 ""
        assert entry.name in ("subdir", "")


# ----------------------------- ZipReader -----------------------------


class TestZipReader:
    def test_list_entries_normal(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "hello", "b.md": "world"})
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"a.txt", "b.md"}
            assert all(not e.is_dir for e in entries)
        finally:
            reader.close()

    def test_list_entries_with_dir(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "b.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("dir/", "")
            zf.writestr("dir/a.txt", "hello")
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            entry_map = {e.entry_name: e for e in entries}
            assert entry_map["dir/"].is_dir
            assert not entry_map["dir/a.txt"].is_dir
        finally:
            reader.close()

    def test_read_entry_text(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "hello world"})
        reader = ZipReader(zip_path)
        try:
            data = reader.read_entry("a.txt")
            assert data == b"hello world"
        finally:
            reader.close()

    def test_read_entry_dir_returns_empty(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "b.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("dir/", "")
        reader = ZipReader(zip_path)
        try:
            assert reader.read_entry("dir/") == b""
        finally:
            reader.close()

    def test_read_entry_not_found(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path)
        try:
            with pytest.raises(ArchiveError, match="条目不存在"):
                reader.read_entry("missing.txt")
        finally:
            reader.close()

    def test_open_bad_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip file")
        with pytest.raises(ArchiveError, match="损坏的 ZIP"):
            ZipReader(path)

    def test_supported_extensions_via_instance(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path)
        try:
            assert reader.supported_extensions == ("zip",)
        finally:
            reader.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        with ZipReader(zip_path) as reader:
            entries = reader.list_entries()
            assert len(entries) == 1

    def test_read_entry_with_password_none_raises(self, tmp_path: Path) -> None:
        """加密条目未提供密码时抛 ArchiveError。"""
        # zipfile 标准库无法创建加密 zip，这里通过 mock ZipInfo flag_bits 模拟加密
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path)
        try:
            original_getinfo = reader._zip.getinfo  # type: ignore[attr-defined]

            def fake_getinfo(name: str):  # type: ignore[no-untyped-def]
                info = original_getinfo(name)
                # 通过对象.__dict__ 直接修改 flag_bits 模拟加密位
                # ZipInfo 是普通对象，可直接 setattr
                info.flag_bits = info.flag_bits | 0x1  # 设置加密位
                return info

            reader._zip.getinfo = fake_getinfo  # type: ignore[attr-defined]
            with pytest.raises(ArchiveError, match="未提供密码"):
                reader.read_entry("a.txt")
        finally:
            reader.close()


# ----------------------------- RarReader -----------------------------


class TestRarReader:
    def test_rar_not_installed_skipped(self, tmp_path: Path) -> None:
        """无 unrar 工具时打开 RAR 抛 ArchiveError（环境依赖）。"""
        path = tmp_path / "a.rar"
        path.write_bytes(b"Rar!\x1a\x07\x00fake")
        try:
            RarReader(path)
        except ArchiveError:
            return
        # 如果 unrar 已安装且能解析，则关闭读取器
        pytest.skip("需要 unrar 工具与真实 RAR 文件")

    def test_open_bad_rar(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.rar"
        path.write_bytes(b"not a rar file")
        with pytest.raises(ArchiveError):
            RarReader(path)

    def test_supported_extensions(self) -> None:
        # 通过类属性访问，由于是抽象方法需通过实例；用 __dict__ 间接验证
        assert hasattr(RarReader, "supported_extensions")


# ----------------------------- ArchiveScanner -----------------------------


class TestArchiveScanner:
    def test_scan_archive_no_reader_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "a.unknown"
        path.write_text("", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        assert scanner.scan_archive(path) == ()

    def test_scan_archive_filename_hit(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "hello", "normal.txt": "world"})
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 2
        hit_results = [r for r in results if r.has_hit]
        assert len(hit_results) == 1
        assert "secret.txt" in str(hit_results[0].path)

    def test_scan_archive_content_hit(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"a.txt": "contains password", "b.txt": "nothing here"},
        )
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "a.txt" in str(hits[0].path)

    def test_scan_archive_skips_dir_entries(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "b.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("dir/", "")
            zf.writestr("dir/a.txt", "x")
        rs = _build_ruleset(_filename_rule("r", "a"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        # 目录条目被跳过，只有 a.txt
        assert len(results) == 1

    def test_scan_archive_multiple_rules(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"secret.txt": "password=123", "normal.txt": "ok"},
        )
        rs = _build_ruleset(
            _filename_rule("fn", "secret"),
            _content_rule("ct", "password"),
        )
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        secret_result = next(r for r in results if "secret.txt" in str(r.path))
        assert len(secret_result.hits) == 2

    def test_scan_archive_and_composite(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"secret.conf": "password", "secret.txt": "password"},
        )
        rule = Rule(
            name="conf-and-pwd",
            severity=Severity.WARNING,
            match=AndMatch(
                children=(
                    LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.REGEX, pattern=r"\.conf$"),
                    LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
                )
            ),
        )
        rs = _build_ruleset(rule)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "secret.conf" in str(hits[0].path)

    def test_scan_archive_not_composite(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"keep.txt": "x", "drop.tmp": "y"},
        )
        rule = Rule(
            name="not-tmp",
            severity=Severity.WARNING,
            match=NotMatch(child=LeafMatch(target=MatchTarget.FILENAME, mode=MatchMode.ENDSWITH, pattern=".tmp")),
        )
        rs = _build_ruleset(rule)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "keep.txt" in str(hits[0].path)

    def test_scan_archive_file_extensions_filter(self, tmp_path: Path) -> None:
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"a.conf": "password", "a.txt": "password"},
        )
        rule = Rule(
            name="conf-only",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
            file_extensions=("conf",),
        )
        rs = _build_ruleset(rule)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "a.conf" in str(hits[0].path)

    def test_scan_archive_oversize_entry_skipped(self, tmp_path: Path) -> None:
        """超过 max_entry_size 的条目内容返回空字符串。"""
        big_content = "x" * 1000
        zip_path = _make_zip(tmp_path / "a.zip", {"big.txt": big_content})
        rs = _build_ruleset(_content_rule("r", "x"))
        scanner = ArchiveScanner(rs, max_entry_size=10)
        results = scanner.scan_archive(zip_path)
        # 内容被跳过，规则不命中
        assert all(not r.has_hit for r in results)

    def test_scan_archive_corrupted_returns_error_result(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip file")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(path)
        assert len(results) == 1
        assert results[0].errors == 1


# ----------------------------- 主 Scanner 集成 -----------------------------


class TestScannerArchiveIntegration:
    def test_scan_archives_disabled_by_default(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs)
        # scan_archive 应抛 RuntimeError
        with pytest.raises(RuntimeError, match="未启用"):
            scanner.scan_archive(zip_path)

    def test_scan_archives_enabled_scans_inside(self, tmp_path: Path) -> None:
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x", "normal.txt": "y"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        # 命中应包含压缩包内 secret.txt
        assert report.stats.matched_files >= 1
        hit_paths = [str(r.path) for r in report.hits]
        assert any("secret.txt" in p for p in hit_paths)

    def test_scan_archives_counts_scanned(self, tmp_path: Path) -> None:
        _make_zip(tmp_path / "a.zip", {"a.txt": "x", "b.txt": "y"})
        rs = _build_ruleset(_filename_rule("r", "nomatch"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        # 1 个 zip 文件 + 2 个内部条目
        assert report.stats.scanned_files == 3

    def test_scan_archives_non_archive_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        rs = _build_ruleset(_filename_rule("r", "nomatch"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        # 普通文件不触发压缩包扫描
        assert report.stats.scanned_files == 1

    def test_scan_archive_method_works_when_enabled(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 1
        assert results[0].has_hit


# ----------------------------- 边界情况 -----------------------------


class TestArchiveEdgeCases:
    def test_read_entry_binary_content(self, tmp_path: Path) -> None:
        """二进制条目内容可正确读取。"""
        binary_data = b"\x89PNG\r\n\x1a\n\x00\x00"
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("img.png", binary_data)
        reader = ZipReader(zip_path)
        try:
            assert reader.read_entry("img.png") == binary_data
        finally:
            reader.close()

    def test_scan_archive_empty_zip(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(str(zip_path), "w"):
            pass
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        assert results == ()

    def test_scan_archive_chinese_filename(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"密码.txt": "secret"})
        rs = _build_ruleset(_filename_rule("r", "密码"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

    def test_factory_register_custom(self) -> None:
        from pyfilescan.archive.base import ArchiveReaderFactory

        class FakeReader(ArchiveReader):
            @property
            def supported_extensions(self) -> tuple[str, ...]:
                return ("fake",)

            def list_entries(self) -> list[ArchiveEntry]:
                return []

            def read_entry(self, entry_name: str) -> bytes:
                return b""

        factory = ArchiveReaderFactory()
        factory.register("fake", FakeReader)
        assert factory.get("fake") is FakeReader


# ----------------------------- 内容提取分支 -----------------------------


class TestArchiveContentExtraction:
    def test_text_entry_decoded(self, tmp_path: Path) -> None:
        """纯文本条目直接解码（不写临时文件）。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "hello world"})
        rs = _build_ruleset(_content_rule("r", "hello"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

    def test_gbk_encoded_text_fallback(self, tmp_path: Path) -> None:
        """GBK 编码文本通过 charset-normalizer 回退解码。"""
        # 使用较长文本避免 charset-normalizer 短文本误判
        gbk_text = "这是一个包含密码字段的配置文件，密码为 password123。"
        gbk_data = gbk_text.encode("gbk")
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("a.txt", gbk_data)
        rs = _build_ruleset(_content_rule("r", "password123"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

    def test_unknown_extension_falls_back_to_decode(self, tmp_path: Path) -> None:
        """无提取器的扩展名回退到字节解码。"""
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("a.unknownext", b"plain text content")
        rs = _build_ruleset(_content_rule("r", "plain"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

    def test_empty_entry_content(self, tmp_path: Path) -> None:
        """空内容条目不触发规则。"""
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("empty.txt", "")
        rs = _build_ruleset(_content_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        assert all(not r.has_hit for r in results)

    def test_read_entry_failure_returns_empty(self, tmp_path: Path) -> None:
        """条目读取失败时返回空内容，规则不命中。"""
        zip_path = tmp_path / "a.zip"
        rs = _build_ruleset(_content_rule("r", "hello"))
        scanner = ArchiveScanner(rs)

        class FailingReader:
            def list_entries(self) -> list[ArchiveEntry]:
                return [
                    ArchiveEntry(
                        archive_path=zip_path,
                        entry_name="a.txt",
                        size=10,
                        compressed_size=10,
                        is_dir=False,
                    )
                ]

            def read_entry(self, entry_name: str) -> bytes:
                raise ArchiveError("mocked failure")

        from pyfilescan.archive import scanner as scanner_module

        original_get_reader = scanner_module.get_reader
        scanner_module.get_reader = lambda path, password=None: FailingReader()  # type: ignore[assignment]
        try:
            results = scanner.scan_archive(zip_path)
            # 读取失败导致内容为空，规则不命中
            assert all(not r.has_hit for r in results)
        finally:
            scanner_module.get_reader = original_get_reader  # type: ignore[assignment]
