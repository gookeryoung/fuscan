# iter-96 清理遗留垃圾与覆盖率提升

## 需求清单

- [x] 修复 tests/test_workers.py 的 ruff lint 错误（unused import + lambda inline）
- [x] 删除 QML 迁移遗留垃圾 resources_rc.py
- [x] 清理 ruff.toml 与 .gitignore 中失效的排除项
- [x] 移除 test_gui_launch.py 的 slow 标记（实测仅 1.3s）
- [x] 新增 AppController 与 fuscan.gui 包入口测试
- [x] 全套门禁通过（ruff/pyrefly/pytest/coverage）

## 迭代目标

延续 iter-95 QML 迁移完成后的清理与质量提升：

1. 修复上一轮 `tests/test_workers.py` 遗留的 ruff lint 错误
2. 删除 iter-95 文档明确要删除但实际未删的 `resources_rc.py`（QML 迁移前 pyside2-rcc 生成的 1996 行资源数据，迁移后 QML 直接文件加载，无任何代码引用）
3. 通过补 AppController 测试与移除 launch 测试的 slow 标记，提升 GUI 入口层覆盖率

## 改动文件清单

### 新增
- `tests/test_gui_app_controller.py` - AppController 与 fuscan.gui 包入口测试（11 测试）

### 删除
- `src/fuscan/resources_rc.py` - QML 迁移遗留垃圾（1996 行 pyside2-rcc 生成数据，无引用）

### 修改
- `tests/test_workers.py` - ruff lint 修复（移除 unused PerfStats import；tuple 加显式类型参数；lambda 改为方法引用）
- `tests/test_gui_launch.py` - 移除 `pytest.mark.slow`（实测仅 1.3s，应纳入默认测试）
- `ruff.toml` - 移除已删文件 `resources_rc.py` 的 extend-exclude 条目
- `.gitignore` - 移除已删路径 `src/fuscan/assets/resources_rc.py` 的忽略条目

## 关键决策与依据

### 1. 删除 resources_rc.py 而非保留
- **依据**：iter-95 文档明确写"移除 resources_rc.py（pyside2-rcc 产物），QML 直接文件加载"
- **验证**：grep 全仓库仅 SKILL.md/iter 文档/ruff.toml/.gitignore/已 done 的 req-10 提及，无任何 .py/.qml 代码引用
- **清理联动**：ruff.toml 与 .gitignore 中针对该文件的排除/忽略条目变为 dead config，一并清理

### 2. 移除 test_gui_launch.py 的 slow 标记
- **依据**：实测 `pytest tests/test_gui_launch.py` 仅 1.3s（远低于 slow 阈值）
- **收益**：默认 `pytest -m "not slow"` 会运行该测试，覆盖 `launch()` 函数的 QML 加载与 controller 注册路径，使 `gui/app.py` 覆盖率从 49% 提升至 91%
- **风险**：无显示器环境已用 `QT_QPA_PLATFORM=offscreen` 跳过；无 PySide2 环境已用 `PYSIDE_AVAILABLE` 模块级 skip

### 3. AppController 测试不创建真实 QQmlApplicationEngine
- **依据**：Windows 上 PySide2 + QML 真实 engine 会触发 `STATUS_STACK_BUFFER_OVERRUN` 崩溃（iter-95 文档已记录的已知问题）
- **实现**：用 duck typing 的 `FakeContext`（仅实现 `setContextProperty`）替代真实 `QQmlApplicationEngine.rootContext()`，验证 `register_to` 调用顺序与注册名

### 4. AppController 类型注解从 fuscan.gui.qml 导入
- **依据**：`fuscan.gui.__init__.py` 通过 `__getattr__` 同时暴露 `launch`（函数）与 `AppController`（类），pyrefly 推断为联合类型而非类，导致 `controller: AppController` 注解报 `not-a-type`
- **实现**：测试中改为 `from fuscan.gui.qml import AppController` 直接从子包导入类，绕过 `__getattr__` 的联合类型推断

## 代码实现情况

### ruff lint 修复（tests/test_workers.py）
- 移除未使用的 `from fuscan.perf import PerfStats`
- `hits: tuple = ()` → `hits: tuple[RuleHit, ...] = ()`（补显式类型参数）
- `results: tuple = ()` → `results: tuple[ScanResult, ...] = ()`
- `worker.finished_ok.connect(lambda p: payloads.append(p))` → `worker.finished_ok.connect(payloads.append)`（ruff PLW0108 自动修复）
- `worker._scanner = FakeScanner()` 改为先赋值给局部变量再加 `# pyrefly: ignore [bad-assignment]`，避免访问 `worker._scanner.pause_called` 触发 `missing-attribute`

### AppController 测试覆盖
- 6 个构造测试：5 个 property 类型断言 + 子 controller parent 链验证
- 1 个 register_to 测试：FakeContext duck typing，断言 5 个 controller 全部以正确名字注册
- 1 个 cleanup 测试：monkeypatch ScanController.cleanup，验证委托调用
- 3 个包入口测试：`__getattr__` 惰性导入 launch / AppController / 未知属性抛 AttributeError

## 整合优化情况

- 删除 1996 行垃圾文件 `resources_rc.py`，源码总行数下降
- 清理 ruff.toml 与 .gitignore 的 dead config 条目
- 修复 test_workers.py 的 27 处 ruff lint 警告（包括 1 处 F401 未用导入 + 25 处 PLW0108 lambda 冗余 + 1 处 UP037 类型注解引号）
- 移除 test_gui_launch.py 的 slow 误标记，让默认测试覆盖 launch 路径

## 测试验证结果

| 模块 | 修改前 | 修改后 |
|------|--------|--------|
| src/fuscan/resources_rc.py | 0% (1996 行垃圾) | 已删除 |
| src/fuscan/gui/__init__.py | 15% | **100%** |
| src/fuscan/gui/app.py | 49% | **91%** |
| src/fuscan/gui/qml/app_controller.py | 60% | **100%** |
| src/fuscan/gui/qml/scan_controller.py | 92% | 93% |
| src/fuscan/workers/scan_worker.py | 23% | **100%** |
| src/fuscan/workers/stats_worker.py | 26% | **100%** |
| src/fuscan/workers/export_worker.py | 58% | **100%** |
| **整体覆盖率** | **92.93%** | **96.03%** |

- 测试总数：1392 → 1404（+12，新增 11 个 AppController 测试 + 1 个 launch 测试纳入默认）
- 全套门禁通过：ruff check / ruff format --check / pyrefly check / pytest 1404 passed / coverage 96.03% ≥ 95%

## 遗留事项

- [ ] manual.md 同步更新 GUI 截图与操作描述（需重新生成 PDF）
- [ ] 托盘 UI 后续单独设计（基于 watcher 功能模块构建新 UI 层）
- [ ] scan_controller.py 仍有 16 行未覆盖（多为扫描 worker 协调的边界路径，可后续补）
- [ ] rules_controller.py 82%（22 行未覆盖，多为规则文件 CRUD 的边界路径）
- [ ] gui/__main__.py 0%（5 行 `if __name__ == "__main__":` 守卫，无法单测覆盖）

## 下一轮计划

- 后续迭代：manual.md 更新与 PDF 重新生成（req-32 遗留事项之一）
- 后续迭代：托盘 UI 重新设计（req-32 遗留事项之一）
- 后续迭代：scan_controller / rules_controller 边界路径补测试，进一步提升 GUI 控制层覆盖率
