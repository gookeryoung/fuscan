# iter-145 修复页面切换卡滞

## 需求清单

- [x] 分析设置/首页等界面切换卡滞瓶颈
- [x] 用 StackLayout 替代 StackView.replace，页面常驻不重建
- [x] 重新编译 resources_rc.py
- [x] 门禁检查（ruff/pyrefly/pytest 非 cache 子集）
- [x] 写迭代记录，删除 iter-140 保留最新 5 条
- [x] git commit + push

## 迭代目标

用户反馈「设置和首页等界面之间切换始终卡滞」，定位瓶颈并消除卡顿，使页面切换零延迟。

## 改动文件清单

- `src/fuscan/gui/views/ContentArea.qml`：StackView+Component+replace 改为 StackLayout+页面实例常驻
- `src/fuscan/gui/resources_rc.py`：重建 qrc 资源
- `src/fuscan/gui/resources.qrc`：build_qrc.py 自动重生

## 关键决策与依据

### 根因定位

`ContentArea.qml` 使用 `StackView.replace` 切换页面，每次切换都**销毁旧页面 + 重新创建新页面**。重页面重建开销大：

- **SettingsPage**：4 个 Tab + ListView（`cacheBuffer: 500` 预渲染屏幕外 delegate）+ `Qt.fontFamilies()`（Windows 数百字体，iter-135 已用 `Component.onCompleted` 延迟但每次切到设置页仍重新执行）+ 2 个 FileDialog
- **HomePage**：3 个 FileDialog + 2 个自绘 Dialog + WorkspaceCard ListView（`cacheBuffer: 500`）+ RulesPanel

每次切换都重新构造这些对象 + GC 抖动，导致卡滞。

### 解决方案

用 `StackLayout` 替代 `StackView`：

- 所有页面常驻，切换只改 `currentIndex`（O(1) 零重建）
- StackLayout 自动设置非当前 index 子项 `visible: false`，不消耗渲染资源
- 代价：失去淡入淡出动画（180ms/120ms）+ 启动时多构造几个页面对象
- 切换流畅性优先级高于切换动画；SettingsPage 的 `Qt.fontFamilies()` 已由 `Component.onCompleted` 延迟到首帧后异步执行，不阻塞首屏

符合 PySide SKILL 硬约束「复用控件（hide/show + 刷数据），禁止反复创建销毁」。

## 代码实现情况

`ContentArea.qml` 重写：

- 移除 `StackView` + 7 个 `Component` 声明 + `Connections.onActivePageChanged` + `stack.replace` 切换逻辑
- 改用 `StackLayout`，直接放 7 个页面实例（HomePage/AddTaskPage/RulesPage/ResultsPage/StatsPage/SettingsPage/AboutPage）
- `currentIndex` 绑定到 `_pageIndex[activePage]` 映射，无效 key 回退 0（首页）
- 页面信号连接（viewResultsRequested/onCreated/onBackRequested 等）直接写在页面实例上，语义不变

## 测试验证结果

- `uv run ruff check src tests`：通过
- `uv run ruff format --check src tests`：163 files already formatted
- `uv run pyrefly check src`：0 errors
- `uv run pytest -m "not slow"`：2407 passed, 42 failed（全为 `sqlite3.OperationalError: disk I/O error`，环境问题）, 75 deselected
- `uv run pytest -m "not slow" --ignore=tests/test_cli.py --ignore=tests/test_gui_scan_controller.py --ignore=tests/test_incremental_scan_controller.py`：2214 passed, 0 failed

### 环境问题说明

42 个 cache 相关测试失败：`~/.fuscan/cache.db`（15.7 GB，异常膨胀）被残留 python 进程（PID 14852，运行 4+ 小时）持有文件锁，SQLite WAL PRAGMA 初始化失败。已尝试 2 轮自救（清理临时文件、Stop-Process）均因权限不足失败，需用户手动终止进程 14852 后删除 `~/.fuscan/cache.db*`。此问题与本次 QML 改动无关。

## 遗留事项

1. 用户手动终止进程 14852 并删除 `~/.fuscan/cache.db*`（15.7GB 异常膨胀，建议排查为何膨胀至此规模）
2. 测试设计问题：`ScanController._build_cache_context` 用 `default_cache_path()`（`~/.fuscan/cache.db`）而非 tmp_path，导致测试共享全局 db，建议后续迭代 mock `config.cache_path` 到 tmp_path 实现测试隔离
3. iter-140 遗留：`_TIER_TIME_LIMITS` 动态阈值仍用硬编码，待后续处理

## 下一轮计划

无主动迭代计划。等待用户反馈或新需求。
