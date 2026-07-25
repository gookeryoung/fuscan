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
                text: "工作区"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "共 " + workspaceController.workspaceCount + " 个任务"
                font.pixelSize: theme.fontSizeSmall
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Button {
                Layout.preferredHeight: theme.btnHeightSecondary
                text: "➕ 新建任务"
                onClicked: homePage.addTaskRequested()
                background: Rectangle {
                    color: parent.down ? theme.colorPrimaryDark : theme.colorPrimary
                    radius: theme.btnRadiusSecondary
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.colorTextOnPrimary
                    font.pixelSize: 12
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }

        // ---------- 工作区列表 ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

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
                    text: "暂无任务\n点击右上角「新建任务」开始"
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
    signal addTaskRequested()
    signal defineRulesRequested(string workspaceId)
    signal viewResultsRequested(string workspaceId)
    signal viewStatsRequested(string workspaceId)
    signal taskSettingsRequested(string workspaceId)
}
