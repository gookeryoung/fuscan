# iter-139 用户反馈问题修复第三轮

## 需求清单

- [x] 修复用户手册打不开：`AboutController.openManual` 在 PDF 阅读器缺失或中文路径下失败时无反馈
- [x] 规则文件列表显示可用性状态：`rulesFileModel` 未暴露文件是否存在，用户无法识别缺失规则文件
- [x] 修复改规则后规则被清空无法启动扫描：`ScanController` 缓存陈旧 `ruleset`，规则变更后 `canStartScan`/`rulesCount` 读旧值
- [x] 修复移至暂存后结果列表未移除：`moveSelectedToStaging` 成功后结果列表仍保留已隔离文件
- [x] 解析速度 tip 包含引擎信息：`SettingsPage` 提取器 tooltip 仅显示速度档次，未显示底层解析引擎

## 验收标准

- 用户手册打开失败时显示 Toast 提示（中文路径兼容）
- 规则文件列表对缺失文件显示「缺失」标记
- 修改规则后 `canStartScan` 与 `rulesCount` 立即反映最新状态
- 移至暂存成功后结果列表与 `_last_report` 同步移除该条目
- 提取器 tooltip 显示「解析速度：T?（引擎：xxx）」
- 全套门禁通过：ruff check / ruff format --check / pyrefly check / pytest --cov≥95%
- QML 改动后重建 `resources_rc.py`
