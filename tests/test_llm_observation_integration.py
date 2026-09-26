"""LLM Observation Integration — 业务链路评估（Phase 3.10.8）。

验证模型（任务书 §三）：**request-level Observation**

    一个实际 LLM Provider request  ↔  一个 Observation

覆盖链路：

    Test 1  RAG：            RAG → LLM → Answer          （1 request / 1 observation）
    Test 2  Tool Calling：   LLM → Tool → LLM            （2 requests / 2 observations）
    Test 3  T2S normal：     T2S → LLM → Validator       （1 request / 1 observation）
    Test 4  T2S retry：      LLM#1 → reject → LLM#2      （2 requests / 2 observations）
    Test 5  Refusal：        LLM → refusal               （1 request / 1 observation / validator=0）
    Test 6  Router：         规则命中 0 LLM；fallback 1 LLM
    Test 7  Orchestrator：   request_count == observation_count
    §十     失败链路：        原始异常保留 + failure observation
    §十三/十四  sink 一致性：No-op 与 Collecting 业务结果一致
    §十五   并发：            5 并发请求 → 5 个互异 Observation
    §十二   敏感信息：        prompt / SQL / chunk / tool arg / error message 不泄漏

全部使用 Fake / MockTransport / 内存 sink：0 网络 / 0 DB。
不修改任何生产业务代码。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

import httpx
import pytest

from backend.app.llm.client import (
    LLMRequestError,
    LLMResponse,
    OpenAICompatibleClient,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import (
    LLMObservation,
    NoopObservationSink,
)
from backend.app.projects.context import DataSource, ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.ai_orchestrator_service import AIOrchestratorService
from backend.app.services.ai_router_service import (
    AIRouterService,
    InMemoryToolCapabilityRegistry,
    RouteType,
    ToolCapability,
)
from backend.app.services.rag_service import RagService
from backend.app.services.relevant_table_selector import (
    TableSelection,
    TableSelectionResult,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.text_to_sql_service import (
    REFUSAL_MARKER,
    TextToSQLService,
)
from backend.app.services.vector_search_service import VectorSearchResult
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry


# ============================================================
# 测试基础设施（仅测试使用；不持久化、不外发）
# ============================================================

class CollectingObservationSink:
    """内存收集 sink（仅测试断言用，不进生产代码）。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)


class FailingObservationSink:
    """故意失败的 sink（模拟观测层故障）。"""

    def record(self, observation: LLMObservation) -> None:
        raise RuntimeError("observation sink failure")


class ScriptedHandler:
    """按脚本回放 OpenAI 兼容响应；记录请求 body 供 request 计数。"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        body = (
            self._responses.pop(0)
            if len(self._responses) > 1
            else self._responses[0]
        )
        return httpx.Response(200, json=body)

    @property
    def request_count(self) -> int:
        return len(self.requests)


def _obs_provider(handler: Any, sink: Any = None) -> DeepSeekProvider:
    """真实 Client + MockTransport + sink → DeepSeekProvider（生产同构链路）。"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )
    return DeepSeekProvider(client)


def _content_body(
    content: str,
    *,
    request_id: str = "chatcmpl-obs-1",
    finish_reason: str = "stop",
    with_usage: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if with_usage:
        body["usage"] = {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        }
    return body


def _tool_call_body(*, request_id: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_inventory",
                                "arguments": '{"material_code": "MAT001"}',
                            },
                        }
                    ],
                },
            }
        ],
    }


def assert_observation_valid(
    observation: LLMObservation, *, success: bool
) -> None:
    """统一 Observation 断言（§八：简单，不建框架）。"""
    assert isinstance(observation, LLMObservation)
    assert observation.success is success
    assert observation.latency_ms is None or observation.latency_ms >= 0


def assert_observation_matches_response(
    observation: LLMObservation, response: LLMResponse
) -> None:
    """Observation 字段与对应 LLMResponse 一致（各自 request 独立）。"""
    assert observation.model == response.model
    assert observation.finish_reason == response.finish_reason
    assert observation.usage is response.usage
    assert observation.request_id == response.metadata.get("request_id")
    assert observation.provider == response.metadata.get("provider")


# ============================================================
# 业务依赖 Fake（最小实现）
# ============================================================

class FakeVectorSearch:
    """VectorSearchService 替身（内存返回，无 DB）。"""

    def __init__(self, results: list[VectorSearchResult]) -> None:
        self._results = results
        self.search_calls: list[str] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self.search_calls.append(query)
        return list(self._results)


class _GuardValidator:
    """Refusal 守卫 Validator：被调用即失败（validator=0 断言）。"""

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, sql: str, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("refusal 路径不应进入 Validator")


# ============================================================
# Test 1 — RAG：RAG → LLM → Answer
# ============================================================

async def test_rag_single_request_single_observation() -> None:
    """RAG 链路：1 个 LLM request → 恰好 1 个 Observation（success=True）。"""
    handler = ScriptedHandler([_content_body("采购入库需要先创建入库单。")])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    rag = RagService(
        vector_search_service=FakeVectorSearch([
            VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0,
                content="入库流程说明", distance=0.1, similarity=0.9,
                metadata={},
            )
        ]),
        llm_client=provider,
    )

    response = await rag.answer("采购入库的流程是什么？")

    assert "入库单" in response.answer
    assert handler.request_count == 1          # 实际 LLM request 数
    assert len(sink.observations) == 1         # observation == request
    observation = sink.observations[0]
    assert_observation_valid(observation, success=True)
    # Usage Visibility（3.10.12）：str 路径 observation 携带实际响应元数据
    assert observation.provider == "deepseek-test"
    assert observation.model == "deepseek-chat"      # 实际响应（非配置值）
    assert observation.finish_reason == "stop"
    assert observation.request_id == "chatcmpl-obs-1"
    assert observation.usage is None                 # 响应未带 usage → None
    assert observation.error_type is None


async def test_rag_empty_results_zero_observations() -> None:
    """RAG 空检索：不调 LLM → 0 request → 0 observation（无遗漏亦无虚构）。"""
    handler = ScriptedHandler([_content_body("不应被调用")])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    rag = RagService(
        vector_search_service=FakeVectorSearch([]),
        llm_client=provider,
    )

    response = await rag.answer("完全不相关的问题")

    assert "没有找到" in response.answer
    assert handler.request_count == 0
    assert sink.observations == []


# ============================================================
# Test 2 — Tool Calling：LLM → Tool → LLM
# ============================================================

async def test_tool_calling_two_requests_two_observations() -> None:
    """Tool Calling（2 次 LLM request）→ 2 个 Observation，
    各自的 finish_reason / request_id 独立对应（#1 ≠ #2）。"""
    handler = ScriptedHandler([
        _tool_call_body(request_id="chatcmpl-tool-req-1"),
        _content_body("MAT001 当前库存 1000 PCS。", request_id="chatcmpl-tool-req-2"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    registry = ToolRegistry()
    register_mock_tools(registry)

    from backend.app.services.tool_chat_service import ToolChatService

    result = await ToolChatService(provider).chat(
        "帮我查询 MAT001 的库存", registry=registry,
    )

    assert result.tool_calls[0].tool_name == "get_inventory"
    assert "1000" in result.answer
    assert handler.request_count == 2
    assert len(sink.observations) == 2
    first, second = sink.observations
    # Observation #1 ≠ #2（request-level，各自独立）
    assert first is not second
    assert first.request_id == "chatcmpl-tool-req-1"
    assert second.request_id == "chatcmpl-tool-req-2"
    assert first.finish_reason == "tool_calls"   # 来自 request #1
    assert second.finish_reason == "stop"        # 来自 request #2
    assert_observation_valid(first, success=True)
    assert_observation_valid(second, success=True)


# ============================================================
# Test 3 — T2S normal
# ============================================================

async def test_t2s_normal_single_observation() -> None:
    """T2S 正常生成：1 LLM request → Validator 通过 → 1 Observation。"""
    handler = ScriptedHandler([
        _content_body("```sql\nSELECT 1 LIMIT 1\n```", request_id="chatcmpl-t2s-1"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider)

    result = await t2s.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.sql == "SELECT 1 LIMIT 1"
    assert handler.request_count == 1
    assert len(sink.observations) == 1
    assert_observation_valid(sink.observations[0], success=True)
    # Usage Visibility（3.10.12）：str 路径携带实际响应元数据
    assert sink.observations[0].provider == "deepseek-test"
    assert sink.observations[0].request_id == "chatcmpl-t2s-1"
    assert sink.observations[0].usage is None        # 响应未带 usage


# ============================================================
# Test 4 — T2S semantic retry：每个实际 request 都有 Observation
# ============================================================

async def test_t2s_semantic_retry_two_observations() -> None:
    """semantic retry：LLM#1 产出非法 SQL（Validator 拒绝）→ LLM#2 通过
    → 2 个 Observation（不是只有最后一次）；request-level 区分用
    fake 提供的不同 request_id（不伪造）。"""
    handler = ScriptedHandler([
        _content_body("SELECT 1", request_id="chatcmpl-t2s-try-1"),  # 缺 LIMIT → 拒绝
        _content_body("```sql\nSELECT 1 LIMIT 1\n```",
                      request_id="chatcmpl-t2s-try-2"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=3)

    result = await t2s.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.attempts == 2                     # 语义 retry 1 次
    assert handler.request_count == 2
    assert len(sink.observations) == 2              # 每个实际 request 一个
    obs1, obs2 = sink.observations
    assert obs1 is not obs2                         # 两个独立 Observation（非复用）
    # Usage Visibility（3.10.12）：request_id 现在可见且互异
    # （request-level 区分更强：独立对象 + 独立 request_id）
    assert obs1.request_id == "chatcmpl-t2s-try-1"
    assert obs2.request_id == "chatcmpl-t2s-try-2"
    assert obs1.request_id != obs2.request_id
    assert obs1.latency_ms is not None and obs2.latency_ms is not None
    assert_observation_valid(obs1, success=True)
    assert_observation_valid(obs2, success=True)
    # retry 不携带 retry_count 字段（3.10.6 DTO 未被修改）
    assert not hasattr(obs1, "retry_count")


# ============================================================
# Test 5 — Refusal：LLM calls = 1 / Observation = 1 / validator = 0
# ============================================================

async def test_refusal_single_observation_success() -> None:
    """Refusal：LLM 成功返回 refusal（业务结果，非调用失败）→
    success=True、恰好 1 个 Observation、Validator=0。"""
    handler = ScriptedHandler([
        _content_body(REFUSAL_MARKER, request_id="chatcmpl-refusal-1"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    validator = _GuardValidator()
    t2s = TextToSQLService(llm_client=provider, validator=validator)

    result = await t2s.generate("删除所有库存", database_context="ctx")

    assert result.status == "refusal"
    assert result.attempts == 1
    assert validator.calls == 0
    assert handler.request_count == 1
    assert len(sink.observations) == 1
    # refusal 是业务结果：LLM 调用本身成功
    assert_observation_valid(sink.observations[0], success=True)


# ============================================================
# Test 6 — Router：规则命中 0 LLM；fallback 1 LLM；分类不被 sink 改变
# ============================================================

def _tool_capabilities() -> InMemoryToolCapabilityRegistry:
    return InMemoryToolCapabilityRegistry([
        ToolCapability(
            name="get_inventory",
            description="查询库存数量",
            aliases=("查库存",),
        )
    ])


async def test_router_rule_hits_produce_zero_observations() -> None:
    """规则命中（TOOL / T2S / RAG）→ 0 LLM request → 0 Observation。"""
    handler = ScriptedHandler([_content_body('{"route": "rag"}')])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    router = AIRouterService(
        llm_client=provider,
        tool_capabilities=_tool_capabilities(),
    )

    tool_decision = await router.route("请帮我查库存")
    t2s_decision = await router.route("库存有多少个？")
    rag_decision = await router.route("采购入库的操作流程是什么？")

    assert tool_decision.route == RouteType.TOOL
    assert tool_decision.source == "tool_match"
    assert t2s_decision.route == RouteType.TEXT_TO_SQL
    assert t2s_decision.source == "rule"
    assert rag_decision.route == RouteType.RAG
    assert rag_decision.source == "rule"
    # 三个问题全部规则命中：0 次 LLM fallback → 0 Observation
    assert handler.request_count == 0
    assert sink.observations == []


async def test_router_llm_fallback_single_observation() -> None:
    """无法归类的问题 → LLM fallback（1 request）→ 1 Observation，
    route 分类正常。"""
    handler = ScriptedHandler([
        _content_body('{"route": "rag", "reason": "general conversation"}',
                      request_id="chatcmpl-router-1"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    router = AIRouterService(
        llm_client=provider,
        tool_capabilities=_tool_capabilities(),
    )

    decision = await router.route("asdfghjkl12345")

    assert decision.route == RouteType.RAG
    assert decision.source == "llm"
    assert handler.request_count == 1
    assert len(sink.observations) == 1
    assert_observation_valid(sink.observations[0], success=True)


# ============================================================
# Test 7 — Orchestrator：request_count == observation_count
# ============================================================

def _make_project() -> ProjectContext:
    return ProjectContext(
        project_id="test-project",
        project_name="Test Project",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def _make_schema() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name="public",
        tables=(
            SchemaTable(
                schema_name="public", name="inventory", description=None,
                columns=(
                    SchemaColumn(name="id", data_type="bigint", nullable=False,
                                 default=None, ordinal_position=1,
                                 is_primary_key=True, description=None),
                ),
                foreign_keys=(),
            ),
        ),
    )


class _FakeTableSelector:
    def select(self, question, *, schema, semantic, top_k=5):
        return TableSelectionResult(question=question, selections=())


class _FakeContextComposer:
    def compose(self, *, project, schema, semantic, tables=None, max_chars=4000):
        return "FAKE_CONTEXT"


class _FakeProjectProvider:
    def resolve(self):
        return _make_project(), _make_schema(), ProjectSemantic()


async def test_orchestrator_request_count_equals_observation_count() -> None:
    """Orchestrator 两条路径：
    规则命中 RAG → router 0 LLM + rag 1 LLM = 1 observation；
    无法归类 → router LLM fallback 1 + rag 1 = 2 observations。
    总 observation 数 == 总实际 LLM request 数。"""
    sink = CollectingObservationSink()
    # 同一 sink 共享给 router 与 rag 的 LLM client（跨层汇总观测）
    router_provider = _obs_provider(
        ScriptedHandler([
            _content_body('{"route": "rag", "reason": "fallback"}',
                          request_id="chatcmpl-router-fb"),
        ]),
        sink,
    )
    rag_provider = _obs_provider(
        ScriptedHandler([
            _content_body("知识库回答 A。", request_id="chatcmpl-rag-1"),
            _content_body("知识库回答 B。", request_id="chatcmpl-rag-2"),
        ]),
        sink,
    )
    rag = RagService(
        vector_search_service=FakeVectorSearch([
            VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0,
                content="kb content", distance=0.1, similarity=0.9,
                metadata={},
            )
        ]),
        llm_client=rag_provider,
    )
    orchestrator = AIOrchestratorService(
        router=AIRouterService(
            llm_client=router_provider,
            tool_capabilities=_tool_capabilities(),
        ),
        rag_service=rag,
    )

    # ---- 路径 1：规则命中 RAG → router 0 LLM + rag 1 LLM ----
    result1 = await orchestrator.execute("采购入库的操作流程是什么？")
    assert result1.route == RouteType.RAG
    assert len(sink.observations) == 1

    # ---- 路径 2：无法归类 → router fallback 1 LLM + rag 1 LLM ----
    result2 = await orchestrator.execute("asdfghjkl12345")
    assert result2.route == RouteType.RAG  # LLM fallback → rag
    assert len(sink.observations) == 3     # router 1 + rag 1（累计 1+2）
    # Usage Visibility（3.10.12）：request_id 可见且互异
    # （request/observation 对应关系：数量 + 独立 request_id + 独立对象）
    request_ids = [obs.request_id for obs in sink.observations]
    assert request_ids == [
        "chatcmpl-rag-1",        # 路径 1 的 RAG
        "chatcmpl-router-fb",    # 路径 2 的 router fallback
        "chatcmpl-rag-2",        # 路径 2 的 RAG
    ]
    assert len({id(obs) for obs in sink.observations}) == 3
    assert all(obs.success for obs in sink.observations)
    assert all(obs.provider == "deepseek-test" for obs in sink.observations)


# ============================================================
# §十 失败链路：原始异常保留 + failure observation
# ============================================================

async def test_rag_llm_failure_preserves_exception_and_observes() -> None:
    """RAG 链路中 LLM 失败：LLMRequestError 原样透传给业务层 +
    failure Observation（success=False / error_type 纯类名）。"""
    sink = CollectingObservationSink()

    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "upstream down"})

    provider = _obs_provider(handler_503, sink)
    rag = RagService(
        vector_search_service=FakeVectorSearch([
            VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0,
                content="kb", distance=0.1, similarity=0.9, metadata={},
            )
        ]),
        llm_client=provider,
    )

    with pytest.raises(LLMRequestError) as exc_info:
        await rag.answer("采购入库的流程是什么？")

    assert "SECRET-ERROR-DETAIL" not in str(exc_info.value)
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert_observation_valid(observation, success=False)
    assert observation.error_type == "LLMRequestError"


def _obs_provider(handler, sink) -> DeepSeekProvider:
    from backend.app.llm.client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )
    return DeepSeekProvider(client)


async def test_t2s_llm_failure_observation_and_reraise() -> None:
    """T2S 链路 LLM 失败：异常原样传播 + failure observation。"""

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    sink = CollectingObservationSink()
    provider = _obs_provider(handler_500, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=2)

    with pytest.raises(LLMRequestError):
        await t2s.generate("知识文档有哪些？", database_context="ctx")

    # LLMError 由 T2S 直接透传（不进入语义 retry）→ 1 次请求 1 个 observation；
    # 无 transport retry（Phase 3.10.2 边界不变）
    assert len(sink.observations) == 1
    assert sink.observations[0].success is False
    assert sink.observations[0].error_type == "LLMRequestError"


async def test_sink_failure_does_not_override_business_exception() -> None:
    """sink 故障不覆盖原始业务异常（异常仍为 LLMRequestError）。"""
    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "x"})

    provider = _obs_provider(handler_503, FailingObservationSink())
    rag = RagService(
        vector_search_service=FakeVectorSearch([
            VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0,
                content="kb", distance=0.1, similarity=0.9, metadata={},
            )
        ]),
        llm_client=provider,
    )
    with pytest.raises(LLMRequestError):
        await rag.answer("采购入库的流程是什么？")


# ============================================================
# §十三 / §十四 Sink 行为一致性
# ============================================================

async def test_noop_and_collecting_sink_same_business_result() -> None:
    """同一请求：No-op sink 与 Collecting sink 的业务结果完全一致
    （返回值 / SQL 不因 sink 存在而改变）。"""
    def handler_factory():
        return ScriptedHandler([
            _content_body("```sql\nSELECT 1 LIMIT 1\n```"),
        ])

    t2s_noop = TextToSQLService(
        llm_client=_obs_provider(handler_factory(), None),
    )
    t2s_collecting = TextToSQLService(
        llm_client=_obs_provider(handler_factory(), CollectingObservationSink()),
    )

    result_noop = await t2s_noop.generate("知识文档有哪些？", database_context="ctx")
    result_collecting = await t2s_collecting.generate(
        "知识文档有哪些？", database_context="ctx",
    )

    assert result_noop.sql == result_collecting.sql == "SELECT 1 LIMIT 1"
    assert result_noop.validated is result_collecting.validated is True
    assert result_noop.attempts == result_collecting.attempts


async def test_default_client_without_sink_still_works() -> None:
    """生产默认（observation_sink=None → NoopObservationSink）：
    调用正常、结果不变。"""
    from backend.app.llm.client import OpenAICompatibleClient

    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_content_body("plain ok"))
        ),
        # observation_sink 不传 → NoopObservationSink
    )
    provider = DeepSeekProvider(client)
    answer = await provider.generate("ping")
    assert answer == "plain ok"


# ============================================================
# §十五 并发回归（业务层）
# ============================================================

async def test_parallel_t2s_requests_no_observation_cross_talk() -> None:
    """5 个并发 T2S 请求 → 5 个 Observation，request_id 互不相同
    （无交叉覆盖 / 无 model 交叉 / 无 success 状态交叉）。"""
    sink = CollectingObservationSink()
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        n = counter["n"]  # asyncio 单线程内同步分配，无 race
        return httpx.Response(
            200,
            json=_content_body(
                f"```sql\nSELECT {n} LIMIT 1\n```",
                request_id=f"chatcmpl-parallel-{n}",
            ),
        )

    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=1)

    results = await asyncio.gather(
        *[
            t2s.generate(f"问题 {i}", database_context="ctx")
            for i in range(5)
        ]
    )

    assert len(sink.observations) == 5
    # Usage Visibility（3.10.12）：request_id 可见 → 互异性直接断言
    request_ids = [obs.request_id for obs in sink.observations]
    assert len(set(request_ids)) == 5                    # 无交叉
    assert request_ids == [f"chatcmpl-parallel-{n}" for n in range(1, 6)]
    assert all(obs.success for obs in sink.observations)  # 状态无交叉
    assert all(obs.model == "deepseek-chat" for obs in sink.observations)
    assert all(r.validated for r in results)
    assert {r.sql for r in results} == {
        f"SELECT {n} LIMIT 1" for n in range(1, 6)
    }


# ============================================================
# §十二 敏感信息：无 prompt / SQL / chunk / tool arg / secrets 泄漏
# ============================================================

async def test_observations_free_of_business_content_and_secrets() -> None:
    """Observation 不含：user prompt / SQL / RAG chunk / tool arguments /
    tool results / exception message；error_type 只含类名。"""
    import dataclasses

    sink = CollectingObservationSink()

    # 1) T2S：question 与 SQL 均带标记
    t2s_handler = ScriptedHandler([
        _content_body("```sql\nSELECT 'SECRET-SQL-CONTENT' LIMIT 1\n```",
                      request_id="chatcmpl-sec-1"),
    ])
    t2s = TextToSQLService(
        llm_client=_obs_provider(t2s_handler, sink),
        max_attempts=1,
    )
    t2s_result = await t2s.generate(
        "SECRET-QUESTION-CONTENT 有多少？", database_context="SECRET-DB-CONTEXT",
    )
    assert t2s_result.validated

    # 2) Tool calling：arguments 带标记
    tool_sink = CollectingObservationSink()
    tool_provider = _obs_provider(
        ScriptedHandler([
            _tool_call_body(request_id="chatcmpl-sec-2"),
            _content_body("done", request_id="chatcmpl-sec-3"),
        ]),
        tool_sink,
    )
    registry = ToolRegistry()
    register_mock_tools(registry)
    from backend.app.services.tool_chat_service import ToolChatService

    await ToolChatService(tool_provider).chat(
        "SECRET-USER-MESSAGE 查询 SECRET-TOOL-ARG-MAT 的库存",
        registry=registry,
    )

    # 3) 失败：exception message 带标记（LLMRequestError message 不进 Observation）
    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "SECRET-ERROR-MSG"})

    fail_sink = CollectingObservationSink()
    fail_provider = _obs_provider(handler_503, fail_sink)
    with pytest.raises(LLMRequestError):
        await fail_provider.generate("SECRET-FAILED-PROMPT")

    all_observations = [
        *sink.observations, *tool_sink.observations, *fail_sink.observations,
    ]
    assert len(all_observations) >= 4
    forbidden = (
        "SECRET-QUESTION-CONTENT", "SECRET-SQL-CONTENT", "SECRET-DB-CONTEXT",
        "SECRET-USER-MESSAGE", "SECRET-TOOL-ARG-MAT", "SECRET-FAILED-PROMPT",
        "SECRET-ERROR-MSG", "MAT001",
    )
    for observation in all_observations:
        as_dict = dataclasses.asdict(observation)
        # 字段白名单（3.10.6 DTO 未扩大）
        assert set(as_dict) <= {
            "provider", "model", "latency_ms", "success",
            "finish_reason", "usage", "request_id", "error_type",
        }
        for value in as_dict.values():
            if isinstance(value, str):
                for secret in forbidden:
                    assert secret not in value
    # 失败 observation 的 error_type 是纯类名
    failure_obs = [o for o in fail_sink.observations][0]
    assert failure_obs.error_type == "LLMRequestError"
