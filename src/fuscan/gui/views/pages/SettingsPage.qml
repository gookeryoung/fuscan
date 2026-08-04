import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 设置页：2 个 Tab（规则 / 通用）。
// 扫描参数/忽略目录/文件类型白名单已迁移到 RuleSet 顶层，
// 由 RulesController.effectiveConfigPreview 暴露只读预览。
// 白名单条目经 WhitelistController.addEntry 委托写入 user-scan.yaml。
Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController
    property WhitelistControllerType whitelistController: WhitelistController
    property RulesControllerType rulesController: RulesController
    property WorkspaceControllerType workspaceController: WorkspaceController
    // 字体列表懒加载标记：首次切到「通用」Tab 时才调 Qt.fontFamilies()
    // （Windows 数百字体，同步调用阻塞主线程），避免设置页构造期阻塞
    property bool _fontListLoaded: false

    // 白名单导入/导出文件对话框
    Dialogs.FileDialog {
        id: whitelistImportDialog
        title: "导入误报白名单"
        nameFilters: ["JSON 文件 (*.json)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = whitelistImportDialog.fileUrl.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            whitelistOpMsg.text = whitelistController.importJson(pathStr)
        }
    }
    Dialogs.FileDialog {
        id: whitelistExportDialog
        title: "导出误报白名单"
        nameFilters: ["JSON 文件 (*.json)", "所有文件 (*.*)"]
        selectExisting: false
        defaultSuffix: "json"
        onAccepted: {
            var pathStr = whitelistExportDialog.fileUrl.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            whitelistOpMsg.text = whitelistController.exportJson(pathStr)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: 0

        // ---------- 标题栏 ----------
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 0
            Layout.rightMargin: 0
            spacing: 12
            Label {
                text: "设置"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconSource: "qrc:/icons/rescan.svg"
                text: "重置"
                tooltip: "重置字体配置为默认值"
                accent: "secondary"
                onClicked: configController.resetToDefaults()
            }
        }

        // ---------- TabBar ----------
        TabBar {
            id: settingsTabBar
            Layout.fillWidth: true
            Layout.topMargin: 8
            spacing: 0
            currentIndex: 0
            // 切到「通用」Tab（索引 1）时懒加载字体列表，避免页面构造期同步阻塞
            onCurrentIndexChanged: {
                if (currentIndex === 1 && !settingsPage._fontListLoaded) {
                    settingsPage._fontListLoaded = true
                    fontFamilyCombo.model = Qt.fontFamilies()
                    if (configController.fontFamily) {
                        var idx = fontFamilyCombo.find(configController.fontFamily)
                        if (idx >= 0) fontFamilyCombo.currentIndex = idx
                    }
                }
            }
            background: Rectangle {
                color: "transparent"
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 1
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                }
            }
            Repeater {
                model: ["规则", "通用"]
                TabButton {
                    id: tabBtn
                    text: modelData
                    height: theme.btnHeightSecondary
                    contentItem: Label {
                        text: tabBtn.text
                        color: tabBtn.checked
                              ? theme.colorPrimary
                              : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                        font.pixelSize: theme.fontSizeBody
                        font.bold: tabBtn.checked
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        id: tabBg
                        color: "transparent"
                        Rectangle {
                            anchors.bottom: tabBg.bottom
                            anchors.horizontalCenter: tabBg.horizontalCenter
                            width: tabBg.width - 16
                            height: 2
                            color: tabBtn.checked ? theme.colorPrimary : "transparent"
                        }
                    }
                }
            }
        }

        // ---------- 内容区（StackLayout 切换） ----------
        StackLayout {
            id: settingsStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 16
            currentIndex: settingsTabBar.currentIndex

            // ===== Tab 1: 规则（生效配置预览 + 白名单 + RulesPanel） =====
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsStack.width
                    spacing: 16

                    // ---------- 生效配置预览（只读，来自 effective RuleSet） ----------
                    GroupBox {
                        Layout.fillWidth: true
                        title: "生效配置预览（来自规则集，只读）"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8

                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "扫描压缩包"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.scanArchives ? "是" : "否"
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大工作线程"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.maxWorkers
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大文件大小（MB）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.maxFileSizeMB
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大扫描深度（0=无限）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.maxDepth
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用扫描结果缓存"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.cacheEnabled ? "是" : "否"
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用性能详细日志"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.perfLogEnabled ? "是" : "否"
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "忽略目录（" + rulesController.effectiveConfigPreview.ignoreDirs.length + " 项）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                            // 忽略目录列表（折叠展示，避免占用过多空间）
                            TextArea {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 80
                                readOnly: true
                                wrapMode: TextArea.Wrap
                                text: rulesController.effectiveConfigPreview.ignoreDirs.join(", ")
                                font.pixelSize: 11
                                font.family: "Consolas, Monaco, monospace"
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                background: Rectangle {
                                    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                    border.width: 1
                                    radius: theme.radiusSm
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "白名单条目数"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: rulesController.effectiveConfigPreview.whitelistCount
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                            }
                        }
                    }

                    // ---------- 白名单（误报抑制） ----------
                    Label {
                        text: "白名单条目在扫描命中聚合阶段过滤：匹配 (路径 glob, 规则名) 组合的命中将被排除。规则名 * 表示匹配任意规则。"
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }

                    // ---------- 白名单：添加表单 ----------
                    Rectangle {
                        Layout.fillWidth: true
                        height: addFormColumn.implicitHeight + 16
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusSm

                        ColumnLayout {
                            id: addFormColumn
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 4

                            Label {
                                text: "添加白名单条目"
                                font.bold: true
                                font.pixelSize: theme.fontSizeBody
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    text: "路径 glob"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    Layout.preferredWidth: 60
                                }
                                TextField {
                                    id: wlPathGlobInput
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                    font.family: "Consolas, Monaco, monospace"
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    placeholderText: "如 /a/vendor/*.txt 或 /a/b/c.txt（* 匹配任意字符）"
                                    selectByMouse: true
                                    background: Rectangle {
                                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                    }
                                    onAccepted: {
                                        if (text.trim().length > 0) {
                                            whitelistOpMsg.text = whitelistController.addEntry(text, wlRuleNameInput.text, wlNoteInput.text)
                                            text = ""
                                            wlRuleNameInput.text = ""
                                            wlNoteInput.text = ""
                                        }
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                Label {
                                    text: "规则名"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    Layout.preferredWidth: 60
                                }
                                TextField {
                                    id: wlRuleNameInput
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                    font.family: "Consolas, Monaco, monospace"
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    placeholderText: "留空表示匹配任意规则（*）"
                                    selectByMouse: true
                                    background: Rectangle {
                                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                    }
                                }
                                Label {
                                    text: "备注"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    Layout.leftMargin: 8
                                }
                                TextField {
                                    id: wlNoteInput
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    placeholderText: "可空"
                                    selectByMouse: true
                                    background: Rectangle {
                                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                    }
                                }
                                Button {
                                    text: "添加"
                                    implicitHeight: 32
                                    font.pixelSize: theme.fontSizeSmall
                                    enabled: wlPathGlobInput.text.trim().length > 0
                                    onClicked: {
                                        whitelistOpMsg.text = whitelistController.addEntry(wlPathGlobInput.text, wlRuleNameInput.text, wlNoteInput.text)
                                        wlPathGlobInput.text = ""
                                        wlRuleNameInput.text = ""
                                        wlNoteInput.text = ""
                                    }
                                }
                            }
                        }
                    }

                    // ---------- 白名单：顶部操作行（导入/导出/清空 + 计数） ----------
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        IconButton {
                            iconSource: "qrc:/icons/export_json.svg"
                            text: "导入"
                            tooltip: "从 JSON 文件导入白名单（合并去重）"
                            accent: "secondary"
                            onClicked: whitelistImportDialog.open()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/export.svg"
                            text: "导出"
                            tooltip: "导出当前白名单到 JSON 文件"
                            accent: "secondary"
                            enabled: whitelistController.whitelistCount > 0
                            onClicked: whitelistExportDialog.open()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/delete.svg"
                            text: "清空"
                            tooltip: "清空全部 JSON store 白名单条目（不影响 user-scan.yaml）"
                            accent: "danger"
                            enabled: whitelistController.whitelistCount > 0
                            onClicked: {
                                whitelistController.clearAll()
                                whitelistOpMsg.text = "已清空 JSON store 白名单"
                            }
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: "共 " + whitelistController.whitelistCount + " 条"
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                    }

                    // 白名单操作消息
                    Label {
                        id: whitelistOpMsg
                        Layout.fillWidth: true
                        visible: text !== ""
                        font.pixelSize: 11
                        color: {
                            if (text.indexOf("已添加") >= 0 || text.indexOf("已导入") >= 0 || text.indexOf("已导出") >= 0 || text.indexOf("已清空") >= 0) return theme.colorSuccess
                            if (text.indexOf("失败") >= 0 || text.indexOf("不能") >= 0) return theme.colorDanger
                            return theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                        wrapMode: Text.WordWrap
                    }

                    // ---------- 白名单条目列表 ----------
                    ListView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 360
                        clip: true
                        interactive: true
                        cacheBuffer: 200
                        model: whitelistController.whitelistEntries
                        // 空状态提示
                        Label {
                            anchors.centerIn: parent
                            visible: whitelistController.whitelistCount === 0
                            text: "（暂无白名单条目）"
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                        delegate: ItemDelegate {
                            width: ListView.view.width
                            height: 40

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 8

                                // 路径 glob
                                Label {
                                    text: modelData.pathGlob
                                    font.pixelSize: 12
                                    font.family: "Consolas, Monaco, monospace"
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                }
                                // 规则名 tag
                                Rectangle {
                                    radius: theme.radiusSm
                                    color: modelData.ruleName === "*" ? theme.colorPrimary : theme.colorAccent
                                    width: wlRuleTagLabel.implicitWidth + 12
                                    height: wlRuleTagLabel.implicitHeight + 4
                                    Label {
                                        id: wlRuleTagLabel
                                        anchors.centerIn: parent
                                        text: modelData.ruleName === "*" ? "全部规则" : modelData.ruleName
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: theme.colorTextOnPrimary
                                    }
                                }
                                // 来源 tag（rules / runtime）
                                Rectangle {
                                    visible: modelData.source !== undefined
                                    radius: theme.radiusSm
                                    color: modelData.source === "rules" ? theme.colorSuccess : theme.colorWarning
                                    width: wlSourceTagLabel.implicitWidth + 10
                                    height: wlSourceTagLabel.implicitHeight + 4
                                    Label {
                                        id: wlSourceTagLabel
                                        anchors.centerIn: parent
                                        text: modelData.source === "rules" ? "规则" : "运行时"
                                        font.pixelSize: 9
                                        font.bold: true
                                        color: theme.colorTextOnPrimary
                                    }
                                }
                                // 创建时间
                                Label {
                                    text: modelData.createdAt
                                    font.pixelSize: 10
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    visible: modelData.createdAt !== ""
                                }
                                // 备注
                                Label {
                                    text: modelData.note
                                    font.pixelSize: 10
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    visible: modelData.note !== ""
                                    elide: Text.ElideRight
                                    Layout.maximumWidth: 160
                                }
                                IconButton {
                                    iconSource: "qrc:/icons/close.svg"
                                    tooltip: "移除（仅 JSON store 历史条目）"
                                    accent: "danger"
                                    iconSize: 14
                                    btnSize: 24
                                    onClicked: whitelistController.removeEntry(index)
                                }
                            }
                        }
                    }

                    // ---------- 规则配置 ----------
                    // 不使用 Layout.fillHeight：在 ScrollView 内 fillHeight 会因内容高度
                    // 等于可视区高度而把 RulesPanel 挤到 0。改用 preferredHeight 给定
                    // 稳定显示高度，超出可视区时由外层 ScrollView 滚动兜底。
                    RulesPanel {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 520
                        // 设置页内嵌不启用折叠，完整展示
                        collapsible: false
                        collapsed: false
                    }
                }
            }

            // ===== Tab 2: 通用（字体设置） =====
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsStack.width
                    spacing: 16

                    Label {
                        text: "字体设置"
                        font.pixelSize: theme.fontSizeHeading
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    // 字体族
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "字体"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: fontFamilyCombo
                            Layout.fillWidth: true
                            // 字体列表懒加载：默认 model 为空，切到「通用」Tab 时
                            // 由 settingsTabBar.onCurrentIndexChanged 加载，避免页面
                            // 构造期同步调用 Qt.fontFamilies()（Windows 数百字体）阻塞主线程
                            displayText: configController.fontFamily
                                ? configController.fontFamily
                                : "平台默认"
                            onActivated: {
                                configController.setFontFamily(fontFamilyCombo.currentText)
                            }
                        }
                        // 清除按钮：恢复平台默认
                        IconButton {
                            text: "默认"
                            tooltip: "恢复平台默认字体"
                            accent: "ghost"
                            onClicked: configController.setFontFamily("")
                        }
                    }

                    // 字号
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "字号"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: fontSizeCombo
                            model: [10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24]
                            displayText: configController.fontSize + " px"
                            onActivated: {
                                configController.setFontSize(fontSizeCombo.currentValue)
                            }
                            Component.onCompleted: {
                                var idx = fontSizeCombo.find(configController.fontSize)
                                if (idx >= 0) fontSizeCombo.currentIndex = idx
                            }
                        }
                        Label {
                            text: "（基准字号，其他字号基于此计算）"
                            font.pixelSize: theme.fontSizeCaption
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                    }

                    // 最小字号
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "最小字号"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: minFontSizeCombo
                            model: [8, 9, 10, 11, 12, 13, 14, 15, 16]
                            displayText: configController.minFontSize + " px"
                            onActivated: {
                                configController.setMinFontSize(minFontSizeCombo.currentValue)
                            }
                            Component.onCompleted: {
                                var idx = minFontSizeCombo.find(configController.minFontSize)
                                if (idx >= 0) minFontSizeCombo.currentIndex = idx
                            }
                        }
                        Label {
                            text: "（小字号下限，避免高 DPI 屏幕显示过小）"
                            font.pixelSize: theme.fontSizeCaption
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                    }

                    // 加粗
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "加粗"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        CheckBox {
                            checked: configController.fontBold
                            onCheckedChanged: configController.setFontBold(checked)
                        }
                    }

                    // 预览
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 60
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusMd
                        Label {
                            anchors.centerIn: parent
                            text: "字体预览 ABC 中文 123"
                            font.family: configController.fontFamily || theme.fontFamily
                            font.pixelSize: configController.fontSize
                            font.bold: configController.fontBold
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

        }
    }
}
