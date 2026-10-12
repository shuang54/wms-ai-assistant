"""Agent Decision Store（Phase 5 P-6 —— LLM Decision 的可 Replay 持久化）。

职责（单一）：把 **Runtime 已接受的 Decision** 落盘，使 Resume 可以：

```text
checkpoint.pending_decision_ref
        ↓
AgentDecisionStore.get_by_ref(ref)
        ↓
Decision（FINAL | TOOL_CALL）        ← **不重新调用 LLM**
```

职责边界（严格）：

    * **只保存恢复控制流真正需要的 Decision**：decision_type / tool_name /
      tool_call_id / arguments / content / finish_reason；
    * **绝对禁止**保存：system prompt / user prompt / 完整 messages /
      conversation context / RAG chunks / tool definitions / API Key /
      Authorization / Cookie / password / database URL / SQL / Tool Result /
      LLM raw response / headers / stack trace / exception message（§十）；
    * **不修改** ``agent_run`` / ``agent_step`` / ``agent_tool_result`` /
      ``agent_checkpoint`` schema；Decision 是**独立**持久化对象（§八）；
    * **写失败 = fail closed**（§十四）：与其它 best-effort 端口不同，
      Decision 未落盘时 Runtime 必须 FAILED（``DECISION_PERSISTENCE_FAILURE``），
      **不执行 Tool、不再调 LLM**；
    * 读失败同样收敛为异常（由 Resume 映射为 fail-closed 稳定码）；
    * ``decision_ref`` = ``"{run_id}:{step_index}"``（稳定引用；**不是**
      ``tool_call_id``（Provider protocol correlation）——§十二）。

依赖方向：本模块可 import agent 内核（decision / errors / id/敏感标记工具）；
Repository **只在调用期由 composition root 注入**（模块加载不引入 DB 基础设施）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from backend.app.agent.decision import (
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
)
from backend.app.agent.errors import AGENT_ERROR_DECISION_PERSISTENCE_FAILURE
from backend.app.agent.sensitive import contains_sensitive_marker

logger = logging.getLogger(__name__)

__all__ = [
    "AgentDecisionRecord",
    "AgentDecisionStore",
    "AgentDecisionPersistenceError",
    "decision_ref",
    "parse_decision_ref",
    "assert_decision_ref_matches",
    "decision_record_of",
    "DECISION_TYPES",
    "MAX_DECISION_ARGUMENTS_SIZE",
    "DECISION_REF_MAX_LENGTH",
    "DECISION_TYPE_MAX_LENGTH",
    "DECISION_TOOL_NAME_MAX_LENGTH",
    "DECISION_TOOL_CALL_ID_MAX_LENGTH",
    "DECISION_CONTENT_MAX_LENGTH",
    "DECISION_FINISH_REASON_MAX_LENGTH",
]

#: 允许的 Decision 类型（**严格复用** Runtime 既有类型；不新增）。
DECISION_TYPES: Final[frozenset[str]] = frozenset({DECISION_FINAL, DECISION_TOOL_CALL})

#: arguments 上限（§十一：超过 ⇒ decision_unavailable；**不**截断、**不**部分保存）。
MAX_DECISION_ARGUMENTS_SIZE: Final[int] = 32 * 1024

# ---- 列长度上限 ----
DECISION_REF_MAX_LENGTH: Final[int] = 128
DECISION_TYPE_MAX_LENGTH: Final[int] = 32
DECISION_TOOL_NAME_MAX_LENGTH: Final[int] = 128
DECISION_TOOL_CALL_ID_MAX_LENGTH: Final[int] = 128
#: ``content``（FINAL 答案）上限：显式有界（超出 ⇒ decision_unavailable）。
DECISION_CONTENT_MAX_LENGTH: Final[int] = 16 * 1024
DECISION_FINISH_REASON_MAX_LENGTH: Final[int] = 64


def decision_ref(run_id: str, step_index: int) -> str:
    """Decision 的稳定引用：``"{run_id}:{step_index}"``（与 P-5 示例一致：``abc:2``）。"""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 必须是非空 str")
    if isinstance(step_index, bool) or not isinstance(step_index, int):
        raise ValueError("step_index 必须是 int")
    if step_index < 0:
        raise ValueError("step_index 必须 >= 0")
    return f"{run_id}:{step_index}"


def parse_decision_ref(ref: str) -> tuple[str, int]:
    """解析 ``decision_ref`` → ``(run_id, step_index)``。"""
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("decision_ref 必须是非空 str")
    run_id, separator, raw_index = ref.rpartition(":")
    if not separator or not run_id.strip():
        raise ValueError(f"decision_ref 格式非法: {ref!r}")
    try:
        step_index = int(raw_index)
    except ValueError as exc:
        raise ValueError(f"decision_ref 的 step_index 非法: {ref!r}") from exc
    if step_index < 0:
        raise ValueError(f"decision_ref 的 step_index 不能为负: {ref!r}")
    return run_id, step_index


def assert_decision_ref_matches(
    ref: str, *, run_id: str, step_index: int
) -> None:
    """纯函数校验：引用必须指向**同一个 run、同一个 step**（§十八 一致性）。

    Raises:
        ValueError: 引用指向其它 run / 其它 step（或格式非法）。
    """
    ref_run_id, ref_step_index = parse_decision_ref(ref)
    if ref_run_id != run_id:
        raise ValueError(
            f"decision_ref 指向其它 run（ref={ref_run_id!r}, run_id={run_id!r}）"
        )
    if ref_step_index != step_index:
        raise ValueError(
            "decision_ref 指向其它 step"
            f"（ref={ref_step_index}, step_index={step_index}）"
        )


@dataclass(frozen=True)
class AgentDecisionRecord:
    """Decision 的持久化投影（frozen；字段 ≡ §九 最小 Schema + ``decision_unavailable``）。

    注意：``arguments`` / ``content`` 均来自 **untrusted LLM 输出**；
    ``decision_unavailable=True`` 表示该 Decision **不可安全 Replay**
    （arguments 超限 / 含敏感标记 / content 超长）⇒ Resume 必须 fail closed。
    """

    run_id: str
    step_index: int
    decision_ref: str
    decision_type: str
    tool_call_id: str | None
    tool_name: str | None
    arguments: Any | None
    content: str | None
    finish_reason: str | None
    decision_unavailable: bool
    created_at: datetime


def _json_safe(value: Any) -> Any:
    """JSON-safe 规范化（**不**接受 bytes / Session / Connection / 任意对象）。

    Raises:
        ValueError: 不是 JSON 兼容结构（含 NaN / Infinity / 非 str key）。
    """
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return json.loads(text)


def decision_record_of(
    *,
    run_id: str,
    step_index: int,
    decision: AgentDecision,
) -> tuple[str, str, str | None, str | None, Any | None, str | None, bool]:
    """``AgentDecision`` → 落盘字段（纯函数；无 IO）。

    Returns:
        ``(decision_ref, decision_type, tool_call_id, tool_name, arguments,
        content, decision_unavailable)``

    安全策略（§十一 / §十）：

        * ``arguments``：必须 JSON-safe；序列化后 ≤ 32 KiB 且不含敏感标记
          ⇒ 保存；否则 ``decision_unavailable=True`` + ``arguments=None``
          （**不**截断、**不**部分保存、**不**改写）；
        * ``content``（FINAL）：非空 str 且 ≤ 上限且不含敏感标记 ⇒ 保存；
          否则 ``decision_unavailable=True`` + ``content=None``；
        * ``tool_call_id``：Provider 原值（**不**生成 / **不**替换）。
    """
    ref = decision_ref(run_id, step_index)
    decision_type = decision.type
    if decision_type not in DECISION_TYPES:
        raise ValueError(f"decision_type 不在允许集合内: {decision_type!r}")

    unavailable = False
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments: Any | None = None
    content: str | None = None

    if decision_type == DECISION_TOOL_CALL:
        raw_arguments = decision.arguments if decision.arguments is not None else {}
        try:
            normalized = _json_safe(raw_arguments)
            serialized = json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            normalized, serialized = None, ""
        if serialized and len(serialized.encode("utf-8")) <= MAX_DECISION_ARGUMENTS_SIZE:
            if contains_sensitive_marker(serialized):
                unavailable = True
            else:
                arguments = normalized
        else:
            unavailable = True
        tool_call_id = (
            decision.tool_call_id
            if isinstance(decision.tool_call_id, str) and decision.tool_call_id.strip()
            else None
        )
        tool_name = (
            decision.tool_name
            if isinstance(decision.tool_name, str) and decision.tool_name.strip()
            else None
        )
    else:
        raw_content = decision.content if isinstance(decision.content, str) else ""
        if (
            raw_content.strip()
            and len(raw_content) <= DECISION_CONTENT_MAX_LENGTH
            and not contains_sensitive_marker(raw_content)
        ):
            content = raw_content
        else:
            unavailable = True

    return (ref, decision_type, tool_call_id, tool_name, arguments, content, unavailable)


class AgentDecisionPersistenceError(Exception):
    """Decision 未持久化（fail-closed；§十四）。

    ``code`` = ``AGENT_DECISION_PERSISTENCE_FAILURE``（稳定码；不含自由文本）。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = AGENT_ERROR_DECISION_PERSISTENCE_FAILURE


class AgentDecisionStore:
    """Decision Store（**fail-closed**）：写失败抛异常；读失败抛异常。

    与其它端口不同：Decision 是"恢复所需的最小事实"，缺失即无法安全继续
    ⇒ 本类**不**做 best-effort 吞异常（由 Runtime / Resume 决定 fail-closed 结果）。
    """

    def __init__(self, repository: Any) -> None:
        """构造 Store（**repository 必须注入**：本模块是纯域层，不 import DB）。"""
        for method in ("create", "get", "get_latest"):
            if not callable(getattr(repository, method, None)):
                raise ValueError(
                    f"repository 必须提供可调用的 {method}()"
                    f"（got {type(repository).__name__}）"
                )
        self._repository = repository

    @property
    def repository(self) -> Any:
        """底层仓储（只读暴露，便于装配断言 / 测试）。"""
        return self._repository

    # ---------- 写（fail-closed） ----------

    def save(
        self,
        *,
        run_id: str,
        step_index: int,
        decision: AgentDecision,
        finish_reason: str | None = None,
    ) -> AgentDecisionRecord:
        """持久化 Decision（幂等：同 (run_id, step_index) 已存在 ⇒ 返回既有记录）。

        Raises:
            ValueError:                   参数非法（触达 DB 之前拦截）。
            AgentDecisionPersistenceError: 写入失败 / 幂等冲突后仍不可读。
        """
        (
            ref,
            decision_type,
            tool_call_id,
            tool_name,
            arguments,
            content,
            unavailable,
        ) = decision_record_of(
            run_id=run_id,
            step_index=step_index,
            decision=decision,
        )
        try:
            created = self._repository.create(
                run_id=run_id,
                step_index=step_index,
                decision_ref=ref,
                decision_type=decision_type,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                content=content,
                decision_unavailable=unavailable,
                finish_reason=finish_reason,
            )
        except Exception as exc:  # noqa: BLE001 —— fail-closed（不吞）
            logger.warning(
                "Decision 持久化失败（fail-closed）: %s",
                type(exc).__name__,
                extra={"error_type": type(exc).__name__, "run_id": run_id},
            )
            raise AgentDecisionPersistenceError(
                f"Decision 持久化失败: {type(exc).__name__}"
            ) from exc
        if created is not None:
            return created
        # 幂等：已存在（first-write-wins）⇒ 必须能读回同一条（否则 fail-closed）
        existing = self.get(run_id, step_index)
        if existing is None:
            raise AgentDecisionPersistenceError(
                "Decision 幂等冲突后无法读回既有记录"
            )
        return existing

    async def asave(
        self,
        *,
        run_id: str,
        step_index: int,
        decision: AgentDecision,
        finish_reason: str | None = None,
    ) -> AgentDecisionRecord:
        """异步入口（同步持久化放进 worker 线程；``CancelledError`` 不吞）。"""
        return await asyncio.to_thread(
            self.save,
            run_id=run_id,
            step_index=step_index,
            decision=decision,
            finish_reason=finish_reason,
        )

    # ---------- 读（fail-closed：异常向上传播） ----------

    def get(self, run_id: str, step_index: int) -> AgentDecisionRecord | None:
        """按执行身份读取（无 ⇒ ``None``）。"""
        return self._repository.get(run_id, step_index)

    def get_by_ref(self, ref: str) -> AgentDecisionRecord | None:
        """按 ``decision_ref`` 读取（Resume 主路径，§十六）。"""
        parse_decision_ref(ref)  # 格式校验（非法 ⇒ ValueError）
        return self._repository.get_by_ref(ref)

    def get_latest(self, run_id: str) -> AgentDecisionRecord | None:
        """读取该 Run 的**最后**一条 Decision（仅供终态内容恢复；§三十七）。"""
        return self._repository.get_latest(run_id)
