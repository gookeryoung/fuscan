import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0

Item {
    id: aboutPage
    property ThemeController theme: Theme
    property AboutControllerType aboutController: AboutController

    ScrollView {
        anchors.fill: parent
        clip: true
        ColumnLayout {
            width: aboutPage.width
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
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    Layout.preferredWidth: 180
                    text: "打开用户手册 PDF"
                    onClicked: aboutController.openManual()
                }
                Button {
                    Layout.preferredHeight: theme.btnHeightSecondary
                    Layout.preferredWidth: 180
                    text: "打开配置目录"
                    onClicked: aboutController.openConfigDir()
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

            // 第三方依赖
            GroupBox {
                Layout.fillWidth: true
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
                        }
                    }
                }
            }
        }
    }
}
