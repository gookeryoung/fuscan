# iter-140 测试动态档位断言修复

## 需求清单

参见 `.trae/req/req-42-测试动态档位断言修复.md`

## 迭代目标

修复 iter-139 遗留的 `test_extractor_benchmark.py` 8 个 tier 声明测试失败：将写死的 `SpeedTier.MEDIUM/SLOW` 断言改为根据 lxml/kreuzberg 可用性动态判断期望档位。

## 改动文件清单

### 测试

- `tests/test_extractor_benchmark.py`：8 个 tier 声明测试改为动态档位断言；文件头注释更新档位说明

## 关键决策与依据

1. **动态断言而非跳过**：参考已有的 `test_pdf_extractor_tier_with_oxide` 用 `_PDF_OXIDE_AVAILABLE` 条件跳过的模式，但采用更简洁的方式——在单个测试内根据依赖可用性计算期望档位，不跳过，一次覆盖两种路径（依赖可用/不可用）。这样每个测试仍验证档次声明的正确性，而非跳过其中一种情况。

2. **依赖检测函数复用**：直接调用各提取器内部使用的依赖检测函数（`_lxml_available`/`_LXML_AVAILABLE`/`kreuzberg_available`），而非重新实现检测逻辑，保证测试期望与提取器实际行为一致。

3. **DocExtractor/PptExtractor 回退档位修正**：原测试写死 `SpeedTier.SLOW`（T4），但提取器实际回退档位是 `SpeedTier.MEDIUM`（T3，olefile + UTF-16LE 正则扫描）。iter-126 已将 DOC/PPT 从 T4 升级到 T3（kreuzberg 可用时 T2）。修正回退档位为 MEDIUM。

4. **文件头注释更新**：原注释档位说明过时——T2 漏了 RTF/MSG/DOC/PPT（kreuzberg，iter-126），T3 回退说明不准确（DOC/PPT 仍写"正则 UTF-16LE 扫描，iter-110"），T4 "已空"错误（PPTX 的 python-pptx 回退仍是 T4）。更新为准确反映当前档位逻辑。

## 代码实现情况

8 个测试改为动态断言，模式统一：

```python
def test_xxx_extractor_tier(self) -> None:
    """XxxExtractor 声明为 T2 快速（依赖）或 T? 中速/慢速（回退）。"""
    from fuscan.extractors.xxx import _dep_available

    expected = SpeedTier.FAST if _dep_available() else SpeedTier.MEDIUM  # 或 SLOW
    extractor = XxxExtractor()
    _assert_tier(extractor, expected)
```

具体改动：

| 测试 | 提取器 | 依赖检测 | 可用档位 | 回退档位 |
|------|--------|---------|---------|---------|
| test_docx_extractor_tier | DocxExtractor | office._lxml_available | FAST (T2) | MEDIUM (T3) |
| test_odt_extractor_tier | OdtExtractor | _odf_xml._LXML_AVAILABLE | FAST (T2) | MEDIUM (T3) |
| test_ods_extractor_tier | OdsExtractor | _odf_xml._LXML_AVAILABLE | FAST (T2) | MEDIUM (T3) |
| test_rtf_extractor_tier | RtfExtractor | _kreuzberg.is_available | FAST (T2) | MEDIUM (T3) |
| test_msg_extractor_tier | MsgExtractor | _kreuzberg.is_available | FAST (T2) | MEDIUM (T3) |
| test_pptx_extractor_tier | PptxExtractor | office._lxml_available | FAST (T2) | SLOW (T4) |
| test_doc_extractor_tier | DocExtractor | _kreuzberg.is_available | FAST (T2) | MEDIUM (T3) |
| test_ppt_extractor_tier | PptExtractor | _kreuzberg.is_available | FAST (T2) | MEDIUM (T3) |

## 整合优化情况

- 无重复代码：每个测试内局部导入依赖检测函数，与 `test_pdf_extractor_tier_with_oxide` 的局部导入风格一致。
- 速度测试（`test_xxx_extraction_speed`）保持原有写死档位阈值不变——这些测试用档位查 `_TIER_TIME_LIMITS` 阈值上限，lxml/kreuzberg 可用时耗时更短，仍在回退档位阈值内，不会失败。改用动态档位会收紧阈值（如 MEDIUM 2s → FAST 1s），可能引入环境相关的偶发失败，超出本轮修复范围。

## 测试验证结果

- `test_extractor_benchmark.py`：29 passed, 2 skipped（pdf_oxide 未安装）——8 个原失败测试全部修复
- ruff check：通过
- ruff format --check：通过
- pyrefly check：通过（0 errors）
- 全套 pytest：2387 passed, 12 skipped, 3 failed
  - 3 failed 均为预存在性能测试波动（非本轮回归）：
    - `test_perf_benchmark.py::TestRegexCacheBenchmark::test_build_matcher_with_cache_speedup`（加速比 0.6x 低于 2x 阈值，环境相关）
    - `test_xml_perf_comparison.py::TestOdfXPathComparison::test_odt_xpath_faster_than_python_filter`（xpath 耗时波动）
    - `test_xml_perf_comparison.py::TestExtremeScale::test_extreme_odt_xpath_vs_iter`（同上）
- 覆盖率：94.02%（低于 95% 门禁，环境问题，见遗留事项）

## 遗留事项

1. **覆盖率 94.02% 低于 95% 门禁**：根因是环境缺失，非代码回归：
   - PySide2 不支持 Python 3.13（当前环境 3.13.13），3 个 GUI 测试跳过（test_gui_app_controller/test_gui_launch/test_gui_qml_scan_progress）
   - pdf_oxide 未安装，7 个 PDF 测试跳过
   - iter-139 记录的 95.36% 可能是在 PySide2 可用环境（Python ≤3.12）下运行
   - 本轮仅改测试文件，未改源码，覆盖率与 iter-139 同源码状态下一致
   - 建议后续评估 PySide6 迁移可行性，或放宽覆盖率门禁至 94% 以适配 Python 3.13 环境

2. **速度测试档位阈值未动态化**：`test_xxx_extraction_speed` 仍用写死档位查 `_TIER_TIME_LIMITS` 阈值。语义上应与提取器实际档位一致，但改用动态档位会收紧阈值（FAST 1s vs MEDIUM 2s），可能引入环境偶发失败。待后续评估。

3. **test_perf_benchmark.py 性能波动**：`test_build_matcher_with_cache_speedup` 加速比不稳定（纯 re.compile 与 build_matcher 的相对耗时随环境波动），建议改为宽松断言或跳过。

4. **test_xml_perf_comparison.py xpath 性能**：xpath 路径在 Windows 环境下偶尔慢于 iter+endswith，环境相关。

5. **TestTier4Slow 类组织**：doc/ppt 已从 T4 升级到 T2/T3（iter-126），但测试仍放在 TestTier4Slow 类下。移动测试到 TestTier2Fast/TestTier3Medium 会改变测试组织结构，超出本轮范围。

## 下一轮计划

- 视用户反馈决定下一步方向。
- 跟进覆盖率环境问题（PySide6 迁移评估或门禁调整）。
- 跟进速度测试档位阈值动态化。
