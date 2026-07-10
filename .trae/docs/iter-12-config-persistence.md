# iter-12 配置文件持久化

## 本轮目标

实现配置文件持久化，存储在 `~/.uniscan/config.yaml` 中，包括窗口几何、
历史扫描文件夹、加载规则文件列表、通用规则开关等。应用启动时自动恢复，
关闭时自动保存。

## 改动文件清单

### 新增

- `src/uniscan/config.py`：配置持久化模块
  - `Config` dataclass：窗口几何、窗口状态、分割器大小、扫描路径历史、
    规则文件路径列表、通用规则开关
  - `load_config(path)`：从 YAML 加载配置，文件不存在或解析失败返回默认值
  - `save_config(config, path)`：保存配置到 YAML，自动创建父目录
  - `CONFIG_PATH`：默认路径 `~/.uniscan/config.yaml`
  - `MAX_HISTORY`：历史记录上限 15 条
- `tests/test_config.py`：配置模块单元测试（12 个测试）

### 修改

- `src/uniscan/gui/main_window.py`：
  - 导入 `Config`、`load_config`、`save_config`、`MAX_HISTORY`、`QComboBox`
  - `__init__` 中调用 `load_config()` 加载配置，新增 `_apply_config()` 恢复状态
  - 顶部路径标签 `QLabel` 改为 `QComboBox`（`_path_combo`），支持历史下拉选择
  - `_build_main_splitter` 存储分割器引用 `self._splitter`，用于保存/恢复大小
  - 新增 `_apply_config()`：恢复窗口几何、最大化状态、分割器大小、通用规则开关、
    规则文件列表（跳过不存在的文件）、扫描路径历史
  - 新增 `_save_config()`：序列化当前窗口状态到 Config 并调用 `save_config`
  - 新增 `_add_scan_path_history(path)`：去重、最近优先、限制数量
  - 新增 `_on_path_selected(index)`：从下拉选择路径时设置 `_scan_root`
  - `_on_select_path` 更新为调用 `_add_scan_path_history`
  - `closeEvent` 新增 `_save_config()` 调用
- `tests/test_gui.py`：
  - 新增 `_isolate_config` autouse fixture，将配置读写重定向到 `tmp_path`，
    避免测试污染用户主目录
  - 新增 `TestConfigPersistence` 测试类（13 个测试）：规则路径恢复、
    不存在路径跳过、通用开关恢复、扫描历史恢复、关闭保存、路径下拉选择、
    去重、数量限制、窗口几何恢复、分割器比例恢复

## 关键决策与依据

1. **配置位置**：用户主目录 `~/.uniscan/config.yaml`，跨平台、多用户隔离
2. **保存时机**：`closeEvent` 中自动保存，无需手动操作
3. **路径标签→下拉框**：用 `QComboBox` 替换 `QLabel`，用户可从历史记录中
   直接选择扫描路径，不必每次打开对话框浏览
4. **信号隔离**：`_apply_config` 中用 `blockSignals` 阻止 checkbox 和 combo
   的信号触发，避免在恢复状态时引发不必要的规则重载
5. **不存在的规则文件**：恢复时跳过 `Path(p).exists() == False` 的路径，
   避免因文件移动/删除导致启动失败
6. **测试隔离**：autouse fixture 将 `load_config`/`save_config` 重定向到
   `tmp_path/config.yaml`，确保测试不读写用户真实配置

## 验证结果

- ruff check：全部通过
- pytest：421 passed, 1 skipped
- coverage：88.66%（门槛 80%）
- `config.py` 覆盖率 90%
- `gui/main_window.py` 覆盖率 84%

## 遗留事项

- iter-06～10 已满 5 轮，按 dev-workflow 规则应归档至 skills 并清理 docs 目录。
- tray 模式未集成配置持久化（tray 有自己的 CLI 参数，暂不需要）。
