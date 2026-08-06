"""多规则约束下解析性能剖析脚本。

针对 iter-73 优化后的 ``_content_buckets`` 模块在 **多规则场景** 下的解析性能
进行 cProfile 热点定位，对比三种典型规则集：

- **S1 内置规则**：加载 ``builtin.yaml``（14 条规则，11 条 CONTENT REGEX 进同一桶 +
  P0103 FILENAME + P0104 AND 组合）
- **S2 纯 CONTENT REGEX 大规则集**：50 条顶层纯 CONTENT REGEX 规则（全部进 CONTENT
  桶，验证桶合并 + 逐规则预筛 + 活跃子集动态编译在「桶内规则数较多」时的瓶颈）
- **S3 AND 组合规则大规则集**：50 条 AND 组合规则，每条含 2~3 个 CONTENT REGEX 子项
  （验证 AndMatcher.matches 递归 + 组合规则无法进桶 + 桶外逐条求值路径的瓶颈）

用法::

    uv run python benchmarks/multi_rule_profile.py
    uv run python benchmarks/multi_rule_profile.py --files 200 --size 8192
    uv run python benchmarks/multi_rule_profile.py --scenario builtin --top 30

输出：

- 各场景总耗时、扫描文件数、吞吐量
- cProfile 热点表（按 cumulative time 排序）
- 重点关注的 5 个函数（AndMatcher.matches / OrMatcher.matches /
  match_content_via_buckets / _compute_active_indices / _get_active_compiled）
  的 ncalls / tottime / cumtime 明细
- 三场景对比总结表
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import sys
import tempfile
import time
from pathlib import Path
from pstats import SortKey

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.sample_files import generate_file  # noqa: E402
from fuscan.rules.builtin import load_builtin_ruleset  # noqa: E402
from fuscan.rules.model import (  # noqa: E402
    AndMatch,
    LeafMatch,
    MatchMode,
    MatchTarget,
    Rule,
    RuleSet,
    Severity,
)
from fuscan.scanner import Scanner  # noqa: E402

__all__ = [
    "build_and_combo_ruleset",
    "build_content_regex_ruleset",
    "main",
    "profile_scenario",
]


# 重点关注的 5 个函数（按 fullname 部分匹配）
_HOTSPOT_PATTERNS: tuple[str, ...] = (
    "matchers.py:",
    "_content_buckets.py:",
    "AndMatcher.matches",
    "OrMatcher.matches",
    "LeafMatcher.matches",
    "match_content_via_buckets",
    "_compute_active_indices",
    "_get_active_compiled",
    "_apply_regex",
    "_apply_contains",
    "compile_regex_cached",
    "build_hit_from_match",
)


# ---------------------------------------------------------------------------
# 规则集构建
# ---------------------------------------------------------------------------


def build_content_regex_ruleset(count: int = 50) -> RuleSet:
    """构建 N 条顶层纯 CONTENT REGEX 规则的规则集（全部进 CONTENT 桶）。

    规则风格仿照 builtin.yaml：覆盖各类凭证 / 密钥 / 敏感赋值 / PII / 危险函数调用 /
    配置项等，模式复杂度与生产规则相当（含字符类、量词、命名捕获组、``|`` 分支等）。

    :param count: 规则数量（默认 50）
    :return: RuleSet（全部为顶层 LeafMatch(target=CONTENT, mode=REGEX)）
    """
    # 50 条真实风格的 CONTENT REGEX 模式（覆盖各类敏感信息）
    patterns: list[tuple[str, str, Severity]] = [
        # 凭证类（与 builtin 风格一致）
        ("AWS-Access-Key-ID", r"\b(AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b", Severity.CRITICAL),
        ("AWS-Secret-Access-Key", r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+=]{40}", Severity.CRITICAL),
        ("GitHub-Token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b", Severity.CRITICAL),
        ("GitHub-PAT", r"\bgithub_pat_[A-Za-z0-9_]{82,}\b", Severity.CRITICAL),
        ("Slack-Token", r"\bxox[abpr]-[A-Za-z0-9-]{10,72}\b", Severity.CRITICAL),
        ("JWT-Token", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", Severity.WARNING),
        ("Stripe-Key", r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{24,}\b", Severity.CRITICAL),
        ("GCP-API-Key", r"\bAIza[0-9A-Za-z_-]{35}\b", Severity.WARNING),
        ("Azure-SAS", r"(?i)sig=[A-Za-z0-9%+/=]{20,}.*(sv=|st=|se=|sr=|sp=)", Severity.CRITICAL),
        ("Azure-AccountKey", r"(?i)accountkey=[A-Za-z0-9+/=]{50,}", Severity.CRITICAL),
        (
            "Generic-API-Key",
            r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token)\s*[=:]\s*[A-Za-z0-9_\-./+=]{20,}",
            Severity.WARNING,
        ),
        ("Bearer-Token", r"(?i)bearer\s+[A-Za-z0-9_\-./+=]{20,}", Severity.WARNING),
        (
            "PEM-Private-Key",
            r"-----BEGIN\s+(RSA\s+|EC\s+|DSA\s+|OPENSSH\s+|PGP\s+)?PRIVATE\s+KEY-----",
            Severity.CRITICAL,
        ),
        ("OpenSSH-Private", r"-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----", Severity.CRITICAL),
        # 数据库连接
        ("MySQL-Conn-String", r"(?i)mysql://[^\s:]+:[^\s@]+@[^\s/]+/\w+", Severity.CRITICAL),
        ("Postgres-Conn", r"(?i)(postgres|postgresql)://[^\s:]+:[^\s@]+@[^\s/]+/\w+", Severity.CRITICAL),
        ("MongoDB-Conn", r"(?i)mongodb(\+srv)?://[^\s:]+:[^\s@]+@[^\s/]+", Severity.CRITICAL),
        ("Redis-Conn", r"(?i)redis://:[^\s@]+@[^\s/]+", Severity.CRITICAL),
        # 通用密码赋值
        ("Password-Assign", r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+", Severity.WARNING),
        ("User-Password", r"(?i)user[_-]?password\s*[=:]\s*\S+", Severity.WARNING),
        ("DB-Password", r"(?i)db[_-]?password\s*[=:]\s*\S+", Severity.WARNING),
        ("Admin-Password", r"(?i)admin[_-]?password\s*[=:]\s*\S+", Severity.WARNING),
        ("Root-Password", r"(?i)root[_-]?password\s*[=:]\s*\S+", Severity.WARNING),
        # PII / 个人信息
        ("Email-Address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", Severity.INFO),
        ("Phone-CN-Mobile", r"\b1[3-9]\d{9}\b", Severity.INFO),
        ("IDCard-CN", r"\b\d{17}[\dXx]\b", Severity.WARNING),
        ("BankCard-CN", r"\b62\d{14,17}\b", Severity.WARNING),
        ("IPv4-Address", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", Severity.INFO),
        ("IPv6-Address", r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b", Severity.INFO),
        # 配置 / 敏感项
        ("Secret-Key-Assign", r"(?i)secret[_-]?key\s*[=:]\s*[A-Za-z0-9_\-]{16,}", Severity.WARNING),
        ("Private-Key-Assign", r"(?i)private[_-]?key\s*[=:]\s*[A-Za-z0-9_\-]{16,}", Severity.WARNING),
        ("Client-Secret", r"(?i)client[_-]?secret\s*[=:]\s*[A-Za-z0-9_\-]{16,}", Severity.WARNING),
        ("Refresh-Token", r"(?i)refresh[_-]?token\s*[=:]\s*[A-Za-z0-9_\-./+=]{20,}", Severity.WARNING),
        ("Access-Token", r"(?i)access[_-]?token\s*[=:]\s*[A-Za-z0-9_\-./+=]{20,}", Severity.WARNING),
        # 危险函数 / 代码模式
        ("Eval-Call", r"(?i)\beval\s*\(", Severity.WARNING),
        ("Exec-Call", r"(?i)\bexec\s*\(", Severity.WARNING),
        ("System-Call", r"(?i)\bsystem\s*\(", Severity.WARNING),
        ("Popen-Call", r"(?i)\bPopen\s*\(", Severity.WARNING),
        ("Pickle-Load", r"(?i)\bpickle\.loads?\s*\(", Severity.WARNING),
        ("Yaml-Unsafe-Load", r"(?i)\byaml\.load\s*\(", Severity.WARNING),
        # SQL 注入迹象
        ("SQL-Union-Injection", r"(?i)union\s+select\s+.+\s+from\s+\w+", Severity.CRITICAL),
        ("SQL-Drop-Table", r"(?i)drop\s+table\s+\w+", Severity.CRITICAL),
        ("SQL-Insert-Into", r"(?i)insert\s+into\s+\w+\s+values", Severity.WARNING),
        # 中文敏感词
        ("CN-Sensitive-Price", r"(价格|内部|商业|薪酬|机密|绝密)", Severity.WARNING),
        ("CN-Internal-Marker", r"(内部资料|商业秘密|机密文件|绝密档案)", Severity.CRITICAL),
        # 框架特有
        ("Django-Secret-Key", r"(?i)SECRET_KEY\s*=\s*['\"][A-Za-z0-9_\-]{32,}['\"]", Severity.CRITICAL),
        ("Flask-Secret-Key", r"(?i)app\.secret_key\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]", Severity.CRITICAL),
        ("Rails-Secret-Key-Base", r"(?i)secret_key_base\s*=\s*['\"][A-Za-z0-9_\-]{32,}['\"]", Severity.CRITICAL),
        ("JWT-Secret-Assign", r"(?i)jwt[_-]?secret\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", Severity.WARNING),
        ("Slack-Webhook", r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+", Severity.CRITICAL),
    ]
    # 不足 count 时循环填充
    while len(patterns) < count:
        idx = len(patterns)
        base = patterns[idx % len(patterns)]
        patterns.append((f"{base[0]}-{idx}", base[1], base[2]))
    rules = tuple(
        Rule(
            name=f"P{idx + 1:04d}-{name}",
            description=f"测试规则 {idx + 1}: {name}",
            severity=sev,
            match=LeafMatch(
                target=MatchTarget.CONTENT,
                mode=MatchMode.REGEX,
                pattern=pat,
            ),
        )
        for idx, (name, pat, sev) in enumerate(patterns[:count])
    )
    return RuleSet(version="1.0", rules=rules)


def build_and_combo_ruleset(count: int = 50) -> RuleSet:
    """构建 N 条 AND 组合规则（每条含 2~3 个 CONTENT REGEX 子项）。

    所有规则均为 ``AndMatch(children=[LeafMatch(CONTENT, REGEX, ...), ...])``，
    顶层非 LeafMatch → 无法进 CONTENT 桶，全部走 ``_remaining_rules`` 逐条求值路径，
    AndMatcher.matches 递归调每个子 ContentMatcher.matches（每个子项独立 finditer）。

    规则模式风格与 build_content_regex_ruleset 一致，但每条由 2~3 个子模式 AND 组合，
    典型场景：检测「同时含敏感赋值 + 特定标识符」的复合规则（如 password= AND root）。

    :param count: 规则数量（默认 50）
    :return: RuleSet（全部为 AndMatch(CONTENT×2~3)）
    """
    # 子模式池：从中抽取 2~3 个组合成 AND 规则
    sub_patterns: list[tuple[str, str]] = [
        ("password-assign", r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"),
        ("api-key-assign", r"(?i)api[_-]?key\s*[=:]\s*[A-Za-z0-9_\-./+=]{20,}"),
        ("aws-key", r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
        ("github-token", r"\bghp_[A-Za-z0-9]{36,}\b"),
        ("jwt-token", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        ("mysql-conn", r"(?i)mysql://[^\s:]+:[^\s@]+@"),
        ("postgres-conn", r"(?i)postgres(ql)?://[^\s:]+:[^\s@]+@"),
        ("pem-key", r"-----BEGIN\s+(RSA\s+|EC\s+)?PRIVATE\s+KEY-----"),
        ("slack-token", r"\bxox[abpr]-[A-Za-z0-9-]{10,72}\b"),
        ("stripe-key", r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{24,}\b"),
        ("cn-price", r"(价格|内部|商业|薪酬|机密|绝密)"),
        ("cn-secret", r"(内部资料|商业秘密|机密文件|绝密档案)"),
        ("bearer-token", r"(?i)bearer\s+[A-Za-z0-9_\-./+=]{20,}"),
        ("secret-key", r"(?i)secret[_-]?key\s*[=:]\s*[A-Za-z0-9_\-]{16,}"),
        ("client-secret", r"(?i)client[_-]?secret\s*[=:]\s*[A-Za-z0-9_\-]{16,}"),
        ("refresh-token", r"(?i)refresh[_-]?token\s*[=:]\s*[A-Za-z0-9_\-./+=]{20,}"),
        ("gcp-api", r"\bAIza[0-9A-Za-z_-]{35}\b"),
        ("azure-accountkey", r"(?i)accountkey=[A-Za-z0-9+/=]{50,}"),
        ("eval-call", r"(?i)\beval\s*\("),
        ("exec-call", r"(?i)\bexec\s*\("),
        ("sql-union", r"(?i)union\s+select\s+.+\s+from\s+\w+"),
        ("sql-drop", r"(?i)drop\s+table\s+\w+"),
        ("phone-cn", r"\b1[3-9]\d{9}\b"),
        ("idcard-cn", r"\b\d{17}[\dXx]\b"),
        ("bankcard-cn", r"\b62\d{14,17}\b"),
        ("django-secret", r"(?i)SECRET_KEY\s*=\s*['\"][A-Za-z0-9_\-]{32,}['\"]"),
        ("flask-secret", r"(?i)app\.secret_key\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
        ("slack-webhook", r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"),
        ("redis-conn", r"(?i)redis://:[^\s@]+@[^\s/]+"),
        ("mongodb-conn", r"(?i)mongodb(\+srv)?://[^\s:]+:[^\s@]+@"),
    ]
    rules: list[Rule] = []
    for i in range(count):
        # 2~3 个子项循环抽取
        n_children = 2 + (i % 2)  # 交替 2/3 个子项
        children: list[LeafMatch] = []
        for j in range(n_children):
            name, pat = sub_patterns[(i * 3 + j) % len(sub_patterns)]
            children.append(
                LeafMatch(
                    target=MatchTarget.CONTENT,
                    mode=MatchMode.REGEX,
                    pattern=pat,
                    description=f"AND 子项 {j + 1}: {name}",
                )
            )
        rules.append(
            Rule(
                name=f"AND-{i + 1:04d}",
                description=f"AND 组合规则 {i + 1}（{n_children} 个 CONTENT 子项）",
                severity=Severity.WARNING,
                match=AndMatch(children=tuple(children)),
            )
        )
    return RuleSet(version="1.0", rules=tuple(rules))


# ---------------------------------------------------------------------------
# 测试文件生成
# ---------------------------------------------------------------------------


# 命中样本池：从每个场景的规则中提取一些「必然命中」的样本片段
_HIT_SAMPLES: tuple[str, ...] = (
    "password=admin123456",
    "api_key=AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "mysql://root:password@localhost:3306/db",
    "-----BEGIN RSA PRIVATE KEY-----",
    "xoxb-1234567890-abcdef",
    "AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "AccountKey=Aabcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ12==",
    "价格 内部 商业 薪酬 机密",
    "内部资料 商业秘密 机密文件",
    "SECRET_KEY = 'django-insecure-abcdefghijklmnopqrstuvwxyz0123456789'",
    "1[3-9]123456789",
    "bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "secret_key=abcdefghijklmnopqrstuvwxyz0123456789",
    "eval('malicious_code')",
    "exec(user_input)",
)

# 噪声文本（不含任何敏感模式）
_FILLER_LINES: tuple[str, ...] = (
    "the quick brown fox jumps over the lazy dog",
    "lorem ipsum dolor sit amet consectetur adipiscing elit",
    "this is a normal configuration file with no sensitive data",
    "documentation comment for the function below",
    "import os sys pathlib typing",
    "def helper_function(x, y): return x + y",
    "class NormalClass: pass",
    "# standard library imports",
    "return result if condition else default",
    "for item in collection: process(item)",
)


def _generate_test_files(
    root: Path,
    count: int,
    size_hint: int,
    seed: int = 42,
) -> list[Path]:
    """生成 N 个混合文本测试文件，约 30% 注入敏感命中样本。

    生成的文件均为 .txt（纯文本），避免提取器开销干扰解析热点测量；
    每个文件大小约 ``size_hint`` 字节，按行填充噪声文本，
    部分行替换为 ``_HIT_SAMPLES`` 中的样本（命中多类规则，验证多规则同时命中场景）。

    :param root: 输出目录
    :param count: 文件数
    :param size_hint: 单文件大小提示（字节）
    :param seed: 随机种子
    :return: 文件路径列表
    """
    import random

    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    exts = ("txt", "md", "yaml", "json", "csv", "html", "xml")
    for i in range(count):
        ext = rng.choice(exts)
        path = root / f"file_{i:05d}.{ext}"
        # 用 generate_file 生成对应格式的文件骨架
        generate_file(path, ext, size_hint, rng)
        # 在生成文件末尾追加 1~3 行命中样本（30% 概率）
        if rng.random() < 0.3:
            n_hits = rng.randint(1, 3)
            with path.open("a", encoding="utf-8") as f:
                for _ in range(n_hits):
                    sample = rng.choice(_HIT_SAMPLES)
                    f.write(f"\n# hit sample: {sample}\n")
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# cProfile 剖析
# ---------------------------------------------------------------------------


def _format_ms(seconds: float) -> str:
    """秒格式化为毫秒字符串。"""
    return f"{seconds * 1000:.2f}ms"


def _print_perf_summary(label: str, perf_summary: dict[str, dict[str, float]], duration: float) -> None:
    """打印 perf_summary 表格：阶段名 | 总耗时 | 占比 | 调用次数 | 平均 | 最大。"""
    print(f"\n=== {label} - perf_summary ===")
    print(f"总耗时: {_format_ms(duration)} ({duration:.4f}s)")
    if not perf_summary:
        print("  （无 perf_summary 数据）")
        return

    items = sorted(perf_summary.items(), key=lambda x: -x[1]["total_ms"])
    grand_total_ms = sum(info["total_ms"] for _, info in items)
    print(f"各阶段累计耗时: {_format_ms(grand_total_ms / 1000.0)}")
    print()

    header = f"{'阶段':<24} {'总计':>12} {'占比':>8} {'调用次数':>10} {'平均':>12} {'最大':>12}"
    print(header)
    print("-" * len(header))
    for name, info in items:
        total_ms = info["total_ms"]
        cnt = info["count"]
        max_ms = info["max_ms"]
        avg_ms = total_ms / cnt if cnt else 0.0
        ratio = total_ms / 1000.0 / duration * 100 if duration > 0 else 0.0
        print(
            f"{name:<24} {_format_ms(total_ms / 1000.0):>12} {ratio:>7.1f}% {cnt:>10} "
            f"{_format_ms(avg_ms / 1000.0):>12} {_format_ms(max_ms / 1000.0):>12}"
        )


def _print_hotspot_table(stats: pstats.Stats, top: int = 30) -> None:
    """打印 cProfile 热点函数表（按 cumulative time 排序，前 top 条）。"""
    print(f"\n=== cProfile 热点（cumulative time 前 {top}）===")
    buffer = io.StringIO()
    stats.stream = buffer  # type: ignore[method-assign]  # pstats.Stats 运行期可写 stream 属性
    stats.sort_stats(SortKey.CUMULATIVE).print_stats(top)
    output = buffer.getvalue()
    # 截取主体表格部分（去掉 pstats 头部冗余行）
    lines = output.splitlines()
    # 找到表头起始（ncalls）
    start_idx = 0
    for idx, line in enumerate(lines):
        if line.strip().startswith("ncalls"):
            start_idx = idx
            break
    for line in lines[start_idx:]:
        print(line)


def _print_hotspot_focus(stats: pstats.Stats) -> None:
    """打印重点关注的 5 个函数的 ncalls/tottime/cumtime 明细。"""
    print("\n=== 重点函数明细（按 fullname 部分匹配）===")
    # 取出所有 stats 条目
    raw: dict[tuple[str, int, str], tuple[int, int, float, float, dict[str, int]]] = stats.stats  # type: ignore[attr-defined]
    focused: list[tuple[str, float, float, int]] = []
    for (filename, _lineno, funcname), (_cc, nc, tt, ct, _callers) in raw.items():
        fullname = f"{Path(filename).name}:{funcname}"
        for pat in _HOTSPOT_PATTERNS:
            if pat in fullname:
                focused.append((fullname, tt, ct, nc))
                break
    if not focused:
        print("  （未匹配到重点函数）")
        return
    # 按 cumtime 降序
    focused.sort(key=lambda x: -x[2])
    header = f"{'函数':<52} {'ncalls':>10} {'tottime':>12} {'cumtime':>12}"
    print(header)
    print("-" * len(header))
    for fullname, tt, ct, nc in focused:
        print(f"{fullname:<52} {nc:>10} {_format_ms(tt):>12} {_format_ms(ct):>12}")


def profile_scenario(
    label: str,
    root: Path,
    scanner: Scanner,
    top: int = 30,
) -> dict[str, object]:
    """执行单场景 cProfile 剖析扫描并返回统计信息。

    用 cProfile 包装 ``scanner.scan(root)``，输出：

    1. perf_summary 各阶段耗时表（来自 ``ScanStats.perf_summary``）
    2. cProfile 热点函数表（cumulative time 前 ``top`` 条）
    3. 重点函数（AndMatcher.matches / match_content_via_buckets / ...）明细
    """
    print(f"\n{'=' * 80}")
    print(f"=== 场景: {label} ===")
    print(f"{'=' * 80}")

    profiler = cProfile.Profile()
    start = time.perf_counter()
    profiler.enable()
    report = scanner.scan(root)
    profiler.disable()
    duration = time.perf_counter() - start

    stats = pstats.Stats(profiler).strip_dirs()

    _print_perf_summary(label, report.stats.perf_summary or {}, duration)
    print()
    print(f"扫描文件数: {report.stats.scanned_files}")
    print(f"命中规则数: {sum(len(r.hits) for r in report.results)}")
    print(f"吞吐量: {report.stats.scanned_files / duration:.1f} files/s" if duration > 0 else "N/A")

    _print_hotspot_table(stats, top=top)
    _print_hotspot_focus(stats)

    return {
        "label": label,
        "duration": duration,
        "files": report.stats.scanned_files,
        "hits": sum(len(r.hits) for r in report.results),
        "files_per_sec": report.stats.scanned_files / duration if duration > 0 else 0.0,
        "perf_summary": report.stats.perf_summary or {},
    }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """脚本入口。"""
    parser = argparse.ArgumentParser(description="fuscan 多规则解析性能剖析")
    parser.add_argument("--files", type=int, default=100, metavar="N", help="测试文件数（默认 100）")
    parser.add_argument("--size", type=int, default=8192, metavar="N", help="单文件大小提示（字节，默认 8192）")
    parser.add_argument("--workers", type=int, default=1, metavar="N", help="并发线程数（默认 1，便于 cProfile 定位）")
    parser.add_argument(
        "--scenario",
        choices=("all", "builtin", "content_regex", "and_combo"),
        default="all",
        help="运行的场景（默认 all）",
    )
    parser.add_argument("--rule-count", type=int, default=50, metavar="N", help="S2/S3 场景规则数（默认 50）")
    parser.add_argument("--top", type=int, default=30, metavar="N", help="cProfile 热点表行数（默认 30）")
    parser.add_argument("--seed", type=int, default=42, metavar="N", help="随机种子")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "fuscan_multi_rule",
        metavar="DIR",
        help="工作目录",
    )
    args = parser.parse_args(argv)

    # 生成测试文件（一次生成，多场景复用）
    data_dir = args.workdir / "files"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    paths = _generate_test_files(data_dir, args.files, args.size, args.seed)
    total_bytes = sum(p.stat().st_size for p in paths if p.exists())
    print(f"已生成 {len(paths)} 个测试文件到 {data_dir}")
    print(f"总字节数: {total_bytes} ({total_bytes / 1024 / 1024:.2f} MB)")

    results: list[dict[str, object]] = []

    if args.scenario in ("all", "builtin"):
        rs_builtin = load_builtin_ruleset()
        scanner = Scanner(rs_builtin, max_workers=args.workers)
        result = profile_scenario(
            f"S1 内置规则（{len(rs_builtin.rules)} 条）",
            data_dir,
            scanner,
            top=args.top,
        )
        results.append(result)

    if args.scenario in ("all", "content_regex"):
        rs_content = build_content_regex_ruleset(args.rule_count)
        scanner = Scanner(rs_content, max_workers=args.workers)
        result = profile_scenario(
            f"S2 纯 CONTENT REGEX（{len(rs_content.rules)} 条）",
            data_dir,
            scanner,
            top=args.top,
        )
        results.append(result)

    if args.scenario in ("all", "and_combo"):
        rs_and = build_and_combo_ruleset(args.rule_count)
        scanner = Scanner(rs_and, max_workers=args.workers)
        result = profile_scenario(
            f"S3 AND 组合（{len(rs_and.rules)} 条 × 2~3 CONTENT 子项）",
            data_dir,
            scanner,
            top=args.top,
        )
        results.append(result)

    # 总结对比表
    print("\n" + "=" * 80)
    print("=== 场景对比 ===")
    print(f"{'场景':<48} {'耗时(s)':>10} {'文件/秒':>10} {'命中数':>10}")
    print("-" * 80)
    for r in results:
        print(
            f"{r['label']:<48} {r['duration']:>10.4f} "  # type: ignore[index]
            f"{r['files_per_sec']:>10.1f} {r['hits']:>10}"  # type: ignore[index]
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
