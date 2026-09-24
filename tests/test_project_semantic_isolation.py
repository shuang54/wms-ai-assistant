"""Project Semantic Context 隔离集成测试（Phase 3.8.3）。

默认 SKIP；``RUN_DB_TESTS=1``（+ DATABASE_URL）后执行。

覆盖任务书验收（§九 / §十一 / §十五 / §十六 / §十七 / §二十一）：

    测试项目（单 PostgreSQL 实例，双 schema，表结构相同、语义故意不同）：

        project-a：schema project_a
                   语义 A：inventory=库存 / material_code=物料编码 / qty=库存数量
        project-b：schema project_b
                   语义 B：inventory=可用库存 / material_code=产品编号 / qty=可用数量

    验证：
    - project_id → Semantic 唯一决定（InMemory Provider 显式注册）
    - Text-to-SQL 拿到的 database_context 含且仅含该项目语义
      （Fake Generator 记录，不调真实 LLM，§16.6）
    - Schema A + Semantic A / Schema B + Semantic B，绝不交叉
    - API E2E：POST /api/ai/chat 两项目分别拿到各自语义
    - 安全：HTTP 注入 semantic_file / semantic 被忽略
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectRegistration,
)
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_provider import (
    InMemoryProjectSemanticProvider,
)
from backend.app.services.project_orchestrator_factory import (
    build_orchestrator_for_project,
)

import backend.app.services.project_orchestrator_factory as factory_module


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


from backend.app.config import settings

requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "semantic isolation tests",
)

SCHEMA_A = "project_a"
SCHEMA_B = "project_b"

QUESTION_A = "库存最多的物料有哪些"      # A 术语
QUESTION_B = "可用库存最多的产品有哪些"   # B 术语

CAPS_T2S = ProjectCapabilities(
    tool_names=(),           # 本文件聚焦 T2S 语义链路，不需要 Tool
    knowledge_enabled=False,  # 问题必走 TEXT_TO_SQL / 分析规则
    text_to_sql_enabled=True,
)

# ---- 语义 A / B（同一表结构，业务名称不同；§七 / §二十一） ----

SEMANTIC_A = ProjectSemantic(
    tables=(
        TableSemantic(
            table="project_a.inventory",
            business_name="库存",
            description="库存明细",
            aliases=("库存",),
        ),
    ),
    columns=(
        ColumnSemantic(
            table="project_a.inventory",
            column="material_code",
            business_name="物料编码",
        ),
        ColumnSemantic(
            table="project_a.inventory",
            column="qty",
            business_name="库存数量",
        ),
    ),
)

SEMANTIC_B = ProjectSemantic(
    tables=(
        TableSemantic(
            table="project_b.inventory",
            business_name="可用库存",
            description="可用库存明细",
            aliases=("可用库存",),
        ),
    ),
    columns=(
        ColumnSemantic(
            table="project_b.inventory",
            column="material_code",
            business_name="产品编号",
        ),
        ColumnSemantic(
            table="project_b.inventory",
            column="qty",
            business_name="可用数量",
        ),
    ),
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(scope="module", autouse=True)
def _schemas(engine):
    """module 级 autouse：project_a / project_b 双 schema + 相同表结构。"""
    with engine.begin() as conn:
        for schema in (SCHEMA_A, SCHEMA_B):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(
                f'CREATE TABLE "{schema}"."inventory" ('
                f"  material_code VARCHAR(64) PRIMARY KEY,"
                f"  qty NUMERIC NOT NULL)"
            ))
        conn.execute(text(
            f'INSERT INTO "{SCHEMA_A}"."inventory" (material_code, qty) '
            f"VALUES ('M-A', 100)"
        ))
        conn.execute(text(
            f'INSERT INTO "{SCHEMA_B}"."inventory" (material_code, qty) '
            f"VALUES ('M-B', 999)"
        ))
    yield
    with engine.begin() as conn:
        for schema in (SCHEMA_A, SCHEMA_B):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _registration(project_id: str, schema_name: str) -> ProjectRegistration:
    return ProjectRegistration(
        context=ProjectContext(
            project_id=project_id,
            project_name=f"Project {project_id}",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        ),
        schema_name=schema_name,
        capabilities=CAPS_T2S,
    )


@contextmanager
def _test_registry():
    registry = InMemoryProjectRegistry()
    registry.register("project-a", _registration("project-a", SCHEMA_A))
    registry.register("project-b", _registration("project-b", SCHEMA_B))
    yield registry


def _semantic_provider() -> InMemoryProjectSemanticProvider:
    """服务器端显式注册的 A / B 语义（project_id 唯一决定 Semantic）。"""
    provider = InMemoryProjectSemanticProvider()
    provider.register("project-a", SEMANTIC_A)
    provider.register("project-b", SEMANTIC_B)
    return provider


class _RecordingT2S:
    """Fake Generator：记录 database_context / allowed_tables / schema（§16.6），
    返回合法 SQL（真实 Validator / Executor 链路照常执行）。"""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.calls: list[dict] = []

    async def generate(self, question, *, database_context="",
                       allowed_tables=None, schema=None, max_rows=100):
        self.calls.append(
            {
                "question": question,
                "database_context": database_context,
                "allowed_tables": tuple(allowed_tables or ()),
                "schema": schema,
            }
        )
        return SimpleNamespace(sql=self.sql)


def _build(project_id: str, generator: _RecordingT2S):
    """构造绑定项目（真实数据源 + 语义 Provider）的 Orchestrator。

    Fake Generator 必须在 build **之前**挂到 base 单例上
    （factory 在构造时拷贝 base._text_to_sql 的引用）。
    """
    from backend.app.api import orchestrator_chat as orch_module

    target = orch_module._default_orchestrator
    original = target._text_to_sql
    target._text_to_sql = generator
    try:
        with _test_registry() as registry:
            orch = build_orchestrator_for_project(
                project_id,
                base=orch_module._default_orchestrator,
                registry=registry,
                semantic_provider=_semantic_provider(),
            )
    finally:
        target._text_to_sql = original
    return orch, generator


def _execute(project_id: str, question: str):
    """一步完成：挂 Fake Generator → build → execute（返回结果 + 记录）。"""
    schema = SCHEMA_A if project_id == "project-a" else SCHEMA_B
    generator = _RecordingT2S(
        sql=f'SELECT material_code, qty FROM "{schema}"."inventory" LIMIT 10'
    )
    orch, _ = _build(project_id, generator)
    result = asyncio.run(orch.execute(question))
    return result, generator


# ============================================================
# 16.6 Text-to-SQL：拿到正确的项目 Semantic（Schema + Semantic 同源）
# ============================================================

@requires_db
class TestTextToSqlSemanticIsolation:
    def test_project_a_gets_semantic_a(self) -> None:
        """A 的分析问题 → database_context 含 Schema A + Semantic A
        （库存 / 物料编码 / project_a.inventory）。"""
        result, generator = _execute("project-a", QUESTION_A)

        assert result.route.value == "text_to_sql"
        assert len(generator.calls) == 1
        call = generator.calls[0]
        ctx = call["database_context"]
        # Schema A
        assert "project_a.inventory" in ctx
        # Semantic A
        assert "库存" in ctx and "物料编码" in ctx and "库存数量" in ctx
        # 绝不含 B 的语义 / schema
        assert "可用库存" not in ctx
        assert "产品编号" not in ctx
        assert "可用数量" not in ctx
        assert "project_b" not in ctx
        # Selector 用 A 语义命中表
        assert call["allowed_tables"] == ("project_a.inventory",)
        assert call["schema"].schema_name == "project_a"

    def test_project_b_gets_semantic_b(self) -> None:
        """B 的分析问题 → database_context 含 Schema B + Semantic B。"""
        result, generator = _execute("project-b", QUESTION_B)

        assert result.route.value == "text_to_sql"
        assert len(generator.calls) == 1
        call = generator.calls[0]
        ctx = call["database_context"]
        assert "project_b.inventory" in ctx
        assert "可用库存" in ctx and "产品编号" in ctx and "可用数量" in ctx
        assert "物料编码" not in ctx
        assert "库存数量" not in ctx
        assert "project_a" not in ctx
        assert call["allowed_tables"] == ("project_b.inventory",)
        assert call["schema"].schema_name == "project_b"

    def test_same_question_different_semantics(self) -> None:
        """同一个问句（含 A 的"库存"术语，同时是 B 的"可用库存"子串）
        在两个项目下都命中各自的 inventory（语义影响匹配，§十一）。"""
        question = "库存最多的物料有哪些"
        result_a, gen_a = _execute("project-a", question)
        assert gen_a.calls[0]["allowed_tables"] == ("project_a.inventory",)

        # B 项目：A 语义的 "库存" 不在 B 语义中；但问句不含 B 术语 →
        # Selector 零匹配（allowed_tables 为空，Generator 仍拿到
        # Schema B + Semantic B 的完整 context）
        result_b, gen_b = _execute("project-b", question)
        ctx_b = gen_b.calls[0]["database_context"]
        assert "可用库存" in ctx_b  # B 语义
        assert "project_b.inventory" in ctx_b
        assert gen_b.calls[0]["schema"].schema_name == "project_b"

    def test_generated_sql_executes_against_project_schema(self) -> None:
        """Fake SQL 在项目各自 schema 上执行（A→100，B→999，
        证明 Executor 与 Schema 同源）。"""
        result_a, _ = _execute("project-a", QUESTION_A)
        assert result_a.data.rows[0][1] == 100.0

        result_b, _ = _execute("project-b", QUESTION_B)
        assert result_b.data.rows[0][1] == 999.0


# ============================================================
# 默认项目回归：vietnam-wms 语义（LoaderBacked 默认 Provider）
# ============================================================

@requires_db
class TestDefaultProjectSemantic:
    def test_default_project_uses_yaml_semantic(self) -> None:
        """vietnam-wms → 默认 LoaderBacked Provider → vietnam-wms.yaml
        （旧行为等价，回归）。"""
        from backend.app.api import orchestrator_chat as orch_module

        orch = build_orchestrator_for_project(
            settings.project.project_id,
            base=orch_module._default_orchestrator,
        )
        # Provider 语义解析（同步 resolve；真实 DB schema inspect）
        project, schema, semantic = asyncio.run(
            asyncio.to_thread(orch._project_provider.resolve)
        )
        assert project.project_id == settings.project.project_id
        assert semantic.tables  # vietnam-wms.yaml 配置了表语义
        assert any(
            "knowledge_document" in t.table for t in semantic.tables
        )


# ============================================================
# API E2E（§十七）：project_id → 服务器端 Semantic
# ============================================================

@requires_db
class TestApiSemanticE2E:
    @contextmanager
    def _api(self, monkeypatch, generator: _RecordingT2S):
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app

        with _test_registry() as registry:
            monkeypatch.setattr(
                factory_module, "get_default_project_registry", lambda: registry
            )
            monkeypatch.setattr(
                factory_module,
                "get_default_project_semantic_provider",
                lambda: _semantic_provider(),
            )
            target = orch_module._default_orchestrator
            original = target._text_to_sql
            target._text_to_sql = generator
            try:
                with TestClient(app) as client:
                    yield client
            finally:
                target._text_to_sql = original

    def _post(self, client, project_id, question, **extra):
        payload = {"question": question, "project_id": project_id, **extra}
        return client.post("/api/ai/chat", json=payload)

    def test_api_project_a_semantic_a(self, monkeypatch) -> None:
        """A：库存最多的物料有哪些 → route=text_to_sql，
        Generator 拿到 Schema A + Semantic A。"""
        generator = _RecordingT2S(
            sql='SELECT material_code, qty FROM project_a.inventory LIMIT 10'
        )
        with self._api(monkeypatch, generator) as client:
            resp = self._post(client, "project-a", QUESTION_A)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "text_to_sql"
        ctx = generator.calls[0]["database_context"]
        assert "库存" in ctx and "project_a.inventory" in ctx
        assert "可用库存" not in ctx and "产品编号" not in ctx

    def test_api_project_b_semantic_b(self, monkeypatch) -> None:
        """B：可用库存最多的产品有哪些 → Semantic B。"""
        generator = _RecordingT2S(
            sql='SELECT material_code, qty FROM project_b.inventory LIMIT 10'
        )
        with self._api(monkeypatch, generator) as client:
            resp = self._post(client, "project-b", QUESTION_B)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "text_to_sql"
        ctx = generator.calls[0]["database_context"]
        assert "可用库存" in ctx and "产品编号" in ctx and "project_b.inventory" in ctx
        assert "物料编码" not in ctx and "库存数量" not in ctx

    # ---- 安全（§十五） ----

    def test_api_semantic_file_injection_ignored(self, monkeypatch) -> None:
        """请求体注入 semantic_file / semantic / semantic_id →
        被忽略（Pydantic 丢弃未知字段），A 仍拿到 Semantic A。"""
        generator = _RecordingT2S(
            sql='SELECT material_code, qty FROM project_a.inventory LIMIT 10'
        )
        with self._api(monkeypatch, generator) as client:
            resp = self._post(
                client,
                "project-a",
                QUESTION_A,
                semantic_file="project-b.yaml",
                semantic={"tables": [{"table": "x", "business_name": "伪造"}]},
                semantic_id="project-b",
            )
        assert resp.status_code == 200, resp.text
        ctx = generator.calls[0]["database_context"]
        # 仍是 A 的语义，注入无效
        assert "库存" in ctx and "project_a.inventory" in ctx
        assert "可用库存" not in ctx
        assert "伪造" not in ctx

    def test_api_unknown_project_404(self, monkeypatch) -> None:
        generator = _RecordingT2S(sql="SELECT 1")
        with self._api(monkeypatch, generator) as client:
            resp = self._post(client, "ghost", QUESTION_A)
        assert resp.status_code == 404, resp.text
        assert generator.calls == []  # 未触达 Generator


__all__ = [
    "TestTextToSqlSemanticIsolation",
    "TestDefaultProjectSemantic",
    "TestApiSemanticE2E",
]
