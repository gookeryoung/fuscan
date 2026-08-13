"""OCR 模型文件目录（PP-OCRv4 mobile）。

本目录承载随软件分发的 4 个 ONNX 模型文件，供 :mod:`fuscan.extractors.ocr`
通过 :func:`importlib.resources.files` 离线加载，避免运行时联网下载。

模型清单（中英文通用，文件名对齐 RapidOCR v3+ ModelScope 上游命名）：

- ``ch_PP-OCRv4_det_mobile.onnx``：文本检测
- ``ch_ppocr_mobile_v2.0_cls_mobile.onnx``：方向分类
- ``ch_PP-OCRv4_rec_mobile.onnx``：中英文识别
- ``ppocr_keys_v1.txt``：识别字符字典

真实模型由 ``scripts/download_ocr_models.py`` 从魔搭社区下载并 SHA256 校验；
未下载时本目录仅有占位文件（hatchling force-include 需文件存在）。
"""
