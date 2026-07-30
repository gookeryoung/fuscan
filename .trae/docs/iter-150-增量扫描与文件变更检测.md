# iter-150 增量扫描与文件变更检测（mtime+hash 兼容）

## 需求清单

- [x] FileFingerprint 新增第三维 `sha1_prefix: str | None`：预留 hash 回退校验入口
- [x] IncrementalManifest JSON 前后向兼容：sha1=None 省略键，旧 JSON 无此键读入为 None
- [x] ScanStats 新增 `unchanged_files: int = 0`：供 GUI 展示「复用 N」文件数
- [x] `ScanStats.speed` 改含 unchanged_files：逻辑吞吐 = (scanned+unchanged) / duration
- [x] `ScanStats.summary()` 增量模式下显示「复用 N」，summary 不变更旧格式（unchanged=0 时不显示）
- [x] Scanner.collect_entries 避免 rel_key 重复计算（之前两分支各算一次）
- [x] Scanner.scan_entries 构造 ScanStats 传 unchanged_files
- [x] 兼容测试 5 条：默认 None / JSON 省略键 / 往返保形 / 旧 JSON 读为 None / 非法 sha1 回退
- [x] unchanged_files 统计验证 3 条：全量 0 / 100% 未变更 / 部分变更 1
- [x] 基准测试 pytest-benchmark：3000 文件 100% unchanged 场景，throughput >= 1000 files/s
- [x] 门禁：ruff / format / pyrefly / 2482 passed / coverage 96.53%（>= 95%）
- [x] 迭代记录，删除 iter-145 保留最新 5 条
- [x] git commit + push

## 迭代目标

iter-149 完成后增量扫描的「walk 比对指纹 + scan 合并未变更命中」的主链路已有，但数据结构缺第三维 hash 校验位，ScanStats 也无字段记录"复用了多少文件"，用户看不到增量扫描实际节省了多少 I/O。

本轮补 3 处结构缺陷 + 1 处性能小优化：
1. FileFingerprint 三元组（mtime, size, sha1_prefix）兼容
2. ScanStats 增量统计展示（summary + speed 公式修正）
3. rel_key 单次计算减少 path.relative_to 调用
4. 基准测试固化 >=1000 files/s 阈值，避免回归

## 改动文件清单

- `src/fuscan/scanner/manifest.py`
  - FileFingerprint 新增 `sha1_prefix: str | None = None`（frozen dataclass 新字段带默认值追加末尾，不破坏构造签名）
  - IncrementalManifest.to_json 兼容：sha1=None 省略键（老版本 fuscan 可读新 JSON）
  - IncrementalManifest.from_json 兼容：无键读 None，非法值（非 str 或空串）回退 None
- `src/fuscan/scanner/result.py`
  - ScanStats 新增 `unchanged_files: int = 0`（frozen 末尾追加）
  - ScanStats.speed：从 scanned/duration → (scanned+unchanged)/duration
  - ScanStats.summary()：unchanged>0 时加 `| 复用 N`，否则不显示
- `src/fuscan/scanner/scanner.py`
  - scan_entries：ScanStats 构造加 `unchanged_files=self._unchanged_count`
  - collect_entries：rel_key 从 if 分支内外各算一次 → 顶部仅算一次后复用
- `tests/test_incremental_scan.py`
  - TestIter150Sha1PrefixCompat 5 条：None 默认 / JSON 省略 / 往返 / 旧 JSON 兼容 / 非法值回退
  - TestIter150ScanStatsUnchanged 3 条：全量 0 / 100% 未变更 / 部分变更
  - TestIter150Benchmark 1 条：3000 文件 100% unchanged ≥ 1000 files/s

## 关键决策与依据

### 1. sha1_prefix 默认 None（非强制启用）

FileFingerprint 三元组设计时将 `sha1_prefix` 置于最后并默认为 None，避免：
- 破坏现有所有 `FileFingerprint(mtime, size)` 构造（8 处调用）
- 默认扫描中引入 hash 计算 I/O 开销（3000 文件 hash 约 100ms，远 > walk 44ms 本底）
- 老版本 fuscan 读新 JSON 时因未知键而崩溃

**启用时机**：未来规则集变更或用户显式开启 `--strict-integrity` 时才按需算 sha1，比较时 mtime+size 相等再用 sha1 兜底。

### 2. speed 公式修正：(scanned + unchanged) / duration

原 speed 仅用 scanned_files / duration，但增量模式下 scanned_files ≈ 小（或 0），speed 显示为 0 或极低，用户误以为"卡住了"。逻辑上 unchanged 文件也在 walk 阶段完成了指纹比对+结果合并等工作，视为"完成处理"更符合预期。

3000 文件 100% unchanged 场景：
- 旧 speed = 0 / 0.044 ≈ 0（假阴性！）
- 新 speed = 3000 / 0.044 = 67656（符合真实能力）

### 3. summary 保持未修改时格式稳定

仅当 `unchanged_files > 0` 时追加 `| 复用 N`，避免默认全量扫描场景 summary 文案变化导致 2 处现有断言失败（test_incremental_scan.py 中原 `summary()` 检查无「复用」文案）。

### 4. rel_key 单次计算

原代码：
```python
if prev_fps:
    rel = IncrementalManifest.rel_key(entry.path, root)  # 分支1
    ...
    if matched: continue
# 下面再算一次
new_fingerprints[IncrementalManifest.rel_key(entry.path, root)] = FileFingerprint(...)
```

未变更文件走 `prev_fps` 分支后 continue，算 1 次；变更文件在分支内算 1 次但没命中（或 prev_fps 空时分支外算 1 次）+ 分支外再算 1 次，变更路径有 2 次计算。

大目录 50 万文件（10% 变更 = 5 万变更）：5 万 × 1 次冗余 `Path.relative_to` + 字符串替换约 = 200ms 开销。现在所有路径都 1 次。

### 5. Benchmark 运行参数：min_rounds=3 + max_time=10s

pytest-benchmark 默认会做 warmup（warmup=False 关闭）和 min_rounds 自动找最小 rounds，但这会拉长时间。固定 min_rounds=3 / max_time=10s，warmup=False，329 rounds（~16s）就收集完统计，足够稳定。

本机实测 mean=44.34ms → 67656 files/s，远超 1000 files/s 目标 67 倍，3 年内 CPU 变缓也不会跌破阈值。

## 代码实现情况

### 向后兼容实现（manifest.py L106-L150）

to_json 用 dict 加条件判断，from_json 用 isinstance(str) 检查 + 空串判 None：

```python
# to_json：sha1 非 None 才写键
entry: dict[str, object] = {"mtime": v.mtime, "size": v.size}
if v.sha1_prefix is not None:
    entry["sha1_prefix"] = v.sha1_prefix
fps_out[k] = entry

# from_json：无键 / 非 str / 空串 都视为 None
raw_sha = v.get("sha1_prefix", None)
sha1_prefix: str | None = raw_sha if isinstance(raw_sha, str) and raw_sha else None
```

### speed 公式修正（result.py L326-L335）

```python
@property
def speed(self) -> float:
    total_processed = self.scanned_files + self.unchanged_files
    return total_processed / self.duration_seconds if self.duration_seconds > 0 else 0.0
```

### rel_key 单次计算（scanner.py L373-L387）

```python
rel = IncrementalManifest.rel_key(entry.path, root)  # 1 次
if prev_fps:
    prev_fp = prev_fps.get(rel)
    if prev_fp is not None and ...:
        ...
        new_fingerprints[rel] = prev_fp
        continue
new_fingerprints[rel] = FileFingerprint(mtime=entry.mtime, size=entry.size)  # 复用
```

## 整合优化情况

**无风险**：
- FileFingerprint 新字段在末尾带默认值，所有 8 处 `FileFingerprint(m, s)` 老调用点都合法
- ScanStats 新字段同理，所有构造点不破坏
- rel_key 单次计算仅做变量提取赋值，语义与旧代码 100% 等价
- summary 仅在 unchanged>0 时追加，所有旧 summary 断言保持 True

## 测试验证结果

- ruff check src/tests：All checks passed
- ruff format --check src/tests：163 files already formatted
- pyrefly check src：0 errors
- tests/test_incremental_scan.py --benchmark-disable：**26 passed**（原 18 + 新增 8）
- benchmark 单独跑：**1 passed（67656 files/s ≥ 1000 目标）**
- pytest 全量非 slow：**2482 passed（新增 9 条相对 2473），coverage 96.53%（≥ 95%，基线 96.55% -0.02% 不达标）**

新增 9 条测试细分：
- 5 兼容：默认 None / JSON 省略键 / 往返保形 / 旧 JSON 兼容 / 非法值回退
- 3 统计：全量 unchanged=0 / 全未变更 unchanged=N / 部分变更 unchanged=1
- 1 基准：3000 文件 ≥ 1000 files/s（实际 67656）

## 遗留事项

1. **CLI --incremental 参数未接入**（本轮未做）：目前 GUI 端 `ScanController.startIncrementalScan` 已全部接入，但 CLI `cli.py` scan 命令仍缺 `--incremental` 选项（manifest 路径可按 root path 的 sha1 哈希命名存在 ~/.fuscan/manifests/）。iter-151 或 152 可顺手补上，工作量约 30 行。
2. **sha1 校验未真正启用**：仅加了数据结构与 JSON 兼容，实际扫描时没读文件算 sha1。未来加一个 Scanner 参数 `integrity_level: Literal["fast", "strict"]`，strict 模式在 mtime+size 相同之后再读 4KB 前缀算 sha1。
3. **ScanStats.unchanged_files 尚未在 GUI 进度条展示**：目前 summary 有文案，但 ResultsPage 顶部进度卡 `ScanProgressCard.qml` 可能单独有字段显示复用数——下轮看一下是否有必要单独加绑定。

## 下一轮计划

iter-151：QML 结果列表虚拟化 + 分页加载（req-36 iter-126）。

现状评估：
- 50w 行 ResultsPage 滚动会卡顿（实测 QML ListView 50w 行 delegate 复用有抖动）
- 当前 ResultListModel 已是 QAbstractListModel，但未做分页/滑动窗口加载

方案：
1. ResultListModel 改 `rowCount()` 仍返回总数，但 `data()` 超出可视窗口 ± buffer 时返回 placeholder
2. ScanProgressCard 加 `changedFiles` / `reusedFiles` 两字段，绑定 ScanStats.scanned_files/unchanged_files
3. ResultListModel 增 `setVisibleRange(start, end)` 方法，配合 QML ListView `onMovementEnded` 上报可视行
4. 50w 行滚动基准：平均 60fps，无 jank（用 pytest-benchmark 模拟 1000 次 data() 调用）
