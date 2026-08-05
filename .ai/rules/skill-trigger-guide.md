# 技能调用指引

> 适用于所有 AI 编程助手。

开发前须查阅对应技能文档获取设计系统、代码模板与硬约束。所有代码须遵守技能文档中的最佳实践。

## 语言场景

Python 项目开发前**必须**查阅 `python-standards` 技能获取跨领域通用硬约束（工具链/类型注解/数据结构/并发/测试/日志/安全/性能/Git 等）；涉及以下领域时查阅对应专项技能获取详细模式与代码模板：

- Python 通用硬约束（工具链/兼容性/类型注解/数据结构/模块与导入/函数设计/异常处理/并发/测试/代码风格/Pythonic 风格/日志/路径与资源/安全/性能/Git 与提交）→ `python-standards` 技能
- 项目骨架（src layout/pyproject.toml 元数据/PEP 631/735 依赖声明/工具链配置拆分/包内部结构/测试文档CI 目录组织/项目类型差异/版本管理与发布流程）→ `python-project-structure` 技能
- 类设计（dataclass/ABC/Enum/缓存/继承组合/设计模式）→ `python-class-design` 技能
- 并发（threading/concurrent.futures/multiprocessing/asyncio/线程安全）→ `python-concurrency` 技能
- 文件 I/O（pathlib/读写/上下文管理/临时文件/序列化/原子写入）→ `python-file-io` 技能
- 测试（pytest fixtures/parametrize/mock/coverage/pytest-qt）→ `python-testing` 技能
- CLI 开发（Click/Typer/子命令/进度/配置/测试）→ `python-cli` 技能
- 日志（dictConfig/文件轮转/结构化日志/GUI 日志面板/CLI --verbose）→ `python-logging` 技能
- 配置管理（TOML 读取/环境变量/.env/多层覆盖/Pydantic Settings/热重载）→ `python-config` 技能
- 子进程（subprocess.run/Popen/流式输出/超时/管道/GUI 集成/安全准则）→ `python-subprocess` 技能
- 性能（基线测量/cProfile 热点剖析/memray 内存分析/pytest-benchmark 回归门禁）→ `python-performance` 技能

## 项目场景

- PySide2/PySide6 GUI 项目（`project_type=gui`）：开发前**必须**查阅 `python-gui-pyside` 技能（含 PySide 硬约束简表、设计系统、四区布局、信号槽、QThread、QSS 样式等）。
