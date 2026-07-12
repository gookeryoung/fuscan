"""压缩文件扫描单元测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from typing_extensions import override

from fuscan.archive import (
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
from fuscan.rules.model import (
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    NotMatch,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner

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
    def test_open_bad_rar(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.rar"
        path.write_bytes(b"not a rar file")
        with pytest.raises(ArchiveError):
            RarReader(path)

    def test_supported_extensions(self) -> None:
        # 通过类属性访问，由于是抽象方法需通过实例；用 __dict__ 间接验证
        assert hasattr(RarReader, "supported_extensions")


class TestRarReaderMocked:
    """通过 mock rarfile 模块覆盖 RarReader 各分支。"""

    def test_init_bad_rar_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rarfile.BadRarFile 应转为 ArchiveError。"""
        import rarfile

        def raise_bad_rar(path: str):
            raise rarfile.BadRarFile("损坏")

        monkeypatch.setattr(rarfile, "RarFile", raise_bad_rar)
        with pytest.raises(ArchiveError, match="损坏的 RAR"):
            RarReader(tmp_path / "a.rar")

    def test_init_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError 应转为 ArchiveError。"""
        import rarfile

        def raise_os_error(path: str):
            raise OSError("权限拒绝")

        monkeypatch.setattr(rarfile, "RarFile", raise_os_error)
        with pytest.raises(ArchiveError, match="无法打开 RAR"):
            RarReader(tmp_path / "a.rar")

    def test_init_generic_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """其他异常（如 unrar 缺失）应转为 ArchiveError。"""
        import rarfile

        def raise_generic(path: str):
            raise RuntimeError("unrar not found")

        monkeypatch.setattr(rarfile, "RarFile", raise_generic)
        with pytest.raises(ArchiveError, match="可能缺少 unrar"):
            RarReader(tmp_path / "a.rar")

    def test_init_import_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rarfile 导入失败应抛 ArchiveError。"""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "rarfile":
                raise ImportError("No module named 'rarfile'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ArchiveError, match="rarfile 库未安装"):
            RarReader(tmp_path / "a.rar")

    def _make_mocked_reader(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rar_mock: object) -> RarReader:
        """构造带 mock _rar 的 RarReader 实例，绕过 __init__。"""
        import rarfile

        monkeypatch.setattr(rarfile, "RarFile", lambda path: rar_mock)
        reader = RarReader.__new__(RarReader)
        reader._path = tmp_path / "a.rar"  # type: ignore[attr-defined]
        reader._password = None  # type: ignore[attr-defined]
        reader._rar = rar_mock  # type: ignore[attr-defined]
        return reader

    def test_list_entries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_entries 应返回所有条目。"""

        class FakeInfo:
            def __init__(self, name: str, size: int, isdir: bool = False) -> None:
                self.filename = name
                self.file_size = size
                self.compress_size = size // 2
                self.isdir = isdir

        class FakeRar:
            def infolist(self):
                return [FakeInfo("a.txt", 100), FakeInfo("dir/", 0, isdir=True)]

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        entries = reader.list_entries()
        assert len(entries) == 2
        assert entries[0].entry_name == "a.txt"
        assert entries[0].size == 100
        assert entries[1].is_dir

    def test_read_entry_dir_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """目录条目返回空字节。"""

        class FakeInfo:
            isdir = True
            needs_password = False

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        assert reader.read_entry("dir/") == b""

    def test_read_entry_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """条目不存在时抛 ArchiveError。"""

        class FakeRar:
            def getinfo(self, name: str):
                raise KeyError(name)

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="条目不存在"):
            reader.read_entry("missing.txt")

    def test_read_entry_getinfo_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """getinfo 抛异常时转为 ArchiveError。"""

        class FakeRar:
            def getinfo(self, name: str):
                raise RuntimeError("模拟失败")

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="获取 RAR 条目信息失败"):
            reader.read_entry("a.txt")

    def test_read_entry_encrypted_no_password(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """加密条目未提供密码时抛 ArchiveError。"""

        class FakeInfo:
            isdir = False
            needs_password = True

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="未提供密码"):
            reader.read_entry("secret.txt")

    def test_read_entry_with_password(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """有密码的加密条目应通过 pwd 参数读取。"""

        class FakeInfo:
            isdir = False
            needs_password = True

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def read(self, name: str, pwd: str | None = None):
                assert pwd is not None
                return b"decrypted content"

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        reader._password = "secret"  # type: ignore[attr-defined]
        assert reader.read_entry("a.txt") == b"decrypted content"

    def test_read_entry_normal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非加密条目直接读取。"""

        class FakeInfo:
            isdir = False
            needs_password = False

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def read(self, name: str, pwd: str | None = None):
                return b"content"

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        assert reader.read_entry("a.txt") == b"content"

    def test_read_entry_password_required(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read 抛 PasswordRequired 时转为 ArchiveError。"""
        import rarfile

        class FakeInfo:
            isdir = False
            needs_password = False

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def read(self, name: str, pwd: str | None = None):
                raise rarfile.PasswordRequired("需要密码")

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="需要密码"):
            reader.read_entry("a.txt")

    def test_read_entry_bad_rar(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read 抛 BadRarFile 时转为 ArchiveError。"""
        import rarfile

        class FakeInfo:
            isdir = False
            needs_password = False

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def read(self, name: str, pwd: str | None = None):
                raise rarfile.BadRarFile("损坏")

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="条目损坏"):
            reader.read_entry("a.txt")

    def test_read_entry_generic_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read 抛其他异常时转为 ArchiveError。"""

        class FakeInfo:
            isdir = False
            needs_password = False

        class FakeRar:
            def getinfo(self, name: str):
                return FakeInfo()

            def read(self, name: str, pwd: str | None = None):
                raise OSError("模拟 IO 错误")

            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with pytest.raises(ArchiveError, match="条目读取失败"):
            reader.read_entry("a.txt")

    def test_close(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """close 应调用 _rar.close()。"""
        called = {"close": False}

        class FakeRar:
            def close(self) -> None:
                called["close"] = True

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        reader.close()
        assert called["close"] is True

    def test_context_manager(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """上下文管理器应正常工作。"""
        called = {"close": False}

        class FakeRar:
            def close(self) -> None:
                called["close"] = True

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        with reader as r:
            assert r is reader
        assert called["close"] is True

    def test_supported_extensions_via_instance(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过实例访问 supported_extensions。"""

        class FakeRar:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeRar())
        assert reader.supported_extensions == ("rar",)


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


class TestArchiveScannerCache:
    """压缩包缓存模式测试。"""

    def test_cache_hit_reuses_result(self, tmp_path: Path) -> None:
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "password=abc"})
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            cache.register_ruleset(rs)
            scanner1 = ArchiveScanner(rs, cache=cache)
            results1 = scanner1.scan_archive(zip_path)
            assert len(results1) == 1
            assert results1[0].has_hit

            # 第二次扫描应命中缓存
            scanner2 = ArchiveScanner(rs, cache=cache)
            results2 = scanner2.scan_archive(zip_path)
            assert len(results2) == 1
            assert results2[0].has_hit
            assert results2[0].hits[0].rule_name == "pwd"
        finally:
            cache.close()

    def test_cache_miss_writes_result(self, tmp_path: Path) -> None:
        from fuscan.cache import CacheStore, hash_bytes

        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "password"})
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            cache.register_ruleset(rs)
            scanner = ArchiveScanner(rs, cache=cache)
            scanner.scan_archive(zip_path)

            rule_hashes = cache.get_rule_hashes()
            file_hash = hash_bytes(b"password")
            cached = cache.get_cached_hits(file_hash, list(rule_hashes.values()))
            assert len(cached) == 1
            assert next(iter(cached.values())) is not None
        finally:
            cache.close()

    def test_content_change_triggers_rescan(self, tmp_path: Path) -> None:
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "password=old"})
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            cache.register_ruleset(rs)
            scanner1 = ArchiveScanner(rs, cache=cache)
            results1 = scanner1.scan_archive(zip_path)
            assert results1[0].has_hit
            assert results1[0].hits[0].match_count == 1

            # 修改压缩包内容
            _make_zip(zip_path, {"a.txt": "password=new\npassword=again"})
            scanner2 = ArchiveScanner(rs, cache=cache)
            results2 = scanner2.scan_archive(zip_path)
            assert results2[0].has_hit
            assert results2[0].hits[0].match_count == 2
        finally:
            cache.close()

    def test_uncached_mode_unchanged(self, tmp_path: Path) -> None:
        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "password"})
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = ArchiveScanner(rs)  # 不传 cache
        assert scanner._cache is None
        results = scanner.scan_archive(zip_path)
        assert len(results) == 1
        assert results[0].has_hit

    def test_cache_none_hit_not_returned(self, tmp_path: Path) -> None:
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"clean.txt": "nothing suspicious"})
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            cache.register_ruleset(rs)
            scanner1 = ArchiveScanner(rs, cache=cache)
            results1 = scanner1.scan_archive(zip_path)
            assert all(not r.has_hit for r in results1)

            scanner2 = ArchiveScanner(rs, cache=cache)
            results2 = scanner2.scan_archive(zip_path)
            assert all(not r.has_hit for r in results2)
        finally:
            cache.close()

    def test_scanner_with_archive_cache(self, tmp_path: Path) -> None:
        """主 Scanner 启用 cache + scan_archives 时压缩包内条目应缓存。"""
        from fuscan.cache import CacheStore

        _make_zip(tmp_path / "a.zip", {"secret.txt": "password"})
        rs = _build_ruleset(_content_rule("pwd", "password"))

        cache_path = tmp_path / "cache.db"
        cache = CacheStore(cache_path)
        try:
            scanner1 = Scanner(rs, scan_archives=True, cache=cache)
            report1 = scanner1.scan(tmp_path)
            assert report1.stats.matched_files >= 1

            # 第二次扫描应命中缓存
            scanner2 = Scanner(rs, scan_archives=True, cache=cache)
            report2 = scanner2.scan(tmp_path)
            assert report2.stats.matched_files >= 1
        finally:
            cache.close()


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
        from fuscan.archive.base import ArchiveReaderFactory

        class FakeReader(ArchiveReader):
            @property
            @override
            def supported_extensions(self) -> tuple[str, ...]:
                return ("fake",)

            @override
            def list_entries(self) -> list[ArchiveEntry]:
                return []

            @override
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

        from fuscan.archive import scanner as scanner_module

        original_get_reader = scanner_module.get_reader
        scanner_module.get_reader = lambda path, password=None: FailingReader()  # type: ignore[assignment]
        try:
            results = scanner.scan_archive(zip_path)
            # 读取失败导致内容为空，规则不命中
            assert all(not r.has_hit for r in results)
        finally:
            scanner_module.get_reader = original_get_reader  # type: ignore[assignment]


# ----------------------------- ZipReader 异常路径 -----------------------------


class TestZipReaderErrorPaths:
    """ZipReader 异常路径覆盖。"""

    def test_open_os_error_raises_archive_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ZipFile 打开时抛 OSError 应转为 ArchiveError。"""
        import zipfile

        path = tmp_path / "a.zip"
        path.write_bytes(b"fake")

        original_zipfile = zipfile.ZipFile

        def fake_zipfile(file: str, mode: str = "r"):
            if file == str(path):
                raise OSError("模拟权限拒绝")
            return original_zipfile(file, mode)

        monkeypatch.setattr(zipfile, "ZipFile", fake_zipfile)
        with pytest.raises(ArchiveError, match="无法打开 ZIP 文件"):
            ZipReader(path)

    def test_read_entry_encrypted_wrong_password(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """加密条目密码错误时抛 ArchiveError。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path, password="wrong")
        try:
            original_getinfo = reader._zip.getinfo  # type: ignore[attr-defined]

            def fake_getinfo(name: str):  # type: ignore[no-untyped-def]
                info = original_getinfo(name)
                info.flag_bits = info.flag_bits | 0x1  # 设置加密位
                return info

            def fake_read(name: str, pwd: bytes | None = None):  # type: ignore[no-untyped-def]
                raise RuntimeError("Bad password for file")

            reader._zip.getinfo = fake_getinfo  # type: ignore[attr-defined]
            reader._zip.read = fake_read  # type: ignore[attr-defined]
            with pytest.raises(ArchiveError, match="密码错误或解密失败"):
                reader.read_entry("a.txt")
        finally:
            reader.close()

    def test_read_entry_runtime_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非加密条目读取 RuntimeError 时抛 ArchiveError。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path)
        try:

            def fake_read(name: str, pwd: bytes | None = None):  # type: ignore[no-untyped-def]
                raise RuntimeError("模拟读取失败")

            reader._zip.read = fake_read  # type: ignore[attr-defined]
            with pytest.raises(ArchiveError, match="ZIP 条目读取失败"):
                reader.read_entry("a.txt")
        finally:
            reader.close()

    def test_read_entry_bad_zip_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非加密条目读取 BadZipFile 时抛 ArchiveError。"""
        import zipfile

        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
        reader = ZipReader(zip_path)
        try:

            def fake_read(name: str, pwd: bytes | None = None):  # type: ignore[no-untyped-def]
                raise zipfile.BadZipFile("模拟损坏")

            reader._zip.read = fake_read  # type: ignore[attr-defined]
            with pytest.raises(ArchiveError, match="ZIP 条目损坏"):
                reader.read_entry("a.txt")
        finally:
            reader.close()


# ----------------------------- ArchiveScanner 异常路径 -----------------------------


class TestArchiveScannerErrorPaths:
    """ArchiveScanner 异常路径覆盖。"""

    def test_list_entries_failure_returns_error_result(self, tmp_path: Path) -> None:
        """list_entries 抛 ArchiveError 时返回单条错误结果。"""
        zip_path = tmp_path / "a.zip"
        zip_path.write_bytes(b"fake")

        class FailingListReader:
            def list_entries(self) -> list[ArchiveEntry]:
                raise ArchiveError("列出条目失败")

            def close(self) -> None:
                pass

        from fuscan.archive import scanner as scanner_module

        original_get_reader = scanner_module.get_reader
        scanner_module.get_reader = lambda path, password=None: FailingListReader()  # type: ignore[assignment]
        try:
            rs = _build_ruleset(_filename_rule("r", "x"))
            scanner = ArchiveScanner(rs)
            results = scanner.scan_archive(zip_path)
            assert len(results) == 1
            assert results[0].errors == 1
        finally:
            scanner_module.get_reader = original_get_reader  # type: ignore[assignment]

    def test_matcher_exception_increments_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """matcher.matches 抛异常时 rule_errors 递增。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"a.txt": "hello"})
        rs = _build_ruleset(_content_rule("r", "hello"))

        from fuscan.scanner.matchers import Matcher

        # 包装 build_matcher 返回会抛异常的 matcher
        class FailingMatcher(Matcher):
            def matches(self, context):  # type: ignore[no-untyped-def]
                raise RuntimeError("模拟匹配失败")

        import fuscan.archive.scanner as scanner_mod

        monkeypatch.setattr(scanner_mod, "build_matcher", lambda match: FailingMatcher())

        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 1
        assert results[0].errors == 1
        assert not results[0].has_hit

    def test_extract_via_temp_with_docx(self, tmp_path: Path) -> None:
        """有注册提取器的格式（.docx）走临时文件提取路径。"""
        from docx import Document

        doc = Document()
        doc.add_paragraph("docx 内的 password")
        docx_bytes = b""
        import io

        buf = io.BytesIO()
        doc.save(buf)
        docx_bytes = buf.getvalue()

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("inner.docx", docx_bytes)

        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "inner.docx" in str(hits[0].path)

    def test_extract_via_temp_failure_falls_back_to_decode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """临时文件提取失败时回退到字节解码。"""
        # 创建一个 .docx 条目但让 extract_content 抛异常
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("inner.docx", b"PK\x03\x04 corrupted docx with password")

        rs = _build_ruleset(_content_rule("r", "password"))

        import fuscan.archive.scanner as scanner_mod

        def fake_extract(path: Path) -> str:
            raise RuntimeError("模拟提取失败")

        monkeypatch.setattr(scanner_mod, "extract_content", fake_extract)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        # 提取失败回退到解码，password 明文在字节中应被命中
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

    def test_safe_unlink_permission_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """临时文件删除遇 PermissionError 时安全跳过。"""
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("inner.docx", b"PK\x03\x04 password")

        rs = _build_ruleset(_content_rule("r", "password"))

        original_unlink = Path.unlink
        call_count = {"n": 0}

        def mock_unlink(self: Path, missing_ok: bool = False) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise PermissionError("模拟文件锁定")
            original_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", mock_unlink)
        scanner = ArchiveScanner(rs)
        # 不应抛异常
        results = scanner.scan_archive(zip_path)
        assert isinstance(results, tuple)

    def test_decode_bytes_charset_normalizer_import_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_decode_bytes 中 charset_normalizer 导入失败时回退到 errors='ignore'。"""
        import builtins

        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "charset_normalizer":
                raise ImportError("No module named 'charset_normalizer'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        # 构造非 UTF-8 字节触发 _decode_bytes 的 except UnicodeDecodeError 分支
        gbk_data = "密码 password".encode("gbk")
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            # 使用 .unknown 扩展名，既不在 _TEXT_EXTENSIONS 也无提取器，走 _decode_bytes
            zf.writestr("a.unknownext", gbk_data)

        rs = _build_ruleset(_content_rule("r", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        # charset_normalizer 导入失败，UTF-8 解码也会失败（GBK 字节），
        # 回退到 errors='ignore'，部分明文 password 可能被截断
        # 但 GBK 的 ASCII 字符 password 仍能保留
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
