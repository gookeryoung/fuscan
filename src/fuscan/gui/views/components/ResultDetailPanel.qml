import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 结果详情面板：左侧清单选中后右侧展示命中详情。
// 承载文件元信息、命中规则列表、上一条/下一条切换、内容替换操作。
// ScanController 通过 selectedResultChanged 信号驱动所有 detail* 属性刷新。
Rectangle {
    id: panel
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 便捷别名：当前扫描控制器（链式访问避免本地 property null 问题，见 iter-101）
    readonly property var scanController: workspaceController.currentScanController

    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 空态：未选中结果时提示
    Label {
        anchors.centerIn: parent
        visible: scanController.selectedResultIndex < 0
        text: "请从左侧选择命中结果\n查看详情与替换操作"
        font.pixelSize: 12
        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
        horizontalAlignment: Text.AlignHCenter
    }

    // 详情内容（选中结果后显示）
    ScrollView {
        anchors.fill: parent
        anchors.margins: 16
        clip: true
        visible: scanController.selectedResultIndex >= 0

        ColumnLayout {
            width: parent.width
            spacing: 14

            // ---------- 文件信息区 ----------
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                Label {
                    text: "文件路径"
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                Label {
                    Layout.fillWidth: true
                    text: scanController.detailFilePath
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    wrapMode: Text.WrapAnywhere
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Label {
                        text: "大小: " + scanController.detailFileSize
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                    Label {
                        text: "命中规则: " + scanController.detailHitsCount + " 条"
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                    Item { Layout.fillWidth: true }
                    // 压缩包内部条目标记
                    Rectangle {
                        visible: scanController.detailIsArchiveEntry
                        radius: 8
                        height: 18
                        width: archiveLabel.width + 12
                        color: theme.colorWarning
                        Label {
                            id: archiveLabel
                            anchors.centerIn: parent
                            text: "压缩包条目"
                            font.pixelSize: 10
                            color: theme.colorTextOnPrimary
                        }
                    }
                }
            }

            // 分隔线
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            }

            // ---------- 命中详情列表 ----------
            Label {
                text: "命中详情"
                font.pixelSize: 12
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }

            Repeater {
                model: scanController.detailHitsModel
                delegate: ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.margins: 0
                        height: childrenRect.height + 12
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusMd

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                // 严重度色条
                                Rectangle {
                                    width: 3
                                    height: 14
                                    color: modelData.severityColor
                                    radius: 2
                                }
                                Label {
                                    text: modelData.ruleName
                                    font.pixelSize: 12
                                    font.bold: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                // 严重度标签
                                Rectangle {
                                    radius: 8
                                    height: 18
                                    width: sevLabel.width + 10
                                    color: modelData.severityColor
                                    Label {
                                        id: sevLabel
                                        anchors.centerIn: parent
                                        text: modelData.severityText
                                        font.pixelSize: 10
                                        color: theme.colorTextOnPrimary
                                    }
                                }
                            }

                            // 匹配目标 + 匹配条数
                            Label {
                                visible: modelData.target !== "" || modelData.matchCount > 0
                                text: {
                                    var parts = []
                                    if (modelData.target) parts.push("目标: " + modelData.target)
                                    if (modelData.matchCount > 0) parts.push("匹配 " + modelData.matchCount + " 处")
                                    return parts.join(" · ")
                                }
                                font.pixelSize: 10
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }

                            // 规则描述
                            Label {
                                visible: modelData.description !== ""
                                Layout.fillWidth: true
                                text: modelData.description
                                font.pixelSize: 10
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                wrapMode: Text.WordWrap
                            }

                            // 匹配文本（高亮显示）
                            Label {
                                visible: modelData.matchText !== ""
                                Layout.fillWidth: true
                                text: "匹配文本: " + modelData.matchText
                                font.pixelSize: 11
                                font.family: "Consolas, Monaco, monospace"
                                color: theme.colorDanger
                                wrapMode: Text.WrapAnywhere
                            }

                            // 上下文
                            Label {
                                visible: modelData.context !== ""
                                Layout.fillWidth: true
                                text: modelData.context
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            Item { Layout.fillHeight: true; Layout.preferredHeight: 8 }
        }
    }

    // ---------- 底部操作栏 ----------
    ColumnLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        spacing: 8
        visible: scanController.selectedResultIndex >= 0

        // 替换结果消息（替换后显示）
        Label {
            id: replaceMsgLabel
            Layout.fillWidth: true
            visible: text !== ""
            font.pixelSize: 11
            color: text.indexOf("成功") >= 0 ? theme.colorSuccess : theme.colorDanger
            wrapMode: Text.WordWrap
        }

        // 上一条 / 下一条 + 替换按钮
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            IconButton {
                text: "◀ 上一条"
                tooltip: "查看上一条命中结果"
                accent: "secondary"
                enabled: scanController.canSelectPrev
                onClicked: {
                    replaceMsgLabel.text = ""
                    scanController.selectPrevResult()
                }
            }
            IconButton {
                text: "下一条 ▶"
                tooltip: "查看下一条命中结果"
                accent: "secondary"
                enabled: scanController.canSelectNext
                onClicked: {
                    replaceMsgLabel.text = ""
                    scanController.selectNextResult()
                }
            }
            Item { Layout.fillWidth: true }
            // 替换按钮：仅 canReplaceSelected 时启用
            IconButton {
                text: "🔄 替换内容"
                tooltip: "备份源文件并替换命中内容（按规则 replace_with）"
                accent: "primary"
                enabled: scanController.canReplaceSelected
                onClicked: {
                    var msg = scanController.replaceSelectedResult()
                    replaceMsgLabel.text = msg
                }
            }
        }
    }
}
