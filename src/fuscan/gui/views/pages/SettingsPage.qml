import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 设置页：仅字体设置。
// 规则配置（规则文件/白名单/生效预览）已迁移到首页任务卡片的「配置规则」
// 与「预览规则」对话框，设置页不再承载规则管理 UI。
Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController
    property WhitelistControllerType whitelistController: WhitelistController
    property RulesControllerType rulesController: RulesController
    property WorkspaceControllerType workspaceController: WorkspaceController

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
                        Connections {
                            target: settingsPage
                            onVisibleChanged: {
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

                Item { Layout.fillHeight: true }
            }
        }
    }
}
