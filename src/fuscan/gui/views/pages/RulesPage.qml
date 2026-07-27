import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

Item {
    id: rulesPage
    property ThemeController theme: Theme
    property RulesControllerType rulesController: RulesController

    // 信号：返回首页/工作区
    signal backRequested()

    // 规则文件选择对话框（QML FileDialog，替代 QWidget QFileDialog）
    Dialogs.FileDialog {
        id: rulesFileDialog
        title: "选择规则文件"
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = rulesFileDialog.fileUrl.toString()
            // file:/// 前缀转本地路径
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.loadFileFromPath(pathStr)
        }
    }

    // iter-122：规则集导入对话框
    Dialogs.FileDialog {
        id: importFileDialog
        title: "导入规则集"
        nameFilters: ["YAML/JSON 文件 (*.yaml *.yml *.json)", "所有文件 (*.*)"]
        onAccepted: {
            var pathStr = importFileDialog.fileUrl.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.importRuleset(pathStr)
        }
    }

    // iter-122：规则集导出对话框
    Dialogs.FileDialog {
        id: exportFileDialog
        title: "导出规则集"
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "JSON 文件 (*.json)", "所有文件 (*.*)"]
        selectExisting: false
        defaultSuffix: "yaml"
        onAccepted: {
            var pathStr = exportFileDialog.fileUrl.toString()
            if (pathStr.startsWith("file:///")) {
                pathStr = decodeURIComponent(pathStr.substring(8))
            }
            rulesController.exportRuleset(pathStr)
        }
    }

    // iter-122：模板选择对话框
    Dialog {
        id: templateDialog
        title: "选择规则模板"
        modal: true
        anchors.centerIn: parent
        width: 420
        height: 360

        background: Rectangle {
            color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
            border.width: 1
            radius: 8
        }

        contentItem: ColumnLayout {
            spacing: 8

            Label {
                text: "选择内置规则模板（加载后可自定义）"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.topMargin: 12
            }

            ListView {
                id: templateListView
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                clip: true
                cacheBuffer: 500
                model: rulesController.templateList
                delegate: ItemDelegate {
                    width: templateListView.width
                    height: 56
                    onClicked: {
                        rulesController.loadTemplate(modelData.name)
                        templateDialog.close()
                    }
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: modelData.name
                                font.pixelSize: 12
                                font.bold: true
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            }
                            Item { Layout.fillWidth: true }
                            // 加载按钮（L3 辅助层级，32px 扁平）
                            Button {
                                text: "加载"
                                flat: true
                                font.pixelSize: 11
                                onClicked: {
                                    rulesController.loadTemplate(modelData.name)
                                    templateDialog.close()
                                }
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: modelData.description
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            elide: Text.ElideRight
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                            visible: index < templateListView.count - 1
                        }
                    }
                }
            }

            // 底部按钮区
            RowLayout {
                Layout.fillWidth: true
                Layout.rightMargin: 12
                Layout.bottomMargin: 12
                Layout.topMargin: 4
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: templateDialog.close()
                }
            }
        }
    }

    // iter-122：导入/导出/模板操作结果通知（Toast 风格）
    Rectangle {
        id: ioToast
        property bool success: false
        property string message: ""
        visible: message.length > 0
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 16
        width: Math.min(toastLabel.implicitWidth + 32, parent.width - 32)
        height: toastLabel.implicitHeight + 16
        radius: 6
        color: success ? theme.colorSuccess : theme.colorDanger
        opacity: 0.95
        z: 100

        Label {
            id: toastLabel
            anchors.centerIn: parent
            text: ioToast.message
            color: "#FFFFFF"
            font.pixelSize: 12
            elide: Text.ElideRight
        }

        // 3 秒后自动消失
        Timer {
            id: toastTimer
            interval: 3000
            repeat: false
            onTriggered: ioToast.message = ""
        }

        Connections {
            target: rulesController
            onRulesIoCompleted: function(ok, msg) {
                ioToast.success = ok
                ioToast.message = msg
                toastTimer.restart()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // 顶部标题 + 返回按钮
        RowLayout {
            Layout.fillWidth: true
            IconButton {
                text:"← 返回"
                tooltip: "返回工作区"
                accent: "secondary"
                onClicked: rulesPage.backRequested()
            }
            Label {
                // iter-107：绑定工作区时显示「规则 — 任务名」，否则显示「全局规则」
                text: rulesController.isBound
                    ? "规则 — " + rulesController.boundWorkspaceName
                    : "全局规则"
                font.pixelSize: 22
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                Layout.leftMargin: 8
            }
            Item { Layout.fillWidth: true }
            // iter-122：导入/导出/模板按钮
            IconButton {
                text: "模板"
                tooltip: "选择内置规则模板"
                accent: "ghost"
                onClicked: templateDialog.open()
            }
            IconButton {
                text: "导入"
                tooltip: "从 YAML/JSON 文件导入规则集"
                accent: "ghost"
                onClicked: importFileDialog.open()
            }
            IconButton {
                text: "导出"
                tooltip: "导出当前规则集到 YAML/JSON 文件"
                accent: "ghost"
                enabled: rulesController.ruleCount > 0
                onClicked: exportFileDialog.open()
            }
            Label {
                // 绑定工作区时提示「仅对该任务生效」
                visible: rulesController.isBound
                text: "仅对该任务生效"
                font.pixelSize: 11
                color: theme.colorPrimary
                Layout.leftMargin: 8
            }
            Label {
                text: "共 " + rulesController.ruleCount + " 条规则"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.leftMargin: 8
            }
        }

        // 主区域：左右分栏
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // ---------- 左侧：规则文件列表 ----------
            Rectangle {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: 8

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Label {
                        text: "规则文件"
                        font.pixelSize: 14
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    // 内置规则勾选
                    RowLayout {
                        Layout.fillWidth: true
                        CheckBox {
                            text: "内置通用规则"
                            checked: rulesController.useBuiltin
                            onCheckedChanged: rulesController.setUseBuiltin(checked)
                        }
                    }

                    // 规则文件列表
                    ListView {
                        id: rulesFileList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        // iter-106 P1：预渲染屏幕外 delegate，避免滚动时重建
                        cacheBuffer: 500
                        model: rulesController.rulesFileModel
                        currentIndex: rulesController.selectedFileIndex
                        onCurrentIndexChanged: rulesController.setSelectedFileIndex(currentIndex)
                        delegate: ItemDelegate {
                            width: rulesFileList.width
                            height: 36
                            // QVariantList of dict 通过 modelData 访问字段
                            text: modelData.fileName
                            font.pixelSize: 12
                            highlighted: ListView.isCurrentItem
                            // ItemDelegate 在 Qt Quick Controls 2 不会自动设置
                            // ListView.currentIndex，需在 onClicked 显式同步选中
                            onClicked: rulesFileList.currentIndex = index
                            background: Rectangle {
                                color: ListView.isCurrentItem
                                    ? (theme.isDark ? theme.colorBgSelectedDark : theme.colorBgSelected)
                                    : (parent.hovered
                                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                        : "transparent")
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }
                            contentItem: Label {
                                text: parent.text
                                font: parent.font
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                elide: Text.ElideMiddle
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: 12
                                rightPadding: 12
                            }
                        }
                    }

                    // 操作按钮
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        IconButton {
                            text:"↑"
                            tooltip: "上移"
                            accent: "ghost"
                            enabled: rulesController.canMoveUp
                            onClicked: rulesController.moveUp()
                        }
                        IconButton {
                            text:"↓"
                            tooltip: "下移"
                            accent: "ghost"
                            enabled: rulesController.canMoveDown
                            onClicked: rulesController.moveDown()
                        }
                        IconButton {
                            text:"−"
                            tooltip: "移除选中规则文件"
                            accent: "ghost"
                            enabled: rulesController.canRemove
                            onClicked: rulesController.removeSelected()
                        }
                        Item { Layout.fillWidth: true }
                        IconButton {
                            iconSource: "qrc:/icons/load_list.svg"
                            text: "加载"
                            tooltip: "加载规则文件"
                            accent: "ghost"
                            onClicked: rulesFileDialog.open()
                        }
                    }
                }
            }

            // ---------- 右侧：规则列表 ----------
            Rectangle {
                Layout.fillHeight: true
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                border.width: 1
                radius: 8

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Label {
                        text: "规则列表"
                        font.pixelSize: 14
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    ListView {
                        id: ruleListView
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        // iter-106 P1：预渲染屏幕外 delegate，避免滚动时重建
                        cacheBuffer: 1000
                        model: rulesController.ruleModel
                        delegate: ItemDelegate {
                            width: ruleListView.width
                            height: 56
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: model.name
                                        font.pixelSize: 13
                                        font.bold: true
                                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    }
                                    Item { Layout.fillWidth: true }
                                    Rectangle {
                                        radius: 8
                                        height: 18
                                        width: severityLabel.width + 12
                                        color: model.severityColor
                                        Label {
                                            id: severityLabel
                                            anchors.centerIn: parent
                                            text: model.severityText
                                            font.pixelSize: 10
                                            color: "#FFFFFF"
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: model.description
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    elide: Text.ElideRight
                                    visible: model.description.length > 0
                                }
                                // 底部分隔线（最后一项不显示）
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                    visible: index < ruleListView.count - 1
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
