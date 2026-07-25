import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Pane {
    id: sidebar
    padding: 0

    // 侧栏背景：深色模式深蓝黑，浅色模式纯白
    background: Rectangle {
        color: Theme.isDark ? Theme.colorSidebarDark : Theme.colorBgCard
        Behavior on color { ColorAnimation { duration: 200 } }
    }

    // 右侧 1px 分割线
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
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
                    color: Theme.colorPrimary
                    Label {
                        anchors.centerIn: parent
                        text: "F"
                        color: Theme.colorTextOnPrimary
                        font.pixelSize: 14
                        font.bold: true
                    }
                }
                Label {
                    text: "fuscan"
                    font.pixelSize: 15
                    font.bold: true
                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
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
            color: Theme.isDark ? Theme.colorBgHover : Theme.colorBgApp
            border.color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
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
                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                    Layout.fillWidth: true
                }
                // 自定义开关
                Rectangle {
                    width: 36; height: 20; radius: 10
                    color: Theme.isDark ? Theme.colorPrimary : Theme.colorBorder
                    Behavior on color { ColorAnimation { duration: 150 } }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: Theme.setDark(!Theme.isDark)
                    }
                    Rectangle {
                        width: 16; height: 16; radius: 8
                        color: "#FFFFFF"
                        x: Theme.isDark ? 18 : 2
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on x { NumberAnimation { duration: 150 } }
                    }
                }
            }
        }
    }
}
