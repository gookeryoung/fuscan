import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 文件监控独立页面：拖拽或选择文件夹加入监控，实时展示命中。
// watchdog 事件驱动，命中后通过 FileMonitorController.hitFound 信号触发
// 托盘通知 + 声音（app.py 中连接）；本页面仅做界面展示与目录管理。
Item {
    id: monitorPage
    property ThemeController theme: Theme
    property FileMonitorControllerType monitorController: FileMonitorController

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        // ---------- 页面标题栏 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Label {
                text: "文件监控"
                font.pixelSize: theme.fontSizeTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Label {
                text: monitorController.monitoringEnabled ? "监控中" : "已停止"
                font.pixelSize: 11
                color: monitorController.monitoringEnabled ? theme.colorSuccess : theme.colorTextSecondary
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            // 启停开关
            Switch {
                checked: monitorController.monitoringEnabled
                enabled: monitorController.watchedCount > 0
                onCheckedChanged: monitorController.setMonitoringEnabled(checked)
                ToolTip.text: checked ? "停止监控" : "开始监控"
                ToolTip.visible: hovered
            }
            IconButton {
                iconSource: "qrc:/icons/add.svg"
                text: "添加监控文件夹"
                accent: "secondary"
                onClicked: folderDialogForMonitor.open()
            }
            IconButton {
                iconSource: "qrc:/icons/delete.svg"
                text: "清空命中"
                accent: "ghost"
                enabled: monitorController.model.count > 0
                onClicked: monitorController.clearHits()
            }
        }

        // ---------- 拖拽接收区（无监控目录时显示，占满剩余空间） ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: monitorController.watchedCount === 0
            radius: theme.radiusMd
            border.color: dropAreaMonitor.containsDrag ? theme.colorPrimary
                : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
            border.width: dropAreaMonitor.containsDrag ? 2 : 1
            color: dropAreaMonitor.containsDrag
                ? Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.08)
                : "transparent"

            DropArea {
                id: dropAreaMonitor
                anchors.fill: parent
                keys: ["text/uri-list"]
                onDropped: {
                    if (drop.hasUrls) {
                        var added = 0
                        for (var i = 0; i < drop.urls.length; i++) {
                            var url = drop.urls[i].toString()
                            if (url.startsWith("file:///")) {
                                var p = decodeURIComponent(url.substring(8))
                                if (monitorController.addWatch(p)) added++
                            }
                        }
                        if (added > 0) {
                            monitorController.setMonitoringEnabled(true)
                        }
                    }
                }
            }

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 8

                Image {
                    source: "qrc:/icons/folder.svg"
                    sourceSize: Qt.size(48, 48)
                    Layout.alignment: Qt.AlignHCenter
                    opacity: 0.5
                }
                Label {
                    text: "拖拽文件夹到此处"
                    font.pixelSize: 14
                    font.bold: true
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "或点击「添加监控文件夹」"
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        // ---------- 监控目录列表 ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            visible: monitorController.watchedCount > 0

            Label {
                text: "监控目录（" + monitorController.watchedCount + "）"
                font.pixelSize: theme.fontSizeCaption
                font.bold: true
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
            }

            Repeater {
                model: monitorController.watchedDirectories
                delegate: Rectangle {
                    Layout.fillWidth: true
                    height: 32
                    radius: 4
                    color: theme.isDark ? theme.colorBgCardDark : theme.colorBgCard
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 6

                        Image {
                            source: "qrc:/icons/folder.svg"
                            sourceSize: Qt.size(14, 14)
                            opacity: 0.6
                        }
                        Label {
                            text: modelData
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                        IconButton {
                            iconSource: "qrc:/icons/close.svg"
                            accent: "ghost"
                            compact: true
                            tooltip: "移除监控"
                            onClicked: monitorController.removeWatch(modelData)
                        }
                    }
                }
            }
        }

        // ---------- 最近变更事件（紧凑日志，让用户看到监控在工作） ----------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            visible: monitorController.watchedCount > 0

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Label {
                    text: "最近变更"
                    font.pixelSize: theme.fontSizeCaption
                    font.bold: true
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                Rectangle {
                    width: eventCountLabel.implicitWidth + 12
                    height: eventCountLabel.implicitHeight + 4
                    radius: 8
                    color: monitorController.eventCount > 0
                        ? Qt.rgba(theme.colorPrimary.r, theme.colorPrimary.g, theme.colorPrimary.b, 0.15)
                        : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                    Label {
                        id: eventCountLabel
                        anchors.centerIn: parent
                        text: monitorController.eventCount + " 个事件"
                        font.pixelSize: 10
                        color: monitorController.eventCount > 0 ? theme.colorPrimary : theme.colorTextSecondary
                    }
                }
                // 过滤统计：仅当有过滤事件时显示
                Label {
                    id: filteredCountLabel
                    visible: monitorController.ignoredDirCount + monitorController.filteredExtCount + monitorController.dirEventCount > 0
                    text: "已过滤 " + (monitorController.ignoredDirCount + monitorController.filteredExtCount + monitorController.dirEventCount) + " 个"
                    font.pixelSize: 10
                    color: theme.colorTextSecondary
                    // Label 无 hovered/hoverEnabled 属性（Qt 5.15），用 MouseArea 悬停驱动 ToolTip
                    MouseArea {
                        id: filteredHover
                        anchors.fill: parent
                        hoverEnabled: true
                    }
                    ToolTip.text: "目录事件 " + monitorController.dirEventCount
                        + " / 噪声目录 " + monitorController.ignoredDirCount
                        + " / 扩展名不匹配 " + monitorController.filteredExtCount
                    ToolTip.visible: filteredHover.containsMouse
                }
                Item { Layout.fillWidth: true }
                IconButton {
                    iconSource: "qrc:/icons/delete.svg"
                    text: "清空事件"
                    accent: "ghost"
                    compact: true
                    enabled: monitorController.eventCount > 0
                    onClicked: monitorController.clearEvents()
                }
            }

            // 最近 3 条事件摘要（单行紧凑显示）
            Repeater {
                model: {
                    var events = monitorController.recentEvents
                    var recent = events.slice(Math.max(0, events.length - 3)).reverse()
                    return recent
                }
                delegate: Rectangle {
                    Layout.fillWidth: true
                    height: 24
                    radius: 3
                    color: theme.isDark ? Qt.rgba(theme.colorBgCardDark.r, theme.colorBgCardDark.g, theme.colorBgCardDark.b, 0.5) : Qt.rgba(theme.colorBgCard.r, theme.colorBgCard.g, theme.colorBgCard.b, 0.5)

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 8

                        Label {
                            text: modelData.time
                            font.pixelSize: 10
                            font.family: "Consolas"
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 55
                        }
                        Label {
                            text: modelData.event_type
                            font.pixelSize: 10
                            color: theme.colorPrimary
                            Layout.preferredWidth: 50
                        }
                        Label {
                            text: modelData.path
                            font.pixelSize: 10
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                    }
                }
            }
        }

        // ---------- 命中列表（占满剩余空间） ----------
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            visible: monitorController.watchedCount > 0

            ListView {
                anchors.fill: parent
                model: monitorController.model
                spacing: 2

                // 空状态提示：区分「无事件」和「有事件但无命中」
                Text {
                    anchors.centerIn: parent
                    visible: monitorController.model.count === 0
                    text: {
                        if (!monitorController.monitoringEnabled)
                            return "点击开关开始监控"
                        if (monitorController.eventCount > 0)
                            return "已接收 " + monitorController.eventCount + " 个变更事件，暂无命中"
                        return "等待文件变更…"
                    }
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }

                delegate: Rectangle {
                    width: ListView.view.width
                    height: 36
                    color: theme.isDark ? theme.colorBgCardDark : theme.colorBgCard
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: 4

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 8

                        Label {
                            text: model.time
                            font.pixelSize: 11
                            font.family: "Consolas"
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 60
                        }
                        Rectangle {
                            width: 4
                            height: 16
                            radius: 2
                            color: model.severityColor
                        }
                        Label {
                            text: model.ruleName
                            font.pixelSize: 11
                            font.bold: true
                            color: model.severityColor
                            Layout.preferredWidth: 120
                            elide: Text.ElideRight
                        }
                        Label {
                            text: model.filePath
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            Layout.fillWidth: true
                            elide: Text.ElideMiddle
                        }
                        Label {
                            text: model.matchText
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 220
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }

    // ---------- 文件夹选择对话框 ----------
    Dialogs.FileDialog {
        id: folderDialogForMonitor
        title: "选择监控文件夹"
        selectFolder: true
        selectMultiple: true
        onAccepted: {
            var added = 0
            for (var i = 0; i < folderDialogForMonitor.fileUrls.length; i++) {
                var url = folderDialogForMonitor.fileUrls[i].toString()
                if (url.startsWith("file:///")) {
                    var p = decodeURIComponent(url.substring(8))
                    if (monitorController.addWatch(p)) added++
                }
            }
            // 首次添加目录后自动启用监控
            if (added > 0 && !monitorController.monitoringEnabled) {
                monitorController.setMonitoringEnabled(true)
            }
        }
    }
}
