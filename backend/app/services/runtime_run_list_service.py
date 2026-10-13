"""Phase 7 Step 3 — 统一 Runtime Run **列表**查询服务（Agent + Workflow；只读）。

定位（在 Phase 7 Step 2 统一详情之上补列表能力）：

```text
API 层（runtime_read.py）
      ↓  授权 project_id（服务端认证配置——本服务**只接受**它）
RuntimeRunListService（本模块）
      ↓  按域分发（**不跨域合并**——两种 Runtime 分别查询）
AgentRunRepository.list_by_project / count_by_project
WorkflowRunRepository.list_by_project / count_by_project
      ↓
PostgreSQL（DB 层 ``WHERE project_id = …`` 过滤）
```

纪律：

* **只读**：不调用任何 Runtime 执行入口（execute / resume / approve /
  cancel / start / advance / request_cancel）；不写任何表；
* **项目隔离在 DB 层**：``project_id`` 过滤由 SQL 执行（仓储保证）——
  本服务绝不做"先查全部再 Python 过滤"；``project_id`` **只**来自
  调用方（API 层从服务端认证配置解析后传入）——**不**接受客户端参数；
* **分页 server-side 校验**：``limit`` 默认 50 / 上限 100（沿用 Agent
  Read 既有规范 ``AGENT_READ_DEFAULT_LIMIT`` / ``AGENT_READ_MAX_LIMIT``），
  ``offset >= 0``；非法 ⇒ ``ValueError``（API 层映射 400）；
* **稳定排序由仓储保证**：``started_at DESC, <run_id> DESC``（唯一 ID
  tiebreaker）——本服务不重排、不修正时钟；
* **total 可靠**：与列表同一过滤条件的真实 COUNT（不可靠则不返回——
  本实现两域均可可靠计数）；
* **状态不映射**：Agent / Workflow 状态词汇原样透传（同一字符串在两域
  含义可能不同——不归一化）；
* **失败显式**：仓储故障 ⇒ :class:`RuntimeRunListError`（API 层映射
  502）——**不**返回空列表伪装成功。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from backend.app.db.agent_run_repository import (
    AgentRunRepository,
    AgentRunRepositoryError,
)
from backend.app.db.workflow_run_repository import (
    WorkflowRunRepository,
    WorkflowRunRepositoryError,
)
from backend.app.dto.agent_read import (
    AGENT_READ_DEFAULT_LIMIT,
    AGENT_READ_MAX_LIMIT,
)

__all__ = [
    "RUNTIME_TYPE_AGENT",
    "RUNTIME_TYPE_WORKFLOW",
    "RuntimeRunListItem",
    "RuntimeRunListPage",
    "RuntimeRunListError",
    "RuntimeRunListService",
]

#: 运行时域标识（与统一详情 API / DTO discriminator 一致）。
RUNTIME_TYPE_AGENT: Final[str] = "agent"
RUNTIME_TYPE_WORKFLOW: Final[str] = "workflow"


@dataclass(frozen=True)
class RuntimeRunListItem:
    """列表项（**概要**字段；两域真实共有——不伪造缺失字段）。

    与 ``RuntimeRunSummaryResponse`` 字段一一对应；**不含** step / node /
    approval / input / 任何明细（列表响应保持轻量；明细走统一详情 API）。
    """

    runtime_type: str
    run_id: str
    project_id: str
    status: str
    started_at: datetime | None


@dataclass(frozen=True)
class RuntimeRunListPage:
    """一页列表结果（含可靠 ``total``）。"""

    items: tuple[RuntimeRunListItem, ...]
    limit: int
    offset: int
    total: int


class RuntimeRunListError(Exception):
    """列表查询失败（仓储故障；API 层映射 502——不伪装空列表）。"""


def _validate_pagination(limit: int | None, offset: int) -> tuple[int, int]:
    """分页参数 server-side 校验（默认 50 / 上限 100 / offset >= 0）。

    Raises:
        ValueError: limit / offset 非法（API 层映射 400）。
    """
    if limit is None:
        limit = AGENT_READ_DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit 必须是 int")
    if limit < 1 or limit > AGENT_READ_MAX_LIMIT:
        raise ValueError(
            f"limit 必须在 1..{AGENT_READ_MAX_LIMIT} 之间（当前: {limit}）"
        )
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError("offset 必须是 int")
    if offset < 0:
        raise ValueError(f"offset 必须 >= 0（当前: {offset}）")
    return limit, offset


class RuntimeRunListService:
    """按授权项目查询 Agent / Workflow Run 列表（**只读**；两域独立）。"""

    def __init__(
        self,
        *,
        agent_run_repository: AgentRunRepository,
        workflow_run_repository: WorkflowRunRepository,
    ) -> None:
        self._agent_runs = agent_run_repository
        self._workflow_runs = workflow_run_repository

    # ---------- Agent 域 ----------

    def list_agent_runs(
        self, *, project_id: str, limit: int | None = None, offset: int = 0
    ) -> RuntimeRunListPage:
        """查询授权项目范围内的 Agent Run 列表（分页 + 可靠 total）。"""
        validated_limit, validated_offset = _validate_pagination(limit, offset)
        try:
            records = self._agent_runs.list_by_project(
                project_id=project_id,
                limit=validated_limit,
                offset=validated_offset,
            )
            total = self._agent_runs.count_by_project(project_id=project_id)
        except (ValueError, AgentRunRepositoryError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise RuntimeRunListError(
                f"Agent Run 列表查询失败: {type(exc).__name__}"
            ) from exc
        return RuntimeRunListPage(
            items=tuple(
                RuntimeRunListItem(
                    runtime_type=RUNTIME_TYPE_AGENT,
                    run_id=record.run_id,
                    project_id=record.project_id or project_id,
                    status=record.status,
                    started_at=record.started_at,
                )
                for record in records
            ),
            limit=validated_limit,
            offset=validated_offset,
            total=total,
        )

    # ---------- Workflow 域 ----------

    def list_workflow_runs(
        self, *, project_id: str, limit: int | None = None, offset: int = 0
    ) -> RuntimeRunListPage:
        """查询授权项目范围内的 Workflow Run 列表（分页 + 可靠 total）。"""
        validated_limit, validated_offset = _validate_pagination(limit, offset)
        try:
            rows = self._workflow_runs.list_by_project(
                project_id=project_id,
                limit=validated_limit,
                offset=validated_offset,
            )
            total = self._workflow_runs.count_by_project(project_id=project_id)
        except (ValueError, WorkflowRunRepositoryError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise RuntimeRunListError(
                f"Workflow Run 列表查询失败: {type(exc).__name__}"
            ) from exc
        return RuntimeRunListPage(
            items=tuple(
                RuntimeRunListItem(
                    runtime_type=RUNTIME_TYPE_WORKFLOW,
                    run_id=row.workflow_run_id,
                    project_id=row.project_id,
                    status=row.status,
                    started_at=row.started_at,
                )
                for row in rows
            ),
            limit=validated_limit,
            offset=validated_offset,
            total=total,
        )
