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

                Item { Layout.fillHeight: true }
            }
        }
    }
}
