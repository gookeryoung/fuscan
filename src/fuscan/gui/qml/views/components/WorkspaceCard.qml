import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 工作区卡片：单任务展示与操作
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 由 ListView delegate 注入
    property string workspaceId: ""
    property string taskName: ""
    property string modeText: ""
    property string target: ""
    property string rulesText: ""
    property string statusText: ""
    property int matchedCount: 0
    property int passedCount: 0
    property int skippedCount: 0
    property int errorCount: 0
    property string lastSummary: ""

    // 是否展开（显示更多操作）
    property bool expanded: false

    // 信号：通知 HomePage 切换到规则页 / 结果页 / 统计页
    signal defineRulesRequested(string workspaceId)
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)

    implicitHeight: contentColumn.implicitHeight + 24
    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 状态色：根据 statusText 决定
    function statusColor() {
        if (statusText === "扫描中") return theme.colorSuccess
        if (statusText === "已暂停") return theme.colorWarning
        if (statusText === "已完成") return (matchedCount > 0 ? theme.colorDanger : theme.colorSuccess)
        if (statusText === "失败" || statusText === "已取消") return theme.colorWarning
        return theme.colorBorder
    }

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        // ---------- 第一行：任务名 + 状态徽标 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Label {
                text: "📋"
                font.pixelSize: 16
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
                height: 22
                width: statusTextLabel.width + 18
                color: card.statusColor()
                Label {
                    id: statusTextLabel
                    anchors.centerIn: parent
                    text: statusText
                    font.pixelSize: 11
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
            Label {
                text: "规则"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: rulesText
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
        }

        // ---------- 第三行：最近摘要 ----------
        Label {
            Layout.fillWidth: true
            text: lastSummary ? "最近：" + lastSummary : "尚未扫描"
            font.pixelSize: 11
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            elide: Text.ElideRight
            visible: lastSummary.length > 0 || true
        }

        // ---------- 第四行：分类计数 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Label {
                text: "<b style='color:#28A745'>通过 " + passedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#DC3545'>命中 " + matchedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#FFC107'>跳过 " + skippedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#DC3545'>错误 " + errorCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Item { Layout.fillWidth: true }
        }

        // ---------- 第五行：操作按钮（左侧主要 + 右侧次要 + 展开） ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // 左侧：定义规则 + 启动/暂停
            Button {
                Layout.preferredHeight: theme.btnHeightSecondary
                text: "定义规则"
                onClicked: card.defineRulesRequested(card.workspaceId)
                background: Rectangle {
                    color: parent.down
                          ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                          : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusSecondary
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            Button {
                Layout.preferredHeight: theme.btnHeightPrimary
                text: statusText === "扫描中" ? "暂停扫描" : "启动扫描"
                enabled: statusText !== "扫描中" || true
                onClicked: {
                    if (statusText === "扫描中" || statusText === "已暂停") {
                        workspaceController.togglePause(card.workspaceId)
                    } else {
                        workspaceController.startScan(card.workspaceId)
                    }
                }
                background: Rectangle {
                    color: parent.enabled
                          ? (parent.down ? theme.colorPrimaryDark : theme.colorPrimary)
                          : theme.colorBorder
                    radius: theme.btnRadiusPrimary
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.colorTextOnPrimary
                    font.pixelSize: 12
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Item { Layout.fillWidth: true }

            // 右侧：查看结果 + 统计 + 展开按钮
            Button {
                Layout.preferredHeight: theme.btnHeightGhost
                text: "查看结果"
                enabled: matchedCount > 0
                onClicked: card.viewResultsRequested(card.workspaceId)
                background: Rectangle {
                    color: parent.down
                          ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                          : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusGhost
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            Button {
                Layout.preferredHeight: theme.btnHeightGhost
                text: "统计"
                onClicked: card.viewStatsRequested(card.workspaceId)
                background: Rectangle {
                    color: parent.down
                          ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                          : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusGhost
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            // 展开按钮（切换更多操作）
            Button {
                Layout.preferredHeight: theme.btnHeightGhost
                Layout.preferredWidth: 32
                text: card.expanded ? "▲" : "▼"
                onClicked: card.expanded = !card.expanded
                background: Rectangle {
                    color: parent.down
                          ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                          : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusGhost
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }

        // ---------- 展开后：更多操作 ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: card.expanded

            // 分隔线
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "导出 CSV"
                    enabled: matchedCount > 0
                    onClicked: exportCsvRequested(card.workspaceId)
                    background: Rectangle {
                        color: parent.down
                              ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                              : "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusGhost
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "导出 JSON"
                    enabled: matchedCount > 0
                    onClicked: exportJsonRequested(card.workspaceId)
                    background: Rectangle {
                        color: parent.down
                              ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                              : "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusGhost
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "任务设置"
                    onClicked: taskSettingsRequested(card.workspaceId)
                    background: Rectangle {
                        color: parent.down
                              ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                              : "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusGhost
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                Item { Layout.fillWidth: true }
                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "删除任务"
                    onClicked: workspaceController.removeWorkspace(card.workspaceId)
                    background: Rectangle {
                        color: parent.down ? theme.colorDanger : "transparent"
                        border.color: theme.colorDanger
                        border.width: 1
                        radius: theme.btnRadiusGhost
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Label {
                        text: parent.text
                        color: parent.parent.down ? theme.colorTextOnPrimary : theme.colorDanger
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }

    // 信号：导出 / 任务设置
    signal exportCsvRequested(string workspaceId)
    signal exportJsonRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)
}
