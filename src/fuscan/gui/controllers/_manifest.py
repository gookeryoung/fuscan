"""增量扫描清单持久化纯函数。

将 :class:`ScanController` 中 ``_load_manifest`` / ``_save_manifest`` /
``invalidate_manifest`` 的纯 I/O 逻辑抽离到模块级，便于独立测试。
``ScanController`` 对应方法改为薄包装：传入工作区 ID 与清单目录后委托本模块。

公共 API：

- :func:`load_manifest`：从 ``<manifests_dir>/<ws_id>.json`` 加载清单
- :func:`save_manifest`：持久化清单到 ``<manifests_dir>/<ws_id>.json``
- :func:`invalidate_manifest`：删除工作区清单（规则变更后强制下次全量扫描）
"""

from __future__ import annotations

import logging
from pathlib import Path

from fuscan.scanner.manifest import IncrementalManifest

__all__ = [
    "invalidate_manifest",
    "load_manifest",
    "save_manifest",
]

logger = logging.getLogger(__name__)


def load_manifest(ws_id: str, manifests_dir: Path) -> IncrementalManifest | None:
    """从 ``<manifests_dir>/<ws_id>.json`` 加载增量扫描清单。

    :param ws_id: 工作区 ID
    :param manifests_dir: 清单持久化目录（``_MANIFESTS_DIR``）
    :return: :class:`IncrementalManifest` 实例；文件不存在或解析失败返回 ``None``
    """
    manifest_file = manifests_dir / f"{ws_id}.json"
    if not manifest_file.exists():
        return None
    try:
        json_str = manifest_file.read_text(encoding="utf-8")
        return IncrementalManifest.from_json(json_str)
    except (OSError, ValueError) as exc:
        logger.warning("工作区 %s 增量清单加载失败: %s", ws_id, exc)
        return None


def save_manifest(ws_id: str, manifest: IncrementalManifest, manifests_dir: Path) -> None:
    """持久化增量扫描清单到 ``<manifests_dir>/<ws_id>.json``。

    :param ws_id: 工作区 ID
    :param manifest: 本次扫描构建的新清单
    :param manifests_dir: 清单持久化目录（``_MANIFESTS_DIR``）
    """
    try:
        manifests_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = manifests_dir / f"{ws_id}.json"
        manifest_file.write_text(manifest.to_json(), encoding="utf-8")
        logger.debug("工作区 %s 增量清单已持久化（%d 项指纹）", ws_id, len(manifest.fingerprints))
    except OSError as exc:
        logger.warning("工作区 %s 增量清单持久化失败: %s", ws_id, exc)


def invalidate_manifest(ws_id: str, manifests_dir: Path) -> None:
    """删除工作区的增量扫描清单。

    规则变更（新增/修改/删除/导入规则）时调用，使下次增量扫描因 manifest 不存在
    而回退为全量扫描，确保新规则被实际执行。

    :param ws_id: 工作区 ID
    :param manifests_dir: 清单持久化目录（``_MANIFESTS_DIR``）
    """
    manifest_file = manifests_dir / f"{ws_id}.json"
    if manifest_file.exists():
        try:
            manifest_file.unlink()
            logger.info("工作区 %s 规则已变更，增量清单已清除", ws_id)
        except OSError as exc:
            logger.warning("工作区 %s 增量清单清除失败: %s", ws_id, exc)
