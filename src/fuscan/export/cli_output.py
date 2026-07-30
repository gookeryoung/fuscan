"""CLI 输出辅助：报告分发到 stdout 或文件。

从 :mod:`fuscan.cli` 迁入本模块，使 CLI 入口专注于参数解析与子命令调度，
报告输出（文本/二进制格式分发）集中归 :mod:`fuscan.export` 子包。

公共 API：

- :func:`write_output`：输出文本到 stdout 或文件
- :func:`output_report`：按格式（text/json/csv/pdf/excel）分发扫描报告
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fuscan.export.report import export_excel, export_pdf
from fuscan.scanner.result import ScanReport

__all__ = ["output_report", "write_output"]

logger = logging.getLogger(__name__)


def write_output(content: str, output_file: Path | None) -> None:
    """输出报告到文件或 stdout。"""
    if output_file is None:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")


def output_report(report: ScanReport, fmt: str, output_file: Path | None) -> None:
    """按格式输出扫描报告，支持文本与二进制格式。

    - text/json/csv：文本格式，通过 ``to_format`` 调度，stdout 或文件
    - pdf/excel：二进制格式，必须输出到文件（``-f`` 参数）
    """
    if fmt == "pdf":
        if output_file is None:
            logger.error("PDF 格式必须配合 -f/--output-file 输出到文件")
            return
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(export_pdf(report))
        return
    if fmt == "excel":
        if output_file is None:
            logger.error("Excel 格式必须配合 -f/--output-file 输出到文件")
            return
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(export_excel(report))
        return
    write_output(report.to_format(fmt), output_file)
