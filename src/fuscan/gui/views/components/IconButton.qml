import QtQuick 2.15
import QtQuick.Controls 2.15
import fuscan.theme 1.0

// 图标按钮：WSLDashboard 风格，统一四类层级（primary/secondary/ghost/danger）
// 通过 accent 属性切换风格，text 放图标 emoji/Unicode，tooltip 鼠标悬停提示
Button {
    id: control

    property string tooltip: ""
    // accent: "primary"（主色填充）/ "secondary"（描边）/ "ghost"（扁平兜底）/ "danger"（危险红描边）
    property string accent: "ghost"
    property int btnSize: accent === "primary" ? Theme.btnHeightSecondary
        : accent === "secondary" ? Theme.btnHeightSecondary
        : Theme.btnHeightGhost
    // dangerColor：accent="danger" 时使用，默认跟随主题危险色
    property color dangerColor: Theme.colorDanger

    implicitWidth: btnSize
    implicitHeight: btnSize
    padding: 0

    ToolTip.visible: hovered && tooltip.length > 0
    ToolTip.text: tooltip
    ToolTip.delay: 400

    background: Rectangle {
        radius: control.accent === "primary" ? Theme.btnRadiusPrimary
            : control.accent === "secondary" ? Theme.btnRadiusSecondary
            : Theme.btnRadiusGhost
        color: {
            if (!control.enabled) {
                return "transparent"
            }
            if (control.down) {
                if (control.accent === "primary") return Theme.colorPrimaryDark
                if (control.accent === "danger") return control.dangerColor
                return Theme.isDark ? Theme.colorBgHoverDark : Theme.colorBgHover
            }
            if (control.hovered) {
                if (control.accent === "primary") return Theme.colorPrimary
                return Theme.isDark ? Theme.colorBgHoverDark : Theme.colorBgHover
            }
            if (control.accent === "primary") return Theme.colorPrimary
            return "transparent"
        }
        border.color: {
            if (!control.enabled) return "transparent"
            if (control.accent === "secondary") {
                return Theme.isDark ? Theme.colorBorderDark : Theme.colorBorder
            }
            if (control.accent === "danger") {
                return control.dangerColor
            }
            return "transparent"
        }
        border.width: (control.accent === "secondary" || control.accent === "danger") ? 1 : 0
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    contentItem: Label {
        text: control.text
        font.pixelSize: control.accent === "primary" ? 14 : 13
        color: {
            if (!control.enabled) {
                return Theme.isDark ? Theme.colorTextSecondary : Theme.colorTextSecondary
            }
            if (control.accent === "primary") return Theme.colorTextOnPrimary
            if (control.accent === "danger") {
                return control.down ? Theme.colorTextOnPrimary : control.dangerColor
            }
            return Theme.isDark ? Theme.colorTextPrimary : Theme.colorTextPrimary
        }
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
