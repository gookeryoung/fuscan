import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 设置页：扫描参数配置 + 字体设置。
// 规则配置（规则文件/白名单/生效预览）已迁移到文件扫描页任务卡片的「配置规则」
// 与「预览规则」对话框，设置页仅承载扫描参数（写入 user-scan.yaml）与字体设置。
Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController
    property WhitelistControllerType whitelistController: WhitelistController
    property RulesControllerType rulesController: RulesController
    property WorkspaceControllerType workspaceController: WorkspaceController
    // 规则测试沙盒结果（testRuleText 返回的 JSON 解析对象，null=未测试）
    property var testResult: null
    // 规则编辑：正在编辑的规则字典（null=未编辑），editMessage 为保存/删除反馈
    property var editingRule: null
    property string editMessage: ""

    // 在 userRulesModel 中按名查找规则字典（新建后定位到刚追加的规则）
    function lookupRuleByName(name) {
        var m = rulesController.userRulesModel
        for (var i = 0; i < m.length; i++) {
            if (m[i].name === name) return m[i]
        }
        return null
    }
    // 枚举值 → 中文展示
    function targetText(v) {
        if (v === "filename") return "文件名"
        if (v === "path") return "路径"
        return "内容"
    }
    function modeText(v) {
        if (v === "equals") return "相等"
        if (v === "startswith") return "开头"
        if (v === "endswith") return "结尾"
        if (v === "regex") return "正则"
        return "包含"
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

        // ---------- 字体设置 ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 16
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: settingsPage.width
                spacing: 16

                Label {
                    text: "扫描参数"
                    font.pixelSize: theme.fontSizeHeading
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }

                // 并发线程数
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: "并发线程"
                        font.pixelSize: theme.fontSizeBody
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.preferredWidth: 100
                    }
                    SpinBox {
                        from: 1
                        to: 32
                        value: rulesController.effectiveConfigPreview.maxWorkers
                        onValueModified: rulesController.setMaxWorkers(value)
                    }
                    Label {
                        text: "（1-32，越大扫描越快但占用资源越多）"
                        font.pixelSize: theme.fontSizeCaption
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                    Item { Layout.fillWidth: true }
                }

                // 最大深度
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: "最大深度"
                        font.pixelSize: theme.fontSizeBody
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.preferredWidth: 100
                    }
                    SpinBox {
                        from: 0
                        to: 100
                        value: rulesController.effectiveConfigPreview.maxDepth
                        onValueModified: rulesController.setMaxDepth(value)
                    }
                    Label {
                        text: "（0=无限递归）"
                        font.pixelSize: theme.fontSizeCaption
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                    Item { Layout.fillWidth: true }
                }

                // 大文件阈值
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: "大文件阈值"
                        font.pixelSize: theme.fontSizeBody
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.preferredWidth: 100
                    }
                    SpinBox {
                        from: 0
                        to: 4096
                        value: rulesController.effectiveConfigPreview.maxFileSizeMB
                        onValueModified: rulesController.setMaxFileSizeMb(value)
                    }
                    Label {
                        text: " MB（0=不限，超过此大小的文件跳过内容扫描）"
                        font.pixelSize: theme.fontSizeCaption
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                    Item { Layout.fillWidth: true }
                }

                // 开关项：扫描压缩包 / 内容缓存 / 性能日志
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16
                    CheckBox {
                        text: "扫描压缩包"
                        checked: rulesController.effectiveConfigPreview.scanArchives
                        onCheckedChanged: rulesController.setScanArchives(checked)
                    }
                    CheckBox {
                        text: "内容缓存"
                        checked: rulesController.effectiveConfigPreview.cacheEnabled
                        onCheckedChanged: rulesController.setCacheEnabled(checked)
                    }
                    CheckBox {
                        text: "性能日志"
                        checked: rulesController.effectiveConfigPreview.perfLogEnabled
                        onCheckedChanged: rulesController.setPerfLogEnabled(checked)
                    }
                    Item { Layout.fillWidth: true }
                }

                // 恢复默认按钮
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    IconButton {
                        iconSource: "qrc:/icons/rescan.svg"
                        text: "恢复默认"
                        tooltip: "恢复扫描参数为内置默认值"
                        accent: "ghost"
                        onClicked: rulesController.resetScanParams()
                    }
                }

                // 分隔线
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                }

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
                        // 字体列表懒加载：默认 model 为空，首次可见时才调
                        // Qt.fontFamilies()（Windows 数百字体，同步调用阻塞主线程）
                        displayText: configController.fontFamily
                            ? configController.fontFamily
                            : "平台默认"
                        onActivated: {
                            configController.setFontFamily(fontFamilyCombo.currentText)
                        }
                        // 监听 settingsPage.visible，首次可见时加载字体列表（仅一次）
                        // 使用 Qt 5.15+ 新语法 function onFoo()，消除
                        // "Implicitly defined onFoo properties in Connections are deprecated" 警告
                        Connections {
                            target: settingsPage
                            function onVisibleChanged() {
                                if (settingsPage.visible && !fontFamilyCombo._loaded) {
                                    fontFamilyCombo._loaded = true
                                    fontFamilyCombo.model = Qt.fontFamilies()
                                    if (configController.fontFamily) {
                                        var idx = fontFamilyCombo.find(configController.fontFamily)
                                        if (idx >= 0) fontFamilyCombo.currentIndex = idx
                                    }
                                }
                            }
                        }
                        property bool _loaded: false
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

                // ---------- 规则测试沙盒 ----------
                // 选择规则 + 输入文本 → 即时验证匹配（基于全局规则集 self._ruleset）
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                }

                Label {
                    text: "规则测试"
                    font.pixelSize: theme.fontSizeHeading
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Label {
                    text: "选择规则并输入文本，即时验证匹配结果。CONTENT 规则匹配输入文本，FILENAME/PATH 规则匹配文件名 test.txt。"
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }

                // 规则选择 + 测试按钮
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: "规则"
                        font.pixelSize: theme.fontSizeBody
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.preferredWidth: 80
                    }
                    ComboBox {
                        id: testRuleCombo
                        Layout.fillWidth: true
                        // ruleModel 暴露 name 角色，currentText 即选中规则名
                        model: rulesController.ruleModel
                        textRole: "name"
                    }
                    IconButton {
                        text: "测试匹配"
                        tooltip: "对输入文本执行选中规则的匹配测试"
                        accent: "primary"
                        enabled: testRuleCombo.count > 0
                        onClicked: {
                            var raw = rulesController.testRuleText(
                                testRuleCombo.currentText, testTextInput.text)
                            try {
                                settingsPage.testResult = JSON.parse(raw)
                            } catch (e) {
                                settingsPage.testResult = {"error": "结果解析失败"}
                            }
                        }
                    }
                }

                // 测试文本输入
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 80
                    clip: true
                    ScrollBar.vertical.policy: ScrollBar.AsNeeded
                    TextArea {
                        id: testTextInput
                        placeholderText: "输入测试文本..."
                        wrapMode: TextArea.Wrap
                        font.pixelSize: theme.fontSizeBody
                        background: Rectangle {
                            color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                            border.width: 1
                            radius: theme.radiusMd
                        }
                    }
                }

                // 结果摘要：命中次数 + 匹配目标
                Label {
                    Layout.fillWidth: true
                    visible: settingsPage.testResult !== null && !settingsPage.testResult.error
                    text: settingsPage.testResult && settingsPage.testResult.matched
                        ? "命中 " + settingsPage.testResult.matchCount + " 次（目标: " + settingsPage.testResult.target + "）"
                        : (settingsPage.testResult !== null ? "未命中" : "")
                    font.pixelSize: theme.fontSizeBody
                    color: settingsPage.testResult && settingsPage.testResult.matched
                        ? theme.colorSuccess
                        : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                }

                // 错误提示（规则未找到 / 规则集未加载 / 结果解析失败）
                Label {
                    Layout.fillWidth: true
                    visible: settingsPage.testResult !== null && !!settingsPage.testResult.error
                    text: settingsPage.testResult ? settingsPage.testResult.error : ""
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.colorDanger
                    wrapMode: Text.WordWrap
                }

                // 命中文本列表（绿色淡底高亮每个匹配片段）
                Repeater {
                    model: settingsPage.testResult && settingsPage.testResult.matches
                        ? settingsPage.testResult.matches : []
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: matchTextLbl.implicitHeight + 8
                        color: Qt.rgba(theme.colorSuccess.r,
                                       theme.colorSuccess.g,
                                       theme.colorSuccess.b,
                                       0.12)
                        radius: theme.radiusSm
                        Label {
                            id: matchTextLbl
                            anchors.fill: parent
                            anchors.margins: 4
                            text: modelData.text
                            font.pixelSize: theme.fontSizeCaption
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }

                // ---------- 规则编辑 ----------
                // 管理 user-scan.yaml 中的自定义规则：列表 + 新建/编辑/删除 + 内嵌表单
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                }

                Label {
                    text: "规则编辑"
                    font.pixelSize: theme.fontSizeHeading
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Label {
                    text: "管理 user-scan.yaml 中的自定义规则。仅叶子规则支持图形编辑，组合规则（AND/OR/NOT）请外部编辑 YAML。"
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }

                // 工具栏：新建规则 + 规则数
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    IconButton {
                        iconSource: "qrc:/icons/add.svg"
                        text: "新建规则"
                        tooltip: "在 user-scan.yaml 追加默认规则并打开编辑器"
                        accent: "primary"
                        compact: true
                        onClicked: {
                            var raw = rulesController.createRule()
                            try {
                                var r = JSON.parse(raw)
                                if (r.ok) {
                                    settingsPage.editingRule = settingsPage.lookupRuleByName(r.name)
                                    settingsPage.editMessage = ""
                                } else {
                                    settingsPage.editMessage = r.error || "新建失败"
                                }
                            } catch (e) {
                                settingsPage.editMessage = "新建失败"
                            }
                        }
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: "共 " + rulesController.userRulesModel.length + " 条"
                        font.pixelSize: theme.fontSizeCaption
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                }

                // 反馈消息（保存/删除/新建失败）
                Label {
                    Layout.fillWidth: true
                    visible: settingsPage.editMessage.length > 0
                    text: settingsPage.editMessage
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.colorDanger
                    wrapMode: Text.WordWrap
                }

                // 规则列表（user-scan.yaml 自身规则）
                Repeater {
                    model: rulesController.userRulesModel
                    delegate: Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 40
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusSm

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 6
                            spacing: 8

                            // 严重度色点
                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: modelData.severity === "critical" ? theme.colorDanger
                                    : (modelData.severity === "warning" ? theme.colorWarning : theme.colorPrimary)
                            }
                            Label {
                                text: modelData.name
                                font.pixelSize: theme.fontSizeBody
                                font.bold: true
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                Layout.preferredWidth: 160
                                elide: Text.ElideRight
                            }
                            Label {
                                text: modelData.isLeaf
                                    ? (settingsPage.targetText(modelData.target) + " · " + settingsPage.modeText(modelData.mode))
                                    : "组合规则（只读）"
                                font.pixelSize: theme.fontSizeCaption
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            // 编辑（仅叶子规则可编辑）
                            IconButton {
                                iconSource: "qrc:/icons/edit.svg"
                                tooltip: "编辑规则"
                                accent: "ghost"
                                compact: true
                                enabled: modelData.isLeaf
                                onClicked: {
                                    settingsPage.editingRule = modelData
                                    settingsPage.editMessage = ""
                                }
                            }
                            // 删除
                            IconButton {
                                iconSource: "qrc:/icons/delete.svg"
                                tooltip: "删除规则"
                                accent: "danger"
                                compact: true
                                onClicked: {
                                    var raw = rulesController.deleteRule(modelData.name)
                                    try {
                                        var r = JSON.parse(raw)
                                        if (r.ok) {
                                            if (settingsPage.editingRule
                                                && settingsPage.editingRule.name === modelData.name) {
                                                settingsPage.editingRule = null
                                            }
                                            settingsPage.editMessage = ""
                                        } else {
                                            settingsPage.editMessage = r.error || "删除失败"
                                        }
                                    } catch (e) {
                                        settingsPage.editMessage = "删除失败"
                                    }
                                }
                            }
                        }
                    }
                }

                // 编辑表单（选中规则后展开；新建后自动展开）
                RuleEditorForm {
                    Layout.fillWidth: true
                    visible: settingsPage.editingRule !== null
                    rulesController: rulesController
                    rule: settingsPage.editingRule
                    onSaveRequested: {
                        var raw = rulesController.updateRule(JSON.stringify(payload))
                        try {
                            var r = JSON.parse(raw)
                            if (r.ok) {
                                settingsPage.editingRule = null
                                settingsPage.editMessage = ""
                            } else {
                                settingsPage.editMessage = r.error || "保存失败"
                            }
                        } catch (e) {
                            settingsPage.editMessage = "保存失败"
                        }
                    }
                    onCancelRequested: {
                        settingsPage.editingRule = null
                        settingsPage.editMessage = ""
                    }
                }

                Item { Layout.fillHeight: true }
            }
        }
    }
}
