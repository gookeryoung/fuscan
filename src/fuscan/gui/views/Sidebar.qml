import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
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
    property string currentPage: "home"

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

        // ---------- 顶部主导航 ----------
        NavItem {
            iconSource: "qrc:/icons/home.svg"; label: "首页"; pageId: "home"
            selected: sidebar.currentPage === "home"
            onClicked: { sidebar.currentPage = "home" }
        }
        NavItem {
            iconSource: "qrc:/icons/add.svg"; label: "添加任务"; pageId: "addTask"
            selected: sidebar.currentPage === "addTask"
            onClicked: { sidebar.currentPage = "addTask" }
        }

        Item { Layout.fillHeight: true }  // 弹性撑开

        // ---------- 底部辅助导航 ----------
        NavItem {
            iconSource: "qrc:/icons/settings.svg"; label: "设置"; pageId: "settings"
            selected: sidebar.currentPage === "settings"
            onClicked: { sidebar.currentPage = "settings" }
        }
        NavItem {
            iconSource: "qrc:/icons/info.svg"; label: "关于"; pageId: "about"
            selected: sidebar.currentPage === "about"
            onClicked: { sidebar.currentPage = "about" }
        }

        // ---------- 暗色切换 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            Layout.rightMargin: 14
            Layout.topMargin: 8
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
                // 暗色模式图标：SVG moon + ColorOverlay 染色为次要文本色
                Item {
                    width: 14
                    height: 14
                    Layout.preferredWidth: 14
                    Layout.preferredHeight: 14
                    Image {
                        id: moonIcon
                        anchors.fill: parent
                        source: "qrc:/icons/moon.svg"
                        sourceSize: Qt.size(14, 14)
                        visible: false
                    }
                    ColorOverlay {
                        anchors.fill: moonIcon
                        source: moonIcon
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
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
