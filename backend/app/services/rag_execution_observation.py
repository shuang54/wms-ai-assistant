"""RAG Execution Observation（Phase 3.12 Step 43）——**Runtime Only** 观测 DTO。

边界（本模块**只有**两件事）：

    RagExecutionObservation    （frozen；Runtime 诊断字段白名单）
    RagExecutionObserver       （Protocol；``record(observation)`` 单一出口）

来源与去向：

    AIOrchestratorService.execute()
        │  with assistant_trace_scope(request_id)      ← Step 36
        ↓
    RagService.answer()                               ← Step 43 最小 instrumentation
        │  current_assistant_request_id() → request_id
        ↓
    RagExecutionObservation（本模块）
        ↓ observer.record(...)                        ← best-effort（失败只 warning）
    InMemoryRagExecutionCollector                     ← Step 43（进程内 / 有界 / 只读查询）

为什么 request_id 用 contextvar 而不是新增参数：

    * RagService 的**公开 API 契约保持完全不变**
      （``answer(query, *, top_k, knowledge_scope)`` 0 新增参数）；
    * 关联键唯一来源仍是 Orchestrator 的 ``new_request_id()``（Step 35 / 36），
      本模块**不生成** ID、不推断、不回填。

安全边界（Step 42 Security Audit 结论 —— 字段白名单）：

    允许（Trace-safe；本 DTO 的全部字段）：
        request_id · started_at · finished_at · duration_ms · result_count ·
        used_chunks_count · top_k · context_truncated · context_chars ·
        reranker_used · rerank_elapsed_ms · chunk_ids · document_ids

    **禁止**（本模块的契约：这些值永不进入 DTO / 永不落内存观察记录）：
        query 原文 · answer · chunk content · document content ·
        embedding vector · prompt · LLM messages · LLM raw response ·
        SQL · DB connection / DATABASE_URL · 凭据 / API Key / Authorization

    特别注意（Step 42 §四）：``similarity`` 本阶段**刻意不进入** DTO ——
    第一版保持最小，需要时另开阶段评估必要性。

明确不做：

    * **无持久化**：无 DB / ORM / Repository / Migration / 文件 / Redis / Kafka；
    * **无 trace span**：无 OpenTelemetry / trace parent·child / span id；
    * **无聚合 / 无 metrics**：不做 count / 平均耗时 / 分位数（后续阶段）；
    * **无 TTL / 无后台任务 / 无单例**（存储由 Collector 显式持有）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "RagExecutionObservation",
    "RagExecutionObserver",
]


def _require_non_negative_int(field_name: str, value: object) -> int:
    """非负 int（``bool`` 明确拒绝 —— ``isinstance(True, int)`` 为 True）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field_name} 必须是 int（bool 不被接受）"
            f"（当前: {type(value).__name__}）"
        )
    if value < 0:
        raise ValueError(f"{field_name} 必须 >= 0（当前: {value}）")
    return value


def _require_optional_non_negative_float(
    field_name: str, value: object
) -> float | None:
    """``float | None``；非负；bool / 其它类型拒绝。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{field_name} 必须是 float 或 None"
            f"（当前: {type(value).__name__}）"
        )
    if value < 0:
        raise ValueError(f"{field_name} 必须 >= 0（当前: {value}）")
    return float(value)


def _require_identifier_tuple(field_name: str, value: object) -> tuple[int, ...]:
    """非负 int 元组；**去重并保持首次出现顺序**（不排序 —— 顺序稳定）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(
            f"{field_name} 必须是 tuple[int, ...]"
            f"（当前: {type(value).__name__}）"
        )
    seen: set[int] = set()
    deduplicated: list[int] = []
    for item in value:
        identifier = _require_non_negative_int(field_name, item)
        if identifier in seen:
            continue
        seen.add(identifier)
        deduplicated.append(identifier)
    return tuple(deduplicated)


@dataclass(frozen=True)
class RagExecutionObservation:
    """一次 RAG 执行的**运行时**观测记录（frozen；字段即安全白名单）。

    Attributes:
        request_id:          Assistant Trace ID（``assistant_trace_scope`` 内的
                            同一次 ``/api/ai/chat`` 请求）；非空字符串。
        started_at:          RAG 开始时间（UTC；仅诊断用，不参与 equality 断言）。
        finished_at:         RAG 结束时间（UTC）。
        duration_ms:         端到端耗时（毫秒；含 Vector Search / Rerank /
                             Context / LLM）。
        result_count:        Vector Search 返回的候选条数（reranker 前）。
        used_chunks_count:   实际纳入 Context 的片段数（可能 < result_count）。
        top_k:               本次实际生效的 top_k（reranker 开启时为
                             ``settings.reranker.top_k``，否则
                             ``settings.rag.default_top_k`` 或调用方显式值）。
        context_truncated:   Context 是否发生截断（片段级 / 内容级）。
        context_chars:       拼装后的 Context 字符数。
        reranker_used:       Reranker 是否参与本次执行（配置开关为准）。
        rerank_elapsed_ms:   Rerank 耗时；未使用 Reranker → ``None``。
        chunk_ids:           纳入 Context 的 chunk id（去重；首次出现顺序）。
        document_ids:        对应的 document id（去重；首次出现顺序）。

    Note:
        * 空检索同样产生 Observation（``result_count == 0``）——
          **空检索 ≠ 没有 RAG 执行**；
        * 不包含 query / answer / content / embedding / prompt / messages /
          LLM raw response / SQL / 凭据（见模块 docstring）；
        * 无 ``similarity``（Step 42 结论：第一版保持最小）；
        * 无 ``request_id`` 之外的任何 identifier（无 project_id / tool_call_id）。
    """

    request_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    result_count: int
    used_chunks_count: int
    top_k: int
    context_truncated: bool
    context_chars: int
    reranker_used: bool
    rerank_elapsed_ms: float | None
    chunk_ids: tuple[int, ...] = field(default_factory=tuple)
    document_ids: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str):
            raise TypeError(
                "request_id 必须是 str"
                f"（当前: {type(self.request_id).__name__}）"
            )
        if not self.request_id or not self.request_id.strip():
            raise ValueError("request_id 不能为空或纯空白")
        if len(self.request_id) > 128:
            raise ValueError(
                f"request_id 超出长度上限（{len(self.request_id)} > 128）"
            )
        if not isinstance(self.started_at, datetime) or not isinstance(
            self.finished_at, datetime
        ):
            raise TypeError("started_at / finished_at 必须是 datetime")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不能早于 started_at")
        _require_optional_non_negative_float("duration_ms", self.duration_ms)
        for field_name in (
            "result_count", "used_chunks_count", "top_k",
        ):
            _require_non_negative_int(field_name, getattr(self, field_name))
        for field_name in ("context_truncated", "reranker_used"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} 必须是 bool")
        _require_non_negative_int("context_chars", self.context_chars)
        _require_optional_non_negative_float(
            "rerank_elapsed_ms", self.rerank_elapsed_ms
        )
        # 规范化 identifier 元组（去重 + 首次出现顺序；frozen → object.__setattr__）
        object.__setattr__(
            self, "chunk_ids", _require_identifier_tuple(
                "chunk_ids", self.chunk_ids
            )
        )
        object.__setattr__(
            self, "document_ids", _require_identifier_tuple(
                "document_ids", self.document_ids
            )
        )

    # ---------- 显式映射（禁止 vars() / __dict__ / asdict / model_dump） ----------

    def to_dict(self) -> dict[str, Any]:
        """**显式字段映射**（13 字段；无递归序列化 / 无 __dict__ / 无 asdict）。"""
        return {
            "request_id": self.request_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "result_count": self.result_count,
            "used_chunks_count": self.used_chunks_count,
            "top_k": self.top_k,
            "context_truncated": self.context_truncated,
            "context_chars": self.context_chars,
            "reranker_used": self.reranker_used,
            "rerank_elapsed_ms": self.rerank_elapsed_ms,
            "chunk_ids": list(self.chunk_ids),
            "document_ids": list(self.document_ids),
        }


@runtime_checkable
class RagExecutionObserver(Protocol):
    """RAG 执行观测出口（唯一写入点；``None`` = 不观测）。

    实现方（当前：``InMemoryRagExecutionCollector``）必须：

        * 接受**一条** ``RagExecutionObservation``；
        * **不得**抛出影响主链路的异常（调用方仍做 best-effort 隔离）；
        * 不执行 RAG / 不访问 LLM / 不访问 DB / 不做聚合。
    """

    def record(self, observation: RagExecutionObservation) -> None:
        """接收一条 RAG 执行观测（实现方原样保存即可）。"""
        ...
