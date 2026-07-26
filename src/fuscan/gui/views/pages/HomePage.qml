import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 首页：工作区列表
Item {
    id: homePage
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController
    property RulesControllerType rulesController: RulesController

    // 当前待导出的目标工作区
    property string _pendingExportWsId: ""

    // CSV 导出文件保存对话框（静态 nameFilters 避免 Windows 原生对话框过滤器乱码）
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

    // JSON 导出文件保存对话框
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

    // 清空所有工作区确认对话框
    MessageDialog {
        id: clearConfirmDialog
        title: "清空所有工作区"
        text: "将移除全部 " + workspaceController.workspaceCount + " 个任务及其扫描结果，此操作不可撤销。是否继续？"
        icon: StandardIcon.Warning
        standardButtons: StandardButton.Ok | StandardButton.Cancel
        onAccepted: {
            var ok = workspaceController.clearAllWorkspaces()
            if (!ok) {
                clearResultDialog.open()
            }
        }
    }

    // 清空结果提示对话框（扫描中拒绝清空时显示）
    MessageDialog {
        id: clearResultDialog
        title: "无法清空"
        text: "有任务正在扫描，请等待扫描结束或取消后再试。"
        icon: StandardIcon.Information
        standardButtons: StandardButton.Ok
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
            // 清空按钮：仅在有任务且无扫描进行时显示，避免误清空运行中任务
            Button {
                visible: !workspaceController.hasActiveScan && workspaceController.workspaceCount > 0
                text: "清空"
                flat: true
                // L3 辅助操作：32px 高度，扁平兜底
                implicitHeight: 32
                font.pixelSize: theme.fontSizeSmall
                palette.buttonText: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
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

            // 用 anchors 而非 ColumnLayout：Rectangle 无 implicitWidth，
            // Layout.fillWidth 在 ColumnLayout 内可能给 0 宽度；
            // anchors.left/right 显式占满视口宽度，与工作区列表视觉宽度一致
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
                // iter-106 P1：预渲染屏幕外 delegate，避免滚动时 WorkspaceCard 重建
                cacheBuffer: 500
                implicitHeight: contentHeight

                // 空态引导
                Label {
                    anchors.centerIn: parent
                    visible: workspaceList.count === 0
                    text: "暂无任务\n点击左侧「添加任务」开始"
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
                    rulesText: model.rulesText
                    rulesTags: model.rulesTags
                    statusText: model.statusText
                    matchedCount: model.matchedCount
                    passedCount: model.passedCount
                    skippedCount: model.skippedCount
                    errorCount: model.errorCount
                    collectedCount: model.collectedCount
                    lastSummary: model.lastSummary

                    onDefineRulesRequested: function(wsId) {
                        // iter-107：进入规则页前绑定工作区，使规则编辑仅作用于该工作区
                        workspaceController.bindRulesController(wsId)
                        homePage.defineRulesRequested(wsId)
                    }
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
                    onTaskSettingsRequested: function(wsId) {
                        homePage.taskSettingsRequested(wsId)
                    }
                }
            }
        }
    }

    // 信号：通知 ContentArea 切换页面
    signal defineRulesRequested(string workspaceId)
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)
}
