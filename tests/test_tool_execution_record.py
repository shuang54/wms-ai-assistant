"""Tool Execution Record Contract（Phase 3.11 Step 14）。

锁定「一次 Tool 执行的不可变元数据事件」：

```text
ToolExecutionContext（Step 13）
        +
ToolResult（Step 12）
        +
Timing（started_at / finished_at / duration_ms）
        ↓
ToolExecutionRecord            （frozen DTO；只描述执行事实）
```

覆盖（§十四）：

```text
DTO            创建 / frozen / 字段类型 / 4 个 context 字段 / tool_name
Timing         started_at / finished_at / duration_ms / >= 0 / tz-aware / 顺序
Result Mapping success=True → True；success=False → False（不推断）
Error Mapping  error_code（不发明：None）/ error_type（既有 ToolError 类名 allow-list）
Context Mapping 4 字段原值映射（不重新生成）
Isolation      Record 不含 arguments / result data / SQL / secrets
Immutability   修改字段必须失败
Determinism    同样输入 → 等价 Record
Purity         模块无 DB / IO / LLM / logging（AST 静态）
Real Tool      1 个 DB-gated（真实 get_inventory + Context + ToolResult 组装）
```

0 DB / 0 Network / 0 Real LLM（除 1 个 DB-gated 只读回归）。
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_record import (
    ToolExecutionRecord,
    elapsed_ms,
    now_utc,
)
from backend.app.tools.base import ToolResult
from backend.app.tools.errors import ToolValidationError
from backend.app.tools.get_inventory import (
    register_get_inventory_tool,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
)
from backend.app.tools.registry import ToolRegistry

from tests.test_get_inventory_tool import requires_db  # noqa: E402
from tests.test_tool_chat_real_get_inventory import (  # noqa: E402
    _PROJECT_ID,
    _real_registry,
    real_engine,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RECORD_MODULE = "backend/app/services/tool_execution_record.py"

_SECRETS = (
    "postgresql://", "wms_user", "s3cret-pw", "db.internal",
    "DATABASE_URL", "Authorization", "Bearer ", "sk-", "password",
    "Traceback",
)

#: 记录中不得出现的字段名（业务数据 / SQL / 凭据）
_FORBIDDEN_FIELDS = frozenset({
    "arguments", "args", "data", "result", "sql", "query", "statement",
    "prompt", "messages", "error", "error_message", "traceback",
    "connection", "connection_string", "dsn", "database_url",
    "api_key", "password", "token", "authorization",
})


# ============================================================
# 测试辅助
# ============================================================

def _context(**overrides: Any) -> ToolExecutionContext:
    kwargs: dict[str, Any] = {
        "request_id": "req-0001",
        "round": 1,
        "project_id": "project-a",
        "tool_call_id": "call_001",
    }
    kwargs.update(overrides)
    return ToolExecutionContext(**kwargs)


def _ok_result(tool_name: str = "get_inventory", **data: Any) -> ToolResult:
    payload = {"material_code": "MAT-001", "qty": 250.0}
    payload.update(data)
    return ToolResult(tool_name=tool_name, success=True, data=payload)


def _fail_result(
    tool_name: str = "get_inventory", error: str = "参数校验失败: missing required field(s): ['material_code']"
) -> ToolResult:
    return ToolResult(tool_name=tool_name, success=False, error=error)


def _record(
    *,
    context: ToolExecutionContext | None = None,
    tool_name: str = "get_inventory",
    result: ToolResult | None = None,
    **kwargs: Any,
) -> ToolExecutionRecord:
    started = now_utc()
    return ToolExecutionRecord.from_execution(
        context=context if context is not None else _context(),
        tool_name=tool_name,
        result=result if result is not None else _ok_result(tool_name),
        started_at=kwargs.pop("started_at", started),
        finished_at=kwargs.pop("finished_at", started + timedelta(milliseconds=3)),
        duration_ms=kwargs.pop("duration_ms", 3.5),
        **kwargs,
    )


# ============================================================
# DTO（§十四 DTO）
# ============================================================

class TestRecordDto:
    def test_creation_with_all_fields(self) -> None:
        started = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
        record = ToolExecutionRecord(
            request_id="req-1",
            round=2,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=12),
            duration_ms=12.0,
            success=True,
            project_id="project-a",
            tool_call_id="call_002",
            error_code=None,
            error_type=None,
        )
        assert record.request_id == "req-1"
        assert record.round == 2
        assert record.tool_name == "get_inventory"
        assert record.success is True
        assert record.project_id == "project-a"
        assert record.tool_call_id == "call_002"

    def test_field_whitelist_exact(self) -> None:
        record = _record()
        record.assert_field_whitelist()
        assert {f.name for f in dataclasses.fields(record)} == {
            "request_id", "project_id", "tool_call_id", "round",
            "tool_name", "started_at", "finished_at", "duration_ms",
            "success", "error_code", "error_type",
        }

    def test_no_business_or_secret_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(_record())}
        assert not (names & _FORBIDDEN_FIELDS), names & _FORBIDDEN_FIELDS

    @pytest.mark.parametrize(
        "field_name",
        [
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        ],
    )
    def test_frozen(self, field_name: str) -> None:
        record = _record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field_name, "changed")

    @pytest.mark.parametrize("bad", ["", "   ", None, 123])
    def test_request_id_must_be_non_empty_str(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id=bad, round=1, tool_name="t",
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
                success=True,
            )

    @pytest.mark.parametrize("bad", [0, -1, "1", True, None, 1.5])
    def test_round_must_be_positive_int(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=bad, tool_name="t",
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
                success=True,
            )

    @pytest.mark.parametrize("bad", ["", "  ", None, 5])
    def test_tool_name_must_be_non_empty_str(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name=bad,
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
                success=True,
            )

    @pytest.mark.parametrize("bad", ["yes", 1, 0, None])
    def test_success_must_be_strict_bool(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name="t",
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
                success=bad,
            )

    @pytest.mark.parametrize("field_name", ["project_id", "tool_call_id"])
    @pytest.mark.parametrize("bad", ["", "   ", 123])
    def test_optional_fields_reject_empty_or_wrong_type(
        self, field_name: str, bad: Any
    ) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name="t",
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
                success=True, **{field_name: bad},
            )

    def test_optional_fields_default_to_none(self) -> None:
        record = ToolExecutionRecord(
            request_id="req-1", round=1, tool_name="t",
            started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
            success=True,
        )
        assert record.project_id is None
        assert record.tool_call_id is None
        assert record.error_code is None
        assert record.error_type is None


# ============================================================
# Timing（§六）
# ============================================================

class TestTiming:
    def test_now_utc_is_timezone_aware(self) -> None:
        value = now_utc()
        assert isinstance(value, datetime)
        assert value.tzinfo is not None
        assert value.utcoffset() == timedelta(0)

    def test_elapsed_ms_is_non_negative(self) -> None:
        started = time.perf_counter()
        measured = elapsed_ms(started)
        assert isinstance(measured, float)
        assert measured >= 0.0

    def test_elapsed_ms_measures_real_duration(self) -> None:
        started = time.perf_counter()
        time.sleep(0.02)
        assert elapsed_ms(started) >= 15.0     # 容差（>= 20ms 实测）

    def test_started_finished_duration_present(self) -> None:
        record = _record()
        assert record.started_at < record.finished_at
        assert record.duration_ms == pytest.approx(3.5)

    @pytest.mark.parametrize("bad", [-0.001, -1.0, float("nan"), float("inf"), True, "1"])
    def test_invalid_duration_rejected(self, bad: Any) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name="t",
                started_at=now_utc(), finished_at=now_utc(), duration_ms=bad,
                success=True,
            )

    def test_zero_duration_is_valid(self) -> None:
        started = now_utc()
        record = ToolExecutionRecord(
            request_id="req-1", round=1, tool_name="t",
            started_at=started, finished_at=started, duration_ms=0.0,
            success=True,
        )
        assert record.duration_ms == 0.0

    def test_naive_datetime_rejected(self) -> None:
        naive = datetime(2026, 9, 27, 10, 0, 0)
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name="t",
                started_at=naive, finished_at=now_utc(), duration_ms=0.0,
                success=True,
            )

    def test_finished_before_started_rejected(self) -> None:
        started = now_utc()
        with pytest.raises(ValueError):
            ToolExecutionRecord(
                request_id="req-1", round=1, tool_name="t",
                started_at=started,
                finished_at=started - timedelta(milliseconds=1),
                duration_ms=1.0, success=True,
            )


# ============================================================
# Result Mapping（§七）
# ============================================================

class TestResultMapping:
    def test_success_true_maps_to_true(self) -> None:
        record = _record(result=_ok_result())
        assert record.success is True
        assert record.error_code is None
        assert record.error_type is None

    def test_success_false_maps_to_false(self) -> None:
        record = _record(result=_fail_result())
        assert record.success is False

    def test_handler_returned_result_is_not_execution_success(self) -> None:
        """Handler 正常返回 ToolResult(False) ≠ execution success。"""
        record = _record(result=_fail_result(error="Tool 未注册: 'ghost'"))
        assert record.success is False

    def test_result_error_message_not_stored(self) -> None:
        record = _record(result=_fail_result(error="参数校验失败: missing required"))
        assert "参数校验失败" not in repr(record)
        assert "missing required" not in repr(record)

    def test_result_data_not_stored(self) -> None:
        record = _record(result=_ok_result(qty=999.0))
        assert "999" not in repr(record)
        assert "MAT-001" not in repr(record)
        assert "qty" not in repr(record)

    def test_arguments_never_passed_to_record(self) -> None:
        """Record 构造不接受 arguments（签名白名单）。"""
        import inspect

        params = set(
            inspect.signature(
                ToolExecutionRecord.from_execution
            ).parameters
        )
        assert params == {
            "context", "tool_name", "result", "started_at", "finished_at",
            "duration_ms", "error_code", "error_type",
        }
        assert "arguments" not in params

    def test_wrong_types_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionRecord.from_execution(
                context=object(),  # type: ignore[arg-type]
                tool_name="get_inventory", result=_ok_result(),
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
            )
        with pytest.raises(TypeError):
            ToolExecutionRecord.from_execution(
                context=_context(), tool_name="get_inventory",
                result={"success": True},  # type: ignore[arg-type]
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
            )

    def test_tool_name_must_match_result(self) -> None:
        with pytest.raises(ValueError):
            ToolExecutionRecord.from_execution(
                context=_context(), tool_name="get_inventory",
                result=_ok_result(tool_name="get_work_order"),
                started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
            )


# ============================================================
# Error Mapping（§八）
# ============================================================

class TestErrorMapping:
    def test_error_code_not_invented(self) -> None:
        """ToolResult 无结构化错误码 → 不发明（恒为 None）。"""
        assert _record(result=_fail_result()).error_code is None

    def test_error_type_mapped_from_existing_tool_error(self) -> None:
        result = _fail_result(
            error="ToolValidationError: Tool 'get_inventory' 参数校验失败: "
                  "material_code 包含非法字符 (field='material_code')"
        )
        assert _record(result=result).error_type == "ToolValidationError"

    def test_error_type_none_for_registry_level_errors(self) -> None:
        """Registry 级错误（无类名前缀）→ None（不解析 message 正文）。"""
        for error in (
            "Tool 未注册: 'ghost'",
            "参数校验失败: unknown field(s): ['x']",
        ):
            assert _record(result=_fail_result(error=error)).error_type is None

    def test_error_type_allow_list_only(self) -> None:
        """非白名单前缀（如伪造 / 注入文本）→ None。"""
        for error in (
            "RuntimeError: boom",
            "postgresql://wms_user:s3cret-pw@db.internal:5432/wms",
            "Traceback (most recent call last): ...",
        ):
            assert _record(result=_fail_result(error=error)).error_type is None

    def test_explicit_error_type_validated(self) -> None:
        record = _record(
            result=_fail_result(), error_type="ToolExecutionError"
        )
        assert record.error_type == "ToolExecutionError"
        for bad in ("has space", "postgresql://x", "a" * 100, "1Bad"):
            with pytest.raises(ValueError):
                _record(result=_fail_result(), error_type=bad)

    def test_explicit_error_code_is_snake_case(self) -> None:
        assert _record(
            result=_fail_result(), error_code="handler_failure"
        ).error_code == "handler_failure"
        for bad in ("HandlerFailure", "has space", "", "1code", "a" * 100):
            with pytest.raises(ValueError):
                _record(result=_fail_result(), error_code=bad)

    def test_success_cannot_carry_error_classification(self) -> None:
        with pytest.raises(ValueError):
            _record(result=_ok_result(), error_type="ToolExecutionError")
        with pytest.raises(ValueError):
            _record(result=_ok_result(), error_code="boom")

    def test_no_secret_in_record_for_failing_result(self) -> None:
        leaky = _fail_result(
            error=(
                "ToolExecutionError: Tool 'get_inventory' 执行失败: "
                "[RuntimeError] connection refused: "
                "postgresql://wms_user:s3cret-pw@db.internal:5432/wms"
            )
        )
        record = _record(result=leaky)
        text = f"{record!r} {record} {json.dumps(dataclasses.asdict(record), default=str)}"
        for secret in _SECRETS:
            assert secret not in text, secret
        # 类名（安全）可以被记录，message 正文不行
        assert record.error_type == "ToolExecutionError"
        assert "connection refused" not in text


# ============================================================
# Context Mapping（§五）
# ============================================================

class TestContextMapping:
    def test_context_fields_mapped_verbatim(self) -> None:
        context = _context(
            request_id="req-xyz", round=3, project_id="project-b",
            tool_call_id="call_777",
        )
        record = _record(context=context)
        assert record.request_id == "req-xyz"
        assert record.round == 3
        assert record.project_id == "project-b"
        assert record.tool_call_id == "call_777"

    def test_optional_context_fields_none(self) -> None:
        context = ToolExecutionContext(request_id="req-1", round=1)
        record = _record(context=context)
        assert record.project_id is None
        assert record.tool_call_id is None

    def test_context_not_mutated(self) -> None:
        context = _context()
        before = dataclasses.asdict(context)
        _record(context=context)
        assert dataclasses.asdict(context) == before

    def test_request_id_not_regenerated(self) -> None:
        record = _record(context=_context(request_id="fixed-id"))
        assert record.request_id == "fixed-id"

    def test_round_not_changed(self) -> None:
        assert _record(context=_context(round=7)).round == 7


# ============================================================
# Isolation / Security / Immutability / Determinism
# ============================================================

class TestIsolation:
    def test_no_forbidden_field_names_in_module(self) -> None:
        with open(
            os.path.join(REPO_ROOT, *(_RECORD_MODULE.split("/"))),
            encoding="utf-8",
        ) as fh:
            tree = ast.parse(fh.read())
        identifiers = {
            node.id.lower() for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower() for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("session", "execute", "fetch", "commit", "cursor"):
            assert forbidden not in identifiers, forbidden

    def test_module_imports_are_pure(self) -> None:
        with open(
            os.path.join(REPO_ROOT, *(_RECORD_MODULE.split("/"))),
            encoding="utf-8",
        ) as fh:
            tree = ast.parse(fh.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported <= {
            "__future__", "math", "re", "time", "dataclasses", "datetime",
            "typing",
            "backend.app.services.tool_execution_context",
            "backend.app.tools.base",
        }, imported
        for forbidden in (
            "sqlalchemy", "logging", "httpx", "openai", "requests",
            "backend.app.llm", "backend.app.db",
            "backend.app.tools.registry",
        ):
            assert not any(
                name.startswith(forbidden) for name in imported
            ), forbidden

    def test_record_does_not_contain_sql_or_credentials(self) -> None:
        record = _record()
        text = repr(record)
        for needle in ("SELECT", "INSERT", "postgresql://", "Bearer "):
            assert needle not in text


class TestImmutability:
    def test_record_is_frozen(self) -> None:
        record = _record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.round = 99  # type: ignore[misc]

    def test_dataclasses_replace_returns_new_instance(self) -> None:
        record = _record()
        other = dataclasses.replace(record, round=2)
        assert record.round == 1 and other.round == 2
        assert other is not record


class TestDeterminism:
    def test_same_inputs_produce_equal_records(self) -> None:
        context = _context()
        result = _ok_result()
        started = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
        finished = started + timedelta(milliseconds=4)
        kwargs: dict[str, Any] = {
            "context": context, "tool_name": "get_inventory", "result": result,
            "started_at": started, "finished_at": finished, "duration_ms": 4.0,
        }
        first = ToolExecutionRecord.from_execution(**kwargs)
        second = ToolExecutionRecord.from_execution(**kwargs)
        assert first == second
        assert dataclasses.asdict(first) == dataclasses.asdict(second)

    def test_from_execution_is_pure(self) -> None:
        """同一输入两次调用：不产生副作用（上下文 / 结果不变）。"""
        context = _context()
        result = _fail_result()
        before_context = dataclasses.asdict(context)
        before_result = dataclasses.asdict(result)
        ToolExecutionRecord.from_execution(
            context=context, tool_name="get_inventory", result=result,
            started_at=now_utc(), finished_at=now_utc(), duration_ms=0.0,
        )
        assert dataclasses.asdict(context) == before_context
        assert dataclasses.asdict(result) == before_result


# ============================================================
# 与真实链路组合（1 个 DB-gated；生产接线 = Deferred）
# ============================================================

@requires_db()
class TestRealToolRecordDb:
    async def test_record_from_real_get_inventory(self, real_engine: Any) -> None:
        """真实 ToolResult + Context + Timing → Record（MAT-001 → 250.0）。"""
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        registry = _real_registry(real_engine)
        boundary = ToolExecutionService(
            registry=registry, project_id=_PROJECT_ID
        )
        context = ToolExecutionContext(
            request_id="req-db-1",
            round=1,
            project_id=_PROJECT_ID,
            tool_call_id="call_001",
        )
        started_at = now_utc()
        perf_started = time.perf_counter()
        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "MAT-001"},
            context=context,
        )
        record = ToolExecutionRecord.from_execution(
            context=context, tool_name="get_inventory", result=result,
            started_at=started_at, finished_at=now_utc(),
            duration_ms=elapsed_ms(perf_started),
        )

        assert result.success is True
        assert float(result.data["qty"]) == pytest.approx(250.0)
        assert record.success is True
        assert record.tool_name == "get_inventory"
        assert record.round == 1
        assert record.project_id == _PROJECT_ID
        assert record.request_id == "req-db-1"
        assert record.duration_ms >= 0.0
        record.assert_field_whitelist()
        # Record 不携带业务数据 / SQL
        text = repr(record)
        assert "MAT-001" not in text and "SELECT" not in text.upper()


# ============================================================
# 与既有错误类型组合（0 DB）
# ============================================================

class TestRecordWithRealRegistry:
    async def test_handler_tool_error_maps_error_type(self) -> None:
        async def _raise(arguments: dict[str, Any]) -> dict[str, Any]:
            raise ToolValidationError(
                tool_name="get_inventory",
                reason="material_code 包含非法字符",
                field_path="material_code",
            )

        registry = ToolRegistry()
        register_get_inventory_tool(registry, handler=_raise)
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        result = await ToolExecutionService(registry=registry).execute(
            "get_inventory", arguments={"material_code": "x"}
        )
        record = _record(result=result)

        assert result.success is False
        assert record.success is False
        assert record.error_type == "ToolValidationError"
        assert "非法字符" not in repr(record)      # message 不写入 Record

    async def test_unknown_tool_record_has_no_error_type(self) -> None:
        admin = ToolRegistry()
        admin.register(MOCK_INVENTORY_DEFINITION, lambda a: a)  # type: ignore[arg-type]
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        result = await ToolExecutionService(registry=admin).execute("ghost")
        record = _record(tool_name="ghost", result=result)

        assert record.success is False
        assert record.error_type is None           # 不推断 / 不发明
        assert record.error_code is None
