import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

Pane {
    id: sidebar
    property ThemeController theme: Theme
    padding: 0

    // 侧栏背景：深色模式深蓝黑，浅色模式纯白
    background: Rectangle {
        color: theme.isDark ? theme.colorSidebarDark : theme.colorBgCard
        Behavior on color { ColorAnimation { duration: 200 } }
    }

    // 右侧 1px 分割线
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    }

    // ========== 当前选中页（供 ContentArea 读取） ==========
    property string currentPage: "scan"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: 0

        // ---------- Logo 区 ----------
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Layout.leftMargin: 20
            Layout.rightMargin: 16

            RowLayout {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10
                Rectangle {
                    width: 28; height: 28; radius: 6
                    color: theme.colorPrimary
                    Label {
                        anchors.centerIn: parent
                        text: "F"
                        color: theme.colorTextOnPrimary
                        font.pixelSize: 14
                        font.bold: true
                    }
                }
                Label {
                    text: "fuscan"
                    font.pixelSize: 15
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
            }
        }

        // ---------- 导航项列表 ----------
        NavItem {
            iconText: "📁"; label: "扫描"; pageId: "scan"
            selected: sidebar.currentPage === "scan"
            onClicked: { sidebar.currentPage = "scan" }
        }
        NavItem {
            iconText: "📋"; label: "规则"; pageId: "rules"
            selected: sidebar.currentPage === "rules"
            onClicked: { sidebar.currentPage = "rules" }
        }
        NavItem {
            iconText: "⚙"; label: "设置"; pageId: "settings"
            selected: sidebar.currentPage === "settings"
            onClicked: { sidebar.currentPage = "settings" }
        }
        NavItem {
            iconText: "ℹ"; label: "关于"; pageId: "about"
            selected: sidebar.currentPage === "about"
            onClicked: { sidebar.currentPage = "about" }
        }

        Item { Layout.fillHeight: true }  // 弹性撑开

        // ---------- 底部：暗色切换 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.bottomMargin: 16
            Layout.preferredHeight: 36
            radius: 8
            color: theme.isDark ? theme.colorBgHover : theme.colorBgApp
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 8
                Label {
                    text: "🌙"
                    font.pixelSize: 14
                }
                Label {
                    text: "暗色模式"
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    Layout.fillWidth: true
                }
                // 自定义开关
                Rectangle {
                    width: 36; height: 20; radius: 10
                    color: theme.isDark ? theme.colorPrimary : theme.colorBorder
                    Behavior on color { ColorAnimation { duration: 150 } }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: theme.setDark(!theme.isDark)
                    }
                    Rectangle {
                        width: 16; height: 16; radius: 8
                        color: "#FFFFFF"
                        x: theme.isDark ? 18 : 2
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on x { NumberAnimation { duration: 150 } }
                    }
                }
            }
        }
    }
}
