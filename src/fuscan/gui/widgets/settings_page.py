"""设置页（Widgets 版）：扫描参数 + 字体设置 + 规则测试沙盒 + 规则编辑。

对照 QML 版 :file:`SettingsPage.qml` 等价迁移：

- 扫描参数：并发线程/最大深度/大文件阈值 SpinBox 与三个开关，写入规则集
  ``scan_params``（经 RulesController 持久化到 user-scan.yaml）
- 字体设置：字体族（首次显示时懒加载）/字号/最小字号/加粗 + 实时预览，
  写入 ConfigController（user-config.yaml）
- 规则测试：选规则输入文本即时验证匹配结果（命中片段绿色高亮列表）
- 规则编辑：user-scan.yaml 自定义规则的列表 + 新建/编辑/删除与内嵌表单
  （组合规则只读提示，叶子规则全字段编辑）

与 QML 版差异：Widgets 写回用「先赋初值、后连接信号」顺序天然避免
binding loop；控制器属性变更不推送信号，页面在用户操作后重读生效预览。
"""

# pyrefly: ignore-errors
# PySide2 存根重载合并缺陷导致 QWidget/QLayout 调用与 Signal.connect 误报，
# 详见 sidebar.py 头部说明；本文件为纯 GUI 布局代码，采用文件级压制。

from __future__ import annotations

import json

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QFontDatabase
from PySide2.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fuscan.gui.controllers import AppController
from fuscan.gui.widgets.about_page import CardGroupBox
from fuscan.gui.widgets.qss import palette_tokens

__all__ = ["SettingsPage"]

_FONT_SIZE_CHOICES = [10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24]
_MIN_FONT_SIZE_CHOICES = [8, 9, 10, 11, 12, 13, 14, 15, 16]

# 枚举值 ↔ 中文展示映射（编辑表单下拉框与规则行摘要共用）
_TARGET_TEXT = {"filename": "文件名", "path": "路径", "content": "内容"}
_MODE_TEXT = {"equals": "相等", "startswith": "开头", "endswith": "结尾", "regex": "正则", "contains": "包含"}


def _json_obj(raw: str) -> dict[str, object] | None:
    """解析控制器返回的 JSON 字符串，失败返回 ``{"error": ...}`` 占位。"""
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"error": "结果解析失败"}
    except ValueError:
        return {"error": "结果解析失败"}


class SettingsPage(QWidget):
    """设置页视图：聚合 RulesController / ConfigController 配置入口。"""

    def __init__(self, controller: AppController, parent: QWidget | None = None) -> None:
        """初始化设置页并读取当前配置填充控件。

        :param controller: 主控制器（使用其 :attr:`rules` / :attr:`config` 子控制器）
        :param parent: 父部件
        """
        super().__init__(parent)
        self._rules = controller.rules
        self._config = controller.config
        self._dark = False
        self._font_families_loaded = False
        # 正在编辑的规则字典（None=收起表单）；新建后定位到刚追加的规则
        self._editing_rule: dict[str, object] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(12)

        # ---------- 标题栏 ----------
        header = QHBoxLayout()
        title = QLabel("设置")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        reset_font_btn = QPushButton(" 重置字体")
        reset_font_btn.setProperty("variant", "secondary")
        reset_font_btn.setToolTip("重置字体配置为默认值")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(reset_font_btn)
        outer.addLayout(header)
        reset_font_btn.clicked.connect(self._config.resetToDefaults)

        # ---------- 内容滚动区 ----------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, stretch=1)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(14)

        self._build_scan_params_section(root)
        self._build_font_section(root)
        self._build_rule_test_section(root)
        self._build_rule_editor_section(root)

        # ---------- 信号联动 ----------
        # 规则集变更（加载/保存/删除）后刷新测试下拉框、规则行与生效参数
        self._rules.rulesetChanged.connect(self.refresh_all)

    # ----------------------------- 构建块 -----------------------------

    def _build_scan_params_section(self, root: QVBoxLayout) -> None:
        """构建扫描参数卡片：三个 SpinBox + 三个开关 + 恢复默认按钮。"""
        group = CardGroupBox("扫描参数")
        c = self._rules
        preview = c.effectiveConfigPreview

        def _spin_row(caption: str, hint: str) -> tuple[QHBoxLayout, QSpinBox]:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(caption)
            label.setFixedWidth(100)
            spin = QSpinBox()
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("font-size: 11px;")
            row.addWidget(label)
            row.addWidget(spin)
            row.addWidget(hint_label)
            row.addStretch()
            return row, spin

        workers_row, self._workers_spin = _spin_row("并发线程", "（1-32，越大扫描越快但占用资源越多）")
        self._workers_spin.setRange(1, 32)
        self._workers_spin.setValue(int(preview["maxWorkers"]))
        group.content.addLayout(workers_row)

        depth_row, self._depth_spin = _spin_row("最大深度", "（0=无限递归）")
        self._depth_spin.setRange(0, 100)
        self._depth_spin.setValue(int(preview["maxDepth"]))
        group.content.addLayout(depth_row)

        size_row, self._size_spin = _spin_row("大文件阈值", "MB（0=不限，超过此大小的文件跳过内容扫描）")
        self._size_spin.setRange(0, 4096)
        self._size_spin.setValue(int(preview["maxFileSizeMB"]))
        group.content.addLayout(size_row)

        switches_row = QHBoxLayout()
        switches_row.setSpacing(16)
        self._archives_check = QCheckBox("扫描压缩包")
        self._cache_check = QCheckBox("内容缓存")
        self._perf_check = QCheckBox("性能日志")
        self._archives_check.setChecked(bool(preview["scanArchives"]))
        self._cache_check.setChecked(bool(preview["cacheEnabled"]))
        self._perf_check.setChecked(bool(preview["perfLogEnabled"]))
        switches_row.addWidget(self._archives_check)
        switches_row.addWidget(self._cache_check)
        switches_row.addWidget(self._perf_check)
        switches_row.addStretch()
        group.content.addLayout(switches_row)

        defaults_row = QHBoxLayout()
        defaults_row.addStretch()
        restore_btn = QPushButton(" 恢复默认")
        restore_btn.setProperty("variant", "ghost")
        restore_btn.setToolTip("恢复扫描参数为内置默认值")
        defaults_row.addWidget(restore_btn)
        group.content.addLayout(defaults_row)
        restore_btn.clicked.connect(self._on_restore_scan_params)

        # 先赋初值后连接信号：避免程序化赋值触发重复写回
        self._workers_spin.valueChanged.connect(self._rules.setMaxWorkers)
        self._depth_spin.valueChanged.connect(self._rules.setMaxDepth)
        self._size_spin.valueChanged.connect(self._rules.setMaxFileSizeMb)
        self._archives_check.toggled.connect(self._rules.setScanArchives)
        self._cache_check.toggled.connect(self._rules.setCacheEnabled)
        self._perf_check.toggled.connect(self._rules.setPerfLogEnabled)
        root.addWidget(group)

    def _on_restore_scan_params(self) -> None:
        """恢复扫描参数默认值并回填控件。"""
        self._rules.resetScanParams()
        preview = self._rules.effectiveConfigPreview
        self._workers_spin.setValue(int(preview["maxWorkers"]))
        self._depth_spin.setValue(int(preview["maxDepth"]))
        self._size_spin.setValue(int(preview["maxFileSizeMB"]))
        self._archives_check.setChecked(bool(preview["scanArchives"]))
        self._cache_check.setChecked(bool(preview["cacheEnabled"]))
        self._perf_check.setChecked(bool(preview["perfLogEnabled"]))

    def _build_font_section(self, root: QVBoxLayout) -> None:
        """构建字体设置卡片：族/字号/最小字号/加粗 + 预览。"""
        group = CardGroupBox("字体设置")
        c = self._config

        family_row = QHBoxLayout()
        family_row.setSpacing(8)
        family_label = QLabel("字体")
        family_label.setFixedWidth(80)
        self._family_combo = QComboBox()
        # 字体列表懒加载（Windows 数百字体同步枚举阻塞主线程），showEvent 时填充；
        # 已配置字体先作为首项插入以正确回显
        if c.fontFamily:
            self._family_combo.addItem(c.fontFamily)
            self._family_combo.setCurrentIndex(0)
        else:
            self._family_combo.addItem("平台默认")
        default_family_btn = QPushButton("默认")
        default_family_btn.setProperty("variant", "ghost")
        default_family_btn.setToolTip("恢复平台默认字体")
        family_row.addWidget(family_label)
        family_row.addWidget(self._family_combo, stretch=1)
        family_row.addWidget(default_family_btn)
        group.content.addLayout(family_row)
        default_family_btn.clicked.connect(lambda: self._config.setFontFamily(""))

        def _size_row(caption: str, choices: list[int], current: int, hint: str) -> QComboBox:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(caption)
            label.setFixedWidth(80)
            combo = QComboBox()
            combo.addItems([str(v) for v in choices])
            idx = combo.findText(str(current))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("font-size: 11px;")
            row.addWidget(label)
            row.addWidget(combo)
            row.addWidget(hint_label)
            row.addStretch()
            group.content.addLayout(row)
            return combo

        self._size_combo = _size_row("字号", _FONT_SIZE_CHOICES, c.fontSize, "（基准字号，其他字号基于此计算）")
        self._min_size_combo = _size_row(
            "最小字号", _MIN_FONT_SIZE_CHOICES, c.minFontSize, "（小字号下限，避免高 DPI 屏幕显示过小）"
        )

        bold_row = QHBoxLayout()
        bold_row.setSpacing(8)
        bold_label = QLabel("加粗")
        bold_label.setFixedWidth(80)
        self._bold_check = QCheckBox()
        self._bold_check.setChecked(c.fontBold)
        bold_row.addWidget(bold_label)
        bold_row.addWidget(self._bold_check)
        bold_row.addStretch()
        group.content.addLayout(bold_row)

        self._preview = QLabel("字体预览 ABC 中文 123")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setFixedHeight(60)
        group.content.addWidget(self._preview)

        self._family_combo.activated[str].connect(self._config.setFontFamily)
        self._size_combo.activated[int].connect(lambda i: self._config.setFontSize(int(self._size_combo.itemText(i))))
        self._min_size_combo.activated[int].connect(
            lambda i: self._config.setMinFontSize(int(self._min_size_combo.itemText(i)))
        )
        self._bold_check.toggled.connect(self._config.setFontBold)
        self._apply_preview()
        root.addWidget(group)

    def showEvent(self, event: object) -> None:
        """首次显示时懒加载系统字体族列表。"""
        super().showEvent(event)
        if not self._font_families_loaded:
            self._font_families_loaded = True
            current = self._config.fontFamily
            families = QFontDatabase().families()
            self._family_combo.blockSignals(True)
            self._family_combo.clear()
            self._family_combo.addItem("平台默认")
            self._family_combo.addItems(families)
            if current:
                idx = self._family_combo.findText(current)
                if idx >= 0:
                    self._family_combo.setCurrentIndex(idx)
            self._family_combo.blockSignals(False)

    def _apply_preview(self) -> None:
        """按当前字体配置刷新预览标签样式。"""
        c = self._config
        family_part = f"font-family: '{c.fontFamily}';" if c.fontFamily else ""
        bold_part = "font-weight: bold;" if c.fontBold else ""
        self._preview.setStyleSheet(f"{family_part} font-size: {c.fontSize}px; {bold_part}")
        t = palette_tokens(self._dark)
        self._preview.setStyleSheet(
            self._preview.styleSheet() + f"color: {t['text_primary']}; border: 1px solid {t['border']};"
            f" background-color: {t['bg_card']}; border-radius: 6px;"
        )

    def _build_rule_test_section(self, root: QVBoxLayout) -> None:
        """构建规则测试沙盒卡片：规则选择 + 文本输入 + 结果展示。"""
        group = CardGroupBox("规则测试")
        desc = QLabel(
            "选择规则并输入文本，即时验证匹配结果。CONTENT 规则匹配输入文本，FILENAME/PATH 规则匹配文件名 test.txt。"
        )
        desc.setWordWrap(True)
        group.content.addWidget(desc)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_label = QLabel("规则")
        pick_label.setFixedWidth(80)
        self._test_rule_combo = QComboBox()
        test_btn = QPushButton(" 测试匹配")
        test_btn.setProperty("variant", "primary")
        pick_row.addWidget(pick_label)
        pick_row.addWidget(self._test_rule_combo, stretch=1)
        pick_row.addWidget(test_btn)
        group.content.addLayout(pick_row)
        test_btn.clicked.connect(self._run_rule_test)

        self._test_input = QPlainTextEdit()
        self._test_input.setPlaceholderText("输入测试文本...")
        self._test_input.setFixedHeight(80)
        group.content.addWidget(self._test_input)

        # 结果摘要与错误提示（初始隐藏）
        self._test_summary = QLabel()
        self._test_error = QLabel()
        self._test_error.setWordWrap(True)
        group.content.addWidget(self._test_summary)
        group.content.addWidget(self._test_error)
        self._test_matches_box = QWidget()
        self._matches_layout = QVBoxLayout(self._test_matches_box)
        self._matches_layout.setContentsMargins(0, 0, 0, 0)
        self._matches_layout.setSpacing(4)
        group.content.addWidget(self._test_matches_box)
        self._test_result: dict[str, object] | None = None
        self._refresh_test_result_views()

        self._reload_test_rules()
        root.addWidget(group)

    def _reload_test_rules(self) -> None:
        """从全局规则模型重建测试用规则名下拉框。"""
        names = [r.name for r in self._rules.ruleModel.rules]
        current = self._test_rule_combo.currentText() if self._test_rule_combo.count() else ""
        self._test_rule_combo.blockSignals(True)
        self._test_rule_combo.clear()
        self._test_rule_combo.addItems(names)
        if current:
            idx = self._test_rule_combo.findText(current)
            if idx >= 0:
                self._test_rule_combo.setCurrentIndex(idx)
        self._test_rule_combo.blockSignals(False)

    def _run_rule_test(self) -> None:
        """对选中规则执行文本匹配测试并渲染结果。"""
        raw = self._rules.testRuleText(self._test_rule_combo.currentText(), self._test_input.toPlainText())
        self._test_result = _json_obj(raw)
        self._refresh_test_result_views()

    def _refresh_test_result_views(self) -> None:
        """渲染规则测试结果：摘要行、错误行、命中文本列表。"""
        t = palette_tokens(self._dark)
        result = self._test_result
        has_result = result is not None and "error" not in result
        self._test_summary.setVisible(has_result)
        error = str(result.get("error")) if result is not None and result.get("error") else ""
        self._test_error.setVisible(bool(error))
        if result is not None and not error and has_result:
            matched_count = int(result.get("matchCount") or 0)  # type: ignore[call-overload]
            if matched_count > 0:
                self._test_summary.setText(f"命中 {matched_count} 次（目标: {result.get('target')}）")
                self._test_summary.setStyleSheet(f"color: {t['success']}; background: transparent;")
            else:
                self._test_summary.setText("未命中")
                self._test_summary.setStyleSheet(f"color: {t['text_secondary']}; background: transparent;")
        elif error:
            self._test_error.setText(error)
            self._test_error.setStyleSheet(f"color: {t['danger']}; background: transparent;")
        # 重建命中文本片段列表
        while self._matches_layout.count():
            item = self._matches_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        matches = (result or {}).get("matches") or []
        if isinstance(matches, list) and matches:
            self._test_matches_box.setVisible(True)
            for m in matches:
                text = str(m.get("text")) if isinstance(m, dict) else str(m)
                chip = QLabel(text)
                chip.setWordWrap(True)
                chip.setStyleSheet(
                    f"background-color: {t['success']}1f; border-radius: 4px;"
                    f" padding: 4px 6px; color: {t['text_primary']};"
                )
                self._matches_layout.addWidget(chip)
        else:
            self._test_matches_box.setVisible(False)

    def _build_rule_editor_section(self, root: QVBoxLayout) -> None:
        """构建规则编辑卡片：规则行列表 + 新建按钮 + 内嵌编辑表单。"""
        group = CardGroupBox("规则编辑")
        self._editor_group = group
        desc = QLabel(
            "管理 user-scan.yaml 中的自定义规则。仅叶子规则支持图形编辑，组合规则（AND/OR/NOT）请外部编辑 YAML。"
        )
        desc.setWordWrap(True)
        group.content.addWidget(desc)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        new_btn = QPushButton(" 新建规则")
        new_btn.setProperty("variant", "primary")
        new_btn.setToolTip("在 user-scan.yaml 追加默认规则并打开编辑器")
        self._rule_count_label = QLabel()
        toolbar.addWidget(new_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._rule_count_label)
        group.content.addLayout(toolbar)
        new_btn.clicked.connect(self._create_rule)

        self._message_label = QLabel()
        self._message_label.setWordWrap(True)
        self._message_label.setVisible(False)
        group.content.addWidget(self._message_label)

        self._rules_rows_box = QWidget()
        self._rules_rows_layout = QVBoxLayout(self._rules_rows_box)
        self._rules_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rules_rows_layout.setSpacing(4)
        group.content.addWidget(self._rules_rows_box)

        # 内嵌编辑表单（默认隐藏）
        self._editor_form = _RuleEditorForm(self._rules)
        self._editor_form.saveRequested.connect(self._save_editing_rule)
        self._editor_form.cancelRequested.connect(self._close_editor)
        self._editor_form.setVisible(False)
        group.content.addWidget(self._editor_form)

        root.addWidget(group)

    # ----------------------------- 规则编辑逻辑 -----------------------------

    def _lookup_rule_by_name(self, name: str) -> dict[str, object] | None:
        """在用户规则列表中按名查找规则字典。"""
        for r in self._rules.userRulesModel:
            if r.get("name") == name:
                return r
        return None

    def _create_rule(self) -> None:
        """新建默认占位规则并直接展开编辑表单。"""
        parsed = _json_obj(self._rules.createRule())
        if parsed.get("ok"):
            rule = self._lookup_rule_by_name(str(parsed.get("name")))
            self._open_editor(rule)
            self._set_message("")
        else:
            self._set_message(str(parsed.get("error") or "新建失败"))

    def _open_editor(self, rule: dict[str, object] | None) -> None:
        """打开内嵌编辑表单并载入指定规则（None 视为新建占位）。"""
        self._editing_rule = rule
        self._editor_form.load_rule(rule)
        self._editor_form.setVisible(True)

    def _close_editor(self) -> None:
        """关闭编辑表单（放弃未保存修改）。"""
        self._editing_rule = None
        self._editor_form.setVisible(False)

    def _save_editing_rule(self, payload: dict[str, object]) -> None:
        """把编辑表单 payload 写回 user-scan.yaml。"""
        parsed = _json_obj(self._rules.updateRule(json.dumps(payload)))
        if parsed.get("ok"):
            self._close_editor()
            self._set_message("")
        else:
            self._set_message(str(parsed.get("error") or "保存失败"))

    def _delete_rule(self, rule: dict[str, object]) -> None:
        """删除指定名称的用户规则。"""
        name = str(rule.get("name"))
        parsed = _json_obj(self._rules.deleteRule(name))
        if parsed.get("ok"):
            if self._editing_rule is not None and self._editing_rule.get("name") == name:
                self._close_editor()
            self._set_message("")
        else:
            self._set_message(str(parsed.get("error") or "删除失败"))

    def _set_message(self, message: str) -> None:
        """更新规则编辑区反馈消息（空串隐藏）。"""
        self._message_label.setText(message)
        self._message_label.setVisible(bool(message))
        if message:
            t = palette_tokens(self._dark)
            self._message_label.setStyleSheet(f"color: {t['danger']}; background: transparent;")

    def _rebuild_rule_rows(self) -> None:
        """重建用户规则行列表（严重度色点 + 名称 + 摘要 + 编辑/删除按钮）。"""
        t = palette_tokens(self._dark)
        while self._rules_rows_layout.count():
            item = self._rules_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        entries = list(self._rules.userRulesModel)
        self._rule_count_label.setText(f"共 {len(entries)} 条")
        severity_colors = {"critical": t["danger"], "warning": t["warning"]}
        for rule in entries:
            row_widget = QWidget()
            row_widget.setObjectName("ruleRowCard")
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(10, 6, 6, 6)
            row.setSpacing(8)
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot_color = severity_colors.get(str(rule.get("severity")), t["primary"])
            dot.setStyleSheet(f"background-color: {dot_color}; border-radius: 4px; border: none;")
            name_label = QLabel(str(rule.get("name")))
            name_label.setFixedWidth(160)
            name_label.setStyleSheet(f"font-weight: bold; color: {t['text_primary']};")
            if bool(rule.get("isLeaf")):
                target = _TARGET_TEXT.get(str(rule.get("target")), "内容")
                mode = _MODE_TEXT.get(str(rule.get("mode")), "包含")
                summary_text = f"{target} · {mode}"
            else:
                summary_text = "组合规则（只读）"
            summary_label = QLabel(summary_text)
            summary_label.setStyleSheet(f"color: {t['text_secondary']};")
            edit_btn = QPushButton(" 编辑")
            edit_btn.setProperty("variant", "ghost")
            delete_btn = QPushButton(" 删除")
            delete_btn.setProperty("variant", "danger")
            for b in (edit_btn, delete_btn):
                b.setCursor(Qt.PointingHandCursor)
            row.addWidget(dot)
            row.addWidget(name_label)
            row.addWidget(summary_label, stretch=1)
            row.addWidget(edit_btn)
            row.addWidget(delete_btn)
            edit_btn.setEnabled(bool(rule.get("isLeaf")))
            if bool(rule.get("isLeaf")):
                edit_btn.clicked.connect(lambda _=False, r=rule: self._open_editor(r))
            delete_btn.clicked.connect(lambda _=False, r=rule: self._delete_rule(r))
            self._rules_rows_layout.addWidget(row_widget)

    # ----------------------------- 公共 API -----------------------------

    def set_dark(self, dark: bool) -> None:
        """主题切换：刷新语义色相关区域。"""
        if self._dark == dark:
            return
        self._dark = dark
        self._apply_preview()
        self._refresh_test_result_views()
        self._rebuild_rule_rows()

    def refresh_all(self) -> None:
        """从控制器重读全部展示数据（规则集变更时调用）。"""
        self._reload_test_rules()
        self._rebuild_rule_rows()
        if self._editing_rule is not None:
            fresh = self._lookup_rule_by_name(str(self._editing_rule.get("name")))
            if fresh is None:
                self._close_editor()


class _RuleEditorForm(QWidget):
    """规则编辑表单：叶子规则字段编辑 + 即时测试匹配 + 保存/取消。

    对应 QML 版 :file:`components/RuleEditorForm.qml`；组合规则由外层在
    打开前拦截（``isLeaf=False`` 时仅提供删除入口，不进入本表单）。
    """

    saveRequested = Signal(dict)
    cancelRequested = Signal()

    def __init__(self, rules_controller: object, parent: QWidget | None = None) -> None:
        """初始化空白表单。

        :param rules_controller: :class:`~fuscan.gui.controllers.RulesController`
        :param parent: 父部件
        """
        super().__init__(parent)
        self._rules = rules_controller
        self._rule: dict[str, object] | None = None
        self._dark = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 4, 0, 0)
        col.setSpacing(8)

        self._name_field = QLineEdit()
        self._name_field.setPlaceholderText("规则唯一名称")
        self._desc_field = QLineEdit()
        self._desc_field.setPlaceholderText("规则说明（可选）")
        self._pattern_field = QLineEdit()
        self._pattern_field.setPlaceholderText("匹配文本或正则表达式")
        self._replace_with_field = QLineEdit()
        self._replace_with_field.setPlaceholderText("命中内容的替换文本")

        col.addLayout(self._row("规则名", self._name_field))
        self._severity_combo = self._enum_combo(
            col, "严重等级", [("info", "信息"), ("warning", "警告"), ("critical", "严重")]
        )
        col.addLayout(self._row("描述", self._desc_field))

        match_row = QHBoxLayout()
        match_row.setSpacing(8)
        match_label = QLabel("匹配")
        match_label.setFixedWidth(80)
        self._target_combo = QComboBox()
        for value, text in (("content", "内容"), ("filename", "文件名"), ("path", "路径")):
            self._target_combo.addItem(text, value)
        self._mode_combo = QComboBox()
        for value, text in (
            ("contains", "包含"),
            ("equals", "相等"),
            ("startswith", "开头"),
            ("endswith", "结尾"),
            ("regex", "正则"),
        ):
            self._mode_combo.addItem(text, value)
        match_row.addWidget(match_label)
        match_row.addWidget(self._target_combo)
        match_row.addWidget(self._mode_combo)
        col.addLayout(match_row)

        col.addLayout(self._row("模式串", self._pattern_field))
        option_row = QHBoxLayout()
        option_label = QLabel("选项")
        option_label.setFixedWidth(80)
        self._case_check = QCheckBox("区分大小写")
        self._replace_check = QCheckBox("启用替换")
        option_row.addWidget(option_label)
        option_row.addWidget(self._case_check)
        option_row.addWidget(self._replace_check)
        option_row.addStretch()
        col.addLayout(option_row)
        self._replace_with_widget = QWidget()
        replace_row = QHBoxLayout(self._replace_with_widget)
        replace_row.setContentsMargins(0, 0, 0, 0)
        replace_row.setSpacing(8)
        replace_label = QLabel("替换为")
        replace_label.setFixedWidth(80)
        replace_row.addWidget(replace_label)
        replace_row.addWidget(self._replace_with_field, stretch=1)
        col.addWidget(self._replace_with_widget)
        self._replace_with_widget.setVisible(False)
        self._replace_check.toggled.connect(self._replace_with_widget.setVisible)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        col.addWidget(sep)

        test_title = QLabel("即时测试")
        test_title.setStyleSheet("font-size: 11px; font-weight: bold;")
        col.addWidget(test_title)
        self._test_input = QPlainTextEdit()
        self._test_input.setPlaceholderText("输入测试文本（CONTENT 匹配此文本，FILENAME/PATH 匹配 test.txt）...")
        self._test_input.setFixedHeight(60)
        col.addWidget(self._test_input)
        test_btn_row = QHBoxLayout()
        self._test_btn = QPushButton(" 测试匹配")
        self._test_btn.setProperty("variant", "secondary")
        self._test_btn.setEnabled(False)
        test_btn_row.addStretch()
        test_btn_row.addWidget(self._test_btn)
        col.addLayout(test_btn_row)
        self._pattern_field.textChanged.connect(lambda text: self._test_btn.setEnabled(bool(text.strip())))
        self._test_btn.clicked.connect(self._run_inline_test)

        self._summary = QLabel()
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        col.addWidget(self._summary)
        col.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(" 取消")
        cancel_btn.setProperty("variant", "ghost")
        save_btn = QPushButton(" 保存")
        save_btn.setProperty("variant", "primary")
        save_btn.setEnabled(False)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        col.addLayout(btn_row)
        cancel_btn.clicked.connect(self.cancelRequested)
        save_btn.clicked.connect(self._emit_save)
        self._save_btn = save_btn

        valid = lambda f1, f2: bool(f1.strip()) and bool(f2.strip())  # noqa: E731
        for field in (self._name_field, self._pattern_field):
            field.textChanged.connect(
                lambda _: save_btn.setEnabled(valid(self._name_field.text(), self._pattern_field.text()))
            )

    @staticmethod
    def _row(caption: str, widget: QWidget) -> QHBoxLayout:
        """构造「标签 80px + 控件」的标准表单行。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(caption)
        label.setFixedWidth(80)
        row.addWidget(label)
        row.addWidget(widget, stretch=1)
        return row

    @staticmethod
    def _enum_combo(col: QVBoxLayout, caption: str, pairs: list[tuple[str, str]]) -> QComboBox:
        """在布局中追加一行枚举下拉框（userData 存枚举值）。"""
        combo = QComboBox()
        for value, text in pairs:
            combo.addItem(text, value)
        col.addLayout(_RuleEditorForm._row(caption, combo))
        return combo

    @staticmethod
    def _current_enum(combo: QComboBox) -> str:
        """返回下拉框当前选中项的枚举值（userData）。"""
        return str(combo.currentData())

    @staticmethod
    def _select_enum(combo: QComboBox, value: object) -> None:
        """按枚举值定位下拉框项（找不到时归位第 0 项）。"""
        idx = combo.findData(value)
        combo.setCurrentIndex(max(0, idx))

    def load_rule(self, rule: dict[str, object] | None) -> None:
        """载入待编辑规则字典到各字段（None 清空为新建占位）。"""
        self._rule = rule
        self._name_field.setText(str((rule or {}).get("name") or ""))
        self._desc_field.setText(str((rule or {}).get("description") or ""))
        self._select_enum(self._severity_combo, (rule or {}).get("severity") or "info")
        self._select_enum(self._target_combo, (rule or {}).get("target") or "content")
        self._select_enum(self._mode_combo, (rule or {}).get("mode") or "contains")
        self._pattern_field.setText(str((rule or {}).get("pattern") or ""))
        self._case_check.setChecked(bool((rule or {}).get("caseSensitive")))
        replace_on = bool((rule or {}).get("replace"))
        self._replace_check.setChecked(replace_on)
        self._replace_with_field.setText(str((rule or {}).get("replaceWith") or ""))
        self._clear_test_views()

    def build_payload(self) -> dict[str, object]:
        """汇总当前字段为保存/测试共用的 payload 字典。"""
        return {
            "originalName": str(self._rule.get("name")) if self._rule else "",
            "name": self._name_field.text(),
            "severity": self._current_enum(self._severity_combo),
            "target": self._current_enum(self._target_combo),
            "mode": self._current_enum(self._mode_combo),
            "pattern": self._pattern_field.text(),
            "caseSensitive": self._case_check.isChecked(),
            "replace": self._replace_check.isChecked(),
            "replaceWith": self._replace_with_field.text(),
            "description": self._desc_field.text(),
        }

    def _emit_save(self) -> None:
        """校验通过后发出 saveRequested(payload)。"""
        if self._name_field.text().strip() and self._pattern_field.text().strip():
            self.saveRequested.emit(self.build_payload())

    def _run_inline_test(self) -> None:
        """用当前编辑字段（未保存）对测试文本执行匹配并渲染结果。"""
        raw = self._rules.testRuleFields(  # pyrefly: ignore [missing-attribute]
            json.dumps(self.build_payload()), self._test_input.toPlainText()
        )
        parsed = _json_obj(raw)
        t = palette_tokens(self._dark)
        error = str(parsed.get("error") or "")
        self._error_label.setText(error)
        self._error_label.setVisible(bool(error))
        self._error_label.setStyleSheet(f"color: {t['danger']}; background: transparent;")
        if error:
            self._summary.setVisible(False)
            return
        matched = int(parsed.get("matchCount") or 0)
        self._summary.setVisible(True)
        if matched > 0:
            self._summary.setText(f"命中 {matched} 次（目标: {parsed.get('target')}）")
            self._summary.setStyleSheet(f"color: {t['success']}; background: transparent;")
        else:
            self._summary.setText("未命中")
            self._summary.setStyleSheet(f"color: {t['text_secondary']}; background: transparent;")

    def _clear_test_views(self) -> None:
        """清空即时测试结果展示。"""
        self._summary.setVisible(False)
        self._error_label.setVisible(False)
