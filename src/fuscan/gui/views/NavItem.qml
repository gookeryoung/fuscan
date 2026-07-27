import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0

ItemDelegate {
    id: navItem
    property ThemeController theme: Theme
    // 图标 qrc 路径（如 "qrc:/icons/home.svg"），优先于 iconText
    property string iconSource: ""
    // 兼容旧 emoji 文本（iconSource 为空时使用）
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

    // 当前图标/文字色：选中=主色，未选中=次要色
    readonly property color _foreground: navItem.selected
        ? (theme.isDark ? theme.colorPrimary : theme.colorPrimary)
        : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)

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

        // 图标区：优先 SVG（ColorOverlay 染色），回退到 emoji 文本
        Item {
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
            visible: navItem.iconSource.length > 0

            Image {
                id: navIcon
                anchors.centerIn: parent
                width: 16
                height: 16
                source: navItem.iconSource
                sourceSize: Qt.size(16, 16)
                visible: false
            }
            ColorOverlay {
                anchors.fill: navIcon
                source: navIcon
                color: navItem._foreground
                Behavior on color { ColorAnimation { duration: 120 } }
            }
        }

        // 旧 emoji 文本回退（iconSource 为空时显示）
        Label {
            text: navItem.iconText
            font.pixelSize: 14
            Layout.preferredWidth: 20
            horizontalAlignment: Text.AlignHCenter
            visible: navItem.iconSource.length === 0
            color: navItem._foreground
            Behavior on color { ColorAnimation { duration: 120 } }
        }

        Label {
            text: navItem.label
            font.pixelSize: 13
            color: navItem._foreground
            Behavior on color { ColorAnimation { duration: 120 } }
        }
    }
}
