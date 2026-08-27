"""QtWidgets 全局 QSS 构建：由主题色板生成深/浅两套应用样式表。

设计令牌（色板/字号）集中定义于本模块，是 Widgets GUI 的唯一色值来源；
:mod:`fuscan.gui.theme` 的 ``ThemeController`` 承接字号配置与 ``@Property``
通知语义，页面同时从两者取值。

用法：``app.setStyleSheet(build_app_qss(dark=True))``；主题切换时重新调用
并整表替换，QApplication 会自动 repolish 全部控件。
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_app_qss"]

# ----------------------------- 色彩令牌 -----------------------------
# 浅色：GitHub Desktop 风格；深色：Tokyo Night 风格（与原 QML 主题一致）

_LIGHT: dict[str, str] = {
    "primary": "#0366D6",
    "primary_dark": "#0245A6",
    "success": "#28A745",
    "warning": "#F0883E",
    "danger": "#D73A49",
    "text_primary": "#24292E",
    "text_secondary": "#586069",
    "text_on_primary": "#FFFFFF",
    "bg_app": "#F5F6F8",
    "bg_card": "#FFFFFF",
    "bg_hover": "#F6F8FA",
    "bg_selected": "#EDF3FF",
    "bg_sidebar": "#FFFFFF",
    "border": "#E1E4E8",
    "border_muted": "#D0D7DE",
}

_DARK: dict[str, str] = {
    "primary": "#7AA2F7",
    "primary_dark": "#5A82E0",
    "success": "#28A745",
    "warning": "#F0883E",
    "danger": "#D73A49",
    "text_primary": "#E0E0EF",
    "text_secondary": "#A0A0B0",
    "text_on_primary": "#101018",
    "bg_app": "#1A1B26",
    "bg_card": "#1E1F2A",
    "bg_hover": "#2A2B3A",
    "bg_selected": "#2A2B3A",
    "bg_sidebar": "#16161E",
    "border": "#2E2F3A",
    "border_muted": "#44465A",
}

# 间距/圆角（主题无关）
_SPACING_MD = 16
_RADIUS_SM = 4
_RADIUS_MD = 6


def _qss(t: dict[str, str], font_family: str, body_px: int) -> str:
    """根据色板与字体配置拼装 QSS 文本。

    :param t: 浅色或深色色板字典
    :param font_family: 主字体族名
    :param body_px: 正文字号基准 px
    """
    small = max(10, body_px - 2)
    return f"""
/* ---------- 全局 ---------- */
QWidget {{
    color: {t["text_primary"]};
    background-color: transparent;
    font-family: "{font_family}";
    font-size: {body_px}px;
}}
QWidget:disabled {{ color: {t["text_secondary"]}; }}
QMainWindow, QDialog {{ background-color: {t["bg_app"]}; }}
QToolTip {{
    background-color: {t["bg_card"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_SM}px;
    padding: 4px 8px;
}}

/* ---------- 卡片容器 ---------- */
QFrame[card="true"] {{
    background-color: {t["bg_card"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_MD}px;
}}
QFrame#separatorLine {{
    background-color: {t["border"]};
    border: none;
    max-height: 1px;
}}

/* ---------- 按钮三级层级 ---------- */
QPushButton {{
    background-color: {t["bg_card"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_muted"]};
    border-radius: {_RADIUS_SM}px;
    padding: 5px 14px;
    min-height: 30px;
}}
QPushButton:hover {{ background-color: {t["bg_hover"]}; }}
QPushButton:pressed {{ background-color: {t["bg_selected"]}; }}
QPushButton:disabled {{ background-color: {t["bg_hover"]}; color: {t["text_secondary"]}; }}
QPushButton[variant="primary"] {{
    background-color: {t["primary"]};
    color: {t["text_on_primary"]};
    border: none;
    font-weight: bold;
}}
QPushButton[variant="primary"]:hover {{ background-color: {t["primary_dark"]}; }}
QPushButton[variant="danger"] {{
    color: {t["danger"]};
    border: 1px solid {t["danger"]};
    background: transparent;
}}
QPushButton[variant="ghost"], QPushButton#iconBtn {{
    background: transparent;
    border: none;
    padding: 4px;
    min-height: 24px;
}}
QPushButton#iconBtn:hover {{ background-color: {t["bg_hover"]}; border-radius: {_RADIUS_SM}px; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QAbstractSpinBox {{
    background-color: {t["bg_card"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border_muted"]};
    border-radius: {_RADIUS_SM}px;
    padding: 3px 8px;
    min-height: 26px;
    selection-background-color: {t["primary"]};
    selection-color: {t["text_on_primary"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {t["primary"]};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {t["border"]};
}}
QComboBox::down-arrow {{
    width: 0; height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {t["text_secondary"]};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["bg_card"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_SM}px;
    selection-background-color: {t["bg_selected"]};
    selection-color: {t["text_primary"]};
    outline: none;
}}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QCheckBox::indicator {{
    border: 1px solid {t["border_muted"]};
    border-radius: 3px;
    background-color: {t["bg_card"]};
}}
QCheckBox::indicator:checked {{ background-color: {t["primary"]}; border-color: {t["primary"]}; }}
QRadioButton::indicator {{
    border: 1px solid {t["border_muted"]};
    border-radius: 8px;
    background-color: {t["bg_card"]};
}}
QRadioButton::indicator:checked {{ background-color: {t["primary"]}; border-color: {t["primary"]}; }}

/* ---------- 分组框 ---------- */
QGroupBox {{
    background-color: {t["bg_card"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_MD}px;
    margin-top: 12px;
    padding-top: 12px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 2px;
    padding: 0 4px;
    color: {t["text_primary"]};
    background-color: {t["bg_card"]};
}}

/* ---------- 列表 / 树 / 表格 ---------- */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background-color: {t["bg_card"]};
    alternate-background-color: {t["bg_hover"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_SM}px;
    outline: none;
}}
QListView::item, QTreeView::item, QListWidget::item {{ min-height: 26px; padding: 2px 6px; }}
QListView::item:hover, QTreeView::item:hover, QListWidget::item:hover {{ background-color: {t["bg_hover"]}; }}
QListView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{
    background-color: {t["bg_selected"]};
    color: {t["text_primary"]};
}}
QHeaderView::section {{
    background-color: {t["bg_hover"]};
    color: {t["text_secondary"]};
    border: none;
    border-bottom: 1px solid {t["border"]};
    padding: 5px 8px;
    font-size: {small}px;
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {t["border_muted"]};
    border-radius: 4px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {t["text_secondary"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {t["border_muted"]};
    border-radius: 4px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t["text_secondary"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollArea {{ border: none; }}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background-color: {t["bg_hover"]};
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {t["primary"]};
    border-radius: 3px;
}}
QProgressBar[indeterminateHeight="true"] {{ min-height: 6px; }}

/* ---------- 对话框按钮与弹窗 ---------- */
QPushButton#dialogBtnPrimary {{ background-color: {t["primary"]}; color: {t["text_on_primary"]}; border: none; }}
QProgressDialog {{ background-color: {t["bg_card"]}; }}
QMenu {{
    background-color: {t["bg_card"]};
    color: {t["text_primary"]};
    border: 1px solid {t["border"]};
    border-radius: {_RADIUS_MD}px;
    padding: 4px;
}}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: {_RADIUS_SM}px; }}
QMenu::item:selected {{ background-color: {t["bg_selected"]}; }}
QMenu::separator {{ height: 1px; background-color: {t["border"]}; margin: 4px 8px; }}
"""


def build_app_qss(
    dark: bool,
    font_family: str = "Microsoft YaHei UI",
    body_font_size: int = 14,
) -> str:
    """生成全局应用样式表。

    :param dark: 是否使用深色色板
    :param font_family: 主字体族名
    :param body_font_size: 正文字号基准（其他字号按 ±2 派生）
    :return: 可传给 ``QApplication.setStyleSheet`` 的 QSS 文本
    """
    t = _DARK if dark else _LIGHT
    return _qss(t, font_family, body_font_size)


def palette_tokens(dark: bool) -> dict[str, Any]:
    """返回当前主题的命名色板（供图标染色等需要精确色值的场景）。

    :param dark: 是否使用深色色板
    :return: 键为语义色名、值为十六进制色串的字典
    """
    return dict(_DARK if dark else _LIGHT)
