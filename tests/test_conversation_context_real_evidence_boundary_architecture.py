"""Real Evidence Boundary 架构审计（Phase 4.1 Step 26）—— AST 精确边界。

审计目标：

    1. ``backend/app/`` 中**不得出现**：real evidence loader / deidentify tool /
       anonymize tool / evaluation artifact importer（模块名 + AST 标识符）；
    2. ``tests/`` 允许 test-local loader / contract DTO / validation helper；
    3. boundary 测试 helper 不得依赖 sqlalchemy / psycopg / redis / celery / kafka /
       backend.app.db；
    4. 不得与运行时耦合（ConversationService / ChatApplicationService /
       AIOrchestrator / RAG / Tool / TextToSQL）；
    5. 本审计自身纯静态、离线。

精确性说明（避免误报既有模块）：

```text
backend/app/projects/semantic_loader.py 为既有项目语义加载器（Phase 3.x），
与本域无关；本审计因此只检查 evidence / artifact / deidentify 组合词。
```

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_real_evidence_boundary_architecture.py"
_BOUNDARY_TEST = "tests/test_conversation_context_real_evidence_boundary.py"
_BOUNDARY_DOC = "docs/evaluation/Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md"

_PRODUCTION_ROOT = "backend/app"

#: production 禁止的模块命名关键词（组合词；排除既有 semantic_loader）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "evidence_loader",
    "evidence_artifact",
    "evidence_importer",
    "real_evidence",
    "artifact_importer",
    "deidentify",
    "anonymize",
    "redact",
)

#: production 禁止的 AST 标识符（loader / 脱敏 / importer 专有名词）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "EvidenceArtifactLoader",
    "EvidenceArtifactValidator",
    "RealEvidenceLoader",
    "load_real_wms_evidence_artifact",
    "validate_artifact",
    "derive_readiness",
    "deidentify",
    "anonymize",
    "redact_pii",
    "EvidenceArtifactError",
    "LoaderRequest",
)

#: boundary 测试 helper 禁止依赖（§二十二 Forbidden imports）。
_FORBIDDEN_TEST_IMPORTS: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "redis",
    "celery",
    "kafka",
    "backend.app.db",
)

#: 禁止的运行时耦合（§二十二 No runtime coupling）。
_FORBIDDEN_RUNTIME_MODULES: tuple[str, ...] = (
    "backend.app.services.conversation_service",
    "backend.app.services.chat_application_service",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.rag_service",
    "backend.app.services.tool_chat_service",
    "backend.app.services.text_to_sql_service",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _production_files() -> list[Path]:
    return sorted((_REPO_ROOT / _PRODUCTION_ROOT).rglob("*.py"))


def _ast_tokens(path: Path) -> set[str]:
    tokens: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, ast.arg):
            tokens.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.add(node.name)
    return tokens


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _relative(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


# ============================================================
# 1. Production 边界（§二十二 Production）
# ============================================================


class TestProductionBoundary:
    def test_1a_production_files_exist(self) -> None:
        files = _production_files()
        assert files, "production 目录为空（审计失效）"
        assert len(files) > 50

    def test_1b_no_evidence_loader_or_deidentification_modules(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            name = path.name.lower()
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1c_no_loader_or_artifact_identifiers(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1d_semantic_loader_is_preexisting_and_unrelated(self) -> None:
        """既有 projects/semantic_loader.py（项目语义加载）不属 Evaluation 域。"""
        loader = _REPO_ROOT / "backend/app/projects/semantic_loader.py"
        assert loader.exists()
        tokens = _ast_tokens(loader)
        for forbidden in ("load_real_wms_evidence_artifact", "EvidenceArtifactLoader"):
            assert forbidden not in tokens, forbidden
        name = loader.name.lower()
        assert "evidence" not in name
        assert "artifact" not in name


# ============================================================
# 2. Tests 侧（§二十二 Tests：允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_2a_boundary_test_exists_with_contract_components(self) -> None:
        boundary = _REPO_ROOT / _BOUNDARY_TEST
        assert boundary.exists()
        tokens = _ast_tokens(boundary)
        for name in (
            "LoaderRequest",
            "load_real_wms_evidence_artifact",
            "validate_artifact",
            "derive_readiness",
            "EvidenceArtifactError",
            "collect_forbidden_fields",
        ):
            assert name in tokens, name

    def test_2b_no_forbidden_test_imports(self) -> None:
        modules = _module_imports(_BOUNDARY_TEST)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in _FORBIDDEN_TEST_IMPORTS:
                assert not module.startswith(forbidden), f"{module}"

    def test_2c_no_runtime_coupling(self) -> None:
        modules = _module_imports(_BOUNDARY_TEST)
        for forbidden in _FORBIDDEN_RUNTIME_MODULES:
            assert forbidden not in modules, forbidden

    def test_2d_boundary_test_is_offline(self) -> None:
        modules = _module_imports(_BOUNDARY_TEST)
        for module in modules:
            for forbidden in ("httpx", "requests", "openai", "tiktoken"):
                assert not module.startswith(forbidden), module

    def test_2e_boundary_doc_exists_with_boundary_statements(self) -> None:
        doc = _source(_BOUNDARY_DOC)
        for statement in (
            "De-identification happens outside repo.",
            "project_id != authorization",
            "Step21 dataset remains SYNTHETIC_ONLY.",
            "does not enter production runtime.",
            "REAL_DEIDENTIFIED",
        ):
            assert statement in doc, statement

    def test_2f_step21_dataset_unchanged(self) -> None:
        dataset = _source(
            "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
        )
        for field in ("annotation:", "reference:", "impact:", "dataset_version:"):
            assert field not in dataset, field


# ============================================================
# 3. 自身审计
# ============================================================


class TestSelfAudit:
    def test_3a_audit_imports_are_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_3b_audit_is_static_only(self) -> None:
        tokens = _ast_tokens(_REPO_ROOT / _SELF)
        for forbidden in ("TestClient", "environ", "getenv", "subprocess"):
            assert forbidden not in tokens, forbidden

    def test_3c_audit_uses_ast_not_regex_scan(self) -> None:
        modules = _module_imports(_SELF)
        assert "ast" in modules
        assert "re" not in modules, "架构审计不得依赖正则全文扫描"
        function_names = {
            node.name
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.FunctionDef)
        }
        assert "_ast_tokens" in function_names
        assert "_module_imports" in function_names


__all__ = [
    "TestProductionBoundary",
    "TestTestLocalContract",
    "TestSelfAudit",
]
