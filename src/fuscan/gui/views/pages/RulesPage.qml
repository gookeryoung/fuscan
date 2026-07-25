import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

Item {
    id: rulesPage
    property ThemeController theme: Theme
    property RulesControllerType rulesController: RulesController

    // 信号：返回首页/工作区
    signal backRequested()

    // 规则文件选择对话框（QML FileDialog，替代 QWidget QFileDialog）
    Dialogs.FileDialog {
        id: rulesFileDialog
        title: "选择规则文件"
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = rulesFileDialog.fileUrl.toString()
            // file:/// 前缀转本地路径
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.loadFileFromPath(pathStr)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // 顶部标题 + 返回按钮
        RowLayout {
            Layout.fillWidth: true
            IconButton {
                text:"← 返回"
                tooltip: "返回工作区"
                accent: "secondary"
                onClicked: rulesPage.backRequested()
            }
            Label {
                text: "规则"
                font.pixelSize: 22
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.leftMargin: 8
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "共 " + rulesController.ruleCount + " 条规则"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
        }

        // 主区域：左右分栏
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // ---------- 左侧：规则文件列表 ----------
            Rectangle {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: 8

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Label {
                        text: "规则文件"
                        font.pixelSize: 14
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    // 内置规则勾选
                    RowLayout {
                        Layout.fillWidth: true
                        CheckBox {
                            text: "内置通用规则"
                            checked: rulesController.useBuiltin
                            onCheckedChanged: rulesController.setUseBuiltin(checked)
                        }
                    }

                    // 规则文件列表
                    ListView {
                        id: rulesFileList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: rulesController.rulesFileModel
                        currentIndex: rulesController.selectedFileIndex
                        onCurrentIndexChanged: rulesController.setSelectedFileIndex(currentIndex)
                        delegate: ItemDelegate {
                            width: rulesFileList.width
                            height: 36
                            // QVariantList of dict 通过 modelData 访问字段
                            text: modelData.fileName
                            font.pixelSize: 12
                            highlighted: ListView.isCurrentItem
                            background: Rectangle {
                                color: ListView.isCurrentItem
                                    ? (theme.isDark ? theme.colorBgSelectedDark : theme.colorBgSelected)
                                    : (parent.hovered
                                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                        : "transparent")
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }
                            contentItem: Label {
                                text: parent.text
                                font: parent.font
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                elide: Text.ElideMiddle
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: 12
                                rightPadding: 12
                            }
                        }
                    }

                    // 操作按钮
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        IconButton {
                            text:"↑"
                            tooltip: "上移"
                            accent: "secondary"
                            enabled: rulesController.canMoveUp
                            onClicked: rulesController.moveUp()
                        }
                        IconButton {
                            text:"↓"
                            tooltip: "下移"
                            accent: "secondary"
                            enabled: rulesController.canMoveDown
                            onClicked: rulesController.moveDown()
                        }
                        IconButton {
                            text:"−"
                            tooltip: "移除选中规则文件"
                            accent: "secondary"
                            enabled: rulesController.canRemove
                            onClicked: rulesController.removeSelected()
                        }
                        Item { Layout.fillWidth: true }
                        IconButton {
                            text:"📂 加载"
                            tooltip: "加载规则文件"
                            accent: "secondary"
                            onClicked: rulesFileDialog.open()
                        }
                    }
                }
            }

            // ---------- 右侧：规则列表 ----------
            Rectangle {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: 8

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Label {
                        text: "规则列表"
                        font.pixelSize: 14
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    ListView {
                        id: ruleListView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: rulesController.ruleModel
                        delegate: ItemDelegate {
                            width: ruleListView.width
                            height: 56
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: model.name
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        radius: 8
                                        height: 18
                                        width: severityLabel.width + 12
                                        color: model.severityColor
                                        Label {
                                            id: severityLabel
                                            anchors.centerIn: parent
                                            text: model.severityText
                                            font.pixelSize: 10
                                            color: "#FFFFFF"
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: model.description
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    elide: Text.ElideRight
                                    visible: model.description.length > 0
                                }
                                // 底部分隔线（最后一项不显示）
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                    visible: index < ruleListView.count - 1
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
