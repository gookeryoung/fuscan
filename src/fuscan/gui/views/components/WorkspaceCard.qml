import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 工作区卡片：单任务展示与操作
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

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

    // 状态色：根据 statusText 决定
    function statusColor() {
        if (statusText === "扫描中") return theme.colorWarning
        if (statusText === "已暂停") return theme.colorTextSecondary
        if (statusText === "已完成") return (matchedCount > 0 ? theme.colorDanger : theme.colorSuccess)
        // 用户取消：红色（保持与命中相同的警示色）
        if (statusText === "已完成[用户取消]") return theme.colorDanger
        if (statusText === "失败" || statusText === "已取消") return theme.colorWarning
        // 就绪：蓝色（非灰色），表示待命可操作
        return theme.colorPrimary
    }

    // 是否处于已完成态（含用户取消）：控制「更新扫描」「查看结果」按钮启用
    function isCompletedState() {
        return statusText === "已完成" || statusText === "已完成[用户取消]"
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

            Label {
                text: "📋"
                font.pixelSize: 16
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
                text:"🔧 定义规则"
                tooltip: "编辑该任务的规则集"
                accent: "secondary"
                onClicked: card.defineRulesRequested(card.workspaceId)
            }
            IconButton {
                text: statusText === "扫描中" ? "⏸ 暂停" : "▶ 启动扫描"
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
                text:"🔄 更新扫描"
                tooltip: "对已完成扫描的任务重新扫描"
                accent: "secondary"
                // 已完成（含用户取消）的工作区可重新扫描
                enabled: card.isCompletedState()
                onClicked: workspaceController.startScan(card.workspaceId)
            }
            IconButton {
                text:"📊 查看结果"
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
                text:"📈 统计"
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
                    Image {
                        source: "file:///" + theme.iconsDir + "/more.svg"
                        sourceSize: Qt.size(14, 14)
                        anchors.verticalCenter: parent.verticalCenter
                        // SVG 颜色跟随主题（Qt 5.15 ColorOverlay 不可用，用 opacity 区分）
                        opacity: theme.isDark ? 0.9 : 1.0
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
                    text:"🎯 切换目标"
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
                IconButton {
                    text:"📄 CSV"
                    tooltip: "导出为 CSV"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportCsvRequested(card.workspaceId)
                }
                IconButton {
                    text:"📦 JSON"
                    tooltip: "导出为 JSON"
                    accent: "ghost"
                    enabled: matchedCount > 0
                    onClicked: exportJsonRequested(card.workspaceId)
                }
                IconButton {
                    text:"⚙ 设置"
                    tooltip: "任务级设置（仅对该任务生效）"
                    accent: "ghost"
                    onClicked: {
                        // 从 workspaceController 读取当前任务级覆盖
                        var jsonStr = workspaceController.taskOverridesJson(card.workspaceId)
                        var overrides = {}
                        try { overrides = JSON.parse(jsonStr) } catch(e) { overrides = {} }
                        // 全局配置作为默认值（未覆盖时显示全局值）
                        taskSettingsDialog.editScanArchives = overrides.scan_archives !== undefined
                            ? overrides.scan_archives : ConfigController.scanArchives
                        taskSettingsDialog.editMaxWorkers = overrides.max_workers !== undefined
                            ? overrides.max_workers : ConfigController.maxWorkers
                        taskSettingsDialog.editMaxFileSizeMB = overrides.max_file_size !== undefined
                            ? Math.floor(overrides.max_file_size / (1024 * 1024))
                            : ConfigController.maxFileSizeMB
                        taskSettingsDialog.editMaxDepth = overrides.max_depth !== undefined
                            ? overrides.max_depth : ConfigController.maxDepth
                        // ignore_dirs 数组转成多行文本
                        var dirs = overrides.ignore_dirs !== undefined ? overrides.ignore_dirs : []
                        taskSettingsDialog.editIgnoreDirs = Array.isArray(dirs) ? dirs.join("\n") : ""
                        taskSettingsDialog.open()
                    }
                }
                Item { Layout.fillWidth: true }
                IconButton {
                    text:"🗑 删除"
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
                    model: ConfigController.drives
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
                    visible: ConfigController.drives.length === 0
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
                    text:"📁 选择"
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
                    text: "当前机器最大线程=" + ConfigController.cpuCount
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                SpinBox {
                    from: 1
                    to: 16
                    value: taskSettingsDialog.editMaxWorkers
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
                SpinBox {
                    from: 1
                    to: 1024
                    value: taskSettingsDialog.editMaxFileSizeMB
                    editable: true
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
            // 提交所有覆盖（与全局值不同才提交，避免无意义持久化）
            var keys = ["scan_archives", "max_workers", "max_file_size", "max_depth", "ignore_dirs"]
            for (var i = 0; i < keys.length; i++) {
                var key = keys[i]
                var value
                if (key === "scan_archives") value = taskSettingsDialog.editScanArchives
                else if (key === "max_workers") value = taskSettingsDialog.editMaxWorkers
                else if (key === "max_file_size") value = taskSettingsDialog.editMaxFileSizeMB * 1024 * 1024
                else if (key === "max_depth") value = taskSettingsDialog.editMaxDepth
                else if (key === "ignore_dirs") {
                    var lines = taskSettingsDialog.editIgnoreDirs.split("\n")
                    var cleaned = []
                    for (var j = 0; j < lines.length; j++) {
                        var line = lines[j].trim()
                        if (line.length > 0) cleaned.push(line)
                    }
                    value = cleaned
                }
                workspaceController.setTaskOverride(card.workspaceId, key, JSON.stringify(value))
            }
        }
    }
}
