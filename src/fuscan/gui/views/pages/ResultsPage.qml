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

    // 通知 ContentArea 返回首页
    signal backRequested()

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        // ---------- 标题区 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            IconButton {
                text:"← 返回"
                tooltip: "返回首页"
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
                    // iter-106 P0：大规模命中（1000+）时预渲染屏幕外 delegate，
                    // 避免滚动时频繁销毁重建（每个 delegate 含路径/规则/严重度绑定）
                    cacheBuffer: 2000
                    currentIndex: workspaceController.currentScanController.selectedResultIndex

                    // 空态引导
                    Label {
                        anchors.centerIn: parent
                        visible: resultListView.count === 0
                        text: workspaceController.hasCurrentWorkspace
                            ? "暂无命中结果"
                            : "未选择任务\n请从首页工作区卡片点击「查看结果」"
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

                        RowLayout {
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

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2

                                // 文件路径
                                Label {
                                    Layout.fillWidth: true
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

                            // 严重度标签
                            Rectangle {
                                radius: 8
                                height: 20
                                width: severityLabel.width + 12
                                color: model.severityColor
                                Label {
                                    id: severityLabel
                                    anchors.centerIn: parent
                                    text: model.severityText
                                    font.pixelSize: 10
                                    color: theme.colorTextOnPrimary
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
