import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 统计页：展示当前工作区的扫描统计信息
Item {
    id: statsPage
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 通知 ContentArea 返回首页
    signal backRequested()

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // ---------- 标题区 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            IconButton {
                text:"← 返回"
                tooltip: "返回首页"
                accent: "secondary"
                onClicked: statsPage.backRequested()
            }
            Label {
                text: "统计信息"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
        }

        // ---------- 空态 ----------
        Label {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !workspaceController.hasCurrentWorkspace
            text: "未选择任务\n请从首页工作区卡片点击「统计」"
            font.pixelSize: 13
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        // ---------- 统计内容 ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            visible: workspaceController.hasCurrentWorkspace

            ColumnLayout {
                width: statsPage.width
                spacing: 16

                // ---------- 状态摘要 ----------
                GroupBox {
                    Layout.fillWidth: true
                    title: "状态摘要"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8

                        Label {
                            text: "当前状态：" + workspaceController.currentScanController.statusText
                            font.pixelSize: 13
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Label {
                            text: workspaceController.currentScanController.statusSummary || "暂无摘要"
                            font.pixelSize: 12
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---------- 进度信息 ----------
                GroupBox {
                    Layout.fillWidth: true
                    title: "进度"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8

                        // 进度条
                        ProgressBar {
                            Layout.fillWidth: true
                            from: 0
                            to: 100
                            value: workspaceController.currentScanController.progress
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "已扫描：" + workspaceController.currentScanController.progressScanned + " / " + workspaceController.currentScanController.progressTotal + " 个文件"
                                font.pixelSize: 12
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.fillWidth: true
                            }
                            Label {
                                text: Math.round(workspaceController.currentScanController.progress) + "%"
                                font.pixelSize: 13
                                font.bold: true
                                color: theme.isDark ? theme.colorPrimary : theme.colorPrimary
                            }
                        }
                    }
                }

                // ---------- 分类计数卡片 ----------
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 12
                    rowSpacing: 12

                    // 通过
                    Rectangle {
                        Layout.fillWidth: true
                        height: 80
                        radius: theme.radiusMd
                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                        border.color: theme.colorSuccess
                        border.width: 1
                        ColumnLayout {
                            anchors.centerIn: parent
                            Label {
                                text: workspaceController.currentScanController.passedCount
                                font.pixelSize: 28
                                font.bold: true
                                color: theme.colorSuccess
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                            Label {
                                text: "通过"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                        }
                    }

                    // 命中
                    Rectangle {
                        Layout.fillWidth: true
                        height: 80
                        radius: theme.radiusMd
                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                        border.color: theme.colorDanger
                        border.width: 1
                        ColumnLayout {
                            anchors.centerIn: parent
                            Label {
                                text: workspaceController.currentScanController.matchedCount
                                font.pixelSize: 28
                                font.bold: true
                                color: theme.colorDanger
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                            Label {
                                text: "命中"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                        }
                    }

                    // 跳过
                    Rectangle {
                        Layout.fillWidth: true
                        height: 80
                        radius: theme.radiusMd
                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                        border.color: theme.colorWarning
                        border.width: 1
                        ColumnLayout {
                            anchors.centerIn: parent
                            Label {
                                text: workspaceController.currentScanController.skippedCount
                                font.pixelSize: 28
                                font.bold: true
                                color: theme.colorWarning
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                            Label {
                                text: "跳过"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                        }
                    }

                    // 错误
                    Rectangle {
                        Layout.fillWidth: true
                        height: 80
                        radius: theme.radiusMd
                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                        border.color: theme.colorDanger
                        border.width: 1
                        ColumnLayout {
                            anchors.centerIn: parent
                            Label {
                                text: workspaceController.currentScanController.errorCount
                                font.pixelSize: 28
                                font.bold: true
                                color: theme.colorDanger
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                            Label {
                                text: "错误"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }
        }
    }
}
