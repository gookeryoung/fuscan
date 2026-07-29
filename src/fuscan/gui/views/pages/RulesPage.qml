import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 规则页（独立页，向后兼容）
// iter-137：规则配置全局化后，首页已内嵌 RulesPanel 提供完整编辑入口。
// 本页保留供 Sidebar 「规则」入口跳转，复用 RulesPanel 组件。
Item {
    id: rulesPage
    property ThemeController theme: Theme
    property RulesControllerType rulesController: RulesController

    // 信号：返回首页
    signal backRequested()

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // 顶部标题 + 返回按钮
        RowLayout {
            Layout.fillWidth: true
            IconButton {
                iconSource: "qrc:/icons/back.svg"
                text: "返回"
                tooltip: "返回首页"
                accent: "secondary"
                onClicked: rulesPage.backRequested()
            }
            Label {
                // iter-137：固定显示「全局规则」（不再有工作区绑定）
                text: "全局规则"
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
                Layout.leftMargin: 8
            }
        }

        // 主区域：复用 RulesPanel 组件
        RulesPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
        }
    }
}

