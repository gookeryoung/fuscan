import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3 as Dialogs
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 规则面板：全局规则配置区
// 从 RulesPage.qml 提取，供 HomePage 内嵌与 RulesPage 独立页共用。
// 包含：标题栏（标题 + 导入/导出 + 规则数）+ 左右分栏（规则文件列表 + 规则列表）
Item {
    id: rulesPanel
    property ThemeController theme: Theme
    property RulesControllerType rulesController: RulesController
    // 可折叠属性。collapsible=true 时显示展开/收起按钮，
    // collapsed=true 时仅显示标题栏，主区域隐藏。
    // HomePage 内嵌时启用可折叠（默认收起），RulesPage 独立页保持展开。
    property bool collapsible: false
    property bool collapsed: false

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

    // 规则集导入对话框
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

    // 规则集导出对话框
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

    // 导入/导出操作结果通知（Toast 风格）
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
            // 使用 Qt 5.15+ 新语法 function onFoo()，消除
            // "Implicitly defined onFoo properties in Connections are deprecated" 警告
            function onRulesIoCompleted(ok, msg) {
                ioToast.success = ok
                ioToast.message = msg
                toastTimer.restart()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        // 标题行：标题 + 导入/导出 + 规则数 + 展开/收起按钮
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "全局规则"
                font.pixelSize: 16
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            // 收起态隐藏导入/导出按钮，标题栏更紧凑
            IconButton {
                text: "导入"
                tooltip: "从 YAML/JSON 文件导入规则集"
                accent: "ghost"
                visible: !rulesPanel.collapsed
                onClicked: importFileDialog.open()
            }
            IconButton {
                text: "导出"
                tooltip: "导出当前规则集到 YAML/JSON 文件"
                accent: "ghost"
                enabled: rulesController.ruleCount > 0
                visible: !rulesPanel.collapsed
                onClicked: exportFileDialog.open()
            }
            Label {
                text: "共 " + rulesController.ruleCount + " 条规则"
                font.pixelSize: 12
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.leftMargin: 8
            }
            // 可折叠模式下显示展开/收起按钮
            IconButton {
                visible: rulesPanel.collapsible
                iconSource: rulesPanel.collapsed
                    ? "qrc:/icons/down_arrow.svg"
                    : "qrc:/icons/up_arrow.svg"
                text: rulesPanel.collapsed ? "展开" : "收起"
                tooltip: rulesPanel.collapsed ? "展开全局规则配置" : "收起全局规则配置"
                accent: "ghost"
                onClicked: rulesPanel.collapsed = !rulesPanel.collapsed
            }
        }

        // 主区域：左右分栏（收起态隐藏）
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
            visible: !rulesPanel.collapsed

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
                        // 预渲染屏幕外 delegate，避免滚动时重建
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
                            // 选中态文字加粗（在 delegate 上设置，contentItem
                            // 通过 parent.font 继承，避免在 Label 上同时设 font 与 font.bold
                            // 触发 "Property has already been assigned a value"）
                            font.bold: ListView.isCurrentItem
                            highlighted: ListView.isCurrentItem
                            // 文件缺失时禁用选中（仍可点击但视觉提示不可用）
                            enabled: modelData.exists
                            // ItemDelegate 在 Qt Quick Controls 2 不会自动设置
                            // ListView.currentIndex，需在 onClicked 显式同步选中
                            onClicked: rulesFileList.currentIndex = index
                            background: Rectangle {
                                // 暗色下 colorBgSelectedDark == colorBgHoverDark 完全无区别，
                                // 选中态改用主色 0.15 透明叠加 + 左侧 3px 色条 + 文字主色加粗，
                                // 与 hover 态形成明显视觉差异。
                                color: ListView.isCurrentItem
                                    ? Qt.rgba(theme.colorPrimary.r,
                                              theme.colorPrimary.g,
                                              theme.colorPrimary.b,
                                              0.15)
                                    : (parent.hovered
                                        ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                        : "transparent")
                                Behavior on color { ColorAnimation { duration: 120 } }
                                // 左侧 3px 色条强调选中态
                                Rectangle {
                                    visible: ListView.isCurrentItem
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: 3
                                    color: theme.colorPrimary
                                }
                            }
                            contentItem: RowLayout {
                                spacing: 6
                                Label {
                                    text: parent.parent.text
                                    font: parent.parent.font
                                    color: parent.parent.highlighted
                                        ? theme.colorPrimary
                                        : (modelData.exists
                                            ? (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                                            : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary))
                                    elide: Text.ElideMiddle
                                    verticalAlignment: Text.AlignVCenter
                                    Layout.fillWidth: true
                                    leftPadding: 12
                                }
                                // 缺失文件显示"缺失"标记
                                Rectangle {
                                    visible: !modelData.exists
                                    radius: 4
                                    height: 16
                                    width: missingLabel.implicitWidth + 8
                                    color: theme.colorDanger
                                    Layout.alignment: Qt.AlignVCenter
                                    Layout.rightMargin: 12
                                    Label {
                                        id: missingLabel
                                        anchors.centerIn: parent
                                        text: "缺失"
                                        font.pixelSize: 10
                                        font.bold: true
                                        color: "#FFFFFF"
                                    }
                                }
                                Item { Layout.fillWidth: !modelData.exists; Layout.rightMargin: 12 }
                            }
                        }
                    }

                    // 操作按钮
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        IconButton {
                            iconSource: "qrc:/icons/up_arrow.svg"
                            tooltip: "上移"
                            accent: "ghost"
                            enabled: rulesController.canMoveUp
                            onClicked: rulesController.moveUp()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/down_arrow.svg"
                            tooltip: "下移"
                            accent: "ghost"
                            enabled: rulesController.canMoveDown
                            onClicked: rulesController.moveDown()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/minus.svg"
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
                        // 预渲染屏幕外 delegate，避免滚动时重建
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
