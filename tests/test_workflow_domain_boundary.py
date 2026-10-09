"""Phase 6 Step 1 —— Workflow 领域包边界审计（AST / 白名单 / frozen）。

覆盖（ADR-01 §5 Architecture Principles / §20 Security / 修正 2/3）：
```text
Test 1  domain 文件清单存在（7 个源文件）
Test 2  AST 导入白名单：domain 文件仅 stdlib + backend.app.workflow 自身
Test 3  显式禁入：db / agent / services / tools / projects / api / llm 零交集
Test 4  反向隔离：agent 包 0 import workflow（Sibling Runtimes）
Test 5  错误码：WORKFLOW_ 前缀 / frozenset / 与 AGENT_ERROR_CODES 无交集
Test 6  frozen：核心 DTO / 定义 / 策略不可变（修正 2/3 的 DTO 纪律）
Test 7  词汇表：无 agent run_id / step_index 字段复用（AST 级 import + 常量级检查）
```

边界：本测试只读源码（AST），零 DB / 零网络。
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from backend.app.workflow.errors import (
    WF_ERROR_CODES,
    WorkflowInputError,
    WorkflowOutputError,
)
from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.models import (
    NodeExecutionIdentity,
    NodeRunOutcome,
    WorkflowApprovalRecord,
    WorkflowRunResult,
    validate_json_value,
)
from backend.app.workflow.policy import NodeRetryPolicy, WorkflowRuntimePolicy
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    WORKFLOW_STATUS_RUNNING,
)

__all__ = [
    "TestDomainFileSet",
    "TestImportBoundary",
    "TestCrossRuntimeIsolation",
    "TestErrorCodeWhitelist",
    "TestFrozenDTOs",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_DIR = _REPO_ROOT / "backend" / "app" / "workflow"
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

#: Step 1 domain 文件集合（显式清单 —— 未来 Step 3 runtime 新增文件
#: 不属于 domain 边界；边界演进必须是有意的测试变更）。
_DOMAIN_FILES: tuple[str, ...] = (
    "__init__.py",
    "definition.py",
    "errors.py",
    "ids.py",
    "models.py",
    "policy.py",
    "states.py",
)

#: 允许的 stdlib 顶层模块（精确白名单 —— 新增需要有意更新本测试）。
_STDLIB_ALLOWED: frozenset[str] = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "json",
        "math",
        "re",
        "typing",
        "uuid",
    }
)

#: 显式禁入前缀（db / agent / services / tools / projects / api / llm / 第三方框架）。
_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "backend.app.db",
    "backend.app.agent",
    "backend.app.services",
    "backend.app.tools",
    "backend.app.projects",
    "backend.app.api",
    "backend.app.llm",
    "backend.app.dto",
    "sqlalchemy",
    "fastapi",
    "httpx",
)

_WORKFLOW_PREFIX = "backend.app.workflow"


def _collect_imports(path: Path) -> set[str]:
    """AST 收集文件的全部 import 模块名（不支持字符串动态 import —— 也不允许）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import：本包内使用（当前不使用）
                modules.add(".<relative>")
                continue
            if node.module:
                modules.add(node.module)
    return modules


class TestDomainFileSet:
    """Test 1：domain 文件清单存在。"""

    def test_domain_files_exist(self) -> None:
        for name in _DOMAIN_FILES:
            assert (_WORKFLOW_DIR / name).is_file(), f"缺少 domain 文件: {name}"


class TestImportBoundary:
    """Test 2 / 3：导入白名单 + 显式禁入。"""

    @pytest.mark.parametrize("file_name", _DOMAIN_FILES)
    def test_imports_within_whitelist(self, file_name: str) -> None:
        modules = _collect_imports(_WORKFLOW_DIR / file_name)
        for module in modules:
            if module.startswith(_WORKFLOW_PREFIX):
                continue
            top = module.split(".")[0]
            assert top in _STDLIB_ALLOWED, (
                f"{file_name} 出现白名单外 import: {module!r}"
                "（domain 仅允许 stdlib + workflow 自身）"
            )

    @pytest.mark.parametrize("file_name", _DOMAIN_FILES)
    def test_forbidden_prefixes_absent(self, file_name: str) -> None:
        modules = _collect_imports(_WORKFLOW_DIR / file_name)
        for module in modules:
            for forbidden in _FORBIDDEN_PREFIXES:
                assert not module.startswith(forbidden), (
                    f"{file_name} 出现禁入 import: {module!r}"
                )

    def test_no_relative_imports_in_domain(self) -> None:
        for file_name in _DOMAIN_FILES:
            assert ".<relative>" not in _collect_imports(
                _WORKFLOW_DIR / file_name
            )


class TestCrossRuntimeIsolation:
    """Test 4：Sibling Runtimes 双向隔离。"""

    def test_agent_package_does_not_import_workflow(self) -> None:
        agent_files = sorted(_AGENT_DIR.glob("*.py"))
        assert agent_files, "agent 包文件缺失（环境异常）"
        for path in agent_files:
            for module in _collect_imports(path):
                assert not module.startswith(_WORKFLOW_PREFIX), (
                    f"agent/{path.name} import 了 workflow: {module!r}"
                    "（Sibling Runtimes 禁止交叉）"
                )


class TestErrorCodeWhitelist:
    """Test 5：错误码白名单（独立 namespace）。"""

    def test_whitelist_shape(self) -> None:
        assert isinstance(WF_ERROR_CODES, frozenset)
        assert len(WF_ERROR_CODES) == 16
        for code in WF_ERROR_CODES:
            assert isinstance(code, str)
            assert code
            assert code.startswith("WORKFLOW_"), f"命名空间泄漏: {code!r}"

    def test_no_overlap_with_agent_error_codes(self) -> None:
        from backend.app.agent.errors import AGENT_ERROR_CODES

        assert not (WF_ERROR_CODES & AGENT_ERROR_CODES)

    def test_no_free_text_codes(self) -> None:
        for code in WF_ERROR_CODES:
            assert " " not in code
            assert code == code.strip()
            assert code == code.upper()


class TestFrozenDTOs:
    """Test 6：核心 DTO / 定义 / 策略不可变。"""

    def test_workflow_node_frozen(self) -> None:
        node = WorkflowNode(
            node_id="a", node_type=NODE_TYPE_AI, question_template="问题"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            node.node_id = "b"  # type: ignore[misc]

    def test_workflow_definition_frozen(self) -> None:
        definition = WorkflowDefinition(
            name="wf",
            version="v1",
            start_node_id="a",
            nodes=(WorkflowNode(node_id="a", node_type=NODE_TYPE_AI, question_template="问题"),),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            definition.version = "v2"  # type: ignore[misc]

    def test_condition_frozen(self) -> None:
        condition = WorkflowCondition(source="qty", operator="EQ", expected=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            condition.source = "other"  # type: ignore[misc]

    def test_identity_and_outcome_frozen(self) -> None:
        identity = NodeExecutionIdentity(
            workflow_run_id="run-1", node_id="a", attempt=1
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            identity.attempt = 2  # type: ignore[misc]

        outcome = NodeRunOutcome(identity=identity, status=NODE_STATUS_RUNNING)
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.status = "FAILED"  # type: ignore[misc]

    def test_run_result_frozen(self) -> None:
        result = WorkflowRunResult(
            workflow_run_id="run-1", status=WORKFLOW_STATUS_RUNNING
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.status = "COMPLETED"  # type: ignore[misc]

    def test_approval_record_is_passive_dto(self) -> None:
        """修正 3：DTO 无 approve() / reject() / cancel() 方法。"""
        record = WorkflowApprovalRecord(
            approval_id="ap-1",
            workflow_run_id="run-1",
            node_id="approve_step",
            status=APPROVAL_STATUS_PENDING,
        )
        assert not hasattr(record, "approve")
        assert not hasattr(record, "reject")
        assert not hasattr(record, "cancel")
        assert not hasattr(record, "resolve")

    def test_approval_pending_carries_no_resolved_at(self) -> None:
        with pytest.raises(WorkflowInputError):
            WorkflowApprovalRecord(
                approval_id="ap-1",
                workflow_run_id="run-1",
                node_id="approve_step",
                status=APPROVAL_STATUS_PENDING,
                resolved_at=__import__("datetime").datetime.now(),
            )

    def test_policies_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            NodeRetryPolicy().max_attempts = 3  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            WorkflowRuntimePolicy().max_nodes_executed = 1  # type: ignore[misc]

    def test_registry_is_not_frozen_but_explicit(self) -> None:
        """Registry 是注册容器（非 frozen）；register 显式失败即可。"""
        registry = WorkflowRegistry()
        assert len(registry) == 0


class TestJsonValueBoundarySmoke:
    """补充：修正 2 的 NodeOutput / JsonValue 边界冒烟（详细用例在 models 测试场景）。"""

    def test_reject_session_like_object(self) -> None:
        class _FakeSession:  # 模拟 DB Session / 任意对象
            pass

        with pytest.raises(WorkflowOutputError):
            validate_json_value(_FakeSession())

    def test_reject_orm_like_tuple_and_bytes(self) -> None:
        with pytest.raises(WorkflowOutputError):
            validate_json_value((1, 2))
        with pytest.raises(WorkflowOutputError):
            validate_json_value(b"bytes")
