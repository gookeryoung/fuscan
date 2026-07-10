"""自定义提取器示例：注册新格式提取器扩展扫描能力。

演示如何：

1. 实现 Extractor 抽象基类
2. 注册到 default_registry
3. 让 Scanner 自动识别新扩展名

场景：扫描 .ini 配置文件时，希望提取 [section] + key=value 结构化文本。

运行：

    python examples/custom_extractor.py /path/to/scan rules/example.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple

from uniscan.extractors import Extractor, ExtractorError, default_registry
from uniscan.rules import load_ruleset
from uniscan.scanner import Scanner


class IniExtractor(Extractor):
    """INI 配置文件提取器。

    虽然纯文本提取器已能读取 .ini，但自定义提取器可做结构化处理，
    例如：剥离注释、合并多文件 section、规范化 key 大小写等。
    本示例仅演示注册流程，提取逻辑保持简单。
    """

    @property
    def supported_extensions(self) -> Tuple[str, ...]:
        return ("ini", "cfg", "conf")

    def extract(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise ExtractorError(f"INI 文件读取失败: {path}: {exc}") from exc

        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            # 保留 section 与 key=value，丢弃纯注释行
            if stripped and not stripped.startswith((";", "#")):
                lines.append(stripped)
        return "\n".join(lines)


def main(scan_path: Path, rules_path: Path) -> int:
    # 1. 注册自定义提取器（覆盖默认 TextExtractor 对 .ini/.cfg/.conf 的处理）
    default_registry.register(IniExtractor())
    print(f"已注册自定义提取器：{IniExtractor.__name__}")
    print(f"  支持扩展名：{IniExtractor().supported_extensions}")

    # 2. 加载规则并扫描
    ruleset = load_ruleset(rules_path)
    scanner = Scanner(ruleset)
    report = scanner.scan(scan_path)

    print(f"\n扫描完成：命中 {report.stats.matched_files} 个文件")
    for result in report.hits:
        print(f"  {result.path}")
        for hit in result.hits:
            print(f"    [{hit.severity.value}] {hit.rule_name}")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"用法：python {sys.argv[0]} <扫描路径> <规则文件>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
