import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 扫描页：内嵌 StackView 三态切换（setup/scanning/results）
Item {
    id: scanPage
    property ThemeController theme: Theme
    property ScanControllerType scanController: ScanController
    property ConfigControllerType configController: ConfigController

    // 当前待导出的格式（FileDialog 选定路径后传入 controller）
    property string _pendingExportFmt: ""

    // 文件夹选择对话框（替代 QFileDialog，避免 QWidget 依赖）
    FileDialog {
        id: folderDialog
        title: "选择扫描目录"
        selectFolder: true
        selectExisting: true
        folder: scanController.folderRoot.length > 0
                ? "file:///" + scanController.folderRoot
                : shortcuts.home
        onAccepted: scanController.setFolderRoot(folderDialog.fileUrl.toString().replace(/^file:\/\/\//, ""))
    }

    // 导出文件保存对话框
    FileDialog {
        id: exportDialog
        title: "导出扫描结果"
        selectExisting: false
        defaultSuffix: scanPage._pendingExportFmt
        nameFilters: [
            scanPage._pendingExportFmt === "csv"
                ? "CSV 文件 (*.csv)"
                : "JSON 文件 (*.json)"
        ]
        onAccepted: {
            var path = exportDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            scanController.exportResults(scanPage._pendingExportFmt, path)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 20

        // ---------- 标题区 ----------
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "扫描"
                font.pixelSize: 22
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            // 状态徽标
            Rectangle {
                radius: 10
                height: 22
                width: statusText.width + 18
                color: scanController.statusBadgeColor
                border.color: scanController.statusBadgeBorder
                border.width: 1
                Label {
                    id: statusText
                    anchors.centerIn: parent
                    text: scanController.statusText
                    font.pixelSize: 11
                    color: scanController.statusBadgeText
                }
            }
        }

        // ---------- 内嵌 StackView 三态 ----------
        StackView {
            id: scanStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            initialItem: scanController.scanState === "setup" ? setupView
                       : scanController.scanState === "scanning" ? scanningView
                       : resultsView

            Connections {
                target: scanController
                function onScanStateChanged() {
                    switch (scanController.scanState) {
                        case "setup":
                            scanStack.replace(setupView)
                            break
                        case "scanning":
                            scanStack.replace(scanningView)
                            break
                        case "results":
                            scanStack.replace(resultsView)
                            break
                    }
                }
            }
        }
    }

    // ========== 配置态 ==========
    Component {
        id: setupView
        ScrollView {
            clip: true
            ColumnLayout {
                width: scanPage.width - 48
                spacing: 16

                // 扫描模式（分段控件：单击切换，无需下拉）
                TabBar {
                    id: modeTabBar
                    Layout.fillWidth: true
                    currentIndex: scanController.scanModeIndex
                    onCurrentIndexChanged: {
                        // 仅在用户操作导致 currentIndex 与 controller 不一致时同步，避免循环
                        if (currentIndex !== scanController.scanModeIndex) {
                            scanController.setScanModeIndex(currentIndex)
                        }
                    }
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

                // 目标路径（仅盘符/文件夹模式显示）
                Loader {
                    Layout.fillWidth: true
                    active: scanController.scanModeIndex !== 0
                    sourceComponent: scanController.scanModeIndex === 1 ? driveComponent : folderComponent
                }

                // 已加载规则摘要
                Label {
                    text: "已加载 " + scanController.rulesCount + " 条规则"
                    font.pixelSize: 13
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }

                // 扫描按钮
                Button {
                    Layout.fillWidth: true
                    Layout.preferredHeight: theme.btnHeightPrimary
                    text: "开始扫描"
                    enabled: scanController.canStartScan
                    onClicked: scanController.startScan()

                    background: Rectangle {
                        color: parent.enabled
                              ? (parent.down ? theme.colorPrimaryDark : theme.colorPrimary)
                              : theme.colorBorder
                        radius: theme.btnRadiusPrimary
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.colorTextOnPrimary
                        font.pixelSize: 14
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }

    Component {
        id: driveComponent
        ColumnLayout {
            spacing: 8
            Label {
                text: "选择盘符"
                font.pixelSize: 14
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Repeater {
                model: configController.drives
                delegate: Button {
                    Layout.preferredWidth: 80
                    Layout.preferredHeight: 40
                    text: modelData
                    checkable: true
                    checked: scanController.selectedDrive === modelData
                    onClicked: scanController.setSelectedDrive(modelData)
                }
            }
        }
    }

    Component {
        id: folderComponent
        ColumnLayout {
            spacing: 8
            Label {
                text: "扫描目录"
                font.pixelSize: 14
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ComboBox {
                    Layout.fillWidth: true
                    Layout.preferredHeight: theme.btnHeightSecondary
                    model: configController.scanPaths
                    currentIndex: 0
                    onActivated: scanController.setFolderRoot(currentText)
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    text: "选择..."
                    onClicked: folderDialog.open()
                }
            }
        }
    }

    // ========== 扫描中态 ==========
    Component {
        id: scanningView
        ColumnLayout {
            width: scanPage.width - 48
            spacing: 16

            // 进度条
            ProgressBar {
                Layout.fillWidth: true
                Layout.preferredHeight: 8
                from: 0
                to: scanController.progressTotal
                value: scanController.progressScanned
                indeterminate: scanController.progressIndeterminate
            }

            // 当前文件
            Label {
                Layout.fillWidth: true
                text: scanController.currentFile || "准备中..."
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                elide: Text.ElideMiddle
            }

            // 状态摘要
            Label {
                Layout.fillWidth: true
                text: scanController.statusSummary
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }

            // 分类统计
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                Label {
                    text: "<b style='color:#28A745'>已通过 " + scanController.passedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#DC3545'>命中 " + scanController.matchedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#FFC107'>跳过 " + scanController.skippedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#DC3545'>错误 " + scanController.errorCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
            }

            Item { Layout.fillHeight: true }

            // 控制按钮
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    text: scanController.isPaused ? "继续扫描" : "暂停扫描"
                    onClicked: scanController.togglePause()
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    text: "取消扫描"
                    onClicked: scanController.cancelScan()
                }
            }
        }
    }

    // ========== 结果态 ==========
    Component {
        id: resultsView
        ColumnLayout {
            width: scanPage.width - 48
            spacing: 12

            // 顶部操作栏
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: "命中 " + scanController.matchedCount + " 个文件"
                    font.pixelSize: 14
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Item { Layout.fillWidth: true }
                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "导出 CSV"
                    enabled: scanController.matchedCount > 0
                    onClicked: {
                        scanPage._pendingExportFmt = "csv"
                        exportDialog.open()
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightGhost
                    text: "导出 JSON"
                    enabled: scanController.matchedCount > 0
                    onClicked: {
                        scanPage._pendingExportFmt = "json"
                        exportDialog.open()
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightPrimary
                    text: "重新扫描"
                    onClicked: scanController.startScan()
                }
            }

            // 结果列表 + 简化详情
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 12

                // 结果列表
                Rectangle {
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: 8

                    ListView {
                        id: resultListView
                        anchors.fill: parent
                        anchors.margins: 1
                        clip: true
                        model: scanController.resultModel
                        currentIndex: scanController.selectedResultIndex
                        onCurrentIndexChanged: scanController.setSelectedResultIndex(currentIndex)
                        delegate: ItemDelegate {
                            width: resultListView.width
                            height: 56
                            highlighted: ListView.isCurrentItem
                            background: Rectangle {
                                color: parent.highlighted
                                      ? (theme.isDark ? theme.colorBgSelected : theme.colorBgSelected)
                                      : "transparent"
                            }
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 12
                                spacing: 2
                                Label {
                                    Layout.fillWidth: true
                                    text: model.filePath
                                    font.pixelSize: 12
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    elide: Text.ElideMiddle
                                }
                                Label {
                                    text: model.ruleName + " · " + model.severityText
                                    font.pixelSize: 11
                                    color: model.severityColor
                                }
                            }
                        }
                    }
                }

                // 简化详情
                Rectangle {
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: 8
                    visible: scanController.selectedResultIndex >= 0

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 8

                        Label {
                            text: "文件信息"
                            font.pixelSize: 14
                            font.bold: true
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Label {
                            Layout.fillWidth: true
                            text: scanController.detailFilePath
                            font.pixelSize: 12
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            wrapMode: Text.WrapAnywhere
                        }
                        Label {
                            text: "命中 " + scanController.detailHitsCount + " 处"
                            font.pixelSize: 12
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }

                        // 命中列表
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            ListView {
                                model: scanController.detailHitsModel
                                delegate: ItemDelegate {
                                    width: parent.width
                                    height: 60
                                    ColumnLayout {
                                        anchors.fill: parent
                                        spacing: 2
                                        Label {
                                            text: model.ruleName + " · " + model.severityText
                                            font.pixelSize: 12
                                            font.bold: true
                                            color: model.severityColor
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: model.context
                                            font.pixelSize: 11
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                            wrapMode: Text.WrapAnywhere
                                            elide: Text.ElideRight
                                            maximumLineCount: 2
                                        }
                                    }
                                }
                            }
                        }

                        // 操作按钮
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Button {
                                Layout.preferredHeight: theme.btnHeightGhost
                                text: "打开文件位置"
                                onClicked: scanController.openLocation()
                            }
                            Button {
                                Layout.preferredHeight: theme.btnHeightGhost
                                text: "复制路径"
                                onClicked: scanController.copyPath()
                            }
                        }
                    }
                }
            }
        }
    }
}
