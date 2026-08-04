import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import fuscan.controllers 1.0

// 启动画面：无边框、居中、圆角背景，显示 logo + 应用名 + 阶段文本 + 进度条
// 由 app.py 在 QGuiApplication 构造后立即加载，主窗口 QML 加载完成后关闭。
// 色值内联（不依赖 ThemeController），与浅色模式主窗口视觉一致，避免引入
// 额外 context property 注册的复杂度。
ApplicationWindow {
    id: splashRoot
    visible: true
    width: 480
    height: 280
    flags: Qt.SplashScreen | Qt.FramelessWindowHint
    color: "transparent"  // 让背景 Rectangle 圆角生效

    // 居中显示到主屏
    x: (Screen.width - width) / 2
    y: (Screen.height - height) / 2

    // 类型化访问 context property
    property SplashControllerType splashController: SplashController

    background: Rectangle {
        color: "#F5F6F8"
        radius: 12
        border.color: "#D1DDE2"
        border.width: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 16

        // 顶部弹簧
        Item { Layout.fillHeight: true }

        // Logo
        Image {
            source: "qrc:/icons/favicon.svg"
            sourceSize: Qt.size(72, 72)
            Layout.alignment: Qt.AlignHCenter
            fillMode: Image.PreserveAspectFit
        }

        // 应用名
        Label {
            text: "fuscan"
            font.pixelSize: 24
            font.bold: true
            color: "#24292E"
            Layout.alignment: Qt.AlignHCenter
        }

        // 阶段文本（绑定 SplashController.stage）
        Label {
            text: splashController.stage
            font.pixelSize: 13
            color: "#586069"
            Layout.alignment: Qt.AlignHCenter
            // 文本变化时淡入动画
            Behavior on text {
                FadeAnimation { duration: 150 }
            }
        }

        // indeterminate 进度条
        ProgressBar {
            indeterminate: true
            Layout.fillWidth: true
            Layout.preferredHeight: 6
            // 自定义进度条样式：圆角 + 主色
            contentItem: Rectangle {
                color: "#E1E4E8"
                radius: 3
                Rectangle {
                    width: parent.width * 0.3
                    height: parent.height
                    color: "#0366D6"
                    radius: 3
                    // 进度条左右往返动画
                    XAnimator on x {
                        from: -parent.width * 0.3
                        to: parent.width
                        duration: 1200
                        loops: Animation.Infinite
                        running: true
                    }
                }
            }
        }

        // 底部版本号（可选，从 SplashController 读取或留空）
        Label {
            text: "正在准备扫描环境"
            font.pixelSize: 11
            color: "#8B949E"
            Layout.alignment: Qt.AlignHCenter
        }

        // 底部弹簧
        Item { Layout.fillHeight: true }
    }
}
