import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 扫描进度卡片：扫描中（含暂停态）显示，承载实时进度与控制按钮。
// HomePage 在 hasActiveScan=true 时用此卡片替换工作区列表，
// 隐藏其余工作区以聚焦当前扫描任务（用户需求：扫描结束才显示其余工作区）。
//
// 注意：ScanController 一律通过 workspaceController.activeScanController.xxx 链式访问，
// 不绑定到本地 property。PySide2 5.15 中将 @Property(ScanController) 返回的 QObject
// 绑定到本地 property var/ScanControllerType 时类型推断失败会识别为 null（iter-101），
// 链式访问每次 binding 求值都重新读取 Property，与 StatsPage 稳定模式一致。
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 由 HomePage 注入：扫描中的工作区 ID 与展示字段
    property string workspaceId: ""
    property string taskName: ""
    property string modeText: ""
    property string target: ""

    implicitHeight: contentColumn.implicitHeight + 32
    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 状态色：扫描中=warning（黄），已暂停=text secondary
    function statusColor() {
        if (workspaceController.activeScanController.isPaused) return theme.colorTextSecondary
        return theme.colorWarning
    }

    function statusText() {
        if (workspaceController.activeScanController.isPaused) return "已暂停"
        return "扫描中"
    }

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        // ---------- 第一行：任务名 + 状态徽标 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Label {
                text: "📋"
                font.pixelSize: 18
            }
            Label {
                text: taskName
                font.pixelSize: theme.fontSizeHeading
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            // 状态徽标
            Rectangle {
                radius: 10
                height: 24
                width: statusBadgeLabel.width + 18
                color: card.statusColor()
                Label {
                    id: statusBadgeLabel
                    anchors.centerIn: parent
                    text: card.statusText()
                    font.pixelSize: 11
                    font.bold: true
                    color: theme.colorTextOnPrimary
                }
            }
        }

        // ---------- 第二行：任务元数据 ----------
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 16
            rowSpacing: 4

            Label {
                text: "模式"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: modeText
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Label {
                text: "目标"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: target || "—"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideMiddle
            }
        }

        // ---------- 第三行：当前文件 ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Label {
                text: "当前文件"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                Layout.fillWidth: true
                text: workspaceController.activeScanController.currentFile || "—"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                elide: Text.ElideMiddle
                // 暂停态淡化提示
                opacity: workspaceController.activeScanController.isPaused ? 0.6 : 1.0
            }
        }

        // ---------- 第四行：进度条 ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "进度"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: workspaceController.activeScanController.progressIndeterminate
                        ? "统计中..."
                        : (workspaceController.activeScanController.progressScanned + " / " + workspaceController.activeScanController.progressTotal)
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
            }
            ProgressBar {
                id: progressBar
                Layout.fillWidth: true
                // 不确定模式（统计阶段）走 ProgressBar 默认动画
                indeterminate: workspaceController.activeScanController.progressIndeterminate
                from: 0.0
                to: Math.max(workspaceController.activeScanController.progressTotal, 1)
                value: workspaceController.activeScanController.progressScanned
                background: Rectangle {
                    implicitHeight: 6
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    radius: 3
                }
                contentItem: Item {
                    implicitHeight: 6
                    Rectangle {
                        width: progressBar.visualPosition * parent.width
                        height: parent.height
                        radius: 3
                        color: card.statusColor()
                    }
                }
            }
        }

        // ---------- 第五行：分类计数（PlainText + color，避免 RichText 解析开销） ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Label {
                text: "通过 " + workspaceController.activeScanController.passedCount
                color: theme.colorSuccess
                font.bold: true
                font.pixelSize: 12
            }
            Label {
                text: "命中 " + workspaceController.activeScanController.matchedCount
                color: theme.colorDanger
                font.bold: true
                font.pixelSize: 12
            }
            Label {
                text: "跳过 " + workspaceController.activeScanController.skippedCount
                color: theme.colorWarning
                font.bold: true
                font.pixelSize: 12
            }
            Label {
                text: "错误 " + workspaceController.activeScanController.errorCount
                color: theme.colorDanger
                font.bold: true
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
        }

        // ---------- 第六行：状态摘要 + 控制按钮 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                Layout.fillWidth: true
                text: workspaceController.activeScanController.statusSummary
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                elide: Text.ElideRight
            }

            // 暂停/继续按钮：扫描中显示「暂停」，已暂停显示「继续」
            IconButton {
                text: workspaceController.activeScanController.isPaused ? "▶ 继续" : "⏸ 暂停"
                tooltip: workspaceController.activeScanController.isPaused ? "继续扫描" : "暂停扫描"
                accent: "secondary"
                onClicked: workspaceController.togglePause(card.workspaceId)
            }
            // 取消按钮：危险操作
            IconButton {
                text: "⏹ 取消"
                tooltip: "取消扫描"
                accent: "danger"
                onClicked: workspaceController.cancelScan(card.workspaceId)
            }
        }
    }
}
