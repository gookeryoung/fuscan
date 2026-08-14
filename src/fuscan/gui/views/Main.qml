import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

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
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 全局字体绑定到 ThemeController：
    // QGuiApplication.setFont() 仅设置默认值，不会主动刷新已存在的 QML 控件。
    // 在 ApplicationWindow 显式绑定 font 属性，themeChanged 触发时窗口字体立即更新，
    // 所有未显式设置 font 的子控件通过 Qt 字体传播机制继承新字体。
    font.family: theme.fontFamily
    font.pixelSize: theme.fontSizeBody
    font.bold: theme.fontBold

    // 拦截窗口关闭，先显示退出保存进度 Popup，再异步触发 Qt.quit()
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

    // 取消扫描进度 Popup（modal，不可手动关闭）
    // 绑定到当前活动 ScanController 的 cancelling 属性：
    // cancelScan 设 True 时自动 open，_reset_scan_ui 设 False 时自动 close。
    // 注意：activeScanController 一律通过 WorkspaceController.activeScanController.xxx
    // 链式访问，不绑定到本地 property（PySide2 5.15 类型推断 null 同源问题）。
    Popup {
        id: cancelPopup
        modal: true
        closePolicy: Popup.NoAutoClose
        anchors.centerIn: parent
        width: 360
        padding: 24
        visible: workspaceController.activeScanController.cancelling

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
                text: "正在取消扫描，请稍候..."
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
                text: "正在等待扫描线程退出并清理资源"
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
            Layout.preferredWidth: sidebar.collapsed ? 0 : 200
            Layout.fillHeight: true
            // 折叠/展开宽度动画（Layout.preferredWidth 为附件属性，
            // Behavior 在 Qt 5.15 支持附件属性动画）
            Behavior on Layout.preferredWidth {
                NumberAnimation { duration: 200; easing.type: Easing.OutQuad }
            }
        }

        // ---------- 右侧主内容 ----------
        ContentArea {
            Layout.fillWidth: true
            Layout.fillHeight: true
            sidebarRef: sidebar
        }
    }

    // ========== 全局快捷键 ==========
    // 仅在无模态弹窗时生效，避免与弹窗操作冲突
    property bool _anyModalVisible: exitPopup.visible || cancelPopup.visible

    // Ctrl+1-6：切换页面（文件扫描/文件监控/扫描结果/统计/设置/关于）
    Shortcut {
        sequence: "Ctrl+1"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "home"
    }
    Shortcut {
        sequence: "Ctrl+2"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "monitor"
    }
    Shortcut {
        sequence: "Ctrl+3"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "results"
    }
    Shortcut {
        sequence: "Ctrl+4"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "stats"
    }
    Shortcut {
        sequence: "Ctrl+5"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "settings"
    }
    Shortcut {
        sequence: "Ctrl+6"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "about"
    }

    // Ctrl+B：折叠/展开侧边栏
    Shortcut {
        sequence: "Ctrl+B"
        enabled: !root._anyModalVisible
        onActivated: sidebar.collapsed = !sidebar.collapsed
    }

    // Ctrl+R：重扫当前工作区（无选中工作区时禁用）
    Shortcut {
        sequence: "Ctrl+R"
        enabled: !root._anyModalVisible && workspaceController.currentWorkspaceId !== ""
        onActivated: workspaceController.startScan(workspaceController.currentWorkspaceId)
    }

    // Esc：返回首页
    Shortcut {
        sequence: "Escape"
        enabled: !root._anyModalVisible
        onActivated: sidebar.currentPage = "home"
    }
}
