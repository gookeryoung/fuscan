import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ItemDelegate {
    id: navItem
    property string iconText: ""
    property string label: ""
    property string pageId: ""
    property bool selected: false
    signal clicked()

    Layout.fillWidth: true
    Layout.preferredHeight: 40
    leftPadding: 0

    // 背景
    background: Rectangle {
        color: navItem.selected
              ? (Theme.isDark ? Theme.colorBgSelected : Theme.colorBgSelected)
              : "transparent"
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    // 左侧 3px 选中指示条
    Rectangle {
        width: 3
        height: parent.height * 0.55
        anchors.verticalCenter: parent.verticalCenter
        color: navItem.selected ? Theme.colorPrimary : "transparent"
        radius: 2
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    // 图标 + 文字
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 18
        anchors.rightMargin: 12
        spacing: 12

        Label {
            text: navItem.iconText
            font.pixelSize: 14
            Layout.preferredWidth: 20
            horizontalAlignment: Text.AlignHCenter
        }
        Label {
            text: navItem.label
            font.pixelSize: 13
            color: navItem.selected
                  ? (Theme.isDark ? Theme.colorTextPrimary : Theme.colorPrimary)
                  : (Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary)
            Behavior on color { ColorAnimation { duration: 120 } }
        }
    }

    onClicked: navItem.clicked()
}
