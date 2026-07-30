"""SARIF 导出格式与统一导出入口单元测试（iter-121）。

覆盖 :meth:`ScanReport.to_sarif` 的 SARIF v2.1.0 格式正确性，
以及 :func:`export_report` 统一导出入口的格式分发逻辑。

SARIF（Static Analysis Results Interchange Format）是 OASIS 标准，
GitHub Code Scanning 原生支持。本测试验证 fuscan 生成的 SARIF 符合规范。
"""

from __future__ import annotations

import json
from pathlib import Path

from fuscan import __version__
from fuscan.export.report import export_report, save_report
from fuscan.rules.model import Severity
from fuscan.scanner import ScanReport, ScanResult
from fuscan.scanner.result import RuleHit, ScanStats


def _build_report(tmp_path: Path) -> ScanReport:
    """构造测试报告：2 个文件命中 3 条规则，覆盖三个严重等级。"""
    results = (
        ScanResult(
            path=tmp_path / "secret.txt" / "a.txt",
            size=10,
            hits=(
                RuleHit("敏感文件名", Severity.WARNING, "文件名含 secret", match_count=1, target="filename"),
                RuleHit("密钥内容", Severity.CRITICAL, "内容含 AKIA 密钥", match_count=2, target="content"),
            ),
        ),
        ScanResult(
            path=tmp_path / "info.log",
            size=20,
            hits=(RuleHit("日志文件", Severity.INFO, "日志文件名", match_count=1, target="filename"),),
        ),
    )
    stats = ScanStats(
        total_files=2,
        scanned_files=2,
        matched_files=2,
        skipped_files=0,
        errors=0,
        duration_seconds=0.5,
        total_matches=4,
    )
    return ScanReport(root=tmp_path, results=results, stats=stats)


def _build_empty_report(tmp_path: Path) -> ScanReport:
    """构造无命中报告。"""
    return ScanReport(root=tmp_path, results=(), stats=ScanStats())


def _build_archive_report(tmp_path: Path) -> ScanReport:
    """构造压缩包内部条目报告。"""
    archive_path = tmp_path / "data.zip"
    results = (
        ScanResult(
            path=tmp_path / "data.zip" / "inner" / "secret.txt",
            size=10,
            hits=(RuleHit("密钥", Severity.CRITICAL, "压缩包内密钥", match_count=1),),
            archive_path=archive_path,
        ),
    )
    return ScanReport(root=tmp_path, results=results, stats=ScanStats(matched_files=1, total_matches=1))


# ---------------------------------------------------------------------------
# SARIF 格式正确性
# ---------------------------------------------------------------------------


class TestSarifFormat:
    """to_sarif() 生成的 SARIF v2.1.0 格式正确性。"""

    def test_sarif_basic_structure(self, tmp_path: Path) -> None:
        """SARIF 应含 $schema/version/runs 顶层字段。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())

        assert data["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
        assert data["version"] == "2.1.0"
        assert len(data["runs"]) == 1

    def test_sarif_tool_driver(self, tmp_path: Path) -> None:
        """tool.driver 应含 name/version/informationUri。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        driver = data["runs"][0]["tool"]["driver"]

        assert driver["name"] == "fuscan"
        assert driver["version"] == __version__
        assert driver["informationUri"] == "https://github.com/gookeryoung/fuscan"

    def test_sarif_results_count(self, tmp_path: Path) -> None:
        """每条 RuleHit 对应一个 SARIF result。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        results = data["runs"][0]["results"]

        # 2 个文件 + 3 条 RuleHit = 3 个 result
        assert len(results) == 3

    def test_sarif_severity_mapping(self, tmp_path: Path) -> None:
        """Severity 应正确映射到 SARIF level（CRITICAL→error, WARNING→warning, INFO→note）。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        levels = {r["level"] for r in data["runs"][0]["results"]}

        assert "error" in levels  # CRITICAL
        assert "warning" in levels  # WARNING
        assert "note" in levels  # INFO

    def test_sarif_result_fields(self, tmp_path: Path) -> None:
        """每个 result 应含 ruleId/level/message/locations/properties。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        result = data["runs"][0]["results"][0]

        assert "ruleId" in result
        assert "level" in result
        assert "message" in result
        assert "text" in result["message"]
        assert "locations" in result
        assert len(result["locations"]) == 1
        assert "physicalLocation" in result["locations"][0]
        assert "artifactLocation" in result["locations"][0]["physicalLocation"]
        assert "uri" in result["locations"][0]["physicalLocation"]["artifactLocation"]
        assert "properties" in result

    def test_sarif_rule_id(self, tmp_path: Path) -> None:
        """ruleId 应为规则名。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        rule_ids = {r["ruleId"] for r in data["runs"][0]["results"]}

        assert "敏感文件名" in rule_ids
        assert "密钥内容" in rule_ids
        assert "日志文件" in rule_ids

    def test_sarif_empty_results(self, tmp_path: Path) -> None:
        """无命中时 results 应为空数组（非 None）。"""
        report = _build_empty_report(tmp_path)
        data = json.loads(report.to_sarif())

        assert data["runs"][0]["results"] == []

    def test_sarif_relative_path_uri(self, tmp_path: Path) -> None:
        """uri 应为相对 root 的路径。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for r in data["runs"][0]["results"]}

        # 路径应相对于 root（tmp_path）
        assert "secret.txt/a.txt" in uris or "secret.txt\\a.txt" in uris
        assert "info.log" in uris

    def test_sarif_absolute_path_when_outside_root(self, tmp_path: Path) -> None:
        """文件路径不在 root 下时，uri 用绝对路径。"""

    # 不能用 tmp_path 构造（root 必须是合法目录），用固定路径测试
    report = ScanReport(
        root=Path("/nonexistent/root"),
        results=(
            ScanResult(
                path=Path("/other/path/file.txt"),
                size=10,
                hits=(RuleHit("规则", Severity.WARNING, "d"),),
            ),
        ),
        stats=ScanStats(matched_files=1, total_matches=1),
    )
    data = json.loads(report.to_sarif())
    uri = data["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    # 不同操作系统路径分隔符不同，仅验证包含 file.txt
    assert "file.txt" in uri

    def test_sarif_archive_entry_message(self, tmp_path: Path) -> None:
        """压缩包内部条目应在 message.text 中附加标注。"""
        report = _build_archive_report(tmp_path)
        data = json.loads(report.to_sarif())
        result = data["runs"][0]["results"][0]

        assert "压缩包" in result["message"]["text"]
        assert "data.zip" in result["message"]["text"]

    def test_sarif_properties_severity(self, tmp_path: Path) -> None:
        """properties.severity 应保留原始 Severity 值。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        severities = {r["properties"]["severity"] for r in data["runs"][0]["results"]}

        assert "critical" in severities
        assert "warning" in severities
        assert "info" in severities

    def test_sarif_properties_match_count(self, tmp_path: Path) -> None:
        """properties.matchCount 应保留 match_count。"""
        report = _build_report(tmp_path)
        data = json.loads(report.to_sarif())
        # 找到密钥内容的 result（match_count=2）
        key_result = next(r for r in data["runs"][0]["results"] if r["ruleId"] == "密钥内容")
        assert key_result["properties"]["matchCount"] == 2

    def test_sarif_valid_json(self, tmp_path: Path) -> None:
        """to_sarif() 返回值应为合法 JSON 字符串。"""
        report = _build_report(tmp_path)
        sarif_str = report.to_sarif()
        # json.loads 不抛异常即合法
        data = json.loads(sarif_str)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# to_format 分发器
# ---------------------------------------------------------------------------


class TestToFormatSarif:
    """to_format("sarif") 分发器。"""

    def test_to_format_sarif_returns_json(self, tmp_path: Path) -> None:
        """to_format("sarif") 应返回 SARIF JSON 字符串。"""
        report = _build_report(tmp_path)
        result = report.to_format("sarif")
        data = json.loads(result)
        assert data["version"] == "2.1.0"

    def test_to_format_unknown_falls_back_to_text(self, tmp_path: Path) -> None:
        """未知格式应回退到 text。"""
        report = _build_report(tmp_path)
        result = report.to_format("unknown")
        assert "扫描路径" in result


# ---------------------------------------------------------------------------
# export_report 统一导出入口
# ---------------------------------------------------------------------------


class TestExportReport:
    """export_report() 统一导出入口。"""

    def test_export_sarif_by_extension(self, tmp_path: Path) -> None:
        """根据 .sarif 扩展名导出 SARIF 文件。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.sarif"
        export_report(report, out)

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["version"] == "2.1.0"

    def test_export_csv_by_extension(self, tmp_path: Path) -> None:
        """根据 .csv 扩展名导出 CSV 文件。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.csv"
        export_report(report, out)

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "path,archive_path" in content

    def test_export_json_by_extension(self, tmp_path: Path) -> None:
        """根据 .json 扩展名导出 JSON 文件。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.json"
        export_report(report, out)

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "root" in data

    def test_export_text_by_unknown_extension(self, tmp_path: Path) -> None:
        """未知扩展名导出可读文本。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.log"
        export_report(report, out)

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "扫描路径" in content

    def test_export_with_explicit_fmt(self, tmp_path: Path) -> None:
        """显式 fmt 参数应覆盖扩展名推断。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.txt"  # 扩展名是 .txt
        export_report(report, out, fmt="sarif")

        content = out.read_text(encoding="utf-8")
        data = json.loads(content)
        assert data["version"] == "2.1.0"

    def test_export_pdf_by_extension(self, tmp_path: Path) -> None:
        """根据 .pdf 扩展名导出 PDF 文件。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.pdf"
        export_report(report, out)

        assert out.exists()
        assert out.read_bytes()[:5] == b"%PDF-"

    def test_save_report_backward_compat(self, tmp_path: Path) -> None:
        """save_report 应作为 export_report 的别名正常工作。"""
        report = _build_report(tmp_path)
        out = tmp_path / "report.sarif"
        save_report(report, out)

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["version"] == "2.1.0"

    def test_export_empty_report_sarif(self, tmp_path: Path) -> None:
        """空报告导出 SARIF 应含空 results 数组。"""
        report = _build_empty_report(tmp_path)
        out = tmp_path / "empty.sarif"
        export_report(report, out)

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["runs"][0]["results"] == []


# ---------------------------------------------------------------------------
# 大结果集导出（流式写入验证）
# ---------------------------------------------------------------------------


class TestLargeReportExport:
    """大结果集导出验证（iter-121 流式写入）。"""

    def _build_large_report(self, tmp_path: Path, count: int = 5000) -> ScanReport:
        """构造大结果集报告（5000 个文件 × 2 条规则 = 10000 条命中）。"""
        results = tuple(
            ScanResult(
                path=tmp_path / f"file_{i}.txt",
                size=100,
                hits=(
                    RuleHit(f"规则_{i % 5}", Severity.WARNING, f"详情_{i}", match_count=1),
                    RuleHit("通用密钥", Severity.CRITICAL, "AKIA", match_count=2),
                ),
            )
            for i in range(count)
        )
        stats = ScanStats(
            total_files=count,
            scanned_files=count,
            matched_files=count,
            total_matches=count * 2,
        )
        return ScanReport(root=tmp_path, results=results, stats=stats)

    def test_large_sarif_export_completes(self, tmp_path: Path) -> None:
        """5000 文件大报告 SARIF 导出应成功完成。"""
        report = self._build_large_report(tmp_path, count=500)
        out = tmp_path / "large.sarif"
        export_report(report, out)

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        # 500 文件 × 2 规则 = 1000 条 result
        assert len(data["runs"][0]["results"]) == 1000

    def test_large_csv_export_completes(self, tmp_path: Path) -> None:
        """500 文件大报告 CSV 导出应成功完成。"""
        report = self._build_large_report(tmp_path, count=500)
        out = tmp_path / "large.csv"
        export_report(report, out)

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        # 500 文件 × 2 规则 = 1000 数据行 + 1 行表头
        # Windows 下 CSV \r\n 经 write_text 转换可能产生 \r\r\n，用 split 过滤空行
        non_empty_lines = [line for line in content.splitlines() if line.strip()]
        assert len(non_empty_lines) == 1001  # 1000 数据行 + 1 表头

    def test_large_json_export_completes(self, tmp_path: Path) -> None:
        """500 文件大报告 JSON 导出应成功完成。"""
        report = self._build_large_report(tmp_path, count=500)
        out = tmp_path / "large.json"
        export_report(report, out)

        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["hits"]) == 500
