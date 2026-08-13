"""OCR 模型文件目录（PP-OCRv3 简中）。

本目录承载随软件内置入仓的 4 个模型文件，供 :mod:`fuscan.extractors.ocr` 通过
:func:`importlib.resources.files` 离线加载，无需运行时联网下载。

模型清单（中英文通用，文件名对齐 RapidOCR-json v0.2.0 release 命名）：

- ``ch_PP-OCRv3_det_infer.onnx``：文本检测
- ``ch_ppocr_mobile_v2.0_cls_infer.onnx``：方向分类
- ``ch_PP-OCRv3_rec_infer.onnx``：中英文识别
- ``ppocr_keys_v1.txt``：识别字符字典

仅 Windows 平台可用（RapidOCR-json 预编译 exe 为 PE 格式），非 Windows 平台
:func:`is_ocr_available` 返回 False，OCR 不可用但不影响其余功能。
"""
