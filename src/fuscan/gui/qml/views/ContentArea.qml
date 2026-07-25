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
        initialItem: homePage

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
            onAddTaskRequested: contentArea.sidebarRef.currentPage = "addTask"
            onDefineRulesRequested: contentArea.sidebarRef.currentPage = "rules"
            onViewResultsRequested: contentArea.sidebarRef.currentPage = "home"
            onViewStatsRequested: contentArea.sidebarRef.currentPage = "home"
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
        id: settingsPage
        SettingsPage {}
    }

    Component {
        id: aboutPage
        AboutPage {}
    }
}
