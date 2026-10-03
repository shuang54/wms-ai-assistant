"""Conversation API Architecture Audit（Phase 4.1 Step 7 —— Audit Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md`

本文件回答一个问题：**Conversation API 的实现范围是否正确？**

检查项（Step 8 起：API 已实现；Step 12 起含 Message POST）：

    1. ``backend/app/api/`` 中 conversations 路由 = 4 条路径 / 5 个操作
       （源码扫描 + OpenAPI 路径表双重校验；不含 DELETE / PATCH / PUT；
        messages 路径同时提供 GET（历史）与 POST（发送，Step 12））
    2. ``backend/app/main.py`` 注册 ``conversations.router``（prefix=/api）
    3. 只存在一个 conversation 路由模块（``api/conversations.py``）
    4. ConversationService **不 import** FastAPI / APIRouter / Request /
       Response / HTTPException / Depends（AST）
    5. ConversationRepository / Conversation ORM 同样不 import FastAPI / AI Core（AST）
    6. 现有 API（/api/ai/chat · Trace · Timeline）保持不变

纯离线：DB = 0 · Network = 0 · DB Writes = 0 · DeepSeek = 0
（不发起 HTTP 请求、不连接 PostgreSQL；仅静态扫描与路由表读取）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import router as trace_router
from backend.app.api.orchestrator_chat import router as orchestrator_router
from backend.app.main import app as fastapi_app

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_api_architecture_audit.py"
_API_DIR = "backend/app/api"
_MAIN = "backend/app/main.py"
_SERVICE = "backend/app/services/conversation_service.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"

#: 应用级禁止出现在 Service 中的 FastAPI 符号
#: （不含通用词 ``status`` —— 它由 import 检查覆盖，避免与 Row.status 误伤）。
FORBIDDEN_FASTAPI_SYMBOLS: tuple[str, ...] = (
    "APIRouter",
    "HTTPException",
    "Request",
    "Response",
    "Depends",
    "FastAPI",
)

#: 期望仍然存在的既有路由（确认未被破坏）。
EXPECTED_EXISTING_PATHS: tuple[str, ...] = (
    "/api/ai/chat",
    "/api/observability/assistant-trace/{assistant_request_id}",
    "/api/observability/assistant-timeline/{assistant_request_id}",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _module_imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _module_identifiers(tree: ast.AST) -> set[str]:
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _app_route_paths() -> set[str]:
    """OpenAPI schema 中的全部路径（含 router prefix；**不发起 HTTP 请求**）。

    注：当前 FastAPI 版本把 ``include_router`` 的 router 以 ``_IncludedRouter``
    形式挂在 ``app.routes`` 上（顶层 ``path`` 为 ``None``），因此用
    ``app.openapi()["paths"]`` 读取最终路径更可靠。
    """
    return set(fastapi_app.openapi().get("paths", {}))


def app_route_paths_by_prefix() -> dict[str, set[str]]:
    """OpenAPI 中以 ``/api/conversations`` 开头的路径 → 方法集合（**不发起 HTTP**）。"""
    return {
        path: {method.upper() for method in operations}
        for path, operations in fastapi_app.openapi().get("paths", {}).items()
        if path.startswith("/api/conversations")
    }


def _router_paths(router: Any) -> set[str]:
    return {str(getattr(route, "path", "")) for route in router.routes}


# ============================================================
# 1：conversations 路由数 = 4（Step 8 已实现）
# ============================================================


class TestConversationRoutesContract:
    """Step 8：Conversation API 已实现 —— 4 条路径（Step 12 起 messages 含 POST）。"""

    #: 冻结的 Conversation 路径（path → 允许的方法）。
    EXPECTED_CONVERSATION_ROUTES: dict[str, set[str]] = {
        "/api/conversations": {"POST"},
        "/api/conversations/{conversation_id}": {"GET"},
        "/api/conversations/{conversation_id}/messages": {"GET", "POST"},
        "/api/conversations/{conversation_id}/archive": {"POST"},
    }

    def test_1a_conversation_paths_declared_in_api_module(self) -> None:
        source = _source("backend/app/api/conversations.py")
        for path in (
            '"/conversations"',
            '"/conversations/{conversation_id}"',
            '"/conversations/{conversation_id}/messages"',
            '"/conversations/{conversation_id}/archive"',
        ):
            assert path in source, path

    def test_1b_exactly_one_conversation_router_module(self) -> None:
        api_root = _REPO_ROOT / _API_DIR
        modules = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in api_root.rglob("conversation*.py")
        )
        assert modules == ["backend/app/api/conversations.py"]

    def test_1c_application_route_table_matches_contract(self) -> None:
        paths = app_route_paths_by_prefix()
        assert paths, "应用路由表为空（审计失效）"
        assert paths == self.EXPECTED_CONVERSATION_ROUTES

    def test_1d_main_py_registers_conversations_router(self) -> None:
        source = _source(_MAIN)
        assert "conversations" in source
        assert "conversations.router, prefix=\"/api\"" in source

    def test_1e_no_delete_patch_put_and_message_post_registered(self) -> None:
        paths = app_route_paths_by_prefix()
        for methods in paths.values():
            assert not methods & {"DELETE", "PATCH", "PUT"}, methods
        assert paths.get("/api/conversations/{conversation_id}/messages") == {
            "GET",
            "POST",
        }

    def test_1f_existing_routes_still_registered(self) -> None:
        paths = _app_route_paths()
        for expected in EXPECTED_EXISTING_PATHS:
            assert expected in paths, expected


# ============================================================
# 2：ConversationService 不依赖 FastAPI
# ============================================================


class TestConversationServiceHasNoFastApi:
    def test_2a_service_does_not_import_fastapi(self) -> None:
        modules = _module_imports(ast.parse(_source(_SERVICE)))
        for module in modules:
            assert not module.startswith("fastapi"), module

    def test_2b_service_has_no_fastapi_symbols(self) -> None:
        identifiers = _module_identifiers(ast.parse(_source(_SERVICE)))
        for forbidden in FORBIDDEN_FASTAPI_SYMBOLS:
            assert forbidden not in identifiers, forbidden

    def test_2c_service_does_not_import_api_layer(self) -> None:
        modules = _module_imports(ast.parse(_source(_SERVICE)))
        for module in modules:
            assert not module.startswith("backend.app.api"), module


# ============================================================
# 3：Repository / ORM 不依赖 FastAPI / AI Core
# ============================================================


class TestPersistenceLayerHasNoWebOrAiDependency:
    def test_3a_repository_has_no_fastapi(self) -> None:
        modules = _module_imports(ast.parse(_source(_REPOSITORY)))
        for module in modules:
            assert not module.startswith("fastapi"), module

    def test_3b_orm_models_have_no_fastapi_or_ai_imports(self) -> None:
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            modules = _module_imports(ast.parse(_source(relative)))
            for module in modules:
                assert not module.startswith("fastapi"), module
                assert not module.startswith("backend.app.services"), module
                assert not module.startswith("backend.app.llm"), module
                assert not module.startswith("backend.app.rag"), module


# ============================================================
# 4：既有 API Contract 未变化
# ============================================================


class TestExistingApiUnchanged:
    def test_4a_ai_chat_route_unchanged(self) -> None:
        paths = _router_paths(orchestrator_router)
        assert "/ai/chat" in paths
        assert not [p for p in paths if "conversation" in p.lower()]

    def test_4b_trace_route_unchanged(self) -> None:
        paths = _router_paths(trace_router)
        assert "/observability/assistant-trace/{assistant_request_id}" in paths
        assert "conversation_id" not in _source(
            "backend/app/api/assistant_trace.py"
        )

    def test_4c_timeline_route_unchanged(self) -> None:
        paths = _router_paths(timeline_router)
        assert "/observability/assistant-timeline/{assistant_request_id}" in paths
        assert "conversation_id" not in _source(
            "backend/app/api/assistant_timeline.py"
        )


# ============================================================
# 5：自身审计（离线）
# ============================================================


class TestSelfAudit:
    def test_5a_self_import_is_offline(self) -> None:
        modules = _module_imports(ast.parse(_source(_SELF)))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in (
                "sqlalchemy",
                "psycopg",
                "alembic",
                "redis",
                "backend.app.db",
                "httpx",
                "requests",
                "aiohttp",
                "openai",
                "socket",
                "urllib",
            ):
                assert not module.startswith(prefix), module


__all__ = [
    "FORBIDDEN_FASTAPI_SYMBOLS",
    "TestConversationRoutesDoNotExist",
    "TestConversationServiceHasNoFastApi",
    "TestPersistenceLayerHasNoWebOrAiDependency",
    "TestExistingApiUnchanged",
    "TestSelfAudit",
]
