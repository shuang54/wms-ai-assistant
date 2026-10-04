"""Annotation Review 架构审计（Phase 4.1 Step 29）—— AST 精确边界。

审计目标：

    1. ``backend/app/`` 中**不得出现**：AnnotationReviewService /
       AnnotationComparisonService / DomainReviewService / AnnotatorService，
       以及 annotation_review / annotation_comparison / domain_review 模块；
    2. 生产模块不得 import tests / annotation review / evaluation review；
    3. Annotation Review 属 offline Evaluation Layer，不进入 production runtime；
    4. 本审计自身纯静态、离线。

事实依据：production 中 "annotation"（含泛词）当前零命中（Step 28 侦察 + 审计确认）。

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_review_architecture.py"
_REVIEW_TEST = "tests/test_conversation_context_annotation_review.py"
_REVIEW_FIXTURE = "tests/fixtures/conversation_context/annotation_review_cases.yaml"
_REVIEW_DOC = "docs/evaluation/Phase 4.1 Step 29 — Annotation Review Procedure.md"

_BACKEND_ROOT = "backend/app"

#: 禁止进入 production 的 Step 29 专有标识符。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "AnnotationReviewService",
    "AnnotationComparisonService",
    "DomainReviewService",
    "AnnotatorService",
    "AnnotationDraft",
    "ComparisonOutcome",
    "ReviewStatistics",
    "compare_annotations",
    "finalize_domain_review",
    "summarize_reviews",
    "format_agreement_rate",
    "review_phase",
    "DOMAIN_REVIEW_REQUIRED",
    "FINAL_REVIEW",
)

#: 禁止出现的模块命名（组合词）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "annotation_review",
    "annotation_comparison",
    "domain_review",
    "annotator",
)

#: 禁止的 runtime coupling：生产模块不得 import tests / annotation review / evaluation review。
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
    """AST 代码标识符（不含字符串常量）—— 用于自身静态性审计。"""
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
# 1. Production 边界（§二十一）
# ============================================================


class TestProductionBoundary:
    def test_1a_backend_files_exist(self) -> None:
        files = _backend_files()
        assert files, "backend/app 为空（审计失效）"
        assert len(files) > 50

    def test_1b_no_review_service_identifiers_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_annotation_review_modules_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            name = path.name.lower()
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1d_annotation_word_still_absent_in_backend(self) -> None:
        """事实断言（延续 Step 28）：production 无 annotation 语义。"""
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            if "annotation" in {token.lower() for token in tokens}:
                offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1e_no_review_directories_created(self) -> None:
        for relative in (
            "backend/app/annotation",
            "backend/app/evaluation",
            "backend/app/db/annotation",
            "backend/app/review",
        ):
            assert not (_REPO_ROOT / relative).exists(), relative


# ============================================================
# 2. Runtime isolation（§二十一）
# ============================================================


class TestRuntimeIsolation:
    def test_2a_runtime_files_exist(self) -> None:
        for relative in _RUNTIME_FILES:
            assert (_REPO_ROOT / relative).exists(), relative

    def test_2b_runtime_does_not_import_tests_or_review(self) -> None:
        for relative in _RUNTIME_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                assert not module.startswith("tests"), f"{relative}:{module}"
                for forbidden in (
                    "annotation",
                    "annotation_review",
                    "domain_review",
                    "evaluation_review",
                    "annotator",
                ):
                    assert forbidden not in module, f"{relative}:{module}"

    def test_2c_runtime_has_no_review_tokens(self) -> None:
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for forbidden in (
                "annotator",
                "review_status",
                "domain_review",
                "annotation_review",
                "comparison_outcome",
            ):
                assert forbidden not in tokens, f"{relative}:{forbidden}"


# ============================================================
# 3. Tests 侧契约（允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_3a_review_test_defines_contract(self) -> None:
        review = _REPO_ROOT / _REVIEW_TEST
        assert review.exists()
        tokens = _ast_tokens(review)
        for name in (
            "AnnotationDraft",
            "ComparisonOutcome",
            "ReviewStatistics",
            "compare_annotations",
            "review_phase",
            "finalize_domain_review",
            "format_agreement_rate",
            "summarize_reviews",
        ):
            assert name in tokens, name

    def test_3b_review_test_is_offline(self) -> None:
        modules = _module_imports(_REVIEW_TEST)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "yaml",
                "backend.app.db",
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_3c_review_test_reuses_prior_contracts(self) -> None:
        modules = _module_imports(_REVIEW_TEST)
        for required in (
            "tests.test_conversation_context_annotation_workflow",
            "tests.test_conversation_context_evaluation_rubric",
        ):
            assert required in modules, sorted(modules)

    def test_3d_review_fixture_is_synthetic_only(self) -> None:
        fixture = _source(_REVIEW_FIXTURE)
        assert "synthetic" in fixture
        assert "非生产数据" in fixture
        for forbidden in (
            "annotation:",
            "reference:",
            "impact:",
            "conversation_id",
            "turn_id",
            "assistant_request_id",
            "provider_request_id",
        ):
            assert forbidden not in fixture, forbidden

    def test_3e_review_doc_exists_with_contract_statements(self) -> None:
        doc = _source(_REVIEW_DOC)
        for statement in (
            "DOMAIN_REVIEW_REQUIRED",
            "FINAL_REVIEW",
            "agreement_rate",
            "N/A",
            "G1 = BLOCKED",
            "G3 Evidence = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_3f_step21_dataset_and_prior_fixtures_unchanged(self) -> None:
        dataset = _source(
            "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
        )
        for field in ("annotation:", "reference:", "impact:"):
            assert field not in dataset, field
        # fixture 目录恰好五个文件（Step 21 dataset + Step 29/30/31/32 fixtures）
        files = sorted(
            path.name
            for path in (
                _REPO_ROOT / "tests/fixtures/conversation_context"
            ).rglob("*")
            if path.is_file()
        )
        assert files == [
            "annotation_finalization_cases.yaml",
            "annotation_review_cases.yaml",
            "real_annotation_execution_cases.yaml",
            "real_evidence_import_cases.yaml",
            "wms_multiturn_conversations.yaml",
        ], files


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
