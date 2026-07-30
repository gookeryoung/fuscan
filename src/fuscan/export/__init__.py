"""扫描报告导出子包。

集中托管扫描报告的导出逻辑，与扫描器核心（:mod:`fuscan.scanner`）解耦：

- :mod:`fuscan.export.report`：报告生成（PDF/Excel 二进制 + 文件分发）
- :mod:`fuscan.export.cli_output`：CLI 输出辅助（文本/二进制格式分发到 stdout/文件）

公共 API：

- :func:`export_pdf` / :func:`export_excel`：生成 PDF/Excel 二进制
- :func:`export_report` / :func:`save_report`：按扩展名自动选择格式写入文件
- :func:`output_report` / :func:`write_output`：CLI 输出辅助
"""

from __future__ import annotations

from fuscan.export.cli_output import output_report, write_output
from fuscan.export.report import export_excel, export_pdf, export_report, save_report

__all__ = [
    "export_excel",
    "export_pdf",
    "export_report",
    "output_report",
    "save_report",
    "write_output",
]
