"""Real Evidence Import 架构审计（Phase 4.1 Step 31）—— AST 精确边界。

审计目标：

    1. ``backend/app/`` 中**不得新增**：real_evidence / evidence_import /
       evaluation_import 等生产模块与实现；
    2. Import Contract 只允许存在于 tests/；
    3. Conversation 运行时无 import 契约耦合；
    4. 本审计自身纯静态、离线。

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_real_evidence_import_architecture.py"
_IMPORT_TEST = "tests/test_conversation_context_real_evidence_import.py"
_IMPORT_FIXTURE = (
    "tests/fixtures/conversation_context/real_evidence_import_cases.yaml"
)
_IMPORT_DOC = "docs/evaluation/Phase 4.1 Step 31 — Real Evidence Import Contract.md"

_BACKEND_ROOT = "backend/app"

#: 禁止进入 production 的 Import 专有标识符。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "RealEvidenceImportResult",
    "RealEvidenceImportContract",
    "DeIdentificationAttestation",
    "import_real_evidence",
    "ImportContractError",
    "IMPORT_SCENARIOS",
    "FAILURE_CODES_REAL_IMPORT",
)

#: 禁止出现的模块命名（组合词）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "real_evidence",
    "evidence_import",
    "evaluation_import",
    "import_contract",
    "de_identification",
)

#: 关键 runtime 文件（不得 import tests / import 契约）。
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
# 1. Production 边界（§二十五）
# ============================================================


class TestProductionBoundary:
    def test_1a_backend_files_exist(self) -> None:
        files = _backend_files()
        assert files, "backend/app 为空（审计失效）"
        assert len(files) > 50

    def test_1b_no_import_contract_identifiers_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_import_modules_in_backend(self) -> None:
        offenders: list[str] = []
        for path in _backend_files():
            name = path.name.lower()
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1d_annotation_and_provenance_still_absent(self) -> None:
        """延续 Step 27~30：production 仍无 annotation / provenance 语义。"""
        offenders: list[str] = []
        for path in _backend_files():
            tokens = _ast_tokens(path)
            lowered = {str(token).lower() for token in tokens}
            for forbidden in ("annotation", "provenance", "annotator"):
                if forbidden in lowered:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1e_no_import_directories_created(self) -> None:
        for relative in (
            "backend/app/evidence",
            "backend/app/import",
            "backend/app/evaluation",
        ):
            assert not (_REPO_ROOT / relative).exists(), relative


# ============================================================
# 2. Runtime isolation
# ============================================================


class TestRuntimeIsolation:
    def test_2a_runtime_files_exist(self) -> None:
        for relative in _RUNTIME_FILES:
            assert (_REPO_ROOT / relative).exists(), relative

    def test_2b_runtime_does_not_import_tests_or_import_contract(self) -> None:
        for relative in _RUNTIME_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                assert not module.startswith("tests"), f"{relative}:{module}"
                for forbidden in (
                    "real_evidence",
                    "evidence_import",
                    "evaluation_import",
                    "import_contract",
                ):
                    assert forbidden not in module, f"{relative}:{module}"

    def test_2c_runtime_has_no_import_contract_tokens(self) -> None:
        for relative in _RUNTIME_FILES:
            tokens = _ast_tokens(_REPO_ROOT / relative)
            for forbidden in (
                "import_real_evidence",
                "de_identification_attestation",
                "RealEvidenceImportResult",
            ):
                assert forbidden not in tokens, f"{relative}:{forbidden}"


# ============================================================
# 3. Tests 侧契约（允许 test-local）
# ============================================================


class TestTestLocalContract:
    def test_3a_import_test_defines_contract(self) -> None:
        import_test = _REPO_ROOT / _IMPORT_TEST
        assert import_test.exists()
        tokens = _ast_tokens(import_test)
        for name in (
            "RealEvidenceImportResult",
            "DeIdentificationAttestation",
            "import_real_evidence",
            "ImportContractError",
        ):
            assert name in tokens, name

    def test_3b_import_test_is_offline(self) -> None:
        modules = _module_imports(_IMPORT_TEST)
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

    def test_3c_import_test_reuses_prior_contracts(self) -> None:
        modules = _module_imports(_IMPORT_TEST)
        for required in (
            "tests.test_conversation_context_real_evidence_boundary",
            "tests.test_conversation_context_evidence_provenance",
            "tests.test_conversation_context_annotation_workflow",
        ):
            assert required in modules, sorted(modules)

    def test_3d_import_fixture_is_synthetic_only(self) -> None:
        fixture = _source(_IMPORT_FIXTURE)
        assert "synthetic" in fixture
        assert "非生产数据" in fixture
        for forbidden in (
            "conversation_id:",
            "assistant_request_id:",
            "turn_id:",
            "provider_request_id:",
        ):
            assert forbidden not in fixture, forbidden

    def test_3e_import_doc_exists_with_statements(self) -> None:
        doc = _source(_IMPORT_DOC)
        for statement in (
            "Import PASS",
            "G1 READY",
            "Synthetic only",
            "No real WMS import",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_3f_fixture_set_is_exactly_five_files(self) -> None:
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

    def test_3g_step21_dataset_unchanged(self) -> None:
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
