import QtQuick 2.15
import QtQuick.Controls 2.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0

// 图标按钮：WSLDashboard 风格，统一四类层级（primary/secondary/ghost/danger）
// 支持两种内容形式：
//   1. 纯文本按钮：text: "定义规则"
//   2. 图标 + 文本按钮：iconSource: "qrc:/icons/rules.svg"; text: "定义规则"
//   3. 纯图标按钮：iconSource: "qrc:/icons/close.svg"（text 留空）
//
// SVG 图标通过 ColorOverlay 实时染色为当前文本色，暗色模式自动反色，
// 避免黑色 SVG 在深色背景下不可见。
Button {
    id: control

    // 类型化访问 Theme context property，消除 setContextProperty 导致的
    // "Cannot read property 'xxx' of null" TypeError（详见 app_controller.py 注释）
    property ThemeController appTheme: Theme

    property string tooltip: ""
    // accent: "primary"（主色填充）/ "secondary"（描边）/ "ghost"（扁平兜底）/ "danger"（危险红描边）
    property string accent: "ghost"
    // 图标 qrc 路径（如 "qrc:/icons/rules.svg"），为空则不显示图标
    property string iconSource: ""
    // 图标尺寸（像素），默认 14 与正文字号一致
    property int iconSize: 14
    property int btnSize: accent === "primary" ? appTheme.btnHeightSecondary
        : accent === "secondary" ? appTheme.btnHeightSecondary
        : accent === "danger" ? appTheme.btnHeightSecondary
        : appTheme.btnHeightGhost
    // dangerColor：accent="danger" 时使用，默认跟随主题危险色
    property color dangerColor: appTheme.colorDanger

    // 当前前景色（图标与文本共用）：根据 enabled/accent/down 状态计算
    readonly property color _foreground: {
        if (!control.enabled) {
            return appTheme.isDark ? appTheme.colorTextSecondary : appTheme.colorTextSecondary
        }
        if (control.accent === "primary") return appTheme.colorTextOnPrimary
        if (control.accent === "danger") {
            return control.down ? appTheme.colorTextOnPrimary : control.dangerColor
        }
        return appTheme.isDark ? appTheme.colorTextPrimary : appTheme.colorTextPrimary
    }

    implicitHeight: btnSize
    // 宽度自适应内容（Button 默认 implicitWidth = contentItem.implicitWidth + padding）
    // 文字按钮如「选择」需足够宽度；左右 padding 12px 留白保证纯图标按钮不过窄
    leftPadding: 12
    rightPadding: 12
    topPadding: 0
    bottomPadding: 0

    ToolTip.visible: hovered && tooltip.length > 0
    ToolTip.text: tooltip
    ToolTip.delay: 400

    background: Rectangle {
        radius: control.accent === "primary" ? appTheme.btnRadiusPrimary
            : control.accent === "secondary" ? appTheme.btnRadiusSecondary
            : appTheme.btnRadiusGhost
        color: {
            if (!control.enabled) {
                return "transparent"
            }
            if (control.down) {
                if (control.accent === "primary") return appTheme.colorPrimaryDark
                if (control.accent === "danger") return control.dangerColor
                return appTheme.isDark ? appTheme.colorBgHoverDark : appTheme.colorBgHover
            }
            if (control.hovered) {
                if (control.accent === "primary") return appTheme.colorPrimary
                return appTheme.isDark ? appTheme.colorBgHoverDark : appTheme.colorBgHover
            }
            if (control.accent === "primary") return appTheme.colorPrimary
            return "transparent"
        }
        border.color: {
            if (!control.enabled) return "transparent"
            if (control.accent === "secondary") {
                return appTheme.isDark ? appTheme.colorBorderDark : appTheme.colorBorder
            }
            if (control.accent === "danger") {
                return control.dangerColor
            }
            return "transparent"
        }
        border.width: (control.accent === "secondary" || control.accent === "danger") ? 1 : 0
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight

        Row {
            id: row
            anchors.centerIn: parent
            spacing: control.iconSource.length > 0 && control.text.length > 0 ? 6 : 0

            // 图标（ColorOverlay 染色，暗色模式自动适配）
            Item {
                width: control.iconSize
                height: control.iconSize
                anchors.verticalCenter: parent.verticalCenter
                visible: control.iconSource.length > 0

                Image {
                    id: icon
                    anchors.fill: parent
                    source: control.iconSource
                    sourceSize: Qt.size(control.iconSize, control.iconSize)
                    // SVG 默认黑色填充，由 ColorOverlay 染色为前景色
                    visible: false
                }
                ColorOverlay {
                    anchors.fill: icon
                    source: icon
                    color: control._foreground
                    // 禁用态降低不透明度，模拟灰化效果
                    opacity: control.enabled ? 1.0 : 0.5
                }
            }

            Label {
                text: control.text
                visible: control.text.length > 0
                font.pixelSize: control.accent === "primary" ? 14 : 13
                color: control._foreground
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
