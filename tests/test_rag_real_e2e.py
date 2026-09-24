"""Real RAG End-to-End Test（Phase 3.7.13：真实 RAG E2E）。

本阶段目标（任务书 §一）：

    POST /api/ai/chat
        ↓
    AIOrchestratorService
        ↓
    AIRouterService
        ↓
    RagService
        ↓
    VectorSearchService → EmbeddingClient（BAAI/BGE-M3, 1024-dim）
        ↓
    PostgreSQL + pgvector
        ↓
    ContextBuilder
        ↓
    LLMClient（DeepSeek deepseek-chat）
        ↓
    ChatResponse

**默认跳过**（不消耗任何真实 API 额度，无 Key 也不硬跑失败）：

    RUN_REAL_RAG_E2E=1  RUN_DB_TESTS=1  pytest tests/test_rag_real_e2e.py -v

跳过条件（三层防护）：
    1. RUN_REAL_RAG_E2E 未设置 → skip
    2. RUN_REAL_RAG_E2E=true 但 RUN_DB_TESTS 未设置 → skip
    3. RUN_REAL_RAG_E2E=true + RUN_DB_TESTS=true 但 DATABASE_URL / EMBEDDING_API_KEY /
       LLM_API_KEY 缺失 → skip（不会硬跑失败）

数据隔离（任务书 §十六）：
    - setup：构造唯一内容（含 UUID 片段）的临时 md 文件
    - 测试中：通过 ``KnowledgeIngestionService.ingest_one`` 复用既有
      Pipeline（Parser → Chunker → Embedding → DB 写入）
    - 测试结束后：只删除本次测试插入的 document_id（CASCADE 自动清 chunks），
      不动 CLI 手动加载的其它文档

测试目标（任务书 §十五）：
    1. Router    → ``/api/ai/chat`` 对"采购入库怎么操作？"路由到 RAG
    2. API       → HTTP 200 + route=rag + content 非空
    3. RAG Content → content 由真实 LLM 生成（不是固定 canned answer）
    4. Sources   → sources 非空，含 document_id / chunk_id / similarity
    5. Retrieval → 命中的 chunk 确实来自本次插入的 knowledge_chunk
    6. Project Context → project_id="vietnam-wms" 正常透传到 Orchestrator
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关。"""
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_REAL_RAG_E2E = _env_flag("RUN_REAL_RAG_E2E")
_RUN_DB_TESTS = _env_flag("RUN_DB_TESTS")
# Phase 3.7.14：真实 Reranker E2E 需要显式同意加载本地 Cross-Encoder 模型
# （与 tests/test_reranker_real.py 的门控约定一致）
_RUN_REAL_RERANKER = _env_flag("RERANKER_ENABLED")


# ============================================================
# Opt-in skip marker（只用于 TestRealRagEndToEnd，不影响失败情况 Unit Test）
# ============================================================

# E2E 真实链路测试的额外 marker（与模块级 pytestmark 双重防护）
_requires_full_real_chain = pytest.mark.skipif(
    not (_RUN_REAL_RAG_E2E and _RUN_DB_TESTS),
    reason="set RUN_REAL_RAG_E2E=1 and RUN_DB_TESTS=1",
)

# Phase 3.7.14：真实 Reranker E2E（额外需要 RERANKER_ENABLED=true 显式同意）
_requires_real_reranker = pytest.mark.skipif(
    not (_RUN_REAL_RAG_E2E and _RUN_DB_TESTS and _RUN_REAL_RERANKER),
    reason="set RUN_REAL_RAG_E2E=1 RUN_DB_TESTS=1 RERANKER_ENABLED=true "
    "to enable real Reranker E2E",
)


# ============================================================
# Helper fixtures (only constructed when env-gates pass)
# ============================================================

def _require_real_dependencies() -> None:
    """第二/三层防护：无 Key / 无 DB 时 skip（不硬跑失败）。"""
    from backend.app.config import settings

    if not settings.database.url.strip():
        pytest.skip("DATABASE_URL 未配置；无法执行真实 RAG 链路")
    if not settings.embedding.api_key.strip():
        pytest.skip("EMBEDDING_API_KEY 未配置；无法调用 BGE-M3 Embedding")
    if not settings.llm.api_key.strip():
        pytest.skip("LLM_API_KEY 未配置；无法调用 DeepSeek")
    if not settings.llm.base_url.strip():
        pytest.skip("LLM_BASE_URL 未配置；无法调用 DeepSeek")
    if not settings.llm.model.strip():
        pytest.skip("LLM_MODEL 未配置；无法调用 DeepSeek")


@pytest.fixture(scope="module")
def engine():
    """Module-level：保证 Base metadata 已创建（pgvector 已扩展）。"""
    from backend.app.config import settings
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    _require_real_dependencies()

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(scope="module")
def _tracked_doc_ids() -> set[int]:
    """记录本测试模块本次插入的 document_id，用于 teardown 精确清理。"""
    return set()


@pytest.fixture
def _cleanup(engine, _tracked_doc_ids):
    """每个测试结束后只删除 _tracked_doc_ids 内的 document + chunks（CASCADE）。"""
    yield
    if not _tracked_doc_ids:
        return
    ids = list(_tracked_doc_ids)
    with engine.begin() as conn:
        # chunks 由 FK CASCADE 自动级联删除
        conn.execute(
            text("DELETE FROM knowledge_document WHERE id = ANY(:ids)"),
            {"ids": ids},
        )


@pytest.fixture
def session(engine, _cleanup):
    """function-level Session。"""
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    assert factory is not None
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app_client():
    """构造 FastAPI TestClient（用默认 _default_orchestrator，含真实 RagService）。"""
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


# ============================================================
# 真实 RAG E2E
# ============================================================

@_requires_full_real_chain
class TestRealRagEndToEnd:
    """Phase 3.7.13 真实 RAG E2E：POST /api/ai/chat 全链路。

    Phase 3.7.14 起，本测试**显式固定 RERANKER_ENABLED=false**：
    作为"无 Reranker 基线"验证既有行为不变（即使环境变量
    RERANKER_ENABLED=true 也不受影响）；Reranker 路径见
    ``TestRealRagRerankerEndToEnd``。
    """

    async def test_real_rag_end_to_end_via_api(
        self,
        engine,
        _tracked_doc_ids,
        session,
        app_client,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """完整链路：

            HTTP POST /api/ai/chat
                ↓
            AIOrchestratorService
                ↓
            AIRouterService.route → rag
                ↓
            RagService.answer
                ↓
            VectorSearchService.search
                ↓
            EmbeddingClient.embed (SiliconFlow BAAI/bge-m3)
                ↓
            PostgreSQL + pgvector (knowledge_chunk.embedding <=> :q)
                ↓
            ContextBuilder
                ↓
            LLMClient.chat (DeepSeek deepseek-chat)
                ↓
            ChatResponse(content, sources, metadata)

        验证任务书 §十五 全部 6 项。
        """
        from backend.app.config import settings
        from backend.app.services.knowledge_ingestion_service import (
            KnowledgeIngestionService,
        )
        from backend.app.services.rag_service import DEFAULT_EMPTY_ANSWER

        # ---- 0. 固定 Reranker 禁用（Phase 3.7.14：本测试为无 Reranker 基线）----
        import dataclasses

        import backend.app.services.rag_service as rag_service_module

        monkeypatch.setattr(
            rag_service_module,
            "settings",
            dataclasses.replace(
                settings, reranker=dataclasses.replace(settings.reranker, enabled=False)
            ),
        )

        # ---- 1. 准备唯一测试文档（避免与 CLI 加载数据冲突） ----
        unique_marker = f"e2e{random_hex()}"
        unique_doc = tmp_path / f"wms_rag_e2e_{unique_marker}.md"
        # 内容刻意与任务书示例不同措辞，但语义相近；
        # RAG 测试目标：通过语义检索找到，不依赖字面匹配。
        body = (
            f"# WMS 测试文档 {unique_marker}\n\n"
            "## 入库流程概述\n\n"
            "本节描述 WMS 系统中供应商到货后，仓库人员如何把货物正式入账的步骤。\n\n"
            "主要环节包括：接收供应商发来的到货通知、扫描并核对物料标签、"
            "登记到货数量与采购数量是否一致、记录批次信息、将货物转移到指定库位、"
            "更新系统库存。整个流程结束后，物料即可用于生产或销售。\n\n"
            "## 异常情况\n\n"
            "当到货数量与采购订单不一致时，必须先与采购部门沟通确认后再继续。\n"
        )
        unique_doc.write_text(body, encoding="utf-8")

        # ---- 2. 通过既有 Pipeline 导入（真实 Embedding + DB 写入） ----
        ingestion = KnowledgeIngestionService()
        result = await ingestion.ingest_one(unique_doc)
        _tracked_doc_ids.add(result.document_id)

        assert result.status == "ready", (
            f"ingestion 失败: status={result.status}, doc_id={result.document_id}"
        )
        assert result.document_id > 0
        assert result.chunk_count >= 1
        assert result.embedded_chunk_count == result.chunk_count

        # ---- 3. 验证知识数据真实存在 ----
        chunk_count_in_db = session.execute(
            text(
                "SELECT COUNT(*) FROM knowledge_chunk WHERE document_id = :did"
            ),
            {"did": result.document_id},
        ).scalar_one()
        assert chunk_count_in_db == result.chunk_count

        # 验证 embedding 维度 = 1024（pgvector 端点核一次）
        dim_check = session.execute(
            text(
                "SELECT vector_dims(embedding) FROM knowledge_chunk "
                "WHERE document_id = :did LIMIT 1"
            ),
            {"did": result.document_id},
        ).scalar_one()
        assert dim_check == settings.embedding.dimension == 1024

        # ---- 4. POST /api/ai/chat 真实端到端 ----
        question = "采购入库怎么操作？"  # 任务书 §十五 标准问题
        response = app_client.post(
            "/api/ai/chat",
            json={"question": question, "project_id": "vietnam-wms"},
        )

        # ---- 5. 验证 HTTP / Router ----
        assert response.status_code == 200, (
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
        payload = response.json()
        assert payload["route"] == "rag", (
            f"路由判定错误: {payload['route']!r}, full={payload}"
        )

        # ---- 6. 验证 RAG Content（真实 LLM 生成） ----
        content = payload.get("content") or ""
        assert content, "RAG content 为空"
        assert content.strip(), "RAG content 全空白"
        # RagService 真实空检索的 canned answer 是 "知识库中没有找到与该问题相关的信息。"
        # （任务书 §十五 #3）—— 必须**不是**它，意味着真实检索命中。
        # 注意：LLM 自身的回答可能包含 "知识库中没有找到" 字样（合法 anti-hallucination），
        # 所以这里只检查精确的 RagService canned answer，避免误判。
        assert content != DEFAULT_EMPTY_ANSWER, (
            f"RAG 返回了 canned empty answer（未命中 knowledge_chunk）: "
            f"{content!r}"
        )
        # 应有真实 LLM 生成的实质内容（> 5 个字符）
        assert len(content) > 5, f"content 过短: {content!r}"

        # ---- 7. 验证 Sources（任务书 §十五 #4） ----
        data = payload.get("data") or {}
        sources = data.get("sources") or []
        assert sources, "sources 为空（未命中 knowledge_chunk）"
        first = sources[0]
        assert "chunk_id" in first
        assert "document_id" in first
        assert "similarity" in first
        # similarity 必须为合法 float
        assert isinstance(first["similarity"], (int, float))
        assert 0.0 <= first["similarity"] <= 1.0

        # ---- 8. 验证 Retrieval 来自 knowledge_chunk（任务书 §十五 #5） ----
        # 至少有一个 source 的 document_id 等于本次测试插入的 doc_id
        inserted_doc_ids = _tracked_doc_ids
        hit_doc_ids = {s["document_id"] for s in sources}
        assert hit_doc_ids & inserted_doc_ids, (
            f"sources 未命中本次插入的 document (inserted={inserted_doc_ids}, "
            f"hit={hit_doc_ids})"
        )

        # chunk_id 必须在本次插入的 chunks 中
        inserted_chunk_ids = set(
            session.execute(
                text(
                    "SELECT id FROM knowledge_chunk WHERE document_id = :did"
                ),
                {"did": result.document_id},
            ).scalars()
        )
        hit_chunk_ids = {s["chunk_id"] for s in sources}
        assert hit_chunk_ids & inserted_chunk_ids, (
            f"sources chunk_id 未命中本次插入的 chunks "
            f"(hit={hit_chunk_ids}, inserted={inserted_chunk_ids})"
        )

        # ---- 9. 验证 metadata 中 project_id 已透传（任务书 §十五 #6） ----
        metadata = payload.get("metadata") or {}
        # RAG 路径 metadata 不显式带 project_id（rag_service 不读取 project_id），
        # 但 metadata 至少要有 decision_source / route_reason 等字段；
        # 同时 project_id 的端到端透传由 _build_orchestrator_for_project_id
        # 处理，本测试仅验证"传入 project_id 不破坏请求"。
        assert "decision_source" in metadata
        assert "route_reason" in metadata

        # ---- 10. 安全：响应不含敏感信息 ----
        body_text = response.text
        for forbidden in (
            "sk-",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "Traceback",
            "<=>",
            "SELECT ",
        ):
            assert forbidden not in body_text, (
                f"响应泄露敏感信息: {forbidden!r}"
            )

        # ---- 11. 报告（输出供日志/ADR 抓取） ----
        print("\n=== Phase 3.7.13 Real RAG E2E Result ===")
        print(f"  Question       : {question}")
        print(f"  Route          : {payload['route']}")
        print(f"  Document ID    : {result.document_id}")
        print(f"  Chunk count    : {result.chunk_count}")
        print(f"  Sources        : {len(sources)} chunk(s)")
        print(f"  Top similarity : {first['similarity']:.4f}")
        print(f"  Content length : {len(content)} chars")
        print(f"  Content (前 100) : {content[:100]}...")
        print(f"  metadata       : {metadata}")
        print("======================================")


# ============================================================
# Phase 3.7.14：真实 RAG + Reranker E2E（opt-in）
# ============================================================

@_requires_real_reranker
class TestRealRagRerankerEndToEnd:
    """Phase 3.7.14 真实 Reranker E2E。

    门控（三层）：
        1. RUN_REAL_RAG_E2E=1 + RUN_DB_TESTS=1（真实链路同意）
        2. RERANKER_ENABLED=true（显式同意加载本地 bge-reranker-v2-m3 模型）
        3. .env 依赖齐备（DB / Embedding / LLM Key）→ 否则 skip

    验证链路：

        POST /api/ai/chat
            ↓
        Router → rag
            ↓
        RagService（RERANKER_ENABLED=true）
            ↓
        VectorSearchService（candidate_top_k 召回）
            ↓
        EmbeddingClient（BGE-M3, 真实 SiliconFlow API）
            ↓
        PostgreSQL + pgvector（真实）
            ↓
        BGERerankerClient（真实本地 bge-reranker-v2-m3 Cross-Encoder）
            ↓
        ContextBuilder（重排后 top_k 条）
            ↓
        LLMClient（真实 DeepSeek）
            ↓
        ChatResponse

    成本控制（任务书 §二十）：单次执行 = 1 次 Embedding（ingestion）
    + 1 次 Embedding（query）+ 1 次 Reranker（本地 CPU，无 API 费用）
    + 1 次 DeepSeek。
    """

    async def test_real_rag_reranker_end_to_end_via_api(
        self,
        engine,
        _tracked_doc_ids,
        session,
        app_client,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import time as time_module

        import dataclasses

        from backend.app.config import settings
        from backend.app.reranker.client import get_default_reranker_client
        from backend.app.services.knowledge_ingestion_service import (
            KnowledgeIngestionService,
        )
        import backend.app.services.rag_service as rag_service_module
        from backend.app.services.rag_service import DEFAULT_EMPTY_ANSWER

        _require_real_dependencies()

        # 检查 torch / transformers 可用（不可用则 skip，不硬跑失败）
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:  # pragma: no cover
            pytest.skip("torch / transformers 未安装，无法执行真实 Reranker E2E")

        # ---- 1. 显式开启 Reranker（钳制候选窗口，控制 CPU 耗时）----
        candidate_top_k = 10
        reranker_top_k = 3
        monkeypatch.setattr(
            rag_service_module,
            "settings",
            dataclasses.replace(
                settings,
                reranker=dataclasses.replace(
                    settings.reranker,
                    enabled=True,
                    candidate_top_k=candidate_top_k,
                    top_k=reranker_top_k,
                ),
            ),
        )

        # ---- 2. 准备唯一测试文档（与基线 E2E 同策略）----
        # 注：chunk_size=800——每个小节必须 ≥800 字符才能独立成 chunk，
        # 否则 MarkdownAwareChunker 会把相邻小节合并（首轮实测 3 小节被并成 1 块）。
        unique_marker = f"rrk{random_hex()}"
        unique_doc = tmp_path / f"wms_rag_rrk_{unique_marker}.md"
        sections = [
            (
                "采购入库流程",
                "供应商送货到达仓库后，收货员首先在系统中打开对应的采购入库通知单，"
                "核对供应商名称、采购订单号与物料清单。第一步是卸货与初步清点："
                "按箱核对物料编码、名称与数量，检查外包装是否破损。"
                "第二步是扫码收货：逐箱扫描物料条码，系统自动比对采购订单明细，"
                "数量一致则登记实收数量，不一致时登记差异并通知采购部门确认。"
                "第三步是质检：IQC 按抽样标准检查来料质量，合格批次放行，"
                "不合格批次转入退货或让步接收流程。第四步是上架："
                "系统根据库位策略推荐上架库位，仓管员将物料搬运至指定库位"
                "并扫描库位条码完成上架确认。第五步是库存入账："
                "上架完成后系统自动增加对应库位的在库数量，采购入库单状态"
                "更新为已完成，同时在操作日志中记录经手人与时间戳。"
                "整个过程中收货、质检、上架三个环节必须由不同人员执行，"
                "以保证职责分离。若当日无法完成上架，物料应暂存待上架区，"
                "并在次日优先处理。紧急物料可申请加急质检通道。"
                "注意事项：采购入库单必须在当月财务结账前全部关闭，"
                "否则会影响应付账款的暂估入账；收货时发现供应商多发物料，"
                "应单独登记暂存，不得直接混入正常库存；"
                "所有条码扫描失败的情况都应改用人工录入并双人复核，"
                "严禁跳过扫码直接确认；入库单据与质检报告需归档保存"
                "至少两年以备审计；对批次管理的物料还必须录入生产日期"
                "与有效期，系统会按先进先出原则推荐出库批次。",
            ),
            (
                "销售出库流程",
                "销售订单审核通过后，系统自动生成销售出库单并释放对应库存。"
                "仓库主管按波次将出库单分配给拣货员。第一步是波次规划："
                "系统按订单优先级、承运商截单时间自动聚单，生成拣货路径。"
                "第二步是拣货：拣货员使用手持终端按路径指引到达指定库位，"
                "扫描库位与物料条码，按拣货数量取货放入周转箱。"
                "第三步是复核：复核员扫描周转箱内所有物料，系统核对"
                "订单明细与拣货结果，多拣、少拣、错拣均会被拦截。"
                "第四步是包装与发运：按客户要求打包、贴物流面单、"
                "称重复核重量，交接给承运商并登记运单号。"
                "第五步是出库确认：系统扣减对应库位库存，出库单关闭，"
                "同时生成应收台账同步给财务系统。"
                "出库过程中如发现库位实物数量不足，应立即创建库存差异报告，"
                "由仓库主管组织循环盘点定位差异原因，再继续执行出库。"
                "客户自提订单需核验提货凭证与身份信息后方可放行。"
                "注意事项：出库单必须在承运商截单时间前完成交接，"
                "逾期订单自动顺延至下一波次；拣货员在库位发现物料"
                "批次临期或包装破损时，应调用换批或换箱流程，"
                "不得将异常物料直接发出；复核环节的拦截记录"
                "会纳入拣货员的绩效考核；对需要温控运输的物料，"
                "包装环节必须加装温度记录仪并在出库单上登记设备编号；"
                "所有出库操作都会在轨迹表中留痕，支持后续追溯"
                "到具体的操作人、时间与库位。",
            ),
            (
                "库存盘点流程",
                "盘点用于保证账实一致，分为全盘、循环盘点与专项盘点三类。"
                "第一步是创建盘点任务：仓管主管选择盘点范围（仓库、库区、"
                "库位或物料），设定盘点类型与计划时间，系统冻结相关库位的"
                "收发操作或采用动态盘点模式。第二步是任务分配："
                "系统将盘点明细按库位拆分给盘点员，盲盘模式下盘点员"
                "看不到系统账面数量，避免先入为主。第三步是实物清点："
                "盘点员到指定库位逐一清点物料，扫描库位与物料条码，"
                "录入实盘数量。第四步是差异分析：系统自动比对账面与实盘，"
                "生成差异清单；差异率超过阈值的库位需复盘，由第二人复核。"
                "第五步是差异处理：经仓库经理审批后，盘盈生成盘盈入库单，"
                "盘亏生成盘亏出库单，账面库存随之调整。"
                "第六步是归档：盘点报告存档，差异原因归类统计，"
                "用于后续改善库位管理 accuracy。盘点期间发现的呆滞料"
                "应标记并通知计划部门评估处理方式。"
                "注意事项：全盘每年至少执行一次，通常安排在财务"
                "年度结账前；循环盘点按 ABC 分类设定频次，A 类物料"
                "每月一次、B 类每季度一次、C 类每半年一次；"
                "盲盘结果录入后系统立即锁定，复盘需要仓库经理授权；"
                "差异率连续三个月超过警戒线的库区应启动专项治理，"
                "排查条码管理、库位标识与人员操作规范；"
                "盘点的所有调整单据必须附差异原因代码，"
                "便于后续做根因分析与趋势统计。",
            ),
        ]
        body = f"# WMS Reranker 测试文档 {unique_marker}\n\n" + "\n\n".join(
            f"## {title}\n\n{content}" for title, content in sections
        )
        unique_doc.write_text(body, encoding="utf-8")

        ingestion = KnowledgeIngestionService()
        result = await ingestion.ingest_one(unique_doc)
        _tracked_doc_ids.add(result.document_id)

        assert result.status == "ready", (
            f"ingestion 失败: status={result.status}"
        )
        assert result.chunk_count >= 3, (
            f"测试文档应至少切成 3 个 chunk（实际 {result.chunk_count}），"
            "否则 rerank 无区分度"
        )

        # ---- 3. 记录测试前 DB 数量（验证无额外写入）----
        doc_count_before = session.execute(
            text("SELECT COUNT(*) FROM knowledge_document")
        ).scalar_one()

        # ---- 4. POST /api/ai/chat（真实全链路 + Reranker）----
        question = "采购入库怎么操作？"
        with caplog.at_level("INFO", logger="backend.app.services.rag_service"):
            t0 = time_module.perf_counter()
            response = app_client.post(
                "/api/ai/chat",
                json={"question": question, "project_id": "vietnam-wms"},
            )
            total_wall_s = time_module.perf_counter() - t0

        # ---- 5. 验证 HTTP / Router / Content ----
        assert response.status_code == 200, (
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
        payload = response.json()
        assert payload["route"] == "rag", payload
        content = payload.get("content") or ""
        assert content and content != DEFAULT_EMPTY_ANSWER
        assert len(content) > 5

        # ---- 6. 验证 Reranker 真实参与 ----
        # 6a. 本地模型确实被加载（懒加载发生在本次请求内）
        default_reranker = get_default_reranker_client()
        assert default_reranker._model is not None, (
            "BGERerankerClient 模型未加载——Reranker 未参与链路"
        )
        # 6b. "RAG rerank applied" 日志存在（说明走了 rerank 分支）
        rerank_records = [
            r for r in caplog.records if r.getMessage() == "RAG rerank applied"
        ]
        assert rerank_records, "未捕获 'RAG rerank applied' 日志"
        rr = rerank_records[-1]
        assert rr.candidate_count >= 1
        assert 1 <= rr.kept_count <= reranker_top_k

        # 6c. "RAG answered" 日志存在且 reranker_used=True
        answered = [
            r for r in caplog.records if r.getMessage() == "RAG answered"
        ]
        assert answered
        ans = answered[-1]
        assert ans.reranker_used is True
        assert ans.rerank_elapsed_ms is not None and ans.rerank_elapsed_ms >= 0

        # ---- 7. 验证 Sources（重排 + 截断至 reranker top_k）----
        data = payload.get("data") or {}
        sources = data.get("sources") or []
        assert sources, "sources 为空"
        assert len(sources) <= reranker_top_k, (
            f"sources 数量 {len(sources)} 超过 reranker top_k {reranker_top_k}"
        )
        first = sources[0]
        assert "document_id" in first and "chunk_id" in first
        assert 0.0 <= first["similarity"] <= 1.0
        # 命中本次插入的文档
        hit_doc_ids = {s["document_id"] for s in sources}
        assert hit_doc_ids & _tracked_doc_ids, (
            f"sources 未命中本次插入文档: {hit_doc_ids} vs {_tracked_doc_ids}"
        )

        # ---- 8. 验证 DB 只读（RAG 链路无写入）----
        doc_count_after = session.execute(
            text("SELECT COUNT(*) FROM knowledge_document")
        ).scalar_one()
        assert doc_count_after == doc_count_before, (
            f"RAG 链路写入了数据库（before={doc_count_before}, "
            f"after={doc_count_after}）"
        )

        # ---- 9. 性能记录（任务书 §十）----
        print("\n=== Phase 3.7.14 Real RAG + Reranker E2E Result ===")
        print(f"  Question         : {question}")
        print(f"  Route            : {payload['route']}")
        print(f"  Candidates       : {rr.candidate_count}")
        print(f"  Kept (rerank)    : {rr.kept_count}")
        print(f"  Rerank elapsed   : {ans.rerank_elapsed_ms:.1f} ms")
        print(f"  Total RAG        : {ans.elapsed_ms:.1f} ms")
        print(f"  API wall time    : {total_wall_s * 1000:.1f} ms")
        print(f"  Sources          : {len(sources)}")
        print(f"  Top similarity   : {first['similarity']:.4f}")
        print(f"  Content (前 100) : {content[:100]}...")
        print("====================================================")


# ============================================================
# 失败情况 Unit Test（不依赖真实外部 API，可随时运行）
# ============================================================

class TestRagFailureUnitTests:
    """Phase 3.7.13 任务书 §十七：失败情况 Unit Test 覆盖。

    不发起任何真实网络调用，使用 MonkeyPatch 注入失败：
        - Embedding unavailable → EmbeddingAPIError → Orchestrator ExecutionError
        - LLM unavailable → LLMRequestError → Orchestrator ExecutionError
        - Vector Search unavailable → VectorSearchError → Orchestrator ExecutionError
        - Empty retrieval → RagResponse(empty) → 200 + DEFAULT_EMPTY_ANSWER

    全部通过真实 Orchestrator（注入 MonkeyPatched 的依赖），
    不伪造 ``RagService`` 的成功返回（任务书 §十二）。
    """

    def _build_real_orchestrator_with_failing_rag(
        self, raise_exc: Exception
    ):
        """构造 Orchestrator，其中 RagService.answer 抛 ``raise_exc``。

        - Router / Orchestrator / ProjectContextProvider / ToolRegistry / T2S 全部真实
        - RagService 是真实对象，仅 monkeypatch 其 answer() 方法抛指定异常
        """
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorService,
        )
        from backend.app.services.ai_router_service import (
            AIRouterService,
            ToolRegistryCapabilityAdapter,
        )
        from backend.app.services.rag_service import RagService
        from backend.app.tools.get_inventory import build_default_tool_registry

        tool_registry = build_default_tool_registry()
        real_rag = RagService()

        async def _raising_answer(query: str, *, top_k=None):
            raise raise_exc

        real_rag.answer = _raising_answer  # type: ignore[method-assign]

        return AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=real_rag,
            tool_registry=tool_registry,
        )

    def _post_with_orchestrator(
        self,
        monkeypatch,
        orch,
    ):
        """把 _default_orchestrator / _build_orchestrator_for_project_id 都替换为 ``orch``。"""
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app

        monkeypatch.setattr(orch_module, "_default_orchestrator", orch)

        def _fake_build(project_id: str):
            return orch

        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            _fake_build,
        )

        return TestClient(app)

    def test_embedding_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """Embedding 不可用 → RAG 抛 EmbeddingAPIError → Orchestrator 包装为 500。"""
        from backend.app.embedding.exceptions import EmbeddingAPIError
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorExecutionError,
        )

        orch = self._build_real_orchestrator_with_failing_rag(
            EmbeddingAPIError("硅基流动 503")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text
        # 必须返回清晰错误，不泄露内部细节
        detail = response.json()["detail"]
        assert "AI 能力执行失败" in detail
        # 严禁泄露 traceback / API Key
        for forbidden in (
            "Traceback",
            "硅基流动 503",  # 内部错误信息不外泄给客户端
            "sk-",
            "Bearer ",
            "<=>",
        ):
            assert forbidden not in response.text, forbidden

    def test_llm_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """LLM 不可用 → RagService.answer 抛 LLMRequestError → Orchestrator 包装为 500。"""
        from backend.app.llm.client import LLMRequestError
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorExecutionError,
        )

        orch = self._build_real_orchestrator_with_failing_rag(
            LLMRequestError("DeepSeek 500")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text
        detail = response.json()["detail"]
        assert "AI 能力执行失败" in detail

    def test_vector_search_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """Vector Search 不可用 → RagService 抛 VectorSearchError → Orchestrator 包装。"""
        from backend.app.services.vector_search_service import VectorSearchError

        orch = self._build_real_orchestrator_with_failing_rag(
            VectorSearchError("pgvector down")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text

    def test_empty_retrieval_returns_canned_answer(
        self, monkeypatch
    ) -> None:
        """空检索 → RagService 走现有策略（不调 LLM，返回 canned answer）。"""
        from backend.app.services.rag_service import (
            DEFAULT_EMPTY_ANSWER,
            RagResponse,
            RagService,
        )

        real_rag = RagService()

        async def _empty_answer(query: str, *, top_k=None):
            # 完全模拟 RagService.answer 在 results=[] 时的真实行为
            return RagResponse(
                answer=DEFAULT_EMPTY_ANSWER,
                sources=(),
                used_chunks_count=0,
            )

        real_rag.answer = _empty_answer  # type: ignore[method-assign]

        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorService,
        )
        from backend.app.services.ai_router_service import (
            AIRouterService,
            ToolRegistryCapabilityAdapter,
        )
        from backend.app.tools.get_inventory import build_default_tool_registry

        tool_registry = build_default_tool_registry()
        orch = AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=real_rag,
            tool_registry=tool_registry,
        )
        monkeypatch.setattr(orch_module, "_default_orchestrator", orch)
        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            lambda project_id: orch,
        )

        client = TestClient(app)
        response = client.post(
            "/api/ai/chat", json={"question": "完全无关问题"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == DEFAULT_EMPTY_ANSWER
        # sources 必须为空（来自真实 RagService 的空检索路径）
        assert payload["data"]["sources"] == []
        assert payload["data"]["used_chunks_count"] == 0


# ============================================================
# Helpers
# ============================================================

def random_hex() -> str:
    """生成短随机 hex 字符串（用于唯一化测试文档）。"""
    return uuid.uuid4().hex[:8]


__all__ = [
    "TestRealRagEndToEnd",
    "TestRealRagRerankerEndToEnd",
    "TestRagFailureUnitTests",
]