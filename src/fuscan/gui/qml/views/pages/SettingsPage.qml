import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController

    ScrollView {
        anchors.fill: parent
        clip: true
        ColumnLayout {
            width: settingsPage.width
            spacing: 20

            // 标题
            Label {
                text: "设置"
                font.pixelSize: 22
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }

            // ---------- 扫描设置 ----------
            GroupBox {
                Layout.fillWidth: true
                title: "扫描设置"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "扫描压缩包"
                            Layout.fillWidth: true
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

            // ---------- 文件类型 ----------
            GroupBox {
                Layout.fillWidth: true
                title: "文件类型（勾选启用）"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            Layout.preferredHeight: theme.btnHeightGhost
                            text: "全选"
                            onClicked: configController.selectAllExtractors()
                        }
                        Button {
                            Layout.preferredHeight: theme.btnHeightGhost
                            text: "全不选"
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
                        Layout.preferredHeight: 200
                        clip: true
                        model: configController.extractorModel
                        delegate: ItemDelegate {
                            width: parent.width
                            height: 32
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                CheckBox {
                                    checked: model.enabled
                                    onCheckedChanged: configController.setExtractorEnabled(model.className, checked)
                                }
                                Label {
                                    text: model.displayName
                                    font.pixelSize: 12
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: model.speedTierText
                                    font.pixelSize: 11
                                    color: model.speedTierColor
                                }
                            }
                        }
                    }
                }
            }

            // ---------- 忽略目录 ----------
            GroupBox {
                Layout.fillWidth: true
                title: "忽略目录（一行一个）"
                TextArea {
                    anchors.fill: parent
                    text: configController.ignoreDirsText
                    onTextChanged: configController.setIgnoreDirsText(text)
                    font.pixelSize: 12
                    background: Rectangle {
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: 4
                    }
                }
            }

            // ---------- 缓存设置 ----------
            GroupBox {
                Layout.fillWidth: true
                title: "缓存设置"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "启用扫描结果缓存"
                            Layout.fillWidth: true
                        }
                        Switch {
                            checked: configController.cacheEnabled
                            onCheckedChanged: configController.setCacheEnabled(checked)
                        }
                    }
                }
            }

            // ---------- 性能 ----------
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
                        }
                        Switch {
                            checked: configController.perfLogEnabled
                            onCheckedChanged: configController.setPerfLogEnabled(checked)
                        }
                    }
                }
            }

            // ---------- 路径历史 ----------
            GroupBox {
                Layout.fillWidth: true
                title: "扫描路径历史"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8
                    ListView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 120
                        clip: true
                        model: configController.scanPaths
                        delegate: ItemDelegate {
                            width: parent.width
                            height: 28
                            Label {
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.left: parent.left
                                anchors.leftMargin: 8
                                text: modelData
                                font.pixelSize: 12
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                elide: Text.ElideMiddle
                            }
                        }
                    }
                    Button {
                        Layout.preferredHeight: theme.btnHeightGhost
                        text: "清除历史"
                        onClicked: configController.clearScanPaths()
                    }
                }
            }
        }
    }
}
