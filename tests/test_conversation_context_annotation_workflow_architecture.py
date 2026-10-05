"""Annotation Workflow 架构审计（Phase 4.1 Step 28）—— AST 精确边界。

审计目标：

    1. ``backend/app/`` 中**不得出现**：AnnotationService / AnnotatedEvidence /
       AnnotationWorkflow / EvidenceAnnotation 等生产实现；
    2. ConversationService / ChatApplicationService / AIOrchestrator 不得 import
       tests / annotation workflow / evaluation annotation；
    3. Annotation 属 offline Evaluation Layer，不进入 production runtime；
    4. 本审计自身纯静态、离线。

事实依据（侦察确认）：

```text
"annotation"（含泛词）在 backend/app 当前**零命中**；
本审计因此对泛词也做精确断言（含字符串常量）。
```

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_workflow_architecture.py"
_WORKFLOW_TEST = "tests/test_conversation_context_annotation_workflow.py"
_WORKFLOW_DOC = (
    "docs/evaluation/Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract.md"
)

_BACKEND_ROOT = "backend/app"

_AUDITED_DIRS: tuple[str, ...] = (
    "backend/app/dto",
    "backend/app/services",
    "backend/app/db",
)

#: 禁止进入 production 的 Annotation 专有标识符。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "AnnotationService",
    "AnnotatedEvidence",
    "AnnotationWorkflow",
    "EvidenceAnnotation",
    "CaseAnnotation",
    "ReferenceAnnotation",
    "DependencyAnnotation",
    "AnnotatorIdentity",
    "annotation_version",
    "AnnotationWorkflowError",
    "AnnotationCompletenessError",
    "DatasetVersionMismatchError",
    "CaseNotFoundError",
    "annotation_status",
    "review_status",
)

#: 禁止出现的 Annotation 模块命名。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "annotation",
    "annotated_evidence",
    "annotation_workflow",
)

#: 禁止的 runtime coupling（§二十二）。
_RUNTIME_FILES: tuple[str, ...] = (
    "backend/app/services/conversation_service.py",
    "backend/app/services/chat_application_service.py",
    "backend/app/services/conversation_context_builder.py",
    "backend/app/services/ai_orchestrator_service.py",
    "backend/app/services/ai_router_service.py",
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens.add(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.add(node.name)
    return tokens


def _code_tokens(path: Path) -> set[str]:
    """AST 代码标识符（**不含字符串常量**）—— 用于自身静态性审计。"""
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

    def test_1b_no_annotation_identifiers_in_backend(self) -> None:
        """Step 37：annotation 持久化已存在 —— 但只允许冻结的 Step 37 模块。"""
        frozen = (
            "backend/app/db/models/evidence_record.py",
            "backend/app/db/models/evidence_annotation_record.py",
            "backend/app/db/evidence_repository.py",
        )
        offenders: list[str] = []
        for path in _backend_files():
            if _relative(path) in frozen:
                continue
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_annotation_word_in_backend(self) -> None:
        """事实断言：production 当前无 annotation 语义（含字符串常量）。"""
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            if "annotation" in {token.lower() for token in tokens}:
                offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1d_no_annotation_modules_in_backend(self) -> None:
        """Step 37：只允许冻结的 annotation 持久化模块；API / Service / Workflow 仍禁止。"""
        frozen = (
            "backend/app/db/models/evidence_record.py",
            "backend/app/db/models/evidence_annotation_record.py",
            "backend/app/db/evidence_repository.py",
        )
        offenders: list[str] = []
        for path in _backend_files():
            if _relative(path) in frozen:
                continue
            name = path.name.lower()
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1e_no_annotation_directories_created(self) -> None:
        for relative in (
            "backend/app/annotation",
            "backend/app/evaluation",
            "backend/app/db/annotation",
        ):
            assert not (_REPO_ROOT / relative).exists(), relative
        for directory in _AUDITED_DIRS:
            for path in (_REPO_ROOT / directory).rglob("annotation*.py"):
                raise AssertionError(_relative(path))


# ============================================================
# 2. Runtime isolation（§二十二）
# ============================================================


class TestRuntimeIsolation:
    def test_2a_runtime_files_exist(self) -> None:
        for relative in _RUNTIME_FILES:
            assert (_REPO_ROOT / relative).exists(), relative

    def test_2b_no_annotation_tokens_in_runtime(self) -> None:
        offenders: list[str] = []
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for token in tokens:
                lowered = token.lower()
                if lowered in ("annotation", "annotation_version", "annotated_evidence"):
                    offenders.append(f"{relative}:{token}")
        assert offenders == [], offenders

    def test_2c_runtime_does_not_import_tests_or_evaluation(self) -> None:
        """运行时不得 import tests / annotation workflow / evaluation annotation。"""
        for relative in _RUNTIME_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                assert not module.startswith("tests"), f"{relative}:{module}"
                for forbidden in (
                    "annotation",
                    "annotated_evidence",
                    "evidence_annotation",
                ):
                    assert forbidden not in module, f"{relative}:{module}"

    def test_2d_runtime_has_no_annotation_parameters(self) -> None:
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for forbidden in ("annotator", "review_status", "dependency_annotation"):
                assert forbidden not in tokens, f"{relative}:{forbidden}"


# ============================================================
# 3. Tests 侧契约（允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_3a_workflow_test_defines_contract(self) -> None:
        workflow = _REPO_ROOT / _WORKFLOW_TEST
        assert workflow.exists()
        tokens = _ast_tokens(workflow)
        for name in (
            "AnnotatedEvidence",
            "CaseAnnotation",
            "ReferenceAnnotation",
            "AnnotatorIdentity",
            "AnnotationWorkflowError",
            "DatasetVersionMismatchError",
            "CaseNotFoundError",
            "evaluate_g3_evidence",
            "check_dataset_version",
            "check_case_ids",
        ):
            assert name in tokens, name

    def test_3b_workflow_test_is_offline(self) -> None:
        modules = _module_imports(_WORKFLOW_TEST)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_3c_workflow_test_reuses_step24_contract(self) -> None:
        """复用 Step 24 契约（不重新设计 annotation schema）。"""
        modules = _module_imports(_WORKFLOW_TEST)
        assert (
            "tests.test_conversation_context_evaluation_rubric" in modules
        ), sorted(modules)
        assert (
            "tests.test_conversation_context_evidence_collection_procedure" in modules
        ), sorted(modules)

    def test_3d_workflow_doc_exists_with_contract_statements(self) -> None:
        doc = _source(_WORKFLOW_DOC)
        for statement in (
            "DATASET_VERSION_MISMATCH",
            "CASE_NOT_FOUND",
            "LLM suggestion",
            "Human / Domain Expert Review",
            "Final Annotation",
            "project_id != authorization",
            "G3 Evidence = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_3e_step26_artifact_and_step27_provenance_unchanged(self) -> None:
        # Step 26 boundary：artifact 仍禁止 annotation 字段
        boundary = _ast_tokens(
            _REPO_ROOT / "tests/test_conversation_context_real_evidence_boundary.py"
        )
        assert "FORBIDDEN_ARTIFACT_FIELDS" in boundary
        # Step 27 provenance：仍有 Proposed Report Provenance Contract
        provenance = _ast_tokens(
            _REPO_ROOT / "tests/test_conversation_context_evidence_provenance.py"
        )
        assert "EvidenceProvenance" in provenance
        # Step 21 dataset 未被修改
        dataset = _source(
            "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
        )
        for field in ("annotation:", "reference:", "impact:"):
            assert field not in dataset, field


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
        tokens = _code_tokens(_REPO_ROOT / _SELF)
        assert tokens, "AST 未解析到标识符（审计失效）"
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
