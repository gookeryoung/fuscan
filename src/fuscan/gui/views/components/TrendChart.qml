import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

// 趋势图：垂直柱状图，按时间正序展示每次扫描的指标变化
//
// 输入：
//   chartData —— [{finished_at, matched_files, status, ...}, ...]（时间正序，最旧在前）
//   metric    —— 展示的数值字段名（默认 "matched_files"）
//
// 输出：
//   每个数据点一根垂直柱，高度按 value/maxValue 比例填充
//   柱色按扫描状态：completed=主色 / cancelled=警告色 / failed=危险色
//   鼠标悬停柱子显示时间 + 数值 Tooltip
//   chartData 为空时显示「暂无趋势数据」占位
//
// 设计要点：
//   - 声明式 Rectangle + Behavior on height 实现高度平滑过渡（与 BarChart 风格一致）
//   - 柱宽随数据点数量自适应（Layout.fillWidth），20 条以内可读性良好
//   - 不画坐标轴/刻度，保持极简（与项目「关键功能优先」约定一致）
Item {
    id: root

    // 类型化访问 Theme context property（与 BarChart/PieChart 约定一致）
    property ThemeController theme: Theme

    // 图表数据：[{finished_at, matched_files, status, ...}, ...]
    property var chartData: []
    // 展示的数值字段名（默认命中文件数）
    property string metric: "matched_files"
    // 数值后缀（如 " 个"），默认空串
    property string valueSuffix: ""

    // 当前悬停的柱索引（-1 表示无悬停）
    property int hoveredIndex: -1

    // 最大值（只读，供柱高计算）
    readonly property real maxValue: {
        var m = 0
        for (var i = 0; i < chartData.length; i++) {
            var v = Number(chartData[i][metric] || 0)
            if (v > m) m = v
        }
        return m
    }

    implicitHeight: chartData.length > 0 ? 130 : 100

    // ---------- 空态 ----------
    Label {
        anchors.centerIn: parent
        visible: root.chartData.length === 0
        text: "暂无趋势数据"
        font.pixelSize: theme.fontSizeBody
        color: theme.colorTextSecondary
    }

    // ---------- 趋势柱区 ----------
    ColumnLayout {
        anchors.fill: parent
        spacing: 4
        visible: root.chartData.length > 0

        // 顶部摘要：最大值 + 悬停详情
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                font.pixelSize: theme.fontSizeCaption
                color: theme.colorTextSecondary
                text: root.maxValue > 0
                    ? ("峰值 " + root.maxValue + root.valueSuffix)
                    : ""
            }
            Item { Layout.fillWidth: true }
            Label {
                font.pixelSize: theme.fontSizeCaption
                color: theme.colorTextPrimary
                font.bold: true
                visible: root.hoveredIndex >= 0 && root.hoveredIndex < root.chartData.length
                text: {
                    if (root.hoveredIndex < 0 || root.hoveredIndex >= root.chartData.length)
                        return ""
                    var d = root.chartData[root.hoveredIndex]
                    var ts = (d.finished_at || "").replace("T", " ").replace("Z", "")
                    return ts + " | " + Number(d[metric] || 0) + root.valueSuffix
                }
            }
        }

        // 柱状区
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 2

            Repeater {
                model: root.chartData

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    property real value: Number(modelData[root.metric] || 0)

                    Rectangle {
                        anchors.bottom: parent.bottom
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: root.maxValue > 0
                            ? parent.height * (parent.value / root.maxValue)
                            : 0
                        // 悬停时柱体加亮（提高不透明度），非悬停用 0.85 区分
                        color: root.statusColor(modelData.status, index === root.hoveredIndex)
                        radius: 2
                        // 高度变化平滑过渡（首次填充与数据更新均有动效）
                        Behavior on height {
                            NumberAnimation {
                                duration: 300
                                easing.type: Easing.OutCubic
                            }
                        }
                    }

                    // 悬停命中区域（整列可触发，便于细柱交互）
                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        onEntered: root.hoveredIndex = index
                        onExited: root.hoveredIndex = -1
                    }
                }
            }
        }
    }

    // 按扫描状态返回柱色；hovered 为 true 时叠加高亮（主色加亮、其余保持原色）
    function statusColor(status, hovered) {
        var base
        if (status === "completed") base = theme.colorPrimary
        else if (status === "cancelled") base = theme.colorWarning
        else base = theme.colorDanger
        // 悬停柱体提亮：通过混入文本主色模拟高光（Qt.rgba 线性混合）
        if (hovered) {
            return Qt.rgba(
                Math.min(1, base.r + 0.15),
                Math.min(1, base.g + 0.15),
                Math.min(1, base.b + 0.15),
                1.0
            )
        }
        return base
    }
}
