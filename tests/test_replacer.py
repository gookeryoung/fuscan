"""replacer 模块单元测试。

覆盖：

- 文本模式替换（多规则、长词优先、不重叠）
- 二进制模式替换（非 UTF-8 文件回退）
- 不支持的文件类型拒绝
- 无 replace=True 规则的提示
- replace=True 但 replace_with 为空的提示
- 备份路径计算（保留相对路径 / 仅文件名 + 序号）
- 备份失败与替换失败的错误传播
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fuscan.replacer import (
    BatchReplaceResult,
    ReplaceResult,
    ReplaceStatus,
    is_text_file,
    replace_batch,
    replace_in_file,
    restore_from_backup,
)
from fuscan.rules.model import (
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner.result import RuleHit


def _make_rule(
    name: str,
    pattern: str,
    *,
    replace: bool = False,
    replace_with: str = "",
    mode: MatchMode = MatchMode.CONTAINS,
) -> Rule:
    """构造测试用 Rule（content 叶子匹配）。"""
    return Rule(
        name=name,
        match=LeafMatch(target=MatchTarget.CONTENT, mode=mode, pattern=pattern),
        severity=Severity.WARNING,
        replace=replace,
        replace_with=replace_with,
    )


def _make_hit(rule: Rule, match_texts: tuple[str, ...]) -> RuleHit:
    """构造测试用 RuleHit，携带指定 match_texts。"""
    return RuleHit(
        rule_name=rule.name,
        severity=rule.severity,
        detail=f"命中 {rule.name}",
        match_text=match_texts[0] if match_texts else "",
        match_count=len(match_texts),
        target="content",
        match_texts=match_texts,
    )


class TestIsTextFile:
    def test_text_extension_supported(self) -> None:
        assert is_text_file(Path("foo.txt"))
        assert is_text_file(Path("foo.py"))
        assert is_text_file(Path("foo.yaml"))
        assert is_text_file(Path("FOO.MD"))  # 大小写不敏感

    def test_binary_extension_rejected(self) -> None:
        assert not is_text_file(Path("foo.pdf"))
        assert not is_text_file(Path("foo.docx"))
        assert not is_text_file(Path("foo.xlsx"))

    def test_no_extension_rejected(self) -> None:
        assert not is_text_file(Path("README"))


class TestReplaceInFile:
    def test_text_replace_success(self, tmp_path: Path) -> None:
        """文本模式替换：源文件备份为 .bak，命中文本被替换为 replace_with。"""
        src = tmp_path / "src" / "a.txt"
        src.parent.mkdir()
        src.write_text("token=abc123\ntoken=def456\n", encoding="utf-8")
        rule = _make_rule("token检测", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))
        backup_root = tmp_path / "backup"
        scan_root = tmp_path

        result = replace_in_file(src, (hit,), ruleset, backup_root, scan_root)

        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 1
        assert result.backup_path is not None
        assert result.backup_path.suffix == ".bak"
        # 备份文件内容为原始内容
        assert result.backup_path.read_text(encoding="utf-8") == "token=abc123\ntoken=def456\n"
        # 源文件已被替换（所有 token 出现处都替换）
        assert src.read_text(encoding="utf-8") == "***=abc123\n***=def456\n"

    def test_multiple_rules_replace(self, tmp_path: Path) -> None:
        """多规则替换：每条规则的 match_texts 都被对应 replace_with 替换。"""
        src = tmp_path / "a.txt"
        src.write_text("password=secret and token=abc\n", encoding="utf-8")
        rule1 = _make_rule("密码", "password", replace=True, replace_with="PWD")
        rule2 = _make_rule("令牌", "token", replace=True, replace_with="TKN")
        hits = (_make_hit(rule1, ("password",)), _make_hit(rule2, ("token",)))
        ruleset = RuleSet(version="1.0", rules=(rule1, rule2))

        result = replace_in_file(src, hits, ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 2
        assert src.read_text(encoding="utf-8") == "PWD=secret and TKN=abc\n"

    def test_long_keyword_priority(self, tmp_path: Path) -> None:
        """长关键词优先替换，避免短关键词破坏长关键词匹配。"""
        src = tmp_path / "a.txt"
        src.write_text("api_key=xxx api=yyy\n", encoding="utf-8")
        rule = _make_rule("API", "api_key", replace=True, replace_with="REDACTED")
        # 同一规则命中两个关键词：长词 + 短词
        hit = _make_hit(rule, ("api_key", "api"))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        # 长词优先：先替换 api_key=xxx → REDACTED=xxx，再替换 api=yyy → REDACTED=yyy
        assert src.read_text(encoding="utf-8") == "REDACTED=xxx REDACTED=yyy\n"

    def test_unsupported_file_type(self, tmp_path: Path) -> None:
        """二进制扩展名（.pdf）直接拒绝替换。"""
        src = tmp_path / "a.pdf"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.UNSUPPORTED_FILE_TYPE
        assert "不支持" in result.message
        # 源文件未被修改
        assert src.read_text(encoding="utf-8") == "token=abc\n"

    def test_no_replace_rules(self, tmp_path: Path) -> None:
        """规则未启用 replace 时不进行任何操作。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token")  # replace 默认 False
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.NO_REPLACE_RULES
        assert src.read_text(encoding="utf-8") == "token=abc\n"

    def test_missing_replace_with(self, tmp_path: Path) -> None:
        """replace=True 但 replace_with 为空：返回提示，不做修改。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.MISSING_REPLACE_WITH
        assert "token" in result.missing_rules
        assert src.read_text(encoding="utf-8") == "token=abc\n"

    def test_backup_preserve_relative_path(self, tmp_path: Path) -> None:
        """preserve_relative=True：备份保留相对扫描根目录的目录结构。"""
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        src = scan_root / "sub" / "deep" / "a.txt"
        src.parent.mkdir(parents=True)
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))
        backup_root = tmp_path / "backup"

        result = replace_in_file(
            src,
            (hit,),
            ruleset,
            backup_root,
            scan_root,
            preserve_relative=True,
        )

        assert result.status == ReplaceStatus.SUCCESS
        assert result.backup_path is not None
        assert result.backup_path == backup_root / "sub" / "deep" / "a.txt.bak"
        assert result.backup_path.exists()

    def test_backup_filename_only_with_conflict(self, tmp_path: Path) -> None:
        """preserve_relative=False：仅文件名，冲突时追加序号。"""
        src1 = tmp_path / "a.txt"
        src1.write_text("token=abc\n", encoding="utf-8")
        src2 = tmp_path / "b.txt"
        src2.write_text("token=def\n", encoding="utf-8")
        # 制造同名冲突：把 src2 重命名为 a.txt（不同目录）
        sub = tmp_path / "sub"
        sub.mkdir()
        src2_conflict = sub / "a.txt"
        src2_conflict.write_text("token=def\n", encoding="utf-8")

        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))
        backup_root = tmp_path / "backup"

        # 第一次替换：a.txt.bak
        r1 = replace_in_file(src1, (hit,), ruleset, backup_root, tmp_path, preserve_relative=False)
        assert r1.status == ReplaceStatus.SUCCESS
        assert r1.backup_path == backup_root / "a.txt.bak"

        # 第二次替换：sub/a.txt → a.1.txt.bak（避免冲突）
        r2 = replace_in_file(
            src2_conflict,
            (hit,),
            ruleset,
            backup_root,
            tmp_path,
            preserve_relative=False,
        )
        assert r2.status == ReplaceStatus.SUCCESS
        assert r2.backup_path is not None
        assert r2.backup_path == backup_root / "a.1.txt.bak"
        assert r2.backup_path.exists()

    def test_backup_failed(self, tmp_path: Path) -> None:
        """备份区不可写（路径是文件而非目录）→ BACKUP_FAILED。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        # backup_root 是一个已存在的文件，mkdir 会失败
        bad_backup = tmp_path / "blocker"
        bad_backup.write_text("block", encoding="utf-8")

        result = replace_in_file(src, (hit,), ruleset, bad_backup, tmp_path)

        assert result.status == ReplaceStatus.BACKUP_FAILED
        # 源文件未被修改
        assert src.read_text(encoding="utf-8") == "token=abc\n"

    def test_rule_not_in_ruleset_skipped(self, tmp_path: Path) -> None:
        """RuleHit 对应的规则不在 ruleset 中（已被用户移除）→ 视为未启用替换。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        # 空 ruleset：rule_map 找不到 rule_name
        empty_ruleset = RuleSet(version="1.0", rules=())

        result = replace_in_file(src, (hit,), empty_ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.NO_REPLACE_RULES
        assert src.read_text(encoding="utf-8") == "token=abc\n"

    def test_replace_preserves_original_content_in_backup(self, tmp_path: Path) -> None:
        """shutil.copy2 备份文件内容与原始源文件一致（替换不影响备份）。"""
        src = tmp_path / "a.txt"
        original = "token=abc\nline2\n"
        src.write_text(original, encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        assert result.backup_path is not None
        # 备份内容应为原始内容（替换不影响备份）
        assert result.backup_path.read_text(encoding="utf-8") == original
        # 源文件已被修改
        assert src.read_text(encoding="utf-8") == "***=abc\nline2\n"

    def test_src_not_in_scan_root_fallback_to_filename(self, tmp_path: Path) -> None:
        """src 不在 scan_root 下（跨盘符或绝对路径）→ 回退到仅文件名（preserve_relative 失效）。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))
        backup_root = tmp_path / "backup"
        # scan_root 设为一个不包含 src 的目录，触发 ValueError 回退
        scan_root = tmp_path / "other_root"
        scan_root.mkdir()

        result = replace_in_file(
            src,
            (hit,),
            ruleset,
            backup_root,
            scan_root,
            preserve_relative=True,
        )

        assert result.status == ReplaceStatus.SUCCESS
        assert result.backup_path is not None
        # 回退到仅文件名模式：备份直接在 backup_root 下
        assert result.backup_path == backup_root / "a.txt.bak"

    def test_text_replace_write_back_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """文本替换写回时 OSError → REPLACE_FAILED，但备份已保留。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        # 拦截 _atomic_write_text 抛 OSError
        from fuscan import replacer as replacer_module

        def _raise_oserror(path: Path, content: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(replacer_module, "_atomic_write_text", _raise_oserror)

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.REPLACE_FAILED
        assert result.backup_path is not None
        assert "disk full" in result.message
        # 备份已保留
        assert result.backup_path.exists()

    def test_empty_match_texts_skipped(self, tmp_path: Path) -> None:
        """RuleHit.match_texts 含空字符串 → 跳过空关键词，其他关键词仍替换。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        # 空字符串 + 有效关键词
        hit = _make_hit(rule, ("", "token"))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 1
        assert src.read_text(encoding="utf-8") == "***=abc\n"

    def test_all_match_texts_empty_no_replacement(self, tmp_path: Path) -> None:
        """RuleHit.match_texts 全为空字符串 → replaced_count=0 但仍备份（文本模式）。"""
        src = tmp_path / "a.txt"
        src.write_text("token=abc\n", encoding="utf-8")
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("", ""))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        # match_texts 全空 → _apply_replace_text 返回 count=0，但仍走 SUCCESS 路径
        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 0
        assert result.backup_path is not None
        assert result.backup_path.exists()
        # 源文件未被修改
        assert src.read_text(encoding="utf-8") == "token=abc\n"


class TestReplaceInFileNonUtf8:
    def test_non_utf8_file_fallback_to_bytes(self, tmp_path: Path) -> None:
        """非 UTF-8 编码文件回退到二进制模式替换。"""
        src = tmp_path / "a.txt"
        # GBK 编码写入中文 + ASCII 关键词
        content_gbk = "密码=password\n".encode("gbk")
        src.write_bytes(content_gbk)
        rule = _make_rule("密码", "password", replace=True, replace_with="***")
        hit = _make_hit(rule, ("password",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        # 备份保留原 GBK 字节
        assert result.backup_path is not None
        assert result.backup_path.read_bytes() == content_gbk
        # 源文件中 password 已被 *** 替换（GBK 字节级替换）
        assert b"password" not in src.read_bytes()
        assert b"***" in src.read_bytes()

    def test_non_utf8_no_match_still_backed_up(self, tmp_path: Path) -> None:
        """非 UTF-8 文件命中关键词不在内容中 → count=0，备份仍保留。"""
        src = tmp_path / "a.txt"
        src.write_bytes("hello=world\n".encode("gbk"))
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 0
        assert result.backup_path is not None
        assert result.backup_path.exists()
        # 源文件未被修改
        assert result.backup_path.read_bytes() == "hello=world\n".encode("gbk")

    def test_non_utf8_replace_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 UTF-8 文件二进制写回时 OSError → REPLACE_FAILED，备份已保留。"""
        src = tmp_path / "a.txt"
        src.write_bytes("密码=password\n".encode("gbk"))
        rule = _make_rule("密码", "password", replace=True, replace_with="***")
        hit = _make_hit(rule, ("password",))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        from fuscan import replacer as replacer_module

        def _raise_oserror(path: Path, raw: bytes) -> None:
            raise OSError("write error")

        monkeypatch.setattr(replacer_module, "_atomic_write_bytes", _raise_oserror)

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.REPLACE_FAILED
        assert result.backup_path is not None
        assert result.backup_path.exists()

    def test_non_utf8_empty_match_texts_no_replacement(self, tmp_path: Path) -> None:
        """非 UTF-8 文件 + match_texts 全空 → bytes 替换 count=0，备份仍保留。"""
        src = tmp_path / "a.txt"
        src.write_bytes("密码=password\n".encode("gbk"))
        rule = _make_rule("密码", "password", replace=True, replace_with="***")
        # 全空 match_texts
        hit = _make_hit(rule, ("", ""))
        ruleset = RuleSet(version="1.0", rules=(rule,))

        result = replace_in_file(src, (hit,), ruleset, tmp_path / "backup", tmp_path)

        # 全空 → _apply_replace_bytes 返回 count=0
        assert result.status == ReplaceStatus.SUCCESS
        assert result.replaced_count == 0
        assert result.backup_path is not None
        assert result.backup_path.exists()

    def test_non_utf8_multiple_rules_partial_match(self, tmp_path: Path) -> None:
        """非 UTF-8 文件 + 多规则 + 部分关键词不命中 → 覆盖内/外层循环迭代分支。

        构造两条 replace=True 规则：规则1关键词命中，规则2关键词不命中。
        覆盖 :func:`_apply_replace_bytes` 中内层 for 循环继续（381->374）
        与外层 for 循环继续（384->372）两条分支。
        """
        src = tmp_path / "a.txt"
        # GBK 编码：包含 password 但不包含 token
        src.write_bytes("密码=password\n".encode("gbk"))
        rule1 = _make_rule("密码", "password", replace=True, replace_with="***")
        rule2 = _make_rule("令牌", "token", replace=True, replace_with="TKN")
        hits = (_make_hit(rule1, ("password",)), _make_hit(rule2, ("token",)))
        ruleset = RuleSet(version="1.0", rules=(rule1, rule2))

        result = replace_in_file(src, hits, ruleset, tmp_path / "backup", tmp_path)

        assert result.status == ReplaceStatus.SUCCESS
        # 只有 rule1 命中（password 被替换），rule2 的 token 不在内容中
        assert result.replaced_count == 1
        assert b"password" not in src.read_bytes()
        assert b"***" in src.read_bytes()

    def test_backup_filename_only_multiple_conflicts(self, tmp_path: Path) -> None:
        """preserve_relative=False + 多次同名冲突 → 序号递增至 2（覆盖 302->300 分支）。

        第一次冲突生成 a.1.txt.bak，第二次冲突生成 a.2.txt.bak，
        覆盖 :func:`_resolve_backup_path` for 循环中 i=1 候选已存在时继续到 i=2 的分支。
        """
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        hit = _make_hit(rule, ("token",))
        ruleset = RuleSet(version="1.0", rules=(rule,))
        backup_root = tmp_path / "backup"

        # 三个不同目录下的同名 a.txt
        src1 = tmp_path / "d1" / "a.txt"
        src1.parent.mkdir()
        src1.write_text("token=1\n", encoding="utf-8")
        src2 = tmp_path / "d2" / "a.txt"
        src2.parent.mkdir()
        src2.write_text("token=2\n", encoding="utf-8")
        src3 = tmp_path / "d3" / "a.txt"
        src3.parent.mkdir()
        src3.write_text("token=3\n", encoding="utf-8")

        r1 = replace_in_file(src1, (hit,), ruleset, backup_root, tmp_path, preserve_relative=False)
        assert r1.backup_path == backup_root / "a.txt.bak"

        r2 = replace_in_file(src2, (hit,), ruleset, backup_root, tmp_path, preserve_relative=False)
        assert r2.backup_path == backup_root / "a.1.txt.bak"

        # 第三次：a.1.txt.bak 已存在 → 循环到 i=2
        r3 = replace_in_file(src3, (hit,), ruleset, backup_root, tmp_path, preserve_relative=False)
        assert r3.backup_path is not None
        assert r3.backup_path == backup_root / "a.2.txt.bak"
        assert r3.backup_path.exists()


# ----------------------------- iter-113 批量替换与撤销 -----------------------------


from fuscan.scanner.result import ScanResult  # noqa: E402


def _make_scan_result(
    src: Path,
    rule: Rule,
    match_texts: tuple[str, ...],
    *,
    archive_path: Path | None = None,
) -> ScanResult:
    """构造测试用 ScanResult（携带单条命中）。"""
    hit = _make_hit(rule, match_texts)
    return ScanResult(
        path=src,
        size=src.stat().st_size if src.exists() else 0,
        hits=(hit,),
        archive_path=archive_path,
    )


class TestReplaceBatch:
    """iter-113 批量替换聚合结果。"""

    def test_batch_all_success(self, tmp_path: Path) -> None:
        """全部成功：succeeded=total，backup_paths 非空。"""
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        ruleset = RuleSet(version="1.0", rules=(rule,))
        src1 = tmp_path / "scan" / "a.txt"
        src1.parent.mkdir(parents=True)
        src1.write_text("token=abc\n", encoding="utf-8")
        src2 = tmp_path / "scan" / "b.txt"
        src2.write_text("token=def\n", encoding="utf-8")
        results = (
            _make_scan_result(src1, rule, ("token",)),
            _make_scan_result(src2, rule, ("token",)),
        )
        backup_root = tmp_path / "backup"

        batch = replace_batch(results, ruleset, backup_root, tmp_path / "scan", preserve_relative=True)

        assert batch.total == 2
        assert batch.succeeded == 2
        assert batch.skipped == 0
        assert batch.failed == 0
        assert batch.total_replaced_count == 2
        assert len(batch.backup_paths) == 2
        # 源文件已被替换
        assert src1.read_text(encoding="utf-8") == "***=abc\n"
        assert src2.read_text(encoding="utf-8") == "***=def\n"
        # message 含聚合信息
        assert "成功 2/2" in batch.message

    def test_batch_mixed_results(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """混合结果：成功 + 跳过（无 replace=True 规则）+ 失败（备份失败）。"""
        # 规则1：启用替换
        rule_replace = _make_rule("token", "token", replace=True, replace_with="***")
        # 规则2：未启用替换
        rule_no_replace = _make_rule("foo", "foo", replace=False)
        ruleset = RuleSet(version="1.0", rules=(rule_replace, rule_no_replace))
        # src1：成功替换
        src1 = tmp_path / "scan" / "a.txt"
        src1.parent.mkdir(parents=True)
        src1.write_text("token=abc\n", encoding="utf-8")
        # src2：跳过（命中的规则未启用 replace）
        src2 = tmp_path / "scan" / "b.txt"
        src2.write_text("foo=bar\n", encoding="utf-8")
        # src3：失败（备份失败，通过 monkeypatch 注入）
        src3 = tmp_path / "scan" / "c.txt"
        src3.write_text("token=xyz\n", encoding="utf-8")
        results = (
            _make_scan_result(src1, rule_replace, ("token",)),
            _make_scan_result(src2, rule_no_replace, ("foo",)),
            _make_scan_result(src3, rule_replace, ("token",)),
        )

        # 拦截 replace_in_file：仅 src3 失败，其他放行
        from fuscan import replacer as replacer_module

        original_replace = replacer_module.replace_in_file

        def _fake_replace(
            src: Path,
            hits: tuple[RuleHit, ...],
            ruleset: RuleSet,
            backup_root: Path,
            scan_root: Path,
            preserve_relative: bool = True,
        ) -> ReplaceResult:
            if src == src3:
                return ReplaceResult(
                    status=ReplaceStatus.BACKUP_FAILED,
                    message="备份文件失败: simulated",
                )
            return original_replace(src, hits, ruleset, backup_root, scan_root, preserve_relative)

        monkeypatch.setattr(replacer_module, "replace_in_file", _fake_replace)

        batch = replace_batch(
            results,
            ruleset,
            tmp_path / "backup",
            tmp_path / "scan",
            preserve_relative=True,
        )

        assert batch.total == 3
        assert batch.succeeded == 1
        assert batch.skipped == 1
        assert batch.failed == 1
        assert batch.total_replaced_count == 1
        # backup_paths 只含成功项
        assert len(batch.backup_paths) == 1
        # details 含每条结果
        assert len(batch.details) == 3

    def test_batch_archive_entry_skipped(self, tmp_path: Path) -> None:
        """压缩包内部条目被跳过（status=UNSUPPORTED_FILE_TYPE）。"""
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        ruleset = RuleSet(version="1.0", rules=(rule,))
        archive = tmp_path / "archive.zip"
        archive.write_bytes(b"fake archive")
        # 构造压缩包内部条目路径
        inner_path = tmp_path / "archive.zip!inner/file.txt"
        results = (_make_scan_result(inner_path, rule, ("token",), archive_path=archive),)

        batch = replace_batch(results, ruleset, tmp_path / "backup", tmp_path)

        assert batch.total == 1
        assert batch.succeeded == 0
        assert batch.skipped == 1
        assert batch.failed == 0
        assert batch.backup_paths == ()
        # details 中记录 UNSUPPORTED_FILE_TYPE
        _, detail_result = batch.details[0]
        assert detail_result.status == ReplaceStatus.UNSUPPORTED_FILE_TYPE

    def test_batch_empty_results(self, tmp_path: Path) -> None:
        """空结果列表：返回零值聚合结果。"""
        rule = _make_rule("token", "token", replace=True, replace_with="***")
        ruleset = RuleSet(version="1.0", rules=(rule,))

        batch = replace_batch((), ruleset, tmp_path / "backup", tmp_path)

        assert batch.total == 0
        assert batch.succeeded == 0
        assert batch.skipped == 0
        assert batch.failed == 0
        assert batch.total_replaced_count == 0
        assert batch.backup_paths == ()
        assert batch.details == ()


class TestRestoreFromBackup:
    """iter-113 从备份撤销替换。"""

    def test_restore_success(self, tmp_path: Path) -> None:
        """备份文件存在 → 成功恢复源文件。"""
        backup = tmp_path / "a.txt.bak"
        backup.write_text("original\n", encoding="utf-8")
        dest = tmp_path / "a.txt"
        dest.write_text("modified\n", encoding="utf-8")

        msg = restore_from_backup(backup, dest)

        assert msg.startswith("已从备份恢复")
        assert dest.read_text(encoding="utf-8") == "original\n"
        # 备份文件仍保留（便于多次撤销）
        assert backup.exists()

    def test_restore_backup_not_exist(self, tmp_path: Path) -> None:
        """备份文件不存在 → 返回提示消息。"""
        backup = tmp_path / "missing.txt.bak"
        dest = tmp_path / "dest.txt"

        msg = restore_from_backup(backup, dest)

        assert msg.startswith("备份文件不存在")
        assert "missing.txt.bak" in msg

    def test_restore_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """恢复过程 OSError → 返回失败消息。"""
        backup = tmp_path / "a.txt.bak"
        backup.write_text("original\n", encoding="utf-8")
        dest = tmp_path / "a.txt"

        from fuscan import replacer as replacer_module

        def _raise_oserror(src: Path, dst: Path) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(replacer_module.shutil, "copy2", _raise_oserror)

        msg = restore_from_backup(backup, dest)

        assert msg.startswith("恢复失败")
        assert "permission denied" in msg


class TestBatchReplaceResult:
    """BatchReplaceResult dataclass 行为。"""

    def test_message_with_zero_results(self) -> None:
        """空批量结果的 message 含 0/0。"""
        result = BatchReplaceResult(total=0, succeeded=0, skipped=0, failed=0, total_replaced_count=0)
        assert "成功 0/0" in result.message
        assert "共替换 0 条" in result.message

    def test_message_with_failures(self) -> None:
        """含失败时 message 含失败数。"""
        result = BatchReplaceResult(total=3, succeeded=2, skipped=0, failed=1, total_replaced_count=5)
        assert "失败 1" in result.message
        assert "共替换 5 条" in result.message

    def test_default_details_and_backup_paths_empty(self) -> None:
        """未提供 details/backup_paths 时默认为空元组。"""
        result = BatchReplaceResult(total=0, succeeded=0, skipped=0, failed=0, total_replaced_count=0)
        assert result.details == ()
        assert result.backup_paths == ()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
