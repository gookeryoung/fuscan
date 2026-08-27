"""扫描图标与动画资源，生成 ``resources.qrc`` 并编译为 ``resources_rc.py``。

将 SVG 图标、favicon.ico 与动画 sprite sheet 打包进 Qt 资源系统（qrc），
运行时通过 ``qrc:///`` 路径访问，减少磁盘 I/O，加快启动速度。

使用方式::

    uv run python scripts/build_qrc.py

输出：

- ``src/fuscan/gui/resources.qrc``：资源清单（XML）
- ``src/fuscan/gui/resources_rc.py``：编译后的 Python 模块，供 ``app.py`` import
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

# 仓库根目录（scripts/build_qrc.py 的上两级）
ROOT = Path(__file__).resolve().parent.parent
# 图标目录
ICONS_DIR = ROOT / "src" / "fuscan" / "assets" / "icons"
# 动画资源目录（PNG sprite sheet 等）
ANIMATIONS_DIR = ROOT / "src" / "fuscan" / "assets" / "animations"
# 输出文件
QRC_FILE = ROOT / "src" / "fuscan" / "gui" / "resources.qrc"
RC_FILE = ROOT / "src" / "fuscan" / "gui" / "resources_rc.py"

__all__ = ["main"]


def collect_icon_files() -> list[tuple[str, Path]]:
    """收集所有 .svg 与 .ico 图标，返回 (qrc_alias, abs_path) 列表。

    :return: alias 形如 ``icons/pause.svg``，对应 qrc 内路径 ``qrc:/icons/pause.svg``
    """
    files: list[tuple[str, Path]] = []
    for pattern in ("*.svg", "*.ico"):
        for icon in sorted(ICONS_DIR.glob(pattern)):
            alias = f"icons/{icon.name}"
            files.append((alias, icon))
    return files


def collect_animation_files() -> list[tuple[str, Path]]:
    """收集所有 .png 动画资源（sprite sheet），返回 (qrc_alias, abs_path) 列表。

    :return: alias 形如 ``animations/spinner_primary.png``，
             对应 qrc 内路径 ``qrc:/animations/spinner_primary.png``
    """
    files: list[tuple[str, Path]] = []
    for png in sorted(ANIMATIONS_DIR.glob("*.png")):
        alias = f"animations/{png.name}"
        files.append((alias, png))
    return files


def write_qrc(
    icon_files: list[tuple[str, Path]],
    animation_files: list[tuple[str, Path]] | None = None,
) -> None:
    """生成 .qrc 文件。

    :param icon_files: 图标文件 (alias, abs_path) 列表
    :param animation_files: 动画 PNG 文件 (alias, abs_path) 列表
    """
    rcc = ET.Element("RCC")
    # schema 版本声明，便于后续升级
    rcc.set("version", "1.0")
    qresource = ET.SubElement(rcc, "qresource", {"prefix": "/"})
    all_files = icon_files + (animation_files or [])
    for alias, path in all_files:
        # qrc 内 <file> 路径相对于 .qrc 文件所在目录解析
        # 用 os.path.relpath 处理跨目录的 ..（pathlib.relative_to 不支持）
        rel = os.path.relpath(path, QRC_FILE.parent).replace("\\", "/")
        ET.SubElement(qresource, "file", {"alias": alias}).text = rel
    raw = ET.tostring(rcc, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
    pretty_str = pretty.decode("utf-8")
    # 去掉 minidom 默认的空行，保持紧凑
    lines = [line for line in pretty_str.splitlines() if line.strip()]
    QRC_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def detect_rcc_tool() -> str:
    """检测 pyside2-rcc 命令。

    优先使用 PATH 中的 ``pyside2-rcc``；找不到则回退到
    ``python -m`` 调用方式。

    :return: 可用的 rcc 命令名
    :raises RuntimeError: 工具不可用
    """
    if shutil.which("pyside2-rcc"):
        return "pyside2-rcc"
    # 兜底：尝试通过 python -m 调用
    try:
        import PySide2  # noqa: F401

        return "pyside2-rcc"
    except ImportError:
        pass
    raise RuntimeError("未找到 pyside2-rcc，请安装 PySide2")


def compile_qrc() -> None:
    """调用 pyside2-rcc 编译 .qrc 为 resources_rc.py。"""
    tool = detect_rcc_tool()
    cmd = [tool, "-o", str(RC_FILE), str(QRC_FILE)]
    print(f"运行: {' '.join(cmd)}")
    # rcc 工具即使成功也可能返回非零退出码（PowerShell 环境下），用 check=True 严格校验
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0 or not RC_FILE.exists():
        # 输出错误信息便于排查
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"rcc 编译失败（退出码 {result.returncode}）")
    print(f"编译产物: {RC_FILE.relative_to(ROOT)} ({RC_FILE.stat().st_size} bytes)")


def main() -> int:
    """入口函数。

    :return: 退出码（0 成功）
    """
    icon_files = collect_icon_files()
    animation_files = collect_animation_files()
    print(f"收集图标 {len(icon_files)} 个，动画 {len(animation_files)} 个")

    write_qrc(icon_files, animation_files)
    print(f"生成清单: {QRC_FILE.relative_to(ROOT)}")

    compile_qrc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
