import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

Item {
    id: aboutPage
    property ThemeController theme: Theme
    property AboutControllerType aboutController: AboutController

    // 打开手册/配置目录失败时的 Toast 提示
    Rectangle {
        id: openToast
        property string message: ""
        visible: message.length > 0
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 16
        width: Math.min(toastLabel.implicitWidth + 32, parent.width - 32)
        height: toastLabel.implicitHeight + 16
        radius: 6
        color: theme.colorDanger
        opacity: 0.95
        z: 100

        Label {
            id: toastLabel
            anchors.centerIn: parent
            text: openToast.message
            color: "#FFFFFF"
            font.pixelSize: 12
            elide: Text.ElideRight
        }

        Timer {
            id: toastTimer
            interval: 3000
            repeat: false
            onTriggered: openToast.message = ""
        }

        Connections {
            target: aboutController
            function onOpenFailed(msg) {
                openToast.message = msg
                toastTimer.restart()
            }
        }
    }

    ScrollView {
        id: scrollView
        anchors.fill: parent
        clip: true
        ScrollBar.vertical.policy: ScrollBar.AsNeeded
        ColumnLayout {
            width: scrollView.width - (scrollView.ScrollBar.vertical.visible ? scrollView.ScrollBar.vertical.width : 0)
            spacing: 20

            // 标题
            Label {
                text: "关于"
                font.pixelSize: 22
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }

            // Logo 区
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: 80
                height: 80
                radius: 16
                color: theme.colorPrimary
                Label {
                    anchors.centerIn: parent
                    text: "F"
                    color: theme.colorTextOnPrimary
                    font.pixelSize: 40
                    font.bold: true
                }
            }

            // 应用信息
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                spacing: 4
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "fuscan"
                    font.pixelSize: 24
                    font.bold: true
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "v" + aboutController.version
                    font.pixelSize: 13
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: aboutController.description
                    font.pixelSize: 12
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "作者: " + aboutController.author + " · " + aboutController.license
                    font.pixelSize: 11
                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                }
            }

            // 快捷入口
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 12
                IconButton {
                    iconSource: "qrc:/icons/manual.svg"
                    text: "用户手册"
                    tooltip: "打开用户手册 PDF"
                    accent: "primary"
                    Layout.preferredWidth: 160
                    onClicked: aboutController.openManual()
                }
                IconButton {
                    iconSource: "qrc:/icons/folder.svg"
                    text: "配置目录"
                    tooltip: "打开配置目录"
                    accent: "secondary"
                    Layout.preferredWidth: 160
                    onClicked: aboutController.openConfigDir()
                }
            }

            // 引擎状态 + 第三方依赖 左右并列
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                // 引擎状态（原生匹配引擎 fuscan-core + OCR 引擎 RapidOCR）
                GroupBox {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    Layout.alignment: Qt.AlignTop
                    title: "引擎状态"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 4
                        Repeater {
                            model: aboutController.nativeEngines
                            Label {
                                text: modelData
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                        // OCR 引擎状态：展示启用情况与未启用原因（exe 缺失/模型文件缺失）
                        Label {
                            text: aboutController.ocrEngine
                            font.pixelSize: 11
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                        // OCR 各依赖项：已安装绿色勾 + 版本号，未安装红色叉
                        Repeater {
                            model: aboutController.ocrDependencies
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Label {
                                    text: modelData.installed ? "✓" : "✗"
                                    color: modelData.installed ? theme.colorSuccess : theme.colorDanger
                                    font.pixelSize: 11
                                    font.bold: true
                                }
                                Label {
                                    text: modelData.name + (modelData.version ? " " + modelData.version : "")
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }

                // 第三方依赖
                GroupBox {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    Layout.alignment: Qt.AlignTop
                    title: "第三方依赖"
                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 4
                        Repeater {
                            model: aboutController.dependencies
                            Label {
                                text: modelData
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }
    }
}
