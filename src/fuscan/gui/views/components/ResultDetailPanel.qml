import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 结果详情面板：左侧清单选中后右侧展示命中详情。
// 承载文件元信息卡片、命中规则列表（可折叠）、上一条/下一条切换、
// 移至暂存、内容替换操作。
// ScanController 通过 selectedResultChanged 信号驱动所有 detail* 属性刷新。
Rectangle {
    id: panel
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController

    // 便捷别名：当前扫描控制器（链式访问避免本地 property null 问题，见 iter-101）
    readonly property var scanController: workspaceController.currentScanController

    // 命中详情展开状态：true 显示完整详情，false 仅显示规则名标题
    property bool detailsExpanded: true

    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 空态：未选中结果时提示
    Label {
        anchors.centerIn: parent
        visible: scanController.selectedResultIndex < 0
        text: "请从左侧选择命中结果\n查看详情与操作"
        font.pixelSize: 12
        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
        horizontalAlignment: Text.AlignHCenter
    }

    // 详情内容（选中结果后显示）
    ScrollView {
        anchors.fill: parent
        anchors.margins: 16
        anchors.bottomMargin: 100  // 给底部操作栏留空间
        clip: true
        visible: scanController.selectedResultIndex >= 0
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 12

            // ---------- 文件信息卡片 ----------
            Rectangle {
                Layout.fillWidth: true
                color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: theme.radiusMd
                implicitHeight: fileInfoColumn.implicitHeight + 20

                ColumnLayout {
                    id: fileInfoColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8

                    // 标题行：文件信息 + 压缩包标签
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Label {
                            text: "📄 文件信息"
                            font.pixelSize: 12
                            font.bold: true
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
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

                    // 文件路径行：路径 + 定位按钮
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Label {
                            Layout.fillWidth: true
                            text: scanController.detailFilePath
                            font.pixelSize: 11
                            font.family: "Consolas, Monaco, monospace"
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            wrapMode: Text.WrapAnywhere
                        }
                        // 定位文件按钮：调用 scanController.openLocation()（无参 Slot）
                        IconButton {
                            text:"📁 定位"
                            tooltip: "在文件管理器中打开并选中该文件"
                            accent: "secondary"
                            enabled: !scanController.detailIsArchiveEntry
                            onClicked: scanController.openLocation()
                        }
                    }

                    // 元信息网格：大小 | 命中规则数
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 16
                        rowSpacing: 4

                        Label {
                            text: "大小"
                            font.pixelSize: 10
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                        Label {
                            text: scanController.detailFileSize || "—"
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Label {
                            text: "命中规则"
                            font.pixelSize: 10
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                        Label {
                            text: scanController.detailHitsCount + " 条"
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                    }
                }
            }

            // ---------- 命中详情标题 + 展开按钮 ----------
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Label {
                    text: "命中详情"
                    font.pixelSize: 12
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: panel.detailsExpanded ? "收起 ▲" : "展开 ▼"
                    font.pixelSize: 10
                    flat: true
                    onClicked: panel.detailsExpanded = !panel.detailsExpanded
                    ToolTip.visible: hovered
                    ToolTip.text: panel.detailsExpanded ? "收起命中详情" : "展开命中详情"
                }
            }

            // ---------- 命中详情列表（展开时显示完整内容） ----------
            Repeater {
                model: scanController.detailHitsModel
                delegate: Rectangle {
                    Layout.fillWidth: true
                    color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.radiusMd
                    implicitHeight: hitColumn.implicitHeight + 16
                    visible: panel.detailsExpanded || index === 0  // 折叠时仅显示第一条摘要

                    ColumnLayout {
                        id: hitColumn
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 4

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

                        // 折叠时仅显示规则名行，展开时显示完整详情
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            visible: panel.detailsExpanded

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
        spacing: 6
        visible: scanController.selectedResultIndex >= 0

        // 操作消息（替换/移至暂存后显示）
        Label {
            id: opMsgLabel
            Layout.fillWidth: true
            visible: text !== ""
            font.pixelSize: 11
            color: {
                if (text.indexOf("成功") >= 0 || text.indexOf("已移至暂存") >= 0) return theme.colorSuccess
                if (text.indexOf("失败") >= 0) return theme.colorDanger
                return theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            wrapMode: Text.WordWrap
        }

        // 第一行：上一条/下一条（左对齐）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            IconButton {
                text: "◀ 上一条"
                tooltip: "查看上一条命中结果"
                accent: "secondary"
                enabled: scanController.canSelectPrev
                onClicked: {
                    opMsgLabel.text = ""
                    scanController.selectPrevResult()
                }
            }
            IconButton {
                text: "下一条 ▶"
                tooltip: "查看下一条命中结果"
                accent: "secondary"
                enabled: scanController.canSelectNext
                onClicked: {
                    opMsgLabel.text = ""
                    scanController.selectNextResult()
                }
            }
            Item { Layout.fillWidth: true }
        }

        // 第二行：移至暂存 + 替换内容（右对齐，移至暂存在替换内容左侧）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Item { Layout.fillWidth: true }
            IconButton {
                text:"📦 移至暂存"
                tooltip: "复制到暂存区隔离目录并标记为跳过"
                accent: "secondary"
                enabled: !scanController.detailIsArchiveEntry && scanController.selectedResultIndex >= 0
                onClicked: {
                    var msg = scanController.moveSelectedToStaging()
                    opMsgLabel.text = msg
                }
            }
            IconButton {
                text: "🔄 替换内容"
                tooltip: "备份源文件并替换命中内容（按规则 replace_with）"
                accent: "primary"
                enabled: scanController.canReplaceSelected
                onClicked: {
                    var msg = scanController.replaceSelectedResult()
                    opMsgLabel.text = msg
                }
            }
        }
    }
}
