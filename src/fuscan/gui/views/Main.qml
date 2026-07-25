import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

ApplicationWindow {
    id: root
    visible: true
    width: 1080
    height: 680
    minimumWidth: 880
    minimumHeight: 560
    title: "fuscan"

    // 类型化访问 context property，消除 setContextProperty 导致的 TypeError
    property ThemeController theme: Theme

    // ========== 全局 palette：未显式设置颜色的控件（CheckBox.text/GroupBox.title/
    // Switch.text/SpinBox/ItemDelegate 等）通过 palette 继承主题色，避免暗色模式下黑字看不清 ==========
    palette.window: theme.colorBgApp
    palette.windowText: theme.colorTextPrimary
    palette.base: theme.colorBgApp
    palette.alternateBase: theme.colorBgCard
    palette.text: theme.colorTextPrimary
    palette.buttonText: theme.colorTextPrimary
    palette.button: theme.colorBgCard
    palette.highlight: theme.colorPrimary
    palette.highlightedText: theme.colorTextOnPrimary
    palette.mid: theme.colorBorder
    palette.dark: theme.colorBorderDark
    palette.light: theme.colorBgHover

    // ========== 背景色随主题切换 ==========
    background: Rectangle {
        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
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
