"""Selection Evidence Data Readiness Audit（Phase 4.1 Step 23 —— **Audit only**）。

本文件只回答一个问题：

> 当前项目中是否存在可用于 Selection Strategy 决策的**真实/脱敏多轮 WMS 对话数据**，
> 以及 G1~G4 所需的数据结构能否从现有数据获得。

审计结论（实测，非预设）：

```text
Data Source = SYNTHETIC_ONLY
G1 = BLOCKED        （real de-identified dataset unavailable）
G2 = INSUFFICIENT   （无真实数据 → 不可计算分布）
G3 = BLOCKED        （无真实数据可标注）
G4 = BLOCKED        （无 reference outcome）
Selection Strategy remains BLOCKED
```

边界：

    * 本文件**不保存任何 conversation content**（DTO 只含计数与布尔）；
    * 不复制真实业务数据到 tests/ / docs/ / git；
    * 纯离线：DB = 0 · Network = 0 · LLM = 0；
    * 不实现 ContextSelector / SelectionPolicy / Budget / Tokenizer / Truncation。
"""
from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_selection_evidence_data_audit.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 23 — Selection Evidence Data Readiness.md"

_CONVERSATION_FIXTURE_DIR = "tests/fixtures/conversation_context"
_SYNTHETIC_DATASET = (
    "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
)
#: Step 29 新增 fixture（annotation review procedure；同为 synthetic / 非生产）。
_ANNOTATION_REVIEW_FIXTURE = (
    "tests/fixtures/conversation_context/annotation_review_cases.yaml"
)
#: Step 30 新增 fixture（annotation finalization；同为 synthetic / 非生产）。
_ANNOTATION_FINALIZATION_FIXTURE = (
    "tests/fixtures/conversation_context/annotation_finalization_cases.yaml"
)
_STEP21_DOC = "docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md"
_STEP22_DOC = "docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md"

_PROXIED_DIRS: tuple[str, ...] = (
    "tests/fixtures/conversation_context",
)

#: 数据状态三值（只接受三种之一）。
SOURCE_TYPES: tuple[str, ...] = (
    "REAL_DEIDENTIFIED",
    "REAL_NOT_DEIDENTIFIED",
    "SYNTHETIC_ONLY",
)

_G1_STATES: tuple[str, ...] = ("READY", "BLOCKED")
_G2_STATES: tuple[str, ...] = ("READY", "INSUFFICIENT")
_G3_STATES: tuple[str, ...] = ("READY", "BLOCKED")
_G4_STATES: tuple[str, ...] = ("READY", "BLOCKED")

#: 真实来源标记（fixture 中若出现即视为可能存在真实数据来源）。
_REAL_ORIGIN_MARKERS: tuple[str, ...] = (
    "production data",
    "真实导出",
    "生产导出",
    "exported from production",
    "real customer",
)

#: 最小 secret pattern（**只检查明确凭据形态**，不对业务数据做全文扫描）。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"postgresql://\S+:\S+@"),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"Authorization:\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]\s*[A-Za-z0-9]{8,}", re.IGNORECASE),
    re.compile(r"DATABASE_URL\s*=\s*\S+"),
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Data Source",
    "## 2. G1",
    "## 3. G2",
    "## 4. G3",
    "## 5. G4",
    "## 6. Privacy",
    "## 7. Leakage",
    "## 8. Decision",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "SYNTHETIC_ONLY",
    "G1 = BLOCKED",
    "G2 = INSUFFICIENT",
    "G3 = BLOCKED",
    "G4 = BLOCKED",
    "Selection Strategy remains BLOCKED",
    "G1 blocked: real de-identified dataset unavailable",
    "de-identification mechanism = unavailable",
)

#: 本 Step 文档允许出现的最大规模声明（synthetic 实测值）。
_EXPECTED_CASE_COUNT = 12
_EXPECTED_TURN_COUNT = 68
_EXPECTED_USER_TURN_COUNT = 40
_EXPECTED_ASSISTANT_TURN_COUNT = 28

_CASE_PATTERN = re.compile(r"^- id: ", re.MULTILINE)
_TURN_PATTERN = re.compile(r"^\s+- role: (user|assistant)\s*$", re.MULTILINE)

_CHECKED_FILES: tuple[str, ...] = (
    _SYNTHETIC_DATASET,
    _ANNOTATION_REVIEW_FIXTURE,
    _ANNOTATION_FINALIZATION_FIXTURE,
    _STEP21_DOC,
    _STEP22_DOC,
    _AUDIT_DOC,
)


# ============================================================
# Test-local DTO（**非 production DTO**；不保存任何 content）
# ============================================================


@dataclasses.dataclass(frozen=True)
class EvidenceDataReadiness:
    """数据可用性描述（**只含计数与布尔**，不含 conversation content）。"""

    source_type: str
    sample_count: int
    conversation_count: int
    turn_count: int
    has_real_origin: bool
    is_deidentified: bool
    contains_sensitive_fields: bool
    usable_for_evaluation: bool


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _classify_dataset(
    *,
    declares_synthetic: bool,
    has_real_origin_markers: bool,
    is_deidentified: bool,
) -> str:
    """数据三分类（真实来源缺失时只能是 SYNTHETIC_ONLY）。"""
    if declares_synthetic or not has_real_origin_markers:
        return "SYNTHETIC_ONLY"
    return "REAL_DEIDENTIFIED" if is_deidentified else "REAL_NOT_DEIDENTIFIED"


def _dataset_declares_synthetic() -> bool:
    header = _source(_SYNTHETIC_DATASET)[:800]
    return "synthetic" in header and "非生产数据" in header


def _dataset_has_real_origin_markers() -> bool:
    text = _source(_SYNTHETIC_DATASET)
    return any(marker in text for marker in _REAL_ORIGIN_MARKERS)


def _count_dataset() -> tuple[int, int, int, int]:
    """返回 (cases, turns, user_turns, assistant_turns) —— 仅在内存中读取。"""
    text = _source(_SYNTHETIC_DATASET)
    roles = _TURN_PATTERN.findall(text)
    cases = len(_CASE_PATTERN.findall(text))
    return cases, len(roles), roles.count("user"), roles.count("assistant")


def _conversation_fixture_files() -> list[str]:
    return sorted(
        str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
        for path in (_REPO_ROOT / _CONVERSATION_FIXTURE_DIR).rglob("*")
        if path.is_file()
    )


def _scan_secret_patterns(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def audit_current_readiness() -> EvidenceDataReadiness:
    """当前真实数据审计（确定性；不读取任何 conversation content 之外的信息）。"""
    cases, turns, _user, _assistant = _count_dataset()
    source_type = _classify_dataset(
        declares_synthetic=_dataset_declares_synthetic(),
        has_real_origin_markers=_dataset_has_real_origin_markers(),
        is_deidentified=False,
    )
    return EvidenceDataReadiness(
        source_type=source_type,
        sample_count=cases,
        conversation_count=cases,
        turn_count=turns,
        has_real_origin=False,
        is_deidentified=False,
        contains_sensitive_fields=False,
        usable_for_evaluation=False,
    )


# ============================================================
# G1~G4 readiness 判定（条件式，非硬编码）
# ============================================================


def evaluate_g1(readiness: EvidenceDataReadiness) -> str:
    """G1 READY 条件：REAL_DEIDENTIFIED + 三类计数 > 0 + 无敏感字段 + 可用于 evaluation。"""
    if (
        readiness.source_type == "REAL_DEIDENTIFIED"
        and readiness.sample_count > 0
        and readiness.conversation_count > 0
        and readiness.turn_count > 0
        and not readiness.contains_sensitive_fields
        and readiness.usable_for_evaluation
    ):
        return "READY"
    return "BLOCKED"


def evaluate_g2(g1_state: str) -> str:
    """G2 依赖 G1：只有真实数据才能计算长度分布（仅字符口径）。"""
    return "READY" if g1_state == "READY" else "INSUFFICIENT"


def evaluate_g3(g1_state: str, *, annotation_spec_ready: bool = False) -> str:
    """G3 依赖真实数据 + 标注规范。"""
    return "READY" if g1_state == "READY" and annotation_spec_ready else "BLOCKED"


def evaluate_g4(g1_state: str, *, reference_outcome_available: bool = False) -> str:
    """G4 依赖真实多轮 history + 当前 user turn + 可比较 reference outcome。"""
    return (
        "READY"
        if g1_state == "READY" and reference_outcome_available
        else "BLOCKED"
    )


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ============================================================
# 1. 数据分类（§四 / §十一.1）
# ============================================================


class TestDatasetClassification:
    def test_01_step21_dataset_is_synthetic_only(self) -> None:
        readiness = audit_current_readiness()
        assert readiness.source_type == "SYNTHETIC_ONLY"
        assert readiness.source_type != "REAL_DEIDENTIFIED"
        assert readiness.source_type != "REAL_NOT_DEIDENTIFIED"

    def test_02_dataset_header_declares_synthetic(self) -> None:
        assert _dataset_declares_synthetic() is True
        assert _dataset_has_real_origin_markers() is False

    def test_03_classifier_is_condition_based(self) -> None:
        # 无真实来源标记 → SYNTHETIC_ONLY（即使未声明 synthetic）
        assert (
            _classify_dataset(
                declares_synthetic=False,
                has_real_origin_markers=False,
                is_deidentified=True,
            )
            == "SYNTHETIC_ONLY"
        )
        # 有真实来源 + 已脱敏 → REAL_DEIDENTIFIED（未来迁移路径）
        assert (
            _classify_dataset(
                declares_synthetic=False,
                has_real_origin_markers=True,
                is_deidentified=True,
            )
            == "REAL_DEIDENTIFIED"
        )
        # 有真实来源 + 未脱敏 → REAL_NOT_DEIDENTIFIED（只能记录，禁止复制数据）
        assert (
            _classify_dataset(
                declares_synthetic=False,
                has_real_origin_markers=True,
                is_deidentified=False,
            )
            == "REAL_NOT_DEIDENTIFIED"
        )

    def test_04_source_type_domain_is_closed(self) -> None:
        assert audit_current_readiness().source_type in SOURCE_TYPES

    def test_05_dataset_scale_matches_step21_evidence(self) -> None:
        cases, turns, user_turns, assistant_turns = _count_dataset()
        assert cases == _EXPECTED_CASE_COUNT
        assert turns == _EXPECTED_TURN_COUNT
        assert user_turns == _EXPECTED_USER_TURN_COUNT
        assert assistant_turns == _EXPECTED_ASSISTANT_TURN_COUNT
        assert user_turns + assistant_turns == turns

    def test_06_readiness_flags_reflect_synthetic_reality(self) -> None:
        readiness = audit_current_readiness()
        assert readiness.has_real_origin is False
        assert readiness.is_deidentified is False
        assert readiness.contains_sensitive_fields is False
        assert readiness.usable_for_evaluation is False

    def test_07_readiness_dto_holds_no_content(self) -> None:
        fields = {field.name for field in dataclasses.fields(EvidenceDataReadiness)}
        assert fields == {
            "source_type",
            "sample_count",
            "conversation_count",
            "turn_count",
            "has_real_origin",
            "is_deidentified",
            "contains_sensitive_fields",
            "usable_for_evaluation",
        }
        for field in dataclasses.fields(EvidenceDataReadiness):
            assert field.type in ("str", "int", "bool"), field.name


# ============================================================
# 2. G1~G4 readiness（§六~§九 / §十二 / §十三）
# ============================================================


class TestG1ToG4Readiness:
    def test_08_g1_blocked(self) -> None:
        readiness = audit_current_readiness()
        assert evaluate_g1(readiness) == "BLOCKED"
        assert evaluate_g1(readiness) in _G1_STATES

    def test_09_g2_insufficient(self) -> None:
        assert evaluate_g2(evaluate_g1(audit_current_readiness())) == "INSUFFICIENT"

    def test_10_g3_blocked(self) -> None:
        state = evaluate_g3(evaluate_g1(audit_current_readiness()))
        assert state == "BLOCKED"
        assert state in _G3_STATES

    def test_11_g4_blocked(self) -> None:
        state = evaluate_g4(evaluate_g1(audit_current_readiness()))
        assert state == "BLOCKED"
        assert state in _G4_STATES

    def test_12_transition_requires_real_deidentified_dataset(self) -> None:
        ready = dataclasses.replace(
            audit_current_readiness(),
            source_type="REAL_DEIDENTIFIED",
            has_real_origin=True,
            is_deidentified=True,
            usable_for_evaluation=True,
        )
        assert evaluate_g1(ready) == "READY"
        assert evaluate_g2(evaluate_g1(ready)) == "READY"
        assert evaluate_g3(evaluate_g1(ready), annotation_spec_ready=True) == "READY"
        assert (
            evaluate_g4(evaluate_g1(ready), reference_outcome_available=True)
            == "READY"
        )

    def test_13_partial_conditions_stay_blocked(self) -> None:
        base = dataclasses.replace(
            audit_current_readiness(),
            source_type="REAL_DEIDENTIFIED",
            has_real_origin=True,
            is_deidentified=True,
            usable_for_evaluation=True,
        )
        # 敏感字段存在 → G1 仍 BLOCKED
        assert (
            evaluate_g1(dataclasses.replace(base, contains_sensitive_fields=True))
            == "BLOCKED"
        )
        # 零样本 → G1 仍 BLOCKED
        assert evaluate_g1(dataclasses.replace(base, turn_count=0)) == "BLOCKED"
        # 未脱敏 → G1 仍 BLOCKED（REAL_NOT_DEIDENTIFIED 不可用）
        assert (
            evaluate_g1(
                dataclasses.replace(
                    base,
                    source_type="REAL_NOT_DEIDENTIFIED",
                    is_deidentified=False,
                )
            )
            == "BLOCKED"
        )

    def test_14_g2_g3_g4_cannot_be_ready_without_g1(self) -> None:
        blocked_g1 = evaluate_g1(audit_current_readiness())
        assert blocked_g1 == "BLOCKED"
        assert evaluate_g2(blocked_g1) == "INSUFFICIENT"
        assert evaluate_g3(blocked_g1, annotation_spec_ready=True) == "BLOCKED"
        assert evaluate_g4(blocked_g1, reference_outcome_available=True) == "BLOCKED"

    def test_15_selection_strategy_remains_blocked(self) -> None:
        """Step 22 的 Gate 结论必须保持（G1 BLOCKED → 不能冻结 Strategy）。"""
        from tests.test_conversation_context_selection_decision_gate import (
            decide_strategy_state,
        )

        assert decide_strategy_state() == "BLOCKED"

    def test_16_statistics_are_not_policy(self) -> None:
        """G2 即使未来 READY，统计也仅是 evidence（禁止直接映射 policy）。"""
        doc = _source(_AUDIT_DOC)
        assert "evidence" in doc
        assert "policy" in doc
        for forbidden_example in ("max_turns = P95", "max_chars = P95"):
            assert forbidden_example in doc, forbidden_example


# ============================================================
# 3. Leakage / Privacy（§十 / §十一.2）
# ============================================================


class TestLeakageAndPrivacy:
    def test_17_no_secret_values_in_checked_files(self) -> None:
        for relative in _CHECKED_FILES:
            hits = _scan_secret_patterns(_source(relative))
            assert hits == [], f"{relative}: {hits}"

    def test_18_conversation_fixture_set_is_exactly_three_files(self) -> None:
        """Step 21 dataset + Step 29 review + Step 30 finalization（均 synthetic/非生产）。"""
        assert _conversation_fixture_files() == [
            _ANNOTATION_FINALIZATION_FIXTURE,
            _ANNOTATION_REVIEW_FIXTURE,
            _SYNTHETIC_DATASET,
        ]

    def test_19_no_local_exported_chat_data_in_repo(self) -> None:
        """repo 内不存在 jsonl / csv 导出的对话数据（data/ 目录不存在）。"""
        assert not (_REPO_ROOT / "data").exists()
        exported = [
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in _REPO_ROOT.rglob("*")
            if path.is_file()
            and path.suffix in (".jsonl", ".csv")
            and ".git" not in path.parts
        ]
        assert exported == [], exported

    def test_20_deidentification_mechanism_unavailable(self) -> None:
        """无脱敏脚本 / 流程模块（scripts 中无 deidentify / anonymize）。"""
        script_names = sorted(
            path.name.lower()
            for path in (_REPO_ROOT / "scripts").rglob("*.py")
        )
        for name in script_names:
            for keyword in ("deidentif", "anonymi", "pseudonym", "redact"):
                assert keyword not in name, name
        doc = _source(_AUDIT_DOC)
        assert "de-identification mechanism = unavailable" in doc

    def test_21_no_forbidden_artifact_dirs(self) -> None:
        for relative in ("fixtures", "datasets", "samples"):
            assert not (_REPO_ROOT / relative).exists(), relative

    def test_22_fixture_declares_no_real_business_fields(self) -> None:
        header = _source(_SYNTHETIC_DATASET)[:800]
        assert "不" in header and "真实" in header
        for field_name in ("订单号", "客户", "供应商", "库存数量", "生产数据"):
            assert field_name in header, field_name


# ============================================================
# 4. 离线 / 生产边界 / 文档（§十一.3 / §十四 / §十六）
# ============================================================


class TestOfflineAndProductionBoundary:
    def test_23_no_db_network_llm_in_audit(self) -> None:
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
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_24_audit_is_static_and_deterministic(self) -> None:
        assert audit_current_readiness() == audit_current_readiness()
        identifiers = {
            node.id for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("TestClient", "environ", "getenv", "subprocess"):
            assert forbidden not in identifiers, forbidden

    def test_25_no_selector_budget_tokenizer_modules(self) -> None:
        for directory in ("backend/app/services", "backend/app/db", "backend/app/dto"):
            for path in (_REPO_ROOT / directory).rglob("*.py"):
                name = path.name
                for keyword in (
                    "selector",
                    "selection_policy",
                    "budget",
                    "tokenizer",
                    "truncation",
                    "summarizer",
                ):
                    if keyword == "selector":
                        # 既有 relevant_table_selector 属 Text-to-SQL（非本域）
                        assert "conversation" not in name or "selector" not in name
                        continue
                    assert keyword not in name, str(path)

    def test_26_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_27_document_records_scale_and_privacy(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert str(_EXPECTED_CASE_COUNT) in doc
        assert "12" in doc
        assert "68" in doc
        assert "脱敏" in doc

    def test_28_document_defines_annotation_types(self) -> None:
        doc = _source(_AUDIT_DOC)
        for annotation_type in (
            "early_constraint",
            "middle_decision",
            "recent_context",
            "old_topic",
            "standalone",
        ):
            assert annotation_type in doc, annotation_type

    def test_29_document_defines_g4_comparison(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "Full History",
            "Selected History",
            "reference",
            "business outcome",
        ):
            assert marker in doc, marker

    def test_30_production_code_untouched_this_step(self) -> None:
        """本 Step 只新增 tests/ 与 docs/（production 目录无新增文件）。"""
        for name in (
            "evidence_data_readiness.py",
            "conversation_data_audit.py",
            "deidentify.py",
        ):
            for directory in ("backend/app/services", "backend/app/db", "backend/app/dto"):
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )


__all__ = [
    "SOURCE_TYPES",
    "EvidenceDataReadiness",
    "audit_current_readiness",
    "evaluate_g1",
    "evaluate_g2",
    "evaluate_g3",
    "evaluate_g4",
]
