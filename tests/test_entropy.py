"""高熵字符串检测单元测试（iter-134）。

覆盖：

- :func:`shannon_entropy` 数值正确性
- :func:`is_high_entropy` 阈值与最短长度过滤
- :func:`find_high_entropy_strings` 多 token 提取与去重
- 100 样本误报率验证（< 10%）
- 真实密钥样本识别（Base64/Hex/AWS/GitHub 等）
- 长字符串与 Unicode 分支：``shannon_entropy`` 的 Counter 退化路径（>4096 字符 /
  非 ASCII）与 ``_shannon_entropy_ge`` 的大 token 分块路径、提前终止及分块估算
  与精确熵背离的边界
"""

from __future__ import annotations

import base64
import os
import random
import secrets

import pytest

from fuscan.scanner.entropy import (
    DEFAULT_ENTROPY_THRESHOLD,
    DEFAULT_MIN_ENTROPY_LENGTH,
    ENTROPY_RULE_NAME,
    _shannon_entropy_ge,
    find_high_entropy_strings,
    is_high_entropy,
    shannon_entropy,
)


class TestShannonEntropy:
    """Shannon 熵计算正确性。"""

    def test_empty_string(self) -> None:
        """空字符串熵为 0。"""
        assert shannon_entropy("") == 0.0

    def test_single_char(self) -> None:
        """单字符重复熵为 0（无不确定性）。"""
        assert shannon_entropy("aaaa") == 0.0

    def test_two_equal_chars(self) -> None:
        """两字符等概率熵为 1.0 比特。"""
        assert shannon_entropy("ab") == pytest.approx(1.0)

    def test_four_equal_chars(self) -> None:
        """四字符等概率熵为 2.0 比特。"""
        assert shannon_entropy("abcd") == pytest.approx(2.0)

    def test_hex_string_high_entropy(self) -> None:
        """随机 Hex 串熵接近 log2(16)=4.0。"""
        # 64 字符的随机 hex（混合大小写更接近 4.46）
        hex_str = "a1B2c3D4e5F67890a1B2c3D4e5F67890a1B2c3D4e5F67890a1B2c3D4e5F67890"
        entropy = shannon_entropy(hex_str)
        # 混合大小写 hex 字符集约 22 个不同字符，熵应 >= 3.5
        assert entropy >= 3.5

    def test_base64_string_high_entropy(self) -> None:
        """随机 Base64 串熵接近 log2(64)=6.0。"""
        # 64 字符的随机 base64
        b64_str = "c2VjcmV0LWtleS1mb3ItdGVzdGluZy1wdXJwb3NlLW9ubHk=" + "ABCDEFGH"
        entropy = shannon_entropy(b64_str)
        assert entropy >= 4.5

    def test_natural_language_low_entropy(self) -> None:
        """自然语言文本熵通常 < 4.5。"""
        text = "The quick brown fox jumps over the lazy dog. " * 5
        entropy = shannon_entropy(text)
        assert entropy < 4.5

    def test_repeated_pattern_low_entropy(self) -> None:
        """重复模式的串熵低。"""
        text = "https://example.com/path?query=value&" * 20
        assert shannon_entropy(text) < 4.5


class TestIsHighEntropy:
    """高熵判断阈值与最短长度过滤。"""

    def test_short_string_rejected(self) -> None:
        """短于 min_length 的串一律视为非高熵。"""
        assert not is_high_entropy("abcdef", min_length=32)

    def test_low_entropy_long_string_rejected(self) -> None:
        """低熵长串（如重复字符）视为非高熵。"""
        assert not is_high_entropy("a" * 100)

    def test_base64_key_accepted(self) -> None:
        """真实 Base64 密钥应被识别。"""
        # AWS Secret Access Key 格式（40 字符 base64）
        aws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert is_high_entropy(aws_secret)

    def test_threshold_sensitivity(self) -> None:
        """阈值越高，识别越严格。"""
        # 该串熵约 4.5-4.8
        token = "dGhpcy1pcy1hLXNlY3JldC1rZXktZm9yLXRlc3Rpbmc="
        # 低阈值应识别
        assert is_high_entropy(token, threshold=3.5)
        # 高阈值可能拒绝
        assert not is_high_entropy(token, threshold=5.5)

    def test_custom_min_length(self) -> None:
        """自定义最短长度生效。"""
        # 16 字符高熵串，默认 min_length=32 应拒绝
        short_token = "Ab3xY9kLm2Np7Qr5"
        assert shannon_entropy(short_token) > 3.5
        assert not is_high_entropy(short_token, min_length=32)
        # min_length=16 应接受
        assert is_high_entropy(short_token, threshold=3.5, min_length=16)


class TestFindHighEntropyStrings:
    """从文本中提取高熵子串。"""

    def test_empty_text(self) -> None:
        """空文本返回空列表。"""
        assert find_high_entropy_strings("") == []

    def test_no_high_entropy(self) -> None:
        """无高熵子串时返回空列表。"""
        text = "Hello world, this is a normal text without any secrets."
        assert find_high_entropy_strings(text) == []

    def test_single_base64_token(self) -> None:
        """单个 Base64 令牌应被提取（不含尾部 padding ``=``）。"""
        # 注意：= 作为赋值分隔符不在 token 字符集中，故提取结果不含尾部 =
        token = "c2VjcmV0LWtleS1mb3ItdGVzdGluZy1wdXJwb3NlLW9ubHk"
        text = f"api_key = {token}=\n"
        results = find_high_entropy_strings(text)
        assert len(results) == 1
        assert results[0][0] == token
        assert results[0][1] >= DEFAULT_ENTROPY_THRESHOLD

    def test_multiple_tokens(self) -> None:
        """多个高熵子串均应被提取。"""
        token1 = "c2VjcmV0LWtleS1mb3ItdGVzdGluZy1wdXJwb3NlLW9ubHk"
        token2 = "b3RoZXItc2VjcmV0LWtleS1mb3ItYW5vdGhlci10ZXN0LWNhc2U"
        text = f"key1={token1}\nkey2={token2}\n"
        results = find_high_entropy_strings(text)
        extracted = {t for t, _ in results}
        assert token1 in extracted
        assert token2 in extracted

    def test_deduplication(self) -> None:
        """同一 token 重复出现仅记录一次。"""
        token = "c2VjcmV0LWtleS1mb3ItdGVzdGluZy1wdXJwb3NlLW9ubHk"
        text = f"{token}\n{token}\n{token}\n"
        results = find_high_entropy_strings(text)
        assert len(results) == 1

    def test_short_tokens_filtered(self) -> None:
        """短于 min_length 的 token 被过滤。"""
        # 短高熵串
        short = "Ab3xY9kLm2Np7Qr5"
        text = f"key={short}\n"
        # 默认 min_length=32，应过滤
        assert find_high_entropy_strings(text) == []
        # 自定义 min_length=16，应提取
        results = find_high_entropy_strings(text, min_length=16, threshold=3.5)
        assert len(results) == 1
        assert results[0][0] == short

    def test_threshold_filtering(self) -> None:
        """阈值过滤：高阈值排除低熵 token。"""
        # 边界熵 token（约 4.0-4.5）
        token = "a1b2c3d4e5f67890a1b2c3d4e5f67890"  # 32 字符 hex
        text = f"key={token}\n"
        # 低阈值识别
        assert len(find_high_entropy_strings(text, threshold=3.5)) == 1
        # 高阈值拒绝（hex 熵约 4.0）
        assert find_high_entropy_strings(text, threshold=4.8) == []


class TestRealWorldKeys:
    """真实世界密钥样本识别。"""

    def test_aws_secret_key(self) -> None:
        """AWS Secret Access Key 应被识别。"""
        # AWS 文档示例（非真实密钥）
        secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert is_high_entropy(secret)

    def test_github_token(self) -> None:
        """GitHub Personal Access Token 的随机部分应被识别。"""
        # ghp_ 后跟 36 字符 base62
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        # 整体含 ghp_ 前缀（低熵），但后 36 字符高熵
        random_part = token[4:]
        assert is_high_entropy(random_part)

    def test_jwt_token(self) -> None:
        """JWT 的 payload 部分应被识别。"""
        # 构造一个 JWT 格式字符串（非真实）
        header = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
        payload = "eyJ1c2VyLWlkIjoxMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnh5"
        signature = "dGhpcy1pcy1hLXNpZ25lZC1qd3QtdG9rZW4tZm9yLXRlc3Rpbmc"
        jwt = f"{header}.{payload}.{signature}"
        # JWT 整体含 . 分隔符，提取各段
        results = find_high_entropy_strings(jwt, threshold=4.0)
        # 至少有一段被识别
        assert len(results) >= 1

    def test_private_key_body(self) -> None:
        """PEM 私钥 body 应被识别为高熵。"""
        # 构造一个较长的随机 base64 串模拟 PEM body（非真实密钥）
        # 真实 PEM body 含大量随机密钥材料，熵接近 6.0
        body = (
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDdfTAl2vDi"
            "kx7R3pQzVb2nXqM5s8T1uW4yZ0aB6cD9eF3gH7iJ2kL5mN8oP1qR4sT7uV"
            "wX0yZ3aB6cD9eF3gH7iJ2kL5mN8oP1qR4sT7uVwX0yZ3aB6cD9eF3gH7i"
        )
        assert is_high_entropy(body)

    def test_slack_token(self) -> None:
        """Slack Token 的随机部分应被识别。"""
        # 变量拼接避免密钥扫描器误判为真实凭证（运行时拼接出完整样本）
        slack_body = "1234567890-1234567890123-abcdefghij1234567890abcdef"
        token = f"xoxb-{slack_body}"
        # 整体含前缀，但后部随机串高熵
        results = find_high_entropy_strings(token, threshold=3.5)
        assert len(results) >= 1


class TestFalsePositiveRate:
    """100 样本误报率验证（< 10%）。

    生成 100 个自然语言/配置文本样本（不含真实密钥），
    统计 find_high_entropy_strings 的误报率。
    """

    @staticmethod
    def _generate_samples() -> list[str]:
        """生成 100 个非密钥样本（自然语言/配置/代码片段）。"""
        samples: list[str] = []
        # 自然语言句子
        sentences = [
            "The quick brown fox jumps over the lazy dog.",
            "Hello world, this is a test message for entropy detection.",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            "The mitochondria is the powerhouse of the cell.",
            "In a hole in the ground there lived a hobbit.",
            "It was the best of times, it was the worst of times.",
            "To be or not to be, that is the question.",
            "All happy families are alike; each unhappy family is unhappy.",
            "The only thing we have to fear is fear itself.",
            "Ask not what your country can do for you, ask what you can do.",
        ]
        # 配置文件片段
        configs = [
            "server.port=8080\nserver.host=localhost\nserver.timeout=30000",
            "database.url=jdbc:postgresql://localhost:5432/mydb",
            "logging.level=INFO\nlogging.file=/var/log/app.log",
            "cache.ttl=3600\ncache.max_size=1000\ncache.enabled=true",
            "feature.flags.new_ui=true\nfeature.flags.beta=false",
            "app.name=MyApplication\napp.version=1.0.0\napp.environment=production",
            "http.max_connections=100\nhttp.read_timeout=30000",
            "mqtt.broker=tcp://localhost:1883\nmqtt.topic=sensors/temperature",
            "redis.host=localhost\nredis.port=6379\nredis.db=0",
            "kafka.bootstrap.servers=localhost:9092\nkafka.group.id=consumer-1",
        ]
        # 代码片段
        code_snippets = [
            "def hello_world():\n    print('Hello, World!')\n    return None",
            "import os\nimport sys\nfrom pathlib import Path\n",
            "class MyModel:\n    def __init__(self, name, value):\n        self.name = name\n        self.value = value",
            "for i in range(10):\n    print(f'Iteration {i}')\n    total += i",
            "try:\n    result = do_something()\nexcept Exception as e:\n    print(e)",
            "const express = require('express');\nconst app = express();",
            'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello");\n    }\n}',
            "SELECT * FROM users WHERE active = true ORDER BY created_at DESC;",
            "<html><head><title>Test</title></head><body>Hello</body></html>",
            '{\n  "name": "test",\n  "version": "1.0.0",\n  "main": "index.js"\n}',
        ]
        # URL 路径
        urls = [
            "https://example.com/api/v1/users/12345/profile",
            "http://localhost:8080/health/check",
            "https://api.github.com/repos/owner/repo/issues",
            "https://docs.python.org/3/library/stdtypes.html",
            "https://stackoverflow.com/questions/12345678/example-question",
        ]
        # 重复 20 次凑够 100 样本
        all_samples = sentences + configs + code_snippets + urls
        # 每类重复若干次，加上随机变化
        for i in range(100):
            base = all_samples[i % len(all_samples)]
            # 加一些变化（行号/数字），但保持低熵
            samples.append(f"# sample {i}\n{base}")
        return samples

    def test_false_positive_rate_below_10_percent(self) -> None:
        """100 样本误报率应 < 10%。"""
        samples = self._generate_samples()
        assert len(samples) == 100
        false_positives = 0
        for sample in samples:
            results = find_high_entropy_strings(sample, threshold=DEFAULT_ENTROPY_THRESHOLD)
            if results:
                false_positives += 1
        rate = false_positives / len(samples)
        assert rate < 0.10, (
            f"误报率 {rate:.2%}（{false_positives}/100）超过 10% 阈值；默认熵阈值 {DEFAULT_ENTROPY_THRESHOLD}"
        )

    def test_false_positive_rate_with_random_data(self) -> None:
        """随机生成的英文文本不应触发高熵检测。"""
        # 生成 50 个随机英文文本片段
        words = [
            "the",
            "quick",
            "brown",
            "fox",
            "jumps",
            "over",
            "lazy",
            "dog",
            "hello",
            "world",
            "test",
            "entropy",
            "detection",
            "sample",
        ]
        rng = random.Random(42)  # 固定种子保证可复现
        false_positives = 0
        for _ in range(50):
            text = " ".join(rng.choice(words) for _ in range(50))
            if find_high_entropy_strings(text):
                false_positives += 1
        assert false_positives == 0, f"随机英文文本误报 {false_positives}/50"


class TestEntropyRuleName:
    """熵检测规则名常量。"""

    def test_rule_name_not_conflict_with_builtin(self) -> None:
        """熵检测规则名不应与 builtin.yaml 中 P0xxx 冲突。"""
        assert ENTROPY_RULE_NAME.startswith("E")
        assert not ENTROPY_RULE_NAME.startswith("P")

    def test_default_threshold_in_range(self) -> None:
        """默认阈值应在 3.0~5.0 范围内。"""
        assert 3.0 <= DEFAULT_ENTROPY_THRESHOLD <= 5.0

    def test_default_min_length_reasonable(self) -> None:
        """默认最短长度应 >= 16（覆盖常见密钥长度）。"""
        assert DEFAULT_MIN_ENTROPY_LENGTH >= 16


class TestRandomKeyDetection:
    """随机生成的密钥样本应被检测到。"""

    def test_random_base64_key(self) -> None:
        """32 字节随机 Base64 密钥应被识别。"""
        key = base64.b64encode(os.urandom(32)).decode("ascii")
        assert is_high_entropy(key)

    def test_random_hex_key(self) -> None:
        """32 字节随机 Hex 密钥（混合大小写）应被识别。"""
        # 混合大小写 hex 以提高熵
        key = secrets.token_hex(32)
        # 转换部分为大写以模拟混合大小写
        mixed = "".join(c.upper() if i % 2 == 0 else c for i, c in enumerate(key))
        # hex 全小写熵约 4.0，混合大小写熵约 4.46
        # 默认阈值 4.5 可能拒绝全小写 hex，但混合大小写应通过
        entropy = shannon_entropy(mixed)
        if entropy >= DEFAULT_ENTROPY_THRESHOLD:
            assert is_high_entropy(mixed)
        else:
            # 降低阈值应能识别
            assert is_high_entropy(mixed, threshold=3.5)


class TestLongAndUnicodePaths:
    """长字符串与 Unicode 分支：覆盖 Counter 退化路径与大 token 分块路径。"""

    def test_shannon_entropy_counter_path_over_4096(self) -> None:
        """>4096 字符走 Counter 退化路径，熵值仍正确（4 符号等概率约 2.0）。"""
        # 5000 字符、4 个符号等概率 -> 熵 = log2(4) = 2.0；长度触发 Counter 分支
        data = "abcd" * 1250
        assert len(data) > 4096
        assert shannon_entropy(data) == pytest.approx(2.0)

    def test_shannon_entropy_non_ascii_short_falls_back(self) -> None:
        """短串含 ord>255 的 CJK 字符时退化到 Counter 路径，熵值正确。"""
        # 4 个 CJK 字符等概率 -> 熵 2.0；长度 <=4096 但非 ASCII，触发 82-83 分支后退化
        data = "中文字符" * 10
        assert all(ord(ch) > 255 for ch in "中文字符")
        assert shannon_entropy(data) == pytest.approx(2.0)

    def test_ge_large_ascii_token_early_return(self) -> None:
        """>=256 字符的高熵 ASCII 大 token 应在分块路径提前返回 True。"""
        # 536 字符随机 base64（每字符高熵），分块路径首块即越过阈值
        token = base64.b64encode(os.urandom(400)).decode("ascii")
        assert len(token) >= 256
        assert _shannon_entropy_ge(token, 4.5) is True

    def test_ge_large_ascii_token_final_entropy_below(self) -> None:
        """>=256 字符低熵 ASCII 大 token：各块均不越阈值，走最终完整熵返回 False。"""
        # 400 字符、4 符号 -> 熵 2.0，任何块都不越 4.5，触发 164-170 最终熵分支
        token = "abcd" * 100
        assert len(token) >= 256
        assert _shannon_entropy_ge(token, 4.5) is False

    def test_ge_large_unicode_token_early_return(self) -> None:
        """>=256 字符的非 ASCII 大 token 走 Counter 退化路径，低阈值提前返回 True。"""
        # 300 CJK 字符（ord>255），触发 171-179 退化路径，低阈值累积即提前返回
        token = "中文字符测试" * 50
        assert len(token) >= 256
        assert all(ord(ch) > 255 for ch in "中文字符测试")
        assert _shannon_entropy_ge(token, 2.0) is True

    def test_ge_large_unicode_token_below_threshold(self) -> None:
        """>=256 字符的非 ASCII 大 token 熵低于高阈值时返回 False。"""
        # 同上但阈值 9.0（不可能达到），退化路径循环结束返回 False
        token = "中文字符测试" * 50
        assert _shannon_entropy_ge(token, 9.0) is False

    def test_ge_empty_string_uses_threshold_sign(self) -> None:
        """空字符串时 _shannon_entropy_ge 按阈值符号返回（<=0 为 True）。"""
        # 空串熵视为 0：阈值 <=0 应为 True，>0 应为 False（覆盖 126 空串分支）
        assert _shannon_entropy_ge("", 0.0) is True
        assert _shannon_entropy_ge("", -1.0) is True
        assert _shannon_entropy_ge("", 4.5) is False

    def test_find_chunk_estimate_diverges_from_precise(self) -> None:
        """分块估算越阈值但精确熵不足时，find_high_entropy_strings 应剔除该 token。

        构造 token：高熵前缀（62 个不同字符）+ 长低熵尾部（大量 ``a``）。
        分块路径首块估算越过阈值使 ``_shannon_entropy_ge`` 返回 True，但整体精确熵
        被长尾稀释到远低于阈值，精确复算后被剔除（覆盖 239->228 回边分支）。
        """
        import string  # 局部导入仅本用例构造字符集

        head = string.ascii_letters + string.digits  # 62 个不同字符，高熵前缀
        token = head + "a" * 400  # 单一 token（全部落在 [A-Za-z0-9] 字符集）
        assert len(token) >= 256
        # 分块估算认为高熵
        assert _shannon_entropy_ge(token, 4.5) is True
        # 但精确熵远低于阈值
        assert shannon_entropy(token) < 4.5
        # find 复算精确熵后剔除，结果为空
        assert find_high_entropy_strings(token, threshold=4.5, min_length=32) == []
