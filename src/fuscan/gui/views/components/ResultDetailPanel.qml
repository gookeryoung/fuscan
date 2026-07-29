import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
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

    // 命中详情展开状态：true 显示完整详情，false 仅显示规则名标题
    property bool detailsExpanded: true

    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 将上下文文本转为 HTML：>>> 匹配行按严重程度颜色高亮整行
    function formatContextHtml(context, severityColor) {
        var lines = context.split("\n")
        var result = []
        for (var i = 0; i < lines.length; i++) {
            var raw = lines[i]
            var isMatch = raw.indexOf(">>>") === 0
            // HTML 转义（& 必须先转义，否则后续 &lt; 中的 & 会被二次转义）
            var escaped = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            // 保留行首缩进（&nbsp; 替换前导空格，防止 HTML 折叠）
            var k = 0
            while (k < escaped.length && escaped.charAt(k) === " ") {
                k++
            }
            var html = ""
            for (var m = 0; m < k; m++) {
                html += "&nbsp;"
            }
            html += escaped.substring(k)
            // 匹配行整行用严重度颜色 + 加粗
            if (isMatch) {
                html = '<span style="color: ' + severityColor + '; font-weight: bold;">' + html + '</span>'
            }
            result.push(html)
        }
        return result.join("<br>")
    }

    // 空态：未选中结果时提示
    // 注意：ScanController 一律通过 workspaceController.currentScanController.xxx 链式访问，
    // 不绑定到本地 property。PySide2 5.15 中将 @Property(ScanController) 返回的 QObject
    // 绑定到本地 property var/ScanControllerType 时类型推断失败会识别为 null（iter-101），
    // 链式访问每次 binding 求值都重新读取 Property，与 ScanProgressCard 稳定模式一致。
    Label {
        anchors.centerIn: parent
        visible: workspaceController.currentScanController.selectedResultIndex < 0
        text: "请从左侧选择命中结果\n查看详情与操作"
        font.pixelSize: 12
        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
        horizontalAlignment: Text.AlignHCenter
    }

    // 固定顶部区域：文件信息卡片 + 命中详情标题（不随详情滚动）
    ColumnLayout {
        id: topSection
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 16
        spacing: 12
        visible: workspaceController.currentScanController.selectedResultIndex >= 0

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
                        // 文件信息图标：SVG file + ColorOverlay 染色
                        Item {
                            width: 12
                            height: 12
                            Layout.preferredWidth: 12
                            Layout.preferredHeight: 12
                            Image {
                                id: fileInfoIcon
                                anchors.fill: parent
                                source: "qrc:/icons/file.svg"
                                sourceSize: Qt.size(12, 12)
                                visible: false
                            }
                            ColorOverlay {
                                anchors.fill: fileInfoIcon
                                source: fileInfoIcon
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            }
                        }
                        Label {
                            text: "文件信息"
                            font.pixelSize: 12
                            font.bold: true
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Item { Layout.fillWidth: true }
                        // 压缩包内部条目标记
                        Rectangle {
                            visible: workspaceController.currentScanController.detailIsArchiveEntry
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
                            text: workspaceController.currentScanController.detailFilePath
                            font.pixelSize: 11
                            font.family: "Consolas, Monaco, monospace"
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            wrapMode: Text.WrapAnywhere
                        }
                        // 定位文件按钮：调用 workspaceController.currentScanController.openLocation()（无参 Slot）
                        // iter-133：压缩包内部条目也启用——定位到压缩包文件本身
                        IconButton {
                            iconSource: "qrc:/icons/folder.svg"
                            text: "定位"
                            tooltip: workspaceController.currentScanController.detailIsArchiveEntry ? "在文件管理器中打开压缩包文件" : "在文件管理器中打开并选中该文件"
                            accent: "secondary"
                            onClicked: workspaceController.currentScanController.openLocation()
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
                            text: workspaceController.currentScanController.detailFileSize || "—"
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                        Label {
                            text: "命中规则"
                            font.pixelSize: 10
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                        Label {
                            text: workspaceController.currentScanController.detailHitsCount + " 条"
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

    }

    // ---------- 命中详情列表（仅此区域滚动） ----------
    ScrollView {
        anchors.top: topSection.bottom
        anchors.topMargin: 12
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        // 底部操作栏高度随 opMsgLabel/批量行可见性动态变化，
        // 绑定到 bottomBar.height 避免内容与按钮重叠（12=bottomBar 底边距，8=间隔）
        anchors.bottomMargin: bottomBar.height + 20
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        clip: true
        visible: workspaceController.currentScanController.selectedResultIndex >= 0
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 12

            // ---------- 命中详情列表（展开时显示完整内容） ----------
            Repeater {
                model: workspaceController.currentScanController.detailHitsModel
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

                            // 上下文（iter-124：实时读取文件内容，匹配行用 >>> 标记）
                            // >>> 匹配行按严重程度颜色高亮整行（RichText + HTML span）
                            TextArea {
                                visible: modelData.context !== ""
                                Layout.fillWidth: true
                                text: panel.formatContextHtml(modelData.context, modelData.severityColor)
                                textFormat: Text.RichText
                                font.pixelSize: 11
                                font.family: "Consolas, Monaco, monospace"
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                wrapMode: Text.WordWrap
                                readOnly: true
                                background: Rectangle {
                                    color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                    border.width: 1
                                    radius: 2
                                }
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
        id: bottomBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        spacing: 6
        visible: workspaceController.currentScanController.selectedResultIndex >= 0

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
                enabled: workspaceController.currentScanController.canSelectPrev
                onClicked: {
                    opMsgLabel.text = ""
                    workspaceController.currentScanController.selectPrevResult()
                }
            }
            IconButton {
                text: "下一条 ▶"
                tooltip: "查看下一条命中结果"
                accent: "secondary"
                enabled: workspaceController.currentScanController.canSelectNext
                onClicked: {
                    opMsgLabel.text = ""
                    workspaceController.currentScanController.selectNextResult()
                }
            }
            Item { Layout.fillWidth: true }
        }

        // 第二行：替换为输入框 + 替换内容按钮（iter-124：自定义替换文本，默认 ...）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "替换为:"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            TextField {
                id: replaceWithInput
                Layout.fillWidth: true
                text: "..."
                placeholderText: "输入替换文本（默认 ...）"
                font.pixelSize: 11
                font.family: "Consolas, Monaco, monospace"
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                selectByMouse: true
                background: Rectangle {
                    color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: 2
                }
            }
            IconButton {
                iconSource: "qrc:/icons/rescan.svg"
                text: "替换内容"
                tooltip: "备份源文件并替换命中内容（用输入框文本替换）"
                accent: "primary"
                enabled: workspaceController.currentScanController.canReplaceSelected
                onClicked: {
                    var msg = workspaceController.currentScanController.replaceSelectedResult(replaceWithInput.text)
                    opMsgLabel.text = msg
                }
            }
            IconButton {
                iconSource: "qrc:/icons/export.svg"
                text: "移至暂存"
                tooltip: workspaceController.currentScanController.detailIsArchiveEntry ? "复制压缩包到暂存区隔离目录并标记为跳过" : "复制到暂存区隔离目录并标记为跳过"
                accent: "secondary"
                enabled: workspaceController.currentScanController.selectedResultIndex >= 0
                onClicked: {
                    var msg = workspaceController.currentScanController.moveSelectedToStaging()
                    opMsgLabel.text = msg
                }
            }
            // iter-133：标记为误报——加入白名单，下次扫描起在命中聚合阶段过滤
            IconButton {
                iconSource: "qrc:/icons/stop.svg"
                text: "标记误报"
                tooltip: workspaceController.currentScanController.detailIsArchiveEntry ? "压缩包内部条目不支持标记误报" : "将此文件全部命中加入误报白名单，下次扫描起自动过滤"
                accent: "secondary"
                enabled: workspaceController.currentScanController.selectedResultIndex >= 0
                         && !workspaceController.currentScanController.detailIsArchiveEntry
                onClicked: {
                    var msg = workspaceController.currentScanController.markAsFalsePositive("")
                    opMsgLabel.text = msg
                }
            }
        }

        // iter-113：第三行 - 批量替换与撤销（针对过滤后的全部结果）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "批量操作"
                font.pixelSize: 10
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                visible: workspaceController.currentScanController.canReplaceAllFiltered || workspaceController.currentScanController.canUndoLastBatchReplace
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconSource: "qrc:/icons/rescan.svg"
                text: "全部替换"
                tooltip: "对当前过滤后的所有命中结果执行批量替换（用上方输入框文本）"
                accent: "primary"
                enabled: workspaceController.currentScanController.canReplaceAllFiltered
                onClicked: {
                    var msg = workspaceController.currentScanController.replaceAllFilteredResults(replaceWithInput.text)
                    opMsgLabel.text = msg
                }
            }
            IconButton {
                iconSource: "qrc:/icons/history.svg"
                text: "撤销批量"
                tooltip: "撤销最近一次批量替换，从 .bak 备份恢复所有文件"
                accent: "secondary"
                enabled: workspaceController.currentScanController.canUndoLastBatchReplace
                onClicked: {
                    var msg = workspaceController.currentScanController.undoLastBatchReplace()
                    opMsgLabel.text = msg
                }
            }
            IconButton {
                iconSource: "qrc:/icons/history.svg"
                text: "撤销当前"
                tooltip: "撤销当前选中结果的最近一次替换（从 .bak 恢复）"
                accent: "secondary"
                enabled: workspaceController.currentScanController.canReplaceSelected
                onClicked: {
                    var msg = workspaceController.currentScanController.undoSelectedReplace()
                    opMsgLabel.text = msg
                }
            }
        }
    }
}
