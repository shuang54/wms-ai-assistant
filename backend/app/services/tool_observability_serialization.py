"""Tool Observability Serialization Boundary（Phase 3.11 Step 24）。

Read Model → **JSON-safe primitive dict** 的稳定转换边界：

    ToolExecutionSnapshot（Step 22，frozen）
        ↓ snapshot_to_dict()（显式逐字段映射）
    dict[str, str | int | float | bool | None]
        ↓ json.dumps()（调用方；本模块不做 IO）

    ToolExecutionMetricsSnapshot（Step 17，frozen）
        ↓ metrics_to_dict()（显式逐字段映射）
    dict[str, str | int | float | bool | None]

设计约束（§五 ~ §十三）：

    * **显式字段映射**：不使用 ``vars()`` / ``dataclasses.asdict()`` /
      ``__dict__``（§八）——未来 DTO 增加内部字段时不会泄漏到对外结构；
      映射与 DTO 字段名**逐一同名**（不新增 / 不删除 / 不重命名 / 不改语义）；
    * ``datetime`` → **ISO 8601 字符串**（``.isoformat()``，沿用项目既有
      约定：``backend/app/cli/knowledge.py::_iso`` 与
      ``text_to_sql_*`` 的 ``datetime.now(timezone.utc).isoformat()``）；
      **保留 tz offset**；naive datetime → ``ValueError``（不静默丢时区、
      不转本地时间、不转 epoch number）；
    * ``None`` 语义**原样保留**（§七）：Metrics 空数据集的
      ``success_rate`` / ``failure_rate`` / ``average_duration_ms`` /
      ``max_duration_ms`` 仍为 ``None``，**绝不**写成 ``0``；
    * **纯函数**：无 IO / 无 DB / 无 LLM / 无 Tool 执行 / 无 HTTP /
      无网络 / 无当前时间 / 无随机数 / 无 UUID；deterministic（§十一）；
    * **不修改入参**（§十二）：返回新 dict，不改 DTO、不改 Record、
      不改 Collector；返回值本身是可变 dict（调用方可自由使用），
      但**不引用**任何 DTO / Record / Collector 对象（无嵌套对象引用）；
    * **无反序列化**（§十三）：本阶段只做 DTO → dict（→ ``json.dumps``），
      不提供 dict → DTO（避免过度设计）。

明确不做（§十八）：

    FastAPI endpoint / Pydantic Response Model / 数据库持久化 / Redis /
    Kafka / Prometheus / OpenTelemetry / Dashboard / 鉴权 / 分页 /
    过滤 DSL / 排序 / 时间范围查询 / Audit 持久化。

    可序列化 **≠** 应当暴露：本模块不接任何 HTTP 层。
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from typing import Any, Final

from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

__all__ = [
    "SERIALIZED_SNAPSHOT_FIELDS",
    "SERIALIZED_METRICS_FIELDS",
    "snapshot_to_dict",
    "metrics_to_dict",
]


#: 序列化字段契约（与 DTO 字段**逐一同名**；顺序即输出顺序）
SERIALIZED_SNAPSHOT_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "round",
    "tool_name",
    "started_at",
    "finished_at",
    "duration_ms",
    "success",
    "project_id",
    "tool_call_id",
    "error_code",
    "error_type",
)

#: Metrics 序列化字段契约（8 项；不改语义）
SERIALIZED_METRICS_FIELDS: Final[tuple[str, ...]] = (
    "total_count",
    "success_count",
    "failure_count",
    "success_rate",
    "failure_rate",
    "total_duration_ms",
    "average_duration_ms",
    "max_duration_ms",
)

#: JSON 允许的原始类型（``bool`` 是 ``int`` 子类，无需单列）
_JSON_PRIMITIVE_TYPES: Final[tuple[type, ...]] = (
    str,
    int,
    float,
    bool,
    type(None),
)


# ============================================================
# 内部：字段 / 值转换
# ============================================================

def _assert_field_contract(obj: Any, expected: tuple[str, ...]) -> None:
    """DTO 字段必须与序列化契约**完全一致**（缺失 / 多余 → 明确失败）。

    防止未来 DTO 扩展字段时，对外结构**静默**漂移（§八）。
    """
    actual = {f.name for f in fields(obj)}
    if actual != set(expected):
        raise ValueError(
            f"{type(obj).__name__} 字段与序列化契约不一致: "
            f"{sorted(actual)}（契约: {sorted(expected)}）"
        )


def _iso8601(name: str, value: datetime) -> str:
    """``datetime`` → ISO 8601 字符串（保留 tz offset；naive → 报错）。

    沿用项目既有约定（``datetime.isoformat()``）；**不**转换时区、
    **不**转成本地时间、**不**转成 epoch number（§五）。
    """
    if not isinstance(value, datetime):
        raise TypeError(
            f"字段 {name!r} 必须是 datetime（got {type(value).__name__}）"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"字段 {name!r} 必须是 timezone-aware datetime"
            "（naive datetime 会导致时区丢失）"
        )
    return value.isoformat()


def _primitive(name: str, value: Any) -> Any:
    """值必须是 JSON 原始类型（否则明确失败，不静默转换）。"""
    if not isinstance(value, _JSON_PRIMITIVE_TYPES):
        raise TypeError(
            f"字段 {name!r} 必须是 JSON 原始类型"
            f"（str/int/float/bool/None；got {type(value).__name__}）"
        )
    return value


# ============================================================
# 对外 Serialization Contract
# ============================================================

def snapshot_to_dict(snapshot: ToolExecutionSnapshot) -> dict[str, Any]:
    """``ToolExecutionSnapshot`` → JSON-safe ``dict``（显式逐字段映射）。

    Args:
        snapshot: Step 22 的对外 Read Model（frozen）。

    Returns:
        ``dict``：11 个字段，**值全部为 JSON 原始类型**；
        ``started_at`` / ``finished_at`` 为 ISO 8601 字符串（含 tz offset）。
        不引用 ``snapshot`` / Record / Collector（无嵌套对象）。

    Raises:
        TypeError:  ``snapshot`` 不是 ``ToolExecutionSnapshot``；
                    或字段值不是 JSON 原始类型。
        ValueError: DTO 字段与序列化契约不一致；或时间戳为 naive datetime。

    Note:
        纯函数（无 IO / 无 DB / 无 LLM / 无 Tool 执行 / 无当前时间）；
        deterministic；**不**修改 ``snapshot``（§十二）。
    """
    if not isinstance(snapshot, ToolExecutionSnapshot):
        raise TypeError(
            "snapshot 必须是 ToolExecutionSnapshot"
            f"（got {type(snapshot).__name__}）"
        )
    _assert_field_contract(snapshot, SERIALIZED_SNAPSHOT_FIELDS)
    return {
        "request_id": _primitive("request_id", snapshot.request_id),
        "round": _primitive("round", snapshot.round),
        "tool_name": _primitive("tool_name", snapshot.tool_name),
        "started_at": _iso8601("started_at", snapshot.started_at),
        "finished_at": _iso8601("finished_at", snapshot.finished_at),
        "duration_ms": _primitive("duration_ms", snapshot.duration_ms),
        "success": _primitive("success", snapshot.success),
        "project_id": _primitive("project_id", snapshot.project_id),
        "tool_call_id": _primitive("tool_call_id", snapshot.tool_call_id),
        "error_code": _primitive("error_code", snapshot.error_code),
        "error_type": _primitive("error_type", snapshot.error_type),
    }


def metrics_to_dict(
    metrics: ToolExecutionMetricsSnapshot,
) -> dict[str, Any]:
    """``ToolExecutionMetricsSnapshot`` → JSON-safe ``dict``（显式映射）。

    ``None`` 语义**原样保留**（§七）：空数据集的
    ``success_rate`` / ``failure_rate`` / ``average_duration_ms`` /
    ``max_duration_ms`` 仍为 ``None``（**不**写成 ``0``）；
    ``total_duration_ms`` 仍为 ``0.0``。

    Args:
        metrics: Step 17 的 Metrics 快照（frozen）。

    Returns:
        ``dict``：8 个字段，值全部为 JSON 原始类型（``float | None``）。

    Raises:
        TypeError:  ``metrics`` 不是 ``ToolExecutionMetricsSnapshot``；
                    或字段值不是 JSON 原始类型。
        ValueError: DTO 字段与序列化契约不一致。

    Note:
        纯函数；deterministic；**不**修改 ``metrics``。
    """
    if not isinstance(metrics, ToolExecutionMetricsSnapshot):
        raise TypeError(
            "metrics 必须是 ToolExecutionMetricsSnapshot"
            f"（got {type(metrics).__name__}）"
        )
    _assert_field_contract(metrics, SERIALIZED_METRICS_FIELDS)
    return {
        "total_count": _primitive("total_count", metrics.total_count),
        "success_count": _primitive("success_count", metrics.success_count),
        "failure_count": _primitive("failure_count", metrics.failure_count),
        "success_rate": _primitive("success_rate", metrics.success_rate),
        "failure_rate": _primitive("failure_rate", metrics.failure_rate),
        "total_duration_ms": _primitive(
            "total_duration_ms", metrics.total_duration_ms
        ),
        "average_duration_ms": _primitive(
            "average_duration_ms", metrics.average_duration_ms
        ),
        "max_duration_ms": _primitive(
            "max_duration_ms", metrics.max_duration_ms
        ),
    }
