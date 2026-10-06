"""Evaluation Rubric 架构审计（Phase 4.1 Step 24）—— AST 精确边界。

审计目标：

    1. ``ContextDependencyAnnotation`` / ``ContextImpactAnnotation``
       （以及 9 个 annotation / impact 标识符）**不得进入 production**；
    2. backend/app/{dto,services,db} 中不得出现 rubric / annotation contract 模块；
    3. contract 只存在于 tests/（本 Step 的 rubric 测试模块）；
    4. 本审计自身纯静态、离线。

方法（§二十一）：使用 AST 精确识别标识符
（Name / Attribute / arg / keyword / ClassDef / FunctionDef / 字符串常量），
**禁止全文字符串扫描**。

纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evaluation_rubric_architecture.py"
_RUBRIC_TEST = "tests/test_conversation_context_evaluation_rubric.py"
_RUBRIC_DOC = (
    "docs/evaluation/Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric.md"
)

_AUDITED_DIRS: tuple[str, ...] = (
    "backend/app/dto",
    "backend/app/services",
    "backend/app/db",
)

#: 不得进入 production 的 annotation / impact 标识符（§二十一）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "early_constraint",
    "middle_decision",
    "recent_context",
    "old_topic",
    "standalone",
    "critical_constraint_lost",
    "business_outcome_changed",
    "entity_changed",
    "intent_changed",
)

#: 不得进入 production 的 contract 类型名（§二十一）。
_FORBIDDEN_TYPE_NAMES: tuple[str, ...] = (
    "ContextDependencyAnnotation",
    "ContextImpactAnnotation",
    "EvaluationSchemaError",
    "ReferenceRequirement",
    "AnnotationQuality",
    "EvaluationRubric",
)

#: 不得在 production 出现的 rubric 命名模块（精确组合词；
#: 既有 backend/app/services/rag_evaluation_service.py 属 RAG 检索评估，非本决策域）。
_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "rubric",
    "annotation",
    "context_evaluation",
    "evaluation_dataset",
    "evaluation_case_schema",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _production_files() -> list[Path]:
    files: list[Path] = []
    for directory in _AUDITED_DIRS:
        files.extend(sorted((_REPO_ROOT / directory).rglob("*.py")))
    return files


def _ast_tokens(path: Path) -> set[str]:
    """AST 精确标识符集合（Name / Attribute / arg / keyword / def / 字符串常量）。"""
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
# 1. production 边界（AST）
# ============================================================


class TestProductionBoundary:
    def test_1a_production_files_exist(self) -> None:
        files = _production_files()
        assert files, "production 审计目录为空（审计失效）"
        assert len(files) > 10

    def test_1b_no_annotation_impact_identifiers_in_production(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1c_no_contract_type_names_in_production(self) -> None:
        offenders: list[str] = []
        for path in _production_files():
            tokens = _ast_tokens(path)
            for forbidden in _FORBIDDEN_TYPE_NAMES:
                if forbidden in tokens:
                    offenders.append(f"{_relative(path)}:{forbidden}")
        assert offenders == [], offenders

    def test_1d_no_rubric_modules_in_production(self) -> None:
        """Rubric 边界保持不变；仅排除 Step 37 冻结的持久化模块。"""
        frozen = (
            "backend/app/db/models/evidence_record.py",
            "backend/app/db/models/evidence_annotation_record.py",
            "backend/app/db/evidence_repository.py",
        )
        offenders: list[str] = []
        for path in _production_files():
            if _relative(path) in frozen:
                continue
            name = path.name
            for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                if keyword in name:
                    offenders.append(_relative(path))
        assert offenders == [], offenders


# ============================================================
# 2. contract 只存在于 tests/ / docs/
# ============================================================


class TestContractLocation:
    def test_2a_rubric_contract_is_test_local(self) -> None:
        assert (_REPO_ROOT / _RUBRIC_TEST).exists()
        # production 三目录中无同名 / 相似契约模块（精确组合词；允许既有 rag_evaluation_service）
        frozen = (
            "backend/app/db/models/evidence_record.py",
            "backend/app/db/models/evidence_annotation_record.py",
            "backend/app/db/evidence_repository.py",
        )
        for directory in _AUDITED_DIRS:
            paths = sorted((_REPO_ROOT / directory).rglob("*.py"))
            assert paths, directory
            for path in paths:
                if _relative(path) in frozen:
                    continue
                name = path.name
                for keyword in _FORBIDDEN_MODULE_KEYWORDS:
                    assert keyword not in name, f"{directory}/{name}"

    def test_2b_rubric_test_defines_contracts(self) -> None:
        tokens = _ast_tokens(_REPO_ROOT / _RUBRIC_TEST)
        assert "ContextDependencyAnnotation" in tokens
        assert "ContextImpactAnnotation" in tokens
        assert "validate_dataset_case" in tokens
        assert "resolve_annotation_status" in tokens

    def test_2c_rubric_doc_exists(self) -> None:
        doc = _source(_RUBRIC_DOC)
        assert "G3 Contract = READY" in doc
        assert "G3 Evidence = BLOCKED" in doc
        assert "Selection Strategy = BLOCKED" in doc

    def test_2d_synthetic_dataset_not_modified_by_step24(self) -> None:
        dataset = _source(
            "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
        )
        for field in ("annotation:", "reference:", "impact:"):
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
                "tiktoken",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_3b_audit_is_static_only(self) -> None:
        """代码标识符（不含字符串常量）中无运行时 / 环境访问入口。"""
        tokens = _code_tokens(_REPO_ROOT / _SELF)
        assert tokens, "AST 未解析到标识符（审计失效）"
        for forbidden in ("TestClient", "environ", "getenv", "subprocess", "system"):
            assert forbidden not in tokens, forbidden

    def test_3c_audit_uses_ast_not_full_text_scan(self) -> None:
        """§二十一：必须 AST 精确识别（不使用正则/全文扫描 production 文件）。"""
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
        assert "_scan" not in function_names


__all__ = [
    "TestProductionBoundary",
    "TestContractLocation",
    "TestSelfAudit",
]
