import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 扫描进度卡片：扫描中（含暂停态）显示，承载实时进度与控制按钮。
// HomePage 在 hasActiveScan=true 时用此卡片替换工作区列表，
// 隐藏其余工作区以聚焦当前扫描任务（用户需求：扫描结束才显示其余工作区）。
//
// 注意：ScanController 一律通过 workspaceController.activeScanController.xxx 链式访问，
// 不绑定到本地 property。PySide2 5.15 中将 @Property(ScanController) 返回的 QObject
// 绑定到本地 property var/ScanControllerType 时类型推断失败会识别为 null，
// 链式访问每次 binding 求值都重新读取 Property，与 StatsPage 稳定模式一致。
Rectangle {
    id: card
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController

    // 由 HomePage 注入：扫描中的工作区 ID 与展示字段
    property string workspaceId: ""
    property string taskName: ""
    property string modeText: ""
    property string target: ""

    implicitHeight: contentColumn.implicitHeight + 32
    color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
    border.width: 1
    radius: theme.radiusLg

    // 状态色：扫描中=warning（黄），已暂停=text secondary
    function statusColor() {
        if (workspaceController.activeScanController.isPaused) return theme.colorTextSecondary
        return theme.colorWarning
    }

    function statusText() {
        if (workspaceController.activeScanController.isPaused) return "已暂停"
        return "扫描中"
    }

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        // ---------- 第一行：任务名 + 状态徽标 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            // 任务图标：SVG rules + ColorOverlay 染色
            Item {
                width: 18
                height: 18
                Layout.preferredWidth: 18
                Layout.preferredHeight: 18
                Image {
                    id: scanTaskIcon
                    anchors.fill: parent
                    source: "qrc:/icons/rules.svg"
                    sourceSize: Qt.size(18, 18)
                    visible: false
                }
                ColorOverlay {
                    anchors.fill: scanTaskIcon
                    source: scanTaskIcon
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
            }
            Label {
                text: taskName
                font.pixelSize: theme.fontSizeHeading
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            // 状态徽标
            Rectangle {
                radius: 10
                height: 24
                width: statusBadgeLabel.width + 18
                color: card.statusColor()
                Label {
                    id: statusBadgeLabel
                    anchors.centerIn: parent
                    text: card.statusText()
                    font.pixelSize: 11
                    font.bold: true
                    color: theme.colorTextOnPrimary
                }
            }
        }

        // ---------- 第二行：任务元数据（整合当前文件） ----------
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 16
            rowSpacing: 4

            Label {
                text: "模式"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: modeText
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Label {
                text: "目标"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: target || "—"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideMiddle
            }
            // 资源配置（CPU 线程 / 最大文件 / 扫描深度）
            // 改读 activeScanController.effective* 以反映任务级 override。
            Label {
                text: "配置"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            Label {
                text: "最多 " + workspaceController.activeScanController.effectiveMaxWorkers + " 线程 / 最大 "
                      + workspaceController.activeScanController.effectiveMaxFileSizeMB + " MB / 深度 "
                      + workspaceController.activeScanController.effectiveMaxDepth
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            // 当前文件并入元数据网格作为第4行（原独立行整合）
            Label {
                text: "当前文件"
                font.pixelSize: 11
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Label {
                    Layout.fillWidth: true
                    text: workspaceController.activeScanController.currentFile || "—"
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    elide: Text.ElideMiddle
                    // 暂停态淡化提示
                    opacity: workspaceController.activeScanController.isPaused ? 0.6 : 1.0
                }
                // 单文件元信息标签：大文件显示 [大小 · 扩展名 · 耗时]，
                // 小文件仅显示 [扩展名 · 大小 KB]；scan 阶段且文件>0 时才展示
                Label {
                    visible: workspaceController.activeScanController.scanPhase === "scan"
                             && workspaceController.activeScanController.currentFileSize > 0
                    text: {
                        var size = workspaceController.activeScanController.currentFileSize
                        var ext = workspaceController.activeScanController.currentFileExt
                        var ms = workspaceController.activeScanController.currentFileElapsedMs
                        if (size > 1048576) {
                            // 大于 1MB：[12.3 MB · pdf · 1.2s]
                            var sizeMB = (size / 1048576).toFixed(1)
                            var sec = (ms / 1000.0).toFixed(1)
                            return "[" + sizeMB + " MB · " + (ext || "?") + " · " + sec + "s]"
                        }
                        // 小文件：[pdf · 12.3 KB]
                        var sizeKB = (size / 1024).toFixed(1)
                        return "[" + (ext || "?") + " · " + sizeKB + " KB]"
                    }
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    opacity: workspaceController.activeScanController.isPaused ? 0.6 : 1.0
                }
            }
        }

        // ---------- 第三行：流程时间线（GitHub Actions 风格阶段节点） ----------
        // 收集 → 筛选 → 解析 三个阶段节点竖直串联，节点间以连接线相连；
        // 进行中的节点显示旋转转圈动画，完成的节点显示对勾。
        // filter 阶段极快（仅 size 比较），单独成节点以直观展示"剔除 N"详情。
        ColumnLayout {
            id: phaseTimeline
            Layout.fillWidth: true
            spacing: 0

            // 阶段状态求值：返回 "pending" / "running" / "done"
            // walk：done=walkDone；running=phase=="walk"；否则 pending
            // filter：running=filterActive；done=walk 已完成且已进入 scan/archive/done；否则 pending
            // scan：running=phase 属 scan/archive；done=scanDone；否则 pending
            readonly property var sc: workspaceController.activeScanController
            function walkNodeState() {
                if (phaseTimeline.sc.walkDone) return "done"
                if (phaseTimeline.sc.scanPhase === "walk") return "running"
                return "pending"
            }
            function filterNodeState() {
                if (phaseTimeline.sc.filterActive) return "running"
                // walk 完成且已推进到 scan/archive/done，说明 filter 已走完
                var p = phaseTimeline.sc.scanPhase
                if (phaseTimeline.sc.walkDone && (p === "scan" || p === "archive" || p === "done"))
                    return "done"
                return "pending"
            }
            function scanNodeState() {
                if (phaseTimeline.sc.scanDone) return "done"
                var p = phaseTimeline.sc.scanPhase
                if (p === "scan" || p === "archive") return "running"
                return "pending"
            }

            // ----- 节点 1：收集文件清单（walk） -----
            PhaseNode {
                Layout.fillWidth: true
                theme: card.theme
                nodeState: phaseTimeline.walkNodeState()
                accentColor: theme.colorPrimary
                title: "收集文件清单"
                showTopLine: false
                progressIndeterminate: phaseTimeline.sc.walkIndeterminate
                progressValue: phaseTimeline.sc.walkIndeterminate ? -1 : phaseTimeline.sc.walkProgress
                detail: phaseTimeline.sc.walkIndeterminate
                    ? "统计中..."
                    : (phaseTimeline.sc.walkClassified + " / " + phaseTimeline.sc.walkDiscovered
                       + ((phaseTimeline.sc.walkSkipped > 0 || phaseTimeline.sc.walkUserSkipped > 0)
                          ? " · 跳过 " + (phaseTimeline.sc.walkSkipped + phaseTimeline.sc.walkUserSkipped)
                          : ""))
            }

            // ----- 节点 2：筛选文件（filter，剔除空/超限/不可读/符号链接） -----
            PhaseNode {
                Layout.fillWidth: true
                theme: card.theme
                nodeState: phaseTimeline.filterNodeState()
                accentColor: theme.colorWarning
                title: "筛选文件"
                progressIndeterminate: true
                progressValue: -1
                detail: {
                    var removed = phaseTimeline.sc.filterRemovedEmpty
                                  + phaseTimeline.sc.filterRemovedOversize
                                  + phaseTimeline.sc.filterRemovedUnreadable
                                  + phaseTimeline.sc.filterRemovedSymlink
                    // 有剔除时展示明细，无剔除时仅展示总数（避免长串 0）
                    if (removed > 0) {
                        return "剔除 " + removed + "（空 " + phaseTimeline.sc.filterRemovedEmpty
                               + " · 超限 " + phaseTimeline.sc.filterRemovedOversize
                               + " · 不可读 " + phaseTimeline.sc.filterRemovedUnreadable
                               + " · 链接 " + phaseTimeline.sc.filterRemovedSymlink + "）"
                    }
                    return phaseTimeline.filterNodeState() === "pending" ? "" : "剔除 0"
                }
            }

            // ----- 节点 3：解析文件内容（scan / archive） -----
            PhaseNode {
                Layout.fillWidth: true
                theme: card.theme
                nodeState: phaseTimeline.scanNodeState()
                accentColor: card.statusColor()
                title: "解析文件内容"
                showBottomLine: false
                // 可展开查看具体文件解析明细（GitHub Actions 风格）：
                // 仅在解析阶段进行中或完成后可展开，pending 时无明细
                expandable: phaseTimeline.scanNodeState() !== "pending"
                progressIndeterminate: phaseTimeline.sc.progressIndeterminate && !phaseTimeline.sc.filterActive
                progressValue: phaseTimeline.sc.progressIndeterminate ? -1 : phaseTimeline.sc.progress
                detail: {
                    if (phaseTimeline.scanNodeState() === "pending") return ""
                    if (phaseTimeline.sc.progressIndeterminate && !phaseTimeline.sc.scanDone) return "等待中..."
                    var t = phaseTimeline.sc.progressScanned + " / " + phaseTimeline.sc.progressTotal
                    if (phaseTimeline.sc.archiveEntryCount > 0)
                        t += "（含压缩包 " + phaseTimeline.sc.archiveEntryCount + "）"
                    // 平均速度（文件/s）便于横向性能比较
                    var spd = phaseTimeline.sc.scanSpeed
                    if (spd > 0)
                        t += " · 平均 " + spd.toFixed(0) + " 文件/s"
                    return t
                }

                // 展开明细区：最近解析文件列表（最新在前），逐行展示 文件名 · 大小 · 耗时
                expandContent: Component {
                    ColumnLayout {
                        spacing: 2

                        Repeater {
                            model: phaseTimeline.sc.recentParsedFiles
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                // 文件名（取路径末段，避免过长）
                                Label {
                                    Layout.fillWidth: true
                                    text: {
                                        var p = modelData.path || ""
                                        var idx = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\"))
                                        return idx >= 0 ? p.substring(idx + 1) : p
                                    }
                                    font.pixelSize: 10
                                    color: card.theme.isDark ? card.theme.colorTextSecondary : card.theme.colorTextSecondary
                                    elide: Text.ElideMiddle
                                }
                                // 大小 · 耗时
                                Label {
                                    text: {
                                        var size = modelData.size || 0
                                        var sizeStr = size > 1048576
                                            ? (size / 1048576).toFixed(1) + " MB"
                                            : (size / 1024).toFixed(1) + " KB"
                                        var ms = modelData.elapsedMs || 0
                                        var msStr = ms >= 1000
                                            ? (ms / 1000.0).toFixed(1) + "s"
                                            : ms.toFixed(0) + "ms"
                                        return sizeStr + " · " + msStr
                                    }
                                    font.pixelSize: 10
                                    color: card.theme.isDark ? card.theme.colorTextSecondary : card.theme.colorTextSecondary
                                }
                            }
                        }
                        // 空态提示：暂无解析明细
                        Label {
                            visible: phaseTimeline.sc.recentParsedFiles.length === 0
                            text: "暂无解析明细"
                            font.pixelSize: 10
                            color: card.theme.isDark ? card.theme.colorTextSecondary : card.theme.colorTextSecondary
                        }
                    }
                }
            }
        }

        // ---------- 第四行：分类计数 + 控制按钮（整合原五六两行） ----------
        // 移除左侧 statusSummary 文本——其"命中/错误"与右侧计数标签重复，
        // "总计/扫描数"已在上方进度条显示，"跳过"在 walk 统计中，"耗时"完成后才准确。
        // 计数靠左，控制按钮靠右
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            // 分类计数（PlainText + color，避免 RichText 解析开销）
            Label {
                text: "安全 " + workspaceController.activeScanController.passedCount
                color: theme.colorSuccess
                font.bold: true
                font.pixelSize: 12
            }
            Label {
                text: "命中 " + workspaceController.activeScanController.matchedCount
                color: theme.colorDanger
                font.bold: true
                font.pixelSize: 12
            }
            Label {
                text: "错误 " + workspaceController.activeScanController.errorCount
                color: theme.colorDanger
                font.bold: true
                font.pixelSize: 12
            }
            // 增量扫描显示复用与变更文件数（仅当有复用时显示，避免空载噪音）
            Label {
                text: "复用 " + workspaceController.activeScanController.reusedFiles
                color: theme.colorPrimary
                font.bold: true
                font.pixelSize: 12
                visible: workspaceController.activeScanController.reusedFiles > 0
            }
            Label {
                text: "重扫 " + workspaceController.activeScanController.changedFiles
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                font.bold: true
                font.pixelSize: 12
                visible: workspaceController.activeScanController.reusedFiles > 0
            }
            Item { Layout.fillWidth: true }

            // 暂停/继续按钮：扫描中显示「暂停」，已暂停显示「继续」
            IconButton {
                iconSource: workspaceController.activeScanController.isPaused
                    ? "qrc:/icons/scan.svg"
                    : "qrc:/icons/pause.svg"
                text: workspaceController.activeScanController.isPaused ? "继续" : "暂停"
                tooltip: workspaceController.activeScanController.isPaused ? "继续扫描" : "暂停扫描"
                accent: "secondary"
                onClicked: workspaceController.togglePause(card.workspaceId)
            }
            // 取消按钮：危险操作
            IconButton {
                iconSource: "qrc:/icons/stop.svg"
                text: "取消"
                tooltip: "取消扫描"
                accent: "danger"
                onClicked: workspaceController.cancelScan(card.workspaceId)
            }
        }
    }
}
