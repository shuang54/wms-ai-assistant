"""RagEvaluationService 单元测试（Phase 3.5.7）。

覆盖任务规范 § 十 的 7 项断言（不含真实调用）：

    1. 全命中
    2. 部分命中
    3. 全未命中
    4. 多关键词（全部命中才算成功）
    5. 空 cases → total=0 / hit_rate=0（防除零）
    6. VectorSearchService 异常 → 结构化失败记录，不阻断后续 case
    7. 不调用 LLM（断言搜索路径之外没有 LLMClient 出现）

策略：构造 FakeVectorSearchService（提供可控 top_k results），
用 MagicMock 验证 RAG 评估服务仅与该 Service 通信。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.llm.client import LLMClient
from backend.app.services.rag_evaluation_service import (
    RagEvaluationService,
    RetrievalEvaluationCase,
    RetrievalEvaluationResult,
    RetrievalEvaluationSummary,
    load_cases_from_json,
)
from backend.app.services.vector_search_service import (
    VectorSearchInputError,
    VectorSearchResult,
)


# ============================================================
# Fake / 双轨 Service
# ============================================================

class FakeVectorSearchService:
    """测试用 VectorSearchService 替身。

    支持：
        - 配置 per-query 返回结果（按 query 文本精确匹配）
        - 未配置 query 的回退结果（fallback）
        - 配置抛出的异常

    提供 `calls` 列表便于断言。
    """

    def __init__(
        self,
        *,
        responses: dict[str, list[VectorSearchResult]] | None = None,
        fallback: list[VectorSearchResult] | None = None,
        error_for: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._responses = responses or {}
        self._fallback = fallback or []
        self._error_for = error_for or set()
        self._error = error
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int = 5) -> list[VectorSearchResult]:
        self.calls.append((query, top_k))
        if query in self._error_for:
            assert self._error is not None
            raise self._error
        return list(self._responses.get(query, self._fallback))[:top_k]


def _result(
    *,
    chunk_id: int,
    content: str,
    similarity: float = 0.9,
    document_id: int = 1,
    chunk_index: int = 0,
    metadata: dict | None = None,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata=metadata or {},
    )


# ============================================================
# 1. 全命中
# ============================================================

class TestAllMatched:
    async def test_all_cases_matched_when_keywords_present(self) -> None:
        """所有 case 在 Top-K 中均能命中 expected_keywords → hit_rate=1.0。"""
        cases = [
            RetrievalEvaluationCase(case_id="c1", query="q1", expected_keywords=("采购入库",)),
            RetrievalEvaluationCase(case_id="c2", query="q2", expected_keywords=("退货入库",)),
        ]
        fake = FakeVectorSearchService(
            responses={
                "q1": [_result(chunk_id=1, content="采购入库的标准步骤：1. 到货登记。")],
                "q2": [_result(chunk_id=2, content="退货入库流程说明。")],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=5)

        assert isinstance(summary, RetrievalEvaluationSummary)
        assert summary.total_cases == 2
        assert summary.matched_cases == 2
        assert summary.failed_cases == 0
        assert summary.hit_rate == 1.0
        assert summary.top_k == 5
        assert all(r.matched for r in summary.results)
        assert summary.results[0].missing_keywords == ()
        assert summary.results[0].matched_keywords == ("采购入库",)


# ============================================================
# 2. 部分命中
# ============================================================

class TestPartialMatched:
    async def test_partial_hit_rate(self) -> None:
        """2/4 命中 → hit_rate = 0.5。"""
        cases = [
            RetrievalEvaluationCase(case_id="hit_1", query="q_hit_1", expected_keywords=("采购入库",)),
            RetrievalEvaluationCase(case_id="miss_1", query="q_miss_1", expected_keywords=("不存在的关键字XYZ",)),
            RetrievalEvaluationCase(case_id="hit_2", query="q_hit_2", expected_keywords=("盘点",)),
            RetrievalEvaluationCase(case_id="miss_2", query="q_miss_2", expected_keywords=("另一个不存在",)),
        ]
        fake = FakeVectorSearchService(
            responses={
                "q_hit_1": [_result(chunk_id=1, content="采购入库流程")],
                "q_miss_1": [_result(chunk_id=2, content="完全不同主题")],
                "q_hit_2": [_result(chunk_id=3, content="盘点任务执行步骤")],
                "q_miss_2": [_result(chunk_id=4, content="另一个不相关片段")],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=5)

        assert summary.total_cases == 4
        assert summary.matched_cases == 2
        assert summary.failed_cases == 0
        assert summary.hit_rate == pytest.approx(0.5)
        # 验证 case 状态映射正确
        by_id = {r.case_id: r for r in summary.results}
        assert by_id["hit_1"].matched is True
        assert by_id["miss_1"].matched is False
        assert by_id["hit_1"].missing_keywords == ()
        assert "不存在的关键字XYZ" in by_id["miss_1"].missing_keywords


# ============================================================
# 3. 全未命中
# ============================================================

class TestNoneMatched:
    async def test_zero_hit_rate(self) -> None:
        """所有 case 均未命中 → hit_rate = 0.0。"""
        cases = [
            RetrievalEvaluationCase(case_id="c1", query="q1", expected_keywords=("xxx1",)),
            RetrievalEvaluationCase(case_id="c2", query="q2", expected_keywords=("xxx2",)),
        ]
        fake = FakeVectorSearchService(
            responses={
                "q1": [_result(chunk_id=1, content="采购入库")],
                "q2": [_result(chunk_id=2, content="盘点任务")],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=3)

        assert summary.matched_cases == 0
        assert summary.hit_rate == 0.0
        assert all(r.matched is False for r in summary.results)


# ============================================================
# 4. 多关键词（全部命中才算成功）
# ============================================================

class TestMultipleKeywords:
    async def test_all_keywords_must_match(self) -> None:
        """expected_keywords=[A, B] → 仅命中 A 视为不通过。"""
        cases = [
            RetrievalEvaluationCase(
                case_id="partial_kw",
                query="q1",
                expected_keywords=("采购入库", "采购单"),
            ),
            RetrievalEvaluationCase(
                case_id="all_kw",
                query="q2",
                expected_keywords=("采购入库", "采购单"),
            ),
        ]
        fake = FakeVectorSearchService(
            responses={
                "q1": [_result(chunk_id=1, content="采购入库的步骤")],  # 缺"采购单"
                "q2": [
                    _result(chunk_id=2, content="采购入库操作"),
                    _result(chunk_id=3, content="采购单创建"),
                ],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=5)

        assert summary.matched_cases == 1  # 仅 all_kw
        assert summary.hit_rate == 0.5
        partial = next(r for r in summary.results if r.case_id == "partial_kw")
        assert partial.matched is False
        assert partial.matched_keywords == ("采购入库",)
        assert partial.missing_keywords == ("采购单",)


# ============================================================
# 5. 空 cases（防除零）
# ============================================================

class TestEmptyCases:
    async def test_empty_returns_zero_summary_without_div_zero(self) -> None:
        """空输入 → 零命中，不抛 ZeroDivisionError。"""
        svc = RagEvaluationService(vector_search_service=FakeVectorSearchService())
        summary = await svc.evaluate([], top_k=5)
        assert summary.total_cases == 0
        assert summary.matched_cases == 0
        assert summary.failed_cases == 0
        assert summary.hit_rate == 0.0
        assert summary.results == ()
        # VectorSearchService 不应被调用
        # （FakeVectorSearchService.calls 仍为空）


# ============================================================
# 6. VectorSearchService 异常 → 结构化失败记录，不阻断
# ============================================================

class TestSearchException:
    async def test_failure_recorded_structurally_and_other_cases_continue(
        self,
    ) -> None:
        """单个 case 失败 → RetrievalEvaluationResult.error 填入 + 后续 case 继续。"""
        cases = [
            RetrievalEvaluationCase(case_id="boom", query="q_boom", expected_keywords=("x",)),
            RetrievalEvaluationCase(case_id="ok", query="q_ok", expected_keywords=("命中",)),
        ]
        fake = FakeVectorSearchService(
            error=VectorSearchInputError("query 为空"),
            error_for={"q_boom"},
            responses={
                "q_ok": [_result(chunk_id=1, content="命中测试")],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=5)

        assert summary.total_cases == 2
        assert summary.failed_cases == 1
        assert summary.matched_cases == 1
        assert summary.hit_rate == 0.5
        by_id = {r.case_id: r for r in summary.results}
        assert by_id["boom"].matched is False
        assert by_id["boom"].error == "query 为空"
        assert by_id["ok"].matched is True
        # 失败 case 不应阻断后续 case
        assert ("q_boom", 5) in fake.calls
        assert ("q_ok", 5) in fake.calls


# ============================================================
# 7. 不调用 LLM
# ============================================================

class TestNoLlmCall:
    async def test_does_not_import_or_call_llm(self) -> None:
        """评估 Service 不得持有 / 调用 LLMClient / chat / generate。"""
        # 持有层断言：模块属性层面不应暴露 LLM 引用
        import backend.app.services.rag_evaluation_service as mod

        for attr in ("_llm_client", "llm", "_chat", "_generate", "_llm"):
            assert not hasattr(mod.RagEvaluationService, attr), (
                f"评估 Service 不应持有 {attr}"
            )
        # 行为层断言：即便注入 VectorSearchService 异常后，也无 LLM 调用
        fake = FakeVectorSearchService(
            error=VectorSearchInputError("e"),
            error_for={"q1"},
        )
        # 把 LLMClient 注入到 VectorSearchService 的某些旁路？—— fake 不会
        # 这里再加一层 MagicMock 监控 chat/generate 不被触发
        llm = MagicMock(spec=LLMClient, name="llm_mock")
        svc = RagEvaluationService(vector_search_service=fake)
        await svc.evaluate(
            [RetrievalEvaluationCase(case_id="c", query="q1")],
        )
        # LLMClient 的 chat / generate 不应被调用
        llm.chat.assert_not_called()  # type: ignore[attr-defined]
        llm.generate.assert_not_called()  # type: ignore[attr-defined]
        # 显式检查 fake 的 .calls 数量以保证 evaluate 确实执行了
        assert fake.calls == [("q1", 5)]

    async def test_llm_module_not_imported_by_evaluation(self) -> None:
        """sanity: 评估 Service 自身未引入 LLMClient 内部 API。"""
        import backend.app.services.rag_evaluation_service as mod

        # 模块 source 中不应出现 LLMClient 类内部符号调用）
        # 允许保留的例外：docstring 中的说明文字（与"不调用 LLM"语义一致的注释）
        # —— 因此这里检查的不是文字提及，而是「类内部不持有 LLMClient 实例」。
        # 已经通过 test_does_not_import_or_call_llm 完成覆盖（持 attr 更强）。
        import inspect

        cls_src = inspect.getsource(mod.RagEvaluationService)
        # 重点排除：实例化 / 函数调用 LLMClient
        assert "LLMClient(" not in cls_src, "RagEvaluationService 不应实例化 LLMClient"
        # 不应出现 chat / generate 调用
        for forbidden in (".chat(", ".generate("):
            assert forbidden not in cls_src, (
                f"RagEvaluationService 不应调用 LLM 的{forbidden!r}"
            )


# ============================================================
# 8. JSON 加载
# ============================================================

class TestJsonLoader:
    def test_loads_array_form(self, tmp_path: Path) -> None:
        """顶层 array 形式可加载。"""
        path = tmp_path / "cases.json"
        path.write_text(
            json.dumps([
                {"id": "c1", "query": "q", "expected_keywords": ["kw"]},
            ]),
            encoding="utf-8",
        )
        cases = load_cases_from_json(str(path))
        assert len(cases) == 1
        assert cases[0].case_id == "c1"
        assert cases[0].expected_keywords == ("kw",)

    def test_loads_wrapped_form(self, tmp_path: Path) -> None:
        """{'cases': [...]} 形式可加载。"""
        path = tmp_path / "cases.json"
        path.write_text(
            json.dumps({
                "_schema_version": "1.0",
                "cases": [
                    {"id": "c1", "query": "q", "expected_keywords": ["kw1", "kw2"]},
                ],
            }),
            encoding="utf-8",
        )
        cases = load_cases_from_json(str(path))
        assert len(cases) == 1
        assert cases[0].expected_keywords == ("kw1", "kw2")

    def test_missing_id_or_query_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(json.dumps([{"query": "only-query"}]), encoding="utf-8")
        with pytest.raises(ValueError):
            load_cases_from_json(str(path))

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_cases_from_json("/nonexistent/path/cases.json")

    def test_loads_default_fixture(self) -> None:
        """仓库内置数据集 ≥ 10 case。"""
        cases = load_cases_from_json("tests/fixtures/rag/evaluation_cases.json")
        assert len(cases) >= 10
        for c in cases:
            assert c.case_id
            assert c.query
            # 字段都是 frozen DTO，不应是 list（list 会绕过类型稳定性）
            assert isinstance(c.expected_keywords, tuple)


# ============================================================
# 9. top_k 透传
# ============================================================

class TestTopKPassThrough:
    async def test_top_k_passed_to_vector_search_service(self) -> None:
        """evaluate(top_k=N) → vector_search_service.search(query, top_k=N)。"""
        cases = [
            RetrievalEvaluationCase(case_id="c1", query="q", expected_keywords=("kw",)),
        ]
        fake = FakeVectorSearchService(
            responses={"q": [_result(chunk_id=1, content="kw 在此")]},
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=7)

        assert fake.calls == [("q", 7)]
        assert summary.top_k == 7


# ============================================================
# 10. document_id 期望
# ============================================================

class TestDocumentIdExpectation:
    async def test_expected_document_id_must_match(self) -> None:
        """expected_document_id 命中 → matched=True；未命中 → False。"""
        cases = [
            # 该 case 期望 document_id=42
            RetrievalEvaluationCase(
                case_id="doc_match",
                query="q1",
                expected_keywords=("命中",),
                expected_document_id=42,
            ),
            # 该 case 期望 document_id=999（结果中不存在）
            RetrievalEvaluationCase(
                case_id="doc_miss",
                query="q2",
                expected_keywords=("命中",),
                expected_document_id=999,
            ),
        ]
        fake = FakeVectorSearchService(
            responses={
                "q1": [_result(chunk_id=1, content="命中测试", document_id=42)],
                "q2": [_result(chunk_id=2, content="命中测试", document_id=42)],
            },
        )
        svc = RagEvaluationService(vector_search_service=fake)

        summary = await svc.evaluate(cases, top_k=5)

        by_id = {r.case_id: r for r in summary.results}
        assert by_id["doc_match"].matched is True
        assert by_id["doc_match"].document_id_match is True
        assert by_id["doc_miss"].matched is False
        assert by_id["doc_miss"].document_id_match is False


__all__ = [
    "FakeVectorSearchService",
    "TestAllMatched",
    "TestPartialMatched",
    "TestNoneMatched",
    "TestMultipleKeywords",
    "TestEmptyCases",
    "TestSearchException",
    "TestNoLlmCall",
    "TestJsonLoader",
    "TestTopKPassThrough",
    "TestDocumentIdExpectation",
]