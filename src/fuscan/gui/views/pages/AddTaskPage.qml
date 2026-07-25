import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Dialogs 1.3
import fuscan.theme 1.0
import fuscan.controllers 1.0

// 添加任务页：新建工作区表单
Item {
    id: addTaskPage
    property ThemeController theme: Theme
    property WorkspaceControllerType workspaceController: WorkspaceController
    property ConfigControllerType configController: ConfigController
    property RulesControllerType rulesController: RulesController

    // 表单状态
    property string taskName: ""
    property int modeIndex: 2  // 默认文件夹扫描
    property string selectedDrive: ""
    property string folderRoot: ""
    property var rulesPaths: []
    property bool useBuiltin: true

    // 文件夹选择对话框
    FileDialog {
        id: folderDialog
        title: "选择扫描目录"
        selectFolder: true
        selectExisting: true
        folder: folderRoot.length > 0 ? "file:///" + folderRoot : shortcuts.home
        onAccepted: {
            folderRoot = folderDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
        }
    }

    // 规则文件选择对话框
    FileDialog {
        id: rulesFileDialog
        title: "选择规则文件"
        selectExisting: true
        nameFilters: ["YAML 文件 (*.yaml *.yml)", "所有文件 (*.*)"]
        onAccepted: {
            var path = rulesFileDialog.fileUrl.toString().replace(/^file:\/\/\//, "")
            if (rulesPaths.indexOf(path) < 0) {
                var paths = rulesPaths.slice()
                paths.push(path)
                rulesPaths = paths
            }
        }
    }

    // 信号：创建成功后返回首页
    signal created()
    signal cancelRequested()

    ScrollView {
        anchors.fill: parent
        clip: true

        ColumnLayout {
            width: addTaskPage.width - 48
            spacing: 16

            // ---------- 标题 ----------
            Label {
                text: "新建扫描任务"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }

            // ---------- 任务名称 ----------
            Label {
                text: "任务名称"
                font.pixelSize: theme.fontSizeBody
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            TextField {
                Layout.fillWidth: true
                Layout.preferredHeight: theme.btnHeightSecondary
                placeholderText: "留空自动生成（如「任务 1」）"
                text: taskName
                onTextChanged: taskName = text
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                background: Rectangle {
                    color: "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusSecondary
                }
            }

            // ---------- 扫描模式 ----------
            Label {
                text: "扫描模式"
                font.pixelSize: theme.fontSizeBody
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            TabBar {
                id: modeTabBar
                Layout.fillWidth: true
                currentIndex: modeIndex
                onCurrentIndexChanged: modeIndex = currentIndex
                TabButton {
                    text: "全盘扫描"
                    height: theme.btnHeightSecondary
                }
                TabButton {
                    text: "盘符扫描"
                    height: theme.btnHeightSecondary
                }
                TabButton {
                    text: "文件夹扫描"
                    height: theme.btnHeightSecondary
                }
            }

            // ---------- 目标路径（动态显示） ----------
            // 全盘扫描：不显示
            // 盘符扫描：盘符按钮列表
            // 文件夹扫描：路径输入 + 选择按钮
            Loader {
                Layout.fillWidth: true
                active: modeIndex !== 0
                sourceComponent: modeIndex === 1 ? driveComponent : folderComponent
            }

            // ---------- 规则配置 ----------
            Label {
                text: "规则配置"
                font.pixelSize: theme.fontSizeBody
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            CheckBox {
                text: "启用内置通用规则"
                checked: useBuiltin
                onCheckedChanged: useBuiltin = checked
            }
            // 规则文件列表
            Repeater {
                model: rulesPaths
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label {
                        text: modelData
                        font.pixelSize: 12
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                    }
                    Button {
                        Layout.preferredHeight: theme.btnHeightGhost
                        Layout.preferredWidth: 32
                        text: "×"
                        onClicked: {
                            var paths = rulesPaths.slice()
                            paths.splice(index, 1)
                            rulesPaths = paths
                        }
                    }
                }
            }
            Button {
                Layout.preferredHeight: theme.btnHeightSecondary
                text: "➕ 添加规则文件"
                onClicked: rulesFileDialog.open()
                background: Rectangle {
                    color: parent.down
                          ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                          : "transparent"
                    border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                    border.width: 1
                    radius: theme.btnRadiusSecondary
                }
                contentItem: Label {
                    text: parent.text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            Item { Layout.fillHeight: true }  // 弹性撑开

            // ---------- 操作按钮 ----------
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    text: "取消"
                    onClicked: addTaskPage.cancelRequested()
                    background: Rectangle {
                        color: parent.down
                              ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                              : "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusSecondary
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightPrimary
                    text: "创建任务"
                    enabled: modeIndex === 0 || (modeIndex === 1 && selectedDrive.length > 0)
                             || (modeIndex === 2 && folderRoot.length > 0)
                    onClicked: {
                        var modeStr = modeIndex === 0 ? "full" : (modeIndex === 1 ? "drive" : "folder")
                        var target = modeIndex === 0 ? "" : (modeIndex === 1 ? selectedDrive : folderRoot)
                        var rulesJson = JSON.stringify(rulesPaths)
                        workspaceController.addWorkspace(taskName, modeStr, target, rulesJson, useBuiltin)
                        addTaskPage.resetForm()
                        addTaskPage.created()
                    }
                    background: Rectangle {
                        color: parent.enabled
                          ? (parent.down ? theme.colorPrimaryDark : theme.colorPrimary)
                          : theme.colorBorder
                        radius: theme.btnRadiusPrimary
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.colorTextOnPrimary
                        font.pixelSize: 13
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }

    // ---------- 动态组件：盘符选择 ----------
    Component {
        id: driveComponent
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "选择盘符"
                font.pixelSize: theme.fontSizeBody
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: configController.drives
                    delegate: Button {
                        Layout.preferredWidth: 80
                        Layout.preferredHeight: theme.btnHeightSecondary
                        text: modelData
                        checkable: true
                        checked: selectedDrive === modelData
                        onClicked: selectedDrive = modelData
                        background: Rectangle {
                            color: parent.checked
                                  ? (parent.down ? theme.colorPrimaryDark : theme.colorPrimary)
                                  : (parent.down
                                      ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                                      : "transparent")
                            border.color: parent.checked ? theme.colorPrimary
                                : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                            border.width: 1
                            radius: theme.btnRadiusSecondary
                            Behavior on color { ColorAnimation { duration: 120 } }
                        }
                        contentItem: Label {
                            text: parent.text
                            color: parent.checked ? theme.colorTextOnPrimary
                                : (theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary)
                            font.pixelSize: 12
                            font.bold: parent.checked
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
                // 盘符为空时提示
                Label {
                    visible: configController.drives.length === 0
                    text: "未检测到可用盘符"
                    font.pixelSize: 12
                    color: theme.colorWarning
                }
            }
        }
    }

    // ---------- 动态组件：文件夹选择 ----------
    Component {
        id: folderComponent
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "扫描目录"
                font.pixelSize: theme.fontSizeBody
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    Layout.fillWidth: true
                    Layout.preferredHeight: theme.btnHeightSecondary
                    placeholderText: "选择或输入扫描目录"
                    text: folderRoot
                    onTextChanged: folderRoot = text
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    background: Rectangle {
                        color: "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusSecondary
                    }
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    text: "选择..."
                    onClicked: folderDialog.open()
                    background: Rectangle {
                        color: parent.down
                              ? (theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover)
                              : "transparent"
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.btnRadiusSecondary
                    }
                    contentItem: Label {
                        text: parent.text
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }

    // 重置表单
    function resetForm() {
        taskName = ""
        modeIndex = 2
        selectedDrive = ""
        folderRoot = ""
        rulesPaths = []
        useBuiltin = true
    }
}
