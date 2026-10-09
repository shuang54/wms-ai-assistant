"""Workflow Runtime 错误边界（Phase 6 ADR-01 §2 / §12 / §20）。

设计（对齐 ``agent/errors.py`` 先例，**独立命名空间**）：
    * 错误**只有分类**，没有自动恢复 / 没有 retry；
    * 稳定错误码 = ``WF_ERROR_*`` 常量（值为 ``WORKFLOW_*`` 字符串），
      独立于 ``AGENT_ERROR_CODES`` —— 不复用 agent 错误码命名空间（ADR §20 SEC4）；
    * 错误码 / 异常消息**不含**自由文本 / 异常正文 / prompt / 参数 / 密钥；
    * 本模块**零依赖**（仅 stdlib）。

两级错误：
    1) 装配 / 调用期异常（``WorkflowError`` 家族；fail-fast —— 定义非法、
       策略非法、状态非法、输出非法、注册表冲突）；
    2) 运行时稳定码（``WF_ERROR_CODES`` 白名单；供 runtime / persistence /
       read 归一化使用 —— Step 2/3/4 消费，本步只冻结常量）。
"""
from __future__ import annotations

from typing import Final

__all__ = [
    # 异常
    "WorkflowError",
    "WorkflowDefinitionError",
    "WorkflowDefinitionNotFoundError",
    "WorkflowDefinitionAlreadyRegisteredError",
    "WorkflowDefinitionVersionMismatchError",
    "WorkflowPolicyError",
    "WorkflowStateError",
    "WorkflowInputError",
    "WorkflowOutputError",
    # 稳定码
    "WF_ERROR_RUN_NOT_FOUND",
    "WF_ERROR_RUN_NOT_RESUMABLE",
    "WF_ERROR_RUN_NOT_CANCELLABLE",
    "WF_ERROR_STATE_CONFLICT",
    "WF_ERROR_DEFINITION_VERSION_MISMATCH",
    "WF_ERROR_NODE_EXECUTION_FAILURE",
    "WF_ERROR_NODE_UNKNOWN",
    "WF_ERROR_RETRY_EXHAUSTED",
    "WF_ERROR_APPROVAL_REQUIRED",
    "WF_ERROR_APPROVAL_NOT_FOUND",
    "WF_ERROR_APPROVAL_CONFLICT",
    "WF_ERROR_APPROVAL_REJECTED",
    "WF_ERROR_CANCEL_REQUESTED",
    "WF_ERROR_LIMIT_REACHED",
    "WF_ERROR_OUTPUT_UNAVAILABLE",
    "WF_ERROR_PERSISTENCE_FAILURE",
    "WF_ERROR_CODES",
]


# ============================================================
# 1) 装配 / 调用期异常（fail-fast；ValueError 家族对齐项目 DTO 校验风格）
# ============================================================


class WorkflowError(Exception):
    """Workflow 边界错误基类（本模块只用于装配 / 调用期错误）。"""


class WorkflowDefinitionError(WorkflowError, ValueError):
    """``WorkflowDefinition`` / ``WorkflowNode`` / ``WorkflowCondition`` 非法。

    fail-closed：定义在注册 / 使用前即被拒绝（不静默修正、不猜测意图）。
    """


class WorkflowDefinitionNotFoundError(WorkflowDefinitionError):
    """Registry 中不存在该 workflow name。

    Attributes:
        name: 未注册的 workflow name（非敏感）。
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Workflow 定义不存在: {name!r}")
        self.name = name


class WorkflowDefinitionAlreadyRegisteredError(WorkflowDefinitionError):
    """同一 name 重复注册（显式失败；不做隐式替换 / 不覆盖）。

    Attributes:
        name: 重复注册的 workflow name。
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Workflow 已注册: {name!r}（重复注册被拒绝）")
        self.name = name


class WorkflowDefinitionVersionMismatchError(WorkflowDefinitionError):
    """Resume 的 definition version 与 Registry 当前版本不一致（fail-closed）。

    ADR 批准修正 4：Resume 必须按持久化 version 解析；
    **禁止**隐式获取 current version。不一致 ⇒ 本错误
    （Step 3 Runtime 映射为 :data:`WF_ERROR_DEFINITION_VERSION_MISMATCH`）。

    Attributes:
        name:    workflow name。
        version: 请求的（持久化）version。
    """

    def __init__(self, name: str, version: str) -> None:
        super().__init__(
            f"Workflow 定义版本不一致: {name!r}（请求 version={version!r}）"
        )
        self.name = name
        self.version = version


class WorkflowPolicyError(WorkflowError, ValueError):
    """``WorkflowRuntimePolicy`` / ``NodeRetryPolicy`` 非法（构造即拒绝，fail-closed）。"""


class WorkflowStateError(WorkflowError, ValueError):
    """未知状态 / 非法状态转移（不静默修正）。"""


class WorkflowInputError(WorkflowError, ValueError):
    """DTO / 输入构造校验失败（frozen dataclass ``__post_init__``）。

    触发示例：``NodeExecutionIdentity`` 字段非法、``WorkflowRunResult``
    计数非法、``WorkflowApprovalRecord`` 字段非法（fail-closed）。
    """


class WorkflowOutputError(WorkflowError, ValueError):
    """节点输出 / 启动输入不满足 JSON 值边界。

    触发（fail-closed）：
        * 非 JSON 兼容类型（bytes / set / 任意对象 / ORM 实体 / Session 等）；
        * 嵌套深度超过 ``JSON_VALUE_MAX_DEPTH``；
        * 序列化字节数超过 ``JSON_VALUE_MAX_BYTES``（32 KiB 内联边界）；
        * float NaN / Infinity（不可序列化 / 不可持久化）。
    """


# ============================================================
# 2) 运行时稳定码（WF_ERROR_CODES 白名单；单源）
# ============================================================

#: run_id 不存在（或读边界不可用 ⇒ fail-closed，由上层归一）。
WF_ERROR_RUN_NOT_FOUND: Final[str] = "WORKFLOW_RUN_NOT_FOUND"

#: 终态 run 禁止 resume（COMPLETED / FAILED / CANCELLED）。
WF_ERROR_RUN_NOT_RESUMABLE: Final[str] = "WORKFLOW_RUN_NOT_RESUMABLE"

#: 终态 run 的 cancel 为 no-op（幂等返回既有状态，不写入）。
WF_ERROR_RUN_NOT_CANCELLABLE: Final[str] = "WORKFLOW_RUN_NOT_CANCELLABLE"

#: 乐观并发 / 单赢冲突（state_version CAS / approval 条件 UPDATE / cancel 竞争）。
WF_ERROR_STATE_CONFLICT: Final[str] = "WORKFLOW_STATE_CONFLICT"

#: Resume 的 definition version 与 Registry 不一致（fail-closed）。
WF_ERROR_DEFINITION_VERSION_MISMATCH: Final[str] = "WORKFLOW_DEFINITION_VERSION_MISMATCH"

#: 节点执行失败（含 ai / tool / 序列化失败的归一化）。
WF_ERROR_NODE_EXECUTION_FAILURE: Final[str] = "WORKFLOW_NODE_EXECUTION_FAILURE"

#: 节点结果不可判定（crash 后 RUNNING 无终态记录；fail-closed，**不自动重跑**）。
WF_ERROR_NODE_UNKNOWN: Final[str] = "WORKFLOW_NODE_UNKNOWN"

#: 重试次数用尽（max_attempts 到达后的终态失败原因）。
WF_ERROR_RETRY_EXHAUSTED: Final[str] = "WORKFLOW_RETRY_EXHAUSTED"

#: approval 节点等待人工裁决（run = WAITING；不是失败）。
WF_ERROR_APPROVAL_REQUIRED: Final[str] = "WORKFLOW_APPROVAL_REQUIRED"

#: approval 记录缺失 / 身份不一致（fail-closed）。
WF_ERROR_APPROVAL_NOT_FOUND: Final[str] = "WORKFLOW_APPROVAL_NOT_FOUND"

#: approval 裁决冲突（重复不同裁决 / run 已终态 / 并发竞争）。
WF_ERROR_APPROVAL_CONFLICT: Final[str] = "WORKFLOW_APPROVAL_CONFLICT"

#: 人工明确拒绝（node FAILED → run FAILED；不重试）。
WF_ERROR_APPROVAL_REJECTED: Final[str] = "WORKFLOW_APPROVAL_REJECTED"

#: 协作式取消在安全边界生效（run CANCELLED；≠ FAILED）。
WF_ERROR_CANCEL_REQUESTED: Final[str] = "WORKFLOW_CANCEL_REQUESTED"

#: 预算上限到达（nodes / attempts；不是失败——同 Agent LIMIT_REACHED 语义）。
WF_ERROR_LIMIT_REACHED: Final[str] = "WORKFLOW_LIMIT_REACHED"

#: 输出不可用（超限 / 敏感标记 / 未持久化）——依赖该输出的推进 fail-closed。
WF_ERROR_OUTPUT_UNAVAILABLE: Final[str] = "WORKFLOW_OUTPUT_UNAVAILABLE"

#: 控制状态写失败（run / node 推进不可持久化 ⇒ fail-closed 停止推进）。
WF_ERROR_PERSISTENCE_FAILURE: Final[str] = "WORKFLOW_PERSISTENCE_FAILURE"

#: 全部运行稳定码（白名单；供上层归一化 / 校验；**不含**自由文本）。
WF_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        WF_ERROR_RUN_NOT_FOUND,
        WF_ERROR_RUN_NOT_RESUMABLE,
        WF_ERROR_RUN_NOT_CANCELLABLE,
        WF_ERROR_STATE_CONFLICT,
        WF_ERROR_DEFINITION_VERSION_MISMATCH,
        WF_ERROR_NODE_EXECUTION_FAILURE,
        WF_ERROR_NODE_UNKNOWN,
        WF_ERROR_RETRY_EXHAUSTED,
        WF_ERROR_APPROVAL_REQUIRED,
        WF_ERROR_APPROVAL_NOT_FOUND,
        WF_ERROR_APPROVAL_CONFLICT,
        WF_ERROR_APPROVAL_REJECTED,
        WF_ERROR_CANCEL_REQUESTED,
        WF_ERROR_LIMIT_REACHED,
        WF_ERROR_OUTPUT_UNAVAILABLE,
        WF_ERROR_PERSISTENCE_FAILURE,
    }
)
