# 需求04：界面用 .ui 文件构建

- [x] 1. 将 main_window.py、detail_dialog.py、rule_editor.py 三个 GUI 文件的界面构建从 Python 硬编码改为 .ui 文件定义。
- [x] 2. 使用 pyside2-uic 编译 .ui 为 _ui.py，编译产物提交版本控制。
- [x] 3. 将硬编码的 QSS 样式表迁移到独立 .qss 文件，运行时加载。
- [x] 4. 菜单栏、工具栏、action、快捷键纳入 .ui 文件声明，Python 代码只做信号槽连接。
- [x] 5. 保持双兼容（PySide2/PySide6）编码模式。
- [x] 6. 全部 193 个 GUI 测试通过，覆盖率不低于上一轮（90.32%）。
- [x] 7. ruff check 无新增错误类型，ruff format 通过。
