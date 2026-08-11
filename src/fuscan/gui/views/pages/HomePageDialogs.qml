import QtQuick 2.15
import Qt.labs.platform 1.0 as Platform
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// HomePage 对话框组：从 HomePage.qml 抽取的 9 个 Dialog + 4 个 FileDialog。
// 由 HomePage.qml 的 Loader 延迟加载，避免启动时立即构造 1400+ 行不可见对话框。
// 对话框内部通过 dialogsRoot.homePage 访问 HomePage 的属性与函数。
Item {
    id: dialogsRoot
    property var homePage: null
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController
    property RulesControllerType rulesController: RulesController

    // ========== 导出文件保存对话框 ==========
    Platform.FileDialog {
        id: exportCsvDialog
        title: "导出扫描结果为 CSV"
        fileMode: Platform.FileDialog.SaveFile
        defaultSuffix: "csv"
        nameFilters: ["CSV (*.csv)"]
        onAccepted: {
            var path = exportCsvDialog.file.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(dialogsRoot.homePage._pendingExportWsId, "csv", path)
        }
    }
    Platform.FileDialog {
        id: exportJsonDialog
        title: "导出扫描结果为 JSON"
        fileMode: Platform.FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON (*.json)"]
        onAccepted: {
            var path = exportJsonDialog.file.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(dialogsRoot.homePage._pendingExportWsId, "json", path)
        }
    }
    Platform.FileDialog {
        id: exportPdfDialog
        title: "导出扫描结果为 PDF"
        fileMode: Platform.FileDialog.SaveFile
        defaultSuffix: "pdf"
        nameFilters: ["PDF (*.pdf)"]
        onAccepted: {
            var path = exportPdfDialog.file.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(dialogsRoot.homePage._pendingExportWsId, "pdf", path)
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
            workspaceController.updateWorkspaceTarget(dialogsRoot.homePage._pendingEditTargetWsId, modeStr, target)
        }
    }

    // 切换目标对话框用的文件夹选择器
    Platform.FolderDialog {
        id: folderDialogForEdit
        title: "选择扫描目录"
        folder: editTargetDialog.editFolder.length > 0
            ? "file:///" + editTargetDialog.editFolder
            : Platform.StandardPaths.standardLocations(Platform.StandardPaths.HomeLocation)[0]
        onAccepted: {
            editTargetDialog.editFolder = folderDialogForEdit.folder.toString().replace(/^file:\/\/\//, "")
        }
    }

    // 文件扫描页「添加文件夹」按钮用的文件夹选择器（单选）。
    // 改用 Qt.labs.platform FolderDialog（Windows 原生 IFileOpenDialog），
    // 避免 QtQuick.Dialogs 1.3 DefaultFileDialog.qml 的 binding loop 警告。
    // 多文件夹场景由用户多次点击「添加」按钮完成；addWorkspacesFromPaths
    // 接受单元素列表，调用方式不变。
    Platform.FolderDialog {
        id: folderDialogForAdd
        title: "选择要扫描的文件夹"
        folder: Platform.StandardPaths.standardLocations(Platform.StandardPaths.HomeLocation)[0]
        onAccepted: {
            var paths = []
            var url = folderDialogForAdd.folder.toString()
            if (url.startsWith("file:///")) {
                paths.push(decodeURIComponent(url.substring(8)))
            }
            if (paths.length > 0) {
                var count = workspaceController.addWorkspacesFromPaths(paths)
                if (count === 0) {
                    dialogsRoot.homePage._showDropToast("选择的目标不是文件或文件夹", false)
                } else {
                    dialogsRoot.homePage._showDropToast("已添加 " + count + " 个扫描任务", true)
                }
            }
        }
    }

    // ========== 配置规则对话框（共享单例，单一列表：内置→全局→临时） ==========
    // 单一 ListView 直接绑定 rulesController.rulesFileModel，所有操作立即生效：
    // - 勾选启用/禁用：内置/全局规则操作 Config，临时规则操作 task_overrides.disabled_temp_rules_paths
    // - 加载到全局：loadFileFromPath 立即生效，所有任务共享
    // - 加载到临时：loadFileToTemp 立即生效，仅当前工作区生效
    // - 上移/下移/移除：均立即生效
    // 作用域标签：内置=蓝 / 全局=蓝 / 临时=绿（colorPrimary / colorSuccess）
    Dialog {
        id: configureRulesDialog
        title: "配置规则 — " + workspaceController.workspaceName(dialogsRoot.homePage._pendingConfigureRulesWsId)
        modal: true
        anchors.centerIn: parent
        width: 600
        height: 620
        // 所有操作立即生效，确定按钮仅用于关闭对话框
        standardButtons: Dialog.Close

        contentItem: ColumnLayout {
            spacing: 10

            // 顶部说明
            Label {
                text: "同规则条件覆盖上方，不同规则采取并集。"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }

            // 规则文件列表（内置 + 全局 + 临时合并）
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: 8

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    ListView {
                        id: configureRulesFileList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        cacheBuffer: 200
                        model: rulesController.rulesFileModel
                        currentIndex: rulesController.selectedFileIndex
                        onCurrentIndexChanged: rulesController.setSelectedFileIndex(currentIndex)
                        delegate: ItemDelegate {
                            id: ruleFileDelegate
                            width: configureRulesFileList.width
                            height: 40
                            // QVariantList of dict 通过 modelData 访问字段
                            highlighted: ListView.isCurrentItem
                            // 文件缺失时禁用（内置规则恒存在）
                            enabled: modelData.exists
                            onClicked: configureRulesFileList.currentIndex = index
                            background: Rectangle {
                                color: ListView.isCurrentItem
                                    ? Qt.rgba(theme.colorPrimary.r,
                                              theme.colorPrimary.g,
                                              theme.colorPrimary.b,
                                              0.15)
                                    : (parent.hovered
                                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                        : "transparent")
                                Behavior on color { ColorAnimation { duration: 120 } }
                                // 左侧 3px 色条强调选中态
                                Rectangle {
                                    visible: ListView.isCurrentItem
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: 3
                                    color: theme.colorPrimary
                                }
                            }
                            contentItem: RowLayout {
                                spacing: 8

                                // 启用/禁用勾选框（所有规则均可勾选，含临时规则）
                                CheckBox {
                                    checked: modelData.enabled
                                    onClicked: {
                                        rulesController.setRuleEnabled(
                                            modelData.path, checked)
                                    }
                                    Layout.alignment: Qt.AlignVCenter
                                }

                                // 文件名
                                Label {
                                    text: modelData.fileName
                                    font.pixelSize: 12
                                    font.bold: ListView.isCurrentItem
                                    color: ListView.isCurrentItem
                                        ? theme.colorPrimary
                                        : (modelData.exists
                                            ? (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                                            : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                                    elide: Text.ElideMiddle
                                    Layout.fillWidth: true
                                    verticalAlignment: Text.AlignVCenter
                                }

                                // 作用域标签：内置=蓝 / 全局=蓝 / 临时=绿
                                Rectangle {
                                    radius: 4
                                    height: 18
                                    width: scopeLabel.implicitWidth + 12
                                    color: modelData.isBuiltin
                                        ? theme.colorPrimary
                                        : (modelData.scope === "temp"
                                            ? theme.colorSuccess
                                            : theme.colorPrimary)
                                    Layout.alignment: Qt.AlignVCenter
                                    Label {
                                        id: scopeLabel
                                        anchors.centerIn: parent
                                        text: modelData.isBuiltin
                                            ? "内置"
                                            : (modelData.scope === "temp" ? "临时" : "全局")
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: "#FFFFFF"
                                    }
                                }

                                // 缺失文件显示"缺失"标记
                                Rectangle {
                                    visible: !modelData.exists
                                    radius: 4
                                    height: 18
                                    width: missingLabel.implicitWidth + 12
                                    color: theme.colorDanger
                                    Layout.alignment: Qt.AlignVCenter
                                    Label {
                                        id: missingLabel
                                        anchors.centerIn: parent
                                        text: "缺失"
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: "#FFFFFF"
                                    }
                                }

                                // 移除按钮（内置规则不显示）
                                IconButton {
                                    iconSource: "qrc:/icons/close.svg"
                                    tooltip: "移除该规则文件"
                                    accent: "ghost"
                                    // 与 delegate 行高(40) 内的其他元素（Label~16/Rectangle 18/CheckBox~24）对齐，
                                    // 默认 btnSize=40 会撑满整行导致按钮顶部/底部超出同行元素
                                    btnSize: 24
                                    visible: modelData.canRemove
                                    Layout.alignment: Qt.AlignVCenter
                                    Layout.rightMargin: 4
                                    onClicked: {
                                        configureRulesFileList.currentIndex = index
                                        rulesController.removeSelected()
                                    }
                                }
                            }
                        }
                    }

                    // 操作行：加载到全局 / 加载到临时 / 上移 / 下移 / 移除
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        IconButton {
                            iconSource: "qrc:/icons/load_list.svg"
                            text: "加载到全局"
                            tooltip: "从文件选择规则文件加载到全局规则（所有任务共享，立即生效）"
                            accent: "secondary"
                            onClicked: loadGlobalRulesFileDialog.open()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/load_list.svg"
                            text: "加载到临时"
                            tooltip: "从文件选择规则文件加载到当前工作区临时规则（仅对该任务生效，立即生效）"
                            accent: "ghost"
                            enabled: rulesController.hasCurrentWorkspace
                            onClicked: loadTempRulesFileDialog.open()
                        }
                        Item { Layout.fillWidth: true }
                        IconButton {
                            iconSource: "qrc:/icons/up_arrow.svg"
                            tooltip: "上移选中全局规则文件"
                            accent: "ghost"
                            enabled: rulesController.canMoveUp
                            onClicked: rulesController.moveUp()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/down_arrow.svg"
                            tooltip: "下移选中全局规则文件"
                            accent: "ghost"
                            enabled: rulesController.canMoveDown
                            onClicked: rulesController.moveDown()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/minus.svg"
                            tooltip: "移除选中规则文件"
                            accent: "ghost"
                            enabled: rulesController.canRemove
                            onClicked: rulesController.removeSelected()
                        }
                    }

                    // 提示文字
                    Label {
                        Layout.fillWidth: true
                        text: rulesController.hasCurrentWorkspace
                            ? "勾选启用/禁用所有规则（含临时规则）；上移/下移仅作用于全局规则文件排序"
                            : "未选择工作区：加载到临时、临时规则启用/禁用需先在文件扫描页选择工作区"
                        font.pixelSize: 10
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        font.italic: true
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
        // 所有规则操作立即生效，关闭对话框无需额外应用动作（standardButtons: Dialog.Close）
    }

    // ========== 预览规则对话框（共享单例，只读展示当前任务 effective 规则集） ==========
    // 由 WorkspaceCard.onPreviewRulesRequested 触发，调用
    // rulesController.previewRuleset(wsId) 取 JSON 后填充各分区只读展示。
    // 采用 TabBar + StackLayout 分页布局，2 个 Tab：
    //   扫描设置（扫描参数 + 忽略目录 + 白名单）/ 规则信息（规则文件 + 匹配规则）
    Dialog {
        id: previewRulesDialog
        title: "预览规则 — " + workspaceController.workspaceName(dialogsRoot.homePage._pendingPreviewRulesWsId)
        modal: true
        anchors.centerIn: parent
        width: 880
        height: 640
        standardButtons: Dialog.Close

        // 解析后的预览数据（由 onPreviewRulesRequested 调用 Slot 后赋值）
        property var previewData: ({})

        contentItem: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: theme.radiusMd

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 0
                spacing: 0

                TabBar {
                    id: previewTabBar
                    Layout.fillWidth: true
                    currentIndex: 0
                    TabButton { text: "扫描设置" }
                    TabButton { text: "规则信息" }
                }

                StackLayout {
                    id: previewStack
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: previewTabBar.currentIndex

                    // ---------- Tab 1: 扫描设置（扫描参数 + 忽略目录 + 白名单） ----------
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        topPadding: 16
                        bottomPadding: 16
                        leftPadding: 20
                        rightPadding: 20
                        ColumnLayout {
                            width: previewStack.width - 40
                            spacing: 16

                            // ----- 分区：扫描参数（瀑布标签） -----
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "扫描参数"
                                    font.pixelSize: theme.fontSizeHeading
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                // 瀑布标签：每项一个圆角矩形，Flow 自动换行，比 GridLayout 两列更紧凑
                                // 每个标签格式：`中文标签 (config_name: 值)`，斜体不加粗，
                                // 让用户直观看到对应的 YAML 配置项与当前生效值
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spScanArchives.implicitWidth + 16
                                        height: spScanArchives.implicitHeight + 8
                                        Label {
                                            id: spScanArchives
                                            anchors.centerIn: parent
                                            text: "扫描压缩包 (scan_archives: <span style=\"color:" + (previewRulesDialog.previewData.scanArchives === true ? dialogsRoot.homePage._rgb(theme.colorSuccess) : dialogsRoot.homePage._rgb(theme.colorDanger)) + "\">" + (previewRulesDialog.previewData.scanArchives === true ? "是" : "否") + "</span>)"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spMaxWorkers.implicitWidth + 16
                                        height: spMaxWorkers.implicitHeight + 8
                                        Label {
                                            id: spMaxWorkers
                                            anchors.centerIn: parent
                                            text: "最大工作线程 (max_workers: " + (previewRulesDialog.previewData.maxWorkers !== undefined
                                                  ? previewRulesDialog.previewData.maxWorkers : "—") + ")"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spMaxFileSize.implicitWidth + 16
                                        height: spMaxFileSize.implicitHeight + 8
                                        Label {
                                            id: spMaxFileSize
                                            anchors.centerIn: parent
                                            text: "最大文件大小 (max_file_size: " + (previewRulesDialog.previewData.maxFileSizeMB !== undefined
                                                  ? previewRulesDialog.previewData.maxFileSizeMB + " MB" : "—") + ")"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spMaxDepth.implicitWidth + 16
                                        height: spMaxDepth.implicitHeight + 8
                                        Label {
                                            id: spMaxDepth
                                            anchors.centerIn: parent
                                            text: "最大扫描深度 (max_depth: " + (previewRulesDialog.previewData.maxDepth !== undefined
                                                  ? previewRulesDialog.previewData.maxDepth : "—") + ")"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spCacheEnabled.implicitWidth + 16
                                        height: spCacheEnabled.implicitHeight + 8
                                        Label {
                                            id: spCacheEnabled
                                            anchors.centerIn: parent
                                            text: "启用扫描结果缓存 (cache_enabled: <span style=\"color:" + (previewRulesDialog.previewData.cacheEnabled === true ? dialogsRoot.homePage._rgb(theme.colorSuccess) : dialogsRoot.homePage._rgb(theme.colorDanger)) + "\">" + (previewRulesDialog.previewData.cacheEnabled === true ? "是" : "否") + "</span>)"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        width: spPerfLog.implicitWidth + 16
                                        height: spPerfLog.implicitHeight + 8
                                        Label {
                                            id: spPerfLog
                                            anchors.centerIn: parent
                                            text: "启用性能详细日志 (perf_log_enabled: <span style=\"color:" + (previewRulesDialog.previewData.perfLogEnabled === true ? dialogsRoot.homePage._rgb(theme.colorSuccess) : dialogsRoot.homePage._rgb(theme.colorDanger)) + "\">" + (previewRulesDialog.previewData.perfLogEnabled === true ? "是" : "否") + "</span>)"
                                            textFormat: Text.RichText
                                            font.pixelSize: 11
                                            font.italic: true
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                        }
                                    }
                                }
                            }

                            // 文件类型分区已下沉到下方「规则文件」列表条目上展示，
                            // 让用户直观看到每个规则文件贡献/覆盖的 scan_extensions，
                            // 避免与规则文件列表割裂。

                            // ----- 分区：忽略目录（瀑布标签） -----
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Label {
                                    text: "忽略目录（" + (previewRulesDialog.previewData.ignoreDirs
                                                      ? previewRulesDialog.previewData.ignoreDirs.length : 0) + " 项）"
                                    font.pixelSize: theme.fontSizeHeading
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: !previewRulesDialog.previewData.ignoreDirs
                                             || previewRulesDialog.previewData.ignoreDirs.length === 0
                                    text: "（暂无忽略目录）"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                                // 每个目录一个圆角标签，Flow 自动换行
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    visible: !!(previewRulesDialog.previewData.ignoreDirs
                                             && previewRulesDialog.previewData.ignoreDirs.length > 0)
                                    Repeater {
                                        model: previewRulesDialog.previewData.ignoreDirs || []
                                        delegate: Rectangle {
                                            radius: 4
                                            color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                                            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                            border.width: 1
                                            width: ignoreDirTag.implicitWidth + 16
                                            height: ignoreDirTag.implicitHeight + 8
                                            Label {
                                                id: ignoreDirTag
                                                anchors.centerIn: parent
                                                text: modelData
                                                font.pixelSize: 11
                                                font.family: "Consolas, Monaco, monospace"
                                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                            }
                                        }
                                    }
                                }
                            }

                            // ----- 分区：白名单 -----
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    text: "白名单（" + (previewRulesDialog.previewData.whitelistEntries
                                                      ? previewRulesDialog.previewData.whitelistEntries.length : 0) + " 项）"
                                    font.pixelSize: theme.fontSizeHeading
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: !!(previewRulesDialog.previewData.whitelistEntries
                                             && previewRulesDialog.previewData.whitelistEntries.length === 0)
                                    text: "（暂无白名单条目）"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                                Repeater {
                                    model: previewRulesDialog.previewData.whitelistEntries || []
                                    delegate: Rectangle {
                                        Layout.fillWidth: true
                                        // 高度与规则文件列表项保持一致（28）
                                        height: 28
                                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 8
                                            anchors.rightMargin: 8
                                            spacing: 8
                                            Label {
                                                text: modelData.pathGlob
                                                font.pixelSize: 12
                                                font.family: "Consolas, Monaco, monospace"
                                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                                Layout.fillWidth: true
                                                elide: Text.ElideMiddle
                                            }
                                            Rectangle {
                                                radius: theme.radiusSm
                                                color: modelData.ruleName === "*" ? theme.colorPrimary : theme.colorWarning
                                                width: prWlRuleTag.implicitWidth + 12
                                                height: prWlRuleTag.implicitHeight + 4
                                                Label {
                                                    id: prWlRuleTag
                                                    anchors.centerIn: parent
                                                    text: modelData.ruleName === "*" ? "全部规则" : modelData.ruleName
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                    color: theme.colorTextOnPrimary
                                                }
                                            }
                                            Rectangle {
                                                visible: modelData.source !== undefined && modelData.source !== ""
                                                radius: theme.radiusSm
                                                color: modelData.source === "rules" ? theme.colorSuccess : theme.colorWarning
                                                width: prWlSrcTag.implicitWidth + 10
                                                height: prWlSrcTag.implicitHeight + 4
                                                Label {
                                                    id: prWlSrcTag
                                                    anchors.centerIn: parent
                                                    text: modelData.source === "rules" ? "规则" : "运行时"
                                                    font.pixelSize: 9
                                                    font.bold: true
                                                    color: theme.colorTextOnPrimary
                                                }
                                            }
                                            Label {
                                                text: modelData.note || ""
                                                font.pixelSize: 10
                                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                                visible: text.length > 0
                                                elide: Text.ElideRight
                                                Layout.maximumWidth: 200
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ---------- Tab 2: 规则信息（规则文件 + 匹配规则） ----------
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        topPadding: 16
                        bottomPadding: 16
                        leftPadding: 20
                        rightPadding: 20
                        ColumnLayout {
                            width: previewStack.width - 40
                            spacing: 16

                            // ----- 分区：规则文件 -----
                            // 用 Column（Positioner）而非 ColumnLayout：
                            // Positioner 严格尊重子项的 height 属性，不做启发式布局；
                            // ColumnLayout 对 Rectangle 的 height 管理基于 Layout.preferredHeight
                            // （Rectangle.implicitHeight 默认 0），可能与显式 height 脱节导致留白
                            Column {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    width: parent.width
                                    text: "规则文件（" + (previewRulesDialog.previewData.ruleFiles
                                                      ? previewRulesDialog.previewData.ruleFiles.length : 0) + " 项）"
                                    font.pixelSize: theme.fontSizeHeading
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    width: parent.width
                                    text: "作用域：内置=蓝 / 全局=蓝 / 临时=绿；灰色文字表示文件缺失；「类型」行显示该规则文件自身的扫描后缀（未设置=继承前序，都不扫描=空覆盖，标签=具体后缀）"
                                    font.pixelSize: 10
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    font.italic: true
                                    wrapMode: Text.WordWrap
                                }
                                Repeater {
                                    model: previewRulesDialog.previewData.ruleFiles || []
                                    delegate: Rectangle {
                                        width: parent.width
                                        // 用 Column + col.height 紧贴实际内容高度。
                                        // 必须用 col.height 而非 childrenRect.height：
                                        // Column（Positioner）只定位 visible:true 的子项，
                                        // col.height 自动反映可见子项排列后的总高度；
                                        // 而 childrenRect 包含所有子项（含 visible:false 的 Label/Flow），
                                        // visible:false 的 Label 仍有 implicitHeight，导致多算不可见子项高度
                                        height: col.height + 8
                                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                        Column {
                                            id: col
                                            x: 8
                                            y: 4
                                            width: parent.width - 16
                                            spacing: 2
                                            // 第一行：启用灯 + 文件名 + 作用域 + 缺失标签
                                            RowLayout {
                                                id: infoRow
                                                width: parent.width
                                                spacing: 8
                                                // 启用状态指示灯：启用=绿，禁用=灰
                                                Rectangle {
                                                    width: 8
                                                    height: 8
                                                    radius: 4
                                                    color: modelData.enabled ? theme.colorSuccess
                                                        : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                                                    Layout.alignment: Qt.AlignVCenter
                                                }
                                                Label {
                                                    text: modelData.fileName
                                                    font.pixelSize: 12
                                                    color: modelData.exists
                                                        ? (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                                                        : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                                                    Layout.fillWidth: true
                                                    elide: Text.ElideMiddle
                                                }
                                                Rectangle {
                                                    radius: 4
                                                    height: 18
                                                    width: prScopeTag.implicitWidth + 12
                                                    color: modelData.isBuiltin
                                                        ? theme.colorPrimary
                                                        : (modelData.scope === "temp" ? theme.colorSuccess : theme.colorPrimary)
                                                    Label {
                                                        id: prScopeTag
                                                        anchors.centerIn: parent
                                                        text: modelData.isBuiltin
                                                            ? "内置"
                                                            : (modelData.scope === "temp" ? "临时" : "全局")
                                                        font.pixelSize: 10
                                                        font.bold: true
                                                        color: "#FFFFFF"
                                                    }
                                                }
                                                Rectangle {
                                                    visible: !modelData.exists
                                                    radius: 4
                                                    height: 18
                                                    width: prMissingTag.implicitWidth + 12
                                                    color: theme.colorDanger
                                                    Label {
                                                        id: prMissingTag
                                                        anchors.centerIn: parent
                                                        text: "缺失"
                                                        font.pixelSize: 10
                                                        font.bold: true
                                                        color: "#FFFFFF"
                                                    }
                                                }
                                            }
                                            // 第二行：文件类型标签（来自该规则文件自身的 scan_extensions）
                                            // state="unset" → 未设置（继承前序，不显示该行）
                                            // state="none"  → 都不扫描
                                            // state="list"  → 后缀标签 Flow
                                            // state="none"：显示"都不扫描"提示（红色）
                                            Label {
                                                width: parent.width
                                                visible: modelData.scanExtensionsState === "none"
                                                text: "都不扫描"
                                                font.pixelSize: 10
                                                font.italic: true
                                                color: theme.colorDanger
                                                wrapMode: Text.WordWrap
                                            }
                                            // state="list"：后缀标签 Flow，自动换行
                                            Flow {
                                                width: parent.width
                                                visible: modelData.scanExtensionsState === "list"
                                                spacing: 3
                                                Repeater {
                                                    model: modelData.scanExtensions || []
                                                    delegate: Rectangle {
                                                        radius: 3
                                                        color: Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                                        border.width: 1
                                                        width: prExtTag.implicitWidth + 8
                                                        height: prExtTag.implicitHeight + 2
                                                        Label {
                                                            id: prExtTag
                                                            anchors.centerIn: parent
                                                            text: "." + modelData
                                                            font.pixelSize: 10
                                                            font.family: "Consolas, Monaco, monospace"
                                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // ----- 分区：匹配规则 -----
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    text: "匹配规则（" + (previewRulesDialog.previewData.rules
                                                      ? previewRulesDialog.previewData.rules.length : 0) + " 条）"
                                    font.pixelSize: theme.fontSizeHeading
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "严重度：红=高危 / 橙=中危 / 黄=低危；「可替换」标签表示命中后可自动脱敏"
                                    font.pixelSize: 10
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    font.italic: true
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: !!(previewRulesDialog.previewData.rules
                                             && previewRulesDialog.previewData.rules.length === 0)
                                    text: "（暂无匹配规则，请检查规则文件是否启用或加载）"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                                Repeater {
                                    model: previewRulesDialog.previewData.rules || []
                                    delegate: Rectangle {
                                        Layout.fillWidth: true
                                        // 单行布局：与规则文件条目同高 28px，description 紧贴 name 右侧
                                        height: 28
                                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                        RowLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 8
                                            anchors.rightMargin: 8
                                            spacing: 8
                                            Label {
                                                text: modelData.name
                                                font.pixelSize: 12
                                                font.bold: true
                                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                                elide: Text.ElideRight
                                                Layout.maximumWidth: 220
                                            }
                                            Label {
                                                text: modelData.description || ""
                                                font.pixelSize: 10
                                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                                visible: text.length > 0
                                            }
                                            Rectangle {
                                                radius: 4
                                                height: 18
                                                width: prSevTag.implicitWidth + 12
                                                color: modelData.severityColor
                                                Layout.alignment: Qt.AlignVCenter
                                                Label {
                                                    id: prSevTag
                                                    anchors.centerIn: parent
                                                    text: modelData.severityText
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                    color: "#FFFFFF"
                                                }
                                            }
                                            Rectangle {
                                                visible: modelData.replace === true
                                                radius: 4
                                                height: 18
                                                width: prReplaceTag.implicitWidth + 12
                                                color: theme.colorPrimary
                                                Layout.alignment: Qt.AlignVCenter
                                                Label {
                                                    id: prReplaceTag
                                                    anchors.centerIn: parent
                                                    text: "可替换"
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                    color: "#FFFFFF"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // 全局规则文件选择器（加载到全局，立即生效）
    Platform.FileDialog {
        id: loadGlobalRulesFileDialog
        title: "选择规则文件（加载到全局）"
        fileMode: Platform.FileDialog.OpenFile
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = loadGlobalRulesFileDialog.file.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.loadFileFromPath(pathStr)
        }
    }

    // 临时规则文件选择器（加载到当前工作区，立即生效）
    Platform.FileDialog {
        id: loadTempRulesFileDialog
        title: "选择规则文件（加载到临时）"
        fileMode: Platform.FileDialog.OpenFile
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = loadTempRulesFileDialog.file.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.loadFileToTemp(pathStr)
        }
    }

    // ========== 扫描历史对话框（共享单例） ==========
    Dialog {
        id: historyDialog
        title: "扫描历史 — " + workspaceController.workspaceName(dialogsRoot.homePage._pendingHistoryWsId)
        modal: true
        anchors.centerIn: parent
        width: 640
        height: 520
        standardButtons: Dialog.Close

        // 历史列表（按时间倒序）
        property var historyList: []
        // 对比结果对象（current/previous/summary/trend/...）
        // 用 ({}) 明确空对象字面量——裸 {} 会被 QML 解析为空块语句使属性为 undefined
        property var comparison: ({})

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
                            workspaceController.clearWorkspaceHistory(dialogsRoot.homePage._pendingHistoryWsId)
                            historyDialog.historyList = []
                            historyDialog.comparison = {}
                        }
                    }
                }
            }
        }
    }
}
