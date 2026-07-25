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

    // 当前待导出的格式与目标工作区
    property string _pendingExportFmt: ""
    property string _pendingExportWsId: ""

    // 导出文件保存对话框
    FileDialog {
        id: exportDialog
        title: "导出扫描结果"
        selectExisting: false
        defaultSuffix: homePage._pendingExportFmt
        nameFilters: [
            homePage._pendingExportFmt === "csv"
                ? "CSV 文件 (*.csv)"
                : "JSON 文件 (*.json)"
        ]
        onAccepted: {
            var path = exportDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            workspaceController.exportResults(homePage._pendingExportWsId, homePage._pendingExportFmt, path)
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

            // 居中展示进度卡片，避免内容贴边
            ColumnLayout {
                width: parent.width
                spacing: 12

                Item { Layout.fillHeight: true; Layout.preferredHeight: 40 }

                ScanProgressCard {
                    Layout.fillWidth: true
                    Layout.maximumWidth: 720
                    Layout.alignment: Qt.AlignHCenter
                    workspaceId: workspaceController.activeScanWorkspaceId
                    taskName: workspaceController.activeScanWorkspaceName
                    modeText: workspaceController.activeScanModeText
                    target: workspaceController.activeScanTarget
                }

                // 提示：扫描结束后自动恢复工作区列表
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "扫描结束后自动恢复工作区列表"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }

                Item { Layout.fillHeight: true; Layout.preferredHeight: 40 }
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
                    statusText: model.statusText
                    matchedCount: model.matchedCount
                    passedCount: model.passedCount
                    skippedCount: model.skippedCount
                    errorCount: model.errorCount
                    lastSummary: model.lastSummary

                    onDefineRulesRequested: function(wsId) {
                        // 切换到规则页：通过 sidebar 引用
                        // 由于 HomePage 无法直接访问 sidebar，用全局 currentPage 切换
                        // 这里通过 root.currentPage 间接切换（root 为 ApplicationWindow）
                        // 实际实现：依赖父级组件传递 currentPage 属性
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
                        homePage._pendingExportFmt = "csv"
                        homePage._pendingExportWsId = wsId
                        exportDialog.open()
                    }
                    onExportJsonRequested: function(wsId) {
                        homePage._pendingExportFmt = "json"
                        homePage._pendingExportWsId = wsId
                        exportDialog.open()
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
