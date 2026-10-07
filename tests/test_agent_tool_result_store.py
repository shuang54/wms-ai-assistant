"""Phase 5 P-4 Agent Tool Result Store（**离线**；Fake Repository，零 DB / 零真实 LLM）。

覆盖（任务书 §三十 / §三十一 / §三十二 / §三十三 / §三十四）：

```text
1  Small success      → payload + hash 落库（replayable）
2  Small failure      → success=False + 稳定码；**无** exception message / 自由文本
3  Medium result      → 32 KiB < size ≤ 1 MiB：payload=NULL + hash 有 + unavailable=True
4  Large result       → > 1 MiB：payload=NULL + unavailable=True
5  Duplicate          → same (run_id, step_index) 只有 1 行（first-write-wins）
6  Different runs     → 同一 step_index 各自 1 行
7  tool_call_id       → **不是**唯一键（模型 / 约束层面）
8  无需截断后 Replay  → 超限**不**截断（truncated 恒 False；payload 直接 NULL）
9  Security           → prompt / messages / RAG chunk / SELECT * FROM secret_users /
                        sk-live-… / password / Bearer … / DATABASE_URL 不落 payload
10 Hash integrity     → same result ⇒ same hash；different result ⇒ different hash
11 Replay availability→ 只断言**已保存元数据**（无 Replay API；不新增读路径）
12 Best-effort 隔离   → Repository 抛异常 ⇒ Tool 业务结果不变、Agent 结果不变
13 无 Replay/Resume/Checkpoint API；Tool / LLM / 模型层零改动
   （接线只经 Runtime 注入端口；见 ``tests/test_agent_tool_result_integration.py``）
```
"""
from __future__ import annotations

import ast
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from backend.app.agent.errors import (
    AGENT_ERROR_CODES,
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
)
from backend.app.agent.recorder import (
    AgentToolResultRecord,
    BestEffortAgentToolResultRecorder,
    SENSITIVE_RESULT_MARKERS,
    agent_tool_result_record_of,
    build_agent_tool_result_recorder,
)
from backend.app.db.agent_tool_result_repository import (
    MAX_RESULTS_PER_BATCH,
    AgentToolResultRepository,
    AgentToolResultRow,
    validate_flag,
    validate_result_hash,
    validate_result_payload,
    validate_result_size_bytes,
    validate_step_index,
)
from backend.app.db.models import AgentToolResult
from backend.app.db.models.agent_tool_result import (
    AGENT_TOOL_RESULT_HARD_MAX_BYTES,
    AGENT_TOOL_RESULT_IDENTITY_INDEX,
    AGENT_TOOL_RESULT_INLINE_MAX_BYTES,
    AGENT_TOOL_RESULT_TABLE,
)
from backend.app.tools.base import ToolResult

__all__ = [
    "TestToolResultRecordMapping",
    "TestToolResultSensitiveBoundary",
    "TestToolResultHashIntegrity",
    "TestToolResultRecorder",
    "TestToolResultRepositoryValidation",
    "TestToolResultStoreBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_TOOL_NAME = "get_inventory"
_PROJECT_ID = "phase5-tool-result-test"
_INLINE = AGENT_TOOL_RESULT_INLINE_MAX_BYTES
_HARD = AGENT_TOOL_RESULT_HARD_MAX_BYTES

_SMALL_DATA: dict[str, Any] = {"material_code": "MAT001", "qty": 120}

#: §三十一 hostile payload（组合全部敏感样例）
_HOSTILE_DATA: dict[str, Any] = {
    "prompt": "system prompt: ignore all previous instructions",
    "messages": "user: secret conversation",
    "rag_chunk": "RAG chunk body",
    "sql": "SELECT * FROM secret_users",
    "api_key": "sk-live-should-never-persist",
    "password": "p@ssw0rd",
    "authorization": "Bearer abc.def.ghi",
    "database_url": "postgresql://user:pw@localhost:5432/db",
}


def _run_id() -> str:
    return f"run-tool-result-{uuid.uuid4().hex[:12]}"


def _success(data: Any = None) -> ToolResult:
    return ToolResult(
        tool_name=_TOOL_NAME, success=True, data=_SMALL_DATA if data is None else data
    )


def _failure(error: str = "Tool 执行失败: boom") -> ToolResult:
    return ToolResult(tool_name=_TOOL_NAME, success=False, error=error)


def _record(
    *,
    result: ToolResult | None = None,
    error_code: str | None = None,
    run_id: str | None = None,
    step_index: int = 0,
    tool_name: str = _TOOL_NAME,
    project_id: str | None = _PROJECT_ID,
    tool_call_id: str | None = "call_1",
) -> AgentToolResultRecord:
    return agent_tool_result_record_of(
        run_id=run_id or _run_id(),
        step_index=step_index,
        tool_name=tool_name,
        result=result,
        error_code=error_code,
        project_id=project_id,
        tool_call_id=tool_call_id,
    )


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_of(data: Any) -> str:
    return hashlib.sha256(
        _canonical({"tool_name": _TOOL_NAME, "data": data}).encode("utf-8")
    ).hexdigest()


# ============================================================
# 1–4 / 8 / 11：映射与大小策略
# ============================================================


class TestToolResultRecordMapping:
    def test_small_success_persists_payload_and_hash(self) -> None:
        record = _record(result=_success())

        assert record.success is True
        assert record.error_code is None
        assert record.result_payload == {"tool_name": _TOOL_NAME, "data": _SMALL_DATA}
        assert record.result_hash == _hash_of(_SMALL_DATA)
        assert record.result_size_bytes == len(
            _canonical({"tool_name": _TOOL_NAME, "data": _SMALL_DATA}).encode("utf-8")
        )
        assert record.result_truncated is False
        assert record.result_unavailable is False  # ⇒ replayable
        assert record.result_ref is None
        assert record.tool_call_id == "call_1"
        assert record.project_id == _PROJECT_ID

    def test_small_failure_keeps_stable_code_only(self) -> None:
        secret_text = "RuntimeError: boom with secret sk-live-xyz"
        record = _record(result=_failure(secret_text))

        assert record.success is False
        assert record.error_code == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert record.error_code in AGENT_ERROR_CODES
        assert record.result_payload is None
        assert record.result_hash is None
        assert record.result_size_bytes == 0
        assert record.result_truncated is False
        assert record.result_unavailable is False  # failure 事实本身可 Replay

        # 自由文本（含 exception message / 密钥样例）**不**进入任何字段
        dumped = repr(record)
        for token in ("RuntimeError", "boom", "sk-live", "secret"):
            assert token not in dumped, token

    def test_beyond_inline_limit_keeps_hash_but_not_payload(self) -> None:
        data = {"material_code": "MAT-MEDIUM", "blob": "x" * (_INLINE + 512)}
        record = _record(result=_success(data))

        assert record.result_size_bytes > _INLINE
        assert record.result_size_bytes <= _HARD
        assert record.result_payload is None
        assert record.result_hash == _hash_of(data)
        assert record.result_truncated is False
        assert record.result_unavailable is True  # ⇒ not replayable

    def test_beyond_hard_limit_keeps_hash_but_not_payload(self) -> None:
        data = {"material_code": "MAT-LARGE", "blob": "y" * (_HARD + 16)}
        record = _record(result=_success(data))

        assert record.result_size_bytes > _HARD
        assert record.result_payload is None
        assert record.result_hash == _hash_of(data)
        assert record.result_unavailable is True

    def test_oversize_result_is_never_truncated(self) -> None:
        """§十六：不做 payload truncation —— 不留下 `result[:32768]` 这种残片。"""
        data = {"material_code": "MAT-MEDIUM", "blob": "z" * (_INLINE + 64)}
        record = _record(result=_success(data))

        assert record.result_truncated is False
        assert record.result_payload is None  # 不是前 32 KiB 的截断结果

    def test_unserializable_result_is_unavailable(self) -> None:
        record = _record(result=_success(data=object()))

        assert record.success is True
        assert record.result_payload is None
        assert record.result_hash is None
        assert record.result_size_bytes == 0
        assert record.result_unavailable is True

    def test_non_finite_float_is_unavailable(self) -> None:
        record = _record(result=_success(data={"qty": float("nan")}))

        assert record.result_unavailable is True
        assert record.result_payload is None

    def test_unknown_outcome_is_fail_closed(self) -> None:
        """§二十三：结果不可判定 ⇒ success=False + unavailable=True + 稳定码。"""
        record = _record(error_code="TOOL_NOT_ALLOWED")

        assert record.success is False
        assert record.error_code == "TOOL_NOT_ALLOWED"
        assert record.result_unavailable is True
        assert record.result_payload is None
        assert record.result_hash is None

    def test_unknown_free_text_error_code_is_normalized(self) -> None:
        record = _record(error_code="ToolError: something leaked sk-live-xyz")

        assert record.error_code == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert "sk-live" not in repr(record)

    def test_replay_availability_metadata_only(self) -> None:
        """§三十三：只断言**已保存元数据**（本阶段无 Replay API）。"""
        replayable = _record(result=_success())
        not_replayable = [
            _record(result=_success(data={"blob": "x" * (_INLINE + 1)})),
            _record(result=_success(data={"blob": "y" * (_HARD + 1)})),
            _record(result=_success(data={"password": "p@ss"})),
            _record(result=_success(data=object())),
        ]

        assert replayable.result_unavailable is False
        assert replayable.result_payload is not None
        assert all(item.result_unavailable is True for item in not_replayable)
        assert all(item.result_payload is None for item in not_replayable)


# ============================================================
# 9：敏感边界（§十九 / §二十 / §三十一）
# ============================================================


class TestToolResultSensitiveBoundary:
    def test_hostile_payload_never_reaches_payload(self) -> None:
        record = _record(result=_success(data=_HOSTILE_DATA))

        assert record.result_payload is None
        assert record.result_hash is None  # 敏感内容连 hash 都不写
        assert record.result_unavailable is True

        dumped = repr(record)
        for token in (
            "prompt",
            "messages",
            "RAG",
            "SELECT",
            "sk-live",
            "password",
            "p@ssw0rd",
            "Bearer",
            "DATABASE_URL",
            "postgresql://",
        ):
            assert token not in dumped, token

    @pytest.mark.parametrize(
        "marker",
        [
            "api_key",
            "authorization",
            "password",
            "database_url",
            "bearer ",
            "sk-",
        ],
    )
    def test_each_sensitive_marker_blocks_payload(self, marker: str) -> None:
        record = _record(result=_success(data={"field": f"value {marker} tail"}))

        assert marker in SENSITIVE_RESULT_MARKERS
        assert record.result_payload is None
        assert record.result_hash is None
        assert record.result_unavailable is True

    def test_marker_detection_is_case_insensitive(self) -> None:
        record = _record(result=_success(data={"note": "PASSWORD=abc"}))

        assert record.result_unavailable is True
        assert record.result_payload is None

    def test_legitimate_result_is_not_flagged(self) -> None:
        record = _record(
            result=_success(data={"material_code": "MAT001", "warehouse_code": "WH1"})
        )

        assert record.result_unavailable is False
        assert record.result_payload is not None


# ============================================================
# 10 / 12：Hash 完整性 + Recorder（幂等 / best-effort 隔离）
# ============================================================


class TestToolResultHashIntegrity:
    def test_same_result_same_hash_regardless_of_key_order(self) -> None:
        first = _record(result=_success(data={"a": 1, "b": 2}))
        second = _record(result=_success(data={"b": 2, "a": 1}))

        assert first.result_hash == second.result_hash
        assert first.result_size_bytes == second.result_size_bytes

    def test_different_result_different_hash(self) -> None:
        first = _record(result=_success(data={"qty": 120}))
        second = _record(result=_success(data={"qty": 121}))

        assert first.result_hash != second.result_hash

    def test_hash_is_sha256_of_canonical_text_not_encryption(self) -> None:
        """§三十二：hash = 完整性校验（SHA-256 hex），**不是**加密 / 存储替代。"""
        record = _record(result=_success())

        assert record.result_hash is not None
        assert len(record.result_hash) == 64
        assert record.result_hash == _hash_of(_SMALL_DATA)
        # hash ≠ payload（hash 不是 payload 的替代载体）
        assert record.result_hash not in _canonical(record.result_payload)


@dataclass
class _FakeRowCapture:
    run_id: str
    step_index: int
    tool_name: str
    success: bool
    result_size_bytes: int
    result_truncated: bool
    result_unavailable: bool
    project_id: str | None
    error_code: str | None
    result_payload: Any | None
    result_hash: str | None
    result_ref: str | None
    tool_call_id: str | None


class FakeAgentToolResultRepository:
    """内存仓储：**模拟** UNIQUE(run_id, step_index) + DO NOTHING 语义。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.rows: list[_FakeRowCapture] = []
        self.batches: list[list[_FakeRowCapture]] = []
        self._error = error

    def create_many(self, records: Any) -> list[int]:
        batch = [_FakeRowCapture(**vars(record)) for record in records]
        self.batches.append(batch)
        if self._error is not None:
            raise self._error
        created: list[int] = []
        for row in batch:
            if any(
                existing.run_id == row.run_id
                and existing.step_index == row.step_index
                for existing in self.rows
            ):
                continue  # 幂等：first-write-wins
            self.rows.append(row)
            created.append(len(self.rows))
        return created

    def create(self, record: Any) -> int | None:
        created = self.create_many([record])
        return created[0] if created else None


class TestToolResultRecorder:
    def test_duplicate_identity_writes_single_row(self) -> None:
        repository = FakeAgentToolResultRepository()
        recorder = BestEffortAgentToolResultRecorder(repository=repository)
        run_id = _run_id()

        first = recorder.record(_record(result=_success(), run_id=run_id))
        second = recorder.record(
            _record(result=_success(data={"qty": 999}), run_id=run_id)
        )

        assert first is not None
        assert second is None  # ON CONFLICT (run_id, step_index) DO NOTHING
        assert len(repository.rows) == 1
        assert repository.rows[0].result_payload == {
            "tool_name": _TOOL_NAME,
            "data": _SMALL_DATA,  # first-write-wins（不 UPDATE）
        }

    def test_different_runs_may_share_step_index(self) -> None:
        repository = FakeAgentToolResultRepository()
        recorder = BestEffortAgentToolResultRecorder(repository=repository)

        assert recorder.record(_record(result=_success())) is not None
        assert recorder.record(_record(result=_success())) is not None

        assert len(repository.rows) == 2
        assert [row.step_index for row in repository.rows] == [0, 0]

    def test_batch_write_is_single_call(self) -> None:
        repository = FakeAgentToolResultRepository()
        recorder = BestEffortAgentToolResultRecorder(repository=repository)
        run_id = _run_id()

        created = recorder.record_many(
            (
                _record(result=_success(), run_id=run_id, step_index=0),
                _record(result=_success(), run_id=run_id, step_index=1),
                _record(result=_failure(), run_id=run_id, step_index=2),
            )
        )

        assert len(created) == 3
        assert len(repository.batches) == 1
        assert [row.success for row in repository.rows] == [True, True, False]

    def test_repository_failure_is_isolated(self) -> None:
        """§三十四：DB 不可用 ⇒ 业务结果不变；不 retry / 不 fallback / 不重跑 Tool。"""
        repository = FakeAgentToolResultRepository(error=RuntimeError("db down"))
        recorder = BestEffortAgentToolResultRecorder(repository=repository)
        tool_result = _success()

        created = recorder.record(_record(result=tool_result))

        assert created is None  # 永不抛出
        assert tool_result.success is True
        assert tool_result.data == _SMALL_DATA
        assert tool_result.error is None

    async def test_arecord_repository_failure_is_isolated(self) -> None:
        repository = FakeAgentToolResultRepository(error=RuntimeError("db down"))
        recorder = BestEffortAgentToolResultRecorder(repository=repository)

        assert await recorder.arecord(_record(result=_success())) is None
        assert await recorder.arecord_many((_record(result=_success()),)) == []

    def test_empty_records_no_write(self) -> None:
        repository = FakeAgentToolResultRepository()
        recorder = BestEffortAgentToolResultRecorder(repository=repository)

        assert recorder.record_many(()) == []
        assert repository.batches == []

    def test_build_recorder_respects_engine_availability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.app.db.session as session_module

        monkeypatch.setattr(session_module, "get_engine", lambda: None)
        assert build_agent_tool_result_recorder() is None

        monkeypatch.setattr(session_module, "get_engine", lambda: object())
        recorder = build_agent_tool_result_recorder()
        assert isinstance(recorder, BestEffortAgentToolResultRecorder)
        assert recorder.repository is None  # 延迟解析（不在装配期创建仓储）


# ============================================================
# 11 / 13：Repository 校验（零 DB）+ 边界审计
# ============================================================


class _ExplodingSessionFactory:
    """任何 DB 访问都会失败的 Session 工厂（证明校验发生在触达 DB 之前）。"""

    def __call__(self) -> Any:
        raise AssertionError("校验必须在触达 DB 之前失败")


def _row(**overrides: Any) -> AgentToolResultRow:
    values: dict[str, Any] = {
        "run_id": "run-1",
        "step_index": 0,
        "tool_name": _TOOL_NAME,
        "success": True,
        "result_size_bytes": 10,
        "result_truncated": False,
        "result_unavailable": False,
        "project_id": _PROJECT_ID,
        "error_code": None,
        "result_payload": {"tool_name": _TOOL_NAME, "data": _SMALL_DATA},
        "result_hash": "a" * 64,
        "result_ref": None,
        "tool_call_id": "call_1",
    }
    values.update(overrides)
    return AgentToolResultRow(**values)


class TestToolResultRepositoryValidation:
    def test_valid_row_is_accepted_and_statement_is_idempotent(self) -> None:
        repository = AgentToolResultRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

        statement = repository.build_idempotent_insert([_row()])
        compiled = str(statement.compile(dialect=postgresql.dialect()))

        assert "INSERT INTO ai_ops.agent_tool_result" in compiled
        assert "ON CONFLICT (run_id, step_index) DO NOTHING" in compiled
        assert "tool_call_id" in compiled  # 列存在，但**不**参与冲突目标

    @pytest.mark.parametrize(
        "overrides",
        [
            {"run_id": ""},
            {"run_id": "r" * 65},
            {"step_index": -1},
            {"step_index": True},
            {"step_index": 1.5},
            {"tool_name": ""},
            {"tool_name": "t" * 129},
            {"project_id": "p" * 129},
            {"tool_call_id": "c" * 129},
            {"error_code": "e" * 65},
            {"result_size_bytes": -1},
            {"result_size_bytes": True},
            {"result_truncated": "false"},
            {"result_unavailable": 1},
            {"result_ref": "s3://bucket/key"},
        ],
    )
    def test_invalid_values_fail_before_db(self, overrides: dict[str, Any]) -> None:
        repository = AgentToolResultRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

        with pytest.raises(ValueError):
            repository.create(_row(**overrides))

    @pytest.mark.parametrize(
        "overrides",
        [
            # 状态不变式
            {"success": True, "error_code": "TOOL_EXECUTION_FAILURE"},
            {"success": False, "error_code": None, "result_payload": None},
            {"success": False, "error_code": "TOOL_EXECUTION_FAILURE"},
            {"result_unavailable": True},  # payload 非空 + unavailable
            {"result_truncated": True},
            {"result_payload": {"a": 1}, "result_size_bytes": 0},
            {
                "result_payload": {"a": 1},
                "result_truncated": True,
                "result_unavailable": True,
            },
        ],
    )
    def test_invariant_violations_fail_before_db(
        self, overrides: dict[str, Any]
    ) -> None:
        repository = AgentToolResultRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

        with pytest.raises(ValueError):
            repository.create(_row(**overrides))

    def test_payload_must_be_json_compatible(self) -> None:
        class Session:  # 伪 Session / 任意对象
            pass

        for payload in (Session(), object(), b"bytes", {"a": {1, 2}}, (1, 2)):
            with pytest.raises(ValueError):
                validate_result_payload(payload)

    def test_payload_rejects_nested_objects_and_non_string_keys(self) -> None:
        class Row:
            pass

        with pytest.raises(ValueError):
            validate_result_payload({"ok": [1, 2, {"bad": Row()}]})
        with pytest.raises(ValueError):
            validate_result_payload({1: "int key"})

    def test_payload_accepts_json_scalars_and_structures(self) -> None:
        assert validate_result_payload(None) is None
        value = {"a": [1, 2.5, True, None, "s"], "b": {"c": "d"}}
        assert validate_result_payload(value) == value

    def test_hash_format_is_enforced(self) -> None:
        assert validate_result_hash(None) is None
        assert validate_result_hash("b" * 64) == "b" * 64
        for value in ("B" * 64, "b" * 63, "b" * 65, "z" * 64, 123):
            with pytest.raises(ValueError):
                validate_result_hash(value)

    def test_scalar_validators(self) -> None:
        assert validate_step_index(0) == 0
        assert validate_result_size_bytes(0) == 0
        assert validate_flag(True, field="success") is True
        for value in (-1, True, 1.0):
            with pytest.raises(ValueError):
                validate_step_index(value)
        for value in (-1, True, 1.0):
            with pytest.raises(ValueError):
                validate_result_size_bytes(value)

    def test_batch_limit_is_enforced(self) -> None:
        repository = AgentToolResultRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )
        rows = [_row(step_index=index) for index in range(MAX_RESULTS_PER_BATCH + 1)]

        with pytest.raises(ValueError):
            repository.create_many(rows)

    def test_empty_batch_no_write(self) -> None:
        repository = AgentToolResultRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

        assert repository.create_many(()) == []

    def test_repository_has_no_read_or_replay_api(self) -> None:
        """P-4 只建立**写入 Store**（无 find / get / list / timeline / replay / resume）。"""
        public = {
            name
            for name in dir(AgentToolResultRepository)
            if not name.startswith("_")
        }

        assert public == {"create", "create_many", "build_idempotent_insert"}


class TestToolResultStoreBoundary:
    def test_model_columns_match_frozen_field_set(self) -> None:
        assert [column.name for column in AgentToolResult.__table__.columns] == [
            "id",
            "run_id",
            "step_index",
            "tool_name",
            "project_id",
            "success",
            "error_code",
            "result_payload",
            "result_hash",
            "result_size_bytes",
            "result_truncated",
            "result_unavailable",
            "result_ref",
            "tool_call_id",
            "created_at",
        ]

    def test_identity_is_run_id_step_index_and_not_tool_call_id(self) -> None:
        table = AgentToolResult.__table__
        unique_indexes = {index.name: index for index in table.indexes if index.unique}

        assert set(unique_indexes) == {AGENT_TOOL_RESULT_IDENTITY_INDEX}
        identity = unique_indexes[AGENT_TOOL_RESULT_IDENTITY_INDEX]
        assert [column.name for column in identity.columns] == [
            "run_id",
            "step_index",
        ]
        assert [c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"] == []
        # tool_call_id **不是**唯一键（Provider 每次响应生成新 id）
        assert not table.columns["tool_call_id"].unique

    def test_model_has_no_foreign_key(self) -> None:
        assert len(AgentToolResult.__table__.foreign_keys) == 0
        assert AGENT_TOOL_RESULT_TABLE == "agent_tool_result"

    def test_tool_boundary_has_no_store_coupling(self) -> None:
        """P-4 接线只经 Runtime 与注入端口：Tool / LLM / 模型层**零改动**。"""
        for relative in (
            ("agent", "llm.py"),
            ("agent", "models.py"),
            ("agent", "capability.py"),
            ("services", "tool_execution_service.py"),
            ("tools", "registry.py"),
            ("tools", "get_inventory.py"),
            ("tools", "get_work_order.py"),
        ):
            source = (_REPO_ROOT / "backend" / "app" / relative[0] / relative[1]).read_text(
                encoding="utf-8"
            )
            assert "agent_tool_result" not in source, relative
            assert "AgentToolResult" not in source, relative
            assert "tool_result_sink" not in source, relative

    def test_runtime_only_carries_injected_port(self) -> None:
        """Runtime 持有的只是**注入端口**：不认识 Store / repository / DB。"""
        source = (_AGENT_DIR / "runtime.py").read_text(encoding="utf-8")

        for forbidden in (
            "agent_tool_result",
            "AgentToolResult",
            "AgentToolResultRepository",
            "sqlalchemy",
            "sessionmaker",
        ):
            assert forbidden not in source, forbidden
        assert "tool_result_sink" in source  # 端口存在（P-4 Integration）

    def test_runtime_and_tool_boundary_have_no_db_dependency(self) -> None:
        for name in ("runtime.py", "llm.py", "models.py", "capability.py"):
            tree = ast.parse((_AGENT_DIR / name).read_text(encoding="utf-8"))
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
                assert not module.startswith("backend.app.db"), (name, module)
                assert not module.startswith("sqlalchemy"), (name, module)

    def test_recorder_is_the_only_agent_module_touching_the_store(self) -> None:
        for path in sorted(_AGENT_DIR.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if path.name == "recorder.py":
                continue
            assert "agent_tool_result_repository" not in source, path.name
