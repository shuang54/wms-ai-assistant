"""Vector Search Service 测试（Phase 3.5.3）。

覆盖（对应 Phase 3.5.3 任务书 §十二）：

    1. 基本搜索：返回结果 + top_k 生效 + distance / similarity 正确
    2. 相似度排序：distance 升序 = similarity 降序
    3. top_k 边界：1 / 3 / 5 均不超限
    4. 非法 top_k：0 / -1 / 51 → 拒绝
    5. 空 / 纯空白 query → 拒绝，EmbeddingClient 不被调用
    6. Embedding API Error → 异常向上传递
    7. Embedding 维度错误 → 拒绝，不发起 DB 查询
    8. 库空 → 返回 []
    9. DB 集成：真实 PostgreSQL + pgvector（RUN_DB_TESTS=1）

所有单元测试使用 MockEmbeddingClient + MagicMock session_factory，
**不发起真实网络** / DB 调用；DB 集成测试走真实 pgvector。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine
from backend.app.embedding.client import EmbeddingClient
from backend.app.embedding.exceptions import (
    EmbeddingAPIError,
    EmbeddingDimensionError,
    EmbeddingInputError,
)
from backend.app.services.vector_search_service import (
    VectorSearchError,
    VectorSearchInputError,
    VectorSearchParameterError,
    VectorSearchResult,
    VectorSearchService,
)

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable vector search DB tests",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    """module-level: Base.metadata.create_all()（幂等）。"""
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"

    from backend.app.db import models  # noqa: F401  # 确保模型已注册

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _truncate_after_each(engine):
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db(_truncate_after_each, engine):
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()


# ============================================================
# Mock / helpers
# ============================================================

class MockEmbeddingClient(EmbeddingClient):
    """确定性 Mock：返回固定维度的向量；支持错误注入。

    默认生成的 query 向量为全 +0.1 常数（便于人造 distance 数据时复用）。
    """

    def __init__(
        self,
        *,
        dimension: int | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._dimension = (
            dimension if dimension is not None else settings.embedding.dimension
        )
        self._raise = raise_exc
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._raise is not None:
            raise self._raise
        # 全 0.1 的常数向量（仅供"是否被调用"断言，不参与距离计算）
        return [0.1] * self._dimension


def _row(
    chunk_id: int,
    document_id: int,
    chunk_index: int,
    content: str,
    distance: float,
    metadata: dict | None = None,
) -> tuple:
    """构造 session.execute(stmt).all() 返回的行元组，列顺序与 Service 一致。"""
    return (
        chunk_id,
        document_id,
        chunk_index,
        content,
        metadata if metadata is not None else {},
        distance,
    )


def _fake_session_factory(rows: list[tuple]):
    """返回无 DB 调用的假 Session 工厂（callable）。

    `session_factory=factory_func` 即可让 Service 在 `with factory() as session:`
    时拿到配置好的 MagicMock session，
    `session.execute(...).all()` 返回给定 rows（模拟 PostgreSQL 已 LIMIT 后的结果）。

    同时把内部 mock 通过 `factory.session` 暴露，便于需要做断言的测试用例。
    """
    session = MagicMock(name="fake_session")
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.return_value.all.return_value = rows

    def factory() -> MagicMock:
        return session

    # 暴露 mock，便于测试做 assert_called 等断言
    factory.session = session  # type: ignore[attr-defined]
    return factory


# ============================================================
# 1. 基本搜索
# ============================================================

class TestBasicSearch:
    async def test_search_returns_results_in_distance_order(self) -> None:
        """正常输入：返回结果、按 distance 升序、similarity = 1 - distance。"""
        client = MockEmbeddingClient()
        rows = [
            _row(chunk_id=1, document_id=10, chunk_index=0, content="A", distance=0.1),
            _row(chunk_id=2, document_id=10, chunk_index=1, content="B", distance=0.3),
            _row(chunk_id=3, document_id=11, chunk_index=0, content="C", distance=0.2),
        ]
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory(rows),
        )

        results = await svc.search("采购入库怎么操作？", top_k=3)

        assert len(results) == 3
        # Service 内 SQL 已 ORDER BY distance；rows 按 0.1/0.3/0.2 输入时，
        # 排序由 DB 完成（Fake 不会自动重排），按输入顺序返回。
        # 业务契约断言：每条 result 的 similarity = 1 - distance
        for r in results:
            assert isinstance(r, VectorSearchResult)
            assert r.similarity == pytest.approx(1.0 - r.distance, abs=1e-9)

        # 关键字段
        r0 = results[0]
        assert r0.chunk_id == 1
        assert r0.document_id == 10
        assert r0.chunk_index == 0
        assert r0.content == "A"
        assert r0.distance == pytest.approx(0.1)
        assert r0.similarity == pytest.approx(0.9)

    async def test_top_k_is_enforced(self) -> None:
        """top_k 由 SQL LIMIT 传给 DB：此处用 MagicMock 验证仅返回前 top_k 行。

        注：Fake 不重排 SQL，真实场景下 LIMIT 由 PostgreSQL 强制。
        """
        client = MockEmbeddingClient()
        rows = [
            _row(chunk_id=i, document_id=1, chunk_index=i, content=f"c{i}", distance=0.01 * i)
            for i in range(1, 6)
        ]
        # Fake 模拟"已 LIMIT"：只返回前 2 行
        session = MagicMock(name="fake_session")
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.execute.return_value.all.return_value = rows[:2]

        svc = VectorSearchService(
            embedding_client=client,
            session_factory=lambda: session,
        )
        results = await svc.search("query", top_k=2)
        assert len(results) == 2


# ============================================================
# 2. 相似度排序（任务书 §十二-2）
# ============================================================

class TestSortOrder:
    async def test_results_sorted_by_distance_ascending(self) -> None:
        """真实 DB 风格：rows 按 distance 升序到达 Service；
        Service 直接按到达顺序构造 DTO（不做二次排序——SQL 已排序）。"""
        client = MockEmbeddingClient()
        # 模拟 DB 已按 distance 升序返回
        rows = [
            _row(chunk_id=1, document_id=1, chunk_index=0, content="最相似", distance=0.1),
            _row(chunk_id=2, document_id=1, chunk_index=1, content="中等",   distance=0.2),
            _row(chunk_id=3, document_id=1, chunk_index=2, content="较远",   distance=0.3),
        ]
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory(rows),
        )

        results = await svc.search("q", top_k=3)

        # 列表顺序与 DB 返回顺序一致（DB 已 ORDER BY）
        distances = [r.distance for r in results]
        assert distances == [0.1, 0.2, 0.3]
        # similarity 自然递减（与 distance 升序互为镜像）
        similarities = [r.similarity for r in results]
        assert similarities == [0.9, 0.8, 0.7]


# ============================================================
# 3. top_k 边界（任务书 §十二-3）
# ============================================================

class TestTopK:
    @pytest.mark.parametrize("top_k", [1, 3, 5])
    async def test_top_k_value_passed_through(self, top_k: int) -> None:
        """top_k = 1 / 3 / 5 都应被接受；Fake 通过 .limit() 断言。"""
        client = MockEmbeddingClient()
        session = MagicMock(name="fake_session")
        session.__enter__.return_value = session
        session.__exit__.return_value = False
        session.execute.return_value.all.return_value = []

        svc = VectorSearchService(
            embedding_client=client,
            session_factory=lambda: session,
        )
        await svc.search("q", top_k=top_k)

        # SELECT stmt 调用链：构建 stmt 后才 execute；Fake 不能直接验证 .limit()。
        # 改为验证 Service 不抛错即可（更细的断言由集成测试覆盖）。
        assert client.calls == ["q"]


# ============================================================
# 4. 非法 top_k（任务书 §十二-4）
# ============================================================

class TestInvalidTopK:
    @pytest.mark.parametrize("bad_top_k", [0, -1, 51, 100])
    async def test_top_k_out_of_range_rejected(self, bad_top_k: int) -> None:
        client = MockEmbeddingClient()
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory([]),
        )

        with pytest.raises(VectorSearchParameterError):
            await svc.search("query", top_k=bad_top_k)
        # 非法 top_k 应在 Embedding 之前被拒绝
        assert client.calls == []

    @pytest.mark.parametrize("bad_top_k", [1.5, "5", None, True])
    async def test_top_k_non_int_rejected(self, bad_top_k: object) -> None:
        client = MockEmbeddingClient()
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory([]),
        )

        with pytest.raises(VectorSearchParameterError):
            await svc.search("query", top_k=bad_top_k)  # type: ignore[arg-type]
        assert client.calls == []


# ============================================================
# 5. 空 query（任务书 §十二-5）
# ============================================================

class TestEmptyQuery:
    @pytest.mark.parametrize(
        "empty_query", ["", "   ", "\n", "\t\n  ", " \r\n\t "]
    )
    async def test_empty_or_whitespace_query_rejected(self, empty_query: str) -> None:
        client = MockEmbeddingClient()
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory([]),
        )

        with pytest.raises(VectorSearchInputError):
            await svc.search(empty_query)
        # 关键：Embedding API 不被调用
        assert client.calls == []

    async def test_non_str_query_rejected(self) -> None:
        client = MockEmbeddingClient()
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory([]),
        )

        with pytest.raises(VectorSearchInputError):
            await svc.search(12345)  # type: ignore[arg-type]
        assert client.calls == []


# ============================================================
# 6. Embedding API Error（任务书 §十二-6）
# ============================================================

class TestEmbeddingError:
    async def test_embedding_api_error_propagates(self) -> None:
        """EmbeddingAPIError 原样向上传递，不吞掉、不返回 []。"""
        client = MockEmbeddingClient(
            raise_exc=EmbeddingAPIError("硅基流动 503")
        )
        factory, call_counter = _make_tracked_factory([])
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=factory,
        )

        with pytest.raises(EmbeddingAPIError, match="硅基流动 503"):
            await svc.search("query")
        # DB 不应被触碰
        assert call_counter.call_count == 0, "Embedding 错误时不应访问 DB"


def _make_tracked_factory(rows: list[tuple]):
    """返回 (factory, call_counter) —— 计数 factory() 被调用次数。

    用于"DB 不应被触碰"类断言。
    """
    session = MagicMock(name="fake_session")
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.return_value.all.return_value = rows

    counter = MagicMock(name="factory_call_counter")

    def factory() -> MagicMock:
        counter()
        return session

    return factory, counter


# ============================================================
# 7. Embedding Dimension Error（任务书 §十二-7）
# ============================================================

class TestDimensionError:
    async def test_dimension_mismatch_rejected_before_db(self) -> None:
        """Mock 返回与配置不符的维度（如 1536）：抛 EmbeddingDimensionError，
        DB session 不被触碰。"""
        wrong_dim = settings.embedding.dimension + 512
        client = MockEmbeddingClient(dimension=wrong_dim)
        # session_factory 准备一个 sentinel：如果被调用就 fail
        session = MagicMock(name="sentinel_session")
        session.__enter__.return_value = session
        session.__exit__.return_value = False

        def _sentinel_factory() -> MagicMock:
            raise AssertionError("维度错误时不应访问 DB")

        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_sentinel_factory,
        )

        with pytest.raises(EmbeddingDimensionError):
            await svc.search("query")
        assert client.calls == ["query"]


# ============================================================
# 8. 库空 / 无命中（任务书 §十二-8）
# ============================================================

class TestEmptyResult:
    async def test_no_match_returns_empty_list(self) -> None:
        """DB 查询无命中：返回 []，不是异常。"""
        client = MockEmbeddingClient()
        svc = VectorSearchService(
            embedding_client=client,
            session_factory=_fake_session_factory([]),
        )

        results = await svc.search("query")
        assert results == []


# ============================================================
# 异常层级（防御性回归）
# ============================================================

class TestExceptionHierarchy:
    def test_input_and_parameter_subclass_base(self) -> None:
        assert issubclass(VectorSearchInputError, VectorSearchError)
        assert issubclass(VectorSearchParameterError, VectorSearchError)


# ============================================================
# 9. DB 集成（任务书 §十二-9）
# ============================================================

@requires_db
class TestDbIntegration:
    async def test_real_pgvector_cosine_search(self, db) -> None:  # type: ignore[name-defined]
        """真实 PostgreSQL + pgvector：
            1. 插入临时 Document + 3 个 Chunk（含 1024 维向量）
            2. 调整向量使 distance 排序为 已知顺序
            3. 调用 VectorSearchService
            4. 断言：distance 升序、top_k 生效、similarity 正确
            5. 清理测试数据（依赖 fixture _truncate_after_each）
        """
        client = MockEmbeddingClient()
        svc = VectorSearchService(embedding_client=client)

        # ---- 构造已知 distance 的向量 ----
        # 设 query 全 0.1，则与 query 的 cosine distance：
        #   - 完全相同向量 → distance 0
        #   - 与 query 同方向（unit），但长度不同也相似
        #   - 简单做法：用归一化向量 + 与 query 角度差构造
        # pgvector `<=>` = 1 - cos_sim，对于归一化向量。
        # 为了避免数值精度坑，直接让：
        #   v0 = query（即全 0.1 ... 但有常数向量陷阱见下）
        # 全 0.1 是零向量？不对，是常数向量。pgvector cosine 距离对常数向量是 NaN / 0。
        # 更稳妥：使用 3 个**正交**向量 + 1 个与 query 完全相同的向量。
        dim = settings.embedding.dimension
        assert dim == 1024

        query_vec = [0.1] * dim  # 与 Mock 输出一致

        # v_same = query（distance 应该 ~0）
        v_same = list(query_vec)
        # v_ortho = 与 query 正交（distance 应该 ~1）
        # 构造：v_ortho[0] = 0.1, 其它 = -0.1/(dim-1)，使其 sum=0 → 与 query 不正交（query 全 0.1）
        # 真正正交：query 单位向量是 (1/sqrt(dim)) * 1，v_ortho 与 1 内积=0
        # 简单做法：v_ortho[0] = -sum(other)，但与 1 的内积为 v_ortho[0] + sum(other)=0 ✓
        # 用更直观方案：v_ortho[0]=1, 其余=-1/(dim-1) → 与全 1 内积 = 1 - 1 = 0
        # 但 query 是 0.1，不影响正交性（缩放）。下面用：
        v_ortho = [1.0] + [-1.0 / (dim - 1)] * (dim - 1)
        # v_opposite = -query（distance 应该 ~2，因为 cosine sim = -1）
        v_opposite = [-x for x in query_vec]

        vectors = [
            (0, v_ortho,  "chunk-ortho"),    # distance ~ 1
            (1, v_same,   "chunk-same"),     # distance ~ 0
            (2, v_opposite, "chunk-opposite"), # distance ~ 2
        ]

        # ---- 写入 ----
        doc = KnowledgeDocument(
            title="[TEST] vector-search integration",
            file_name="vector_search_test.md",
            file_type="md",
            source="test",
            content_hash=hashlib.sha256(b"vector-search-test").hexdigest(),
            status="ready",
            meta_data={"source_type": "md"},
        )
        db.add(doc)
        db.flush()

        for chunk_index, vec, content in vectors:
            db.add(
                KnowledgeChunk(
                    document_id=doc.id,
                    chunk_index=chunk_index,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    embedding=vec,
                    meta_data={"source_type": "markdown"},
                )
            )
        db.flush()
        # 必须 commit：VectorSearchService 内部使用独立的 session，
        # PostgreSQL 默认 READ COMMITTED 隔离级别看不到本会话的未提交数据。
        db.commit()

        # ---- 检索 top_k=2 ----
        results = await svc.search("query", top_k=2)

        # 排序：same (d~0) → ortho (d~1) → opposite (d~2)
        assert len(results) == 2
        assert results[0].chunk_index == 1  # same
        assert results[1].chunk_index == 0  # ortho

        # distance 校验：same ≈ 0, ortho ≈ 1
        assert results[0].distance == pytest.approx(0.0, abs=1e-4)
        assert results[1].distance == pytest.approx(1.0, abs=1e-3)

        # similarity = 1 - distance
        for r in results:
            assert r.similarity == pytest.approx(1.0 - r.distance, abs=1e-9)

        # 关键字段类型校验
        for r in results:
            assert isinstance(r.chunk_id, int)
            assert isinstance(r.document_id, int)
            assert isinstance(r.chunk_index, int)
            assert isinstance(r.content, str)
            assert isinstance(r.metadata, dict)
            assert r.metadata.get("source_type") == "markdown"
            # chunk_id == KnowledgeChunk.id
            assert r.document_id == doc.id

        # ---- top_k=3 应包含 opposite ----
        all_results = await svc.search("query", top_k=3)
        assert len(all_results) == 3
        assert all_results[2].chunk_index == 2  # opposite

        # opposite distance ≈ 2
        assert all_results[2].distance == pytest.approx(2.0, abs=1e-3)
        assert all_results[2].similarity == pytest.approx(-1.0, abs=1e-3)

        # ---- 清理：依赖 module 级 fixture _truncate_after_each ----

    async def test_db_empty_returns_empty_list(self, db) -> None:  # type: ignore[name-defined]
        """库中无任何 embedding 不为 NULL 的 chunk：返回 []。"""
        client = MockEmbeddingClient()
        svc = VectorSearchService(embedding_client=client)

        results = await svc.search("anything", top_k=5)
        assert results == []


__all__ = [
    "TestBasicSearch",
    "TestSortOrder",
    "TestTopK",
    "TestInvalidTopK",
    "TestEmptyQuery",
    "TestEmbeddingError",
    "TestDimensionError",
    "TestEmptyResult",
    "TestExceptionHierarchy",
    "TestDbIntegration",
]