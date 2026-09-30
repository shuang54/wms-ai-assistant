"""RAG Runtime Observability 测试（Phase 3.12 Step 43）。

    RagService（observer 注入）→ RagExecutionObservation
        → InMemoryRagExecutionCollector → RagObservabilityQueryService

0 DB / 0 real LLM / 0 网络 / 0 新 HTTP API / 0 Assistant Trace 修改。
所有 Fake 均为确定性（固定 chunk / 固定 LLM 文本），不依赖真实外部服务。
"""
from __future__ import annotations

import ast
import dataclasses
from dataclasses import fields
from pathlib import Path

import pytest

from backend.app.api import orchestrator_chat as root
from backend.app.config import settings
from backend.app.main import app
from backend.app.services import rag_service as rag_service_module
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.in_memory_rag_execution_collector import (
    DEFAULT_MAX_RECORDS,
    InMemoryRagExecutionCollector,
)
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.rag_observability_query_service import (
    RagObservabilityQueryService,
)
from backend.app.services.rag_service import RagResponse, RagService
from backend.app.services.vector_search_service import VectorSearchResult
from backend.app.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRACE_A = "trace-rag-A"
_TRACE_B = "trace-rag-B"
_QUERY = "采购入库怎么操作？"
_LLM_ANSWER = "[mock-llm] 采购入库包括收货、质检和上架。"
_CHUNK_SENTINEL = "SENTINEL-CHUNK-CONTENT-STEP43"

#: 安全白名单（13 字段；无 similarity / 无 query / 无 content）
_SAFE_FIELDS = (
    "request_id", "started_at", "finished_at", "duration_ms", "result_count",
    "used_chunks_count", "top_k", "context_truncated", "context_chars",
    "reranker_used", "rerank_elapsed_ms", "chunk_ids", "document_ids",
)

_FORBIDDEN_KEYS = (
    "query", "answer", "content", "prompt", "messages", "raw_response",
    "embedding", "vector", "sql", "password", "api_key", "authorization",
    "database_url", "session", "connection", "traceback", "similarity",
    "project_id", "tool_call_id", "metadata", "source", "sources",
)


# ============================================================
# Fakes（确定性；与 tests/test_rag_service.py 同风格）
# ============================================================

class _MockVectorSearchService:
    def __init__(
        self,
        *,
        results: list[VectorSearchResult] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._results = results if results is not None else []
        self._raise = raise_exc
        self.search_calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self.search_calls.append((query, top_k))
        if self._raise is not None:
            raise self._raise
        return list(self._results)


class _ScriptedLLMClient:
    def __init__(
        self, *, response: str = _LLM_ANSWER, raise_exc: Exception | None = None
    ) -> None:
        self._response = response
        self._raise = raise_exc
        self.chat_calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.chat_calls.append(messages)
        if self._raise is not None:
            raise self._raise
        return self._response


class _EchoContextBuilder:
    """确定性 ContextBuilder：原样使用全部 results（便于验证顺序 / 去重）。"""

    def __init__(self, *, total_chars: int = 100, truncated: bool = False) -> None:
        self._total_chars = total_chars
        self._truncated = truncated

    def build(self, results):  # noqa: ANN001, ANN201
        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=self._total_chars,
            truncated=self._truncated,
            dropped_count=0,
        )


class _MockReranker:
    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = scores
        self.calls = 0

    async def rerank(self, query: str, contents: list[str]) -> list[float]:
        self.calls += 1
        if self._scores is not None:
            return list(self._scores)
        return [1.0 - index * 0.01 for index in range(len(contents))]


class _BrokenCollector:
    """Observer 本体故障（record 抛错）——验证观测失败隔离。"""

    def __init__(self) -> None:
        self.calls = 0

    def record(self, observation: RagExecutionObservation) -> None:
        self.calls += 1
        raise RuntimeError("collector boom")


class _SpyCollector:
    """记录是否被访问（用于"校验先于查询"断言）。"""

    def __init__(self) -> None:
        self.records_calls = 0
        self.by_request_id_calls: list[str] = []

    def records(self):  # noqa: ANN201
        self.records_calls += 1
        return ()

    def records_by_request_id(self, request_id: str):  # noqa: ANN201
        self.by_request_id_calls.append(request_id)
        return ()


def _chunk(
    chunk_id: int,
    *,
    document_id: int = 10,
    chunk_index: int = 0,
    content: str = _CHUNK_SENTINEL,
    similarity: float = 0.9,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata={"heading": "采购入库"},
    )


def _service(
    collector,
    *,
    results: list[VectorSearchResult] | None = None,
    search_error: Exception | None = None,
    context_builder=None,  # noqa: ANN001
    reranker=None,  # noqa: ANN001
    top_k: int = 5,
):
    return RagService(
        vector_search_service=_MockVectorSearchService(  # type: ignore[arg-type]
            results=results if results is not None else [_chunk(1)],
            raise_exc=search_error,
        ),
        llm_client=_ScriptedLLMClient(),  # type: ignore[arg-type]
        context_builder=context_builder or _EchoContextBuilder(),  # type: ignore[arg-type]
        reranker_client=reranker,  # type: ignore[arg-type]
        observer=collector,
    )


def _enable_reranker(monkeypatch, *, candidate_top_k: int = 10, top_k: int = 3) -> None:
    """按既有测试写法打开 Reranker 配置开关。"""
    new_reranker = dataclasses.replace(
        settings.reranker,
        enabled=True,
        candidate_top_k=candidate_top_k,
        top_k=top_k,
    )
    new_settings = dataclasses.replace(settings, reranker=new_reranker)
    monkeypatch.setattr(rag_service_module, "settings", new_settings)


# ============================================================
# 1 / 2. 正常执行 + request_id 关联
# ============================================================

class TestObservationLifecycle:
    async def test_success_produces_one_observation(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(
            collector,
            results=[_chunk(1), _chunk(2, document_id=20)],
            context_builder=_EchoContextBuilder(total_chars=321),
        )

        with assistant_trace_scope(_TRACE_A):
            response = await service.answer(_QUERY, top_k=4)

        assert isinstance(response, RagResponse)
        records = collector.records()
        assert len(records) == 1
        observation = records[0]
        assert observation.request_id == _TRACE_A          # ← 关联键
        assert observation.result_count == 2
        assert observation.used_chunks_count == 2
        assert observation.top_k == 4
        assert observation.context_chars == 321
        assert observation.context_truncated is False
        assert observation.reranker_used is False
        assert observation.rerank_elapsed_ms is None
        assert observation.duration_ms >= 0.0
        assert observation.finished_at >= observation.started_at

    async def test_request_id_matches_orchestrator_metadata(self) -> None:
        """Correlation：observation.request_id == 响应 metadata.request_id。"""
        collector = InMemoryRagExecutionCollector()
        registry = ToolRegistry()
        orchestrator = AIOrchestratorService(
            router=AIRouterService(
                llm_fallback_enabled=False,
                tool_capabilities=ToolRegistryCapabilityAdapter(registry),
            ),
            rag_service=_service(collector),  # type: ignore[arg-type]
        )

        result = await orchestrator.execute(_QUERY)

        assert result.route.value == "rag"
        records = collector.records()
        assert len(records) == 1
        assert result.metadata["request_id"] == records[0].request_id
        assert records[0].request_id

    async def test_empty_retrieval_still_produces_observation(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(collector, results=[])

        with assistant_trace_scope(_TRACE_A):
            response = await service.answer(_QUERY)

        assert response.sources == ()
        records = collector.records()
        assert len(records) == 1                        # 空检索 ≠ 没有执行
        assert records[0].result_count == 0
        assert records[0].used_chunks_count == 0
        assert records[0].chunk_ids == ()
        assert records[0].document_ids == ()
        assert records[0].request_id == _TRACE_A

    async def test_reranker_disabled_flag(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(collector, reranker=_MockReranker())

        with assistant_trace_scope(_TRACE_A):
            await service.answer(_QUERY)

        observation = collector.records()[0]
        assert observation.reranker_used is False
        assert observation.rerank_elapsed_ms is None

    async def test_reranker_enabled_records_usage_and_duration(
        self, monkeypatch
    ) -> None:
        _enable_reranker(monkeypatch, candidate_top_k=4, top_k=2)
        collector = InMemoryRagExecutionCollector()
        reranker = _MockReranker()
        service = _service(
            collector,
            results=[_chunk(1), _chunk(2), _chunk(3)],
            reranker=reranker,
        )

        with assistant_trace_scope(_TRACE_A):
            await service.answer(_QUERY)

        observation = collector.records()[0]
        assert reranker.calls == 1
        assert observation.reranker_used is True
        assert observation.rerank_elapsed_ms is not None
        assert observation.rerank_elapsed_ms >= 0.0
        assert observation.top_k == 2                    # settings.reranker.top_k

    async def test_observation_failure_is_isolated(self) -> None:
        """观测失败（observer.record 抛错）绝不能把 RAG 成功变成失败。"""
        collector = _BrokenCollector()
        service = _service(collector, results=[_chunk(1)])

        with assistant_trace_scope(_TRACE_A):
            response = await service.answer(_QUERY)

        assert isinstance(response, RagResponse)         # 主链路成功
        assert response.used_chunks_count == 1
        assert collector.calls == 1

    async def test_rag_error_preserved_and_observed(self) -> None:
        """RAG 异常：观测安全结束 + **原始异常原样抛出**。"""
        error = RuntimeError("vector search down")
        collector = InMemoryRagExecutionCollector()
        service = _service(collector, search_error=error)

        with assistant_trace_scope(_TRACE_A):
            with pytest.raises(RuntimeError) as caught:
                await service.answer(_QUERY)

        assert caught.value is error                     # 同一实例 / 未被替换
        records = collector.records()
        assert len(records) == 1                         # 失败也产生观测
        assert records[0].request_id == _TRACE_A
        assert records[0].result_count == 0

    async def test_no_observer_no_observation(self) -> None:
        """observer=None（默认）→ 0 观测；行为与 Step 43 之前完全一致。"""
        service = RagService(
            vector_search_service=_MockVectorSearchService(  # type: ignore[arg-type]
                results=[_chunk(1)]
            ),
            llm_client=_ScriptedLLMClient(),  # type: ignore[arg-type]
            context_builder=_EchoContextBuilder(),  # type: ignore[arg-type]
        )

        with assistant_trace_scope(_TRACE_A):
            response = await service.answer(_QUERY)

        assert isinstance(response, RagResponse)

    async def test_unbound_trace_scope_records_nothing(self) -> None:
        """未绑定 Assistant Trace（旧链路 / 直连 Service）→ 不记录（无关联键）。"""
        collector = InMemoryRagExecutionCollector()
        service = _service(collector)

        response = await service.answer(_QUERY)

        assert isinstance(response, RagResponse)
        assert collector.records() == ()

    async def test_observer_must_be_callable_record(self) -> None:
        with pytest.raises(TypeError):
            RagService(observer=object())  # type: ignore[arg-type]


# ============================================================
# 3 / 4. chunk / document id（去重 + 首次出现顺序）
# ============================================================

class TestIdentifierExtraction:
    async def test_chunk_and_document_ids_deduplicated_in_order(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(
            collector,
            results=[
                _chunk(1, document_id=10),
                _chunk(2, document_id=10),
                _chunk(1, document_id=30),           # 重复 chunk → 保留首次
            ],
        )

        with assistant_trace_scope(_TRACE_A):
            await service.answer(_QUERY)

        observation = collector.records()[0]
        assert observation.chunk_ids == (1, 2)           # 1,2,1 → 1,2
        assert observation.document_ids == (10, 30)      # 10,10,30 → 10,30
        assert observation.used_chunks_count == 3        # 计数仍为实际片段数

    def test_dto_normalizes_duplicates_on_construction(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        observation = RagExecutionObservation(
            request_id=_TRACE_A,
            started_at=now,
            finished_at=now,
            duration_ms=0.0,
            result_count=2,
            used_chunks_count=2,
            top_k=5,
            context_truncated=False,
            context_chars=0,
            reranker_used=False,
            rerank_elapsed_ms=None,
            chunk_ids=(5, 5, 7, 5),
            document_ids=(1, 1, 1),
        )

        assert observation.chunk_ids == (5, 7)
        assert observation.document_ids == (1,)


# ============================================================
# 5 / 6. Collector（capacity / clear / 类型）
# ============================================================

class TestCollectorBoundaries:
    async def test_default_capacity_is_bounded(self) -> None:
        """1001 条 → 保留最新 1000（无界增长被禁止）。"""
        assert DEFAULT_MAX_RECORDS == 1000
        collector = InMemoryRagExecutionCollector()

        for index in range(DEFAULT_MAX_RECORDS + 1):
            with assistant_trace_scope(f"{_TRACE_A}-{index}"):
                await _service(collector).answer(_QUERY)

        records = collector.records()
        assert len(records) == DEFAULT_MAX_RECORDS
        assert records[0].request_id == f"{_TRACE_A}-1"   # 最旧一条被淘汰
        assert records[-1].request_id == f"{_TRACE_A}-{DEFAULT_MAX_RECORDS}"

    async def test_explicit_capacity_fifo(self) -> None:
        collector = InMemoryRagExecutionCollector(max_records=2)
        for trace_id in ("r-1", "r-2", "r-3"):
            with assistant_trace_scope(trace_id):
                await _service(collector).answer(_QUERY)

        assert [r.request_id for r in collector.records()] == ["r-2", "r-3"]
        assert collector.max_records == 2

    async def test_clear_empties_window(self) -> None:
        collector = InMemoryRagExecutionCollector()
        with assistant_trace_scope(_TRACE_A):
            await _service(collector).answer(_QUERY)
        assert len(collector.records()) == 1

        collector.clear()

        assert collector.records() == ()

    async def test_records_by_request_id_exact_match(self) -> None:
        collector = InMemoryRagExecutionCollector()
        for trace_id in (_TRACE_A, _TRACE_B):
            with assistant_trace_scope(trace_id):
                await _service(collector).answer(_QUERY)

        assert [r.request_id for r in collector.records_by_request_id(_TRACE_A)] \
            == [_TRACE_A]
        assert collector.records_by_request_id("unknown") == ()
        with pytest.raises(ValueError):
            collector.records_by_request_id("   ")
        with pytest.raises(TypeError):
            collector.records_by_request_id(None)  # type: ignore[arg-type]

    def test_collector_rejects_non_observation(self) -> None:
        collector = InMemoryRagExecutionCollector()

        with pytest.raises(TypeError):
            collector.record({"request_id": "x"})  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [True, 1.5, "10", None])
    def test_capacity_type_validation(self, bad) -> None:  # noqa: ANN001
        with pytest.raises(TypeError):
            InMemoryRagExecutionCollector(max_records=bad)

    def test_capacity_value_validation(self) -> None:
        with pytest.raises(ValueError):
            InMemoryRagExecutionCollector(max_records=0)


# ============================================================
# 7. Query Boundary（校验先于查询）
# ============================================================

class TestQueryBoundary:
    async def test_lookup_and_latest(self) -> None:
        collector = InMemoryRagExecutionCollector()
        with assistant_trace_scope(_TRACE_A):
            await _service(collector).answer(_QUERY)
        query = RagObservabilityQueryService(collector)

        assert len(query.observations()) == 1
        assert len(query.observations_by_request_id(_TRACE_A)) == 1
        assert query.latest_observation_by_request_id(_TRACE_A) is not None
        assert query.latest_observation_by_request_id("nope") is None
        assert query.collector is collector

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, TypeError),
            (123, TypeError),
            (b"bytes", TypeError),
            ("", ValueError),
            ("   ", ValueError),
            ("A" * 129, ValueError),
        ],
    )
    def test_invalid_request_id_rejected_before_collector(
        self, value, expected
    ) -> None:  # noqa: ANN001
        spy = _SpyCollector()
        query = RagObservabilityQueryService(spy)  # type: ignore[arg-type]

        with pytest.raises(expected):
            query.observations_by_request_id(value)

        assert spy.records_calls == 0                 # collector 未被访问
        assert spy.by_request_id_calls == []

    def test_max_length_128_is_accepted(self) -> None:
        spy = _SpyCollector()
        query = RagObservabilityQueryService(spy)  # type: ignore[arg-type]

        assert query.observations_by_request_id("A" * 128) == ()
        assert spy.by_request_id_calls == ["A" * 128]

    def test_query_service_is_read_only(self) -> None:
        spy = _SpyCollector()
        query = RagObservabilityQueryService(spy)  # type: ignore[arg-type]

        for forbidden in ("record", "clear", "on_execution", "append"):
            assert forbidden not in dir(RagObservabilityQueryService), forbidden

    def test_collector_contract_validation(self) -> None:
        with pytest.raises(TypeError):
            RagObservabilityQueryService(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            RagObservabilityQueryService(object())  # type: ignore[arg-type]


# ============================================================
# 8. Runtime isolation（A / B 不串）
# ============================================================

class TestRuntimeIsolation:
    async def test_two_requests_are_isolated(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(collector)
        query = RagObservabilityQueryService(collector)

        with assistant_trace_scope(_TRACE_A):
            await service.answer(_QUERY)
        with assistant_trace_scope(_TRACE_B):
            await service.answer(_QUERY)

        assert len(collector.records()) == 2
        a = query.latest_observation_by_request_id(_TRACE_A)
        b = query.latest_observation_by_request_id(_TRACE_B)
        assert a is not None and b is not None
        assert a.request_id == _TRACE_A
        assert b.request_id == _TRACE_B
        assert len(query.observations_by_request_id(_TRACE_A)) == 1
        assert len(query.observations_by_request_id(_TRACE_B)) == 1

    async def test_scope_restored_after_request(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(collector)

        with assistant_trace_scope(_TRACE_A):
            await service.answer(_QUERY)
        await service.answer(_QUERY)                  # 已退出 scope

        assert len(collector.records()) == 1


# ============================================================
# 9. Security
# ============================================================

class TestObservationSecurity:
    async def test_field_whitelist(self) -> None:
        collector = InMemoryRagExecutionCollector()
        with assistant_trace_scope(_TRACE_A):
            await _service(collector).answer(_QUERY)

        observation = collector.records()[0]

        assert tuple(f.name for f in fields(RagExecutionObservation)) == (
            _SAFE_FIELDS
        )
        assert tuple(observation.to_dict()) == _SAFE_FIELDS
        for forbidden in _FORBIDDEN_KEYS:
            assert forbidden not in observation.to_dict(), forbidden

    async def test_no_sensitive_values_leaked(self) -> None:
        collector = InMemoryRagExecutionCollector()
        with assistant_trace_scope(_TRACE_A):
            await _service(collector).answer(_QUERY)

        payload = repr(collector.records()[0].to_dict())

        for secret in (_CHUNK_SENTINEL, _QUERY, _LLM_ANSWER, "postgresql://"):
            assert secret not in payload, secret

    def test_observation_module_uses_explicit_mapping(self) -> None:
        source = (
            _REPO_ROOT / "backend/app/services/rag_execution_observation.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        # 只检查**代码**（docstring 里的"禁止 ..."说明不算）
        used = {
            node.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("vars", "__dict__", "asdict", "model_dump"):
            assert forbidden not in used, forbidden
        # 无 DB / 无 ORM / 无持久化
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for forbidden in ("sqlalchemy", "backend.app.db"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    def test_new_modules_have_no_persistence_capability(self) -> None:
        for relative in (
            "backend/app/services/rag_execution_observation.py",
            "backend/app/services/in_memory_rag_execution_collector.py",
            "backend/app/services/rag_observability_query_service.py",
        ):
            source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            identifiers = {
                node.attr.lower()
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            } | {
                node.id.lower()
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
            }
            for forbidden in (
                "insert", "commit", "rollback", "flush", "session", "engine",
                "repository", "traceback",
            ):
                assert forbidden not in identifiers, (relative, forbidden)
            assert "begin(" not in source or relative.endswith(
                "rag_service.py"
            ), relative


# ============================================================
# 10. Determinism
# ============================================================

class TestDeterminism:
    async def test_same_input_stable_observation(self) -> None:
        collector = InMemoryRagExecutionCollector()
        service = _service(
            collector,
            results=[_chunk(3, document_id=7), _chunk(3, document_id=9)],
            context_builder=_EchoContextBuilder(total_chars=55, truncated=True),
        )

        for _ in range(2):
            with assistant_trace_scope(_TRACE_A):
                await service.answer(_QUERY, top_k=2)

        first, second = collector.records()

        def _stable(observation: RagExecutionObservation) -> tuple:
            return (
                observation.request_id,
                observation.result_count,
                observation.used_chunks_count,
                observation.top_k,
                observation.context_truncated,
                observation.context_chars,
                observation.reranker_used,
                observation.rerank_elapsed_ms,
                observation.chunk_ids,
                observation.document_ids,
            )

        assert _stable(first) == _stable(second)
        assert _stable(first) == (
            _TRACE_A, 2, 2, 2, True, 55, False, None, (3,), (7, 9),
        )
        # 时间 / 耗时存在但不参与稳定性断言
        assert isinstance(first.duration_ms, float)
        assert isinstance(second.duration_ms, float)


# ============================================================
# 11. Runtime-only 边界（0 DB / 0 新 API / 0 Trace 修改）
# ============================================================

class TestRuntimeOnlyBoundaries:
    def test_no_new_http_api(self) -> None:
        paths = sorted(app.openapi()["paths"])

        # 观测类端点集合：Step 68 新增 **Timeline** 只读端点（分组投影），
        # 仍未扩大 RAG 侧端点（无 /api/observability/rag*）
        assert [p for p in paths if "observability" in p] == [
            "/api/observability/assistant-timeline/{assistant_request_id}",
            "/api/observability/assistant-trace/{assistant_request_id}",
            "/api/observability/tools",
            "/api/observability/tools/history",
            "/api/observability/tools/metrics",
            "/api/observability/tools/metrics/persistent",
        ]
        # RAG 相关端点仍只有历史 /api/rag/answer
        assert [p for p in paths if "rag" in p.lower()] == ["/api/rag/answer"]

    def test_assistant_trace_api_three_legacy_fields_unchanged(self) -> None:
        """Step 48/64：Trace 响应 additive —— 旧三个字段与语义不变。"""
        schema = app.openapi()["components"]["schemas"][
            "AssistantTraceResponse"
        ]

        properties = list(schema["properties"])
        assert properties[0] == "assistant_request_id"
        assert all(
            name in properties for name in ("llm_usage", "tool_executions")
        )
        assert "rag_executions" in properties          # Step 48（additive）
        assert "outcome" in properties                 # Step 64（additive）

    def test_rag_public_contract_unchanged(self) -> None:
        import inspect

        assert list(inspect.signature(RagService.answer).parameters) == [
            "self", "query", "top_k", "knowledge_scope",
        ]
        assert [f.name for f in fields(RagResponse)] == [
            "answer", "sources", "used_chunks_count",
        ]
        observer_param = inspect.signature(
            RagService.__init__
        ).parameters.get("observer")
        assert observer_param is not None
        assert observer_param.default is None          # 可选（默认不观测）

    def test_one_rag_persistence_table_only(self) -> None:
        """Step 46：仅有契约定义的 1 张 RAG 表；Runtime 侧无持久化能力。"""
        from backend.app.db.base import Base

        assert sorted(
            name for name in Base.metadata.tables if "rag" in name.lower()
        ) == ["ai_ops.rag_execution_record"]
        # API 模块本身**不**持有 RAG Collector（Step 44 的生产接线放在
        # Service 层 runtime 装配模块；Composition Root 只注入已接线实例）
        assert not hasattr(root, "_RAG_EXECUTION_COLLECTOR")

    def test_rag_service_isolates_observer_failures_statically(self) -> None:
        source = (
            _REPO_ROOT / "backend/app/services/rag_service.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        finish = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_finish_observation"
        )

        assert any(
            isinstance(node, ast.Try) for node in ast.walk(finish)
        ), "观测提交必须被 try 包裹（best-effort）"
        assert not [
            node
            for node in ast.walk(finish)
            if isinstance(node, ast.Raise)
        ], "观测路径不得 raise"


__all__ = [
    "TestObservationLifecycle",
    "TestIdentifierExtraction",
    "TestCollectorBoundaries",
    "TestQueryBoundary",
    "TestRuntimeIsolation",
    "TestObservationSecurity",
    "TestDeterminism",
    "TestRuntimeOnlyBoundaries",
]
