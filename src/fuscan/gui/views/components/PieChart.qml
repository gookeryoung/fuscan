import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

// 饼图组件：Canvas 自绘环形饼图 + 图例
//
// 输入：
//   chartData  —— [{label: string, value: int, color: string(hex)}, ...]
//   centerTitle—— 中心环内标题（如「命中文件」），空串仅显示数值
//
// 输出：
//   左侧环形饼图（中心叠加总数），右侧图例列表（色块 + 标签 + 数值）
//   chartData 为空时显示「暂无数据」占位
//
// 设计要点：
//   - Canvas onPaint 重绘由 chartData/width/height/isDark 变化触发 requestPaint
//   - 内圆用 colorBgCard 填充与卡片背景融合，形成环形视觉
//   - 起始角 -π/2（12 点方向），顺时针填充，符合阅读直觉
Item {
    id: root

    // 类型化访问 Theme context property（与 IconButton.qml 约定一致）
    property ThemeController theme: Theme

    // 图表数据：[{label, value, color}, ...]
    property var chartData: []
    // 中心标题（空串仅显示数值）
    property string centerTitle: ""
    // 总和（只读，供中心文字与外部绑定）
    readonly property int totalValue: {
        var sum = 0
        for (var i = 0; i < chartData.length; i++) {
            sum += chartData[i].value
        }
        return sum
    }

    // chartData 变化时触发饼图重绘（属性在 root 上，处理器须与属性同级）
    onChartDataChanged: pieCanvas.requestPaint()

    implicitHeight: 220

    // ---------- 空态 ----------
    Label {
        anchors.centerIn: parent
        visible: root.chartData.length === 0
        text: "暂无数据"
        font.pixelSize: theme.fontSizeBody
        color: theme.colorTextSecondary
    }

    // ---------- 图表主体 ----------
    RowLayout {
        anchors.fill: parent
        spacing: 16
        visible: root.chartData.length > 0

        // 饼图 + 中心文字叠加
        Item {
            Layout.preferredWidth: 200
            Layout.fillHeight: true

            Canvas {
                id: pieCanvas
                anchors.fill: parent
                // 尺寸变化时重绘（chartData 变化由 root.onChartDataChanged 触发）
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                Component.onCompleted: requestPaint()
                // 主题切换时重绘（内圆填充色依赖 colorBgCard）
                Connections {
                    target: root.theme
                    function onThemeChanged() { pieCanvas.requestPaint() }
                }
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    var w = width
                    var h = height
                    var cx = w / 2
                    var cy = h / 2
                    var radius = Math.min(w, h) / 2 - 8
                    var innerRadius = radius * 0.58

                    var total = root.totalValue
                    if (total === 0) return

                    var startAngle = -Math.PI / 2
                    for (var i = 0; i < root.chartData.length; i++) {
                        var item = root.chartData[i]
                        var sliceAngle = (item.value / total) * 2 * Math.PI
                        var endAngle = startAngle + sliceAngle

                        ctx.beginPath()
                        ctx.moveTo(cx, cy)
                        ctx.arc(cx, cy, radius, startAngle, endAngle)
                        ctx.closePath()
                        ctx.fillStyle = item.color
                        ctx.fill()

                        startAngle = endAngle
                    }

                    // 内圆挖空形成环形（颜色与卡片背景一致）
                    ctx.beginPath()
                    ctx.arc(cx, cy, innerRadius, 0, 2 * Math.PI)
                    ctx.fillStyle = theme.colorBgCard
                    ctx.fill()
                }
            }

            // 中心文字叠加（环形内圆区域）
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 2
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: root.centerTitle
                    visible: root.centerTitle.length > 0
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.colorTextSecondary
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: root.totalValue
                    font.pixelSize: 22
                    font.bold: true
                    color: theme.colorTextPrimary
                }
            }
        }

        // ---------- 图例 ----------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            Repeater {
                model: root.chartData
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Rectangle {
                        width: 12
                        height: 12
                        radius: 2
                        color: modelData.color
                    }
                    Label {
                        Layout.fillWidth: true
                        text: modelData.label
                        font.pixelSize: theme.fontSizeCaption
                        color: theme.colorTextPrimary
                        elide: Text.ElideRight
                    }
                    Label {
                        text: modelData.value
                        font.pixelSize: theme.fontSizeCaption
                        font.bold: true
                        color: theme.colorTextPrimary
                    }
                }
            }
        }
    }
}
