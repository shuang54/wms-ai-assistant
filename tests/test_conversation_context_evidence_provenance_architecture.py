"""Evidence Provenance 架构审计（Phase 4.1 Step 27）—— AST 精确边界。

审计目标：

    1. ``backend/app/`` 中**不得新增**：EvidenceProvenance / EvidenceArtifact /
       RealEvidenceLoader / DatasetVersionResolver 等生产实现；
    2. Conversation 运行时（ConversationService / ChatApplicationService /
       AIOrchestrator / RAG / Tool / TextToSQL）**没有 provenance 依赖**；
    3. 本审计自身纯静态、离线。

精确性说明（避免误报既有模块）：

```text
* "dataset_version" 在 production 中**已存在**，属 Text-to-SQL 评估域
  （text_to_sql_baseline_service / diagnosis / evaluation_taxonomy /
    prompt_experiment / quality_evaluation / real_llm_baseline；Phase 3.9.x 既有）；
  本审计因此**不**检查泛词 "dataset_version"，只检查 Provenance 域专有名词与模块；
* "provenance" 泛词在 production 当前零命中（本审计对此做事实断言）。
```

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evidence_provenance_architecture.py"
_PROVENANCE_TEST = "tests/test_conversation_context_evidence_provenance.py"
_PROVENANCE_DOC = (
    "docs/evaluation/Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit.md"
)

_AUDITED_DIRS: tuple[str, ...] = (
    "backend/app/dto",
    "backend/app/services",
    "backend/app/db",
)

_BACKEND_ROOT = "backend/app"

#: 禁止进入 production 的 Provenance 专有标识符（§二十）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "EvidenceProvenance",
    "EvidenceProvenanceError",
    "EvidenceArtifact",
    "RealEvidenceLoader",
    "DatasetVersionResolver",
    "derive_provenance",
    "reconcile_provenance",
    "build_proposed_report_binding",
    "AnnotatedEvidenceIdentity",
    "VersionMismatchError",
    "SourceTypeMismatchError",
)

#: 禁止出现的 Provenance 模块命名。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "evidence_provenance",
    "evidence_artifact",
    "version_resolver",
    "dataset_version_resolver",
)

#: Conversation 运行时文件（不得有 provenance / dataset_version 依赖）。
_RUNTIME_FILES: tuple[str, ...] = (
    "backend/app/services/conversation_service.py",
    "backend/app/services/chat_application_service.py",
    "backend/app/services/conversation_context_builder.py",
    "backend/app/services/ai_orchestrator_service.py",
    "backend/app/services/rag_service.py",
    "backend/app/services/tool_chat_service.py",
    "backend/app/services/text_to_sql_service.py",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _backend_files() -> list[Path]:
    return sorted((_REPO_ROOT / _BACKEND_ROOT).rglob("*.py"))


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
# 1. Production 边界
# ============================================================


class TestProductionBoundary:
    def test_1a_backend_files_exist(self) -> None:
        files = _backend_files()
        assert files, "backend/app 为空（审计失效）"
        assert len(files) > 50

    def test_1b_no_provenance_identifiers_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_provenance_modules(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            name = path.name
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1d_no_provenance_word_in_backend(self) -> None:
        """事实断言：production 当前无 provenance 语义（含标识符与字符串常量）。"""
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            if "provenance" in {token.lower() for token in tokens}:
                offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1e_dataset_version_domain_is_preexisting_t2sql_only(self) -> None:
        """既有 dataset_version 只属于 Text-to-SQL 评估域，不属 Conversation 证据域。"""
        owners: set[str] = set()
        for path in _backend_files():
            if "dataset_version" in _ast_tokens(path):
                owners.add(_relative(path))
        assert owners, "既有 dataset_version 分布发生变化（审计需更新）"
        for owner in owners:
            assert "text_to_sql" in owner, owner


# ============================================================
# 2. Conversation 运行时无 provenance 依赖
# ============================================================


class TestRuntimeIsolation:
    def test_2a_runtime_files_exist(self) -> None:
        for relative in _RUNTIME_FILES:
            assert (_REPO_ROOT / relative).exists(), relative

    def test_2b_no_provenance_identifiers_in_runtime(self) -> None:
        offenders: list[str] = []
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for forbidden in ("provenance", "dataset_version", "EvidenceProvenance"):
                for token in tokens:
                    if token.lower() == forbidden.lower():
                        offenders.append(f"{relative}:{forbidden}")
        assert offenders == [], offenders

    def test_2c_no_provenance_imports_in_runtime(self) -> None:
        for relative in _RUNTIME_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for forbidden in (
                    "evidence_provenance",
                    "evidence_artifact",
                    "version_resolver",
                ):
                    assert forbidden not in module, f"{relative}:{module}"

    def test_2d_runtime_does_not_import_provenance_test(self) -> None:
        for relative in _RUNTIME_FILES:
            for module in _module_imports(relative):
                assert not module.startswith("tests"), f"{relative}:{module}"


# ============================================================
# 3. Tests 侧契约（允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_3a_provenance_test_defines_contract(self) -> None:
        provenance = _REPO_ROOT / _PROVENANCE_TEST
        assert provenance.exists()
        tokens = _ast_tokens(provenance)
        for name in (
            "EvidenceProvenance",
            "derive_provenance",
            "reconcile_provenance",
            "compare_versions",
            "build_proposed_report_binding",
            "VersionMismatchError",
            "SourceTypeMismatchError",
            "AnnotatedEvidenceIdentity",
        ):
            assert name in tokens, name

    def test_3b_provenance_test_is_offline(self) -> None:
        modules = _module_imports(_PROVENANCE_TEST)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "hashlib",
                "uuid",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_3c_provenance_doc_exists_with_contract_statements(self) -> None:
        doc = _source(_PROVENANCE_DOC)
        for statement in (
            "dataset_version != app_version",
            "VERSION_MISMATCH",
            "SOURCE_TYPE_MISMATCH",
            "Artifact Integrity Hash = DEFERRED",
            "Execution Metadata Contract",
            "project_id",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_3d_no_evidence_directories_created(self) -> None:
        for relative in ("backend/app/evidence", "backend/app/db/evidence"):
            assert not (_REPO_ROOT / relative).exists(), relative
        for directory in _AUDITED_DIRS:
            for path in (_REPO_ROOT / directory).rglob("evidence*.py"):
                raise AssertionError(_relative(path))


# ============================================================
# 4. 自身审计
# ============================================================


class TestSelfAudit:
    def test_4a_audit_imports_are_offline(self) -> None:
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

    def test_4b_audit_is_static_only(self) -> None:
        tokens = _ast_tokens(_REPO_ROOT / _SELF)
        for forbidden in ("TestClient", "environ", "getenv", "subprocess"):
            assert forbidden not in tokens, forbidden

    def test_4c_audit_uses_ast_not_regex_scan(self) -> None:
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
    "TestRuntimeIsolation",
    "TestTestLocalContract",
    "TestSelfAudit",
]
