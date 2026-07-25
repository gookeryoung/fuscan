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

    StackView {
        id: stack
        anchors.fill: parent
        anchors.margins: 24
        clip: true  // 限制动画渲染在 StackView 边界内，避免溢出影响 sidebar
        initialItem: homePage

        // 淡入淡出切换动画（替代默认水平滑动，避免动画溢出到 sidebar）
        replaceEnter: Transition {
            OpacityAnimator { from: 0.0; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
        }
        replaceExit: Transition {
            OpacityAnimator { from: 1.0; to: 0.0; duration: 120; easing.type: Easing.InCubic }
        }

        // 根据 activePage 切换页面（replace 复用，避免重复创建）
        Connections {
            target: contentArea
            function onActivePageChanged() {
                switch (contentArea.activePage) {
                    case "home":
                        stack.replace(homePage)
                        break
                    case "addTask":
                        stack.replace(addTaskPage)
                        break
                    case "rules":
                        stack.replace(rulesPage)
                        break
                    case "results":
                        stack.replace(resultsPage)
                        break
                    case "stats":
                        stack.replace(statsPage)
                        break
                    case "settings":
                        stack.replace(settingsPage)
                        break
                    case "about":
                        stack.replace(aboutPage)
                        break
                }
            }
        }
    }

    // ========== 页面 Component ==========
    Component {
        id: homePage
        HomePage {
            onDefineRulesRequested: contentArea.sidebarRef.currentPage = "rules"
            onViewResultsRequested: contentArea.sidebarRef.currentPage = "results"
            onViewStatsRequested: contentArea.sidebarRef.currentPage = "stats"
            onTaskSettingsRequested: contentArea.sidebarRef.currentPage = "settings"
        }
    }

    Component {
        id: addTaskPage
        AddTaskPage {
            onCreated: contentArea.sidebarRef.currentPage = "home"
            onCancelRequested: contentArea.sidebarRef.currentPage = "home"
        }
    }

    Component {
        id: rulesPage
        RulesPage {}
    }

    Component {
        id: resultsPage
        ResultsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }
    }

    Component {
        id: statsPage
        StatsPage {
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }
    }

    Component {
        id: settingsPage
        SettingsPage {}
    }

    Component {
        id: aboutPage
        AboutPage {}
    }
}
