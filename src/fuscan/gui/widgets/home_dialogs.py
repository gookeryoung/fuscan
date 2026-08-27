"""文件扫描页共享对话框组（Widgets 版）。

对照 QML 版 :file:`HomePageDialogs.qml` 等价迁移，从 QML 的共享单例 Dialog
改为按需构建的模态 :class:`QDialog`：

- 切换扫描目标：盘符/文件夹双模式（对应 ``editTargetDialog``）
- 配置规则：规则文件列表 + 勾选启用/加载/排序（对应 ``configureRulesDialog``）
- 预览规则：只读双 Tab 展示 effective 规则集（对应 ``previewRulesDialog``）
- 扫描历史：趋势图 + 对比摘要 + 历史列表（对应 ``historyDialog``）

导出/清空等简单交互由 HomePage 直接用原生 QFileDialog / QMessageBox 完成。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout/QDialog 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

import json

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.widgets.qss import palette_tokens
from fuscan.gui.widgets.stats_page import BarChart

__all__ = ["EditTargetDialog", "HistoryDialog", "PreviewRulesDialog", "RulesDialog"]

_MONO_FAMILY = "Consolas"


def _tokens(dark: bool) -> dict[str, str]:
    return palette_tokens(dark)


def _chip(
    text_html: str,
    dark: bool,
    *,
    bg_color: str | None = None,
    mono: bool = False,
    italic: bool = False,
    tip: str = "",
) -> QFrame:
    """圆角标签芯片：预览瀑布标签与目录/后缀列表共用。"""
    t = _tokens(dark)
    frame = QFrame()
    frame.setStyleSheet(
        f"background-color: {bg_color or t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;"
    )
    col = QHBoxLayout(frame)
    col.setContentsMargins(8, 3, 8, 3)
    label = QLabel()
    label.setTextFormat(Qt.RichText)
    label.setText(text_html)
    style = f"font-size: 11px; color: {t['text_primary']}; background: transparent;"
    if mono:
        style += f" font-family: '{_MONO_FAMILY}';"
    if italic:
        style += " font-style: italic;"
    label.setStyleSheet(style)
    if tip:
        frame.setToolTip(tip)
    col.addWidget(label)
    return frame


class _TagLabel(QLabel):
    """实底小徽标（作用域/严重度/缺失标记）：白字加粗。"""

    def __init__(self, text: str, bg: str) -> None:
        super().__init__(text)
        self.setStyleSheet(
            f"background-color: {bg}; color: #FFFFFF; font-size: 10px;"
            " font-weight: bold; padding: 1px 6px; border-radius: 4px;"
        )


class EditTargetDialog(QDialog):
    """切换扫描目标对话框：盘符按钮组或文件夹路径选择。"""

    def __init__(
        self,
        config_controller: object,
        workspace_controller: object,
        ws_id: str,
        mode_text: str,
        target: str,
        dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        """初始化并填充当前目标。

        :param config_controller: ConfigController（读可用盘符）
        :param workspace_controller: WorkspaceController（写回 updateWorkspaceTarget）
        :param ws_id: 目标工作区 ID
        :param mode_text: 当前模式的中文文本（"盘符扫描"/"文件夹扫描"）
        :param target: 当前扫描目标
        """
        super().__init__(parent)
        self.setWindowTitle("切换扫描目标")
        self.setMinimumWidth(420)
        self.resize(420, self.sizeHint().height())
        self._config = config_controller
        self._wc = workspace_controller
        self._ws_id = ws_id
        self._mode_index = 0 if mode_text == "盘符扫描" else 1
        self._drive = target if self._mode_index == 0 else ""
        self._folder = target if self._mode_index == 1 else ""
        self._dark = dark

        root = QVBoxLayout(self)
        root.setSpacing(12)

        root.addWidget(QLabel("扫描模式"))
        mode_row = QHBoxLayout()
        for i, text in enumerate(("盘符扫描", "文件夹扫描")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(i == self._mode_index)
            btn.clicked.connect(lambda _checked=False, k=i: self._select_mode(k))
            mode_row.addWidget(btn)
            setattr(self, f"_mode_btn_{i}", btn)
        mode_row.addStretch()
        root.addLayout(mode_row)

        self._drive_area = QWidget()
        drive_row = QHBoxLayout(self._drive_area)
        drive_row.setContentsMargins(0, 0, 0, 0)
        drive_row.setSpacing(6)
        drives = list(config_controller.drives)
        for d in drives:
            dbtn = QPushButton(d)
            dbtn.setCheckable(True)
            dbtn.setChecked(d == self._drive)
            dbtn.clicked.connect(lambda _checked=False, letter=d: self._select_drive(letter))
            drive_row.addWidget(dbtn)
        if not drives:
            warn = QLabel("未检测到可用盘符")
            warn.setStyleSheet(f"font-size: 12px; color: {_tokens(dark)['warning']};")
            drive_row.addWidget(warn)
        drive_row.addStretch()
        root.addWidget(self._drive_area)

        self._folder_area = QWidget()
        folder_row = QHBoxLayout(self._folder_area)
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(6)
        self._folder_edit = QLineEditPlaceholder()
        self._folder_edit.setText(self._folder)
        self._folder_edit.setPlaceholderText("选择或输入扫描目录")
        pick_btn = QPushButton(" 选择")
        pick_btn.setToolTip("选择扫描目录")
        folder_row.addWidget(self._folder_edit, stretch=1)
        folder_row.addWidget(pick_btn)
        root.addWidget(self._folder_area)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("确定")
        ok_btn.setProperty("variant", "primary")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        self._select_mode(self._mode_index)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)
        pick_btn.clicked.connect(self._pick_folder)

    def _select_mode(self, index: int) -> None:
        """切换盘符/文件夹两种模式的可视区域。"""
        self._mode_index = index
        self._mode_btn_0.setChecked(index == 0)
        self._mode_btn_1.setChecked(index == 1)
        self._drive_area.setVisible(index == 0)
        self._folder_area.setVisible(index == 1)
        self.adjustSize()

    def _select_drive(self, letter: str) -> None:
        """选中盘符并互斥其它盘符按钮。"""
        self._drive = letter
        for i in range(self._drive_area.layout().count()):
            w = self._drive_area.layout().itemAt(i).widget()
            if isinstance(w, QPushButton) and w.text() != letter:
                w.setChecked(False)

    def _pick_folder(self) -> None:
        """弹出原生目录选择器写入路径框。"""
        from PySide2.QtWidgets import QFileDialog

        chosen = QFileDialog.getExistingDirectory(self, "选择扫描目录")
        if chosen:
            self._folder_edit.setText(chosen)

    def accept(self) -> None:
        """确认后写回工作区目标。"""
        if self._mode_index == 0:
            mode_str, target = "drive", self._drive
        else:
            mode_str, target = "folder", self._folder_edit.text()
        if target:
            self._wc.updateWorkspaceTarget(self._ws_id, mode_str, target)
        super().accept()


class QLineEditPlaceholder(QLineEdit):
    """单行输入控件（独立类型便于桩测试替换）。"""


class RulesDialog(QDialog):
    """配置规则对话框：单一列表管理内置/全局/临时规则文件。"""

    def __init__(
        self,
        rules_controller: object,
        ws_name: str,
        dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        """初始化对话框并重建规则文件列表。

        所有操作经 RulesController 立即生效，关闭即完成。
        """
        super().__init__(parent)
        self.setWindowTitle(f"配置规则 — {ws_name}")
        self.setMinimumSize(600, 520)
        self._rules = rules_controller
        self._dark = dark

        root = QVBoxLayout(self)
        root.setSpacing(10)
        hint = QLabel("同规则条件覆盖上方，不同规则采取并集。")
        hint.setStyleSheet(f"font-size: 11px; color: {_tokens(dark)['text_secondary']};")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._sync_selection)
        root.addWidget(self._list, stretch=1)

        ops_row = QHBoxLayout()
        ops_row.setSpacing(6)
        load_global_btn = QPushButton(" 加载到全局")
        load_global_btn.setProperty("variant", "secondary")
        load_global_btn.setToolTip("从文件选择规则文件加载到全局规则（所有任务共享，立即生效）")
        load_temp_btn = QPushButton(" 加载到临时")
        load_temp_btn.setToolTip("加载到当前工作区临时规则（仅对该任务生效，立即生效）")
        up_btn = QPushButton("上移")
        down_btn = QPushButton("下移")
        remove_btn = QPushButton("移除")
        for b in (up_btn, down_btn, remove_btn):
            b.setProperty("variant", "ghost")
        ops_row.addWidget(load_global_btn)
        ops_row.addWidget(load_temp_btn)
        ops_row.addStretch()
        ops_row.addWidget(up_btn)
        ops_row.addWidget(down_btn)
        ops_row.addWidget(remove_btn)
        root.addLayout(ops_row)

        self._hint2 = QLabel()
        self._hint2.setWordWrap(True)
        self._hint2.setStyleSheet(f"font-size: 10px; font-style: italic; color: {_tokens(dark)['text_secondary']};")
        root.addWidget(self._hint2)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setProperty("variant", "primary")
        close_row.addWidget(close_btn)
        root.addLayout(close_row)
        close_btn.clicked.connect(self.accept)

        load_global_btn.clicked.connect(lambda: self._load(to_temp=False))
        load_temp_btn.clicked.connect(lambda: self._load(to_temp=True))
        up_btn.clicked.connect(self._rules.moveUp)
        down_btn.clicked.connect(self._rules.moveDown)
        remove_btn.clicked.connect(self._rules.removeSelected)

        self.rebuild()

    # ----------------------------- 刷新 -----------------------------

    def rebuild(self) -> None:
        """重建规则文件列表行与提示文字。"""
        t = _tokens(self._dark)
        self._list.clear()
        prev_path = self._selected_path()
        select_row = -1
        for i, item in enumerate(list(self._rules.rulesFileModel)):
            row = QListWidgetItem()
            widget = self._build_row(item, t)
            row.setSizeHint(widget.sizeHint())
            self._list.addItem(row)
            self._list.setItemWidget(row, widget)
            if prev_path is not None and item.get("path") == prev_path:
                select_row = i
        self._list.setCurrentRow(max(0, select_row))
        self._sync_selection()
        self._hint2.setText(
            "勾选启用/禁用所有规则（含临时规则）；上移/下移仅作用于全局规则文件排序"
            if self._rules.hasCurrentWorkspace
            else "未选择工作区：加载到临时、临时规则启用/禁用需先在文件扫描页选择工作区"
        )

    def _selected_path(self) -> object | None:
        """返回当前选中行的规则文件路径（空列表返回 None）。"""
        model = list(self._rules.rulesFileModel)
        row = self._list.currentRow()
        if 0 <= row < len(model):
            return model[row].get("path")
        return None

    def _build_row(self, item: dict[str, object], t: dict[str, str]) -> QWidget:
        """构建单个规则文件行：勾选 + 文件名 + 作用域/缺失徽标 + 移除。"""
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(8)
        check = QCheckBox()
        check.setChecked(bool(item.get("enabled")))
        path = str(item.get("path"))
        check.toggled.connect(lambda on, p=path: self._on_toggle(p, on))
        name = QLabel(str(item.get("fileName") or ""))
        missing = not bool(item.get("exists"))
        name_suffix = "missing" if missing else ""
        if name_suffix:
            name.setToolTip(str(item.get("path")))
            name_style = f"font-size: 12px; background: transparent; color: {t['text_secondary']};"
        else:
            name_style = f"font-size: 12px; background: transparent; color: {t['text_primary']};"
        name.setStyleSheet(name_style)
        row.addWidget(check)
        row.addWidget(name, stretch=1)
        if bool(item.get("isBuiltin")):
            row.addWidget(_TagLabel("内置", t["primary"]))
        elif str(item.get("scope")) == "temp":
            row.addWidget(_TagLabel("临时", t["success"]))
        else:
            row.addWidget(_TagLabel("全局", t["primary"]))
        if missing:
            row.addWidget(_TagLabel("缺失", t["danger"]))
        if bool(item.get("canRemove")):
            rm = QPushButton("×")
            rm.setProperty("variant", "ghost")
            rm.setFixedSize(24, 24)
            rm.setToolTip("移除该规则文件")
            rm.clicked.connect(self._remove_current)
            row.addWidget(rm)
        return wrap

    def _on_toggle(self, path: str, enabled: bool) -> None:
        """勾选启用/禁用规则文件（含临时规则），立即生效。"""
        self._rules.setRuleEnabled(path, enabled)

    def _remove_current(self) -> None:
        """移除当前选中规则文件后重建列表。"""
        self._rules.removeSelected()
        self.rebuild()

    def _sync_selection(self) -> None:
        """同步 RulesController 的选中行号以驱动 上移/下移/移除 可用性。"""
        self._rules.setSelectedFileIndex(self._list.currentRow())

    def _load(self, to_temp: bool) -> None:
        """弹出 YAML 选择器并把文件载入全局/临时规则集。"""
        from PySide2.QtWidgets import QFileDialog

        title = "选择规则文件（加载到临时）" if to_temp else "选择规则文件（加载到全局）"
        chosen, _ = QFileDialog.getOpenFileName(self, title, "", "YAML 文件 (*.yaml *.yml);;所有文件 (*.*)")
        if not chosen:
            return
        if to_temp:
            self._rules.loadFileToTemp(chosen)
        else:
            self._rules.loadFileFromPath(chosen)
        self.rebuild()


class PreviewRulesDialog(QDialog):
    """预览规则对话框：只读展示当前任务 effective 规则集（双 Tab）。"""

    def __init__(
        self,
        preview_data: dict[str, object],
        ws_name: str,
        dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        """初始化并填充各分区。

        :param preview_data: ``RulesController.previewRuleset`` 返回的解析后 dict
        """
        super().__init__(parent)
        self._dark = dark
        self.setWindowTitle(f"预览规则 — {ws_name}")
        self.setMinimumSize(760, 560)
        self.resize(880, 640)
        t = _tokens(dark)
        from PySide2.QtWidgets import QTabWidget

        tabs = QTabWidget()
        tabs.addTab(self._build_scan_settings_tab(preview_data, t), "扫描设置")
        tabs.addTab(self._build_rules_tab(preview_data, t), "规则信息")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(tabs)

    # ----------------------------- Tab 构建 -----------------------------

    def _build_scan_settings_tab(self, data: dict[str, object], t: dict[str, str]) -> QWidget:
        """扫描参数/忽略目录/白名单三个分区的只读视图。"""
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(16)

        def _section_title(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
            return lbl

        # ----- 扫描参数 -----
        col.addWidget(_section_title("扫描参数"))

        def yn(value: object) -> str:
            ok = value is True
            color = t["success"] if ok else t["danger"]
            return f'<span style="color:{color}">{"是" if ok else "否"}</span>'

        params: list[tuple[str, str]] = [
            ("扫描压缩包 (scan_archives:", f"{yn(data.get('scanArchives'))})"),
            ("最大工作线程 (max_workers:", f" {data.get('maxWorkers', '—')})"),
            (
                "最大文件大小 (max_file_size:",
                f" {data.get('maxFileSizeMB', '—')}{'' if data.get('maxFileSizeMB') is None else ' MB'})",
            ),
            ("最大扫描深度 (max_depth:", f" {data.get('maxDepth', '—')})"),
            ("启用扫描结果缓存 (cache_enabled:", f"{yn(data.get('cacheEnabled'))})"),
            ("启用性能详细日志 (perf_log_enabled:", f"{yn(data.get('perfLogEnabled'))})"),
        ]
        chips = [_chip(f"<i>{name}</i>{suffix}", self._dark, bg_color=t["bg_app"]) for name, suffix in params]
        col.addWidget(_wrap_rows(chips))

        # ----- 忽略目录 -----
        ignore_dirs = list(data.get("ignoreDirs") or [])
        col.addWidget(_section_title(f"忽略目录（{len(ignore_dirs)} 项）"))
        if ignore_dirs:
            chips = [_chip(d, self._dark, mono=True) for d in ignore_dirs]
            col.addWidget(_wrap_rows(chips))
        else:
            empty = QLabel("（暂无忽略目录）")
            empty.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
            col.addWidget(empty)

        # ----- 白名单 -----
        whitelist = list(data.get("whitelistEntries") or [])
        col.addWidget(_section_title(f"白名单（{len(whitelist)} 项）"))
        if whitelist:
            for entry in whitelist:
                row = self._whitelist_row(entry, t)
                col.addWidget(row)
        else:
            empty = QLabel("（暂无白名单条目）")
            empty.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
            col.addWidget(empty)
        col.addStretch()
        return self._into_scroll(body)

    @staticmethod
    def _into_scroll(body: QWidget) -> QWidget:
        """内容区包一层滚动条以防窗口过矮。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(body)
        return scroll

    def _whitelist_row(self, entry: dict[str, object], t: dict[str, str]) -> QWidget:
        """构建单个白名单条目：glob 路径 + 规则名徽标 + 来源徽标 + 备注。"""
        wrap = QFrame()
        wrap.setStyleSheet(f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(8, 3, 8, 3)
        row.setSpacing(8)
        glob_lbl = QLabel(str(entry.get("pathGlob") or ""))
        glob_lbl.setStyleSheet(
            f"font-family: '{_MONO_FAMILY}'; font-size: 12px; color: {t['text_primary']}; background: transparent;"
        )
        row.addWidget(glob_lbl, stretch=1)
        rule_name = str(entry.get("ruleName") or "")
        row.addWidget(
            _TagLabel("全部规则" if rule_name == "*" else rule_name, t["primary"] if rule_name == "*" else t["warning"])
        )
        source = str(entry.get("source") or "")
        if source:
            row.addWidget(
                _TagLabel(
                    "规则" if source == "rules" else "运行时", t["success"] if source == "rules" else t["warning"]
                )
            )
        note = str(entry.get("note") or "")
        if note:
            note_lbl = QLabel(note)
            note_lbl.setStyleSheet(f"font-size: 10px; color: {t['text_secondary']}; background: transparent;")
            note_lbl.setMaximumWidth(200)
            row.addWidget(note_lbl)
        return wrap

    def _build_rules_tab(self, data: dict[str, object], t: dict[str, str]) -> QWidget:
        """规则文件与匹配规则两个分区的只读视图。"""
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(16)

        def _section_title(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
            return lbl

        caption = QLabel(
            "作用域：内置=蓝 / 全局=蓝 / 临时=绿；灰色文字表示文件缺失；「类型」行显示该规则文件自身的扫描后缀"
        )
        caption.setStyleSheet(f"font-size: 10px; font-style: italic; color: {t['text_secondary']};")
        caption.setWordWrap(True)

        # ----- 规则文件 -----
        col.addWidget(_section_title(f"规则文件（{len(list(data.get('ruleFiles') or []))} 项）"))
        col.addWidget(caption)
        for rf in list(data.get("ruleFiles") or []):
            col.addWidget(self._rule_file_row(rf, t))

        # ----- 匹配规则 -----
        col.addWidget(_section_title(f"匹配规则（{len(list(data.get('rules') or []))} 条）"))
        sev_caption = QLabel("严重度：红=高危 / 橙=中危 / 黄=低危；「可替换」标签表示命中后可自动脱敏")
        sev_caption.setStyleSheet(f"font-size: 10px; font-style: italic; color: {t['text_secondary']};")
        sev_caption.setWordWrap(True)
        col.addWidget(sev_caption)
        rules = list(data.get("rules") or [])
        if not rules:
            empty = QLabel("（暂无匹配规则，请检查规则文件是否启用或加载）")
            empty.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']};")
            col.addWidget(empty)
        for r in rules:
            col.addWidget(self._rule_row(r, t))
        col.addStretch()
        return self._into_scroll(body)

    def _rule_file_row(self, rf: dict[str, object], t: dict[str, str]) -> QWidget:
        """单条规则文件卡片：灯 + 名称 + 徽标 + 类型（后缀标签）。"""
        wrap = QFrame()
        wrap.setStyleSheet(f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(8, 4, 8, 4)
        col.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        dot = QLabel("●" if bool(rf.get("enabled")) else "○")
        dot.setStyleSheet(
            f"font-size: 9px; color: {t['success'] if bool(rf.get('enabled')) else t['text_secondary']};"
            " background: transparent;"
        )
        name_lbl = QLabel(str(rf.get("fileName") or ""))
        exists = bool(rf.get("exists"))
        name_lbl.setStyleSheet(
            f"font-size: 12px; background: transparent; color: {t['text_primary'] if exists else t['text_secondary']};"
        )
        top.addWidget(dot)
        top.addWidget(name_lbl, stretch=1)
        scope = str(rf.get("scope") or "")
        if bool(rf.get("isBuiltin")):
            top.addWidget(_TagLabel("内置", t["primary"]))
        elif scope == "temp":
            top.addWidget(_TagLabel("临时", t["success"]))
        else:
            top.addWidget(_TagLabel("全局", t["primary"]))
        if not exists:
            top.addWidget(_TagLabel("缺失", t["danger"]))
        col.addLayout(top)
        state = str(rf.get("scanExtensionsState") or "")
        exts = list(rf.get("scanExtensions") or [])
        if state == "none":
            lbl = QLabel("都不扫描")
            lbl.setStyleSheet(f"font-size: 10px; font-style: italic; color: {t['danger']}; background: transparent;")
            col.addWidget(lbl)
        elif state == "list":
            chip_row = QHBoxLayout()
            chip_row.setSpacing(3)
            for e in exts:
                chip = _chip(
                    f".{e}",
                    False,
                    bg_color=t["bg_card"],
                    mono=True,
                )
                chip.setStyleSheet(
                    f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 3px;"
                )
                chip_row.addWidget(chip)
            chip_row.addStretch()
            col.addLayout(chip_row)
        return wrap

    def _rule_row(self, r: dict[str, object], t: dict[str, str]) -> QWidget:
        """单条匹配规则行：名称 + 描述 + 严重度徽标 + 可替换徽标。"""
        wrap = QFrame()
        wrap.setStyleSheet(f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(8, 3, 8, 3)
        row.setSpacing(8)
        name_lbl = QLabel(str(r.get("name") or ""))
        name_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {t['text_primary']}; background: transparent;"
        )
        name_lbl.setMaximumWidth(220)
        desc_lbl = QLabel(str(r.get("description") or ""))
        desc_lbl.setStyleSheet(f"font-size: 10px; color: {t['text_secondary']}; background: transparent;")
        desc_lbl.setWordWrap(True)
        row.addWidget(name_lbl)
        row.addWidget(desc_lbl, stretch=1)
        row.addWidget(_TagLabel(str(r.get("severityText") or ""), str(r.get("severityColor") or t["warning"])))
        if r.get("replace") is True:
            row.addWidget(_TagLabel("可替换", t["primary"]))
        return wrap


def _wrap_rows(chips: list[QWidget]) -> QWidget:
    """把芯片列表纵排到一个透明容器中（占满宽度）。"""
    holder = QWidget()
    col = QVBoxLayout(holder)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    for chip in chips:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(chip)
        row.addStretch()
        col.addLayout(row)
    return holder


class HistoryDialog(QDialog):
    """扫描历史对话框：趋势图 + 最近对比摘要 + 历史记录列表。"""

    def __init__(
        self,
        workspace_controller: object,
        ws_id: str,
        ws_name: str,
        dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        """初始化并拉取历史数据。

        :param workspace_controller: WorkspaceController（历史 JSON Slots）
        :param ws_id: 工作区 ID
        """
        super().__init__(parent)
        self.setWindowTitle(f"扫描历史 — {ws_name}")
        self.setMinimumSize(640, 560)
        self.resize(680, 620)
        self._wc = workspace_controller
        self._ws_id = ws_id
        self._dark = dark
        self._history_list: list[dict[str, object]] = []
        self._comparison: dict[str, object] = {}
        self._trend_data: list[dict[str, object]] = []
        self._selected_scan_ids: list[str] = []

        t = _tokens(dark)
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ---------- 趋势图 ----------
        self._trend_box = QFrame()
        self._trend_box.setStyleSheet(
            f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;"
        )
        trend_col = QVBoxLayout(self._trend_box)
        trend_col.setContentsMargins(10, 8, 10, 8)
        trend_col.setSpacing(6)
        trend_title = QLabel("命中趋势")
        trend_title.setStyleSheet("font-size: 12px; font-weight: bold; background: transparent;")
        trend_col.addWidget(trend_title)
        self._trend_chart = BarChart(label_width=110)
        self._trend_chart.setFixedHeight(140)
        trend_col.addWidget(self._trend_chart)
        root.addWidget(self._trend_box)

        # ---------- 对比摘要 ----------
        self._cmp_box = QFrame()
        self._cmp_box.setStyleSheet(
            f"background-color: {t['bg_app']}; border: 1px solid {t['border']}; border-radius: 4px;"
        )
        cmp_col = QVBoxLayout(self._cmp_box)
        cmp_col.setContentsMargins(10, 8, 10, 8)
        cmp_col.setSpacing(6)
        cmp_title = QLabel("对比摘要")
        cmp_title.setStyleSheet("font-size: 12px; font-weight: bold; background: transparent;")
        self._cmp_summary = QLabel()
        self._cmp_summary.setWordWrap(True)
        self._cmp_summary.setStyleSheet(f"font-size: 11px; color: {t['text_secondary']}; background: transparent;")
        cmp_col.addWidget(cmp_title)
        cmp_col.addWidget(self._cmp_summary)
        self._cmp_tag_holder = QWidget()
        tag_row = QHBoxLayout(self._cmp_tag_holder)
        tag_row.setContentsMargins(0, 0, 0, 0)
        tag_row.addStretch()
        cmp_col.addWidget(self._cmp_tag_holder)
        root.addWidget(self._cmp_box)

        # ---------- 历史列表 ----------
        list_title = QLabel("历史记录")
        list_title.setStyleSheet("font-size: 12px; font-weight: bold;")
        root.addWidget(list_title)
        self._list_widget = QListWidget()
        root.addWidget(self._list_widget, stretch=1)
        self._empty_label = QLabel("暂无扫描历史")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(f"font-size: 12px; color: {t['text_secondary']};")
        root.addWidget(self._empty_label)

        # ---------- 操作栏 ----------
        ops_row = QHBoxLayout()
        ops_row.setSpacing(8)
        self._sel_count_label = QLabel()
        self._compare_btn = QPushButton("对比选中")
        self._compare_btn.setEnabled(False)
        self._clear_btn = QPushButton("清空历史")
        ops_row.addWidget(self._sel_count_label)
        ops_row.addWidget(self._compare_btn)
        ops_row.addStretch()
        ops_row.addWidget(self._clear_btn)
        root.addLayout(ops_row)

        self._compare_btn.clicked.connect(self._compare_selected)
        self._clear_btn.clicked.connect(self._clear_history)

        self.reload()

    # ----------------------------- 数据 -----------------------------

    def reload(self) -> None:
        """重新拉取历史/对比/趋势三份数据并刷新界面。"""
        self._history_list = self._parse_json(self._wc.workspaceHistoryJson(self._ws_id)) or []
        self._comparison = self._parse_json(self._wc.compareWithPreviousScan(self._ws_id)) or {}
        self._trend_data = self._parse_json(self._wc.scanTrendJson(self._ws_id)) or []
        self._refresh_trend()
        self._refresh_comparison()
        self._rebuild_list()

    @staticmethod
    def _parse_json(raw: str) -> object | None:
        """安全解析控制器 Slot 返回的 JSON 字符串。"""
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _refresh_trend(self) -> None:
        """用命中数绘制逐次扫描趋势条形图；无数据显示隐藏整块。"""
        self._trend_box.setVisible(len(self._trend_data) > 0)
        if not self._trend_data:
            return
        items: list[dict[str, object]] = []
        primary = _tokens(self._dark)["primary"]
        for entry in self._trend_data:
            ts = str(entry.get("finished_at", "")).replace("T", " ").replace("Z", "")
            items.append(
                {
                    "label": ts[:16],
                    "value": entry.get("matched_files", 0),
                    "color": primary,
                }
            )
        self._trend_chart.set_data(items)

    def _refresh_comparison(self) -> None:
        """最近两次对比摘要与趋势徽标显隐刷新。"""
        summary = str(self._comparison.get("summary") or "")
        trend = str(self._comparison.get("trend") or "")
        self._cmp_box.setVisible(bool(summary) or bool(trend))
        self._cmp_summary.setText(summary)
        # 清理旧徽标
        layout = self._cmp_tag_holder.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if trend:
            t = _tokens(self._dark)
            color_map = {
                "改善": t["success"],
                "恶化": t["danger"],
                "首次": t["primary"],
            }
            tag_row = self._cmp_tag_holder.layout()
            tag = _TagLabel(trend, color_map.get(trend, t["text_secondary"]))
            tag_row.insertWidget(0, tag)

    def _rebuild_list(self) -> None:
        """重建历史记录行（倒序复选框限制最多选 2 个）。"""
        t = _tokens(self._dark)
        self._list_widget.clear()
        for entry in self._history_list:
            wrap = QWidget()
            row = QHBoxLayout(wrap)
            row.setContentsMargins(8, 4, 8, 4)
            row.setSpacing(10)
            scan_id = str(entry.get("scan_id"))
            status = str(entry.get("status"))
            status_bg = (
                t["success"] if status == "completed" else (t["warning"] if status == "cancelled" else t["danger"])
            )
            status_text = {"completed": "完成", "cancelled": "取消"}.get(status, "失败")
            bar = QLabel()
            bar.setFixedSize(3, 30)
            bar.setStyleSheet(f"background-color: {status_bg}; border-radius: 2px;")
            col = QVBoxLayout()
            finished = str(entry.get("finished_at") or "").replace("T", " ").replace("Z", "")
            line1 = QLabel(f"{finished} | 命中 {entry.get('matched_files', 0)}")
            line1.setStyleSheet(f"font-size: 12px; color: {t['text_primary']}; background: transparent;")
            summary_text = str(entry.get("summary") or "")
            line2 = QLabel(summary_text)
            line2.setStyleSheet(f"font-size: 10px; color: {t['text_secondary']}; background: transparent;")
            line2.setVisible(bool(summary_text))
            col.addWidget(line1)
            col.addWidget(line2)
            col_w = QWidget()
            col_w.setLayout(col)
            col_w.setStyleSheet("background: transparent;")
            check = QCheckBox()
            check.setChecked(scan_id in self._selected_scan_ids)
            check.setEnabled(scan_id in self._selected_scan_ids or len(self._selected_scan_ids) < 2)
            check.toggled.connect(lambda on, sid=scan_id: self._toggle_select(sid, on))
            row.addWidget(check)
            row.addWidget(bar)
            row.addWidget(col_w, stretch=1)
            row.addWidget(_TagLabel(status_text, status_bg))
            item = QListWidgetItem()
            item.setSizeHint(wrap.sizeHint())
            self._list_widget.addItem(item)
            self._list_widget.setItemWidget(item, wrap)
        self._empty_label.setVisible(not self._history_list)
        self._clear_btn.setEnabled(bool(self._history_list))
        self._refresh_sel_count()

    def _toggle_select(self, scan_id: str, checked: bool) -> None:
        """切换某次扫描的选中状态（最多保留 2 个）。"""
        if checked:
            if len(self._selected_scan_ids) >= 2:
                self._selected_scan_ids.pop(0)
            self._selected_scan_ids.append(scan_id)
        elif scan_id in self._selected_scan_ids:
            self._selected_scan_ids.remove(scan_id)
        self._rebuild_checks()
        self._refresh_sel_count()

    def _rebuild_checks(self) -> None:
        """仅重刷各行勾选态（避免整体重建导致滚动位置丢失）。"""
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            holder = self._list_widget.itemWidget(item)
            if holder is None:
                continue
            check = holder.findChild(QCheckBox)
            if check is None:
                continue
            sid = str(self._history_list[i].get("scan_id"))
            check.blockSignals(True)
            check.setChecked(sid in self._selected_scan_ids)
            check.setEnabled(sid in self._selected_scan_ids or len(self._selected_scan_ids) < 2)
            check.blockSignals(False)

    def _refresh_sel_count(self) -> None:
        """已选计数与对比按钮可用态刷新。"""
        n = len(self._selected_scan_ids)
        self._sel_count_label.setText(f"已选 {n}/2")
        self._sel_count_label.setVisible(bool(self._history_list))
        self._compare_btn.setEnabled(n == 2)

    def _compare_selected(self) -> None:
        """调用 compareScans 比较选中的两次扫描并复用摘要区展示。"""
        if len(self._selected_scan_ids) != 2:
            return
        raw = self._wc.compareScans(self._ws_id, self._selected_scan_ids[0], self._selected_scan_ids[1])
        parsed = self._parse_json(raw)
        self._comparison = parsed if isinstance(parsed, dict) else {}
        self._refresh_comparison()

    def _clear_history(self) -> None:
        """清空该工作区的全部扫描历史。"""
        self._wc.clearWorkspaceHistory(self._ws_id)
        self._history_list = []
        self._trend_data = []
        self._comparison = {}
        self._selected_scan_ids = []
        self._refresh_trend()
        self._refresh_comparison()
        self._rebuild_list()
