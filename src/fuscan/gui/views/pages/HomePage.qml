import QtQuick 2.15
import QtQuick.Dialogs 1.3
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 文件扫描页：工作区列表。
// 对话框（切换目标/任务级设置/扫描历史）共享单例，由 WorkspaceCard 发信号触发，
// 避免 N 个工作区各持一份对话框对象（复用控件约束）。
Item {
    id: homePage
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController
    property RulesControllerType rulesController: RulesController

    // 当前待操作的工作区 ID（共享对话框单次操作目标）
    property string _pendingExportWsId: ""
    property string _pendingEditTargetWsId: ""
    property string _pendingHistoryWsId: ""
    property string _pendingConfigureRulesWsId: ""
    property string _pendingPreviewRulesWsId: ""

    // 将 Theme 的 QColor 转换为 RichText 内联 CSS 用的 rgb() 字符串
    function _rgb(c) {
        return "rgb(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + ")"
    }

    // 供 HomePageDialogs.qml 跨文件调用 dropToast.show（dropToast 定义在本文件尾部）
    function _showDropToast(msg, ok) {
        dropToast.show(msg, ok)
    }

    // 对话框组：延迟加载，首帧后异步激活，避免启动时构造 1400+ 行不可见对话框。
    // anchors.fill: parent 让 Loader 拥有 HomePage 尺寸，进而使 dialogsRoot
    // （通过 anchors.fill: parent）获得尺寸，Dialog 的 anchors.centerIn: parent
    // 才能正确居中于页面而非 (0,0)。
    Loader {
        id: dialogsLoader
        anchors.fill: parent
        active: false
        asynchronous: true
        source: "HomePageDialogs.qml"
        onLoaded: item.homePage = homePage
    }

    Component.onCompleted: dialogsLoader.active = true


    // ========== 拖拽接收区：覆盖整个文件扫描页，拖入文件夹即创建扫描任务 ==========
    DropArea {
        id: dropArea
        anchors.fill: parent
        keys: ["text/uri-list"]

        onDropped: {
            if (drop.hasUrls) {
                var paths = []
                for (var i = 0; i < drop.urls.length; i++) {
                    var url = drop.urls[i].toString()
                    if (url.startsWith("file:///")) {
                        paths.push(decodeURIComponent(url.substring(8)))
                    }
                }
                if (paths.length > 0) {
                    var count = workspaceController.addWorkspacesFromPaths(paths)
                    if (count === 0) {
                        dropToast.show("拖拽的目标不是文件或文件夹", false)
                    } else {
                        dropToast.show("已添加 " + count + " 个扫描任务", true)
                    }
                }
            }
        }

        // 拖拽悬浮提示遮罩
        Rectangle {
            anchors.fill: parent
            color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
            border.color: theme.colorPrimary
            border.width: 2
            radius: theme.radiusLg
            visible: dropArea.containsDrag
            z: 1000

            Column {
                anchors.centerIn: parent
                spacing: 12
                Image {
                    source: "qrc:/icons/folder.svg"
                    sourceSize: Qt.size(48, 48)
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Label {
                    text: "松开以添加扫描任务"
                    font.pixelSize: 16
                    font.bold: true
                    color: theme.colorPrimary
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }
        }
    }

    // ========== 拖拽结果 Toast ==========
    Rectangle {
        id: dropToast
        property bool success: false
        property string message: ""
        visible: message.length > 0
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 16
        width: Math.min(dropToastLabel.implicitWidth + 32, parent.width - 32)
        height: dropToastLabel.implicitHeight + 16
        radius: 6
        color: success ? theme.colorSuccess : theme.colorDanger
        opacity: 0.95
        z: 1001

        Label {
            id: dropToastLabel
            anchors.centerIn: parent
            text: dropToast.message
            color: "#FFFFFF"
            font.pixelSize: 12
            elide: Text.ElideRight
        }

        Timer {
            id: dropToastTimer
            interval: 3000
            repeat: false
            onTriggered: dropToast.message = ""
        }

        function show(msg, ok) {
            dropToast.success = ok
            dropToast.message = msg
            dropToastTimer.restart()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // ---------- 标题区 ----------
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: workspaceController.hasActiveScan ? "扫描中" : "工作区"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            // 添加文件夹按钮：扫描中隐藏；点击打开文件夹选择对话框（支持多选）
            // 显眼入口，避免用户不知道可以拖拽文件夹添加任务
            IconButton {
                visible: !workspaceController.hasActiveScan
                iconSource: "qrc:/icons/folder.svg"
                text: "添加文件夹"
                tooltip: "选择一个或多个文件夹创建扫描任务（也可直接拖拽到文件扫描页）"
                accent: "secondary"
                onClicked: { if (dialogsLoader.item) dialogsLoader.item.folderDialogForAdd.open() }
            }
            // 清空按钮：与同行 IconButton 高度一致，danger 风格提示破坏性
            IconButton {
                visible: !workspaceController.hasActiveScan && workspaceController.workspaceCount > 0
                iconSource: "qrc:/icons/delete.svg"
                text: "清空"
                tooltip: "清空所有工作区"
                accent: "ghost"
                onClicked: { if (dialogsLoader.item) dialogsLoader.item.clearConfirmDialog.open() }
            }
            Label {
                // 扫描中隐藏任务计数，避免与扫描进度面板信息冗余
                text: workspaceController.hasActiveScan
                    ? "扫描进行中..."
                    : ("共 " + workspaceController.workspaceCount + " 个任务")
                font.pixelSize: theme.fontSizeSmall
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
        }

        // ---------- 扫描进度面板（扫描中/暂停中显示，隐藏其余工作区） ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            visible: workspaceController.hasActiveScan

            Item {
                anchors.fill: parent

                ScanProgressCard {
                    id: scanCard
                    anchors.top: parent.top
                    anchors.topMargin: 40
                    anchors.left: parent.left
                    anchors.right: parent.right
                    workspaceId: workspaceController.activeScanWorkspaceId
                    taskName: workspaceController.activeScanWorkspaceName
                    modeText: workspaceController.activeScanModeText
                    target: workspaceController.activeScanTarget
                }

                // 提示：扫描结束后自动恢复工作区列表
                Label {
                    anchors.top: scanCard.bottom
                    anchors.topMargin: 12
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "扫描结束后自动恢复工作区列表"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
            }
        }

        // ---------- 工作区列表（无扫描任务时显示） ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            visible: !workspaceController.hasActiveScan

            ListView {
                id: workspaceList
                anchors.fill: parent
                model: workspaceController.workspaceModel
                spacing: 12
                // 预渲染屏幕外 delegate，避免滚动时 WorkspaceCard 重建
                cacheBuffer: 500
                implicitHeight: contentHeight

                // 已有任务时常驻顶部拖拽提示条（点击打开文件夹选择对话框）
                header: Rectangle {
                    width: workspaceList.width
                    height: 36
                    visible: workspaceList.count > 0
                    color: dropArea.containsDrag
                        ? Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                        : (theme.isDark ? theme.colorBgApp : theme.colorBgApp)
                    border.color: dropArea.containsDrag
                        ? theme.colorPrimary
                        : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                    border.width: 1
                    radius: theme.radiusSm

                    Label {
                        anchors.centerIn: parent
                        text: dropArea.containsDrag
                            ? "松开以添加扫描任务"
                            : "拖拽文件夹到此处添加任务，或点击右上角「添加文件夹」"
                        font.pixelSize: 12
                        color: dropArea.containsDrag
                            ? theme.colorPrimary
                            : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { if (dialogsLoader.item) dialogsLoader.item.folderDialogForAdd.open() }
                    }
                }

                // 空态引导
                Label {
                    anchors.centerIn: parent
                    visible: workspaceList.count === 0
                    text: "拖拽文件夹到此处创建扫描任务"
                    font.pixelSize: 13
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    horizontalAlignment: Text.AlignHCenter
                }

                delegate: WorkspaceCard {
                    width: workspaceList.width
                    workspaceId: model.workspaceId
                    taskName: model.name
                    modeText: model.modeText
                    target: model.target
                    rulesTags: model.rulesTags
                    statusText: model.statusText
                    matchedCount: model.matchedCount
                    passedCount: model.passedCount
                    skippedCount: model.skippedCount
                    errorCount: model.errorCount
                    collectedCount: model.collectedCount
                    lastSummary: model.lastSummary

                    onViewResultsRequested: function(wsId) {
                        workspaceController.setCurrentWorkspaceId(wsId)
                        homePage.viewResultsRequested(wsId)
                    }
                    onViewStatsRequested: function(wsId) {
                        workspaceController.setCurrentWorkspaceId(wsId)
                        homePage.viewStatsRequested(wsId)
                    }
                    onExportCsvRequested: function(wsId) {
                        homePage._pendingExportWsId = wsId
                        if (dialogsLoader.item) dialogsLoader.item.exportCsvDialog.open()
                    }
                    onExportJsonRequested: function(wsId) {
                        homePage._pendingExportWsId = wsId
                        if (dialogsLoader.item) dialogsLoader.item.exportJsonDialog.open()
                    }
                    onExportPdfRequested: function(wsId) {
                        homePage._pendingExportWsId = wsId
                        if (dialogsLoader.item) dialogsLoader.item.exportPdfDialog.open()
                    }
                    // 切换目标：初始化 editTargetDialog 并打开
                    onEditTargetRequested: function(wsId) {
                        homePage._pendingEditTargetWsId = wsId
                        var modeStr = model.modeText === "盘符扫描" ? "drive" : "folder"
                        if (dialogsLoader.item) {
                            dialogsLoader.item.editTargetDialog.editModeIndex = modeStr === "drive" ? 0 : 1
                            dialogsLoader.item.editTargetDialog.editDrive = modeStr === "drive" ? model.target : ""
                            dialogsLoader.item.editTargetDialog.editFolder = modeStr === "folder" ? model.target : ""
                            dialogsLoader.item.editTargetDialog.open()
                        }
                    }
                    // 扫描历史：加载历史 JSON 与对比 JSON
                    onViewHistoryRequested: function(wsId) {
                        homePage._pendingHistoryWsId = wsId
                        var histJson = workspaceController.workspaceHistoryJson(wsId)
                        var cmpJson = workspaceController.compareWithPreviousScan(wsId)
                        if (dialogsLoader.item) {
                            try { dialogsLoader.item.historyDialog.historyList = JSON.parse(histJson) } catch(e) { dialogsLoader.item.historyDialog.historyList = [] }
                            try { dialogsLoader.item.historyDialog.comparison = JSON.parse(cmpJson) } catch(e) { dialogsLoader.item.historyDialog.comparison = {} }
                            dialogsLoader.item.historyDialog.open()
                        }
                    }
                    // 配置规则：打开共享对话框（单一列表，所有操作立即生效，无需初始化本地编辑状态）
                    onConfigureRulesRequested: function(wsId) {
                        homePage._pendingConfigureRulesWsId = wsId
                        // 切换 RulesController 的当前工作区上下文，使临时规则列表刷新
                        workspaceController.setCurrentWorkspaceId(wsId)
                        if (dialogsLoader.item) dialogsLoader.item.configureRulesDialog.open()
                    }
                    // 预览规则：调用 Slot 取 effective ruleset JSON，解析后打开只读对话框
                    onPreviewRulesRequested: function(wsId) {
                        homePage._pendingPreviewRulesWsId = wsId
                        var jsonStr = rulesController.previewRuleset(wsId)
                        var data = {}
                        try { data = JSON.parse(jsonStr) } catch(e) { data = {} }
                        if (dialogsLoader.item) {
                            dialogsLoader.item.previewRulesDialog.previewData = data
                            dialogsLoader.item.previewRulesDialog.open()
                        }
                    }
                }
            }
        }

        // ---------- 盘符扫描入口（工作区列表下方，补充拖拽创建任务） ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            visible: !workspaceController.hasActiveScan

            Label {
                text: "盘符扫描"
                font.pixelSize: theme.fontSizeCaption
                font.bold: true
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: configController.drives
                    delegate: IconButton {
                        iconSource: "qrc:/icons/disk.svg"
                        text: modelData
                        accent: "secondary"
                        enabled: !workspaceController.hasActiveScan
                        onClicked: {
                            workspaceController.addWorkspace("", "drive", modelData, "[]", true)
                        }
                    }
                }
                Label {
                    visible: configController.drives.length === 0
                    text: "未检测到可用盘符"
                    font.pixelSize: 12
                    color: theme.colorWarning
                }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // 信号：通知 ContentArea 切换页面
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)
}
