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

    // 各非首页页面的「已加载」标志：首次访问后保持常驻，避免反复创建销毁。
    // 启动时仅构造 HomePage（首屏可见），其他 5 个页面在首次切换到时才加载，
    // 显著降低主 QML 加载耗时（HomePage 1704 行占大头，其他页面共 1485 行延后）。
    // 加载完成后保持 active=true，StackLayout 切换仍是 O(1)（符合 PySide SKILL
    // 「复用控件（hide/show + 刷数据），禁止反复创建销毁」）。
    property bool _monitorLoaded: false
    property bool _resultsLoaded: false
    property bool _statsLoaded: false
    property bool _settingsLoaded: false
    property bool _aboutLoaded: false

    // 用 StackLayout 替代 StackView.replace，所有页面常驻不重建，
    // 切换仅改 currentIndex（O(1)），消除重页面（SettingsPage/HomePage）反复
    // 构造导致的卡滞。代价是失去淡入淡出动画与启动时多构造几个页面对象，
    // 但切换流畅性优先级更高；SettingsPage 的 Qt.fontFamilies() 已由
    // Component.onCompleted 延迟到首帧后异步执行，不阻塞首屏。
    // 非首页用 Loader 包裹按需加载，启动仅构造 HomePage。
    readonly property var _pageIndex: ({
        "home": 0,
        "monitor": 1,
        "results": 2,
        "stats": 3,
        "settings": 4,
        "about": 5
    })

    StackLayout {
        id: stack
        anchors.fill: parent
        anchors.margins: 24
        currentIndex: contentArea._pageIndex[contentArea.activePage] ?? 0

        // 首次切换到非首页时命令式标记为已加载，之后常驻不卸载。
        // 不能用 Loader.onLoaded 写回 _xxLoaded：active 绑定读取 _xxLoaded，
        // onLoaded 又写回 _xxLoaded 会触发 binding loop（active 重新求值→
        // onLoaded 再次触发）。改为在 StackLayout.onCurrentIndexChanged 中
        // 命令式赋值，active 绑定只读 _xxLoaded，写入路径不再回到 active 绑定，
        // 循环断开。Loader.active 一旦为 true 不会再变 false（_xxLoaded 单调
        // 置位），页面常驻符合「复用控件」约束。
        onCurrentIndexChanged: {
            switch (currentIndex) {
                case 1: contentArea._monitorLoaded = true; break
                case 2: contentArea._resultsLoaded = true; break
                case 3: contentArea._statsLoaded = true; break
                case 4: contentArea._settingsLoaded = true; break
                case 5: contentArea._aboutLoaded = true; break
            }
        }

        // HomePage：启动首屏，立即加载
        HomePage {
            onViewResultsRequested: contentArea.sidebarRef.currentPage = "results"
            onViewStatsRequested: contentArea.sidebarRef.currentPage = "stats"
        }

        // FileMonitorPage：首次切换到时加载，之后常驻
        Loader {
            active: contentArea._monitorLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: FileMonitorPage {}
        }

        // ResultsPage：首次切换到时加载，之后常驻
        Loader {
            id: resultsLoader
            active: contentArea._resultsLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: ResultsPage {}
        }
        Connections {
            target: resultsLoader.item
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        // StatsPage：首次切换到时加载，之后常驻
        Loader {
            id: statsLoader
            active: contentArea._statsLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: StatsPage {}
        }
        Connections {
            target: statsLoader.item
            onBackRequested: contentArea.sidebarRef.currentPage = "home"
        }

        // SettingsPage：首次切换到时加载，之后常驻
        Loader {
            active: contentArea._settingsLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: SettingsPage {}
        }

        // AboutPage：首次切换到时加载，之后常驻
        Loader {
            active: contentArea._aboutLoaded
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: AboutPage {}
        }
    }
}
