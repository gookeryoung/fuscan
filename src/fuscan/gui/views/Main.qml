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

    // iter-124：拦截窗口关闭，先显示退出保存进度 Popup，再异步触发 Qt.quit()
    // 避免 cleanup 阻塞主线程时用户看到「无响应」假象
    onClosing: {
        close.accepted = false  // 阻止默认关闭
        exitPopup.open()
        exitTimer.start()  // 50ms 后调用 Qt.quit()，让 Popup 先渲染
    }

    // 退出保存进度 Popup（modal，不可手动关闭）
    Popup {
        id: exitPopup
        modal: true
        closePolicy: Popup.NoAutoClose
        anchors.centerIn: parent
        width: 360
        padding: 24

        background: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: theme.radiusLg
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 16

            Label {
                Layout.fillWidth: true
                text: "正在保存退出状态，请稍候..."
                font.pixelSize: 14
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                horizontalAlignment: Text.AlignHCenter
            }

            ProgressBar {
                Layout.fillWidth: true
                indeterminate: true
            }

            Label {
                Layout.fillWidth: true
                text: "正在清理扫描线程与缓存资源"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }

    // 延迟触发 Qt.quit()，让 exitPopup 先渲染显示
    Timer {
        id: exitTimer
        interval: 50
        repeat: false
        onTriggered: Qt.quit()
    }

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
