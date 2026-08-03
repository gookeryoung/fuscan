import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 工作区卡片：单任务展示与操作。
// 对话框（切换目标/任务级设置/扫描历史）已外提到 HomePage 共享单例，
// 卡片只负责展示与发信号，避免 N 个工作区各持一份对话框对象（复用控件约束）。
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController

    // 由 ListView delegate 注入
    property string workspaceId: ""
    property string taskName: ""
    property string modeText: ""
    property string target: ""
    property var rulesTags: []
    property string statusText: ""
    property int matchedCount: 0
    property int passedCount: 0
    property int skippedCount: 0
    property int errorCount: 0
    property int collectedCount: 0
    property string lastSummary: ""

    // 是否展开（显示更多操作）
    property bool expanded: false

    // 信号：由 HomePage 接收并打开共享对话框
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)
    signal editTargetRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)
    signal viewHistoryRequested(string workspaceId)
    signal exportCsvRequested(string workspaceId)
    signal exportJsonRequested(string workspaceId)
    signal exportPdfRequested(string workspaceId)
    // 跳转到设置页规则 Tab 配置全局规则集（不依赖工作区状态，始终可用）
    signal configureRulesRequested(string workspaceId)

    implicitHeight: contentColumn.implicitHeight + 24
    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 状态色：根据 statusText 决定（与 StatsPage 统一判断逻辑与配色）
    function statusColor() {
        var s = String(statusText || "")
        if (s === "扫描中") return theme.colorWarning
        if (s === "已暂停") return theme.colorTextSecondary
        if (s === "已完成") return (matchedCount > 0 ? theme.colorDanger : theme.colorSuccess)
        // 用户取消/失败：黄色警示（非命中危险色，与 StatsPage 一致）
        if (s.indexOf("取消") >= 0 || s === "失败") return theme.colorWarning
        // 就绪：蓝色（非灰色），表示待命可操作
        return theme.colorPrimary
    }

    // 是否处于已完成态（含用户取消）：控制「查看结果」「扫描选项」按钮启用
    function isCompletedState() {
        var s = String(statusText || "")
        return s === "已完成" || s.indexOf("取消") >= 0
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

            // 任务图标：SVG rules + ColorOverlay 染色为前景色
            Item {
                width: 16
                height: 16
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
                Image {
                    id: taskIcon
                    anchors.fill: parent
                    source: "qrc:/icons/rules.svg"
                    sourceSize: Qt.size(16, 16)
                    visible: false
                }
                ColorOverlay {
                    anchors.fill: taskIcon
                    source: taskIcon
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
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
            // 收集到的符合文件类型文件数
            Label {
                text: "<b style='color:#0366D6'>纳入扫描 " + collectedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
                visible: collectedCount > 0
            }
            Label {
                text: "<b style='color:#28A745'>安全 " + passedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#DC3545'>命中 " + matchedCount + "</b>"
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

        // ---------- 第五行：主操作按钮（左主操作 + 右查看/统计/展开） ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // 左侧：启动/暂停/继续扫描（主操作，primary 色）
            IconButton {
                iconSource: statusText === "扫描中" ? "qrc:/icons/pause.svg" : "qrc:/icons/scan.svg"
                text: statusText === "扫描中" ? "暂停"
                    : (statusText === "已暂停" ? "继续" : "启动扫描")
                tooltip: statusText === "扫描中" ? "暂停扫描"
                    : (statusText === "已暂停" ? "继续扫描" : "启动扫描")
                accent: "primary"
                // 已完成态禁用（扫描操作收入展开区「扫描选项」菜单）
                enabled: !card.isCompletedState()
                onClicked: {
                    // 扫描中/已暂停：切换暂停；就绪态：启动扫描
                    if (statusText === "扫描中" || statusText === "已暂停") {
                        workspaceController.togglePause(card.workspaceId)
                    } else {
                        workspaceController.startScan(card.workspaceId)
                    }
                }
            }

            // 配置规则：跳转到设置页规则 Tab（secondary 描边，与启动扫描同高）
            // 工作区展示的规则 TAG 来自全局规则集，提供快捷入口便于修改
            IconButton {
                iconSource: "qrc:/icons/rules.svg"
                text: "配置规则"
                tooltip: "跳转到设置页配置全局规则集"
                accent: "secondary"
                onClicked: card.configureRulesRequested(card.workspaceId)
            }

            Item { Layout.fillWidth: true }

            // 右侧：查看结果（完成态 primary 激活）+ 统计 + 展开
            IconButton {
                iconSource: "qrc:/icons/search.svg"
                text: "查看结果"
                tooltip: "查看扫描结果"
                accent: "primary"
                // 已完成（含用户取消）的工作区可查看结果，未完成时 disabled 变灰
                enabled: card.isCompletedState()
                onClicked: card.viewResultsRequested(card.workspaceId)
            }
            IconButton {
                iconSource: "qrc:/icons/stats.svg"
                text: "统计"
                tooltip: "查看扫描统计"
                accent: "ghost"
                onClicked: card.viewStatsRequested(card.workspaceId)
            }
            // 展开按钮：与同行其他 IconButton 高度一致（40px），ghost 风格
            IconButton {
                iconSource: theme.iconsPrefix + "more.svg"
                text: card.expanded ? "收起" : "展开"
                tooltip: card.expanded ? "收起更多操作" : "展开更多操作"
                accent: "ghost"
                onClicked: card.expanded = !card.expanded
            }
        }

        // ---------- 展开后：更多操作（非常用功能） ----------
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

                // 切换目标（非常用，移入展开区）
                IconButton {
                    iconSource: "qrc:/icons/target.svg"
                    text: "切换目标"
                    tooltip: "修改该任务的扫描模式与目标路径"
                    accent: "ghost"
                    // 扫描中/暂停中禁用
                    enabled: statusText !== "扫描中" && statusText !== "已暂停"
                    onClicked: card.editTargetRequested(card.workspaceId)
                }
                // 扫描选项：增量扫描 / 全量重新扫描（合并菜单）
                IconButton {
                    iconSource: "qrc:/icons/rescan.svg"
                    text: "扫描选项"
                    tooltip: "增量扫描（仅变更文件）或全量重新扫描"
                    accent: "ghost"
                    // 已完成（含用户取消）的工作区可重新扫描
                    enabled: card.isCompletedState()
                    onClicked: scanOptionsMenu.open()
                    Menu {
                        id: scanOptionsMenu
                        MenuItem {
                            text: "增量扫描（仅变更文件）"
                            onTriggered: workspaceController.startIncrementalScan(card.workspaceId)
                        }
                        MenuItem {
                            text: "全量重新扫描"
                            onTriggered: workspaceController.startScan(card.workspaceId)
                        }
                    }
                }
                // 导出：CSV/JSON/PDF 合并为菜单
                IconButton {
                    iconSource: "qrc:/icons/export_csv.svg"
                    text: "导出"
                    tooltip: "导出扫描结果（选择格式）"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportFormatMenu.open()
                    Menu {
                        id: exportFormatMenu
                        MenuItem {
                            text: "CSV (*.csv)"
                            onTriggered: card.exportCsvRequested(card.workspaceId)
                        }
                        MenuItem {
                            text: "JSON (*.json)"
                            onTriggered: card.exportJsonRequested(card.workspaceId)
                        }
                        MenuItem {
                            text: "PDF (*.pdf)"
                            onTriggered: card.exportPdfRequested(card.workspaceId)
                        }
                    }
                }
                // 任务级设置
                IconButton {
                    iconSource: "qrc:/icons/settings.svg"
                    text: "设置"
                    tooltip: "任务级设置（仅对该任务生效）"
                    accent: "ghost"
                    onClicked: card.taskSettingsRequested(card.workspaceId)
                }
                // 扫描历史
                IconButton {
                    iconSource: "qrc:/icons/history.svg"
                    text: "历史"
                    tooltip: "查看扫描历史与对比摘要"
                    accent: "ghost"
                    onClicked: card.viewHistoryRequested(card.workspaceId)
                }
                Item { Layout.fillWidth: true }
                // 删除任务
                IconButton {
                    iconSource: "qrc:/icons/delete.svg"
                    text: "删除"
                    tooltip: "删除该任务"
                    accent: "danger"
                    onClicked: workspaceController.removeWorkspace(card.workspaceId)
                }
            }
        }
    }
}
