import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1080
    height: 680
    minimumWidth: 880
    minimumHeight: 560
    title: "fuscan"

    // ========== 背景色随主题切换 ==========
    background: Rectangle {
        color: Theme.isDark ? Theme.colorBgApp : Theme.colorBgApp
        Behavior on color { ColorAnimation { duration: 200 } }
    }

    // ========== 主布局：侧边栏 + 内容 ==========
    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---------- 左侧侧边栏 ----------
        Sidebar {
            id: sidebar
            Layout.preferredWidth: 200
            Layout.fillHeight: true
        }

        // ---------- 右侧主内容 ----------
        ContentArea {
            Layout.fillWidth: true
            Layout.fillHeight: true
            sidebarRef: sidebar
        }
    }
}
