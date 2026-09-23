"""Reranker Client 单元测试（Phase 3.5.12）。

覆盖任务书 §十五 Client 部分：

    1. 空 query          2. 空 documents     3. 单 document
    4. 多 documents      5. score 数量一致    6. 模型异常
    7. DTO / exception   8. 不访问 DB        9. 不暴露 embedding

**不依赖 torch / transformers**：`_ensure_model` / `_score_pairs`
通过子类 / monkeypatch 打桩；真实模型由
`tests/test_reranker_real.py`（RUN_REAL_RERANKER=1）验证。
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect

import pytest

import backend.app.reranker.client as reranker_client_module
from backend.app.reranker.client import (
    BGERerankerClient,
    RerankerClient,
    create_reranker_client,
    get_default_reranker_client,
    reset_default_reranker_client,
)
from backend.app.reranker.exceptions import (
    RerankerConfigurationError,
    RerankerError,
    RerankerInputError,
    RerankerModelError,
)


class StubbedRerankerClient(BGERerankerClient):
    """跳过模型加载、返回固定分数的桩（保留输入校验与数量校验）。"""

    def __init__(self, scores: list[float] | None = None, *, model_name="stub/r") -> None:
        super().__init__(model_name=model_name)
        self._stub_scores = scores
        self.rerank_calls: list[tuple[str, list[str]]] = []

    def _ensure_model(self) -> None:  # no-op：不加载真实模型
        if self._model is None:
            self._model = object()  # 标记已加载
            self._tokenizer = object()
            self._resolved_device = "cpu"

    def _score_pairs(self, query: str, documents: list[str]) -> list[float]:
        if self._stub_scores is not None:
            return list(self._stub_scores)
        # 默认：确定性伪分数（按文档首字符）
        return [round(0.1 + (ord(d[0]) % 10) / 20.0, 4) for d in documents]


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ============================================================
# 输入校验
# ============================================================

class TestInputValidation:
    def test_empty_query_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError):
            run(client.rerank("", ["doc"]))

    def test_whitespace_query_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError):
            run(client.rerank("   ", ["doc"]))

    def test_non_str_query_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError):
            run(client.rerank(123, ["doc"]))  # type: ignore[arg-type]

    def test_empty_documents_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError):
            run(client.rerank("query", []))

    def test_empty_document_element_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError) as ei:
            run(client.rerank("query", ["ok", "  "]))
        assert ei.value.index == 1  # 指明第几个元素非法

    def test_non_str_document_rejected(self) -> None:
        client = StubbedRerankerClient()
        with pytest.raises(RerankerInputError):
            run(client.rerank("query", ["ok", None]))  # type: ignore[list-item])


# ============================================================
# 打分行为
# ============================================================

class TestScoring:
    def test_single_document(self) -> None:
        client = StubbedRerankerClient(scores=[0.87])
        scores = run(client.rerank("查询", ["采购入库的流程"]))
        assert scores == [pytest.approx(0.87)]

    def test_multiple_documents(self) -> None:
        client = StubbedRerankerClient(scores=[0.9, 0.5, 0.3])
        docs = ["文档A", "文档B", "文档C"]
        scores = run(client.rerank("查询", docs))
        assert len(scores) == 3

    def test_score_count_matches_documents(self) -> None:
        client = StubbedRerankerClient()  # 默认伪分数
        scores = run(client.rerank("查询", [f"文档{i}" for i in range(7)]))
        assert len(scores) == 7  # 一一对应

    def test_stubbed_model_loaded_once_across_calls(self) -> None:
        """模型只加载一次（§ 十七），多次 rerank 复用。"""
        client = StubbedRerankerClient()
        run(client.rerank("q1", ["a", "b"]))
        loaded_marker = client._model
        run(client.rerank("q2", ["c", "d"]))
        assert client._model is loaded_marker


# ============================================================
# 模型异常
# ============================================================

class TestModelErrors:
    def test_model_load_failure_raises_reranker_model_error(self) -> None:
        """_ensure_model 内部已把依赖缺失归一化为 RerankerModelError，原样传播。"""
        client = BGERerankerClient(model_name="stub/fail")

        def _boom() -> None:
            raise RerankerModelError(
                "加载 Reranker 失败：torch / transformers 未安装或导入失败"
                "（cause=ModuleNotFoundError）",
                cause_type="ModuleNotFoundError",
            )

        client._ensure_model = _boom  # type: ignore[method-assign]
        with pytest.raises(RerankerModelError) as ei:
            run(client.rerank("q", ["doc"]))
        assert ei.value.cause_type == "ModuleNotFoundError"

    def test_inference_failure_normalized_to_model_error(self) -> None:
        client = StubbedRerankerClient()

        def _bad_score(query, documents):  # type: ignore[no-untyped-def]
            raise RuntimeError("CUDA OOM")

        client._score_pairs = _bad_score  # type: ignore[method-assign]
        with pytest.raises(RerankerModelError) as ei:
            run(client.rerank("q", ["doc"]))
        assert ei.value.cause_type == "RuntimeError"

    def test_score_count_mismatch_raises_model_error(self) -> None:
        client = StubbedRerankerClient(scores=[0.5])  # 1 个分数，2 个文档
        with pytest.raises(RerankerModelError):
            run(client.rerank("q", ["doc1", "doc2"]))


# ============================================================
# DTO / exception 体系
# ============================================================

class TestExceptionHierarchy:
    def test_hierarchy(self) -> None:
        assert issubclass(RerankerInputError, RerankerError)
        assert issubclass(RerankerModelError, RerankerError)
        assert issubclass(RerankerConfigurationError, RerankerError)
        assert issubclass(RerankerError, Exception)

    def test_client_is_abstract_like(self) -> None:
        base = RerankerClient()
        with pytest.raises(NotImplementedError):
            run(base.rerank("q", ["d"]))


class TestConfiguration:
    def test_invalid_device_rejected(self) -> None:
        client = BGERerankerClient(model_name="stub/r", device="tpu")
        with pytest.raises(RerankerConfigurationError):
            run(client.rerank("q", ["d"]))

    def test_default_client_singleton(self) -> None:
        reset_default_reranker_client()
        c1 = get_default_reranker_client()
        c2 = get_default_reranker_client()
        assert c1 is c2
        reset_default_reranker_client()

    def test_create_reranker_client_returns_bge(self) -> None:
        client = create_reranker_client()
        assert isinstance(client, BGERerankerClient)


# ============================================================
# 隔离性：不访问 DB / 不暴露 embedding
# ============================================================

class TestIsolation:
    def test_module_does_not_import_db_or_sqlalchemy(self) -> None:
        """Reranker 模块不 import 任何数据库 / ORM 组件。"""
        source = inspect.getsource(reranker_client_module)
        for forbidden in (
            "sqlalchemy",
            "get_session",
            "get_engine",
            "backend.app.db",
            "vector_search",
            "rag_service",
        ):
            assert forbidden not in source, f"client.py 不应引用 {forbidden}"

    def test_rerank_returns_only_scores_no_vectors(self) -> None:
        """返回值只是 float score 列表，无 embedding / 无元数据泄漏。"""
        client = StubbedRerankerClient(scores=[0.5, 0.6])
        scores = run(client.rerank("查询", ["文档A", "文档B"]))
        assert all(isinstance(s, float) for s in scores)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_client_frozen_free_of_orm_fields(self) -> None:
        """BGERerankerClient 无公开 ORM / DB 属性（约定字段检查）。"""
        client = BGERerankerClient(model_name="stub/r")
        public = [a for a in vars(client) if not a.startswith("_")]
        assert public == []  # 无公开可变状态
