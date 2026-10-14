"""生产 Workflow **定义目录**（Phase 7 Step 7）。

背景（**为什么需要本模块**）：

    Phase 6 已交付 Workflow Domain（``WorkflowDefinition`` / ``WorkflowRegistry``）
    与 Runtime（factory / execution / runtime），Phase 7 Step 1 已交付 HTTP 命令
    API（start / advance / cancel / decision）。但**生产注册表此前为空** ——
    ``WorkflowRegistry()`` 零定义 ⇒ 任何 start 都是 404（fail-closed 设计）。
    本模块提供**服务器端静态定义目录**（唯一事实来源），由
    ``workflow_runtime.command_service.get_workflow_registry()`` 惰性注册；
    定义发现走只读 API ``GET /api/workflow-definitions``。

目录（当前 2 条；全部**只读**语义，无写操作节点）：

```text
inventory_check  v1   TOOL get_inventory → APPROVAL → 终态
    输入契约：{"material_code": "<物料编码>"}
    语义：真实 WMS 只读库存查询 + 人工确认（查询结果由审批人核对）。
    数据源：``get_inventory`` 的库存表（数据库外部业务表由部署提供；
            缺失 ⇒ 节点 fail-closed 失败，**不**伪造查询成功）。

answer_review    v1   AI（问题模板）→ APPROVAL → 终态
    输入契约：{"question": "<问题>"}
    语义：项目作用域内的 AI 回答 + 人工确认后关闭流程。
```

纪律：

* **纯域**：只 import ``workflow.definition``（零 IO / 零 DB / 零 LLM /
  零 services / 零 tools）——与 ``workflow`` 包边界一致；
* **不新增节点类型、不新增状态**：只用既有 AI / TOOL / APPROVAL
  （CONDITION 未使用——两个流程无需分支）；
* **fail-closed**：定义错误在构造期即抛出（DAG / 类型-字段一致性校验由
  域层强制）；
* 版本发布后冻结（Run 记录 version；Resume 按持久化 version 解析 ——
  改语义必须新增 version，不得原地修改）；
* 无敏感信息（无凭据 / 无连接串 / 无内部路径；审批文案为通用提示语）。
"""
from __future__ import annotations

from typing import Final

from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowNode,
)

__all__ = [
    "WORKFLOW_VERSION_V1",
    "WORKFLOW_NAME_INVENTORY_CHECK",
    "WORKFLOW_NAME_ANSWER_REVIEW",
    "DEFAULT_WORKFLOW_NAMES",
    "build_default_workflow_definitions",
]

#: 目录内定义的统一版本标识（发布后冻结；语义变更 ⇒ 新 version）。
WORKFLOW_VERSION_V1: Final[str] = "v1"

#: ``inventory_check``：物料库存查询 + 人工确认（只读 Tool）。
WORKFLOW_NAME_INVENTORY_CHECK: Final[str] = "inventory_check"

#: ``answer_review``：AI 回答 + 人工确认。
WORKFLOW_NAME_ANSWER_REVIEW: Final[str] = "answer_review"

#: 生产目录内的全部 workflow 名（顺序 = 注册顺序；只读常量）。
DEFAULT_WORKFLOW_NAMES: Final[tuple[str, ...]] = (
    WORKFLOW_NAME_INVENTORY_CHECK,
    WORKFLOW_NAME_ANSWER_REVIEW,
)

#: ``inventory_check`` 的输入字段（start input JSON 必须提供）。
_INVENTORY_CHECK_INPUT_FIELD: Final[str] = "material_code"

#: ``answer_review`` 的输入字段（start input JSON 必须提供）。
_ANSWER_REVIEW_INPUT_FIELD: Final[str] = "question"


def build_default_workflow_definitions() -> tuple[WorkflowDefinition, ...]:
    """构造生产目录的全部定义（纯函数；**每次返回独立实例**）。

    Returns:
        tuple[WorkflowDefinition, ...]：注册顺序 = ``DEFAULT_WORKFLOW_NAMES``。

    Note:
        冻结定义（``frozen=True``）；调用方可安全注册进
        :class:`~backend.app.workflow.definition.WorkflowRegistry`
        （重复 name ⇒ 域异常，不隐式替换）。
    """
    return (
        _inventory_check_definition(),
        _answer_review_definition(),
    )


# ============================================================
# 定义（逐字段显式；不做任何字符串拼接 / 动态构造）
# ============================================================


def _inventory_check_definition() -> WorkflowDefinition:
    """``inventory_check``：真实只读库存查询 → 人工确认（无自动裁决）。"""
    return WorkflowDefinition(
        name=WORKFLOW_NAME_INVENTORY_CHECK,
        version=WORKFLOW_VERSION_V1,
        start_node_id="query_stock",
        nodes=(
            WorkflowNode(
                node_id="query_stock",
                node_type=NODE_TYPE_TOOL,
                # 唯一执行的 Tool 必须是 Agent 侧同一只读能力（不新增 Tool）。
                tool_name="get_inventory",
                # ``material_code`` 来自 start input（受限引用；不做任意表达式）。
                arguments_map={
                    _INVENTORY_CHECK_INPUT_FIELD: (
                        f"input.{_INVENTORY_CHECK_INPUT_FIELD}"
                    )
                },
                next_node_id="confirm_stock",
            ),
            WorkflowNode(
                node_id="confirm_stock",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请核对库存查询结果（物料编码 / 数量）是否与实际情况一致。",
            ),
        ),
    )


def _answer_review_definition() -> WorkflowDefinition:
    """``answer_review``：项目作用域 AI 回答 → 人工确认（无自动裁决）。"""
    return WorkflowDefinition(
        name=WORKFLOW_NAME_ANSWER_REVIEW,
        version=WORKFLOW_VERSION_V1,
        start_node_id="answer",
        nodes=(
            WorkflowNode(
                node_id="answer",
                node_type=NODE_TYPE_AI,
                # 受限占位符：只允许 {input.<path>} / {previous.<path>}。
                question_template=(
                    f"请回答以下问题：{{input.{_ANSWER_REVIEW_INPUT_FIELD}}}"
                ),
                next_node_id="review_answer",
            ),
            WorkflowNode(
                node_id="review_answer",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请确认 AI 回答内容是否可以使用。",
            ),
        ),
    )
