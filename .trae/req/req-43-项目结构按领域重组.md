# iter-141 项目结构按领域重组

## 需求清单

- [x] 按"彻底：按领域全重组"方案重新组织 `src/fuscan/` 目录结构，使职责划分更清晰
- [x] `workers/` 子包移入 `gui/workers/`（Worker 仅服务于 GUI 层）
- [x] 拆分 `config.py`：保留纯配置，资产路径移至 `paths.py`，规则加载移至 `rules/builtin.py`，暂存/备份目录探测移至 `processing/storage.py`
- [x] 导出逻辑集中到 `export/` 子包（`report.py`、`cli_output.py`）
- [x] 同步更新测试文件命名（`test_<包>_<模块>.py`）与导入路径
- [x] 全套门禁验证：ruff check / ruff format --check / pyrefly / pytest --cov≥95%

## 验收标准

- `src/fuscan/` 顶层仅保留领域子包与入口模块，无跨领域混杂模块
- 所有测试通过，覆盖率 ≥ 95%
- ruff / pyrefly 全部通过，无新增告警
- 测试文件命名遵循 `test_<包>_<模块>.py` 规范
