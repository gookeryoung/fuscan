import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "."

// 文件监控面板：拖拽或选择文件夹加入监控，实时展示命中。
// watchdog 事件驱动，命中后通过 FileMonitorController.hitFound 信号触发
// 托盘通知 + 声音（app.py 中连接）；本面板仅做界面展示与目录管理。
ColumnLayout {
    id: panel
    property ThemeController theme: Theme
    property FileMonitorControllerType monitorController: FileMonitorController

    spacing: 8
    Layout.fillWidth: true

    // ---------- 标题栏 ----------
    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Label {
            text: "文件监控"
            font.pixelSize: theme.fontSizeCaption
            font.bold: true
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
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

    // ---------- 拖拽接收区（无监控目录时显示） ----------
    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 60
        visible: monitorController.watchedCount === 0
        radius: theme.radiusMd
        border.color: dropAreaMonitor.containsDrag ? theme.colorPrimary
            : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
        border.width: 1
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

        Label {
            anchors.centerIn: parent
            text: "拖拽文件夹到此处，或点击「添加监控文件夹」"
            font.pixelSize: 12
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
        }
    }

    // ---------- 监控目录列表 ----------
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        visible: monitorController.watchedCount > 0

        Repeater {
            model: monitorController.watchedDirectories
            delegate: RowLayout {
                Layout.fillWidth: true
                spacing: 6
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

    // ---------- 命中列表 ----------
    ScrollView {
        Layout.fillWidth: true
        Layout.preferredHeight: 180
        clip: true
        visible: monitorController.model.count > 0

        ListView {
            anchors.fill: parent
            model: monitorController.model
            spacing: 2

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
