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

    // 通知 ContentArea 返回文件扫描页
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
                tooltip: "返回文件扫描页"
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
            text: "未选择任务\n请从文件扫描页工作区卡片点击「统计」"
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
                    title: "收集文件"
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
                            // 收集用时（后端 walkElapsedText 已格式化，空串表示未开始）
                            Label {
                                visible: !!workspaceController.currentScanController.walkElapsedText
                                text: "用时 " + workspaceController.currentScanController.walkElapsedText
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
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
                            id: walkBar
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
                            // contentItem 内的 parent 是此 Item 自身，visualPosition 是
                            // ProgressBar 的属性，须通过 id（walkBar）引用，否则
                            // parent.visualPosition 为 undefined，填充宽度为 NaN（条为空）。
                            contentItem: Item {
                                implicitHeight: 6
                                Rectangle {
                                    width: walkBar.visualPosition * parent.width
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
                    title: "解析文件"
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
                            // 解析用时（后端 scanElapsedText 已格式化，空串表示未进入解析阶段）
                            Label {
                                visible: !!workspaceController.currentScanController.scanElapsedText
                                text: "用时 " + workspaceController.currentScanController.scanElapsedText
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
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
                            id: scanBar
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
                            // 同收集进度条：visualPosition 须通过 id（scanBar）引用，
                            // 不能用 parent.visualPosition（parent 为内层 Item，无此属性）。
                            contentItem: Item {
                                implicitHeight: 6
                                Rectangle {
                                    width: scanBar.visualPosition * parent.width
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
                    columns: 3
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

                // ---------- 命中分布图表 ----------
                // 仅在扫描完成且有命中时展示，避免空态占用版面
                GroupBox {
                    Layout.fillWidth: true
                    title: "命中分布"
                    visible: workspaceController.currentScanController.scanDone
                             && workspaceController.currentScanController.matchedCount > 0

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 16

                        // 上排：严重度分布 + 扩展名分布 并排
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 16
                            rowSpacing: 12

                            // 严重度分布饼图
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    text: "严重度分布"
                                    font.pixelSize: theme.fontSizeCaption
                                    color: theme.colorTextSecondary
                                }
                                PieChart {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 220
                                    chartData: workspaceController.currentScanController.severityChartData
                                    centerTitle: "命中文件"
                                }
                            }

                            // 扩展名分布饼图
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    text: "扩展名分布"
                                    font.pixelSize: theme.fontSizeCaption
                                    color: theme.colorTextSecondary
                                }
                                PieChart {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 220
                                    chartData: workspaceController.currentScanController.extensionChartData
                                    centerTitle: "命中文件"
                                }
                            }
                        }

                        // 分隔线
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        }

                        // 下排：Top 规则条形图（全宽）
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Label {
                                text: "命中数 Top 10 规则"
                                font.pixelSize: theme.fontSizeCaption
                                color: theme.colorTextSecondary
                            }
                            BarChart {
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.max(120, chartData.length * 34)
                                chartData: workspaceController.currentScanController.topRulesChartData
                                labelWidth: 160
                            }
                        }
                    }
                }
            }
        }
    }
}
