import QtQuick 2.15
import QtQuick.Dialogs 1.3
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 首页：工作区列表。
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
    property string _pendingTaskSettingsWsId: ""
    property string _pendingHistoryWsId: ""

    // ========== 导出文件保存对话框 ==========
    FileDialog {
        id: exportCsvDialog
        title: "导出扫描结果为 CSV"
        selectExisting: false
        defaultSuffix: "csv"
        nameFilters: ["CSV (*.csv)"]
        onAccepted: {
            var path = exportCsvDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(homePage._pendingExportWsId, "csv", path)
        }
    }
    FileDialog {
        id: exportJsonDialog
        title: "导出扫描结果为 JSON"
        selectExisting: false
        defaultSuffix: "json"
        nameFilters: ["JSON (*.json)"]
        onAccepted: {
            var path = exportJsonDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(homePage._pendingExportWsId, "json", path)
        }
    }
    FileDialog {
        id: exportPdfDialog
        title: "导出扫描结果为 PDF"
        selectExisting: false
        defaultSuffix: "pdf"
        nameFilters: ["PDF (*.pdf)"]
        onAccepted: {
            var path = exportPdfDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(homePage._pendingExportWsId, "pdf", path)
        }
    }

    // ========== 清空所有工作区确认对话框 ==========
    Dialog {
        id: clearConfirmDialog
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: theme.radiusMd
            implicitWidth: 380
            implicitHeight: confirmColumn.implicitHeight + 32

            ColumnLayout {
                id: confirmColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 16

                // 标题行：警告图标 + 标题
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Item {
                        width: theme.fontSizeHeading
                        height: theme.fontSizeHeading
                        Layout.preferredWidth: theme.fontSizeHeading
                        Layout.preferredHeight: theme.fontSizeHeading
                        Image {
                            id: warnIcon
                            anchors.fill: parent
                            source: "qrc:/icons/warning.svg"
                            sourceSize: Qt.size(theme.fontSizeHeading, theme.fontSizeHeading)
                            visible: false
                        }
                        ColorOverlay {
                            anchors.fill: warnIcon
                            source: warnIcon
                            color: theme.colorWarning
                        }
                    }
                    Label {
                        text: "清空所有工作区"
                        font.pixelSize: theme.fontSizeHeading
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }
                    Item { Layout.fillWidth: true }
                }

                Label {
                    Layout.fillWidth: true
                    text: "将移除全部 " + workspaceController.workspaceCount + " 个任务及其扫描结果，此操作不可撤销。是否继续？"
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignRight
                    spacing: 8
                    Button {
                        text: "取消"
                        implicitWidth: 80
                        implicitHeight: theme.btnHeightSecondary
                        font.pixelSize: theme.fontSizeBody
                        palette.buttonText: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        background: Rectangle {
                            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                            border.width: 1
                            radius: theme.radiusSm
                        }
                        onClicked: clearConfirmDialog.reject()
                    }
                    Button {
                        text: "清空"
                        implicitWidth: 80
                        implicitHeight: theme.btnHeightSecondary
                        font.pixelSize: theme.fontSizeBody
                        palette.buttonText: theme.colorTextOnPrimary
                        background: Rectangle {
                            color: theme.colorDanger
                            border.color: theme.colorDanger
                            border.width: 1
                            radius: theme.radiusSm
                        }
                        onClicked: {
                            var ok = workspaceController.clearAllWorkspaces()
                            clearConfirmDialog.close()
                            if (!ok) {
                                clearResultDialog.open()
                            }
                        }
                    }
                }
            }
        }
    }

    // 清空结果提示对话框（扫描中拒绝清空时显示）
    Dialog {
        id: clearResultDialog
        modal: true
        anchors.centerIn: parent
        width: 340

        contentItem: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: theme.radiusMd
            implicitWidth: 340
            implicitHeight: resultColumn.implicitHeight + 32

            ColumnLayout {
                id: resultColumn
                anchors.fill: parent
                anchors.margins: 16
                spacing: 16

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Item {
                        width: theme.fontSizeHeading
                        height: theme.fontSizeHeading
                        Layout.preferredWidth: theme.fontSizeHeading
                        Layout.preferredHeight: theme.fontSizeHeading
                        Image {
                            id: infoIcon
                            anchors.fill: parent
                            source: "qrc:/icons/info.svg"
                            sourceSize: Qt.size(theme.fontSizeHeading, theme.fontSizeHeading)
                            visible: false
                        }
                        ColorOverlay {
                            anchors.fill: infoIcon
                            source: infoIcon
                            color: theme.colorPrimary
                        }
                    }
                    Label {
                        text: "无法清空"
                        font.pixelSize: theme.fontSizeHeading
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }
                    Item { Layout.fillWidth: true }
                }

                Label {
                    Layout.fillWidth: true
                    text: "有任务正在扫描，请等待扫描结束或取消后再试。"
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignRight
                    Button {
                        text: "知道了"
                        implicitHeight: theme.btnHeightPrimary
                        font.pixelSize: theme.fontSizeBody
                        palette.buttonText: theme.colorTextOnPrimary
                        background: Rectangle {
                            color: theme.colorPrimary
                            radius: theme.radiusSm
                        }
                        onClicked: clearResultDialog.close()
                    }
                }
            }
        }
    }

    // ========== 切换扫描目标对话框（共享单例） ==========
    Dialog {
        id: editTargetDialog
        title: "切换扫描目标"
        modal: true
        anchors.centerIn: parent
        width: 420
        standardButtons: Dialog.Cancel | Dialog.Ok

        // 临时编辑状态（由 delegate 信号处理初始化）
        property int editModeIndex: 1
        property string editDrive: ""
        property string editFolder: ""

        contentItem: ColumnLayout {
            spacing: 12

            Label {
                text: "扫描模式"
                font.bold: true
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            TabBar {
                id: editModeTabBar
                Layout.fillWidth: true
                currentIndex: editTargetDialog.editModeIndex
                onCurrentIndexChanged: editTargetDialog.editModeIndex = currentIndex
                TabButton {
                    text: "盘符扫描"
                    height: theme.btnHeightSecondary
                }
                TabButton {
                    text: "文件夹扫描"
                    height: theme.btnHeightSecondary
                }
            }

            // 盘符选择（modeIndex === 0）
            RowLayout {
                Layout.fillWidth: true
                visible: editTargetDialog.editModeIndex === 0
                spacing: 6
                Repeater {
                    model: configController.drives
                    delegate: Button {
                        Layout.preferredWidth: 72
                        Layout.preferredHeight: theme.btnHeightSecondary
                        text: modelData
                        checkable: true
                        checked: editTargetDialog.editDrive === modelData
                        onClicked: editTargetDialog.editDrive = modelData
                        background: Rectangle {
                            color: parent.checked
                                  ? (parent.down ? theme.colorPrimaryDark : theme.colorPrimary)
                                  : (parent.down
                                      ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                      : "transparent")
                            border.color: parent.checked ? theme.colorPrimary
                                : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                            border.width: 1
                            radius: theme.btnRadiusSecondary
                        }
                        contentItem: Label {
                            text: parent.text
                            color: parent.checked ? theme.colorTextOnPrimary
                                : (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                            font.pixelSize: 12
                            font.bold: parent.checked
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
                Label {
                    visible: configController.drives.length === 0
                    text: "未检测到可用盘符"
                    font.pixelSize: 12
                    color: theme.colorWarning
                }
            }

            // 文件夹选择（modeIndex === 1）
            RowLayout {
                Layout.fillWidth: true
                visible: editTargetDialog.editModeIndex === 1
                spacing: 8
                TextField {
                    Layout.fillWidth: true
                    Layout.preferredHeight: theme.btnHeightSecondary
                    placeholderText: "选择或输入扫描目录"
                    text: editTargetDialog.editFolder
                    onTextChanged: editTargetDialog.editFolder = text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    background: Rectangle {
                        color: "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusSecondary
                    }
                }
                IconButton {
                    iconSource: "qrc:/icons/folder.svg"
                    text: "选择"
                    tooltip: "选择扫描目录"
                    accent: "secondary"
                    onClicked: folderDialogForEdit.open()
                }
            }
        }

        onAccepted: {
            var modeStr = editTargetDialog.editModeIndex === 0 ? "drive" : "folder"
            var target = editTargetDialog.editModeIndex === 0
                ? editTargetDialog.editDrive
                : editTargetDialog.editFolder
            workspaceController.updateWorkspaceTarget(homePage._pendingEditTargetWsId, modeStr, target)
        }
    }

    // 切换目标对话框用的文件夹选择器
    Dialogs.FileDialog {
        id: folderDialogForEdit
        title: "选择扫描目录"
        selectFolder: true
        selectExisting: true
        folder: editTargetDialog.editFolder.length > 0
            ? "file:///" + editTargetDialog.editFolder
            : shortcuts.home
        onAccepted: {
            editTargetDialog.editFolder = folderDialogForEdit.fileUrl.toString().replace(/^file:\/\/\//, "")
        }
    }

    // 首页「添加文件夹」按钮用的文件夹选择器（多选，创建多个任务）
    Dialogs.FileDialog {
        id: folderDialogForAdd
        title: "选择要扫描的文件夹"
        selectFolder: true
        selectExisting: true
        selectMultiple: true
        folder: shortcuts.home
        onAccepted: {
            var paths = []
            var urls = folderDialogForAdd.fileUrls
            for (var i = 0; i < urls.length; i++) {
                var url = urls[i].toString()
                if (url.startsWith("file:///")) {
                    paths.push(decodeURIComponent(url.substring(8)))
                }
            }
            if (paths.length > 0) {
                var count = workspaceController.addWorkspacesFromPaths(paths)
                if (count === 0) {
                    dropToast.show("选择的目标不是文件夹", false)
                } else {
                    dropToast.show("已添加 " + count + " 个扫描任务", true)
                }
            }
        }
    }

    // ========== 任务级设置对话框（共享单例） ==========
    Dialog {
        id: taskSettingsDialog
        title: "任务级设置 — " + workspaceController.workspaceName(homePage._pendingTaskSettingsWsId)
        modal: true
        anchors.centerIn: parent
        width: 460
        standardButtons: Dialog.Cancel | Dialog.Ok

        // 临时编辑状态（由 delegate 信号处理从 taskOverridesJson 读取初始化）
        property bool editScanArchives: true
        property int editMaxWorkers: 5
        property int editMaxFileSizeMB: 50
        property int editMaxDepth: 10
        property string editIgnoreDirs: ""

        contentItem: ColumnLayout {
            spacing: 12

            Label {
                text: "仅对该任务生效，不影响全局设置。留空或默认值时使用全局配置。"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }

            // 扫描压缩包
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "扫描压缩包"
                    Layout.fillWidth: true
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Switch {
                    checked: taskSettingsDialog.editScanArchives
                    onCheckedChanged: taskSettingsDialog.editScanArchives = checked
                }
            }

            // 最大工作线程
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "最大工作线程"
                    Layout.fillWidth: true
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Label {
                    text: "当前机器最大线程=" + configController.cpuCount
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                SpinBox {
                    id: taskMaxWorkersSpin
                    from: 1
                    to: Math.max(configController.cpuCount, 1)
                    value: Math.min(taskSettingsDialog.editMaxWorkers, configController.cpuCount)
                    editable: true
                    // 用 onValueModified 替代 onValueChanged，避免 binding loop
                    onValueModified: taskSettingsDialog.editMaxWorkers = value
                }
            }

            // 最大文件大小（MB）
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "最大文件大小（MB）"
                    Layout.fillWidth: true
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                SpinBox {
                    id: taskMaxFileSizeSpin
                    from: 1
                    to: 1024
                    value: taskSettingsDialog.editMaxFileSizeMB
                    editable: true
                    stepSize: {
                        var v = taskMaxFileSizeSpin.value
                        if (v < 50) return 10
                        if (v < 100) return 25
                        return 100
                    }
                    onValueModified: taskSettingsDialog.editMaxFileSizeMB = value
                }
            }

            // 最大递归深度
            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "最大递归深度"
                    Layout.fillWidth: true
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                SpinBox {
                    from: 1
                    to: 64
                    value: taskSettingsDialog.editMaxDepth
                    editable: true
                    onValueModified: taskSettingsDialog.editMaxDepth = value
                }
            }

            // 忽略目录
            Label {
                text: "忽略目录（一行一个）"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 100
                clip: true
                TextArea {
                    text: taskSettingsDialog.editIgnoreDirs
                    onTextChanged: taskSettingsDialog.editIgnoreDirs = text
                    font.pixelSize: theme.fontSizeBody
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    background: Rectangle {
                        color: "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusSecondary
                    }
                }
            }
        }

        onAccepted: {
            // 与全局值相同的字段清除覆盖，不同的字段才下发 setTaskOverride
            var wsId = homePage._pendingTaskSettingsWsId
            var keys = ["scan_archives", "max_workers", "max_file_size", "max_depth", "ignore_dirs"]
            for (var i = 0; i < keys.length; i++) {
                var key = keys[i]
                var value
                var globalValue
                if (key === "scan_archives") {
                    value = taskSettingsDialog.editScanArchives
                    globalValue = configController.scanArchives
                } else if (key === "max_workers") {
                    value = taskSettingsDialog.editMaxWorkers
                    globalValue = configController.maxWorkers
                } else if (key === "max_file_size") {
                    value = taskSettingsDialog.editMaxFileSizeMB * 1024 * 1024
                    globalValue = configController.maxFileSizeMB * 1024 * 1024
                } else if (key === "max_depth") {
                    value = taskSettingsDialog.editMaxDepth
                    globalValue = configController.maxDepth
                } else if (key === "ignore_dirs") {
                    var lines = taskSettingsDialog.editIgnoreDirs.split("\n")
                    var cleaned = []
                    for (var j = 0; j < lines.length; j++) {
                        var line = lines[j].trim()
                        if (line.length > 0) cleaned.push(line)
                    }
                    if (cleaned.length === 0) {
                        workspaceController.clearTaskOverride(wsId, key)
                    } else {
                        workspaceController.setTaskOverride(wsId, key, JSON.stringify(cleaned))
                    }
                    continue
                }
                if (value === globalValue) {
                    workspaceController.clearTaskOverride(wsId, key)
                } else {
                    workspaceController.setTaskOverride(wsId, key, JSON.stringify(value))
                }
            }
        }
    }

    // ========== 扫描历史对话框（共享单例） ==========
    Dialog {
        id: historyDialog
        title: "扫描历史 — " + workspaceController.workspaceName(homePage._pendingHistoryWsId)
        modal: true
        anchors.centerIn: parent
        width: 640
        height: 520
        standardButtons: Dialog.Close

        // 历史列表（按时间倒序）
        property var historyList: []
        // 对比结果对象（current/previous/summary/trend/...）
        property var comparison: {}

        contentItem: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: theme.radiusMd

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12

                // ---------- 对比摘要区 ----------
                Rectangle {
                    Layout.fillWidth: true
                    visible: !!(historyDialog.comparison && historyDialog.comparison.summary)
                    color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.radiusSm
                    implicitHeight: cmpColumn.implicitHeight + 16

                    ColumnLayout {
                        id: cmpColumn
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        Label {
                            text: "对比摘要"
                            font.pixelSize: 12
                            font.bold: true
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Label {
                            Layout.fillWidth: true
                            text: historyDialog.comparison ? (historyDialog.comparison.summary || "") : ""
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            wrapMode: Text.WordWrap
                        }
                        // 趋势标签
                        Rectangle {
                            visible: !!(historyDialog.comparison && historyDialog.comparison.trend)
                            radius: 8
                            height: 18
                            width: trendLabel.width + 12
                            color: {
                                var t = historyDialog.comparison ? historyDialog.comparison.trend : ""
                                if (t === "改善") return theme.colorSuccess
                                if (t === "恶化") return theme.colorDanger
                                if (t === "首次") return theme.colorPrimary
                                return theme.colorTextSecondary
                            }
                            Label {
                                id: trendLabel
                                anchors.centerIn: parent
                                text: historyDialog.comparison ? (historyDialog.comparison.trend || "") : ""
                                font.pixelSize: 10
                                font.bold: true
                                color: theme.colorTextOnPrimary
                            }
                        }
                    }
                }

                // ---------- 历史列表 ----------
                Label {
                    text: "历史记录"
                    font.pixelSize: 12
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    ListView {
                        id: historyListView
                        model: historyDialog.historyList
                        spacing: 6

                        // 空态
                        Label {
                            anchors.centerIn: parent
                            visible: historyListView.count === 0
                            text: "暂无扫描历史"
                            font.pixelSize: 12
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }

                        delegate: Rectangle {
                            width: historyListView.width
                            height: histRow.implicitHeight + 16
                            color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                            border.width: 1
                            radius: theme.radiusSm

                            RowLayout {
                                id: histRow
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 10

                                // 状态色条
                                Rectangle {
                                    width: 3
                                    height: parent.height * 0.6
                                    color: {
                                        var s = modelData.status
                                        if (s === "completed") return theme.colorSuccess
                                        if (s === "cancelled") return theme.colorWarning
                                        return theme.colorDanger
                                    }
                                    radius: 2
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    // 第一行：完成时间 + 命中数
                                    Label {
                                        Layout.fillWidth: true
                                        text: {
                                            var ts = modelData.finished_at || ""
                                            return ts.replace("T", " ").replace("Z", "") + " | 命中 " + modelData.matched_files
                                        }
                                        font.pixelSize: 12
                                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        elide: Text.ElideRight
                                    }
                                    // 第二行：摘要
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.summary || ""
                                        font.pixelSize: 10
                                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        elide: Text.ElideRight
                                        visible: text.length > 0
                                    }
                                }

                                // 状态徽标
                                Rectangle {
                                    radius: 8
                                    height: 18
                                    width: statusTag.width + 10
                                    color: {
                                        var s = modelData.status
                                        if (s === "completed") return theme.colorSuccess
                                        if (s === "cancelled") return theme.colorWarning
                                        return theme.colorDanger
                                    }
                                    Label {
                                        id: statusTag
                                        anchors.centerIn: parent
                                        text: {
                                            var s = modelData.status
                                            if (s === "completed") return "完成"
                                            if (s === "cancelled") return "取消"
                                            return "失败"
                                        }
                                        font.pixelSize: 10
                                        color: theme.colorTextOnPrimary
                                    }
                                }
                            }
                        }
                    }
                }

                // ---------- 清空历史按钮 ----------
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Button {
                        text: "清空历史"
                        font.pixelSize: theme.fontSizeSmall
                        flat: true
                        implicitHeight: 32
                        palette.buttonText: theme.colorDanger
                        enabled: historyDialog.historyList.length > 0
                        onClicked: {
                            workspaceController.clearWorkspaceHistory(homePage._pendingHistoryWsId)
                            historyDialog.historyList = []
                            historyDialog.comparison = {}
                        }
                    }
                }
            }
        }
    }

    // ========== 拖拽接收区：覆盖整个首页，拖入文件夹即创建扫描任务 ==========
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
                        dropToast.show("拖拽的目标不是文件夹", false)
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
                tooltip: "选择一个或多个文件夹创建扫描任务（也可直接拖拽到首页）"
                accent: "secondary"
                onClicked: folderDialogForAdd.open()
            }
            // 清空按钮：与同行 IconButton 高度一致，danger 风格提示破坏性
            IconButton {
                visible: !workspaceController.hasActiveScan && workspaceController.workspaceCount > 0
                iconSource: "qrc:/icons/delete.svg"
                text: "清空"
                tooltip: "清空所有工作区"
                accent: "ghost"
                onClicked: clearConfirmDialog.open()
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
                        onClicked: folderDialogForAdd.open()
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
                        exportCsvDialog.open()
                    }
                    onExportJsonRequested: function(wsId) {
                        homePage._pendingExportWsId = wsId
                        exportJsonDialog.open()
                    }
                    onExportPdfRequested: function(wsId) {
                        homePage._pendingExportWsId = wsId
                        exportPdfDialog.open()
                    }
                    // 切换目标：初始化 editTargetDialog 并打开
                    onEditTargetRequested: function(wsId) {
                        homePage._pendingEditTargetWsId = wsId
                        var modeStr = model.modeText === "盘符扫描" ? "drive" : "folder"
                        editTargetDialog.editModeIndex = modeStr === "drive" ? 0 : 1
                        editTargetDialog.editDrive = modeStr === "drive" ? model.target : ""
                        editTargetDialog.editFolder = modeStr === "folder" ? model.target : ""
                        editTargetDialog.open()
                    }
                    // 任务级设置：从 taskOverridesJson 初始化并打开
                    onTaskSettingsRequested: function(wsId) {
                        homePage._pendingTaskSettingsWsId = wsId
                        var jsonStr = workspaceController.taskOverridesJson(wsId)
                        var overrides = {}
                        try { overrides = JSON.parse(jsonStr) } catch(e) { overrides = {} }
                        taskSettingsDialog.editScanArchives = overrides.scan_archives !== undefined
                            ? overrides.scan_archives : configController.scanArchives
                        taskSettingsDialog.editMaxWorkers = overrides.max_workers !== undefined
                            ? overrides.max_workers : configController.maxWorkers
                        taskSettingsDialog.editMaxFileSizeMB = overrides.max_file_size !== undefined
                            ? Math.floor(overrides.max_file_size / (1024 * 1024))
                            : configController.maxFileSizeMB
                        taskSettingsDialog.editMaxDepth = overrides.max_depth !== undefined
                            ? overrides.max_depth : configController.maxDepth
                        var dirs = overrides.ignore_dirs !== undefined ? overrides.ignore_dirs : []
                        taskSettingsDialog.editIgnoreDirs = Array.isArray(dirs) ? dirs.join("\n") : ""
                        taskSettingsDialog.open()
                    }
                    // 扫描历史：加载历史 JSON 与对比 JSON
                    onViewHistoryRequested: function(wsId) {
                        homePage._pendingHistoryWsId = wsId
                        var histJson = workspaceController.workspaceHistoryJson(wsId)
                        var cmpJson = workspaceController.compareWithPreviousScan(wsId)
                        try { historyDialog.historyList = JSON.parse(histJson) } catch(e) { historyDialog.historyList = [] }
                        try { historyDialog.comparison = JSON.parse(cmpJson) } catch(e) { historyDialog.comparison = {} }
                        historyDialog.open()
                    }
                    // 配置规则：转发给 ContentArea 跳转到设置页规则 Tab
                    onConfigureRulesRequested: function(wsId) {
                        homePage.configureRulesRequested(wsId)
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
    // 信号：通知 ContentArea 跳转到设置页规则 Tab
    signal configureRulesRequested(string workspaceId)
}
