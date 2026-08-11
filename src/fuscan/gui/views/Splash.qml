import QtQuick 2.15
import QtQuick.Window 2.15
import fuscan.controllers 1.0

// 启动画面：无边框、居中、圆角背景，显示 logo + 应用名 + 阶段文本 + 进度条
// 由 app.py 在 QGuiApplication 构造后立即加载，主窗口 QML 加载完成后关闭。
// 仅依赖 QtQuick + QtQuick.Window，不引入 QtQuick.Controls/Layouts，
// 避免 Splash 加载阶段触发重型 Controls plugin 初始化拖慢首屏反馈。
// 色值内联（不依赖 ThemeController），与浅色模式主窗口视觉一致。
Window {
    id: splashRoot
    visible: true
    width: 480
    height: 280
    flags: Qt.SplashScreen | Qt.FramelessWindowHint
    color: "transparent"  // 让背景 Rectangle 圆角生效

    // 居中显示到主屏
    x: (Screen.width - width) / 2
    y: (Screen.height - height) / 2

    // 类型化访问 context property，消除 setContextProperty 导致的 TypeError
    property SplashControllerType splashController: SplashController

    // 圆角背景
    Rectangle {
        id: bg
        anchors.fill: parent
        color: "#F5F6F8"
        radius: 12
        border.color: "#D1DDE2"
        border.width: 1
    }

    // Logo：居中略偏上
    Image {
        id: logo
        source: "qrc:/icons/favicon.svg"
        sourceSize: Qt.size(72, 72)
        width: 72
        height: 72
        fillMode: Image.PreserveAspectFit
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 56
    }

    // 应用名
    Text {
        id: appName
        text: "fuscan"
        font.pixelSize: 24
        font.bold: true
        color: "#24292E"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: logo.bottom
        anchors.topMargin: 12
    }

    // 阶段文本（绑定 SplashController.stage）
    Text {
        id: stageText
        text: splashController.stage
        font.pixelSize: 13
        color: "#586069"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: appName.bottom
        anchors.topMargin: 8
        // 文本变化时淡入：对 opacity 应用 NumberAnimation，
        // onTextChanged 触发 0→1 过渡（Behavior on text 对字符串属性无法实现淡入）
        Behavior on opacity {
            NumberAnimation { duration: 150 }
        }
        onTextChanged: {
            opacity = 0
            opacity = 1
        }
    }

    // indeterminate 进度条：圆角 + 主色 + 左右往返动画
    Rectangle {
        id: progressTrack
        width: parent.width - 64
        height: 6
        color: "#E1E4E8"
        radius: 3
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: stageText.bottom
        anchors.topMargin: 16

        Rectangle {
            width: progressTrack.width * 0.3
            height: progressTrack.height
            color: "#0366D6"
            radius: 3
            // 进度条左右往返动画
            XAnimator on x {
                from: -progressTrack.width * 0.3
                to: progressTrack.width
                duration: 1200
                loops: Animation.Infinite
                running: true
            }
        }
    }

    // 底部提示
    Text {
        text: "正在准备扫描环境"
        font.pixelSize: 11
        color: "#8B949E"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 24
    }
}
