"""原子写入工具：写入临时文件后 ``Path.replace`` 覆盖，避免半写损坏。

提供项目内跨模块复用的原子写入函数，供 :mod:`fuscan.history`、
:mod:`fuscan.processing`、:mod:`fuscan.rules` 等持久化场景共享，
避免重复实现 ``mkdir`` + 临时文件 + ``replace`` 样板。

公共 API：

- :func:`atomic_write_text`：原子写入文本文件（UTF-8）
- :func:`atomic_write_bytes`：原子写入二进制文件

异常策略：写入或替换失败时 ``OSError`` 向上抛，由调用方决定日志策略
（记录后吞掉或重抛为业务异常）。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["atomic_write_bytes", "atomic_write_text"]


def atomic_write_text(path: Path, content: str) -> None:
    """原子写入文本文件：写入同目录临时文件后 ``Path.replace`` 覆盖目标。

    父目录不存在时自动创建（``mkdir(parents=True, exist_ok=True)``）。
    写入使用 UTF-8 编码。临时文件命名为 ``<原名>.tmp``，与目标同目录
    以保证 ``Path.replace`` 的原子性（同文件系统内 rename 是原子操作）。

    :param path: 目标文件路径。
    :param content: 文本内容。
    :raises OSError: 写入临时文件或替换目标失败（异常向上抛，由调用方处理）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def atomic_write_bytes(path: Path, raw: bytes) -> None:
    """原子写入二进制文件：写入同目录临时文件后 ``Path.replace`` 覆盖目标。

    父目录不存在时自动创建（``mkdir(parents=True, exist_ok=True)``）。
    临时文件命名为 ``<原名>.tmp``，与目标同目录以保证 ``Path.replace``
    的原子性。

    :param path: 目标文件路径。
    :param raw: 二进制内容。
    :raises OSError: 写入临时文件或替换目标失败。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
