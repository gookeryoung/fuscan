import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0
import fuscan.controllers 1.0
import "../components"

// 设置页：TabBar 分组切换，避免单页滚动过长
Item {
    id: settingsPage
    property ThemeController theme: Theme
    property ConfigControllerType configController: ConfigController

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: 0

        // ---------- 标题栏 ----------
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 0
            Layout.rightMargin: 0
            spacing: 12
            Label {
                text: "设置"
                font.pixelSize: theme.fontSizePageTitle
                font.bold: true
                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconSource: "qrc:/icons/rescan.svg"
                text: "重置"
                tooltip: "重置扫描相关配置为默认值"
                accent: "secondary"
                onClicked: configController.resetToDefaults()
            }
        }

        // ---------- TabBar ----------
        TabBar {
            id: settingsTabBar
            Layout.fillWidth: true
            Layout.topMargin: 8
            spacing: 0
            currentIndex: 0
            background: Rectangle {
                color: "transparent"
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 1
                    color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                }
            }
            Repeater {
                model: ["通用", "扫描", "忽略目录"]
                TabButton {
                    id: tabBtn
                    text: modelData
                    height: theme.btnHeightSecondary
                    contentItem: Label {
                        text: tabBtn.text
                        color: tabBtn.checked
                              ? theme.colorPrimary
                              : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
                        font.pixelSize: theme.fontSizeBody
                        font.bold: tabBtn.checked
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        id: tabBg
                        color: "transparent"
                        Rectangle {
                            anchors.bottom: tabBg.bottom
                            anchors.horizontalCenter: tabBg.horizontalCenter
                            width: tabBg.width - 16
                            height: 2
                            color: tabBtn.checked ? theme.colorPrimary : "transparent"
                        }
                    }
                }
            }
        }

        // ---------- 内容区（StackLayout 切换） ----------
        StackLayout {
            id: settingsStack
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 16
            currentIndex: settingsTabBar.currentIndex

            // ===== Tab 1: 通用（字体设置） =====
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsStack.width
                    spacing: 16

                    Label {
                        text: "字体设置"
                        font.pixelSize: theme.fontSizeHeading
                        font.bold: true
                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    }

                    // 字体族
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "字体"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: fontFamilyCombo
                            Layout.fillWidth: true
                            // 用 Qt.fontFamilies() 获取系统可用字体列表
                            model: Qt.fontFamilies()
                            // 显示当前配置字体（空串显示"平台默认"）
                            displayText: configController.fontFamily
                                ? configController.fontFamily
                                : "平台默认"
                            onActivated: {
                                configController.setFontFamily(fontFamilyCombo.currentText)
                            }
                            // 预选当前字体
                            Component.onCompleted: {
                                if (configController.fontFamily) {
                                    var idx = fontFamilyCombo.find(configController.fontFamily)
                                    if (idx >= 0) fontFamilyCombo.currentIndex = idx
                                }
                            }
                        }
                        // 清除按钮：恢复平台默认
                        IconButton {
                            text: "默认"
                            tooltip: "恢复平台默认字体"
                            accent: "ghost"
                            onClicked: configController.setFontFamily("")
                        }
                    }

                    // 字号
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "字号"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: fontSizeCombo
                            model: [10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24]
                            displayText: configController.fontSize + " px"
                            onActivated: {
                                configController.setFontSize(fontSizeCombo.currentValue)
                            }
                            Component.onCompleted: {
                                var idx = fontSizeCombo.find(configController.fontSize)
                                if (idx >= 0) fontSizeCombo.currentIndex = idx
                            }
                        }
                        Label {
                            text: "（基准字号，其他字号基于此计算）"
                            font.pixelSize: theme.fontSizeCaption
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                    }

                    // 最小字号
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "最小字号"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        ComboBox {
                            id: minFontSizeCombo
                            model: [8, 9, 10, 11, 12, 13, 14, 15, 16]
                            displayText: configController.minFontSize + " px"
                            onActivated: {
                                configController.setMinFontSize(minFontSizeCombo.currentValue)
                            }
                            Component.onCompleted: {
                                var idx = minFontSizeCombo.find(configController.minFontSize)
                                if (idx >= 0) minFontSizeCombo.currentIndex = idx
                            }
                        }
                        Label {
                            text: "（小字号下限，避免高 DPI 屏幕显示过小）"
                            font.pixelSize: theme.fontSizeCaption
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        }
                    }

                    // 加粗
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Label {
                            text: "加粗"
                            font.pixelSize: theme.fontSizeBody
                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            Layout.preferredWidth: 80
                        }
                        CheckBox {
                            checked: configController.fontBold
                            onCheckedChanged: configController.setFontBold(checked)
                        }
                    }

                    // 预览
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 60
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusMd
                        Label {
                            anchors.centerIn: parent
                            text: "字体预览 ABC 中文 123"
                            font.family: configController.fontFamily || theme.fontFamily
                            font.pixelSize: configController.fontSize
                            font.bold: configController.fontBold
                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ===== Tab 2: 扫描 =====
            ScrollView {
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: settingsStack.width
                    spacing: 16

                    GroupBox {
                        Layout.fillWidth: true
                        title: "扫描参数"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "扫描压缩包"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Switch {
                                    checked: configController.scanArchives
                                    onCheckedChanged: configController.setScanArchives(checked)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大工作线程"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Label {
                                    text: "当前机器最大线程=" + configController.cpuCount
                                    font.pixelSize: theme.fontSizeCaption
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                                // iter-125：上限改为 cpuCount（与提示一致），editable 支持手输入
                                SpinBox {
                                    id: maxWorkersSpin
                                    from: 1
                                    to: Math.max(configController.cpuCount, 1)
                                    value: Math.min(configController.maxWorkers, configController.cpuCount)
                                    editable: true
                                    onValueChanged: configController.setMaxWorkers(value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大文件大小（MB）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                // iter-125：动态步进 <50 步 10，50-100 步 25，>100 步 100
                                SpinBox {
                                    id: maxFileSizeSpin
                                    from: 1
                                    to: 1024
                                    value: configController.maxFileSizeMB
                                    editable: true
                                    stepSize: {
                                        var v = maxFileSizeSpin.value
                                        if (v < 50) return 10
                                        if (v < 100) return 25
                                        return 100
                                    }
                                    onValueChanged: configController.setMaxFileSizeMB(value)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "最大扫描深度（0=无限）"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                // iter-125：editable 支持手输入
                                // iter-127：上限统一为 64（与 WorkspaceCard 任务级设置一致）
                                SpinBox {
                                    from: 0
                                    to: 64
                                    value: configController.maxDepth
                                    editable: true
                                    onValueChanged: configController.setMaxDepth(value)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "缓存"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用扫描结果缓存"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Switch {
                                    checked: configController.cacheEnabled
                                    onCheckedChanged: configController.setCacheEnabled(checked)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "性能"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    text: "启用性能详细日志"
                                    Layout.fillWidth: true
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                }
                                Switch {
                                    checked: configController.perfLogEnabled
                                    onCheckedChanged: configController.setPerfLogEnabled(checked)
                                }
                            }
                        }
                    }

                    GroupBox {
                        Layout.fillWidth: true
                        title: "文件类型"
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                IconButton {
                                    iconSource: "qrc:/icons/check_box.svg"
                                    text: "全选"
                                    tooltip: "全部启用"
                                    accent: "secondary"
                                    onClicked: configController.selectAllExtractors()
                                }
                                IconButton {
                                    iconSource: "qrc:/icons/check_box_blank.svg"
                                    text: "全不选"
                                    tooltip: "全部禁用"
                                    accent: "secondary"
                                    onClicked: configController.unselectAllExtractors()
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: configController.extractorCountText
                                    font.pixelSize: 11
                                    color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                }
                            }
                            ListView {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 320
                                clip: true
                                interactive: true
                                // iter-106 P1：预渲染屏幕外 delegate，避免滚动时重建
                                cacheBuffer: 500
                                model: configController.extractorModel
                                // 按 category 角色分组，配合 section.delegate 渲染类别头部
                                section.property: "category"
                                section.criteria: ViewSection.FullString
                                section.delegate: Rectangle {
                                    width: ListView.view.width
                                    height: 32
                                    color: theme.isDark ? theme.colorBgHoverDark : theme.colorBgHover

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 12
                                        anchors.rightMargin: 8
                                        spacing: 8

                                        // 类别父节点勾选框（iter-104 统一勾选）：
                                        // 三态显示：0=全不选, 1=全选, 2=部分选中
                                        // 点击切换为相反状态（全选↔全不选）
                                        CheckBox {
                                            id: categoryCheckbox
                                            // tristate 让 Qt 允许三态显示（包含 PartiallyChecked）
                                            tristate: true
                                            // 根据 categoryEnabledState 计算三态
                                            checkState: {
                                                var state = configController.categoryEnabledState(section)
                                                if (state === 1) return Qt.Checked
                                                if (state === 2) return Qt.PartiallyChecked
                                                return Qt.Unchecked
                                            }
                                            onClicked: {
                                                // 点击时切换为相反状态：当前全选→全不选，否则→全选
                                                var state = configController.categoryEnabledState(section)
                                                configController.setCategoryEnabled(section, state !== 1)
                                            }
                                            ToolTip.visible: hovered
                                            ToolTip.text: "统一勾选/取消该类别下所有文件类型"
                                            ToolTip.delay: 400
                                        }

                                        // 类别标题
                                        Label {
                                            text: section
                                            font.bold: true
                                            font.pixelSize: theme.fontSizeBody
                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        }

                                        Item { Layout.fillWidth: true }
                                    }
                                }
                                delegate: ItemDelegate {
                                    id: extractorDelegate
                                    width: ListView.view.width
                                    height: 32
                                    // 速度档次 1-5，T1 最快（5 格满），T5 最慢（1 格）
                                    property int tier: parseInt(model.speedTierText.charAt(1))
                                    property color speedColor: model.speedTierColor
                                    // 缓存 formatTags 列表，避免 Repeater 内 model 关键字遮蔽
                                    property var formatTagsList: model.formatTags
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 32
                                        CheckBox {
                                            checked: model.enabled
                                            onCheckedChanged: configController.setExtractorEnabled(model.className, checked)
                                        }
                                        Label {
                                            text: model.displayName
                                            font.pixelSize: 12
                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        }
                                        // 蓝色格式 tag 列表：扩展名较多的提取器显示多个代表性 tag
                                        // （如源代码显示 HTML/C/CPP/PY），其余显示单个 formatLabel
                                        // 用 Row + spacing 控制多 tag 间距，Repeater model 用 id 引用避免遮蔽
                                        Row {
                                            Layout.leftMargin: 6
                                            spacing: 4
                                            Repeater {
                                                model: extractorDelegate.formatTagsList
                                                Rectangle {
                                                    radius: theme.radiusSm
                                                    color: theme.colorPrimary
                                                    width: formatTagLabel.implicitWidth + 12
                                                    height: formatTagLabel.implicitHeight + 4
                                                    Label {
                                                        id: formatTagLabel
                                                        anchors.centerIn: parent
                                                        text: modelData
                                                        font.pixelSize: 10
                                                        font.bold: true
                                                        color: theme.colorTextOnPrimary
                                                    }
                                                }
                                            }
                                        }
                                        Item { Layout.fillWidth: true }
                                        // 「解析速度」文字标签 + 五格指示器（含 ToolTip）
                                        Label {
                                            text: "解析速度"
                                            font.pixelSize: 10
                                            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                            Layout.rightMargin: 4
                                        }
                                        // Item 包裹 Row + MouseArea，避免 Row 内子项使用 anchors
                                        Item {
                                            id: speedIndicator
                                            Layout.rightMargin: 8
                                            width: speedRow.width
                                            height: speedRow.height
                                            // ToolTip：hover 时显示完整速度档次（如「T2 快速」）
                                            ToolTip.visible: speedIndicatorMouseArea.containsMouse
                                            ToolTip.delay: 300
                                            ToolTip.text: "解析速度：" + model.speedTierText
                                            Row {
                                                id: speedRow
                                                spacing: 2
                                                Repeater {
                                                    model: 5
                                                    Rectangle {
                                                        width: 6
                                                        height: 12
                                                        radius: 1
                                                        color: index < (6 - extractorDelegate.tier)
                                                            ? extractorDelegate.speedColor
                                                            : (theme.isDark ? theme.colorBorderDark : theme.colorBorder)
                                                    }
                                                }
                                            }
                                            MouseArea {
                                                id: speedIndicatorMouseArea
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.WhatsThisCursor
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            // ===== Tab 3: 忽略目录（分类管理） =====
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: settingsStack.width
                    spacing: 8

                    Label {
                        text: "按目录名匹配（大小写不敏感，任意层级）。勾选表示扫描时跳过该目录。"
                        font.pixelSize: 11
                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }

                    // ---------- 顶部全选/全不选按钮 ----------
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        IconButton {
                            iconSource: "qrc:/icons/check_box.svg"
                            text: "全选"
                            tooltip: "勾选所有预设分类下的忽略目录（自定义目录不动）"
                            accent: "secondary"
                            onClicked: configController.selectAllIgnoreDirs()
                        }
                        IconButton {
                            iconSource: "qrc:/icons/check_box_blank.svg"
                            text: "全不选"
                            tooltip: "取消所有预设分类下的忽略目录（自定义目录不动）"
                            accent: "secondary"
                            onClicked: configController.unselectAllIgnoreDirs()
                        }
                        Item { Layout.fillWidth: true }
                    }

                    // ---------- 预设分类列表 ----------
                    Repeater {
                        model: configController.ignoreDirCategories

                        // 分类卡片
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.leftMargin: 0
                            Layout.rightMargin: 0
                            // 高度由内容撑开：header 高度 + 子目录列表高度
                            height: categoryColumn.implicitHeight + 16
                            color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                            border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                            border.width: 1
                            radius: theme.radiusSm

                            property var categoryData: modelData
                            property string categoryName: modelData.category
                            property var categoryDirs: modelData.dirs
                            property bool categoryAllEnabled: modelData.allEnabled

                            ColumnLayout {
                                id: categoryColumn
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 4

                                // 分类标题行：全选 CheckBox + 分类名
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8

                                    CheckBox {
                                        tristate: true
                                        checkState: categoryData.allEnabled ? Qt.Checked : Qt.Unchecked
                                        // tristate 仅用于显示，点击时切全选/全不选
                                        onClicked: {
                                            var willEnable = !(categoryData.allEnabled)
                                            configController.setIgnoreDirCategoryEnabled(categoryName, willEnable)
                                        }
                                        ToolTip.visible: hovered
                                        ToolTip.text: "统一勾选/取消该分类下所有目录"
                                        ToolTip.delay: 400
                                    }

                                    Label {
                                        text: categoryName
                                        font.bold: true
                                        font.pixelSize: theme.fontSizeBody
                                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    }

                                    Item { Layout.fillWidth: true }

                                    // 目录计数
                                    Label {
                                        text: categoryDirs.length + " 项"
                                        font.pixelSize: 11
                                        color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                    }
                                }

                                // 分类下目录列表
                                Repeater {
                                    model: categoryDirs

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 28
                                        spacing: 8

                                        CheckBox {
                                            checked: modelData.enabled
                                            onClicked: configController.toggleIgnoreDir(modelData.name, checked)
                                        }

                                        Label {
                                            text: modelData.name
                                            font.pixelSize: 12
                                            font.family: "Consolas, Monaco, monospace"
                                            color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        }

                                        Item { Layout.fillWidth: true }
                                    }
                                }
                            }
                        }
                    }

                    // ---------- 自定义目录区 ----------
                    Rectangle {
                        Layout.fillWidth: true
                        height: customColumn.implicitHeight + 16
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusSm

                        ColumnLayout {
                            id: customColumn
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 4

                            Label {
                                text: "自定义目录"
                                font.bold: true
                                font.pixelSize: theme.fontSizeBody
                                color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                            }

                            Label {
                                text: "添加不在预设分类中的目录名，扫描时同样跳过。"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                            }

                            // 添加输入行
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8

                                TextField {
                                    id: customDirInput
                                    Layout.fillWidth: true
                                    font.pixelSize: 12
                                    font.family: "Consolas, Monaco, monospace"
                                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                    placeholderText: "输入目录名后按 Enter 或点击添加"
                                    onAccepted: {
                                        if (text.trim().length > 0) {
                                            configController.addCustomIgnoreDir(text)
                                            text = ""
                                        }
                                    }
                                    background: Rectangle {
                                        color: theme.isDark ? theme.colorBgCard : theme.colorBgCard
                                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                                        border.width: 1
                                        radius: theme.radiusSm
                                    }
                                }

                                Button {
                                    text: "添加"
                                    implicitHeight: 32
                                    font.pixelSize: theme.fontSizeSmall
                                    enabled: customDirInput.text.trim().length > 0
                                    onClicked: {
                                        configController.addCustomIgnoreDir(customDirInput.text)
                                        customDirInput.text = ""
                                    }
                                }
                            }

                            // 自定义目录列表
                            Repeater {
                                model: configController.customIgnoreDirs

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.leftMargin: 8
                                    spacing: 8

                                    Label {
                                        text: modelData
                                        font.pixelSize: 12
                                        font.family: "Consolas, Monaco, monospace"
                                        color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                                        Layout.fillWidth: true
                                    }

                                    Button {
                                        text: "✕"
                                        flat: true
                                        implicitHeight: 24
                                        implicitWidth: 24
                                        font.pixelSize: 12
                                        palette.buttonText: theme.colorDanger
                                        onClicked: configController.removeCustomIgnoreDir(modelData)
                                    }
                                }
                            }

                            // 空状态提示
                            Label {
                                visible: configController.customIgnoreDirs.length === 0
                                text: "（暂无自定义目录）"
                                font.pixelSize: 11
                                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                                Layout.leftMargin: 8
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }
        }
    }
}
