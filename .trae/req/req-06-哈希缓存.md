# 需求：基于哈希的扫描结果缓存

## 核心需求

- [x] 建立扫描规则文件（文件名、哈希）与被扫描文件（路径、文件哈希）之间的联系
- [x] 规则文件解析成每个规则条目，存储每个规则的哈希
- [x] 一个文件名对应有自身哈希以及多个规则名称及其哈希
- [x] 每次扫描后保存结果到数据库，下次扫描时若文件哈希和规则哈希都没变化则复用之前的分析结果

## 扩展需求

- [x] 全链路接入：Scanner + CLI + GUI + Tray
- [x] 替换现有 IncrementalScanner 为哈希缓存委托模式
- [x] SQLite 存储，WAL 模式，线程安全（RLock）
- [x] 维持 96% 覆盖率门槛
- [x] Python 3.8 兼容性（from __future__ import annotations）
- [x] PySide2/PySide6 双兼容 GUI
- [x] CLI cache 子命令：stats/clear/prune
- [x] SettingsDialog 加缓存设置 UI（启用开关 + 自定义路径）
