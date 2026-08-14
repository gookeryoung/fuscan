import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.theme 1.0

// 规则编辑表单：叶子规则字段编辑 + 即时测试匹配 + 保存/取消。
//
// 输入：
//   rulesController —— RulesController（调用 testRuleFields 即时测试未保存字段）
//   rule            —— 待编辑规则字典（来自 userRulesModel），null 视为新建占位
//
// 输出：
//   saveRequested(payload) —— payload 含 originalName/name/severity/target/mode/
//                              pattern/caseSensitive/replace/replaceWith/description
//   cancelRequested()      —— 用户取消编辑
//
// 设计要点：
//   - 仅支持叶子规则编辑；组合规则（isLeaf=false）显示只读提示
//   - 严重等级/匹配目标/匹配模式用中文展示、枚举值传回后端
//   - 「测试匹配」用 testRuleFields 测试编辑中字段（无需先保存）
Item {
    id: root
    property ThemeController theme: Theme
    property var rulesController: null
    property var rule: null
    // 组合规则只读：字段区隐藏，仅显示提示
    readonly property bool readOnly: root.rule && root.rule.isLeaf === false

    signal saveRequested(var payload)
    signal cancelRequested()

    // 测试匹配结果（testRuleFields 返回的 JSON 解析对象，null=未测试）
    property var testResult: null

    implicitHeight: formColumn.implicitHeight

    function populate() {
        if (!root.rule) {
            return
        }
        nameField.text = root.rule.name || ""
        descriptionField.text = root.rule.description || ""
        severityCombo.setValue(root.rule.severity || "info")
        targetCombo.setValue(root.rule.target || "content")
        modeCombo.setValue(root.rule.mode || "contains")
        patternField.text = root.rule.pattern || ""
        caseSensitiveBox.checked = !!root.rule.caseSensitive
        replaceBox.checked = !!root.rule.replace
        replaceWithField.text = root.rule.replaceWith || ""
        root.testResult = null
    }

    function buildPayload() {
        return {
            "originalName": root.rule ? root.rule.name : "",
            "name": nameField.text,
            "severity": severityCombo.currentValue(),
            "target": targetCombo.currentValue(),
            "mode": modeCombo.currentValue(),
            "pattern": patternField.text,
            "caseSensitive": caseSensitiveBox.checked,
            "replace": replaceBox.checked,
            "replaceWith": replaceWithField.text,
            "description": descriptionField.text
        }
    }

    onRuleChanged: populate()
    Component.onCompleted: populate()

    // ---------- 组合规则只读提示 ----------
    Label {
        anchors.fill: parent
        visible: root.readOnly
        text: "组合规则（AND/OR/NOT）暂不支持图形编辑，请外部编辑 YAML 文件。\n可点击「删除」移除该规则。"
        font.pixelSize: theme.fontSizeBody
        color: theme.colorWarning
        wrapMode: Text.WordWrap
        verticalAlignment: Text.AlignTop
    }

    // ---------- 叶子规则编辑表单 ----------
    ColumnLayout {
        id: formColumn
        anchors.fill: parent
        spacing: 10
        visible: !root.readOnly

        // 规则名
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "规则名"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            TextField {
                id: nameField
                Layout.fillWidth: true
                placeholderText: "规则唯一名称"
                font.pixelSize: theme.fontSizeBody
                selectByMouse: true
            }
        }

        // 严重等级
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "严重等级"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            ComboBox {
                id: severityCombo
                Layout.fillWidth: true
                textRole: "text"
                model: ListModel {
                    ListElement { text: "信息"; value: "info" }
                    ListElement { text: "警告"; value: "warning" }
                    ListElement { text: "严重"; value: "critical" }
                }
                function currentValue() {
                    return model.get(currentIndex).value
                }
                function setValue(v) {
                    for (var i = 0; i < model.count; i++) {
                        if (model.get(i).value === v) {
                            currentIndex = i
                            return
                        }
                    }
                    currentIndex = 0
                }
            }
        }

        // 描述
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "描述"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            TextField {
                id: descriptionField
                Layout.fillWidth: true
                placeholderText: "规则说明（可选）"
                font.pixelSize: theme.fontSizeBody
                selectByMouse: true
            }
        }

        // 匹配目标 + 匹配模式
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "匹配"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            ComboBox {
                id: targetCombo
                Layout.fillWidth: true
                textRole: "text"
                model: ListModel {
                    ListElement { text: "内容"; value: "content" }
                    ListElement { text: "文件名"; value: "filename" }
                    ListElement { text: "路径"; value: "path" }
                }
                function currentValue() {
                    return model.get(currentIndex).value
                }
                function setValue(v) {
                    for (var i = 0; i < model.count; i++) {
                        if (model.get(i).value === v) {
                            currentIndex = i
                            return
                        }
                    }
                    currentIndex = 0
                }
            }
            ComboBox {
                id: modeCombo
                Layout.fillWidth: true
                textRole: "text"
                model: ListModel {
                    ListElement { text: "包含"; value: "contains" }
                    ListElement { text: "相等"; value: "equals" }
                    ListElement { text: "开头"; value: "startswith" }
                    ListElement { text: "结尾"; value: "endswith" }
                    ListElement { text: "正则"; value: "regex" }
                }
                function currentValue() {
                    return model.get(currentIndex).value
                }
                function setValue(v) {
                    for (var i = 0; i < model.count; i++) {
                        if (model.get(i).value === v) {
                            currentIndex = i
                            return
                        }
                    }
                    currentIndex = 0
                }
            }
        }

        // 匹配模式串
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Label {
                text: "模式串"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            TextField {
                id: patternField
                Layout.fillWidth: true
                placeholderText: "匹配文本或正则表达式"
                font.pixelSize: theme.fontSizeBody
                selectByMouse: true
            }
        }

        // 开关行：区分大小写 / 启用替换
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            Label {
                text: "选项"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            CheckBox {
                id: caseSensitiveBox
                text: "区分大小写"
                font.pixelSize: theme.fontSizeBody
            }
            CheckBox {
                id: replaceBox
                text: "启用替换"
                font.pixelSize: theme.fontSizeBody
            }
        }

        // 替换为（仅启用替换时可编辑）
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: replaceBox.checked
            Label {
                text: "替换为"
                font.pixelSize: theme.fontSizeBody
                color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
                Layout.preferredWidth: 80
            }
            TextField {
                id: replaceWithField
                Layout.fillWidth: true
                placeholderText: "命中内容的替换文本"
                font.pixelSize: theme.fontSizeBody
                selectByMouse: true
            }
        }

        // ---------- 即时测试 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
        }

        Label {
            text: "即时测试"
            font.pixelSize: theme.fontSizeCaption
            font.bold: true
            color: theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 60
                clip: true
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                TextArea {
                    id: testInput
                    placeholderText: "输入测试文本（CONTENT 匹配此文本，FILENAME/PATH 匹配 test.txt）..."
                    wrapMode: TextArea.Wrap
                    font.pixelSize: theme.fontSizeBody
                    background: Rectangle {
                        color: theme.isDark ? theme.colorBgApp : theme.colorBgApp
                        border.color: theme.isDark ? theme.colorBorderDark : theme.colorBorder
                        border.width: 1
                        radius: theme.radiusMd
                    }
                }
            }
            IconButton {
                iconSource: "qrc:/icons/search.svg"
                text: "测试匹配"
                tooltip: "用当前编辑字段（未保存）对测试文本执行匹配"
                accent: "secondary"
                compact: true
                enabled: patternField.text.length > 0
                onClicked: {
                    if (!root.rulesController) {
                        return
                    }
                    var raw = root.rulesController.testRuleFields(
                        JSON.stringify(root.buildPayload()), testInput.text)
                    try {
                        root.testResult = JSON.parse(raw)
                    } catch (e) {
                        root.testResult = {"error": "结果解析失败"}
                    }
                }
            }
        }

        // 测试结果摘要
        Label {
            Layout.fillWidth: true
            visible: root.testResult !== null && !root.testResult.error
            text: root.testResult && root.testResult.matched
                ? "命中 " + root.testResult.matchCount + " 次（目标: " + root.testResult.target + "）"
                : (root.testResult !== null ? "未命中" : "")
            font.pixelSize: theme.fontSizeCaption
            color: root.testResult && root.testResult.matched
                ? theme.colorSuccess
                : (theme.isDark ? theme.colorTextSecondary : theme.colorTextSecondary)
        }

        // 测试错误提示
        Label {
            Layout.fillWidth: true
            visible: root.testResult !== null && !!root.testResult.error
            text: root.testResult ? root.testResult.error : ""
            font.pixelSize: theme.fontSizeCaption
            color: theme.colorDanger
            wrapMode: Text.WordWrap
        }

        // 命中文本列表
        Repeater {
            model: root.testResult && root.testResult.matches ? root.testResult.matches : []
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: matchTextLbl.implicitHeight + 8
                color: Qt.rgba(theme.colorSuccess.r, theme.colorSuccess.g,
                               theme.colorSuccess.b, 0.12)
                radius: theme.radiusSm
                Label {
                    id: matchTextLbl
                    anchors.fill: parent
                    anchors.margins: 4
                    text: modelData.text
                    font.pixelSize: theme.fontSizeCaption
                    color: theme.isDark ? theme.colorTextPrimary : theme.colorTextPrimary
                    wrapMode: Text.WrapAnywhere
                }
            }
        }

        // ---------- 操作按钮 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Item { Layout.fillWidth: true }
            IconButton {
                iconSource: "qrc:/icons/close.svg"
                text: "取消"
                accent: "ghost"
                compact: true
                onClicked: root.cancelRequested()
            }
            IconButton {
                iconSource: "qrc:/icons/check.svg"
                text: "保存"
                accent: "primary"
                compact: true
                enabled: nameField.text.trim().length > 0 && patternField.text.trim().length > 0
                onClicked: root.saveRequested(root.buildPayload())
            }
        }
    }
}
