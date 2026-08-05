import QtQuick 2.15
import "components"
import fuscan.theme 1.0
Rectangle {
    width: 420; height: 260
    color: Theme.isDark ? Theme.colorBgCard : "#ffffff"
    Column {
        anchors.fill: parent; anchors.margins: 24; spacing: 0
        PhaseNode { width: 372; nodeState: "done"; accentColor: Theme.colorPrimary;
            title: "收集文件清单"; detail: "1000 / 1000"; showTopLine: false }
        PhaseNode { width: 372; nodeState: "running"; accentColor: Theme.colorWarning;
            title: "筛选文件"; detail: "剔除 600（空 500 · 超限 100 · 不可读 0 · 链接 0）";
            progressIndeterminate: true }
        PhaseNode { width: 372; nodeState: "pending"; accentColor: Theme.colorWarning;
            title: "解析文件内容"; detail: ""; showBottomLine: false }
    }
}
