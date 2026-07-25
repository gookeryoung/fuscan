import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// 扫描页：内嵌 StackView 三态切换（setup/scanning/results）
Item {
    id: scanPage

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
                color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            // 状态徽标
            Rectangle {
                radius: 10
                height: 22
                width: statusText.width + 18
                color: ScanController.statusBadgeColor
                border.color: ScanController.statusBadgeBorder
                border.width: 1
                Label {
                    id: statusText
                    anchors.centerIn: parent
                    text: ScanController.statusText
                    font.pixelSize: 11
                    color: ScanController.statusBadgeText
                }
            }
        }

        // ---------- 内嵌 StackView 三态 ----------
        StackView {
            id: scanStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            initialItem: ScanController.scanState === "setup" ? setupView
                       : ScanController.scanState === "scanning" ? scanningView
                       : resultsView

            Connections {
                target: ScanController
                function onScanStateChanged() {
                    switch (ScanController.scanState) {
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

                // 扫描模式
                Label {
                    text: "扫描模式"
                    font.pixelSize: 14
                    font.bold: true
                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                }
                ComboBox {
                    id: modeCombo
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.btnHeightSecondary
                    model: ["全盘扫描", "盘符扫描", "文件夹扫描"]
                    currentIndex: ScanController.scanModeIndex
                    onActivated: ScanController.setScanModeIndex(currentIndex)
                }

                // 目标路径（仅盘符/文件夹模式显示）
                Loader {
                    Layout.fillWidth: true
                    active: ScanController.scanModeIndex !== 0
                    sourceComponent: modeCombo.currentIndex === 1 ? driveComponent : folderComponent
                }

                // 已加载规则摘要
                Label {
                    text: "已加载 " + ScanController.rulesCount + " 条规则"
                    font.pixelSize: 13
                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                }

                // 扫描按钮
                Button {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.btnHeightPrimary
                    text: "开始扫描"
                    enabled: ScanController.canStartScan
                    onClicked: ScanController.startScan()

                    background: Rectangle {
                        color: parent.enabled
                              ? (parent.down ? Theme.colorPrimaryDark : Theme.colorPrimary)
                              : Theme.colorBorder
                        radius: Theme.btnRadiusPrimary
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Label {
                        text: parent.text
                        color: Theme.colorTextOnPrimary
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
                color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
            }
            Repeater {
                model: ConfigController.drives
                delegate: Button {
                    Layout.preferredWidth: 80
                    Layout.preferredHeight: 40
                    text: modelData
                    checkable: true
                    checked: ScanController.selectedDrive === modelData
                    onClicked: ScanController.setSelectedDrive(modelData)
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
                color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ComboBox {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.btnHeightSecondary
                    model: ConfigController.scanPaths
                    currentIndex: 0
                    onActivated: ScanController.setFolderRoot(currentText)
                }
                Button {
                    Layout.preferredHeight: Theme.btnHeightSecondary
                    text: "选择..."
                    onClicked: ScanController.selectFolder()
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
                to: ScanController.progressTotal
                value: ScanController.progressScanned
                indeterminate: ScanController.progressIndeterminate
            }

            // 当前文件
            Label {
                Layout.fillWidth: true
                text: ScanController.currentFile || "准备中..."
                font.pixelSize: 12
                color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                elide: Text.ElideMiddle
            }

            // 状态摘要
            Label {
                Layout.fillWidth: true
                text: ScanController.statusSummary
                font.pixelSize: 12
                color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
            }

            // 分类统计
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                Label {
                    text: "<b style='color:#28A745'>已通过 " + ScanController.passedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#DC3545'>命中 " + ScanController.matchedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#FFC107'>跳过 " + ScanController.skippedCount + "</b>"
                    textFormat: Text.RichText
                    font.pixelSize: 12
                }
                Label {
                    text: "<b style='color:#DC3545'>错误 " + ScanController.errorCount + "</b>"
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
                    Layout.preferredHeight: Theme.btnHeightSecondary
                    text: ScanController.isPaused ? "继续扫描" : "暂停扫描"
                    onClicked: ScanController.togglePause()
                }
                Button {
                    Layout.preferredHeight: Theme.btnHeightSecondary
                    text: "取消扫描"
                    onClicked: ScanController.cancelScan()
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
                    text: "命中 " + ScanController.matchedCount + " 个文件"
                    font.pixelSize: 14
                    font.bold: true
                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                }
                Item { Layout.fillWidth: true }
                Button {
                    Layout.preferredHeight: Theme.btnHeightGhost
                    text: "导出 CSV"
                    enabled: ScanController.matchedCount > 0
                    onClicked: ScanController.exportResults("csv")
                }
                Button {
                    Layout.preferredHeight: Theme.btnHeightGhost
                    text: "导出 JSON"
                    enabled: ScanController.matchedCount > 0
                    onClicked: ScanController.exportResults("json")
                }
                Button {
                    Layout.preferredHeight: Theme.btnHeightPrimary
                    text: "重新扫描"
                    onClicked: ScanController.startScan()
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
                    color: Theme.isDark ? Theme.colorBgCard : Theme.colorBgCard
                    border.color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
                    border.width: 1
                    radius: 8

                    ListView {
                        id: resultListView
                        anchors.fill: parent
                        anchors.margins: 1
                        clip: true
                        model: ScanController.resultModel
                        currentIndex: ScanController.selectedResultIndex
                        onCurrentIndexChanged: ScanController.setSelectedResultIndex(currentIndex)
                        delegate: ItemDelegate {
                            width: resultListView.width
                            height: 56
                            highlighted: ListView.isCurrentItem
                            background: Rectangle {
                                color: parent.highlighted
                                      ? (Theme.isDark ? Theme.colorBgSelected : Theme.colorBgSelected)
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
                                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
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
                    color: Theme.isDark ? Theme.colorBgCard : Theme.colorBgCard
                    border.color: Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
                    border.width: 1
                    radius: 8
                    visible: ScanController.selectedResultIndex >= 0

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 8

                        Label {
                            text: "文件信息"
                            font.pixelSize: 14
                            font.bold: true
                            color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                        }
                        Label {
                            Layout.fillWidth: true
                            text: ScanController.detailFilePath
                            font.pixelSize: 12
                            color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                            wrapMode: Text.WrapAnywhere
                        }
                        Label {
                            text: "命中 " + ScanController.detailHitsCount + " 处"
                            font.pixelSize: 12
                            color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                        }

                        // 命中列表
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            ListView {
                                model: ScanController.detailHitsModel
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
                                            color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
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
                                Layout.preferredHeight: Theme.btnHeightGhost
                                text: "打开文件位置"
                                onClicked: ScanController.openLocation()
                            }
                            Button {
                                Layout.preferredHeight: Theme.btnHeightGhost
                                text: "复制路径"
                                onClicked: ScanController.copyPath()
                            }
                        }
                    }
                }
            }
        }
    }
}
