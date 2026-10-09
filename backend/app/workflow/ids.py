"""Workflow Run 身份生成（Phase 6 ADR-01 §8.5 namespace 纪律 / §9）。

职责（唯一）：
    ``new_workflow_run_id()`` —— 一次 Workflow start 的**执行身份**（uuid4 文本）。

边界（严格）：
    * 身份由 Workflow Runtime 层在 start 时**签发一次**；
      Persistence / Read / 任何端口**不得**生成 ``workflow_run_id``（只读取）；
    * ``node_id`` 来自 ``WorkflowDefinition``（不由本模块生成）；
      ``attempt`` 是运行计数（不是身份生成）；
    * 与 agent ``run_id`` / ``request_id`` / conversation ``turn_id`` /
      ``tool_call_id`` **互不相同**（独立 namespace —— 禁止复用 agent 语义）；
    * 零依赖（stdlib only）。
"""
from __future__ import annotations

import uuid

__all__ = ["new_workflow_run_id"]


def new_workflow_run_id() -> str:
    """签发一个新的 Workflow Run 身份（``uuid4`` 字符串）。

    Returns:
        str：标准 uuid4 文本（不截断、不含语义信息）。

    Note:
        调用点唯一 = Workflow Runtime ``start``（每次执行签发一个）；
        持久化 / 读边界**只读取**该值，不重新生成。
    """
    return str(uuid.uuid4())
