import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 设置页：TabBar 分组切换，避免单页滚动过长
Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController

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
                text:"↺ 重置"
                tooltip: "重置扫描相关配置为默认值"
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
                model: ["扫描", "忽略目录"]
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

            // ===== Tab 1: 扫描 =====
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsStack.width
                    spacing: 16

                    GroupBox {
                        Layout.fillWidth: true
                        title: "扫描参数"
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
                                Switch {
                                    checked: configController.scanArchives
                                    onCheckedChanged: configController.setScanArchives(checked)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大工作线程"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                SpinBox {
                                    from: 1
                                    to: 16
                                    value: configController.maxWorkers
                                    onValueChanged: configController.setMaxWorkers(value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大文件大小（MB）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                SpinBox {
                                    from: 1
                                    to: 500
                                    value: configController.maxFileSizeMB
                                    onValueChanged: configController.setMaxFileSizeMB(value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大扫描深度（0=无限）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                SpinBox {
                                    from: 0
                                    to: 50
                                    value: configController.maxDepth
                                    onValueChanged: configController.setMaxDepth(value)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "缓存"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用扫描结果缓存"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Switch {
                                    checked: configController.cacheEnabled
                                    onCheckedChanged: configController.setCacheEnabled(checked)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "性能"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用性能详细日志"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Switch {
                                    checked: configController.perfLogEnabled
                                    onCheckedChanged: configController.setPerfLogEnabled(checked)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "文件类型"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                IconButton {
                                    text:"☑ 全选"
                                    tooltip: "全部启用"
                                    accent: "secondary"
                                    onClicked: configController.selectAllExtractors()
                                }
                                IconButton {
                                    text:"☐ 全不选"
                                    tooltip: "全部禁用"
                                    accent: "secondary"
                                    onClicked: configController.unselectAllExtractors()
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: configController.extractorCountText
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 320
                                clip: true
                                interactive: true
                                model: configController.extractorModel
                                // 按 category 角色分组，配合 section.delegate 渲染类别头部
                                section.property: "category"
                                section.criteria: ViewSection.FullString
                                section.delegate: Rectangle {
                                    width: ListView.view.width
                                    height: 36
                                    color: theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 8
                                        anchors.rightMargin: 8
                                        spacing: 8

                                        // 类别头部三态 CheckBox：全选/全不选该类别
                                        CheckBox {
                                            id: catCheckBox
                                            tristate: true
                                            checkState: Qt.Unchecked

                                            Component.onCompleted: catCheckBox.updateState()

                                            Connections {
                                                target: configController.extractorModel
                                                onCategoryStatesChanged: catCheckBox.updateState()
                                            }

                                            function updateState() {
                                                var s = configController.extractorModel.categoryStates[section]
                                                if (s === "all") checkState = Qt.Checked
                                                else if (s === "none") checkState = Qt.Unchecked
                                                else checkState = Qt.PartiallyChecked
                                            }

                                            // 点击后：当前为 all → 全不选；其他 → 全选
                                            onToggled: {
                                                var s = configController.extractorModel.categoryStates[section]
                                                configController.extractorModel.setCategoryEnabled(section, s !== "all")
                                            }
                                        }

                                        Label {
                                            text: section
                                            font.bold: true
                                            font.pixelSize: theme.fontSizeBody
                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        }

                                        Item { Layout.fillWidth: true }

                                        Label {
                                            text: {
                                                var s = configController.extractorModel.categoryStates[section]
                                                if (s === "all") return "全部启用"
                                                if (s === "none") return "全部禁用"
                                                return "部分启用"
                                            }
                                            font.pixelSize: 11
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                }
                                delegate: ItemDelegate {
                                    id: extractorDelegate
                                    width: ListView.view.width
                                    height: 32
                                    // 速度档次 1-5，T1 最快（5 格满），T5 最慢（1 格）
                                    property int tier: parseInt(model.speedTierText.charAt(1))
                                    property color speedColor: model.speedTierColor
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 32
                                        CheckBox {
                                            checked: model.enabled
                                            onCheckedChanged: configController.setExtractorEnabled(model.className, checked)
                                        }
                                        Label {
                                            text: model.displayName
                                            font.pixelSize: 12
                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        }
                                        // 蓝色格式 tag：显示 formatLabel（如 DOCX/PDF/XLSX）
                                        Rectangle {
                                            radius: theme.radiusSm
                                            color: theme.colorPrimary
                                            Layout.leftMargin: 6
                                            implicitWidth: formatTagLabel.implicitWidth + 12
                                            implicitHeight: formatTagLabel.implicitHeight + 4
                                            Label {
                                                id: formatTagLabel
                                                anchors.centerIn: parent
                                                text: model.formatLabel
                                                font.pixelSize: 10
                                                font.bold: true
                                                color: theme.colorTextOnPrimary
                                            }
                                        }
                                        Item { Layout.fillWidth: true }
                                        // 「解析速度」文字标签 + 五格指示器（含 ToolTip）
                                        Label {
                                            text: "解析速度"
                                            font.pixelSize: 10
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                            Layout.rightMargin: 4
                                        }
                                        // Item 包裹 Row + MouseArea，避免 Row 内子项使用 anchors
                                        Item {
                                            id: speedIndicator
                                            Layout.rightMargin: 8
                                            width: speedRow.width
                                            height: speedRow.height
                                            // ToolTip：hover 时显示完整速度档次（如「T2 快速」）
                                            ToolTip.visible: speedIndicatorMouseArea.containsMouse
                                            ToolTip.delay: 300
                                            ToolTip.text: "解析速度：" + model.speedTierText
                                            Row {
                                                id: speedRow
                                                spacing: 2
                                                Repeater {
                                                    model: 5
                                                    Rectangle {
                                                        width: 6
                                                        height: 12
                                                        radius: 1
                                                        color: index < (6 - extractorDelegate.tier)
                                                            ? extractorDelegate.speedColor
                                                            : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                                                    }
                                                }
                                            }
                                            MouseArea {
                                                id: speedIndicatorMouseArea
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.WhatsThisCursor
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ===== Tab 2: 忽略目录 =====
            ColumnLayout {
                spacing: 8
                Label {
                    text: "一行一个目录名（扫描时跳过匹配目录）"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                TextArea {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    text: configController.ignoreDirsText
                    onTextChanged: configController.setIgnoreDirsText(text)
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    wrapMode: TextArea.Wrap
                    background: Rectangle {
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: 4
                    }
                }
            }
        }
    }
}
