"""Phase 7 Step 2 — 统一 Runtime 读取入口的**认证解析**（按域路由复用）。

设计原则（任务书 §五）：**不新增认证机制、不放宽任何权限**。本模块不实现
自己的凭据校验 / 作用域解析，而是按 ``runtime_type`` **直接调用**两套既有
只读域的认证依赖：

```text
GET /api/runtime-runs/agent/{run_id}    → require_agent_read_scope
GET /api/runtime-runs/workflow/{run_id} → require_workflow_read_scope
```

⇒ 503（未配置 fail-closed）/ 401（缺失或错误凭据）/ 恒定时间比较 /
授权 ``project_id`` 仅来自服务端配置 / 凭据不进日志——全部语义**逐字
沿用**现有两域实现（``agent_read_auth`` / ``workflow_read_auth``）。

按需解析语义（对应任务书 §五.4"认证未配置时，继续遵循对应 API 的
fail-closed 行为"）：

* 请求 ``agent`` 运行 ⇒ 只解析 Agent 域（Workflow 域未配置**不影响**）；
* 请求 ``workflow`` 运行 ⇒ 只解析 Workflow 域；
* 未涉及的域**不要求**配置——不会因为另一域缺配置而拒绝合法请求，
  也不会因为另一域已配置而放宽本域边界。

边界：

* 授权作用域**绝不**来自客户端 Query / 普通请求头 / 请求体；
* 本模块只 import 两域 auth 模块 + stdlib + fastapi（与两域 auth 的
  AST 边界精神一致；不触达 db / services / tools / llm / runtime）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from backend.app.api.agent_read_auth import (
    api_key_header,
    require_agent_read_scope,
)
from backend.app.api.workflow_read_auth import require_workflow_read_scope

logger = logging.getLogger(__name__)

__all__ = [
    "RUNTIME_TYPE_AGENT",
    "RUNTIME_TYPE_WORKFLOW",
    "RUNTIME_TYPES",
    "api_key_header",
    "RuntimeReadScope",
    "resolve_runtime_read_scope",
]

#: 运行时域标识（路径参数白名单；与 DTO discriminator 一致）。
RUNTIME_TYPE_AGENT: Final[str] = "agent"
RUNTIME_TYPE_WORKFLOW: Final[str] = "workflow"
RUNTIME_TYPES: Final[tuple[str, ...]] = (RUNTIME_TYPE_AGENT, RUNTIME_TYPE_WORKFLOW)


@dataclass(frozen=True)
class RuntimeReadScope:
    """已解析的统一读取作用域（域标识 + 该域服务端授权 ``project_id``）。

    **不**由客户端构造——仅由 :func:`resolve_runtime_read_scope` 在对应域
    凭据校验通过后产出。
    """

    runtime_type: str
    project_id: str


async def resolve_runtime_read_scope(
    *,
    runtime_type: str,
    api_key: str | None,
) -> RuntimeReadScope:
    """按 ``runtime_type`` 解析对应域认证作用域（未涉及的域不要求配置）。

    Args:
        runtime_type: ``"agent"`` 或 ``"workflow"``（调用方先做白名单校验）。
        api_key: 客户端 ``X-API-Key`` 头原值（可为 ``None`` ⇒ 401）。

    Returns:
        RuntimeReadScope：域标识 + 该域授权 ``project_id``。

    Raises:
        HTTPException 503: 对应域未配置（fail closed；**沿用该域文案**）。
        HTTPException 401: 缺失或错误凭据（**沿用该域文案**；不泄露资源存在性）。
        ValueError: ``runtime_type`` 不在白名单（调用方应先校验）。

    Note:
        未知 ``runtime_type`` 在本函数内**拒绝**（不落到默认域、不静默
        放行）——纵深防御；正常调用方已在 API 层先行拦截。
    """
    if runtime_type == RUNTIME_TYPE_AGENT:
        scope = await require_agent_read_scope(api_key)
        return RuntimeReadScope(
            runtime_type=RUNTIME_TYPE_AGENT, project_id=scope.project_id
        )
    if runtime_type == RUNTIME_TYPE_WORKFLOW:
        scope = await require_workflow_read_scope(api_key)
        return RuntimeReadScope(
            runtime_type=RUNTIME_TYPE_WORKFLOW, project_id=scope.project_id
        )
    raise ValueError(f"runtime_type 不在允许集合内: {runtime_type!r}")
