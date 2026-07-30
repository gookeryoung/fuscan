# req-44 线程内存卡滞修复

## 背景

iter-145 cache.db 异常膨胀至 15.7GB，用户手动清理但根因未解决。综合诊断 4 类问题：

1. **内存泄漏与无法退出的线程**：CacheStore 读连接僵尸句柄、ScanController quick_cancel 残留 QThread、Scanner 每次 scan 重建 pool
2. **界面卡滞**：quick_cancel/cleanup 主线程 wait 阻塞累计
3. **性能瓶颈**：CacheStore 实例与连接无复用、进度回调开销
4. **隐患**：cleanup 路径不一致（quick_cancel 不关 cache，cleanup 关 cache）

## 需求清单

- [x] FIX-1：CacheStore 读连接跟踪改 `weakref.WeakSet`，修复原每次创建新连接的 BUG（正常路径 worker 退出后连接自动 GC）
- [x] FIX-2：统一 ScanController cleanup 路径，`quick_cancel` 退出路径增加 cache.close()（daemon thread 异步执行避免阻塞主线程）
- [x] FIX-3：评估 Scanner pool 复用必要性（跳过：收益小风险高）
- [x] FIX-4：`workspace_controller.cleanup` 退出路径降低 wait 超时到 200ms
- [x] FIX-5：并发模式进度回调 batch 从 5 提高到 10
- [x] 新增测试覆盖：6 条（连接复用/WeakSet 自动清理/quick_cancel 异步关 cache/worker 置 None）
- [x] 门禁检查（ruff/format/pyrefly/pytest/coverage）通过且覆盖率不下降（96.52%）
- [x] 写迭代记录 iter-147，删除 iter-142 保留最新 5 条
- [x] git commit + push
