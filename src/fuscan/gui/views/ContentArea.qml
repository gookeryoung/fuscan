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

    // 用 StackLayout 替代 StackView.replace，所有页面常驻不重建，
    // 切换仅改 currentIndex（O(1)），消除重页面（SettingsPage/HomePage）反复
    // 构造导致的卡滞。代价是失去淡入淡出动画与启动时多构造几个页面对象，
    // 但切换流畅性优先级更高；SettingsPage 的 Qt.fontFamilies() 已由
    // Component.onCompleted 延迟到首帧后异步执行，不阻塞首屏。
    // 符合 PySide SKILL 硬约束「复用控件（hide/show + 刷数据），禁止反复创建销毁」。
    readonly property var _pageIndex: ({
        "home": 0,
        "results": 1,
        "stats": 2,
        "settings": 3,
        "about": 4
    })

    StackLayout {
        id: stack
        anchors.fill: parent
        anchors.margins: 24
        currentIndex: contentArea._pageIndex[contentArea.activePage] ?? 0

        HomePage {
            onViewResultsRequested: contentArea.sidebarRef.currentPage = "results"
            onViewStatsRequested: contentArea.sidebarRef.currentPage = "stats"
            // 跳转到设置页规则 Tab（索引 2 = ["扫描","忽略目录","规则","通用"]）
            onConfigureRulesRequested: {
                contentArea.sidebarRef.currentPage = "settings"
                settingsPageItem.switchToTab(2)
            }
        }

        ResultsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        StatsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        SettingsPage {
            id: settingsPageItem
        }

        AboutPage {}
    }
}
