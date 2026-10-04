"""WMS Selection Evidence 架构审计（Phase 4.1 Step 21）—— AST / Dataset 边界。

审计对象：

    1. ``tests/test_conversation_context_selection_evidence.py``
       —— 不得 import：sqlalchemy / psycopg / redis / celery / kafka /
       backend.app.db / conversation_context_selector / context_selector；
       不得引入 token 估算符号。
    2. ``tests/fixtures/conversation_context/wms_multiturn_conversations.yaml``
       —— fixture（**不是** production data / DB seed）；schema 冻结。
    3. production 层 —— selector / policy / budget / tokenizer 模块必须缺席。

检查项全部基于 AST / 解析后结构（不做全文扫描）。
纯离线：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_evidence_architecture.py"
_EVIDENCE = "tests/test_conversation_context_selection_evidence.py"
_DATASET = "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "redis",
    "celery",
    "kafka",
    "backend.app.db",
    "backend.app.services.conversation_context_selector",
    "backend.app.services.context_selector",
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
)

_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "estimated_tokens",
    "token_count",
    "count_tokens",
    "chars_per_token",
    "tokens_per_char",
    "tiktoken",
    "max_tokens",
    "max_chars",
    "max_turns",
    "ContextSelector",
    "context_selector",
    "SelectionPolicy",
)

_ALLOWED_CASE_KEYS: frozenset[str] = frozenset({"id", "project_id", "turns"})
_ALLOWED_TURN_KEYS: frozenset[str] = frozenset({"role", "content"})
_ALLOWED_ROLES: frozenset[str] = frozenset({"user", "assistant"})

_DEFERRED_PRODUCTION_MODULES: tuple[str, ...] = (
    "conversation_context_selector.py",
    "conversation_selection_policy.py",
    "conversation_context_budget.py",
    "conversation_context_tokenizer.py",
    "conversation_summarizer.py",
    "conversation_memory.py",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _parse(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_parse(relative)):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifiers(relative: str) -> set[str]:
    tree = _parse(relative)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _dataset_raw() -> list[dict]:
    raw = yaml.safe_load(_source(_DATASET))
    assert isinstance(raw, list) and raw
    return raw


# ============================================================
# 1. Evidence 测试文件边界
# ============================================================


class TestEvidenceFileBoundary:
    def test_1a_no_forbidden_imports(self) -> None:
        modules = _module_imports(_EVIDENCE)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1b_no_token_estimation_or_budget_identifiers(self) -> None:
        identifiers = _identifiers(_EVIDENCE)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_1c_evidence_file_is_offline_only(self) -> None:
        modules = _module_imports(_EVIDENCE)
        for forbidden in ("httpx", "requests", "openai", "socket"):
            assert not any(module.startswith(forbidden) for module in modules), (
                forbidden
            )

    def test_1d_evidence_does_not_import_backend_domain_services(self) -> None:
        """evidence 只做离线模拟：不依赖 ConversationService / Builder / AI。"""
        modules = _module_imports(_EVIDENCE)
        for forbidden in (
            "backend.app.services.conversation_service",
            "backend.app.services.conversation_context_builder",
            "backend.app.services.chat_application_service",
            "backend.app.services.ai_orchestrator_service",
        ):
            assert forbidden not in modules, forbidden


# ============================================================
# 2. Dataset fixture 边界
# ============================================================


class TestDatasetFixtureBoundary:
    def test_2a_dataset_lives_in_tests_fixtures(self) -> None:
        assert (_REPO_ROOT / _DATASET).exists()
        relative = str((_REPO_ROOT / _DATASET).relative_to(_REPO_ROOT)).replace(
            "\\", "/"
        )
        assert relative.startswith("tests/fixtures/")
        assert not relative.startswith("backend/")

    def test_2b_dataset_schema_is_frozen(self) -> None:
        for entry in _dataset_raw():
            assert set(entry) == set(_ALLOWED_CASE_KEYS), entry.get("id")
            for turn in entry["turns"]:
                assert set(turn) == set(_ALLOWED_TURN_KEYS), entry.get("id")
                assert turn["role"] in _ALLOWED_ROLES, entry.get("id")

    def test_2c_dataset_has_no_derived_or_production_fields(self) -> None:
        for entry in _dataset_raw():
            for forbidden in (
                "token_count",
                "estimated_tokens",
                "embedding",
                "relevance_score",
                "expected_sql",
                "model",
                "user_id",
                "tenant_id",
                "database_url",
                "api_key",
            ):
                assert forbidden not in entry, f"{entry.get('id')}:{forbidden}"
                for turn in entry["turns"]:
                    assert forbidden not in turn, f"{entry.get('id')}:{forbidden}"

    def test_2d_dataset_ids_are_unique_and_project_scoped(self) -> None:
        raw = _dataset_raw()
        ids = [entry["id"] for entry in raw]
        assert len(set(ids)) == len(ids)
        assert all(entry["project_id"] == "vietnam-wms" for entry in raw)


# ============================================================
# 3. Production 层（Deferred 模块缺席）
# ============================================================


class TestProductionBoundary:
    def test_3a_no_deferred_production_modules(self) -> None:
        for name in _DEFERRED_PRODUCTION_MODULES:
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name

    def test_3b_no_selector_module_anywhere_in_backend(self) -> None:
        offenders = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in (_REPO_ROOT / "backend/app").rglob("*selector*.py")
            if "conversation" in path.name
        )
        assert offenders == [], offenders

    def test_3c_no_dataset_seed_under_backend(self) -> None:
        offenders = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in (_REPO_ROOT / "backend").rglob("*wms_multiturn*")
        )
        assert offenders == [], offenders


# ============================================================
# 4. 自身审计
# ============================================================


class TestSelfAudit:
    def test_4a_self_import_is_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "tiktoken",
                "openai",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_4b_audit_is_static_only(self) -> None:
        identifiers = _identifiers(_SELF)
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestEvidenceFileBoundary",
    "TestDatasetFixtureBoundary",
    "TestProductionBoundary",
    "TestSelfAudit",
]
