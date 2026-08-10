import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtGraphicalEffects 1.15
import fuscan.theme 1.0

// GitHub Actions 风格的流程节点：左侧状态指示器 + 竖直连接线，右侧标题与详情。
// 三种状态（nodeState）：
//   - "pending"：未开始，空心灰圈
//   - "running"：进行中，旋转小圆点转圈（强调色）
//   - "done"：已完成，实心圆 + 对勾（成功色，语义与 running 强调色区分）
// 节点通过顶部/底部连接线串成时间线；首节点隐藏上连线，尾节点隐藏下连线。
// 可选展开：expandable=true 时标题右侧显示展开箭头，点击切换 expanded，
// 展开内容通过 default property 子项承载（放于详情下方，随节点高度撑开）。
Item {
    id: node
    property ThemeController theme: Theme

    // 节点状态：pending / running / done
    property string nodeState: "pending"
    // 进行中时的强调色（调用方按阶段传入，默认主色）——running 转圈/进度条/标题用色
    property color accentColor: theme.colorPrimary
    // 完成态用色：对勾实心圆与已完成连接线用此色，默认成功绿。
    // 与 accentColor 分离，避免筛选节点（accentColor=warning 橙）完成后显示橙色对勾——
    // 完成语义应统一为成功绿，运行中的阶段色仅用于转圈动画阶段。
    property color doneColor: theme.colorSuccess
    // 标题与右侧详情文本
    property string title: ""
    property string detail: ""
    // 时间线连接线控制：首节点无上连线，尾节点无下连线
    property bool showTopLine: true
    property bool showBottomLine: true
    // 可选的内嵌细进度条（0-100），value<0 时隐藏进度条
    property real progressValue: -1
    property bool progressIndeterminate: false
    // 可展开：为 true 时标题行右侧显示展开箭头，点击切换 expanded。
    // 展开内容由调用方通过 expandContent 传入一个 Component（延迟实例化），
    // 仅在 expanded && expandable 时加载，用于展示如"最近解析文件明细"。
    property bool expandable: false
    property bool expanded: false
    // 承载展开内容的 Component（GitHub Actions 展开明细区）。
    // 用 Component + Loader 而非 default property，避免默认子项与 PhaseNode
    // 内部的 leftCol/contentCol 冲突（default property 会劫持所有默认子项）。
    property Component expandContent: null

    readonly property int indicatorSize: 20
    // 行内容固定最小高度，避免连接线高度依赖内容布局形成绑定循环
    implicitHeight: Math.max(34, contentCol.implicitHeight + 10)

    // 指示器颜色：running 用强调色，done 用成功色，pending 用边框灰
    readonly property color _stateColor: nodeState === "pending"
        ? (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
        : (nodeState === "done" ? doneColor : accentColor)
    readonly property color _lineColor: nodeState === "done"
        ? doneColor
        : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)

    // ---- 左列：连接线 + 状态指示器（固定宽度对齐时间线） ----
    Item {
        id: leftCol
        width: node.indicatorSize
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom

        // 指示器垂直居中于整个节点
        readonly property real centerY: height / 2

        // 上连接线：节点顶部 → 指示器中心
        Rectangle {
            visible: node.showTopLine
            width: 2
            x: (node.indicatorSize - width) / 2
            y: 0
            height: leftCol.centerY - node.indicatorSize / 2
            color: node._lineColor
        }
        // 下连接线：指示器中心 → 节点底部
        Rectangle {
            visible: node.showBottomLine
            width: 2
            x: (node.indicatorSize - width) / 2
            y: leftCol.centerY + node.indicatorSize / 2
            height: leftCol.height - (leftCol.centerY + node.indicatorSize / 2)
            color: node._lineColor
        }

        // 状态指示器容器
        Item {
            width: node.indicatorSize
            height: node.indicatorSize
            x: 0
            y: leftCol.centerY - node.indicatorSize / 2

            // pending：空心圈
            Rectangle {
                anchors.fill: parent
                visible: node.nodeState === "pending"
                radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: node._stateColor
            }

            // done：实心圆 + 对勾（SVG + ColorOverlay 染白，避免 Canvas 重绘问题）
            // 实心圆用 doneColor（成功绿），与 running 转圈的 accentColor 区分——
            // 使筛选节点（accentColor=warning 橙）完成后显示绿色对勾而非橙色。
            Rectangle {
                anchors.fill: parent
                visible: node.nodeState === "done"
                radius: width / 2
                color: node.doneColor
                Image {
                    id: checkIcon
                    anchors.centerIn: parent
                    width: parent.width * 0.62
                    height: parent.height * 0.62
                    source: "qrc:/icons/check.svg"
                    sourceSize: Qt.size(width, height)
                    visible: false
                }
                ColorOverlay {
                    anchors.fill: checkIcon
                    source: checkIcon
                    color: node.theme.colorTextOnPrimary
                }
            }

            // running：旋转转圈（彩色 PNG sprite sheet + AnimatedSprite）
            // GitHub 风格 comet tail spinner：270 度弧线 + alpha 渐变（尾部透明→头部实色），
            // 视觉上比小圆点跳跃更连续流畅。24 帧 × 15 度 = 一周，frameDuration=50ms → 1200ms 一周。
            // 用帧动画替代 RotationAnimator，避免 Win7 OpenGL 软件渲染掉帧——
            // AnimatedSprite 基于帧切换（仅纹理 rect 更新，无变换矩阵），软件渲染也顺滑。
            // 去掉 ColorOverlay（ShaderEffect 在软件渲染下不随 AnimatedSprite 帧切换实时更新，
            // 导致动画卡顿），改为直接用彩色 sprite sheet：根据 accentColor 选择对应资源。
            Item {
                id: spinner
                anchors.fill: parent
                visible: node.nodeState === "running"

                // 根据 accentColor 选择对应彩色 sprite sheet
                // accentColor 取值：colorPrimary 蓝 / colorWarning 橙 / colorTextSecondary 灰
                readonly property string _spinnerSource: {
                    if (Qt.colorEqual(node.accentColor, node.theme.colorPrimary))
                        return "qrc:/animations/spinner_primary.png"
                    if (Qt.colorEqual(node.accentColor, node.theme.colorWarning))
                        return "qrc:/animations/spinner_warning.png"
                    if (Qt.colorEqual(node.accentColor, node.theme.colorTextSecondary))
                        return "qrc:/animations/spinner_secondary.png"
                    // 默认 primary（accentColor 总是上述三者之一，兜底防御）
                    return "qrc:/animations/spinner_primary.png"
                }

                AnimatedSprite {
                    anchors.fill: parent
                    source: spinner._spinnerSource
                    frameWidth: 20
                    frameHeight: 20
                    frameCount: 24
                    frameDuration: 50  // 1200ms / 24 帧
                    loops: Animation.Infinite
                    running: node.nodeState === "running"
                }
            }
        }
    }

    // ---- 右列：标题 + 详情 + 可选进度条 + 可选展开明细 ----
    ColumnLayout {
        id: contentCol
        anchors.left: leftCol.right
        anchors.leftMargin: 10
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3

        RowLayout {
            id: titleRow
            Layout.fillWidth: true
            spacing: 6
            Label {
                text: node.title
                font.pixelSize: 11
                font.bold: node.nodeState === "running"
                color: node.nodeState === "pending"
                    ? (node.theme.isDark ? node.theme.colorTextSecondary : node.theme.colorTextSecondary)
                    : (node.nodeState === "done" ? node.doneColor : node.accentColor)
            }
            Item { Layout.fillWidth: true }
            Label {
                text: node.detail
                font.pixelSize: 11
                color: node.theme.isDark ? node.theme.colorTextSecondary : node.theme.colorTextSecondary
            }
            // 展开/收起 明细：pill 样式按钮（文字提示 + chevron），扩大点击热区、
            // 提供 hover 背景反馈，明确可点击态。仅可展开节点显示（GitHub Actions
            // 折叠交互）。收起时 chevron 指右并显示「展开明细」，展开时 chevron
            // 指下并显示「收起明细」，交互语义清晰。
            Rectangle {
                id: expandToggle
                visible: node.expandable
                Layout.preferredHeight: 22
                Layout.preferredWidth: toggleRow.implicitWidth + 14
                radius: node.theme.radiusSm
                color: toggleHover.hovered
                    ? (node.theme.isDark ? node.theme.colorBgHoverDark : node.theme.colorBgHover)
                    : "transparent"
                border.width: 1
                border.color: toggleHover.hovered
                    ? (node.theme.isDark ? node.theme.colorBorderDark : node.theme.colorBorder)
                    : "transparent"
                Behavior on color { ColorAnimation { duration: 120 } }

                RowLayout {
                    id: toggleRow
                    anchors.centerIn: parent
                    spacing: 4
                    Label {
                        text: node.expanded ? "收起明细" : "展开明细"
                        font.pixelSize: node.theme.fontSizeMin
                        color: node.theme.isDark ? node.theme.colorTextSecondary : node.theme.colorTextSecondary
                    }
                    Item {
                        Layout.preferredWidth: 12
                        Layout.preferredHeight: 12
                        Image {
                            id: chevronIcon
                            anchors.fill: parent
                            source: "qrc:/icons/down_arrow.svg"
                            sourceSize: Qt.size(12, 12)
                            // 收起时旋转 -90° 指向右侧，展开时 0° 指向下方
                            rotation: node.expanded ? 0 : -90
                            visible: false
                            Behavior on rotation {
                                NumberAnimation { duration: 120 }
                            }
                        }
                        ColorOverlay {
                            anchors.fill: chevronIcon
                            source: chevronIcon
                            color: node.theme.isDark ? node.theme.colorTextSecondary : node.theme.colorTextSecondary
                        }
                    }
                }

                HoverHandler {
                    id: toggleHover
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: node.expanded = !node.expanded
                }
            }
        }

        // 内嵌细进度条：仅进行中且提供了 progressValue 时显示
        ProgressBar {
            id: inlineBar
            Layout.fillWidth: true
            visible: node.nodeState === "running" && (node.progressValue >= 0 || node.progressIndeterminate)
            indeterminate: node.progressIndeterminate
            from: 0.0
            to: 100.0
            value: node.progressValue < 0 ? 0 : node.progressValue
            background: Rectangle {
                implicitHeight: 4
                color: node.theme.isDark ? node.theme.colorBorderDark : node.theme.colorBorder
                radius: 2
            }
            contentItem: Item {
                implicitHeight: 4
                Rectangle {
                    width: inlineBar.visualPosition * parent.width
                    height: parent.height
                    radius: 2
                    color: node.accentColor
                }
            }
        }

        // 展开明细区：通过 Loader 延迟加载调用方传入的 expandContent Component，
        // 仅在 expandable && expanded 时激活并占据布局空间。
        Loader {
            id: expandArea
            Layout.fillWidth: true
            Layout.topMargin: active ? 6 : 0
            active: node.expandable && node.expanded
            visible: active
            sourceComponent: node.expandContent
        }
    }
}
