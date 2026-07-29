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
                iconSource: "qrc:/icons/back.svg"
                text: "返回"
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

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            // 状态色点：与状态文字同色
                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: {
                                    var s = String(workspaceController.currentScanController.statusText || "")
                                    if (s === "扫描中") return theme.colorWarning
                                    if (s === "已暂停") return theme.colorTextSecondary
                                    if (s === "已完成") return (workspaceController.currentScanController.matchedCount > 0
                                        ? theme.colorDanger : theme.colorSuccess)
                                    if (s.indexOf("取消") >= 0 || s === "失败") return theme.colorWarning
                                    return theme.colorPrimary
                                }
                            }
                            Label {
                                text: "当前状态："
                                font.pixelSize: 13
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }
                            Label {
                                text: workspaceController.currentScanController.statusText
                                font.pixelSize: 13
                                font.bold: true
                                color: {
                                    var s = String(workspaceController.currentScanController.statusText || "")
                                    if (s === "扫描中") return theme.colorWarning
                                    if (s === "已暂停") return theme.colorTextSecondary
                                    if (s === "已完成") return (workspaceController.currentScanController.matchedCount > 0
                                        ? theme.colorDanger : theme.colorSuccess)
                                    if (s.indexOf("取消") >= 0 || s === "失败") return theme.colorWarning
                                    return theme.colorPrimary
                                }
                            }
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

                // ---------- 收集阶段（walk）进度 ----------
                GroupBox {
                    Layout.fillWidth: true
                    title: "收集文件清单"
                    visible: workspaceController.currentScanController.scanPhase !== "setup"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8

                        // 阶段状态行
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: workspaceController.currentScanController.walkDone
                                    ? theme.colorSuccess
                                    : (workspaceController.currentScanController.scanPhase === "walk"
                                       ? theme.colorPrimary : theme.colorBorder)
                            }
                            Label {
                                text: workspaceController.currentScanController.walkDone
                                    ? "已完成"
                                    : (workspaceController.currentScanController.walkIndeterminate
                                       ? "统计中..." : "进行中")
                                font.pixelSize: 12
                                font.bold: true
                                color: workspaceController.currentScanController.walkDone
                                    ? theme.colorSuccess
                                    : (workspaceController.currentScanController.scanPhase === "walk"
                                       ? theme.colorPrimary
                                       : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: Math.round(workspaceController.currentScanController.walkProgress) + "%"
                                font.pixelSize: 13
                                font.bold: true
                                color: theme.isDark ? theme.colorPrimary : theme.colorPrimary
                            }
                        }

                        // 收集进度条
                        ProgressBar {
                            Layout.fillWidth: true
                            indeterminate: workspaceController.currentScanController.walkIndeterminate
                            from: 0
                            to: 100
                            value: workspaceController.currentScanController.walkProgress
                            background: Rectangle {
                                implicitHeight: 6
                                color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                radius: 3
                            }
                            contentItem: Item {
                                implicitHeight: 6
                                Rectangle {
                                    width: parent.visualPosition * parent.width
                                    height: parent.height
                                    radius: 3
                                    color: workspaceController.currentScanController.walkDone
                                        ? theme.colorSuccess : theme.colorPrimary
                                }
                            }
                        }

                        // 收集统计网格
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 4
                            columnSpacing: 12
                            rowSpacing: 4

                            Label {
                                text: "已发现"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }
                            Label {
                                text: "纳入扫描"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }
                            Label {
                                text: "类型不符跳过"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }
                            Label {
                                text: "用户标记跳过"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }
                            Label {
                                text: workspaceController.currentScanController.walkDiscovered
                                font.pixelSize: 14
                                font.bold: true
                                color: theme.isDark ? theme.colorPrimary : theme.colorPrimary
                            }
                            Label {
                                text: workspaceController.currentScanController.walkClassified
                                font.pixelSize: 14
                                font.bold: true
                                color: theme.colorSuccess
                            }
                            Label {
                                text: workspaceController.currentScanController.walkSkipped
                                font.pixelSize: 14
                                font.bold: true
                                color: theme.colorWarning
                            }
                            Label {
                                text: workspaceController.currentScanController.walkUserSkipped
                                font.pixelSize: 14
                                font.bold: true
                                color: theme.colorDanger
                            }
                        }
                    }
                }

                // ---------- 解析阶段（scan）进度 ----------
                GroupBox {
                    Layout.fillWidth: true
                    title: "解析文件内容"
                    visible: workspaceController.currentScanController.scanPhase !== "setup"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 8

                        // 阶段状态行
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Rectangle {
                                width: 8
                                height: 8
                                radius: 4
                                color: workspaceController.currentScanController.scanDone
                                    ? theme.colorSuccess
                                    : (workspaceController.currentScanController.scanPhase === "scan"
                                       || workspaceController.currentScanController.scanPhase === "archive"
                                       ? theme.colorWarning : theme.colorBorder)
                            }
                            Label {
                                text: workspaceController.currentScanController.scanDone
                                    ? "已完成"
                                    : (workspaceController.currentScanController.progressIndeterminate
                                       ? "等待中..." : "进行中")
                                font.pixelSize: 12
                                font.bold: true
                                color: workspaceController.currentScanController.scanDone
                                    ? theme.colorSuccess
                                    : ((workspaceController.currentScanController.scanPhase === "scan"
                                        || workspaceController.currentScanController.scanPhase === "archive")
                                       ? theme.colorWarning
                                       : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                            }
                            Item { Layout.fillWidth: true }
                            Label {
                                text: Math.round(workspaceController.currentScanController.progress) + "%"
                                font.pixelSize: 13
                                font.bold: true
                                color: theme.isDark ? theme.colorPrimary : theme.colorPrimary
                            }
                        }

                        // 解析进度条
                        ProgressBar {
                            Layout.fillWidth: true
                            indeterminate: workspaceController.currentScanController.progressIndeterminate
                            from: 0
                            to: 100
                            value: workspaceController.currentScanController.progress
                            background: Rectangle {
                                implicitHeight: 6
                                color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                radius: 3
                            }
                            contentItem: Item {
                                implicitHeight: 6
                                Rectangle {
                                    width: parent.visualPosition * parent.width
                                    height: parent.height
                                    radius: 3
                                    color: workspaceController.currentScanController.scanDone
                                        ? theme.colorSuccess : theme.colorWarning
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: "已扫描：" + workspaceController.currentScanController.progressScanned + " / " + workspaceController.currentScanController.progressTotal + " 个文件"
                                    + (workspaceController.currentScanController.archiveEntryCount > 0
                                       ? "（含压缩包内条目 " + workspaceController.currentScanController.archiveEntryCount + "）"
                                       : "")
                                font.pixelSize: 12
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.fillWidth: true
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
                        color: theme.isDark ? Qt.rgba(0.15, 0.62, 0.38, 0.15) : Qt.rgba(0.15, 0.62, 0.38, 0.08)
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
                                text: "安全"
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
                        color: theme.isDark ? Qt.rgba(0.91, 0.30, 0.24, 0.15) : Qt.rgba(0.91, 0.30, 0.24, 0.08)
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

                    // 错误
                    Rectangle {
                        Layout.fillWidth: true
                        height: 80
                        radius: theme.radiusMd
                        color: theme.isDark ? Qt.rgba(0.91, 0.30, 0.24, 0.15) : Qt.rgba(0.91, 0.30, 0.24, 0.08)
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
