import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

ItemDelegate {
    id: navItem
    property ThemeController theme: Theme
    property string iconText: ""
    property string label: ""
    property string pageId: ""
    property bool selected: false
    // clicked() 信号继承自 ItemDelegate，无需重复声明
    // 外部通过 onClicked 连接处理页面切换（见 Sidebar.qml），禁止在此 emit clicked()
    // 否则会导致无限递归（onClicked → clicked() → onClicked → ...）直至栈溢出崩溃

    Layout.fillWidth: true
    Layout.preferredHeight: 40
    leftPadding: 0

    // 背景
    background: Rectangle {
        color: navItem.selected
              ? (theme.isDark ? theme.colorBgSelected : theme.colorBgSelected)
              : "transparent"
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    // 左侧 3px 选中指示条
    Rectangle {
        width: 3
        height: parent.height * 0.55
        anchors.verticalCenter: parent.verticalCenter
        color: navItem.selected ? theme.colorPrimary : "transparent"
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
                  ? (theme.isDark ? theme.colorTextPrimary : theme.colorPrimary)
                  : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
            Behavior on color { ColorAnimation { duration: 120 } }
        }
    }
}
