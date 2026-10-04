"""Evidence Collection Procedure 架构审计（Phase 4.1 Step 25）—— AST 精确边界。

审计目标：

    1. Step 25 的 4 个 test-local 组件
       （EvidenceDatasetValidator / ContextDependencyAnnotation /
        ContextImpactAnnotation / EvidenceCollectionReport）
       **不得进入 production**（backend/app/{dto,services,db}）；
    2. Context 域选择 / 预算 / Tokenizer / Truncation / Summarizer / Memory
       **仍未进入 production**；
    3. 本审计自身纯静态、离线。

精确性说明（避免误报既有模块）：

```text
* "tokenizer" 泛词在 production 中**已存在**（reranker/client.py 的
  AutoTokenizer / self._tokenizer，属 Reranker 域，Phase 3.x 既有）；
  本审计因此只检查 Context/Conversation 域的**组合词**
  （context_tokenizer / conversation_tokenizer / history_tokenizer 等）；
* "truncate" 泛词在 production 中以注释 / SQL 关键字形态存在
  （"不 truncate"说明、TRUNCATE TABLE 拒词表）；
  本审计只检查 truncate_context / truncate_history 组合词；
* "memory" 泛词对应既有 in_memory_*_collector（观测 collector）；
  本审计只检查 conversation_memory / context_memory。
```

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evidence_collection_procedure_architecture.py"
_PROCEDURE_TEST = (
    "tests/test_conversation_context_evidence_collection_procedure.py"
)
_PROCEDURE_DOC = "docs/evaluation/Phase 4.1 Step 25 — Evidence Collection Procedure.md"

_AUDITED_DIRS: tuple[str, ...] = (
    "backend/app/dto",
    "backend/app/services",
    "backend/app/db",
)

#: Step 25 test-local 组件（不得进入 production）。
_FORBIDDEN_STEP25_NAMES: tuple[str, ...] = (
    "EvidenceDatasetValidator",
    "EvidenceCollectionReport",
    "ContextDependencyAnnotation",
    "ContextImpactAnnotation",
    "CandidateHistory",
    "G4ComparisonUnit",
)

#: Context 域禁词（精确专有名词 / 组合词；已确认 production 零命中）。
_FORBIDDEN_CONTEXT_IDENTIFIERS: tuple[str, ...] = (
    "ContextSelector",
    "context_selector",
    "SelectionPolicy",
    "selection_policy",
    "context_budget",
    "history_budget",
    "ContextTokenizer",
    "context_tokenizer",
    "conversation_tokenizer",
    "history_tokenizer",
    "truncate_context",
    "truncate_history",
    "ContextTruncation",
    "context_truncation",
    "summarizer",
    "Summarizer",
    "conversation_memory",
    "context_memory",
    "conversation_summary",
    "ContextSummarizer",
)

#: 不得在 production 出现的 Step 25 模块命名（组合词）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "evidence_collection",
    "evidence_dataset",
    "evidence_report",
    "context_selector",
    "selection_policy",
    "conversation_memory",
    "context_budget",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _production_files() -> list[Path]:
    files: list[Path] = []
    for directory in _AUDITED_DIRS:
        files.extend(sorted((_REPO_ROOT / directory).rglob("*.py")))
    return files


def _ast_tokens(path: Path) -> set[str]:
    """AST 标识符（Name / Attribute / arg / keyword / def / 字符串常量）。"""
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
# 1. production 边界（Step 25 组件 + Context 域禁词）
# ============================================================


class TestProductionBoundary:
    def test_1a_production_files_exist(self) -> None:
        files = _production_files()
        assert files, "production 审计目录为空（审计失效）"
        assert len(files) > 10

    def test_1b_step25_components_not_in_production(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_STEP25_NAMES:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_context_domain_identifiers_not_in_production(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            code_tokens = _code_tokens(path)
            for forbidden in _FORBIDDEN_CONTEXT_IDENTIFIERS:
                if forbidden in code_tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1d_no_evidence_or_selector_modules_in_production(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            name = path.name
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders

    def test_1e_reranker_tokenizer_is_preexisting_and_unrelated(self) -> None:
        """既有 reranker tokenizer（Reranker 域）不属于 Conversation Context 域。"""
        reranker = _REPO_ROOT / "backend/app/reranker/client.py"
        assert reranker.exists()
        tokens = _code_tokens(reranker)
        assert "AutoTokenizer" in tokens  # 既有依赖（Phase 3.x）
        # 但不得出现 context / conversation 域的 tokenizer 组合词
        for forbidden in (
            "context_tokenizer",
            "conversation_tokenizer",
            "history_tokenizer",
        ):
            assert forbidden not in tokens, forbidden

    def test_1f_builder_still_has_no_selection_parameters(self) -> None:
        builder = _REPO_ROOT / "backend/app/services/conversation_context_builder.py"
        tokens = _code_tokens(builder)
        for forbidden in (
            "max_turns",
            "max_chars",
            "max_tokens",
            "policy",
            "budget",
            "selector",
        ):
            assert forbidden not in tokens, forbidden


# ============================================================
# 2. Procedure 契约位置（tests/ + docs/）
# ============================================================


class TestProcedureLocation:
    def test_2a_procedure_components_are_test_local(self) -> None:
        procedure = _REPO_ROOT / _PROCEDURE_TEST
        assert procedure.exists()
        tokens = _ast_tokens(procedure)
        for name in (
            "EvidenceDatasetValidator",
            "EvidenceCollectionReport",
            "run_g1_procedure",
            "run_g2_procedure",
            "run_g3_procedure",
            "run_g4_procedure",
            "build_evidence_report",
            "validate_report",
        ):
            assert name in tokens, name

    def test_2b_procedure_doc_exists_with_gate_state(self) -> None:
        doc = _source(_PROCEDURE_DOC)
        for statement in (
            "G1 = BLOCKED",
            "G2 = INSUFFICIENT",
            "G3 Contract = READY",
            "G3 Evidence = BLOCKED",
            "G4 = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_2c_step21_dataset_not_modified(self) -> None:
        dataset = _source(
            "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
        )
        for field in ("annotation:", "reference:", "impact:"):
            assert field not in dataset, field

    def test_2d_no_deidentification_scripts_created(self) -> None:
        for name in ("deidentify.py", "anonymize.py", "redact.py"):
            for directory in ("scripts", "backend/app/services", "tests"):
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )


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
                "tiktoken",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_3b_audit_is_static_only(self) -> None:
        tokens = _code_tokens(_REPO_ROOT / _SELF)
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
        assert "_code_tokens" in function_names


__all__ = [
    "TestProductionBoundary",
    "TestProcedureLocation",
    "TestSelfAudit",
]
