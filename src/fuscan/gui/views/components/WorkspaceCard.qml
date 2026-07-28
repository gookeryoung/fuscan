import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 工作区卡片：单任务展示与操作
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    // 类型化访问 context property，消除 setContextProperty 导致的 TypeError
    // （与 Main.qml / AddTaskPage.qml 同模式：PySide2 5.15 直接用 ConfigController.xxx
    // 会被类型推断识别为 null，须声明 property ConfigControllerType 后用 configController.xxx）
    property ConfigControllerType configController: ConfigController

    // 由 ListView delegate 注入
    property string workspaceId: ""
    property string taskName: ""
    property string modeText: ""
    property string target: ""
    property string rulesText: ""
    property var rulesTags: []
    property string statusText: ""
    property int matchedCount: 0
    property int passedCount: 0
    property int skippedCount: 0
    property int errorCount: 0
    property int collectedCount: 0
    property string lastSummary: ""

    // 是否展开（显示更多操作）
    property bool expanded: false

    // 信号：通知 HomePage 切换到规则页 / 结果页 / 统计页
    signal defineRulesRequested(string workspaceId)
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)

    implicitHeight: contentColumn.implicitHeight + 24
    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 状态色：根据 statusText 决定（iter-127：与 StatsPage 统一判断逻辑与配色）
    function statusColor() {
        var s = String(statusText || "")
        if (s === "扫描中") return theme.colorWarning
        if (s === "已暂停") return theme.colorTextSecondary
        if (s === "已完成") return (matchedCount > 0 ? theme.colorDanger : theme.colorSuccess)
        // 用户取消/失败：黄色警示（非命中危险色，与 StatsPage 一致）
        if (s.indexOf("取消") >= 0 || s === "失败") return theme.colorWarning
        // 就绪：蓝色（非灰色），表示待命可操作
        return theme.colorPrimary
    }

    // 是否处于已完成态（含用户取消）：控制「更新扫描」「查看结果」按钮启用
    function isCompletedState() {
        var s = String(statusText || "")
        return s === "已完成" || s.indexOf("取消") >= 0
    }

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        // ---------- 第一行：任务名 + 状态徽标 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            // 任务图标：SVG rules + ColorOverlay 染色为前景色
            Item {
                width: 16
                height: 16
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
                Image {
                    id: taskIcon
                    anchors.fill: parent
                    source: "qrc:/icons/rules.svg"
                    sourceSize: Qt.size(16, 16)
                    visible: false
                }
                ColorOverlay {
                    anchors.fill: taskIcon
                    source: taskIcon
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
            }
            Label {
                text: taskName
                font.pixelSize: theme.fontSizeHeading
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            // 状态徽标
            Rectangle {
                radius: 10
                height: 22
                width: statusTextLabel.width + 18
                color: card.statusColor()
                Label {
                    id: statusTextLabel
                    anchors.centerIn: parent
                    text: statusText
                    font.pixelSize: 11
                    color: theme.colorTextOnPrimary
                }
            }
        }

        // ---------- 第二行：任务元数据 ----------
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 16
            rowSpacing: 4

            Label {
                text: "模式"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: modeText
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Label {
                text: "目标"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: target || "—"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideMiddle
            }
            Label {
                text: "规则"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            // 规则 TAG 标签列表：内置=灰色，用户定义=绿色，从左到右排列
            RowLayout {
                Layout.fillWidth: true
                spacing: 4
                Repeater {
                    model: rulesTags
                    delegate: Rectangle {
                        radius: 4
                        height: 18
                        width: tagLabel.width + 12
                        // 内置=灰色背景，用户定义=绿色背景
                        color: modelData.is_builtin
                            ? (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                            : theme.colorSuccess
                        Label {
                            id: tagLabel
                            anchors.centerIn: parent
                            text: modelData.name
                            font.pixelSize: 10
                            font.bold: true
                            color: modelData.is_builtin
                                ? (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                                : theme.colorTextOnPrimary
                        }
                    }
                }
                // 空态：未配置规则
                Label {
                    visible: rulesTags.length === 0
                    text: "未配置规则"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
            }
        }

        // ---------- 第三行：最近摘要 ----------
        Label {
            Layout.fillWidth: true
            text: lastSummary ? "最近：" + lastSummary : "尚未扫描"
            font.pixelSize: 11
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            elide: Text.ElideRight
            visible: lastSummary.length > 0 || true
        }

        // ---------- 第四行：分类计数 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            // 收集到的符合文件类型文件数（iter-105 新增）
            Label {
                text: "<b style='color:#0366D6'>符合 " + collectedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
                visible: collectedCount > 0
            }
            Label {
                text: "<b style='color:#28A745'>通过 " + passedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#DC3545'>命中 " + matchedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#FFC107'>跳过 " + skippedCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Label {
                text: "<b style='color:#DC3545'>错误 " + errorCount + "</b>"
                textFormat: Text.RichText
                font.pixelSize: 11
            }
            Item { Layout.fillWidth: true }
        }

        // ---------- 第五行：操作按钮（左侧主要 + 右侧次要 + 展开） ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            // 左侧：定义规则 + 启动/暂停扫描 + 查看结果
            IconButton {
                iconSource: "qrc:/icons/rules.svg"
                text: "定义规则"
                tooltip: "编辑该任务的规则集"
                accent: "secondary"
                onClicked: card.defineRulesRequested(card.workspaceId)
            }
            IconButton {
                iconSource: statusText === "扫描中" ? "qrc:/icons/pause.svg" : "qrc:/icons/scan.svg"
                text: statusText === "扫描中" ? "暂停" : "启动扫描"
                tooltip: statusText === "扫描中" ? "暂停扫描" : "启动扫描"
                accent: "primary"
                // 扫描完成后切换为未激活，避免已完成任务仍高亮扫描按钮
                enabled: !card.isCompletedState()
                onClicked: {
                    if (statusText === "扫描中" || statusText === "已暂停") {
                        workspaceController.togglePause(card.workspaceId)
                    } else {
                        workspaceController.startScan(card.workspaceId)
                    }
                }
            }
            IconButton {
                iconSource: "qrc:/icons/rescan.svg"
                text: "增量扫描"
                tooltip: "仅扫描变更文件，未变更文件复用上次结果（首次或无缓存时自动回退全量扫描）"
                accent: "secondary"
                // 已完成（含用户取消）的工作区可增量扫描
                enabled: card.isCompletedState()
                onClicked: workspaceController.startIncrementalScan(card.workspaceId)
            }
            IconButton {
                iconSource: "qrc:/icons/search.svg"
                text: "查看结果"
                tooltip: "查看扫描结果"
                // 扫描完成后高亮（与扫描前的扫描按钮同色），未完成时 disabled 变灰
                accent: "primary"
                // 已完成（含用户取消）的工作区可查看结果
                enabled: card.isCompletedState()
                onClicked: card.viewResultsRequested(card.workspaceId)
            }

            Item { Layout.fillWidth: true }

            // 右侧：统计 + 展开按钮（保留）
            IconButton {
                iconSource: "qrc:/icons/stats.svg"
                text: "统计"
                tooltip: "查看扫描统计"
                accent: "ghost"
                onClicked: card.viewStatsRequested(card.workspaceId)
            }
            // 展开按钮：more.svg icon + 「展开/收起」文字
            Button {
                id: expandBtn
                Layout.preferredHeight: theme.btnHeightGhost
                leftPadding: 10
                rightPadding: 10
                topPadding: 0
                bottomPadding: 0
                onClicked: card.expanded = !card.expanded
                ToolTip.visible: hovered
                ToolTip.text: card.expanded ? "收起更多操作" : "展开更多操作"
                ToolTip.delay: 400
                background: Rectangle {
                    color: expandBtn.down || expandBtn.hovered
                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                        : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusGhost
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
                contentItem: Row {
                    spacing: 4
                    // 展开图标：SVG more + ColorOverlay 染色
                    Item {
                        width: 14
                        height: 14
                        anchors.verticalCenter: parent.verticalCenter
                        Image {
                            id: moreIcon
                            anchors.fill: parent
                            source: theme.iconsPrefix + "more.svg"
                            sourceSize: Qt.size(14, 14)
                            visible: false
                        }
                        ColorOverlay {
                            anchors.fill: moreIcon
                            source: moreIcon
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                    }
                    Label {
                        text: card.expanded ? "收起" : "展开"
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
        }

        // ---------- 展开后：更多操作 ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: card.expanded

            // 分隔线
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                IconButton {
                    iconSource: "qrc:/icons/target.svg"
                    text: "切换目标"
                    tooltip: "修改该任务的扫描模式与目标路径"
                    accent: "ghost"
                    // 扫描中/暂停中禁用
                    enabled: statusText !== "扫描中" && statusText !== "已暂停"
                    onClicked: {
                        // 用当前工作区状态初始化编辑表单
                        var modeStr = card.modeText === "全盘扫描" ? "full"
                                   : (card.modeText === "盘符扫描" ? "drive" : "folder")
                        editTargetDialog.editModeIndex = modeStr === "full" ? 0
                            : (modeStr === "drive" ? 1 : 2)
                        editTargetDialog.editDrive = modeStr === "drive" ? card.target : ""
                        editTargetDialog.editFolder = modeStr === "folder" ? card.target : ""
                        editTargetDialog.open()
                    }
                }
                // iter-125：CSV/JSON 合并为「导出」按钮 + Menu 格式选择
                IconButton {
                    iconSource: "qrc:/icons/export_csv.svg"
                    text: "导出"
                    tooltip: "导出扫描结果（选择格式）"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportFormatMenu.open()
                    Menu {
                        id: exportFormatMenu
                        MenuItem {
                            text: "CSV (*.csv)"
                            onTriggered: card.exportCsvRequested(card.workspaceId)
                        }
                        MenuItem {
                            text: "JSON (*.json)"
                            onTriggered: card.exportJsonRequested(card.workspaceId)
                        }
                    }
                }
                IconButton {
                    iconSource: "qrc:/icons/settings.svg"
                    text: "设置"
                    tooltip: "任务级设置（仅对该任务生效）"
                    accent: "ghost"
                    onClicked: {
                        // 从 workspaceController 读取当前任务级覆盖
                        var jsonStr = workspaceController.taskOverridesJson(card.workspaceId)
                        var overrides = {}
                        try { overrides = JSON.parse(jsonStr) } catch(e) { overrides = {} }
                        // 全局配置作为默认值（未覆盖时显示全局值）
                        taskSettingsDialog.editScanArchives = overrides.scan_archives !== undefined
                            ? overrides.scan_archives : configController.scanArchives
                        taskSettingsDialog.editMaxWorkers = overrides.max_workers !== undefined
                            ? overrides.max_workers : configController.maxWorkers
                        taskSettingsDialog.editMaxFileSizeMB = overrides.max_file_size !== undefined
                            ? Math.floor(overrides.max_file_size / (1024 * 1024))
                            : configController.maxFileSizeMB
                        taskSettingsDialog.editMaxDepth = overrides.max_depth !== undefined
                            ? overrides.max_depth : configController.maxDepth
                        // ignore_dirs 数组转成多行文本
                        var dirs = overrides.ignore_dirs !== undefined ? overrides.ignore_dirs : []
                        taskSettingsDialog.editIgnoreDirs = Array.isArray(dirs) ? dirs.join("\n") : ""
                        taskSettingsDialog.open()
                    }
                }
                IconButton {
                    iconSource: "qrc:/icons/history.svg"
                    text: "历史"
                    tooltip: "查看扫描历史与对比摘要"
                    accent: "ghost"
                    onClicked: {
                        // 加载历史 JSON 与对比 JSON
                        var histJson = workspaceController.workspaceHistoryJson(card.workspaceId)
                        var cmpJson = workspaceController.compareWithPreviousScan(card.workspaceId)
                        try { historyDialog.historyList = JSON.parse(histJson) } catch(e) { historyDialog.historyList = [] }
                        try { historyDialog.comparison = JSON.parse(cmpJson) } catch(e) { historyDialog.comparison = {} }
                        historyDialog.open()
                    }
                }
                Item { Layout.fillWidth: true }
                IconButton {
                    iconSource: "qrc:/icons/delete.svg"
                    text: "删除"
                    tooltip: "删除该任务"
                    accent: "danger"
                    onClicked: workspaceController.removeWorkspace(card.workspaceId)
                }
            }
        }
    }

    // ---------- 切换目标对话框（iter-104 任务切换扫描目标） ----------
    Dialog {
        id: editTargetDialog
        title: "切换扫描目标"
        modal: true
        anchors.centerIn: parent
        width: 420
        standardButtons: Dialog.Cancel | Dialog.Ok

        // 临时编辑状态
        property int editModeIndex: 2
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
                    text: "全盘扫描"
                    height: theme.btnHeightSecondary
                }
                TabButton {
                    text: "盘符扫描"
                    height: theme.btnHeightSecondary
                }
                TabButton {
                    text: "文件夹扫描"
                    height: theme.btnHeightSecondary
                }
            }

            // 盘符选择（modeIndex === 1）
            RowLayout {
                Layout.fillWidth: true
                visible: editTargetDialog.editModeIndex === 1
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

            // 文件夹选择（modeIndex === 2）
            RowLayout {
                Layout.fillWidth: true
                visible: editTargetDialog.editModeIndex === 2
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
            var modeStr = editTargetDialog.editModeIndex === 0 ? "full"
                       : (editTargetDialog.editModeIndex === 1 ? "drive" : "folder")
            var target = editTargetDialog.editModeIndex === 0 ? ""
                       : (editTargetDialog.editModeIndex === 1 ? editTargetDialog.editDrive : editTargetDialog.editFolder)
            workspaceController.updateWorkspaceTarget(card.workspaceId, modeStr, target)
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

    // 信号：导出 / 任务设置
    signal exportCsvRequested(string workspaceId)
    signal exportJsonRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)

    // ---------- 任务级设置对话框（iter-104 任务专属配置覆盖） ----------
    Dialog {
        id: taskSettingsDialog
        title: "任务级设置 — " + card.taskName
        modal: true
        anchors.centerIn: parent
        width: 460
        standardButtons: Dialog.Cancel | Dialog.Ok

        // 临时编辑状态（初始化时从 taskOverridesJson 读取）
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
                // iter-125：上限改为 cpuCount（与提示一致）
                SpinBox {
                    id: taskMaxWorkersSpin
                    from: 1
                    to: Math.max(configController.cpuCount, 1)
                    value: Math.min(taskSettingsDialog.editMaxWorkers, configController.cpuCount)
                    editable: true
                    onValueChanged: taskSettingsDialog.editMaxWorkers = value
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
                // iter-125：动态步进 <50 步 10，50-100 步 25，>100 步 100
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
                    onValueChanged: taskSettingsDialog.editMaxFileSizeMB = value
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
                    onValueChanged: taskSettingsDialog.editMaxDepth = value
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
            // iter-127：与全局值相同的字段清除覆盖（留空使用全局承诺），
            // 不同的字段才下发 setTaskOverride，避免任务级配置冗余持久化。
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
                    // ignore_dirs 比较复杂（全局含预设分类），空列表清除覆盖，
                    // 非空列表始终下发（用户自定义优先于全局预设）
                    if (cleaned.length === 0) {
                        workspaceController.clearTaskOverride(card.workspaceId, key)
                    } else {
                        workspaceController.setTaskOverride(card.workspaceId, key, JSON.stringify(cleaned))
                    }
                    continue
                }
                // 与全局值相同则清除覆盖，不同则下发
                if (value === globalValue) {
                    workspaceController.clearTaskOverride(card.workspaceId, key)
                } else {
                    workspaceController.setTaskOverride(card.workspaceId, key, JSON.stringify(value))
                }
            }
        }
    }

    // ---------- 扫描历史对话框（iter-115） ----------
    Dialog {
        id: historyDialog
        title: "扫描历史 — " + card.taskName
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
                                            // ISO 格式简化展示：取 YYYY-MM-DD HH:MM:SS 部分
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
                            var removed = workspaceController.clearWorkspaceHistory(card.workspaceId)
                            historyDialog.historyList = []
                            historyDialog.comparison = {}
                        }
                    }
                }
            }
        }
    }
}
