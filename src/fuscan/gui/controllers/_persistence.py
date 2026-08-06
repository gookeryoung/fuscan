"""工作区持久化与任务级覆盖序列化纯函数。

将 :class:`WorkspaceController` 中与工作区持久化（JSON 读写）、任务级覆盖
序列化/反序列化相关的纯逻辑抽离到模块级，便于独立测试。

持久化文件路径运行时由 ``config_module.CONFIG_DIR / _PERSIST_FILENAME`` 计算，
``config_module.CONFIG_DIR`` 受测试 monkeypatch 控制，故路径在调用时计算。

公共 API：

- :data:`PERSIST_FILENAME` / :data:`PERSIST_VERSION`：持久化文件元数据
- :data:`TASK_OVERRIDE_KEYS`：任务级覆盖白名单（仅临时规则相关字段）
- :func:`serialize_task_overrides`：序列化 task_overrides 供 JSON 持久化
- :func:`deserialize_task_overrides`：反序列化 task_overrides（容错）
- :func:`serialize_workspace`：序列化单个工作区为 dict
- :func:`serialize_workspaces`：序列化工作区列表为持久化 payload
- :func:`load_persisted_workspaces`：从 JSON 文件加载工作区 dict 列表
- :func:`save_persisted_workspaces`：保存工作区 payload 到 JSON 文件
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fuscan.gui.models.workspace_model import WorkspaceItem

__all__ = [
    "PERSIST_FILENAME",
    "PERSIST_VERSION",
    "TASK_OVERRIDE_KEYS",
    "coerce_float",
    "coerce_int",
    "coerce_str",
    "coerce_str_tuple",
    "deserialize_task_overrides",
    "load_persisted_workspaces",
    "save_persisted_workspaces",
    "serialize_task_overrides",
    "serialize_workspace",
    "serialize_workspaces",
]

logger = logging.getLogger(__name__)


def coerce_str(value: object, default: str = "") -> str:
    """将任意值安全转换为 ``str``。

    ``None`` 返回 ``default``；非字符串值调用 ``str()`` 转换。
    用于从 JSON 反序列化的 ``dict[str, object]`` 中提取字符串字段。

    :param value: 原始值（可能是 str/int/None 等）
    :param default: ``None`` 或转换失败时的默认值
    :return: 类型安全的字符串
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def coerce_int(value: object, default: int = 0) -> int:
    """将任意值安全转换为 ``int``。

    ``None`` 或非数字值返回 ``default``；``bool`` 视为非数字以避免 ``True→1`` 的语义混淆。

    :param value: 原始值（可能是 int/str/None 等）
    :param default: 转换失败时的默认值
    :return: 类型安全的整数
    """
    if isinstance(value, bool):  # bool 是 int 的子类，单独拦截
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def coerce_float(value: object, default: float = 0.0) -> float:
    """将任意值安全转换为 ``float``。

    ``None`` 或非数字值返回 ``default``；``bool`` 视为非数字以避免 ``True→1.0`` 的语义混淆。

    :param value: 原始值（可能是 float/int/str/None 等）
    :param default: 转换失败时的默认值
    :return: 类型安全的浮点数
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def coerce_str_tuple(value: object) -> tuple[str, ...]:
    """将任意值安全转换为 ``tuple[str, ...]``。

    ``list[str]`` / ``tuple[str, ...]`` 直接转换；其他类型返回空元组。

    :param value: 原始值（可能是 list/tuple/None 等）
    :return: 类型安全的字符串元组
    """
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    return ()


# 持久化文件名（路径在运行时计算，跟随 CONFIG_DIR monkeypatch）
PERSIST_FILENAME = "workspaces.json"
PERSIST_VERSION = 1

# 允许任务级覆盖的 Config 字段及类型校验器
# task_overrides 仅承载「任务级临时规则」相关字段（不再包含任务级扫描设置）：
# rules_paths/use_builtin：任务级规则覆盖，覆盖时扫描使用任务专属规则集，
# 未覆盖时回退全局 RulesController
# temp_rules_paths：任务级临时规则文件路径，叠加在全局规则之上（不覆盖），
# 仅对当前工作区生效，扫描时与全局启用的规则合并
# disabled_temp_rules_paths：任务级禁用的临时规则文件路径（不参与扫描合并），
# 与全局 disabled_rules_paths 同语义，仅持久化到 task_overrides（非全局 Config），
# 配合 temp_rules_paths 实现"保留但禁用"——临时规则从列表移除前可单独禁用
#
# 历史上 task_overrides 还承载 scan_archives/max_workers/max_file_size/max_depth/
# ignore_dirs 等「任务级扫描设置」，因该功能无实际配置入口已移除；扫描参数统一
# 由 effective RuleSet（全局规则集）决定，不再支持逐任务覆盖。
TASK_OVERRIDE_KEYS: dict[str, type] = {
    "rules_paths": tuple,
    "use_builtin": bool,
    "temp_rules_paths": tuple,
    "disabled_temp_rules_paths": tuple,
}


def serialize_task_overrides(overrides: dict[str, object]) -> dict[str, object]:
    """序列化 task_overrides 供 JSON 持久化。

    ``rules_paths``/``temp_rules_paths``/``disabled_temp_rules_paths`` 的 tuple
    转为 list（JSON 不支持 tuple），其余字段原样返回。非白名单字段被剔除。

    :param overrides: 原始 task_overrides 字典
    :return: 可 JSON 序列化的 dict
    """
    out: dict[str, object] = {}
    for key, value in overrides.items():
        if key not in TASK_OVERRIDE_KEYS:
            continue
        if key in ("rules_paths", "temp_rules_paths", "disabled_temp_rules_paths") and isinstance(value, tuple):
            out[key] = list(value)
        else:
            out[key] = value
    return out


def deserialize_task_overrides(raw: object) -> dict[str, object]:
    """反序列化 task_overrides（容错：跳过类型不符字段）。

    ``rules_paths``/``temp_rules_paths``/``disabled_temp_rules_paths`` 的 list
    转为 tuple，类型不符字段跳过并 warning。

    :param raw: 从 JSON 解析得到的原始数据
    :return: 类型安全的 task_overrides 字典
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    for key, value in raw.items():
        if key not in TASK_OVERRIDE_KEYS:
            logger.warning("反序列化 task_overrides：跳过未知字段 %s", key)
            continue
        if key in ("rules_paths", "temp_rules_paths", "disabled_temp_rules_paths"):
            if isinstance(value, list) and all(isinstance(x, str) for x in value):
                out[key] = tuple(value)
            else:
                logger.warning("task_overrides.%s 类型不符，跳过: %r", key, value)
        elif isinstance(value, TASK_OVERRIDE_KEYS[key]):
            out[key] = value
        else:
            logger.warning("task_overrides.%s 类型不符，跳过: %r", key, value)
    return out


def serialize_workspace(item: WorkspaceItem) -> dict[str, object]:
    """序列化单个工作区为 dict（供 JSON 持久化）。

    持久化「定义字段」与「上次扫描状态」（status_text/counts/summary），
    使重启后仍能展示上次扫描结果状态。

    :param item: 工作区数据对象
    :return: 可 JSON 序列化的 dict
    """
    return {
        "id": item.workspace_id,
        "name": item.name,
        "mode": item.mode_str,
        "target": item.target,
        "rules_paths": list(item.rules_paths),
        "use_builtin": item.use_builtin,
        # 持久化上次扫描状态，重启后仍能展示
        "status_text": item.status_text,
        "matched_count": item.matched_count,
        "passed_count": item.passed_count,
        "skipped_count": item.skipped_count,
        "error_count": item.error_count,
        "last_summary": item.last_summary,
        # 持久化收集到的符合文件类型文件数
        "collected_count": item.collected_count,
        # 持久化任务级配置覆盖
        "task_overrides": serialize_task_overrides(item.task_overrides),
        # 持久化最近活动时间，用于列表排序（最新活动在最上方）
        "last_activity_time": item.last_activity_time,
    }


def serialize_workspaces(items: list[WorkspaceItem]) -> dict[str, object]:
    """序列化工作区列表为持久化 payload。

    :param items: 工作区数据对象列表
    :return: 含 ``version`` 与 ``workspaces`` 字段的 payload dict
    """
    return {
        "version": PERSIST_VERSION,
        "workspaces": [serialize_workspace(item) for item in items],
    }


def load_persisted_workspaces(persist_file: Path) -> list[dict[str, object]]:
    """从 JSON 文件加载工作区 dict 列表。

    文件不存在/解析失败/版本不兼容时返回空列表（首次启动或文件损坏）。

    :param persist_file: 持久化 JSON 文件路径
    :return: 工作区 dict 列表（每个 dict 为 :func:`serialize_workspace` 的输出）
    """
    if not persist_file.exists():
        return []
    try:
        payload = json.loads(persist_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("工作区持久化文件读取失败: %s", exc)
        return []
    if not isinstance(payload, dict) or payload.get("version") != PERSIST_VERSION:
        logger.warning(
            "工作区持久化版本不兼容，跳过: %s", payload.get("version") if isinstance(payload, dict) else None
        )
        return []
    workspaces = payload.get("workspaces", [])
    if not isinstance(workspaces, list):
        return []
    return [ws for ws in workspaces if isinstance(ws, dict)]


def save_persisted_workspaces(persist_file: Path, payload: dict[str, object], config_dir: Path) -> None:
    """保存工作区 payload 到 JSON 文件。

    :param persist_file: 持久化 JSON 文件路径
    :param payload: :func:`serialize_workspaces` 输出的 payload dict
    :param config_dir: 配置目录（用于 ``mkdir(parents=True, exist_ok=True)``）
    """
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        persist_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("工作区持久化失败: %s", exc)
