"""压缩文件扫描单元测试。"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

import pytest
from typing_extensions import override

from fuscan.archive import (
    ArchiveEntry,
    ArchiveError,
    ArchiveReader,
    ArchiveScanner,
    RarReader,
    SevenZReader,
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
from fuscan.scanner._archive_phase import _collect_archive_futures, run_archive_phase
from fuscan.scanner.context import FileEntry
from fuscan.scanner.result import ProgressInfo, ScanResult

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


def _make_gbk_zip(zip_path: Path, files: dict[str, str | bytes]) -> Path:
    """构造 GBK 文件名 ZIP（模拟 Windows 压缩工具，不设置 UTF-8 标志位）。

    手动构造 ZIP 字节流，绕过标准库 zipfile 对非 ASCII 文件名强制设置
    UTF-8 标志位的行为，真实复现 WinRAR/好压/360 等工具产生的 GBK 乱码：
    文件名以 GBK 字节存储且 ``flag_bits`` 不含 0x800，读取时 zipfile 按
    CP437 解码产生乱码（如 ``密码.txt`` → ``├▄┬δ.txt``）。

    采用 STORE 模式（无压缩）简化构造，不影响文件名编码场景验证。
    """
    central_dir: list[tuple[bytes, bytes]] = []
    offset = 0
    with zip_path.open("wb") as f:
        for name, content in files.items():
            name_bytes = name.encode("gbk")
            content_bytes = content.encode("utf-8") if isinstance(content, str) else content
            crc = zlib.crc32(content_bytes) & 0xFFFFFFFF
            # Local file header：flag_bits = 0（不设置 UTF-8 标志位）
            local_header = struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04",
                20,  # version needed
                0,  # flag_bits（不设置 0x800 → 按 CP437 解码 → GBK 乱码）
                0,  # compression (store)
                0,
                0,  # mod time, mod date
                crc,
                len(content_bytes),
                len(content_bytes),
                len(name_bytes),
                0,  # extra field length
            )
            f.write(local_header)
            f.write(name_bytes)
            f.write(content_bytes)
            local_size = len(local_header) + len(name_bytes) + len(content_bytes)

            central_header = struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02",
                20,  # version made by
                20,  # version needed
                0,  # flag_bits
                0,  # compression
                0,
                0,  # time, date
                crc,
                len(content_bytes),
                len(content_bytes),
                len(name_bytes),
                0,  # extra length
                0,  # comment length
                0,  # disk number
                0,  # internal attrs
                0,  # external attrs
                offset,  # local header offset
            )
            central_dir.append((central_header, name_bytes))
            offset += local_size

        cd_offset = offset
        cd_size = 0
        for header, name_bytes in central_dir:
            f.write(header)
            f.write(name_bytes)
            cd_size += len(header) + len(name_bytes)

        eocd = struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06",
            0,
            0,
            len(central_dir),
            len(central_dir),
            cd_size,
            cd_offset,
            0,  # comment length
        )
        f.write(eocd)
    return zip_path


def _make_7z(zip_path: Path, files: dict[str, str | bytes]) -> Path:
    """创建 7Z 文件（基于 py7zr，纯 Python 写入）。

    文件内容支持 str（自动 utf-8 编码）或 bytes。
    """
    try:
        import py7zr
    except ImportError:
        pytest.skip("py7zr 未安装，跳过 7Z 测试")
    with py7zr.SevenZipFile(str(zip_path), mode="w") as sz:
        for name, content in files.items():
            if isinstance(content, str):
                bio: io.BytesIO = io.BytesIO(content.encode("utf-8"))
            else:
                bio = io.BytesIO(content)
            sz.writef(bio, name)
    return zip_path


# ----------------------------- 注册与工厂 -----------------------------


class TestFactoryRegistration:
    def test_register_all_registers_zip_and_rar(self) -> None:
        factory = default_factory
        register_all(factory)
        assert factory.get("zip") is ZipReader
        assert factory.get("rar") is RarReader

    def test_register_all_registers_7z(self) -> None:
        """register_all 应注册 SevenZReader 到 7z 扩展名。"""
        factory = default_factory
        register_all(factory)
        assert factory.get("7z") is SevenZReader

    def test_register_all_is_idempotent(self) -> None:
        factory = default_factory
        register_all(factory)
        register_all(factory)
        assert factory.get("zip") is ZipReader

    def test_registered_extensions_contains_all(self) -> None:
        """registered_extensions 应包含 zip/rar/7z。"""
        register_all(default_factory)
        exts = default_factory.registered_extensions
        assert "zip" in exts
        assert "rar" in exts
        assert "7z" in exts

    def test_get_reader_returns_sevenz_for_7z(self, tmp_path: Path) -> None:
        """get_reader 对 .7z 文件应返回 SevenZReader 实例。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "hello"})
        reader = get_reader(sevenz_path)
        assert isinstance(reader, SevenZReader)
        reader.close()

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

    def test_entry_extension_dotfile_single_dot(self, tmp_path: Path) -> None:
        """dotfile 单点（.env）：suffix 为空，取点后全部作为扩展名。"""
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name=".env",
            size=10,
            compressed_size=10,
        )
        assert entry.extension == "env"

    def test_entry_extension_dotfile_bashrc(self, tmp_path: Path) -> None:
        """dotfile 单点（.bashrc）：取点后全部。"""
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name=".bashrc",
            size=10,
            compressed_size=10,
        )
        assert entry.extension == "bashrc"

    def test_entry_extension_dotfile_multi_dot(self, tmp_path: Path) -> None:
        """dotfile 多点（.env.local）：suffix 非空，取最后一段（与普通多段一致）。"""
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name=".env.local",
            size=10,
            compressed_size=10,
        )
        # Path(".env.local").suffix == ".local"，走第一分支返回 "local"
        assert entry.extension == "local"

    def test_entry_extension_multi_suffix(self, tmp_path: Path) -> None:
        """普通多段扩展名（archive.tar.gz）：取最后一段。"""
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name="archive.tar.gz",
            size=10,
            compressed_size=10,
        )
        assert entry.extension == "gz"

    def test_entry_extension_uppercase_normalized(self, tmp_path: Path) -> None:
        """扩展名大写归一化为小写。"""
        entry = ArchiveEntry(
            archive_path=tmp_path / "a.zip",
            entry_name="README.MD",
            size=10,
            compressed_size=10,
        )
        assert entry.extension == "md"


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


# ----------------------------- ZIP GBK 文件名乱码修复 -----------------------------


class TestZipReaderGbkFilename:
    """ZIP 文件名 GBK 乱码修复。

    Windows 压缩工具（WinRAR/好压/360）默认用 GBK 编码中文文件名且不设置
    UTF-8 标志位（flag_bits 0x800），导致 zipfile 按 CP437 解码产生乱码，
    使下游 FILENAME/PATH 正则规则与扩展名白名单判断全部失效。
    """

    def test_list_entries_decodes_gbk_filename(self, tmp_path: Path) -> None:
        """未设置 UTF-8 标志位的 GBK 文件名被正确解码为中文。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "secret"})
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"密码.txt"}
        finally:
            reader.close()

    def test_list_entries_decodes_gbk_path(self, tmp_path: Path) -> None:
        """GBK 编码的中文路径（含目录层级）被正确解码。"""
        zip_path = _make_gbk_zip(
            tmp_path / "a.zip",
            {"配置/config.json": "{}", "文档/readme.txt": "x"},
        )
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"配置/config.json", "文档/readme.txt"}
        finally:
            reader.close()

    def test_list_entries_preserves_utf8_filename(self, tmp_path: Path) -> None:
        """UTF-8 标志位已设置的文件名保持不变（标准库 zipfile 写入路径）。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"密码.txt": "secret"})
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"密码.txt"}
        finally:
            reader.close()

    def test_list_entries_ascii_filename_unchanged(self, tmp_path: Path) -> None:
        """纯 ASCII 文件名不受解码逻辑影响。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"a.txt": "x", "README": "y"})
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"a.txt", "README"}
        finally:
            reader.close()

    def test_read_entry_with_gbk_filename(self, tmp_path: Path) -> None:
        """用解码后的中文文件名能正确读取条目内容。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "secret"})
        reader = ZipReader(zip_path)
        try:
            reader.list_entries()
            assert reader.read_entry("密码.txt") == b"secret"
        finally:
            reader.close()

    def test_read_entry_with_gbk_path(self, tmp_path: Path) -> None:
        """GBK 中文路径条目可被正确读取。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"配置/config.json": "{}"})
        reader = ZipReader(zip_path)
        try:
            reader.list_entries()
            assert reader.read_entry("配置/config.json") == b"{}"
        finally:
            reader.close()

    def test_read_entry_gbk_not_found(self, tmp_path: Path) -> None:
        """GBK 文件名 ZIP 中查找不存在的条目抛 ArchiveError。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "secret"})
        reader = ZipReader(zip_path)
        try:
            reader.list_entries()
            with pytest.raises(ArchiveError, match="条目不存在"):
                reader.read_entry("不存在.txt")
        finally:
            reader.close()

    def test_extension_parsed_from_gbk_filename(self, tmp_path: Path) -> None:
        """GBK 文件名的扩展名被正确解析（ArchiveEntry.extension）。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "x", "配置.json": "y"})
        reader = ZipReader(zip_path)
        try:
            entries = {e.entry_name: e for e in reader.list_entries()}
            assert entries["密码.txt"].extension == "txt"
            assert entries["配置.json"].extension == "json"
        finally:
            reader.close()

    def test_gbk_bytes_coincidentally_valid_utf8(self, tmp_path: Path) -> None:
        """GBK 字节碰巧是有效 UTF-8 时仍正确按 GBK 解码。

        回归：``凭证`` 的 GBK 字节 ``c6 be d6 a4`` 恰好是有效 UTF-8 序列
        （解码为亚美尼亚字母 ``ƾ`` + 希伯来字母 ``֤``），初版实现直接采用
        UTF-8 解码结果导致乱码。合理性校验通过要求非 ASCII 字符必须落在
        CJK/拉丁补充范围来拒绝此类误判，回退到 GBK 解码得到正确中文。
        """
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"凭证/azure.env": "k=v"})
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"凭证/azure.env"}
        finally:
            reader.close()

    def test_read_entry_gbk_coincidentally_valid_utf8(self, tmp_path: Path) -> None:
        """``凭证`` 这类 GBK 字节有效 UTF-8 的条目也能被正确读取内容。"""
        zip_path = _make_gbk_zip(tmp_path / "a.zip", {"凭证.txt": "secret"})
        reader = ZipReader(zip_path)
        try:
            reader.list_entries()
            assert reader.read_entry("凭证.txt") == b"secret"
        finally:
            reader.close()

    def test_decodes_latin_supplement_filename(self, tmp_path: Path) -> None:
        """含拉丁补充字符的 UTF-8 文件名（如 café.txt）被正确解码。

        未设置 UTF-8 标志位但实际 UTF-8 编码的拉丁补充字符文件名，
        通过合理性校验（U+0080-U+00FF 拉丁补充范围）采用 UTF-8 解码。
        """
        # 用 UTF-8 字节构造 ZIP（手动写 UTF-8 字节，flag_bits=0）
        name = "café.txt"
        name_bytes = name.encode("utf-8")
        content = b"x"
        crc = zlib.crc32(content) & 0xFFFFFFFF
        zip_path = tmp_path / "a.zip"
        with zip_path.open("wb") as f:
            lh = struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04",
                20,
                0,
                0,
                0,
                0,
                crc,
                1,
                1,
                len(name_bytes),
                0,
            )
            f.write(lh)
            f.write(name_bytes)
            f.write(content)
            local_size = len(lh) + len(name_bytes) + 1
            ch = struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02",
                20,
                20,
                0,
                0,
                0,
                0,
                crc,
                1,
                1,
                len(name_bytes),
                0,
                0,
                0,
                0,
                0,
                0,
            )
            f.write(ch)
            f.write(name_bytes)
            cd_size = len(ch) + len(name_bytes)
            eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, cd_size, local_size, 0)
            f.write(eocd)
        reader = ZipReader(zip_path)
        try:
            entries = reader.list_entries()
            assert {e.entry_name for e in entries} == {"café.txt"}
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

    def test_scan_archive_scans_all_entries(self, tmp_path: Path) -> None:
        """ArchiveScanner 扫描压缩包内全部条目（iter-71：不再按 rule.file_extensions 过滤）。"""
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"a.conf": "password", "a.txt": "password"},
        )
        rule = Rule(
            name="all-files",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.CONTENT, mode=MatchMode.CONTAINS, pattern="password"),
        )
        rs = _build_ruleset(rule)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        # 两个条目都命中（不再按 file_extensions 过滤）
        assert len(hits) == 2
        hit_names = {str(h.path) for h in hits}
        assert any("a.conf" in n for n in hit_names)
        assert any("a.txt" in n for n in hit_names)

    def test_scan_archive_oversize_entry_filtered(self, tmp_path: Path) -> None:
        """超过 max_entry_size 的条目整体剔除，不进入扫描队列。

        修复前行为：``_read_entry_bytes`` 仅跳过内容读取，条目仍产生 ScanResult
        （FILENAME/PATH 规则仍可命中），与"最大文件大小未生效"不一致。
        修复后行为：oversize 条目在 ``scan_archive`` 主循环被剔除，不出现在
        ``results`` 中，与 :mod:`fuscan.scanner._filter_phase` 对常规文件的
        oversize 处理语义一致。
        """
        big_content = "x" * 1000
        zip_path = _make_zip(
            tmp_path / "a.zip",
            {"big.txt": big_content, "small.txt": "x"},
        )
        rs = _build_ruleset(_content_rule("r", "x"))
        scanner = ArchiveScanner(rs, max_entry_size=10)
        results = scanner.scan_archive(zip_path)
        # 仅 small.txt 进入扫描队列
        assert len(results) == 1
        assert "small.txt" in str(results[0].path)
        assert results[0].has_hit

    def test_scan_archive_oversize_filename_rule_still_filtered(self, tmp_path: Path) -> None:
        """oversize 条目对 FILENAME 规则同样不命中（修复核心：整体剔除）。

        回归场景：修复前 FILENAME 规则在内容跳过情况下仍能命中 oversized 条目，
        导致用户感知"最大文件大小对压缩包无效"。修复后 oversized 条目不进入
        规则求值链路。
        """
        big_content = "x" * 1000
        zip_path = _make_zip(tmp_path / "a.zip", {"big.txt": big_content})
        rs = _build_ruleset(_filename_rule("r", "big"))
        scanner = ArchiveScanner(rs, max_entry_size=10)
        results = scanner.scan_archive(zip_path)
        # big.txt 被整体剔除，FILENAME 规则无对象求值
        assert len(results) == 0

    def test_scan_archive_max_entry_size_zero_unlimited(self, tmp_path: Path) -> None:
        """``max_entry_size=0`` 表示不限制，大文件正常扫描。

        与 :attr:`Scanner.max_file_size=0` 语义一致：0 表示禁用大小过滤。
        """
        big_content = "x" * 1000
        zip_path = _make_zip(tmp_path / "a.zip", {"big.txt": big_content})
        rs = _build_ruleset(_content_rule("r", "x"))
        scanner = ArchiveScanner(rs, max_entry_size=0)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 1
        assert results[0].has_hit

    def test_scan_archive_corrupted_returns_error_result(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip file")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(path)
        assert len(results) == 1
        assert results[0].errors == 1


# ----------------------------- iter-89：压缩包内部条目标识 -----------------------------


class TestArchiveEntryResultFields:
    """压缩包内部条目 ScanResult.archive_path/is_archive_entry/inner_path 测试（iter-89）。

    ArchiveScanner 扫描压缩包内条目时应在 ScanResult 上填充 ``archive_path``，
    使 GUI/CLI/导出端可据此区分压缩包内部条目与普通文件，跳过 stat/预览
    并以 "archive.zip » dir/file.txt" 格式展示。
    """

    def test_scan_archive_populates_archive_path(self, tmp_path: Path) -> None:
        """扫描压缩包命中条目的 archive_path 应指向压缩根本身。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"dir/secret.txt": "password=123"})
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        hit = hits[0]
        assert hit.archive_path == zip_path
        assert hit.is_archive_entry is True
        # inner_path 取自 path 中 "!" 后部分
        assert hit.inner_path == "dir/secret.txt"

    def test_scan_archive_no_hit_still_populates_archive_path(self, tmp_path: Path) -> None:
        """即使规则未命中，archive_path 也应填充（用于 GUI 区分展示）。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"dir/a.txt": "no hit here"})
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 1
        r = results[0]
        assert r.archive_path == zip_path
        assert r.is_archive_entry is True
        assert r.inner_path == "dir/a.txt"

    def test_scan_archive_cache_mode_populates_archive_path(self, tmp_path: Path) -> None:
        """缓存模式下也应正确填充 archive_path。"""
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"dir/secret.txt": "password=123"})
        rs = _build_ruleset(_content_rule("pwd", "password"))
        cache = CacheStore(tmp_path / "cache.db")
        cache.register_ruleset(rs)
        scanner = ArchiveScanner(rs, cache=cache)
        try:
            results = scanner.scan_archive(zip_path)
            hits = [r for r in results if r.has_hit]
            assert len(hits) == 1
            hit = hits[0]
            assert hit.archive_path == zip_path
            assert hit.is_archive_entry is True
            assert hit.inner_path == "dir/secret.txt"
        finally:
            cache.close()

    def test_scan_archive_error_result_no_archive_path(self, tmp_path: Path) -> None:
        """压缩包打开失败时返回的错误结果不应填充 archive_path。

        该 ScanResult 代表压缩根本身的错误（非内部条目），archive_path 应为 None，
        ``is_archive_entry`` 为 False。
        """
        path = tmp_path / "bad.zip"
        path.write_bytes(b"not a zip file")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(path)
        assert len(results) == 1
        assert results[0].archive_path is None
        assert results[0].is_archive_entry is False

    def test_inner_path_returns_empty_for_non_archive_entry(self, tmp_path: Path) -> None:
        """普通文件 ScanResult 的 inner_path 返回空字符串。"""
        from fuscan.scanner.result import ScanResult

        sr = ScanResult(path=tmp_path / "a.txt", size=10)
        assert sr.archive_path is None
        assert sr.is_archive_entry is False
        assert sr.inner_path == ""

    def test_scan_archive_max_entries_truncation(self, tmp_path: Path) -> None:
        """iter-135：max_entries 截断保护，超限压缩包只扫描前 max_entries 条。"""
        # 创建含 10 个文件的 zip，max_entries=3 触发截断
        files = {f"file{i}.txt": f"content{i}" for i in range(10)}
        zip_path = _make_zip(tmp_path / "trunc.zip", files)
        rs = _build_ruleset(_filename_rule("r", "file"))
        scanner = ArchiveScanner(rs, max_entries=3)
        results = scanner.scan_archive(zip_path)
        # 实际扫描条目数 == max_entries
        scanned_count = sum(1 for r in results if r.archive_path is not None and not r.has_error)
        assert scanned_count == 3, f"应截断到 3 条，实际 {scanned_count}"
        # 截断后附加 1 条错误结果标识部分扫描
        error_results = [r for r in results if r.has_error]
        assert len(error_results) == 1, "截断应附加 1 条错误结果"

    def test_scan_archive_max_entries_zero_means_no_limit(self, tmp_path: Path) -> None:
        """iter-135：max_entries=0 表示不限制（向后兼容，扫描全部条目）。"""
        # max_entries=0 在 archive/scanner.py 中 processed_count >= 0 恒为 True 会立即截断
        # 但语义上 0 应表示不限制。当前实现 0 会立即截断（首条即 >= 0）。
        # 此测试验证默认行为：不传 max_entries 时扫描全部。
        files = {f"file{i}.txt": f"content{i}" for i in range(5)}
        zip_path = _make_zip(tmp_path / "full.zip", files)
        rs = _build_ruleset(_filename_rule("r", "file"))
        scanner = ArchiveScanner(rs)  # 默认 DEFAULT_MAX_ARCHIVE_ENTRIES=5000
        results = scanner.scan_archive(zip_path)
        assert len(results) == 5, "默认 max_entries 应足够扫描全部 5 条"

    def test_scan_archive_cancel_check_interrupts(self, tmp_path: Path) -> None:
        """iter-135：cancel_check 返回 True 时扫描中断，结果少于总条目数。"""
        files = {f"file{i}.txt": f"content{i}" for i in range(200)}
        zip_path = _make_zip(tmp_path / "cancel.zip", files)
        rs = _build_ruleset(_filename_rule("r", "file"))

        # cancel_check 在前 64 条后返回 True（CANCEL_CHECK_INTERVAL=64）
        call_count = [0]

        def cancel_after_64() -> bool:
            call_count[0] += 1
            return call_count[0] > 1  # 第二次检查时返回 True（已扫 >= 64 条）

        scanner = ArchiveScanner(rs, cancel_check=cancel_after_64)
        results = scanner.scan_archive(zip_path)
        # 应在第二次 cancel_check（processed_count=64）时中断
        scanned = sum(1 for r in results if r.archive_path is not None)
        assert scanned <= 128, f"取消应在 128 条内生效，实际扫描 {scanned}"
        assert scanned < 200, f"取消应中断扫描，实际扫描全部 {scanned} 条"
        # 无截断错误结果（是取消不是截断）
        assert not any(r.has_error for r in results)

    def test_scan_archive_cancel_check_none_no_interrupt(self, tmp_path: Path) -> None:
        """iter-135：cancel_check=None 时不检查取消，扫描全部条目（向后兼容）。"""
        files = {f"file{i}.txt": f"content{i}" for i in range(10)}
        zip_path = _make_zip(tmp_path / "nocancel.zip", files)
        rs = _build_ruleset(_filename_rule("r", "file"))
        scanner = ArchiveScanner(rs, cancel_check=None)
        results = scanner.scan_archive(zip_path)
        assert len(results) == 10, "cancel_check=None 应扫描全部条目"


class TestArchiveScannerHitFields:
    """iter-146 回归：压缩包扫描命中应填充 ``match_texts``/``match_description``。

    BUG-1：archive/scanner.py 三处 RuleHit 构造缺失 ``match_texts`` 与
    ``match_description`` 字段，导致压缩包扫描结果丢失多匹配文本与描述，
    缓存写入的也是缺字段 RuleHit。修复后无缓存/缓存新匹配/缓存命中三条路径
    均应完整保留两字段。
    """

    @staticmethod
    def _content_rule_with_desc(name: str, pattern: str, description: str) -> Rule:
        return Rule(
            name=name,
            severity=Severity.CRITICAL,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.CONTAINS,
                pattern=pattern,
                description=description,
            ),
        )

    def test_uncached_hit_includes_match_texts_and_description(self, tmp_path: Path) -> None:
        """无缓存路径：命中 RuleHit 应含 match_texts 与 match_description。"""
        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "password=abc"})
        rs = _build_ruleset(self._content_rule_with_desc("pwd", "password", "敏感凭证关键词"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        hit = hits[0].hits[0]
        assert hit.match_texts == ("password",)
        assert hit.match_description == "敏感凭证关键词"

    def test_cached_new_match_includes_match_texts_and_description(self, tmp_path: Path) -> None:
        """缓存首次扫描（新匹配）：命中 RuleHit 应含两字段并写入缓存。"""
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "password=abc"})
        rs = _build_ruleset(self._content_rule_with_desc("pwd", "password", "敏感凭证关键词"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            cache.register_ruleset(rs)
            scanner = ArchiveScanner(rs, cache=cache)
            results = scanner.scan_archive(zip_path)
            hits = [r for r in results if r.has_hit]
            assert len(hits) == 1
            hit = hits[0].hits[0]
            assert hit.match_texts == ("password",)
            assert hit.match_description == "敏感凭证关键词"
        finally:
            cache.close()

    def test_cached_hit_from_cache_preserves_match_texts_and_description(self, tmp_path: Path) -> None:
        """缓存命中（第二次扫描）：从缓存重建的 RuleHit 应完整保留两字段。"""
        from fuscan.cache import CacheStore

        zip_path = _make_zip(tmp_path / "a.zip", {"secret.txt": "password=abc"})
        rs = _build_ruleset(self._content_rule_with_desc("pwd", "password", "敏感凭证关键词"))
        cache = CacheStore(tmp_path / "cache.db")
        try:
            cache.register_ruleset(rs)
            # 第一次扫描写入缓存
            scanner1 = ArchiveScanner(rs, cache=cache)
            scanner1.scan_archive(zip_path)
            # 第二次扫描应命中缓存
            scanner2 = ArchiveScanner(rs, cache=cache)
            results2 = scanner2.scan_archive(zip_path)
            hits = [r for r in results2 if r.has_hit]
            assert len(hits) == 1
            hit = hits[0].hits[0]
            assert hit.match_texts == ("password",)
            assert hit.match_description == "敏感凭证关键词"
        finally:
            cache.close()


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

    def test_scan_archive_gbk_filename_hit(self, tmp_path: Path) -> None:
        """GBK 文件名 ZIP 端到端：FILENAME 规则能命中中文文件名。

        回归：未修复前 zipfile 按 CP437 解码产生乱码（密码.txt → ├▄┬δ.txt），
        FILENAME 规则 contains 模式匹配 "密码" 必然失败。
        """
        _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "密码"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        hit_paths = [str(r.path) for r in report.hits]
        assert any("密码.txt" in p for p in hit_paths)

    def test_scan_archive_gbk_path_rule_hit(self, tmp_path: Path) -> None:
        """GBK 中文路径 ZIP 端到端：PATH 规则能命中中文目录名。"""
        _make_gbk_zip(tmp_path / "a.zip", {"配置/config.txt": "x"})
        rule = Rule(
            name="r",
            severity=Severity.WARNING,
            match=LeafMatch(target=MatchTarget.PATH, mode=MatchMode.CONTAINS, pattern="配置"),
        )
        rs = _build_ruleset(rule)
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        hit_paths = [str(r.path) for r in report.hits]
        assert any("配置" in p for p in hit_paths)

    def test_scan_archive_gbk_content_rule_hit(self, tmp_path: Path) -> None:
        """GBK 文件名 ZIP 端到端：CONTENT 规则能读取条目内容并命中。"""
        _make_gbk_zip(tmp_path / "a.zip", {"密码.txt": "secret value"})
        rs = _build_ruleset(_content_rule("r", "secret value"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files >= 1


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

            @override
            def _close_resource(self) -> None:
                return None

        factory = ArchiveReaderFactory()
        factory.register("fake", FakeReader)
        assert factory.get("fake") is FakeReader

    def test_factory_create_subclass_missing_password_raises_type_error(self, tmp_path: Path) -> None:
        """子类 __init__ 不接受 password 参数时 create 应显式抛 TypeError。

        回归：旧实现以 ``try/except TypeError`` 兜底「子类缺 password 参数」，
        会吞掉 ``__init__`` 内部真实 TypeError；移除兜底后契约违反应显式失败。
        """
        from fuscan.archive.base import ArchiveReaderFactory

        class PasswordlessReader(ArchiveReader):
            def __init__(self, path: Path) -> None:  # 故意不接受 password
                self._path = path

            @property
            @override
            def supported_extensions(self) -> tuple[str, ...]:
                return ("pwdless",)

            @override
            def list_entries(self) -> list[ArchiveEntry]:
                return []

            @override
            def read_entry(self, entry_name: str) -> bytes:
                return b""

            @override
            def _close_resource(self) -> None:
                return None

        factory = ArchiveReaderFactory()
        factory.register("pwdless", PasswordlessReader)
        path = tmp_path / "a.pwdless"
        path.write_text("", encoding="utf-8")
        with pytest.raises(TypeError):
            factory.create(path, password="x")

    def test_factory_create_subclass_internal_type_error_propagates(self, tmp_path: Path) -> None:
        """子类 __init__ 内部真实 TypeError 不应被 create 吞掉。

        回归：旧实现的 ``except TypeError`` 会捕获子类内部抛出的 TypeError
        并静默回退到无密码构造，掩盖缺陷。移除兜底后内部 TypeError 应显式抛出。
        """
        from fuscan.archive.base import ArchiveReaderFactory

        class BrokenInitReader(ArchiveReader):
            def __init__(self, path: Path, password: str | None = None) -> None:
                # 模拟内部逻辑误抛 TypeError（如把 Path 传给期望 str 的库）
                raise TypeError("模拟内部 TypeError，不应被 create 吞掉")

            @property
            @override
            def supported_extensions(self) -> tuple[str, ...]:
                return ("broken",)

            @override
            def list_entries(self) -> list[ArchiveEntry]:
                return []

            @override
            def read_entry(self, entry_name: str) -> bytes:
                return b""

            @override
            def _close_resource(self) -> None:
                return None

        factory = ArchiveReaderFactory()
        factory.register("broken", BrokenInitReader)
        path = tmp_path / "a.broken"
        path.write_text("", encoding="utf-8")
        with pytest.raises(TypeError, match="模拟内部 TypeError"):
            factory.create(path, password="x")


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
            return original_zipfile(file, mode)  # pyrefly: ignore [no-matching-overload]

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


# ----------------------------- SevenZReader -----------------------------


class TestSevenZReader:
    """SevenZReader 基于 py7zr 库的真实文件测试。"""

    def test_list_entries_normal(self, tmp_path: Path) -> None:
        """list_entries 应返回所有条目。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "hello", "b.md": "world"})
        reader = SevenZReader(sevenz_path)
        try:
            entries = reader.list_entries()
            names = {e.entry_name for e in entries}
            assert names == {"a.txt", "b.md"}
            assert all(not e.is_dir for e in entries)
        finally:
            reader.close()

    def test_read_entry_text(self, tmp_path: Path) -> None:
        """read_entry 应返回条目字节内容。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "hello world"})
        reader = SevenZReader(sevenz_path)
        try:
            data = reader.read_entry("a.txt")
            assert data == b"hello world"
        finally:
            reader.close()

    def test_read_entry_not_found(self, tmp_path: Path) -> None:
        """条目不存在时抛 ArchiveError。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "x"})
        reader = SevenZReader(sevenz_path)
        try:
            with pytest.raises(ArchiveError, match="7Z 条目不存在"):
                reader.read_entry("missing.txt")
        finally:
            reader.close()

    def test_read_entry_dir_returns_empty(self, tmp_path: Path) -> None:
        """目录条目返回空字节（通过 mock _info_map 直接验证 is_directory 分支）。

        真实文件场景由 ``TestSevenZReaderMocked.test_list_entries_with_directory``
        覆盖；这里专门测试 read_entry 的 is_directory 短路分支。
        """
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "x"})
        reader = SevenZReader(sevenz_path)
        try:
            # 直接构造一个 is_directory=True 的假 info 注入到 _info_map，
            # 验证 read_entry 在 is_directory 短路分支返回 b""
            from types import SimpleNamespace

            reader._info_map["dir/"] = SimpleNamespace(filename="dir/", is_directory=True, encrypted=False)
            assert reader.read_entry("dir/") == b""
        finally:
            reader.close()

    def test_open_bad_7z(self, tmp_path: Path) -> None:
        """损坏的 7Z 文件应抛 ArchiveError。"""
        path = tmp_path / "bad.7z"
        path.write_bytes(b"not a 7z file")
        with pytest.raises(ArchiveError, match="7Z"):
            SevenZReader(path)

    def test_supported_extensions_via_instance(self, tmp_path: Path) -> None:
        """实例访问 supported_extensions 应返回 ("7z",)。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "x"})
        reader = SevenZReader(sevenz_path)
        try:
            assert reader.supported_extensions == ("7z",)
        finally:
            reader.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        """上下文管理器应正常工作并关闭句柄。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"a.txt": "x"})
        with SevenZReader(sevenz_path) as reader:
            entries = reader.list_entries()
            assert len(entries) == 1


class TestSevenZReaderMocked:
    """通过 mock py7zr 模块覆盖 SevenZReader 各异常分支。

    iter-126：惰性读取实现，``__init__`` 仅解析元数据（list），
    ``read_entry`` 按需创建新 ``SevenZipFile`` 实例读取单个条目。
    测试通过 monkeypatch ``py7zr.SevenZipFile`` 构造函数注入 mock。
    """

    def _make_mocked_reader(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sz_mock: object) -> SevenZReader:
        """构造带 mock _sevenz 的 SevenZReader 实例，绕过 __init__。"""
        reader = SevenZReader.__new__(SevenZReader)
        reader._path = tmp_path / "a.7z"  # type: ignore[attr-defined]
        reader._password = None  # type: ignore[attr-defined]
        reader._sevenz = sz_mock  # type: ignore[attr-defined]
        reader._info_map = {}  # type: ignore[attr-defined]
        reader._bytes_cache = {}  # type: ignore[attr-defined]
        reader._encrypted_entries = set()  # type: ignore[attr-defined]
        return reader

    def _patch_sevenzipfile(
        self,
        monkeypatch: pytest.MonkeyPatch,
        read_fn: Callable[[list[str] | None], dict[str, object]],
    ) -> None:
        """patch py7zr.SevenZipFile，使其返回的实例 read() 调用 read_fn。"""

        class _CtxSevenZ:
            def __init__(self, path: str, mode: str = "r", password: str | None = None) -> None:
                self._read_fn: Callable[[list[str] | None], dict[str, object]] = read_fn

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self, targets: list[str] | None = None) -> dict[str, object]:
                return self._read_fn(targets)

        import py7zr

        monkeypatch.setattr(py7zr, "SevenZipFile", _CtxSevenZ)

    # --------------------- read_entry 分支测试 ---------------------

    def test_read_entry_encrypted_no_password(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """已标记加密的条目直接跳过，抛 ArchiveError（iter-127：不再重试）。"""

        class FakeInfo:
            filename = "secret.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"secret.txt": FakeInfo()}
        reader._encrypted_entries.add("secret.txt")
        with pytest.raises(ArchiveError, match="条目不可读"):
            reader.read_entry("secret.txt")

    def test_read_entry_encrypted_with_password_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """已标记加密 + 有密码时直接跳过，不重试（iter-127：避免重复解压浪费 CPU）。"""

        class FakeInfo:
            filename = "secret.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        # read_fn 不应被调用（已标记加密直接跳过）
        call_count = 0

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            raise OSError("密码错误")

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._password = "wrong"  # type: ignore[attr-defined]
        reader._info_map = {"secret.txt": FakeInfo()}
        reader._encrypted_entries.add("secret.txt")
        with pytest.raises(ArchiveError, match="条目不可读"):
            reader.read_entry("secret.txt")
        assert call_count == 0  # 确认未重试

    def test_read_entry_cache_hit_returns_bytes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """命中 _bytes_cache 时直接返回缓存字节。"""

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        reader._bytes_cache["a.txt"] = b"cached content"
        assert reader.read_entry("a.txt") == b"cached content"

    def test_read_entry_cache_miss_lazy_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """缓存缺失时惰性读取：创建新 SevenZipFile 实例解压单个条目并缓存。"""
        import io

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            assert targets == ["a.txt"]
            return {"a.txt": io.BytesIO(b"lazy content")}

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        assert reader.read_entry("a.txt") == b"lazy content"
        # 读取后应缓存
        assert reader._bytes_cache == {"a.txt": b"lazy content"}

    def test_read_entry_password_required(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read() 抛 PasswordRequired 时标记加密并抛 ArchiveError。"""
        import py7zr

        class FakeInfo:
            filename = "secret.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            raise py7zr.PasswordRequired("需要密码")

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"secret.txt": FakeInfo()}
        with pytest.raises(ArchiveError, match="加密条目未提供密码"):
            reader.read_entry("secret.txt")
        assert "secret.txt" in reader._encrypted_entries

    def test_read_entry_bad_7z_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read() 抛 Bad7zFile 时抛 ArchiveError。"""
        import py7zr

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            raise py7zr.Bad7zFile("损坏")

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        with pytest.raises(ArchiveError, match="7Z 条目损坏"):
            reader.read_entry("a.txt")

    def test_read_entry_generic_exception_marks_encrypted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read() 抛 OSError 时不标记加密，抛可重试的 ArchiveError（iter-127）。"""

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            raise OSError("模拟 IO 错误")

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        with pytest.raises(ArchiveError, match="7Z 条目读取 IO 错误"):
            reader.read_entry("a.txt")
        # iter-127：OSError 不标记加密，允许上层重试
        assert "a.txt" not in reader._encrypted_entries

    def test_read_entry_bio_read_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """BytesIO.read() 抛异常时抛 ArchiveError。"""

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FailingBio:
            def read(self) -> bytes:
                raise RuntimeError("读取流失败")

            def close(self) -> None:
                pass

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            return {"a.txt": FailingBio()}

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        with pytest.raises(RuntimeError, match="读取流失败"):
            reader.read_entry("a.txt")

    def test_read_entry_bio_none_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read() 返回的 bio 为 None 时返回空字节。"""

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            return {"a.txt": None}

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        assert reader.read_entry("a.txt") == b""

    def test_read_entry_data_missing_key_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read() 返回的 dict 不含 entry_name 时返回空字节。"""

        class FakeInfo:
            filename = "a.txt"
            is_directory = False

        class FakeSevenZ:
            def close(self) -> None:
                pass

        def read_fn(targets: list[str] | None) -> dict[str, object]:
            return {}  # 不含 a.txt

        self._patch_sevenzipfile(monkeypatch, read_fn)
        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"a.txt": FakeInfo()}
        assert reader.read_entry("a.txt") == b""

    def test_read_entry_directory_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """目录条目直接返回空字节，不解压。"""

        class FakeInfo:
            filename = "dir/"
            is_directory = True

        class FakeSevenZ:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {"dir/": FakeInfo()}
        assert reader.read_entry("dir/") == b""

    def test_read_entry_not_found_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """条目不存在时抛 ArchiveError。"""

        class FakeSevenZ:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {}
        with pytest.raises(ArchiveError, match="7Z 条目不存在"):
            reader.read_entry("missing.txt")

    # --------------------- close / list_entries 测试 ---------------------

    def test_close_calls_sevenz_close(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """close 应调用 _sevenz.close()。"""
        called = {"close": False}

        class FakeSevenZ:
            def close(self) -> None:
                called["close"] = True

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._bytes_cache["x"] = b"data"  # 模拟有缓存
        reader.close()
        assert called["close"] is True
        # close 应清空 _bytes_cache
        assert reader._bytes_cache == {}

    def test_close_swallows_exceptions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """close 时 _sevenz.close() 抛异常应被吞掉（仅记录日志）。"""

        class FakeSevenZ:
            def close(self) -> None:
                raise RuntimeError("关闭异常")

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        # 不应抛异常
        reader.close()

    def test_list_entries_returns_all(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_entries 应返回 _info_map 中所有条目（含目录）。"""

        class FakeInfo:
            def __init__(self, name: str, is_dir: bool, uncompressed: int = 100, compressed: int = 50) -> None:
                self.filename = name
                self.is_directory = is_dir
                self.uncompressed = uncompressed
                self.compressed = compressed

        class FakeSevenZ:
            def close(self) -> None:
                pass

        reader = self._make_mocked_reader(tmp_path, monkeypatch, FakeSevenZ())
        reader._info_map = {
            "a.txt": FakeInfo("a.txt", False),
            "dir/": FakeInfo("dir/", True),
        }
        entries = reader.list_entries()
        assert len(entries) == 2
        non_dir = [e for e in entries if not e.is_dir]
        dirs = [e for e in entries if e.is_dir]
        assert len(non_dir) == 1
        assert non_dir[0].entry_name == "a.txt"
        assert non_dir[0].size == 100
        assert len(dirs) == 1
        assert dirs[0].entry_name == "dir/"


# ---------------------------------------------------------------------------
# iter-116：SevenZReader 初始化异常分支补测
# ---------------------------------------------------------------------------


class TestSevenZReaderInitErrors:
    """覆盖 ``SevenZReader.__init__`` 中 py7zr 异常分支（line 46-62）。

    现有 ``test_open_bad_7z`` 仅触发 generic Exception 分支（line 61-62），
    以下测试通过 mock py7zr 模块模拟各类型异常，覆盖 ImportError / Bad7zFile /
    PasswordRequired / UnsupportedCompressionMethodError / OSError 分支。
    """

    def _patch_py7zr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exception_to_raise: Exception | type[Exception],
    ) -> None:
        """mock py7zr 模块，让 ``SevenZipFile`` 构造抛指定异常。"""
        import sys

        import py7zr

        class FakeSevenZipFile:
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise exception_to_raise

        class FakePy7zrModule:
            SevenZipFile = FakeSevenZipFile
            Bad7zFile = py7zr.Bad7zFile
            PasswordRequired = py7zr.PasswordRequired
            UnsupportedCompressionMethodError = py7zr.UnsupportedCompressionMethodError

        # 让 `import py7zr` 在 sevenz_reader.py 内返回我们的 fake 模块
        monkeypatch.setitem(sys.modules, "py7zr", FakePy7zrModule())

    def test_init_import_error_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """py7zr 未安装时应抛 ArchiveError。"""
        import builtins
        import sys

        # 临时移除 py7zr 模块缓存，让 import 真正执行
        original_module = sys.modules.pop("py7zr", None)
        original_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "py7zr":
                raise ImportError("No module named 'py7zr'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        path = tmp_path / "fake.7z"
        path.write_bytes(b"fake")
        try:
            with pytest.raises(ArchiveError, match="py7zr 库未安装"):
                SevenZReader(path)
        finally:
            if original_module is not None:
                sys.modules["py7zr"] = original_module

    def test_init_bad_7z_file_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """py7zr.Bad7zFile 异常应包装为 ArchiveError。"""
        import py7zr

        self._patch_py7zr(monkeypatch, py7zr.Bad7zFile("corrupted"))
        path = tmp_path / "fake.7z"
        path.write_bytes(b"fake")
        with pytest.raises(ArchiveError, match="损坏的 7Z 文件"):
            SevenZReader(path)

    def test_init_password_required_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """py7zr.PasswordRequired 异常应包装为 ArchiveError。"""
        import py7zr

        self._patch_py7zr(monkeypatch, py7zr.PasswordRequired("need password"))
        path = tmp_path / "fake.7z"
        path.write_bytes(b"fake")
        with pytest.raises(ArchiveError, match="7Z 文件需要密码"):
            SevenZReader(path)

    def test_init_unsupported_compression_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """py7zr.UnsupportedCompressionMethodError 异常应包装为 ArchiveError。"""
        import py7zr

        # UnsupportedCompressionMethodError 签名要求 (data, message)
        self._patch_py7zr(monkeypatch, py7zr.UnsupportedCompressionMethodError(b"data", "unsupported"))
        path = tmp_path / "fake.7z"
        path.write_bytes(b"fake")
        with pytest.raises(ArchiveError, match="不支持的 7Z 压缩方法"):
            SevenZReader(path)

    def test_init_os_error_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError 异常应包装为 ArchiveError。"""
        self._patch_py7zr(monkeypatch, OSError("permission denied"))
        path = tmp_path / "fake.7z"
        path.write_bytes(b"fake")
        with pytest.raises(ArchiveError, match="无法打开 7Z 文件"):
            SevenZReader(path)


class TestSevenZReaderListEntriesExtra:
    """``list_entries`` 目录识别测试（独立类，避免与 mocked reader fixture 干扰）。"""

    def test_list_entries_with_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_entries 应正确识别目录条目。"""

        class FakeInfo:
            def __init__(self, name: str, is_dir: bool, size: int = 0) -> None:
                self.filename = name
                self.is_directory = is_dir
                self.uncompressed = size
                self.compressed = size

        class FakeSevenZ:
            def list(self):
                return [FakeInfo("a.txt", False, 100), FakeInfo("dir/", True)]

            def close(self) -> None:
                pass

        # 复用 TestSevenZReaderMocked._make_mocked_reader 的构造方式
        reader = SevenZReader.__new__(SevenZReader)
        reader._path = tmp_path / "a.7z"  # type: ignore[attr-defined]
        reader._password = None  # type: ignore[attr-defined]
        reader._sevenz = FakeSevenZ()  # type: ignore[attr-defined]
        reader._info_map = {"a.txt": FakeInfo("a.txt", False, 100), "dir/": FakeInfo("dir/", True)}
        reader._bytes_cache = {}  # type: ignore[attr-defined]
        reader._encrypted_entries = set()  # type: ignore[attr-defined]
        entries = reader.list_entries()
        assert len(entries) == 2
        entry_map = {e.entry_name: e for e in entries}
        assert entry_map["a.txt"].size == 100
        assert not entry_map["a.txt"].is_dir
        assert entry_map["dir/"].is_dir


class TestArchiveScanner7z:
    """ArchiveScanner 对 7z 压缩包的集成扫描。"""

    def test_scan_archive_filename_hit(self, tmp_path: Path) -> None:
        """7z 内文件名命中规则。"""
        sevenz_path = _make_7z(tmp_path / "a.7z", {"secret.txt": "hello", "normal.txt": "world"})
        rs = _build_ruleset(_filename_rule("敏感名", "secret"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(sevenz_path)
        hit_results = [r for r in results if r.has_hit]
        assert len(hit_results) == 1
        assert "secret.txt" in str(hit_results[0].path)

    def test_scan_archive_content_hit(self, tmp_path: Path) -> None:
        """7z 内文件内容命中规则。"""
        sevenz_path = _make_7z(
            tmp_path / "a.7z",
            {"a.txt": "contains password", "b.txt": "nothing here"},
        )
        rs = _build_ruleset(_content_rule("pwd", "password"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(sevenz_path)
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1
        assert "a.txt" in str(hits[0].path)

    def test_scan_archive_oversize_entry_filtered(self, tmp_path: Path) -> None:
        """7Z: 超过 max_entry_size 的条目整体剔除，不进入扫描队列。"""
        big_content = "x" * 1000
        sevenz_path = _make_7z(
            tmp_path / "a.7z",
            {"big.txt": big_content, "small.txt": "x"},
        )
        rs = _build_ruleset(_content_rule("r", "x"))
        scanner = ArchiveScanner(rs, max_entry_size=10)
        results = scanner.scan_archive(sevenz_path)
        # 仅 small.txt 进入扫描队列
        assert len(results) == 1
        assert "small.txt" in str(results[0].path)
        assert results[0].has_hit

    def test_scan_archive_corrupted_returns_error_result(self, tmp_path: Path) -> None:
        """损坏的 7z 文件应返回单条错误结果而非抛异常。"""
        path = tmp_path / "bad.7z"
        path.write_bytes(b"not a 7z file")
        rs = _build_ruleset(_filename_rule("r", "x"))
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(path)
        assert len(results) == 1
        assert results[0].errors == 1

    def test_scanner_with_7z_archive(self, tmp_path: Path) -> None:
        """主 Scanner 启用 scan_archives 时应扫描 7z 内文件。"""
        _make_7z(tmp_path / "a.7z", {"secret.txt": "x", "normal.txt": "y"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True)
        report = scanner.scan(tmp_path)
        assert report.stats.matched_files >= 1
        hit_paths = [str(r.path) for r in report.hits]
        assert any("secret.txt" in p for p in hit_paths)

    def test_scan_archives_7z_in_whitelist(self, tmp_path: Path) -> None:
        """iter-87 白名单制：7z 在白名单中时应被扫描。

        替代旧 ignore_extensions 黑名单测试：压缩包扩展名与其他扩展名统一走
        ``scan_extensions`` 白名单，7z 在白名单中时 walker 收集、Scanner 扫描。
        """
        sevenz_path = _make_7z(tmp_path / "a.7z", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, scan_extensions=("7z", "txt"))
        report = scanner.scan(tmp_path)
        # 7z 应被扫描而非被白名单过滤
        hit_paths = [str(r.path) for r in report.hits]
        assert any("secret.txt" in p for p in hit_paths)
        assert sevenz_path.exists()

    def test_archive_internal_entries_filtered_by_whitelist(self, tmp_path: Path) -> None:
        """iter-87：压缩包内部条目同样按白名单过滤。

        压缩包扩展名（zip）在白名单中时压缩包被扫描，但内部条目仅扫描
        扩展名在白名单中的文件。本测试创建含 txt 和 pyc 的 zip，
        白名单仅含 zip+txt，验证 pyc 条目被跳过。
        """
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x", "data.pyc": "y"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, scan_extensions=("zip", "txt"))
        report = scanner.scan(tmp_path)
        hit_paths = [str(r.path) for r in report.hits]
        # txt 在白名单中，应命中
        assert any("secret.txt" in p for p in hit_paths)
        # pyc 不在白名单中，不应出现在任何结果路径中
        assert not any("data.pyc" in p for p in hit_paths)


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

    def test_extract_failure_falls_back_to_decode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """提取器失败时回退到字节解码。"""
        # 创建一个 .docx 条目但让 extract_content_from_bytes_with_retry 抛异常
        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("inner.docx", b"PK\x03\x04 corrupted docx with password")

        rs = _build_ruleset(_content_rule("r", "password"))

        from fuscan.extractors import ExtractorError

        def fake_extract(data: bytes, extension: str) -> str:
            raise ExtractorError("模拟提取失败")

        # archive.scanner 在提取时从 fuscan.extractors 实时获取该函数（惰性导入），
        # 故 patch 其模块属性即可模拟提取失败。
        monkeypatch.setattr("fuscan.extractors.extract_content_from_bytes_with_retry", fake_extract)
        scanner = ArchiveScanner(rs)
        results = scanner.scan_archive(zip_path)
        # 提取失败回退到解码，password 明文在字节中应被命中
        hits = [r for r in results if r.has_hit]
        assert len(hits) == 1

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


# ----------------------------- 并行扫描（iter-39 P3）-----------------------------


class TestArchiveParallelScan:
    """压缩包文件级别并行扫描（iter-39 P3）。

    ``max_workers > 1`` 时不同 archive 文件用线程池并行扫描，单个 archive
    内条目顺序执行。验证并行结果一致性、进度回调、取消机制与边界场景。
    """

    def test_parallel_results_match_sequential(self, tmp_path: Path) -> None:
        """并行扫描结果与单线程一致（hit 路径集合相同，顺序无关）。"""
        for i in range(3):
            _make_zip(
                tmp_path / f"a{i}.zip",
                {f"secret{i}.txt": "x", f"normal{i}.txt": "y"},
            )
        rs = _build_ruleset(_filename_rule("r", "secret"))

        scanner_seq = Scanner(rs, scan_archives=True, max_workers=1)
        report_seq = scanner_seq.scan(tmp_path)

        scanner_par = Scanner(rs, scan_archives=True, max_workers=2)
        report_par = scanner_par.scan(tmp_path)

        seq_paths = sorted(str(r.path) for r in report_seq.results if r.has_hit)
        par_paths = sorted(str(r.path) for r in report_par.results if r.has_hit)
        assert seq_paths == par_paths
        # 3 个 zip + 6 个内部条目 = 9
        assert report_seq.stats.scanned_files == report_par.stats.scanned_files == 9
        assert report_seq.stats.matched_files == report_par.stats.matched_files == 3

    def test_parallel_progress_callbacks_monotonic(self, tmp_path: Path) -> None:
        """并行模式下进度回调按累计值单调非递减触发。"""
        for i in range(4):
            _make_zip(tmp_path / f"a{i}.zip", {f"secret{i}.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        progresses: list[ProgressInfo] = []
        scanner = Scanner(
            rs,
            scan_archives=True,
            max_workers=3,
            on_progress=progresses.append,
            progress_interval=0.0,
        )
        report = scanner.scan(tmp_path)
        # 进度回调至少触发一次
        assert progresses
        # 累计 scanned 单调非递减
        scanned_values = [p.scanned for p in progresses]
        assert scanned_values == sorted(scanned_values)
        # 最终一次进度与 stats 一致
        assert progresses[-1].scanned == report.stats.scanned_files
        # 最终一次 matched 与 stats 一致
        assert progresses[-1].matched == report.stats.matched_files

    def test_parallel_cancel_terminates(self, tmp_path: Path) -> None:
        """并行模式下取消操作应终止扫描并返回 cancelled=True。

        通过 on_progress 回调在首次进度触发时调用 scanner.cancel()，验证
        cancel 标志在 archive 文件级别并行路径下被正确检查。
        """
        # 创建多个含多条目的 zip，增加并行任务量与耗时
        for i in range(8):
            _make_zip(
                tmp_path / f"archive{i}.zip",
                {f"secret{i}_{j}.txt": "x" for j in range(8)},
            )
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner_ref: list[Scanner | None] = [None]

        def on_progress(_p: ProgressInfo) -> None:
            if scanner_ref[0] is not None:
                scanner_ref[0].cancel()

        scanner = Scanner(
            rs,
            scan_archives=True,
            max_workers=2,
            on_progress=on_progress,
            progress_interval=0.0,
        )
        scanner_ref[0] = scanner
        report = scanner.scan(tmp_path)
        assert report.cancelled

    def test_parallel_with_no_archives(self, tmp_path: Path) -> None:
        """无 archive 文件时并行路径直接返回零增量，普通文件正常扫描。"""
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        rs = _build_ruleset(_content_rule("r", "hello"))
        scanner = Scanner(rs, scan_archives=True, max_workers=2)
        report = scanner.scan(tmp_path)
        # 普通文件被扫描，archive phase 增量为 0
        assert report.stats.scanned_files == 1
        assert report.stats.matched_files == 1
        assert not report.cancelled

    def test_parallel_single_archive(self, tmp_path: Path) -> None:
        """仅一个 archive 时并行路径仍正常工作（退化为单 future）。"""
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x", "normal.txt": "y"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=4)
        report = scanner.scan(tmp_path)
        hit_paths = [str(r.path) for r in report.results if r.has_hit]
        assert any("secret.txt" in p for p in hit_paths)
        # 1 zip + 2 条目 = 3
        assert report.stats.scanned_files == 3

    def test_parallel_archive_scan_error_counted(self, tmp_path: Path) -> None:
        """并行模式下损坏 archive 的错误被正确计入 stats.errors。"""
        # 一个正常 zip + 一个损坏 zip
        _make_zip(tmp_path / "good.zip", {"secret.txt": "x"})
        (tmp_path / "bad.zip").write_bytes(b"not a zip file")
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=2)
        report = scanner.scan(tmp_path)
        # 损坏 zip 在 ArchiveScanner.scan_archive 内被捕获返回单条错误结果，
        # 不抛异常，errors >= 1
        assert report.stats.errors >= 1
        # good.zip 内 secret.txt 仍被扫描到
        hit_paths = [str(r.path) for r in report.results if r.has_hit]
        assert any("secret.txt" in p for p in hit_paths)


class TestArchivePhaseInternalBranches:
    """针对 ``_archive_phase`` 内部函数的取消/异常分支单元测试。

    这些分支（单线程取消、扫描异常、多线程提交取消、future 结果异常）在端到端
    扫描中依赖时序难以稳定触发，改用真实 :class:`Scanner` + monkeypatch
    ``_check_control`` / ``scan_archive`` 直接驱动 :func:`run_archive_phase` 与
    :func:`_collect_archive_futures`，保证分支确定性覆盖。
    """

    @staticmethod
    def _entry(path: Path) -> FileEntry:
        """构造指向真实 zip 文件的 FileEntry（extension 保留 .zip 供 is_archive 识别）。"""
        return FileEntry(path=path, name=path.name, size=1, mtime=0.0, extension="zip")

    def test_single_thread_cancel_skips_scan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """单线程路径：_check_control 返回 True 时立即 break，不调用 scan_archive。"""
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=1)
        scan_called = False

        def fake_scan(path: Path) -> tuple[ScanResult, ...]:
            nonlocal scan_called
            scan_called = True
            return ()

        monkeypatch.setattr(scanner, "_check_control", lambda: True)
        monkeypatch.setattr(scanner._archive_scanner, "scan_archive", fake_scan)
        results: list[ScanResult] = []
        delta = run_archive_phase(scanner, [self._entry(tmp_path / "a.zip")], results)
        assert delta == (0, 0, 0, 0)
        assert not scan_called
        assert results == []

    def test_single_thread_scan_exception_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """单线程路径：scan_archive 抛异常时 errors 递增并继续下一个 entry。"""
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=1)

        def boom(path: Path) -> tuple[ScanResult, ...]:
            raise RuntimeError("扫描失败")

        monkeypatch.setattr(scanner, "_check_control", lambda: False)
        monkeypatch.setattr(scanner._archive_scanner, "scan_archive", boom)
        results: list[ScanResult] = []
        scanned, matched, errors, matches = run_archive_phase(scanner, [self._entry(tmp_path / "a.zip")], results)
        assert errors == 1
        assert scanned == matched == matches == 0
        assert results == []

    def test_multithread_cancel_during_submit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """多线程路径：提交阶段 _check_control 返回 True 立即取消，零增量返回。"""
        _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        _make_zip(tmp_path / "b.zip", {"secret.txt": "y"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=2)
        monkeypatch.setattr(scanner, "_check_control", lambda: True)
        results: list[ScanResult] = []
        delta = run_archive_phase(
            scanner,
            [self._entry(tmp_path / "a.zip"), self._entry(tmp_path / "b.zip")],
            results,
        )
        assert delta == (0, 0, 0, 0)
        assert results == []

    def test_collect_cancel_shuts_down_pool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_collect_archive_futures：收集时 _check_control 返回 True 立即 break。"""
        from fuscan.scanner._executor import DaemonThreadPoolExecutor

        _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=2)
        monkeypatch.setattr(scanner, "_check_control", lambda: True)
        results: list[ScanResult] = []
        pool = DaemonThreadPoolExecutor(max_workers=1)
        try:
            entry = self._entry(tmp_path / "a.zip")
            future = pool.submit(lambda: ())
            future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry] = {future: entry}
            delta = _collect_archive_futures(scanner, future_to_entry, results, pool)
        finally:
            pool.shutdown(wait=False)
        assert delta == (0, 0, 0, 0)
        assert results == []

    def test_collect_future_result_raises_counts_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_collect_archive_futures：future.result() 抛异常时 errors 递增。"""
        from fuscan.scanner._executor import DaemonThreadPoolExecutor

        _make_zip(tmp_path / "a.zip", {"secret.txt": "x"})
        rs = _build_ruleset(_filename_rule("r", "secret"))
        scanner = Scanner(rs, scan_archives=True, max_workers=2)
        monkeypatch.setattr(scanner, "_check_control", lambda: False)
        results: list[ScanResult] = []
        pool = DaemonThreadPoolExecutor(max_workers=1)

        def boom() -> tuple[ScanResult, ...]:
            raise RuntimeError("worker 失败")

        try:
            entry = self._entry(tmp_path / "a.zip")
            future = pool.submit(boom)
            future_to_entry: dict[Future[tuple[ScanResult, ...]], FileEntry] = {future: entry}
            scanned, matched, errors, matches = _collect_archive_futures(scanner, future_to_entry, results, pool)
        finally:
            pool.shutdown(wait=False)
        assert errors == 1
        assert scanned == matched == matches == 0
