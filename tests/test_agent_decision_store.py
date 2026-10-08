"""Phase 5 P-6 Agent Decision Store（**离线**；Fake Repository，零 DB / 零真实 LLM）。

覆盖（任务书 §九–§十四 / §四十四 / §四十五 / §六十七）：

```text
DTO / identity   decision_ref = "{run_id}:{step_index}"（唯一 + 身份双约束）·
                 FINAL / TOOL_CALL 白名单 · tool_call_id 只作 protocol correlation
写               create → get / get_by_ref / get_latest · duplicate（first-write-wins）
安全             JSON-safe（拒 bytes / Session / 任意对象）· 32 KiB 上限 ⇒
                 decision_unavailable（不截断 / 不部分保存）· 敏感标记 ⇒ 不可用
fail-closed      Store 写失败 ⇒ AgentDecisionPersistenceError（不吞异常）
边界             不保存 prompt / messages / context / SQL / Tool Result / usage；
                 Runtime 仍 0 DB（端口注入）
```
"""
from __future__ import annotations

import ast
import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent.decision import (
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
)
from backend.app.agent.decision_store import (
    DECISION_TYPES,
    MAX_DECISION_ARGUMENTS_SIZE,
    AgentDecisionPersistenceError,
    AgentDecisionRecord,
    AgentDecisionStore,
    assert_decision_ref_matches,
    decision_record_of,
    decision_ref,
    parse_decision_ref,
)
from backend.app.db.agent_decision_repository import (
    AgentDecisionRepository,
    validate_arguments,
    validate_decision_type,
    validate_step_index,
)

__all__ = [
    "TestDecisionContract",
    "TestDecisionRecordMapping",
    "TestAgentDecisionStore",
    "TestDecisionRepositoryValidation",
    "TestDecisionBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_RUN_ID = "run-p6-decision"
_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name="get_inventory",
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p6_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


class FakeAgentDecisionRepository:
    """内存仓储：模拟 UNIQUE(run_id, step_index) + UNIQUE(decision_ref)。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.rows: dict[str, AgentDecisionRecord] = {}
        self._error = error

    def create(self, **kwargs: Any) -> AgentDecisionRecord | None:
        if self._error is not None:
            raise self._error
        key = f"{kwargs['run_id']}:{kwargs['step_index']}"
        if key in self.rows or any(
            row.decision_ref == kwargs["decision_ref"] for row in self.rows.values()
        ):
            return None  # first-write-wins
        now = datetime.now(timezone.utc)
        record = AgentDecisionRecord(
            run_id=kwargs["run_id"],
            step_index=kwargs["step_index"],
            decision_ref=kwargs["decision_ref"],
            decision_type=kwargs["decision_type"],
            tool_call_id=kwargs["tool_call_id"],
            tool_name=kwargs["tool_name"],
            arguments=kwargs["arguments"],
            content=kwargs["content"],
            finish_reason=kwargs["finish_reason"],
            decision_unavailable=kwargs["decision_unavailable"],
            created_at=now,
        )
        self.rows[key] = record
        return record

    def get(self, run_id: str, step_index: int) -> AgentDecisionRecord | None:
        if self._error is not None:
            raise self._error
        return self.rows.get(f"{run_id}:{step_index}")

    def get_by_ref(self, decision_ref: str) -> AgentDecisionRecord | None:
        if self._error is not None:
            raise self._error
        for row in self.rows.values():
            if row.decision_ref == decision_ref:
                return row
        return None

    def get_latest(self, run_id: str) -> AgentDecisionRecord | None:
        if self._error is not None:
            raise self._error
        rows = sorted(
            (row for row in self.rows.values() if row.run_id == run_id),
            key=lambda row: row.step_index,
        )
        return rows[-1] if rows else None


class _ExplodingSessionFactory:
    def __call__(self) -> Any:
        raise AssertionError("校验必须在触达 DB 之前失败")


def _store(repository: Any = None) -> AgentDecisionStore:
    return AgentDecisionStore(
        repository if repository is not None else FakeAgentDecisionRepository()
    )


class TestDecisionContract:
    def test_decision_ref_round_trip(self) -> None:
        ref = decision_ref(_RUN_ID, 2)

        assert ref == f"{_RUN_ID}:2"
        assert parse_decision_ref(ref) == (_RUN_ID, 2)
        assert_decision_ref_matches(ref, run_id=_RUN_ID, step_index=2)  # 不抛

    @pytest.mark.parametrize("bad", ["", "no-colon", ":2", "run:x", "run:-1"])
    def test_invalid_refs_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_decision_ref(bad)

    def test_cross_identity_ref_rejected(self) -> None:
        with pytest.raises(ValueError, match="其它 run"):
            assert_decision_ref_matches("other:2", run_id=_RUN_ID, step_index=2)
        with pytest.raises(ValueError, match="其它 step"):
            assert_decision_ref_matches(
                decision_ref(_RUN_ID, 3), run_id=_RUN_ID, step_index=2
            )

    def test_decision_types_whitelist(self) -> None:
        assert DECISION_TYPES == {DECISION_FINAL, DECISION_TOOL_CALL}

    def test_record_dto_fields_are_minimal(self) -> None:
        names = {field.name for field in fields(AgentDecisionRecord)}

        assert names == {
            "run_id",
            "step_index",
            "decision_ref",
            "decision_type",
            "tool_call_id",
            "tool_name",
            "arguments",
            "content",
            "finish_reason",
            "decision_unavailable",
            "created_at",
        }
        # 禁止字段（§十）：prompt / messages / context / SQL / Tool Result / usage
        for forbidden in (
            "prompt",
            "messages",
            "context",
            "sql",
            "tool_result",
            "result_payload",
            "conversation_id",
            "turn_id",
            "idempotency_key",
            "evidence_id",
            "prompt_tokens",
            "completion_tokens",
            "model",
            "provider",
        ):
            assert forbidden not in names, forbidden


class TestDecisionRecordMapping:
    def test_tool_call_mapping(self) -> None:
        ref, dtype, tool_call_id, tool_name, arguments, content, unavailable = (
            decision_record_of(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)
        )

        assert ref == f"{_RUN_ID}:0"
        assert dtype == DECISION_TOOL_CALL
        assert tool_call_id == "call_p6_1"  # Provider 原值（不生成）
        assert tool_name == "get_inventory"
        assert arguments == {"material_code": "MAT001"}
        assert content is None
        assert unavailable is False

    def test_final_mapping(self) -> None:
        ref, dtype, tool_call_id, tool_name, arguments, content, unavailable = (
            decision_record_of(run_id=_RUN_ID, step_index=1, decision=_FINAL)
        )

        assert (ref, dtype) == (f"{_RUN_ID}:1", DECISION_FINAL)
        assert (tool_call_id, tool_name, arguments) == (None, None, None)
        assert content == "库存 120 件。"
        assert unavailable is False

    def test_oversized_arguments_make_decision_unavailable(self) -> None:
        """§十一：超过 32 KiB ⇒ decision_unavailable（**不**截断 / 不部分保存）。"""
        huge = AgentDecision(
            type=DECISION_TOOL_CALL,
            tool_name="t",
            arguments={"blob": "x" * (MAX_DECISION_ARGUMENTS_SIZE + 16)},
            tool_call_id="call-big",
        )

        _, _, _, _, arguments, _, unavailable = decision_record_of(
            run_id=_RUN_ID, step_index=0, decision=huge
        )

        assert unavailable is True
        assert arguments is None

    def test_sensitive_arguments_make_decision_unavailable(self) -> None:
        secret = AgentDecision(
            type=DECISION_TOOL_CALL,
            tool_name="t",
            arguments={"password": "p@ssw0rd", "api_key": "sk-live-x"},
            tool_call_id="call-secret",
        )

        _, _, _, _, arguments, _, unavailable = decision_record_of(
            run_id=_RUN_ID, step_index=0, decision=secret
        )

        assert unavailable is True
        assert arguments is None

    def test_non_json_arguments_make_decision_unavailable(self) -> None:
        bad = AgentDecision(
            type=DECISION_TOOL_CALL,
            tool_name="t",
            arguments={"session": object()},
            tool_call_id="call-obj",
        )

        _, _, _, _, arguments, _, unavailable = decision_record_of(
            run_id=_RUN_ID, step_index=0, decision=bad
        )

        assert unavailable is True
        assert arguments is None

    def test_sensitive_or_oversized_content_makes_decision_unavailable(self) -> None:
        leaked = AgentDecision(
            type=DECISION_FINAL, content="Bearer abc.def: 结果如下"
        )
        enormous = AgentDecision(type=DECISION_FINAL, content="x" * 20000)

        for decision in (leaked, enormous):
            _, _, _, _, _, content, unavailable = decision_record_of(
                run_id=_RUN_ID, step_index=0, decision=decision
            )
            assert unavailable is True
            assert content is None


class TestAgentDecisionStore:
    def test_create_and_read_back(self) -> None:
        store = _store()

        record = store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)

        assert record.decision_ref == f"{_RUN_ID}:0"
        assert store.get(_RUN_ID, 0) == record
        assert store.get_by_ref(record.decision_ref) == record
        assert store.get_latest(_RUN_ID) == record
        assert store.get(_RUN_ID, 9) is None

    def test_duplicate_is_first_write_wins(self) -> None:
        """同一 (run_id, step_index) 第二次写入 ⇒ 不覆盖（Replay 确定性）。"""
        store = _store()
        first = store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)

        second = store.save(
            run_id=_RUN_ID,
            step_index=0,
            decision=AgentDecision(
                type=DECISION_TOOL_CALL,
                tool_name="other_tool",
                arguments={"x": 1},
                tool_call_id="call-2",
            ),
        )

        assert second == first  # 返回既有记录
        assert store.get(_RUN_ID, 0).tool_name == "get_inventory"

    def test_wrong_run_or_step_returns_none(self) -> None:
        store = _store()
        store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)

        assert store.get("other-run", 0) is None
        assert store.get(_RUN_ID, 1) is None
        assert store.get_by_ref("other-run:0") is None

    def test_get_latest_returns_highest_step(self) -> None:
        store = _store()
        store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)
        latest = store.save(run_id=_RUN_ID, step_index=1, decision=_FINAL)

        assert store.get_latest(_RUN_ID) == latest

    def test_write_failure_is_fail_closed(self) -> None:
        """§十四：写失败 ⇒ AgentDecisionPersistenceError（**不**吞异常）。"""
        store = _store(FakeAgentDecisionRepository(error=RuntimeError("db down")))

        with pytest.raises(AgentDecisionPersistenceError) as excinfo:
            store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)

        assert excinfo.value.code == "AGENT_DECISION_PERSISTENCE_FAILURE"

    def test_read_failure_propagates(self) -> None:
        repository = FakeAgentDecisionRepository()
        store = _store(repository)
        store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)
        repository._error = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            store.get(_RUN_ID, 0)
        with pytest.raises(RuntimeError):
            store.get_by_ref(f"{_RUN_ID}:0")

    def test_invalid_decision_type_rejected(self) -> None:
        class _Weird:
            type = "PLAN"
            tool_name = None
            tool_call_id = None
            arguments = None
            content = None

        with pytest.raises(ValueError):
            decision_record_of(run_id=_RUN_ID, step_index=0, decision=_Weird())

    def test_store_requires_repository(self) -> None:
        with pytest.raises(ValueError, match="create\\(\\)"):
            AgentDecisionStore(object())

    async def test_asave(self) -> None:
        store = _store()

        record = await store.asave(run_id=_RUN_ID, step_index=0, decision=_FINAL)

        assert record.decision_type == DECISION_FINAL


class TestDecisionRepositoryValidation:
    def _repository(self) -> AgentDecisionRepository:
        return AgentDecisionRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

    def _create(self, **overrides: Any) -> Any:
        values: dict[str, Any] = {
            "run_id": _RUN_ID,
            "step_index": 0,
            "decision_ref": f"{_RUN_ID}:0",
            "decision_type": DECISION_TOOL_CALL,
            "tool_name": "get_inventory",
            "arguments": {"material_code": "MAT001"},
            "tool_call_id": "call_1",
        }
        values.update(overrides)
        return self._repository().create(**values)

    def test_valid_payload_reaches_db_layer(self) -> None:
        with pytest.raises(AssertionError):  # 通过校验 ⇒ 触达 DB
            self._create()

    def test_valid_final_payload_reaches_db_layer(self) -> None:
        with pytest.raises(AssertionError):
            self._create(
                decision_type=DECISION_FINAL,
                tool_name=None,
                tool_call_id=None,
                arguments=None,
                content="库存 120 件。",
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"run_id": ""},
            {"run_id": "r" * 65},
            {"step_index": -1},
            {"step_index": True},
            {"decision_ref": "other-run:0"},  # 身份不一致
            {"decision_ref": f"{_RUN_ID}:1"},  # step 不一致
            {"decision_ref": ""},
            {"decision_type": "PLAN"},
            {"decision_type": ""},
            {"tool_name": "t" * 129},
            {"tool_call_id": "c" * 129},
            {"finish_reason": "f" * 65},
            {"arguments": {"x": object()}},  # 禁止任意对象
            {"arguments": {"x": b"bytes"}},  # 禁止 bytes
            {"arguments": {1: "int-key"}},  # key 必须 str
            {"arguments": {"x": float("nan")}},
            {"decision_unavailable": 1},
            # 不变式：FINAL 不得带 tool_* / arguments
            {
                "decision_type": DECISION_FINAL,
                "content": "答案",
            },
            # 不变式：TOOL_CALL 不得带 content
            {"content": "答案"},
            # 不变式：unavailable ⇒ arguments / content 必须为 None
            {"decision_unavailable": True},
            # 不变式：可 Replay 的 FINAL 必须有 content
            {
                "decision_type": DECISION_FINAL,
                "tool_name": None,
                "arguments": None,
                "tool_call_id": None,
            },
        ],
    )
    def test_invalid_values_fail_before_db(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            self._create(**overrides)

    def test_public_validators(self) -> None:
        assert validate_step_index(0) == 0
        assert validate_decision_type(DECISION_FINAL) == DECISION_FINAL
        assert validate_arguments(None) is None
        assert validate_arguments({"a": [1, 2.5, True, None]}) == {
            "a": [1, 2.5, True, None]
        }
        with pytest.raises(ValueError):
            validate_step_index(-1)
        with pytest.raises(ValueError):
            validate_decision_type("PLAN")

    def test_repository_api_surface(self) -> None:
        public = {
            name for name in dir(AgentDecisionRepository) if not name.startswith("_")
        }

        assert public == {
            "create",
            "get",
            "get_by_ref",
            "get_latest",
            "delete_for_test",
        }
        for forbidden in ("delete", "resume", "replay", "list", "timeline"):
            assert not hasattr(AgentDecisionRepository, forbidden)


class TestDecisionBoundary:
    def test_decision_store_module_has_no_db_import(self) -> None:
        source = (_AGENT_DIR / "decision_store.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        for module in modules:
            assert not module.startswith("backend.app.db"), module
            assert not module.startswith("sqlalchemy"), module
        # 禁止把完整 messages / prompt / result 落盘的字段名出现在 DTO 里
        names = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in ("prompt", "messages", "tool_result", "raw_response"):
            assert forbidden not in names, forbidden

    def test_runtime_has_no_db_dependency(self) -> None:
        """§六十一 / §六十二：Runtime 仍 0 DB（只依赖注入端口 / 抽象）。"""
        source = (_AGENT_DIR / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        for module in modules:
            assert not module.startswith("backend.app.db"), module
            assert not module.startswith("sqlalchemy"), module
        for forbidden in (
            "AgentDecisionRepository",
            "agent_decision_repository",
            "AgentCheckpointRepository",
            "agent_checkpoint_repository",
        ):
            assert forbidden not in source, forbidden
        # DB 标识符（AST 层面；docstring 中的说明文字不算依赖）
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in ("sessionmaker", "Session", "Engine", "create_engine"):
            assert forbidden not in identifiers, forbidden
        # 端口存在（DecisionStore / CheckpointSink / ToolResultSink）
        assert "AgentDecisionStorePort" in source
        assert "decision_store" in source

    def test_no_new_resume_lifecycle_status(self) -> None:
        """§六十八：不新增 RESUMING / REPLAYING / RESUMED 生命周期状态。"""
        from backend.app.agent.checkpoint import CHECKPOINT_STATUSES

        assert CHECKPOINT_STATUSES == {"ACTIVE", "PAUSED", "TERMINAL"}
        source = (_AGENT_DIR / "resume.py").read_text(encoding="utf-8")
        for forbidden in ("RESUMING", "REPLAYING", "RESUMED"):
            assert forbidden not in source, forbidden

    def test_decision_store_json_is_deterministic(self) -> None:
        first = decision_record_of(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)[4]
        second = decision_record_of(
            run_id=_RUN_ID,
            step_index=0,
            decision=AgentDecision(
                type=DECISION_TOOL_CALL,
                tool_name="get_inventory",
                arguments={"material_code": "MAT001"},
                tool_call_id="call_p6_1",
            ),
        )[4]

        assert json.loads(json.dumps(first, sort_keys=True)) == second
