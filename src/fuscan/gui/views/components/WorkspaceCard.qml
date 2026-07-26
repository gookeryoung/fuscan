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
    property var rulesTags: []
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
        if (statusText === "扫描中") return theme.colorWarning
        if (statusText === "已暂停") return theme.colorTextSecondary
        if (statusText === "已完成") return (matchedCount > 0 ? theme.colorDanger : theme.colorSuccess)
        if (statusText === "失败" || statusText === "已取消") return theme.colorWarning
        // 就绪：蓝色（非灰色），表示待命可操作
        return theme.colorPrimary
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
            // 规则 TAG 标签列表：内置=灰色，用户定义=绿色，从左到右排列
            RowLayout {
                Layout.fillWidth: true
                spacing: 4
                Repeater {
                    model: rulesTags
                    delegate: Rectangle {
                        radius: 4
                        height: 18
                        width: tagLabel.width + 12
                        // 内置=灰色背景，用户定义=绿色背景
                        color: modelData.is_builtin
                            ? (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                            : theme.colorSuccess
                        Label {
                            id: tagLabel
                            anchors.centerIn: parent
                            text: modelData.name
                            font.pixelSize: 10
                            font.bold: true
                            color: modelData.is_builtin
                                ? (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                                : theme.colorTextOnPrimary
                        }
                    }
                }
                // 空态：未配置规则
                Label {
                    visible: rulesTags.length === 0
                    text: "未配置规则"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
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

            // 左侧：定义规则 + 启动/暂停扫描 + 查看结果
            IconButton {
                text:"🔧 定义规则"
                tooltip: "编辑该任务的规则集"
                accent: "secondary"
                onClicked: card.defineRulesRequested(card.workspaceId)
            }
            IconButton {
                text: statusText === "扫描中" ? "⏸ 暂停" : "▶ 启动扫描"
                tooltip: statusText === "扫描中" ? "暂停扫描" : "启动扫描"
                accent: "primary"
                // 扫描完成后切换为未激活，避免已完成任务仍高亮扫描按钮
                enabled: statusText !== "已完成"
                onClicked: {
                    if (statusText === "扫描中" || statusText === "已暂停") {
                        workspaceController.togglePause(card.workspaceId)
                    } else {
                        workspaceController.startScan(card.workspaceId)
                    }
                }
            }
            IconButton {
                text:"🔄 更新扫描"
                tooltip: "对已完成扫描的任务重新扫描"
                accent: "secondary"
                // 仅已完成扫描的工作区可用，其余状态禁用
                enabled: statusText === "已完成"
                onClicked: workspaceController.startScan(card.workspaceId)
            }
            IconButton {
                text:"📊 查看结果"
                tooltip: "查看扫描结果"
                // 扫描完成后高亮（与扫描前的扫描按钮同色），未完成时 disabled 变灰
                accent: "primary"
                // 扫描完成前未激活，扫描完成后激活
                enabled: statusText === "已完成"
                onClicked: card.viewResultsRequested(card.workspaceId)
            }

            Item { Layout.fillWidth: true }

            // 右侧：统计 + 展开按钮（保留）
            IconButton {
                text:"📈 统计"
                tooltip: "查看扫描统计"
                accent: "ghost"
                onClicked: card.viewStatsRequested(card.workspaceId)
            }
            // 展开按钮：more.svg icon + 「展开/收起」文字
            Button {
                id: expandBtn
                Layout.preferredHeight: theme.btnHeightGhost
                leftPadding: 10
                rightPadding: 10
                topPadding: 0
                bottomPadding: 0
                onClicked: card.expanded = !card.expanded
                ToolTip.visible: hovered
                ToolTip.text: card.expanded ? "收起更多操作" : "展开更多操作"
                ToolTip.delay: 400
                background: Rectangle {
                    color: expandBtn.down || expandBtn.hovered
                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                        : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusGhost
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
                contentItem: Row {
                    spacing: 4
                    Image {
                        source: "file:///" + theme.iconsDir + "/more.svg"
                        sourceSize: Qt.size(14, 14)
                        anchors.verticalCenter: parent.verticalCenter
                        // SVG 颜色跟随主题（Qt 5.15 ColorOverlay 不可用，用 opacity 区分）
                        opacity: theme.isDark ? 0.9 : 1.0
                    }
                    Label {
                        text: card.expanded ? "收起" : "展开"
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
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

                IconButton {
                    text:"📄 CSV"
                    tooltip: "导出为 CSV"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportCsvRequested(card.workspaceId)
                }
                IconButton {
                    text:"📦 JSON"
                    tooltip: "导出为 JSON"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportJsonRequested(card.workspaceId)
                }
                IconButton {
                    text:"⚙ 设置"
                    tooltip: "任务级设置"
                    accent: "ghost"
                    onClicked: taskSettingsRequested(card.workspaceId)
                }
                Item { Layout.fillWidth: true }
                IconButton {
                    text:"🗑 删除"
                    tooltip: "删除该任务"
                    accent: "danger"
                    onClicked: workspaceController.removeWorkspace(card.workspaceId)
                }
            }
        }
    }

    // 信号：导出 / 任务设置
    signal exportCsvRequested(string workspaceId)
    signal exportJsonRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)
}
