import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: aboutPage

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
                color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
            }

            // Logo 区
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: 80
                height: 80
                radius: 16
                color: Theme.colorPrimary
                Label {
                    anchors.centerIn: parent
                    text: "F"
                    color: Theme.colorTextOnPrimary
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
                    color: Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "v" + AboutController.version
                    font.pixelSize: 13
                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: AboutController.description
                    font.pixelSize: 12
                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "作者: " + AboutController.author + " · " + AboutController.license
                    font.pixelSize: 11
                    color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                }
            }

            // 用户手册入口
            Button {
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredHeight: Theme.btnHeightSecondary
                Layout.preferredWidth: 200
                text: "打开用户手册 PDF"
                onClicked: AboutController.openManual()
            }

            // 第三方依赖
            GroupBox {
                Layout.fillWidth: true
                title: "第三方依赖"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 4
                    Repeater {
                        model: AboutController.dependencies
                        Label {
                            text: modelData
                            font.pixelSize: 11
                            color: Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
                        }
                    }
                }
            }
        }
    }
}
