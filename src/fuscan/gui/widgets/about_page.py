"""关于页：应用信息 + 快捷入口 + 引擎状态 + 第三方依赖 + 快捷键说明。

- 打开手册/配置目录失败时经 :attr:`AboutController.openFailed` 显示 Toast
- 引擎状态组展示 fuscan-core 与 OCR 各依赖项绿勾/红叉
- 快捷键清单须与 :class:`MainWindow._build_shortcuts` 注册的全局快捷键一致
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QTimer 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QResizeEvent
from PySide2.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.controllers import AppController
from fuscan.gui.widgets.icons import tinted_svg_icon
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["AboutPage", "CardGroupBox"]

# 快捷键说明清单：(序列, 功能说明)，须随 MainWindow 全局 Shortcut 增删同步维护
_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+1", "切换到文件扫描（首页）"),
    ("Ctrl+2", "切换到文件监控"),
    ("Ctrl+3", "切换到扫描结果"),
    ("Ctrl+4", "切换到统计"),
    ("Ctrl+5", "切换到设置"),
    ("Ctrl+6", "切换到关于"),
    ("Ctrl+B", "折叠/展开侧边栏"),
    ("Esc", "返回首页"),
)

_TOAST_MS = 3000


class CardGroupBox(QFrame):
    """卡片式分组框：全局 QSS ``QFrame[card=\"true\"]`` 提供 边框+圆角+底色。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(6)
        self._title = QLabel(title)
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        outer.addWidget(self._title)
        self.content = QVBoxLayout()
        self.content.setSpacing(4)
        outer.addLayout(self.content)


class AboutPage(QWidget):
    """关于页视图：只读信息展示 + 手册/配置目录入口。"""

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        """初始化关于页并连接控制器信号。

        :param controller: 主控制器（使用其 :attr:`about` 子控制器）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._controller = controller.about
        self._dark = False
        self._ocr_rows: list[tuple[QLabel, QLabel]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("page_about")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)

        title = QLabel("关于")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        root.addWidget(title)
        root.addWidget(self._build_logo_section())
        root.addWidget(self._build_entry_buttons())
        engines_row = QHBoxLayout()
        engines_row.setSpacing(12)
        engines_row.addWidget(self._build_engine_group(), stretch=1)
        engines_row.addWidget(self._build_dependency_group(), stretch=1)
        root.addLayout(engines_row)
        root.addWidget(self._build_shortcut_group())
        root.addStretch()

        # 失败 Toast：顶部居中悬浮条，3 秒自动消失
        self._toast = QLabel(self)
        self._toast.setVisible(False)
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self._toast.setVisible(False))
        self._controller.openFailed.connect(self.show_toast)
        self._refresh_semantic_colors()

    # ----------------------------- 构建块 -----------------------------

    def _build_logo_section(self) -> QWidget:
        """Logo 方块 + 应用名/版本/描述/作者信息列。"""
        c = self._controller
        section = QWidget()
        column = QVBoxLayout(section)
        column.setAlignment(Qt.AlignHCenter)
        column.setSpacing(4)
        logo_box = QLabel("F", alignment=Qt.AlignCenter)
        logo_box.setFixedSize(80, 80)
        self._logo_box = logo_box
        column.addWidget(logo_box, alignment=Qt.AlignHCenter)
        app_name = QLabel("fuscan", alignment=Qt.AlignCenter)
        app_name.setStyleSheet("font-size: 24px; font-weight: bold;")
        version = QLabel(f"v{c.version}", alignment=Qt.AlignCenter)
        description = QLabel(c.description, alignment=Qt.AlignCenter)
        description.setWordWrap(True)
        author_line = QLabel(f"作者: {c.author} · {c.license}", alignment=Qt.AlignCenter)
        for label in (app_name, version, description, author_line):
            column.addWidget(label)
        return section

    def _build_entry_buttons(self) -> QWidget:
        """快捷入口按钮行：用户手册 / 配置目录。"""
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setAlignment(Qt.AlignHCenter)
        row.setSpacing(12)
        manual_btn = QPushButton(" 用户手册")
        manual_btn.setProperty("variant", "primary")
        manual_btn.setToolTip("打开用户手册 PDF")
        manual_btn.setFixedWidth(160)
        manual_btn.clicked.connect(self._controller.openManual)
        config_btn = QPushButton(" 配置目录")
        config_btn.setToolTip("打开配置目录")
        config_btn.setFixedWidth(160)
        config_btn.clicked.connect(self._controller.openConfigDir)
        row.addWidget(manual_btn)
        row.addWidget(config_btn)
        self._manual_btn = manual_btn
        self._config_btn = config_btn
        return row_widget

    def _build_engine_group(self) -> QWidget:
        """「引擎状态」卡片：fuscan-core 行 + OCR 引擎汇总行 + OCR 各依赖勾叉。"""
        c = self._controller
        group = CardGroupBox("引擎状态")
        small = "font-size: 11px;"
        for text in c.nativeEngines:
            label = QLabel(str(text))
            label.setWordWrap(True)
            label.setStyleSheet(small)
            group.content.addWidget(label)
        ocr_label = QLabel(c.ocrEngine)
        ocr_label.setWordWrap(True)
        ocr_label.setStyleSheet(small)
        group.content.addWidget(ocr_label)
        for dep in c.ocrDependencies:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            mark = QLabel("✓" if dep["installed"] else "✗")
            name = QLabel(f"{dep['name']}{' ' + str(dep['version']) if dep['version'] else ''}")
            name.setWordWrap(True)
            name.setStyleSheet(small)
            row.addWidget(mark)
            row.addWidget(name, stretch=1)
            group.content.addWidget(row_widget)
            self._ocr_rows.append((mark, name))
        group.content.addStretch()
        return group

    def _build_dependency_group(self) -> QWidget:
        """「第三方依赖」卡片：逐行列出依赖与用途。"""
        group = CardGroupBox("第三方依赖")
        for text in self._controller.dependencies:
            label = QLabel(str(text))
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 11px;")
            group.content.addWidget(label)
        group.content.addStretch()
        return group

    def _build_shortcut_group(self) -> QWidget:
        """「快捷键」卡片：序列主色高亮 + 功能说明。"""
        group = CardGroupBox("快捷键")
        t = palette_tokens(self._dark)
        for seq, desc in _SHORTCUTS:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            seq_label = QLabel(seq)
            seq_label.setFixedWidth(90)
            seq_label.setStyleSheet(
                f"font-family: monospace; font-weight: bold; font-size: 11px; color: {t['primary']};"
            )
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            row.addWidget(seq_label)
            row.addWidget(desc_label, stretch=1)
            group.content.addWidget(row_widget)
        group.content.addStretch()
        return group

    # ----------------------------- 公共 API -----------------------------

    def show_toast(self, message: str) -> None:
        """显示失败提示条并在 3 秒后自动隐藏。"""
        t = palette_tokens(self._dark)
        self._toast.setStyleSheet(
            f"background-color: {t['danger']}; color: #FFFFFF; border-radius: 6px; padding: 6px 16px;"
        )
        self._toast.setText(message)
        self._toast.adjustSize()
        self._reposition_toast()
        self._toast.setVisible(True)
        self._toast_timer.start(_TOAST_MS)

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新 Logo、按钮图标与语义色标签。

        :param dark: 是否启用深色主题
        """
        if self._dark == dark:
            return
        self._dark = dark
        self._refresh_semantic_colors()

    # ----------------------------- 私有 -----------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窗口尺寸变化时保持 Toast 顶部居中。"""
        super().resizeEvent(event)
        self._reposition_toast()

    def _reposition_toast(self) -> None:
        """按当前页面宽度居中放置 Toast。"""
        x = max(0, (self.width() - self._toast.width()) // 2)
        self._toast.move(x, 16)

    def _refresh_semantic_colors(self) -> None:
        """主题相关色值集中刷新（Logo/图标/勾叉）。"""
        t = palette_tokens(self._dark)
        self._logo_box.setStyleSheet(
            f"background-color: {t['primary']}; color: {t['text_on_primary']};"
            " border-radius: 16px; font-size: 40px; font-weight: bold;"
        )
        self._manual_btn.setIcon(tinted_svg_icon(":/icons/manual.svg", t["text_on_primary"], 16))
        self._config_btn.setIcon(tinted_svg_icon(":/icons/folder.svg", t["text_primary"], 16))
        for mark, _name in self._ocr_rows:
            color = t["danger"] if mark.text() == "✗" else t["success"]
            mark.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
