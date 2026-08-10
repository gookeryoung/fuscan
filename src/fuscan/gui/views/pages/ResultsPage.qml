import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 结果页：左侧扫描结果清单 + 右侧命中详情面板（含上一条/下一条切换与内容替换）
Item {
    id: resultsPage
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 通知 ContentArea 返回文件扫描页
    signal backRequested()

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        // ---------- 标题区 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            IconButton {
                iconSource: "qrc:/icons/back.svg"
                text: "返回"
                tooltip: "返回文件扫描页"
                accent: "secondary"
                onClicked: resultsPage.backRequested()
            }
            Label {
                text: "扫描结果"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Label {
                text: workspaceController.hasCurrentWorkspace
                  ? "（" + workspaceController.currentScanController.statusText + "）"
                  : "（未选择任务）"
                font.pixelSize: theme.fontSizeSmall
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                visible: workspaceController.hasCurrentWorkspace
            }
            Item { Layout.fillWidth: true }
            Label {
                text: "命中 " + workspaceController.currentScanController.matchedCount + " 项"
                font.pixelSize: theme.fontSizeSmall
                color: theme.colorDanger
                visible: workspaceController.hasCurrentWorkspace
            }
        }

        // ---------- 过滤+排序工具栏 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: workspaceController.hasCurrentWorkspace

            // 文件路径搜索框
            TextField {
                id: filterTextInput
                Layout.fillWidth: true
                Layout.preferredWidth: 280
                placeholderText: "搜索文件路径…"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                background: Rectangle {
                    color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.radiusMd
                }
                // 防抖：输入后 300ms 触发过滤，避免每个字符都 reset model
                Timer {
                    id: filterDebounce
                    interval: 300
                    repeat: false
                    onTriggered: {
                        workspaceController.currentScanController.setResultFilterText(filterTextInput.text)
                    }
                }
                onTextEdited: filterDebounce.restart()
            }

            // 严重度过滤（多选，用 ComboBox 简化为单选迭代）
            ComboBox {
                id: severityFilterCombo
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                font.pixelSize: theme.fontSizeBody
                model: ["全部", "严重", "警告", "信息"]
                onCurrentIndexChanged: {
                    var sel = []
                    if (currentIndex === 1) sel = ["严重"]
                    else if (currentIndex === 2) sel = ["警告"]
                    else if (currentIndex === 3) sel = ["信息"]
                    workspaceController.currentScanController.setResultFilterSeverities(sel)
                }
            }

            // 排序字段
            ComboBox {
                id: sortFieldCombo
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                font.pixelSize: theme.fontSizeBody
                model: ["默认顺序", "文件路径", "命中数", "严重度"]
                // 默认按严重度排序（currentIndex=3）
                currentIndex: 3
                onCurrentIndexChanged: {
                    var field = "default"
                    if (currentIndex === 1) field = "filePath"
                    else if (currentIndex === 2) field = "hitsCount"
                    else if (currentIndex === 3) field = "severity"
                    workspaceController.currentScanController.setResultSort(field, sortOrderCombo.currentIndex === 0)
                }
            }

            // 排序方向（升/降）
            ComboBox {
                id: sortOrderCombo
                Layout.preferredWidth: 80
                Layout.preferredHeight: 32
                font.pixelSize: theme.fontSizeBody
                model: ["升序", "降序"]
                // 默认降序（严重 → 轻微）
                currentIndex: 1
                onCurrentIndexChanged: {
                    var field = "default"
                    if (sortFieldCombo.currentIndex === 1) field = "filePath"
                    else if (sortFieldCombo.currentIndex === 2) field = "hitsCount"
                    else if (sortFieldCombo.currentIndex === 3) field = "severity"
                    workspaceController.currentScanController.setResultSort(field, currentIndex === 0)
                }
            }

            // 重置排序按钮（仅重置排序字段与方向，不影响过滤条件）
            Button {
                text: "重置排序"
                font.pixelSize: theme.fontSizeBody
                Layout.preferredHeight: 32
                onClicked: {
                    sortFieldCombo.currentIndex = 3
                    sortOrderCombo.currentIndex = 1
                    workspaceController.currentScanController.setResultSort("severity", false)
                }
            }

            // 过滤后计数
            Label {
                text: workspaceController.currentScanController.resultFilteredCount
                      + " / " + workspaceController.currentScanController.resultTotalCount
                font.pixelSize: theme.fontSizeSmall
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
        }

        // ---------- 已替换维度 Tab：待处理 / 已替换 / 全部 ----------
        TabBar {
            id: replacedTabBar
            Layout.fillWidth: true
            // 默认「待处理」：扫描完成后用户进入结果页时，自动替换的项已被
            // 标记 replaced=True 并分到此 Tab，避免与未替换项混在一起
            currentIndex: 0
            visible: workspaceController.hasCurrentWorkspace

            TabButton {
                text: "待处理"
                font.pixelSize: theme.fontSizeBody
            }
            TabButton {
                text: "已替换"
                font.pixelSize: theme.fontSizeBody
            }
            TabButton {
                text: "全部"
                font.pixelSize: theme.fontSizeBody
            }

            onCurrentIndexChanged: {
                if (!workspaceController.hasCurrentWorkspace) return
                // 0=待处理（仅未替换）→ 1, 1=已替换 → 2, 2=全部 → 0
                var v = 1
                if (currentIndex === 1) v = 2
                else if (currentIndex === 2) v = 0
                workspaceController.currentScanController.setResultFilterReplaced(v)
            }

            // 切换工作区时重置回「待处理」并同步 controller 过滤条件
            Connections {
                target: workspaceController
                function onCurrentWorkspaceChanged() {
                    replacedTabBar.currentIndex = 0
                    if (workspaceController.hasCurrentWorkspace) {
                        workspaceController.currentScanController.setResultFilterReplaced(1)
                    }
                }
            }

            Component.onCompleted: {
                if (workspaceController.hasCurrentWorkspace) {
                    workspaceController.currentScanController.setResultFilterReplaced(1)
                }
            }
        }

        // ---------- 主体：左右分栏 ----------
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // 左侧：结果清单
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 320
                Layout.preferredWidth: 480
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: theme.radiusLg
                clip: true

                ListView {
                    id: resultListView
                    anchors.fill: parent
                    anchors.margins: 8
                    model: workspaceController.currentScanController.resultModel
                    spacing: 4
                    // cacheBuffer 按结果量动态调整。
                    // 小结果集高 cacheBuffer 提升滚动流畅度；大结果集降低减少内存占用。
                    // 10w 结果 × 56px delegate ≈ 5.6MB 视觉区，cacheBuffer 500 限制预渲染约 9 个
                    cacheBuffer: resultListView.count > 50000 ? 500
                               : resultListView.count > 10000 ? 1000
                               : 2000
                    currentIndex: workspaceController.currentScanController.selectedResultIndex
                    // 滚动停止时上报当前可视范围给 Model，启用虚拟化（大结果集才生效）
                    property int delegateHeight: 56
                    onMovementEnded: {
                        if (count <= 0 || !visibleArea) return
                        var vp = visibleArea.yPosition
                        var start = Math.max(0, Math.floor(vp * count))
                        var visibleRows = visibleArea.height > 0 ? Math.ceil(visibleArea.height / delegateHeight) : 10
                        var end = Math.min(count - 1, start + visibleRows + 4)
                        workspaceController.currentScanController.resultModel.setVisibleRange(start, end)
                    }
                    onCountChanged: {
                        // 首次加载或过滤后重置：上报当前顶部可视范围，确保 Model 立即进入虚拟化态
                        if (count > 0 && visibleArea) {
                            var vp0 = visibleArea.yPosition
                            var s0 = Math.max(0, Math.floor(vp0 * count))
                            var vr0 = visibleArea.height > 0 ? Math.ceil(visibleArea.height / delegateHeight) : 10
                            var e0 = Math.min(count - 1, s0 + vr0 + 4)
                            workspaceController.currentScanController.resultModel.setVisibleRange(s0, e0)
                        }
                    }

                    // 恢复中占位态（后台异步加载缓存结果）
                    ColumnLayout {
                        anchors.centerIn: parent
                        visible: workspaceController.currentScanController.restoring
                        spacing: 12
                        BusyIndicator {
                            Layout.alignment: Qt.AlignHCenter
                            running: visible
                        }
                        Label {
                            text: "正在恢复扫描结果…"
                            font.pixelSize: 13
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }

                    // 空态引导
                    Label {
                        anchors.centerIn: parent
                        visible: resultListView.count === 0
                            && !workspaceController.currentScanController.restoring
                        text: workspaceController.hasCurrentWorkspace
                            ? "暂无命中结果"
                            : "未选择任务\n请从文件扫描页工作区卡片点击「查看结果」"
                        font.pixelSize: 13
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        horizontalAlignment: Text.AlignHCenter
                    }

                    delegate: ItemDelegate {
                        width: resultListView.width
                        height: 56

                        background: Rectangle {
                            color: ListView.isCurrentItem
                                  ? (theme.isDark ? theme.colorBgSelectedDark : theme.colorBgSelected)
                                  : (parent.down
                                      ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                      : "transparent")
                            border.color: ListView.isCurrentItem
                                ? (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                                : "transparent"
                            border.width: 1
                            radius: theme.radiusMd
                            Behavior on color { ColorAnimation { duration: 120 } }
                        }

                        onClicked: {
                            workspaceController.currentScanController.setSelectedResultIndex(model.index)
                        }

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 10

                            // 严重度色条
                            Rectangle {
                                width: 3
                                height: parent.height * 0.6
                                color: model.severityColor
                                radius: 2
                            }

                            // 严重度标签（必须在内容前定义，供 Column 宽度绑定引用）
                            Rectangle {
                                id: severityTag
                                radius: 8
                                height: 20
                                width: Math.max(implicitWidth, tagLabel.implicitWidth + 12)
                                color: model.severityColor
                                readonly property Label tagLabel: severityLabel
                                Label {
                                    id: severityLabel
                                    anchors.centerIn: parent
                                    text: model.severityText
                                    font.pixelSize: 10
                                    color: theme.colorTextOnPrimary
                                }
                            }

                            Column {
                                width: parent.width - 3 - parent.spacing - severityTag.tagLabel.implicitWidth
                                spacing: 2

                                // 文件路径
                                Label {
                                    width: parent.width
                                    text: model.filePath
                                    font.pixelSize: 12
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    elide: Text.ElideMiddle
                                }
                                // 规则名 + 命中数
                                Label {
                                    text: model.ruleName + " · 命中 " + model.hitsCount + " 处"
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                            }
                        }
                    }
                }
            }

            // 右侧：详情面板
            ResultDetailPanel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 360
                Layout.preferredWidth: 520
            }
        }
    }
}
