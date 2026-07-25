import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: rulesPage

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // 顶部标题
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "规则"
                font.pixelSize: 22
                font.bold: true
                color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "共 " + RulesController.ruleCount + " 条规则"
                font.pixelSize: 12
                color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
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
                color: Theme.isDark ? Theme.colorBgCard : Theme.colorBgCard
                border.color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
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
                        color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                    }

                    // 内置规则勾选
                    RowLayout {
                        Layout.fillWidth: true
                        CheckBox {
                            text: "内置通用规则"
                            checked: RulesController.useBuiltin
                            onCheckedChanged: RulesController.setUseBuiltin(checked)
                        }
                    }

                    // 规则文件列表
                    ListView {
                        id: rulesFileList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: RulesController.rulesFileModel
                        delegate: ItemDelegate {
                            width: rulesFileList.width
                            height: 36
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                Label {
                                    text: model.fileName
                                    font.pixelSize: 12
                                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                                    elide: Text.ElideMiddle
                                }
                            }
                        }
                    }

                    // 操作按钮
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        Button {
                            Layout.preferredHeight: Theme.btnHeightGhost
                            text: "↑"
                            enabled: RulesController.canMoveUp
                            onClicked: RulesController.moveUp()
                        }
                        Button {
                            Layout.preferredHeight: Theme.btnHeightGhost
                            text: "↓"
                            enabled: RulesController.canMoveDown
                            onClicked: RulesController.moveDown()
                        }
                        Button {
                            Layout.preferredHeight: Theme.btnHeightGhost
                            text: "−"
                            enabled: RulesController.canRemove
                            onClicked: RulesController.removeSelected()
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            Layout.preferredHeight: Theme.btnHeightSecondary
                            text: "加载..."
                            onClicked: RulesController.loadFile()
                        }
                    }
                }
            }

            // ---------- 右侧：规则列表 ----------
            Rectangle {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                color: Theme.isDark ? Theme.colorBgCard : Theme.colorBgCard
                border.color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
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
                        color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                    }

                    ListView {
                        id: ruleListView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: RulesController.ruleModel
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
                                        color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
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
                                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                                    elide: Text.ElideRight
                                    visible: model.description.length > 0
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
