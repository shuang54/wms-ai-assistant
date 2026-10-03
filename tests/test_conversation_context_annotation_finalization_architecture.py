"""Annotation Finalization 架构审计（Phase 4.1 Step 30）—— AST 精确边界。

审计目标：

    1. ``backend/app/**/*.py`` 中**不得出现**新的：finalization / annotation_review /
       annotation_comparison / domain_review / annotator 生产实现；
    2. 生产模块不得 import tests / docs / evaluation；
    3. Annotation Finalization 属 offline Evaluation Layer，不进入 production runtime；
    4. 本审计自身纯静态、离线。

精确性说明（避免误报既有模块）：

```text
* "finaliz" 词干在 production 中已存在（text_to_sql_semantic_result_evaluation_service.py /
  db/base.py 的既有 finalize 语义，属 Text-to-SQL / DB 会话域）；
  本审计因此只检查 Step 30 的**精确组合词与专有名词**
  （annotation_finalization / finalize_annotated_evidence / AnnotationEvidenceIntegrityResult 等）；
* "annotator" / "domain_review" / "annotation_review" / "annotation_comparison"
  在 production 当前零命中（本审计对此做事实断言）。
```

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_annotation_finalization_architecture.py"
_FINALIZATION_TEST = "tests/test_conversation_context_annotation_finalization.py"
_FINALIZATION_FIXTURE = (
    "tests/fixtures/conversation_context/annotation_finalization_cases.yaml"
)
_FINALIZATION_DOC = (
    "docs/evaluation/Phase 4.1 Step 30 — Annotation Evidence Finalization.md"
)

_BACKEND_ROOT = "backend/app"

#: 禁止进入 production 的 Step 30 专有标识符（精确名词）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "AnnotationEvidenceIntegrityResult",
    "CaseFinalizationMetadata",
    "finalize_annotated_evidence",
    "FINALIZATION_SCENARIOS",
    "annotation_finalization",
    "finalize_annotation",
)

#: 禁止出现的模块命名（组合词）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "annotation_finalization",
    "annotation_review",
    "annotation_comparison",
    "domain_review",
    "annotator",
    "integrity_gate",
)

#: 生产模块禁止 import 的前缀（§二十一）。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "tests",
    "docs",
    "evaluation",
)

#: 关键 runtime 文件（不得出现 finalization / annotation 语义）。
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

    def test_1b_no_finalization_identifiers_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_finalization_modules_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            name = path.name.lower()
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1d_finalization_word_stem_is_preexisting_unrelated(self) -> None:
        """既有 finalize 语义（T2SQL 评估 / DB 会话）不属于本决策域。"""
        allowed_owners = {
            "backend/app/services/text_to_sql_semantic_result_evaluation_service.py",
            "backend/app/db/base.py",
        }
        for owner in allowed_owners:
            assert (_REPO_ROOT / owner).exists(), owner
        # 这些既有文件不得携带 annotation / finalization 域标识符
        for owner in allowed_owners:
            tokens = _ast_tokens(_REPO_ROOT / owner)
            for forbidden in (
                "AnnotationEvidenceIntegrityResult",
                "finalize_annotated_evidence",
                "annotation_finalization",
            ):
                assert forbidden not in tokens, f"{owner}:{forbidden}"

    def test_1e_no_tests_docs_evaluation_imports(self) -> None:
        """以真实 import 模块名判断（不把字符串常量中的路径当 import）。"""
        offenders: list[str] = []
        for path in _backend_files():
            modules = _module_imports(_relative(path))
            for module in modules:
                for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                    if module == prefix or module.startswith(f"{prefix}."):
                        offenders.append(f"{_relative(path)}:{module}")
        assert offenders == [], offenders

    def test_1f_no_finalization_directories_created(self) -> None:
        for relative in (
            "backend/app/annotation",
            "backend/app/evaluation",
            "backend/app/finalization",
        ):
            assert not (_REPO_ROOT / relative).exists(), relative


# ============================================================
# 2. Runtime isolation
# ============================================================


class TestRuntimeIsolation:
    def test_2a_runtime_files_exist(self) -> None:
        for relative in _RUNTIME_FILES:
            assert (_REPO_ROOT / relative).exists(), relative

    def test_2b_runtime_has_no_annotation_or_finalization_tokens(self) -> None:
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for token in tokens:
                lowered = str(token).lower()
                if lowered in (
                    "annotation",
                    "annotation_version",
                    "annotated_evidence",
                    "annotator",
                    "domain_review",
                    "annotator_id",
                ):
                    raise AssertionError(f"{relative}:{token}")

    def test_2c_runtime_imports_no_evaluation_modules(self) -> None:
        for relative in _RUNTIME_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for forbidden in (
                    "annotation",
                    "domain_review",
                    "evaluation",
                    "tests",
                ):
                    assert forbidden not in module, f"{relative}:{module}"


# ============================================================
# 3. Tests 侧契约（允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_3a_finalization_test_defines_contract(self) -> None:
        finalization = _REPO_ROOT / _FINALIZATION_TEST
        assert finalization.exists()
        tokens = _ast_tokens(finalization)
        for name in (
            "AnnotationEvidenceIntegrityResult",
            "CaseFinalizationMetadata",
            "finalize_annotated_evidence",
            "FAILURE_CODES",
        ):
            assert name in tokens, name

    def test_3b_finalization_test_is_offline(self) -> None:
        modules = _module_imports(_FINALIZATION_TEST)
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

    def test_3c_finalization_fixture_is_synthetic_only(self) -> None:
        fixture = _source(_FINALIZATION_FIXTURE)
        assert "synthetic" in fixture
        assert "非生产数据" in fixture
        for forbidden in (
            "conversation_id",
            "turn_id",
            "assistant_request_id",
            "provider_request_id",
        ):
            assert forbidden not in fixture, forbidden

    def test_3d_finalization_doc_exists_with_statements(self) -> None:
        doc = _source(_FINALIZATION_DOC)
        for statement in (
            "Finalization PASS",
            "G3 Evidence READY",
            "Synthetic fixtures only",
            "No real WMS annotation",
            "Production Code = 0",
            "DUPLICATE_REVIEWED_VERSION",
        ):
            assert statement in doc, statement

    def test_3e_fixture_set_is_exactly_three_files(self) -> None:
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
            "wms_multiturn_conversations.yaml",
        ], files

    def test_3f_step21_dataset_unchanged(self) -> None:
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
