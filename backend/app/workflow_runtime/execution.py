"""Workflow Runtime 单节点执行器（Phase 6 Step 3 · S3；ADR-01 §6/§10/§11/§14）。

职责（**仅执行一个节点**）：
    * CONDITION —— 复用 :func:`binding.evaluate_condition`（不复制求值逻辑；
      无 DB / 无外部服务）；
    * TOOL —— 经 :class:`ToolExecutionPort`（**唯一执行边界**）；
      ``arguments`` 由 :func:`binding.resolve_arguments_map` 生成；
      **先解析参数、后调用 Tool**（映射失败 ⇒ 零调用）；
    * AI —— 经 :class:`OrchestratorPort`（严格使用 ports 已有签名）；
      输出按 OQ-4 投影；Agent 进入 WAITING 时保留恢复所需
      ``agent_run_id`` / ``agent_approval_id``（**不**自动裁决 / **不**递归 /
      **不**多步 Tool Calling）；
    * APPROVAL —— 通过 :class:`WorkflowApprovalStore` 创建 PENDING 记录，
      返回明确的 WAITING_APPROVAL；**不**自动批准/拒绝；**不**修改 Run 状态。

**不负责**（S4 Runtime 职责）：下一节点决策 / Run 的 CAS 与状态推进 /
retry / 取消 / 恢复 / attempt 编号。本层**不**重试、**不**把失败转换为
可重试错误、**不**对 UNKNOWN attempt 发起第二次执行。

结果（四态，经 :class:`NodeExecutionOutcome` 明确表达）：
```text
SUCCEEDED（output 为 JSON 文本）/ WAITING_APPROVAL（workflow 审批 或
嵌套 Agent）/ FAILED（error_code 为 WF_ERROR_* 稳定码）
```
所有成功输出均由 :func:`serialize_json_value` 编码（域 JSON-safe 边界：
≤ 32 KiB / 深度 8 / 拒绝任意对象）；失败**不**伪造成功输出。

异常纪律：业务失败**不抛异常**——一律归一为 ``NodeExecutionOutcome(FAILED)``
（含端口异常 / 绑定失败 / 输出边界失败）；仅**契约违反**（编程错误：未知
node_type / 必填字段缺失 / 参数非法）抛 :class:`ExecutionError`（fail loud）。

边界（AST 测试锁定）：仅 import stdlib + ``backend.app.workflow``（域）+
同包 ``ports`` / ``binding``；不 import db / services / tools / agent /
api / llm / projects。``NodeExecutionOutcome`` 暂居本模块——是否上移
``ports.py`` 见 S3 报告 §8（未确认前不修改 ports）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    WorkflowNode,
)
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_CODES,
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_OUTPUT_UNAVAILABLE,
    WorkflowOutputError,
)
from backend.app.workflow.models import serialize_json_value
from backend.app.workflow.states import (
    APPROVAL_STATUS_PENDING,
    NODE_STATUS_FAILED,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
)
from backend.app.workflow_runtime.binding import (
    evaluate_condition,
    render_template,
    resolve_arguments_map,
)
from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    ApprovalIdFactory,
    OrchestratorProvider,
    ToolCallContext,
    ToolExecutionProvider,
    WorkflowApprovalStore,
)

__all__ = [
    "ExecutionError",
    "NodeExecutionOutcome",
    "NodeExecutor",
]

#: 执行器可返回的 attempt 状态（RUNNING 由 Runtime 在调用前持久化——不在本层）。
_EXECUTION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        NODE_STATUS_SUCCEEDED,
        NODE_STATUS_WAITING_APPROVAL,
        NODE_STATUS_FAILED,
    }
)


class ExecutionError(Exception):
    """执行器**契约违反**（编程错误：未知 node_type / 必填字段缺失 / 参数非法）。

    业务失败不抛本异常——一律归一为 ``NodeExecutionOutcome(status=FAILED,
    error_code=...)``；本异常只表示调用方给的契约不成立（fail loud）。
    """


@dataclass(frozen=True)
class NodeExecutionOutcome:
    """单节点执行结果（frozen；S4 Runtime 消费）。

    Attributes:
        status:     ``SUCCEEDED`` / ``WAITING_APPROVAL`` / ``FAILED``。
        output:     ``SUCCEEDED`` / ``WAITING_APPROVAL`` 时的 JSON 文本
                    （域 ``serialize_json_value`` 产物；FAILED 时必须 None）。
        error_code: ``FAILED`` 时的 ``WF_ERROR_*`` 稳定码（白名单校验；
                    非法码 / 伪造成功均被构造拒绝）。
        approval_id:        WORKFLOW APPROVAL 等待点的审批身份。
        agent_run_id:       AI 节点嵌套等待时，子 Agent 身份（恢复键）。
        agent_approval_id:  AI 节点嵌套等待时，子 Agent 审批身份。
    """

    status: str
    output: str | None = None
    error_code: str | None = None
    approval_id: str | None = None
    agent_run_id: str | None = None
    agent_approval_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, str)
            or self.status not in _EXECUTION_STATUSES
        ):
            raise ExecutionError(
                f"status 非法: {self.status!r}"
                f"（白名单: {sorted(_EXECUTION_STATUSES)}）"
            )
        if self.status == NODE_STATUS_FAILED:
            if (
                not isinstance(self.error_code, str)
                or self.error_code not in WF_ERROR_CODES
            ):
                raise ExecutionError(
                    f"FAILED 必须携带 WF_ERROR_* 稳定码（当前: {self.error_code!r}）"
                )
            if self.output is not None:
                raise ExecutionError("FAILED 不得携带 output（不伪造成功输出）")
        else:
            if not isinstance(self.output, str) or not self.output.strip():
                raise ExecutionError(
                    "SUCCEEDED / WAITING_APPROVAL 必须携带非空 JSON 文本 output"
                )
            if self.error_code is not None:
                raise ExecutionError("非 FAILED 结果不得携带 error_code")
        if self.status == NODE_STATUS_WAITING_APPROVAL:
            if not (self.approval_id or self.agent_run_id):
                raise ExecutionError(
                    "WAITING_APPROVAL 必须携带审批身份"
                    "（workflow approval_id 或嵌套 agent_run_id）"
                )


class NodeExecutor:
    """单节点执行器（无状态；端口全部构造注入——不触达 DB / 网络 / Registry）。"""

    def __init__(
        self,
        *,
        orchestrator_provider: OrchestratorProvider,
        tool_execution_provider: ToolExecutionProvider,
        approval_store: WorkflowApprovalStore,
        approval_id_factory: ApprovalIdFactory,
    ) -> None:
        self._orchestrator_provider = orchestrator_provider
        self._tool_execution_provider = tool_execution_provider
        self._approval_store = approval_store
        self._approval_id_factory = approval_id_factory

    async def execute_node(
        self,
        node: WorkflowNode,
        *,
        workflow_run_id: str,
        project_id: str,
        request_id: str,
        input: str | None,
        previous: str | None,
        attempt: int,
    ) -> NodeExecutionOutcome:
        """执行单个节点（不推进 Run；返回明确结果）。

        Args:
            node:           节点静态声明（域对象；本层不修改）。
            workflow_run_id / project_id / request_id: 服务器端上下文（由 Runtime 提供）。
            input / previous: JSON 文本（``RunRecord.input`` / 最近成功节点 output）。
            attempt:        当前 attempt 编号（**由 Runtime 决定**；本层只写进
                            ``tool_call_id`` 以稳定标识本次执行）。

        Returns:
            NodeExecutionOutcome（四态之一）。

        Raises:
            ExecutionError: 契约违反（编程错误）。业务失败一律归一为 outcome。
        """
        if not isinstance(node, WorkflowNode):
            raise ExecutionError(
                f"node 必须是 WorkflowNode（当前: {type(node).__name__}）"
            )
        _require_non_empty("workflow_run_id", workflow_run_id)
        _require_non_empty("project_id", project_id)
        _require_non_empty("request_id", request_id)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ExecutionError(f"attempt 必须是 >= 1 的 int（当前: {attempt!r}）")

        try:
            if node.node_type == NODE_TYPE_CONDITION:
                return self._execute_condition(node, previous=previous)
            if node.node_type == NODE_TYPE_TOOL:
                return await self._execute_tool(
                    node,
                    workflow_run_id=workflow_run_id,
                    project_id=project_id,
                    request_id=request_id,
                    input=input,
                    previous=previous,
                    attempt=attempt,
                )
            if node.node_type == NODE_TYPE_AI:
                return await self._execute_ai(
                    node,
                    project_id=project_id,
                    input=input,
                    previous=previous,
                )
            if node.node_type == NODE_TYPE_APPROVAL:
                return self._execute_approval(node, workflow_run_id=workflow_run_id)
        except ExecutionError:
            raise
        except Exception:
            # 外部执行边界：任何未归一异常 ⇒ 明确失败（不重试 / 不伪装 /
            # 不携带异常正文——错误码是唯一可见信息）。
            return _failed(WF_ERROR_NODE_EXECUTION_FAILURE)

        # 域白名单（NODE_TYPES）保证不可达；防御性 fail loud。
        raise ExecutionError(f"未知 node_type: {node.node_type!r}")

    # ------------------------------------------------------------
    # CONDITION
    # ------------------------------------------------------------

    def _execute_condition(
        self, node: WorkflowNode, *, previous: str | None
    ) -> NodeExecutionOutcome:
        """执行 CONDITION（复用 binding；输出 JSON 布尔 = 选中分支事实）。

        语义（冻结）：缺失路径 / null / 类型不匹配 ⇒ False（binding 内建）；
        载荷损坏（非法 JSON / 非 JSON-safe）⇒ binding 抛错 ⇒ 本层归一为
        ``WF_ERROR_NODE_EXECUTION_FAILURE``（数据完整性问题不伪装成 false）。
        """
        if node.condition is None:
            raise ExecutionError("CONDITION 节点缺少 condition（契约违反）")
        branch = evaluate_condition(node.condition, previous=previous)
        return NodeExecutionOutcome(
            status=NODE_STATUS_SUCCEEDED,
            output=serialize_json_value(branch),
        )

    # ------------------------------------------------------------
    # TOOL
    # ------------------------------------------------------------

    async def _execute_tool(
        self,
        node: WorkflowNode,
        *,
        workflow_run_id: str,
        project_id: str,
        request_id: str,
        input: str | None,
        previous: str | None,
        attempt: int,
    ) -> NodeExecutionOutcome:
        """执行 TOOL（先解析参数、后调用；失败归一；无自动重试）。"""
        if node.tool_name is None:
            raise ExecutionError("TOOL 节点缺少 tool_name（契约违反）")

        # 1) 参数映射（**必须**先于任何外部调用；失败 ⇒ 零调用）
        try:
            arguments = resolve_arguments_map(
                node.arguments_map, input=input, previous=previous
            )
        except Exception:
            return _failed(WF_ERROR_NODE_EXECUTION_FAILURE)

        # 2) 唯一执行边界（ToolExecutionPort；Tool Registry / Handler 不可见）
        context = ToolCallContext(
            request_id=request_id,
            round=1,
            project_id=project_id,
            tool_call_id=_build_tool_call_id(workflow_run_id, node.node_id, attempt),
        )
        port = self._tool_execution_provider(project_id)
        outcome = await port.execute(
            node.tool_name, arguments=arguments, context=context
        )
        if not outcome.success:
            # 工具报告失败（错误文本仅用于分类 / 日志——不落库、不进 outcome）
            return _failed(WF_ERROR_NODE_EXECUTION_FAILURE)

        # 3) 输出边界（OQ-9：不满足 JSON-safe ⇒ 明确失败，不截断）
        try:
            output = serialize_json_value(outcome.data)
        except WorkflowOutputError:
            return _failed(WF_ERROR_OUTPUT_UNAVAILABLE)
        return NodeExecutionOutcome(status=NODE_STATUS_SUCCEEDED, output=output)

    # ------------------------------------------------------------
    # AI
    # ------------------------------------------------------------

    async def _execute_ai(
        self,
        node: WorkflowNode,
        *,
        project_id: str,
        input: str | None,
        previous: str | None,
    ) -> NodeExecutionOutcome:
        """执行 AI（复用 binding 渲染；OQ-4 投影；WAITING 保留恢复键）。"""
        if node.question_template is None:
            raise ExecutionError("AI 节点缺少 question_template（契约违反）")

        # 1) 模板渲染（失败 ⇒ 零调用）
        try:
            question = render_template(
                node.question_template, input=input, previous=previous
            )
        except Exception:
            return _failed(WF_ERROR_NODE_EXECUTION_FAILURE)

        # 2) 唯一允许路径（OrchestratorPort；不触达 AgentRuntime 内部）
        port = self._orchestrator_provider(project_id)
        outcome = await port.execute(question)

        # 3) 嵌套等待（ADR §10.2）：保留恢复所需身份（**不**自动裁决）
        if outcome.agent_status == AGENT_STATUS_WAITING_APPROVAL:
            if not outcome.agent_run_id:
                # WAITING 但恢复键缺失 ⇒ 不可恢复，不能记 WAITING（fail-closed）
                return _failed(WF_ERROR_NODE_EXECUTION_FAILURE)
            try:
                output = serialize_json_value(
                    {
                        "agent_run_id": outcome.agent_run_id,
                        "agent_approval_id": outcome.agent_approval_id,
                    }
                )
            except WorkflowOutputError:
                return _failed(WF_ERROR_OUTPUT_UNAVAILABLE)
            return NodeExecutionOutcome(
                status=NODE_STATUS_WAITING_APPROVAL,
                output=output,
                agent_run_id=outcome.agent_run_id,
                agent_approval_id=outcome.agent_approval_id,
            )

        # 4) 普通投影（OQ-4：route + content；不暴露 data / metadata / Session）
        try:
            output = serialize_json_value(
                {"route": outcome.route, "content": outcome.content}
            )
        except WorkflowOutputError:
            return _failed(WF_ERROR_OUTPUT_UNAVAILABLE)
        return NodeExecutionOutcome(status=NODE_STATUS_SUCCEEDED, output=output)

    # ------------------------------------------------------------
    # APPROVAL
    # ------------------------------------------------------------

    def _execute_approval(
        self, node: WorkflowNode, *, workflow_run_id: str
    ) -> NodeExecutionOutcome:
        """创建待审批记录并返回 WAITING_APPROVAL（不自动批准 / 不改 Run 状态）。"""
        approval_id = self._approval_id_factory()
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ExecutionError(
                "approval_id_factory 必须返回非空 str（契约违反）"
            )

        # 幂等创建（ports 契约：重复 (run, node) ⇒ 返回既有行——不产生第二条）
        try:
            record = self._approval_store.create(
                approval_id=approval_id,
                workflow_run_id=workflow_run_id,
                node_id=node.node_id,
            )
        except ExecutionError:
            raise
        except Exception:
            # 创建失败 / 冲突 / 端口异常 ⇒ 明确的可处理错误（不静默、不重试、
            # 不产生第二条记录——唯一约束由存储层兜底）
            return _failed(WF_ERROR_APPROVAL_CONFLICT)
        if record.status != APPROVAL_STATUS_PENDING:
            # 既有审批已被裁决：不得伪装 WAITING（明确的可处理错误）
            return _failed(WF_ERROR_APPROVAL_CONFLICT)

        try:
            output = serialize_json_value({"approval_id": record.approval_id})
        except WorkflowOutputError:
            return _failed(WF_ERROR_OUTPUT_UNAVAILABLE)
        return NodeExecutionOutcome(
            status=NODE_STATUS_WAITING_APPROVAL,
            output=output,
            approval_id=record.approval_id,
        )


# ============================================================
# 内部辅助
# ============================================================


def _failed(error_code: str) -> NodeExecutionOutcome:
    return NodeExecutionOutcome(status=NODE_STATUS_FAILED, error_code=error_code)


def _build_tool_call_id(
    workflow_run_id: str, node_id: str, attempt: int
) -> str:
    """稳定、可追溯的 tool 调用标识（workflow 场景无 LLM tool_call 协议）。"""
    return f"{workflow_run_id}:{node_id}:{attempt}"


def _require_non_empty(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError(
            f"{field} 必须是非空 str（当前: {type(value).__name__}）"
        )
    return value
