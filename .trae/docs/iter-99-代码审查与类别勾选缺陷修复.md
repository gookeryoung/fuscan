# iter-99 代码审查与类别勾选缺陷修复

## 需求清单

- [x] 分析优化代码存在的问题，提出解决方案
- [x] P0：修复类别头部 CheckBox onToggled 信号死循环（UI 卡死风险）
- [x] P1：类别勾选绕过持久化（重启丢失、计数不刷新）
- [x] P1：enabled_extensions 空模型语义错误
- [x] P2：setCategoryEnabled 逐行 emit 改批量；_category_sort_key dict 化
- [x] P3：模块 docstring 与 categoryStates 注释更新
- [x] 全套门禁通过

## 迭代目标

对 iter-98 交付的类别分组代码做审查，修复发现的缺陷并补齐测试。

## 改动文件清单

### 修改

- `src/fuscan/gui/views/pages/SettingsPage.qml`
  - 类别头部 CheckBox `onToggled` → `onClicked`：Qt 的 toggled 信号对程序性
    checkState 修改同样发射，updateState() 会被误判为用户点击——用户勾满
    某组最后一项时，updateState 切到 Checked 触发 onToggled → 读到 "all"
    → setCategoryEnabled(false) 全组清空 → 又触发 toggled → 读到 "none"
    → setCategoryEnabled(true) → 无限信号循环卡死 UI
  - 改调 `configController.setCategoryEnabled`（原直接调 model，绕过持久化）

- `src/fuscan/gui/controllers/config_controller.py`
  - 新增 `setCategoryEnabled(category, enabled)` Slot：同步
    `Config.disabled_extractors`、emit `extractorCountChanged`、save 持久化；
    docstring 注明必须经此方法同步配置的原因

- `src/fuscan/gui/models/extractor_model.py`
  - `enabled_extensions()` 新增空模型防御：`all([])` 为 True 会误判「全部勾选」
    返回 None（扫描所有文件），空模型应返回空 tuple（无提取器可用）
  - `setCategoryEnabled` 改为批量 emit：行按类别排序后命中行必连续，
    一次 dataChanged 替代逐行 N 次（rule-12 批量更新原则）
  - `_category_sort_key` 预构建 `_CATEGORY_ORDER_INDEX` dict 映射，
    O(1) 查找且消除 try/except + pragma 防御分支
  - 模块 docstring 更新：排序依据改为 category 分组、补充
    categoryStates/setCategoryEnabled 公共 API、删除过时的「简化父子联动」描述
  - `categoryStates` docstring 修正：QML 按键查询而非迭代 dict

- `tests/test_gui_extractor_model.py`
  - `TestEnabledExtensions.test_empty_model_returns_empty_tuple`：空模型语义
  - `TestSetCategoryEnabled.test_emits_signals_exactly_once`：批量 emit 仅一次
  - `TestSetCategoryEnabled.test_no_change_emits_nothing`：无变化不发信号

- `tests/test_gui_config.py`
  - 新增 `TestCategorySelection` 4 个用例：禁用/启用类别持久化、
    extractorCountChanged 发射、配置写入磁盘

## 关键决策与依据

1. **onClicked 优于 onToggled + 标志位**：两种方案均可屏蔽程序性触发的
   死循环，onClicked 语义天然为「仅用户交互」，无需引入 `_updating` 布尔
   标志，最简洁且后续维护者不会误用。

2. **持久化收敛到 ConfigController**：类别勾选与单项勾选走同一条
   「model 更新 → 同步 Config → emit 计数 → save」路径，避免 QML 直接
   操作 model 绕过持久化。

3. **不实施的微优化**：`set_extractor_enabled` 线性查找 O(N)、`data()` 中
   extensions join 每次重算、`categoryStates` 全量重建 dict——数据量仅
   18 行 × 6 类，引入索引/缓存收益为零，违反最小复杂度原则。

## 代码实现情况

- P0/P1 缺陷全部修复；QML 侧死循环修复依赖 onClicked 语义，无法被
  Python 单测覆盖，正确性由 Qt 信号语义保证（clicked 仅用户交互发射）
- Python 侧新增 7 个测试用例，覆盖空模型语义、批量 emit 次数、
  无变化静默、类别持久化全链路

## 测试验证结果

- `uv run ruff check`（修改文件）：通过
- `uv run ruff format`（修改文件）：通过
- `uv run pyrefly check`：0 errors
- `uv run pytest -m "not slow" --cov=fuscan --cov-fail-under=95`：
  1526 passed（新增 7 个），覆盖率 95.63%

## 遗留事项

- 无

## 下一轮计划

- 无（待用户反馈或新需求）
