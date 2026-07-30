# iter-140 测试动态档位断言修复

## 需求清单

- [x] 修复 `test_extractor_benchmark.py` 8 个 tier 声明测试写死档位导致的环境相关失败
- [x] 更新文件头注释中过时的档位说明

## 验收标准

- 8 个原失败测试（docx/odt/ods/rtf/msg/pptx/doc/ppt 的 tier 声明测试）改为动态档位断言，根据 lxml/kreuzberg 可用性判断期望档位
- DocExtractor/PptExtractor 回退档位修正为 MEDIUM（原测试写错为 SLOW）
- 文件头注释档位说明准确反映当前档位逻辑
- `test_extractor_benchmark.py` 全部通过（29 passed, 2 skipped for pdf_oxide）
- ruff check / ruff format --check / pyrefly check 通过
- 预存在的性能测试波动（test_perf_benchmark/test_xml_perf_comparison）非本次回归
