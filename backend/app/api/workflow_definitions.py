"""Workflow 定义目录 API（Phase 7 Step 7）—— **只读 + 认证** HTTP 边界。

```text
HTTP GET /api/workflow-definitions      （已注册定义目录：name / version /
                                          start_node_id / input_fields / nodes 概要）
        ↓  X-API-Key（复用 Workflow **读取**域认证；恒定时间比较）
        ↓  授权 project_id（**仅**来自服务端配置——客户端零输入）
WorkflowRegistry（服务器端静态定义；Composition Root = command_service
                 的同一注册表实例——与命令 API 看到的定义**同源**）
```

为什么需要（**Step 7 交付的下一项核心能力**）：

    Workflow 定义此前**未在生产注册表注册** ⇒ ``POST /api/workflow-runs``
    对任何名称都是 404，命令 API 事实上不可用。Step 7 起生产目录
    （``backend.app.workflow.catalog``）经 Composition Root 惰性注册；
    本端点提供**只读发现**：调用方在 start 之前即可获知
    「有哪些 workflow、其版本、起始节点、start input 需要哪些字段」。

边界（**只读**）：

    * **Query only**：不调用 Runtime 的 start / advance / request_cancel /
      submit_approval_decision；不调用 Tool / LLM / Orchestrator；
      不创建 / 修改 / 删除任何定义（定义注册仅由服务器端代码
      ``register_workflow_definition`` 完成）；
    * 不接触 DB / ORM / Session / Repository（定义是服务器端静态对象）；
    * **不**暴露定义内部：AI 模板 / TOOL 参数映射 / 审批展示文案 /
      retry 策略 / 边（``next_node_id``）均不返回；只返回启动前必需的
      目录信息（name / version / start_node_id / input_fields / 节点类型概要）；
    * 响应只由 ``backend.app.dto.workflow_read_api`` 的 Pydantic DTO 组成
      （``response_model`` 同时充当字段过滤边界）；error detail 使用固定
      文案（**不**含异常正文 / 内部路径）。

认证与授权（复用 Phase 6 Step 7.2 既有边界，**不新增认证机制**）：

    * 挂载 :func:`backend.app.api.workflow_read_auth.require_workflow_read_scope`
      ——与 ``/api/workflow-runs`` 读面**同一凭据 / 同一 fail-closed 语义**；
    * 未配置 ⇒ 503（绝不匿名放行）；缺失 / 错误凭据 ⇒ 401（固定文案）；
    * 本端点**不**接受任何客户端作用域参数（无 project_id 查询参数 /
      无请求体；未知 query 参数被忽略）——定义是服务器端静态目录，
      响应内容与项目无关（不因客户端输入变化）。

错误契约：

```text
200  定义目录（可能为空列表——空 ≠ 错误）
401  缺少或无效的 X-API-Key（固定文案）
405  未知写方法（如 POST）
503  服务端认证 / 作用域未配置（fail closed）
500  其它未预期错误（固定文案；不暴露 traceback / 内部路径）
```
"""
from __future__ import annotations

import logging
import re
from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Security, status

from backend.app.api.workflow_read_auth import (
    WorkflowReadScope,
    require_workflow_read_scope,
)
from backend.app.dto.workflow_read_api import (
    WorkflowDefinitionCatalogResponse,
    WorkflowDefinitionNodeResponse,
    WorkflowDefinitionResponse,
)
from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowRegistry,
)

logger = logging.getLogger(__name__)
router = APIRouter()

__all__ = [
    "router",
    "get_workflow_definition_registry",
    "collect_input_fields",
]

#: 固定错误文案（**不**回传异常正文 / 内部路径 / 配置值）。
_UNAVAILABLE_DETAIL: Final[str] = "Workflow 定义目录不可用"

#: 受限引用根（与域层 ``_REFERENCE_ROOTS`` 的 ``input`` 一致）。
_INPUT_REFERENCE_PREFIX: Final[str] = "input."

#: AI 模板占位符提取（与域层校验同一语法；此处只读提取）。
_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{([^{}]*)\}")

#: 两个域共用的认证错误声明（文案与域端点一致）。
_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {
        "description": (
            "缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）"
        )
    },
    503: {"description": "服务端认证 / 作用域未配置（fail closed）"},
}


def get_workflow_definition_registry() -> WorkflowRegistry:
    """Composition Root：生产定义注册表（与命令 API **同一实例**）。

    延迟 import：保持 API 层与 runtime 装配的弱耦合（沿既有惯例）；
    测试以 monkeypatch 替换本函数注入自有注册表。
    """
    from backend.app.workflow_runtime.command_service import (
        get_workflow_registry,
    )

    return get_workflow_registry()


# ============================================================
# 只读推导（纯函数，零 IO）
# ============================================================


def _collect_input_fields(target: set[str], reference: object) -> None:
    """从一条受限引用中提取 ``input.<path>`` 的顶层字段（否则忽略）。"""
    if not isinstance(reference, str) or not reference.startswith(
        _INPUT_REFERENCE_PREFIX
    ):
        return
    remainder = reference[len(_INPUT_REFERENCE_PREFIX) :]
    if not remainder:
        return
    target.add(remainder.split(".", 1)[0])


def collect_input_fields(definition: WorkflowDefinition) -> tuple[str, ...]:
    """推导 start input JSON 需要的顶层字段（排序去重；纯函数）。

    来源（**只读**；与 Runtime 的参数解析 / 模板渲染语义一致）：

    * TOOL 节点的 ``arguments_map`` 取值（``input.<path>`` ⇒ 顶层字段）；
    * AI 节点的 ``question_template`` 占位符（``{input.<path>}`` ⇒ 顶层字段）。

    CONDITION 的 ``condition.source`` **不算**输入字段（它作用于直接上游
    节点 output，不是 start input）。
    """
    fields: set[str] = set()
    for node in definition.nodes:
        if node.node_type == NODE_TYPE_TOOL:
            for reference in node.arguments_map.values():
                _collect_input_fields(fields, reference)
        elif node.node_type == NODE_TYPE_AI:
            template = node.question_template or ""
            for placeholder in _PLACEHOLDER_PATTERN.findall(template):
                _collect_input_fields(fields, placeholder)
    return tuple(sorted(fields))


def _to_api_definition(definition: WorkflowDefinition) -> WorkflowDefinitionResponse:
    """定义 → 目录 DTO（**显式逐字段**；不返回模板 / 参数映射 / 审批文案）。"""
    return WorkflowDefinitionResponse(
        name=definition.name,
        version=definition.version,
        start_node_id=definition.start_node_id,
        input_fields=list(collect_input_fields(definition)),
        nodes=[
            WorkflowDefinitionNodeResponse(
                node_id=node.node_id, node_type=node.node_type
            )
            for node in definition.nodes
        ],
    )


# ============================================================
# 端点
# ============================================================


@router.get(
    "/workflow-definitions",
    response_model=WorkflowDefinitionCatalogResponse,
    responses={
        200: {"description": "已注册 Workflow 定义目录（只读；可能为空）"},
        500: {"description": "定义目录读取未预期失败（固定文案）"},
        **_AUTH_RESPONSES,
    },
)
async def list_workflow_definitions(
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> WorkflowDefinitionCatalogResponse:
    """列出服务器端已注册的 Workflow 定义（**只读**；认证先于任何读取）。

    与 ``POST /api/workflow-runs`` 使用**同一**注册表实例 ⇒ 目录中出现的
    ``name`` 必然可被 start 解析（定义不存在 ⇒ 命令 API 404，不在此处伪造）。
    """
    try:
        registry = get_workflow_definition_registry()
        definitions = registry.list_definitions()
    except Exception as exc:  # noqa: BLE001 —— 固定文案，不暴露内部细节
        logger.error(
            "workflow definition catalog unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        ) from None
    return WorkflowDefinitionCatalogResponse(
        items=[_to_api_definition(definition) for definition in definitions]
    )
