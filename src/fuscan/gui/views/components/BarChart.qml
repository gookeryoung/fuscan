import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

// 水平条形图组件：标签 + 进度条 + 数值
//
// 输入：
//   chartData  —— [{label: string, value: int, color: string(hex)}, ...]
//   labelWidth —— 标签列固定宽度（默认 140，超出省略）
//
// 输出：
//   每行一条数据，条形宽度按 value/maxValue 比例填充
//   chartData 为空时显示「暂无数据」占位
//
// 设计要点：
//   - 条形用 Rectangle + NumberAnimation 实现宽度平滑过渡（数据更新有动效）
//   - 背景轨道用 colorBorder 浅色填充，与条形主色形成对比
//   - 标签 ElideRight 避免长规则名撑爆布局
Item {
    id: root

    // 类型化访问 Theme context property（与 IconButton.qml 约定一致）
    property ThemeController theme: Theme

    // 图表数据：[{label, value, color}, ...]
    property var chartData: []
    // 标签列固定宽度
    property int labelWidth: 140

    // 最大值（只读，供条形宽度计算）
    readonly property int maxValue: {
        var m = 0
        for (var i = 0; i < chartData.length; i++) {
            if (chartData[i].value > m) m = chartData[i].value
        }
        return m
    }

    // 行高 + 间距推导总高度
    implicitHeight: chartData.length > 0
        ? chartData.length * 28 + (chartData.length - 1) * 6 + 8
        : 120

    // ---------- 空态 ----------
    Label {
        anchors.centerIn: parent
        visible: root.chartData.length === 0
        text: "暂无数据"
        font.pixelSize: theme.fontSizeBody
        color: theme.colorTextSecondary
    }

    // ---------- 条形列表 ----------
    ColumnLayout {
        anchors.fill: parent
        spacing: 6
        visible: root.chartData.length > 0

        Repeater {
            model: root.chartData

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                // 标签
                Label {
                    Layout.preferredWidth: root.labelWidth
                    text: modelData.label
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.colorTextPrimary
                    elide: Text.ElideRight
                }

                // 条形轨道 + 填充
                Item {
                    id: trackRect
                    Layout.fillWidth: true
                    Layout.preferredHeight: 20

                    // 背景轨道
                    Rectangle {
                        anchors.fill: parent
                        color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        radius: 3
                    }

                    // 实际值条（按 value/maxValue 比例填充，声明式绑定自动响应数据/尺寸变化）
                    Rectangle {
                        id: barFill
                        height: parent.height
                        width: root.maxValue > 0
                            ? trackRect.width * (modelData.value / root.maxValue)
                            : 0
                        color: modelData.color
                        radius: 3
                        // 宽度变化平滑过渡（首次填充与数据更新均有动效）
                        Behavior on width {
                            NumberAnimation {
                                duration: 300
                                easing.type: Easing.OutCubic
                            }
                        }
                    }
                }

                // 数值
                Label {
                    Layout.preferredWidth: 44
                    text: modelData.value
                    font.pixelSize: theme.fontSizeCaption
                    font.bold: true
                    color: theme.colorTextPrimary
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
    }
}
