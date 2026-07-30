import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "pages"

Pane {
    id: contentArea
    padding: 0

    // 引用 Sidebar 以读取 currentPage
    property var sidebarRef: null
    property string activePage: sidebarRef ? sidebarRef.currentPage : "home"

    background: Rectangle {
        color: "transparent"
    }

    // iter-144：用 StackLayout 替代 StackView.replace，所有页面常驻不重建，
    // 切换仅改 currentIndex（O(1)），消除重页面（SettingsPage/HomePage）反复
    // 构造导致的卡滞。代价是失去淡入淡出动画与启动时多构造几个页面对象，
    // 但切换流畅性优先级更高；SettingsPage 的 Qt.fontFamilies() 已由
    // Component.onCompleted 延迟到首帧后异步执行，不阻塞首屏。
    // 符合 PySide SKILL 硬约束「复用控件（hide/show + 刷数据），禁止反复创建销毁」。
    readonly property var _pageIndex: ({
        "home": 0,
        "addTask": 1,
        "rules": 2,
        "results": 3,
        "stats": 4,
        "settings": 5,
        "about": 6
    })

    StackLayout {
        id: stack
        anchors.fill: parent
        anchors.margins: 24
        currentIndex: contentArea._pageIndex[contentArea.activePage] ?? 0

        HomePage {
            onViewResultsRequested: contentArea.sidebarRef.currentPage = "results"
            onViewStatsRequested: contentArea.sidebarRef.currentPage = "stats"
            onTaskSettingsRequested: contentArea.sidebarRef.currentPage = "settings"
        }

        AddTaskPage {
            onCreated: contentArea.sidebarRef.currentPage = "home"
            onCancelRequested: contentArea.sidebarRef.currentPage = "home"
        }

        RulesPage {
            // iter-137：规则配置全局化——不再有工作区绑定，直接返回首页
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        ResultsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        StatsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        SettingsPage {}

        AboutPage {}
    }
}
