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

        // ---------- 第三行：双进度条（收集 + 解析） ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8

            // ----- 阶段 1：收集文件清单（walk） -----
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    // 阶段标识圆点：进行中=主色脉冲，完成=成功色，未开始=灰
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: workspaceController.activeScanController.walkDone
                            ? theme.colorSuccess
                            : (workspaceController.activeScanController.scanPhase === "walk"
                               ? theme.colorPrimary : theme.colorBorder)
                    }
                    Label {
                        text: "收集文件清单"
                        font.pixelSize: 11
                        font.bold: workspaceController.activeScanController.scanPhase === "walk"
                        color: workspaceController.activeScanController.walkDone
                            ? theme.colorSuccess
                            : (workspaceController.activeScanController.scanPhase === "walk"
                               ? theme.colorPrimary
                               : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                    }
                    Item { Layout.fillWidth: true }
                    // 统计文字精简——去掉冗长前缀，改为「纳入 / 发现 · 跳过 N」紧凑格式
                    // 跳过数 = 类型不符 + 用户标记，仅在有跳过时显示，避免空载噪音
                    Label {
                        text: workspaceController.activeScanController.walkIndeterminate
                            ? "统计中..."
                            : (workspaceController.activeScanController.walkClassified
                               + " / " + workspaceController.activeScanController.walkDiscovered
                               + ((workspaceController.activeScanController.walkSkipped > 0
                                   || workspaceController.activeScanController.walkUserSkipped > 0)
                                  ? " · 跳过 "
                                    + (workspaceController.activeScanController.walkSkipped
                                       + workspaceController.activeScanController.walkUserSkipped)
                                  : ""))
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                }
                ProgressBar {
                    id: walkProgressBar
                    Layout.fillWidth: true
                    // walk 阶段无确定 total：刚启动 indeterminate，收到首个进度后按"已分类占比"显示
                    indeterminate: workspaceController.activeScanController.walkIndeterminate
                    from: 0.0
                    to: 100.0
                    value: workspaceController.activeScanController.walkProgress
                    background: Rectangle {
                        implicitHeight: 6
                        color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        radius: 3
                    }
                    contentItem: Item {
                        implicitHeight: 6
                        Rectangle {
                            width: walkProgressBar.visualPosition * parent.width
                            height: parent.height
                            radius: 3
                            color: workspaceController.activeScanController.walkDone
                                ? theme.colorSuccess : theme.colorPrimary
                        }
                    }
                }
            }

            // ----- 阶段 2：解析文件内容（scan） -----
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: workspaceController.activeScanController.scanDone
                            ? theme.colorSuccess
                            : (workspaceController.activeScanController.scanPhase === "scan"
                               || workspaceController.activeScanController.scanPhase === "archive"
                               ? card.statusColor()
                               : theme.colorBorder)
                    }
                    Label {
                        text: "解析文件内容"
                        font.pixelSize: 11
                        font.bold: workspaceController.activeScanController.scanPhase === "scan"
                            || workspaceController.activeScanController.scanPhase === "archive"
                        color: (workspaceController.activeScanController.scanPhase === "scan"
                                || workspaceController.activeScanController.scanPhase === "archive")
                            ? card.statusColor()
                            : (workspaceController.activeScanController.scanDone
                               ? theme.colorSuccess
                               : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: workspaceController.activeScanController.progressIndeterminate
                            ? "等待中..."
                            : (workspaceController.activeScanController.progressScanned + " / "
                               + workspaceController.activeScanController.progressTotal
                               + (workspaceController.activeScanController.archiveEntryCount > 0
                                  ? "（含压缩包 " + workspaceController.activeScanController.archiveEntryCount + "）"
                                  : ""))
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    }
                }
                ProgressBar {
                    id: scanProgressBar
                    Layout.fillWidth: true
                    // scan 阶段未开始时 indeterminate（walk 进行中）
                    indeterminate: workspaceController.activeScanController.progressIndeterminate
                    // 改用 progress 百分比（0-100），扫描完成时 progress=100 进度条满。
                    // 原 progressScanned/progressTotal 在扫描完成时可能 scanned<total
                    // （错误文件未计入），导致 visualPosition<1 进度条未满。
                    from: 0.0
                    to: 100.0
                    value: workspaceController.activeScanController.progress
                    background: Rectangle {
                        implicitHeight: 6
                        color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        radius: 3
                    }
                    contentItem: Item {
                        implicitHeight: 6
                        Rectangle {
                            width: scanProgressBar.visualPosition * parent.width
                            height: parent.height
                            radius: 3
                            color: workspaceController.activeScanController.scanDone
                                ? theme.colorSuccess : card.statusColor()
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
