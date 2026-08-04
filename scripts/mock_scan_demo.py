"""Mock 数据端到端扫描演示。

构造临时目录包含各类敏感文件，加载内置规则 + 3 个示例规则文件，
运行 Scanner.scan() 并格式化输出命中报告，验证文件收集与扫描规则的最新逻辑。

运行方式：
    uv run python scripts/mock_scan_demo.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from fuscan.rules import load_with_builtin
from fuscan.rules.model import RuleSet
from fuscan.scanner import Scanner
from fuscan.scanner.result import ScanReport

# ── 项目路径 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_RULES = [
    PROJECT_ROOT / "rules" / "examples" / "sensitive-data.yaml",
    PROJECT_ROOT / "rules" / "examples" / "security-audit.yaml",
    PROJECT_ROOT / "rules" / "examples" / "compliance.yaml",
]

# ── Mock 文件清单：(相对路径, 内容) ──
MOCK_FILES: list[tuple[str, str | bytes]] = [
    # 内置 P0102：硬编码密码赋值
    ("config/.env", "DATABASE_URL=postgres://localhost:5432\ndb_password = s3cretP@ss\napi_key=AKIAEXAMPLE\n"),
    # 内置 P0201：AWS Access Key ID + 内置 P0203：GitHub Token
    ("src/app.py", 'AKIAIOSFODNN7EXAMPLE\nghp_1234567890abcdefghijklmnopqrstuvwxyz\npassword = "hardcoded123"\n'),
    # 内置 P0101：PEM 私钥文件头
    ("keys/server.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\n"),
    # 示例 sensitive-data：日志文件中的手机号（AND: FILENAME + CONTENT）+ 银行卡号泄露
    ("logs/app.log", "用户 13812345678 登录成功\n银行卡号: 6222020200011111111\n警告: 13987654321 异常\n"),
    # 示例 compliance：数据库导出文件（FILENAME）
    ("data/backup.sql", "CREATE TABLE users (id INT, password VARCHAR(255));\n"),
    # 示例 compliance：编辑器临时文件（FILENAME）
    ("tmp/.server.conf.swp", b"\x56\x69\x6d\x20\x73\x77\x61\x70\x00"),  # 二进制 swap 文件头
    # 示例 compliance：疑似凭证文件（FILENAME: id_rsa.pem）
    ("secrets/id_rsa.pem", "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----\n"),
    # 示例 compliance：生产配置明文密码（AND: FILENAME .conf + CONTENT password + NOT path test/example）
    ("config/prod.conf", "db_host = 10.0.0.1\ndb_password = prod_secret_456\nport = 5432\n"),
    # 对照组：test 目录下的 .conf 应被 NOT path 排除（不触发 生产配置明文密码）
    ("test/config.conf", "db_password = test_secret_789\n"),
    # 对照组：普通 txt 无敏感内容（应无命中）
    ("readme.txt", "这是一个普通的项目说明文件，没有敏感信息。\n"),
]


def create_mock_files(root: Path) -> list[Path]:
    """在 root 下创建 mock 文件，返回所有创建的文件路径列表。"""
    created: list[Path] = []
    for rel_path, content in MOCK_FILES:
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content, encoding="utf-8")
        created.append(full)
    return created


def print_mock_files(root: Path) -> None:
    """打印 mock 文件清单。"""
    print("=" * 80)
    print("Mock 文件清单")
    print("=" * 80)
    for rel_path, _ in MOCK_FILES:
        size = (root / rel_path).stat().st_size
        print(f"  {rel_path:45s}  ({size} bytes)")
    print()


def print_ruleset_info(ruleset: RuleSet, user_paths: list[Path]) -> None:
    """打印加载的规则集信息。"""
    print("=" * 80)
    print("规则集加载")
    print("=" * 80)
    print("  内置规则: builtin.yaml")
    for p in user_paths:
        print(f"  示例规则: {p.name}")
    print(f"  合并后规则总数: {len(ruleset.rules)}")
    print(f"  scan_extensions: {ruleset.scan_extensions}")
    print()


def print_scan_report(report: ScanReport) -> None:
    """格式化打印扫描报告。"""
    print("=" * 80)
    print("扫描结果")
    print("=" * 80)
    print(f"  根目录: {report.root}")
    print(f"  总文件数: {report.stats.total_files}")
    print(f"  已扫描: {report.stats.scanned_files}")
    print(f"  跳过(后缀): {report.stats.skipped_files}")
    print(f"  命中文件数: {report.stats.matched_files}")
    print(f"  错误数: {report.stats.errors}")
    print()

    if not report.hits:
        print("  (无命中)")
        return

    print("-" * 80)
    print(f"{'文件路径':50s} | {'规则名':25s} | {'严重度':8s} | 详情")
    print("-" * 80)
    for result in report.results:
        if not result.has_hit:
            continue
        rel = result.path.relative_to(report.root)
        for hit in result.hits:
            print(f"  {rel!s:48s} | {hit.rule_name:23s} | {hit.severity.value:6s} | {hit.detail}")
    print()

    print("-" * 80)
    print("命中规则汇总")
    print("-" * 80)
    for name in report.rule_names:
        count = sum(1 for r in report.hits for h in r.hits if h.rule_name == name)
        print(f"  {name:35s}  命中 {count} 次")
    print()


def main() -> int:
    """主入口：创建 mock 数据 → 加载规则 → 扫描 → 输出报告 → 清理。"""
    # 加载规则
    ruleset = load_with_builtin(EXAMPLE_RULES)
    print_ruleset_info(ruleset, EXAMPLE_RULES)

    # 创建临时目录
    tmp_root = Path(tempfile.mkdtemp(prefix="fuscan_mock_"))
    try:
        create_mock_files(tmp_root)
        print_mock_files(tmp_root)

        # 扫描：覆盖 scan_extensions 以包含 mock 文件用到的所有后缀
        # （默认 scan_extensions 不含 py/pem/sql/swp/key，这里显式扩展）
        scanner = Scanner(
            ruleset,
            scan_extensions=(
                "conf",
                "ini",
                "env",
                "yaml",
                "yml",
                "properties",
                "json",
                "xml",
                "log",
                "txt",
                "csv",
                "pdf",
                "docx",
                "xlsx",
                "py",
                "pem",
                "sql",
                "swp",
                "key",
            ),
        )
        report = scanner.scan(tmp_root)
        print_scan_report(report)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        print(f"已清理临时目录: {tmp_root}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
