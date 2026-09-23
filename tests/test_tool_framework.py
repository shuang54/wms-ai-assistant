"""Tool Framework 测试（Phase 3.6.1）。

覆盖：

    * ``ToolDefinition`` 构造校验（name / description / parameters）
    * ``ToolResult`` 三态契约（success / data / error 关系）
    * ``validate_arguments`` 极简 JSON Schema 子集
    * ``ToolRegistry`` 注册 / 注销 / 查找 / list
    * ``ToolRegistry.execute`` 全部执行路径：
        - 正常执行
        - 参数缺失 / 类型错误 / 未知字段
        - Tool 不存在
        - Handler 抛 ToolError / 抛未知异常
        - ``error`` 不暴露 traceback / 敏感信息
    * Mock Tools（``get_inventory`` / ``get_work_order``）端到端
    * 安全：ToolResult / ToolDefinition 不暴露 API Key / Authorization /
      DATABASE_URL / SQL / traceback / embedding

不依赖 DB / LLM / Embedding。
"""
from __future__ import annotations

import pytest

from backend.app.tools.base import (
    ToolDefinition,
    ToolResult,
    validate_arguments,
)
from backend.app.tools.errors import (
    ToolAlreadyRegisteredError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION,
    GET_WORK_ORDER_DEFINITION,
    GetInventoryHandler,
    GetWorkOrderHandler,
    register_mock_tools,
)
from backend.app.tools.registry import ToolRegistry


# ============================================================
# 1. ToolDefinition 构造
# ============================================================

class TestToolDefinitionConstruction:
    """ToolDefinition 不可变 DTO 的构造 / 字段校验。"""

    def test_normal_construction(self) -> None:
        d = ToolDefinition(
            name="get_foo",
            description="查询 foo",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert d.name == "get_foo"
        assert d.description == "查询 foo"
        assert d.parameters == {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

    def test_default_parameters_is_empty_dict(self) -> None:
        d = ToolDefinition(name="noop", description="空 Tool")
        assert d.parameters == {}

    def test_is_frozen(self) -> None:
        d = ToolDefinition(name="t", description="d")
        with pytest.raises((AttributeError, Exception)):
            d.name = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "bad_name",
        ["", "has space", "has.dot", "has/slash", "中文_tool"],
    )
    def test_invalid_name_rejected(self, bad_name: str) -> None:
        with pytest.raises(ValueError):
            ToolDefinition(name=bad_name, description="d")

    def test_non_string_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolDefinition(name=123, description="d")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_desc", ["", "   ", "\t\n"])
    def test_empty_description_rejected(self, bad_desc: str) -> None:
        with pytest.raises(ValueError):
            ToolDefinition(name="t", description=bad_desc)

    def test_non_dict_parameters_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolDefinition(name="t", description="d", parameters="not dict")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "good_name",
        ["get_inventory", "get-work-order", "tool1", "A_B-C_2"],
    )
    def test_valid_names_accepted(self, good_name: str) -> None:
        d = ToolDefinition(name=good_name, description="d")
        assert d.name == good_name


# ============================================================
# 2. ToolResult 三态契约
# ============================================================

class TestToolResultContract:
    """ToolResult success / data / error 关系。"""

    def test_success_with_dict_data(self) -> None:
        r = ToolResult(tool_name="t", success=True, data={"x": 1})
        assert r.success is True
        assert r.data == {"x": 1}
        assert r.error is None

    def test_success_with_none_data_allowed(self) -> None:
        r = ToolResult(tool_name="t", success=True, data=None)
        assert r.success is True
        assert r.data is None

    def test_failure_with_error(self) -> None:
        r = ToolResult(tool_name="t", success=False, error="bad input")
        assert r.success is False
        assert r.data is None
        assert r.error == "bad input"

    def test_success_with_error_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(tool_name="t", success=True, error="oops")

    def test_failure_with_data_rejected(self) -> None:
        with pytest.raises(ValueError):
            ToolResult(tool_name="t", success=False, data={"x": 1}, error="x")

    @pytest.mark.parametrize("bad_error", ["", "   "])
    def test_failure_with_empty_error_rejected(self, bad_error: str) -> None:
        with pytest.raises(ValueError):
            ToolResult(tool_name="t", success=False, error=bad_error)

    def test_is_frozen(self) -> None:
        r = ToolResult(tool_name="t", success=True)
        with pytest.raises((AttributeError, Exception)):
            r.success = False  # type: ignore[misc]


# ============================================================
# 3. validate_arguments 极简校验
# ============================================================

_SAMPLE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "s": {"type": "string"},
        "i": {"type": "integer"},
        "n": {"type": "number"},
        "b": {"type": "boolean"},
        "a": {"type": "array"},
        "optional": {"type": "string"},
    },
    "required": ["s", "i", "n", "b", "a"],
}


class TestValidateArguments:
    """极简 JSON Schema 子集。"""

    def test_all_required_correct_types_pass(self) -> None:
        validate_arguments(
            _SAMPLE_SCHEMA,
            {"s": "x", "i": 1, "n": 1.5, "b": True, "a": [1, 2]},
        )  # 不抛即通过

    def test_missing_required_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required"):
            validate_arguments(_SAMPLE_SCHEMA, {"s": "x"})

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown field"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": "x", "i": 1, "n": 1.0, "b": True, "a": [], "extra": 1},
            )

    def test_string_type_wrong_rejected(self) -> None:
        with pytest.raises(ValueError, match="string"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": 123, "i": 1, "n": 1.0, "b": True, "a": []},
            )

    def test_integer_type_wrong_rejected(self) -> None:
        with pytest.raises(ValueError, match="integer"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": "x", "i": "1", "n": 1.0, "b": True, "a": []},
            )

    def test_integer_rejects_bool(self) -> None:
        """bool 是 int 子类，必须先排除。"""
        with pytest.raises(ValueError, match="integer"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": "x", "i": True, "n": 1.0, "b": True, "a": []},
            )

    def test_boolean_type_wrong_rejected(self) -> None:
        with pytest.raises(ValueError, match="boolean"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": "x", "i": 1, "n": 1.0, "b": "yes", "a": []},
            )

    def test_array_type_wrong_rejected(self) -> None:
        with pytest.raises(ValueError, match="array"):
            validate_arguments(
                _SAMPLE_SCHEMA,
                {"s": "x", "i": 1, "n": 1.0, "b": True, "a": "not array"},
            )

    def test_optional_missing_ok(self) -> None:
        validate_arguments(
            _SAMPLE_SCHEMA,
            {"s": "x", "i": 1, "n": 1.0, "b": True, "a": []},
        )

    def test_non_object_schema_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="object"):
            validate_arguments(
                {"type": "string", "properties": {}},
                {},
            )

    def test_non_mapping_arguments_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_arguments(_SAMPLE_SCHEMA, ["not", "mapping"])  # type: ignore[arg-type]

    def test_unsupported_type_rejected(self) -> None:
        """嵌套 object 不在本阶段支持范围内。"""
        with pytest.raises(ValueError, match="object"):
            validate_arguments(
                {
                    "type": "object",
                    "properties": {"x": {"type": "object"}},
                    "required": ["x"],
                },
                {"x": {}},
            )


# ============================================================
# 4. Registry 注册 / 注销 / 查找
# ============================================================

class TestRegistryRegisterUnregister:
    def test_initial_state(self) -> None:
        r = ToolRegistry()
        assert len(r) == 0
        assert r.list_definitions() == ()
        assert "foo" not in r

    def test_register_single(self) -> None:
        r = ToolRegistry()
        d = ToolDefinition(name="t1", description="d")
        h = GetInventoryHandler()
        r.register(d, h)
        assert len(r) == 1
        assert "t1" in r
        assert r.get_definition("t1") is d

    def test_register_multiple_preserves_order(self) -> None:
        r = ToolRegistry()
        d1 = ToolDefinition(name="a", description="d")
        d2 = ToolDefinition(name="b", description="d")
        d3 = ToolDefinition(name="c", description="d")
        for d in (d1, d2, d3):
            r.register(d, GetInventoryHandler())
        assert [d.name for d in r.list_definitions()] == ["a", "b", "c"]

    def test_duplicate_register_rejected(self) -> None:
        r = ToolRegistry()
        d = ToolDefinition(name="dup", description="d")
        r.register(d, GetInventoryHandler())
        with pytest.raises(ToolAlreadyRegisteredError) as exc:
            r.register(d, GetWorkOrderHandler())
        assert exc.value.tool_name == "dup"

    def test_unregister_existing(self) -> None:
        r = ToolRegistry()
        d = ToolDefinition(name="t", description="d")
        r.register(d, GetInventoryHandler())
        r.unregister("t")
        assert "t" not in r
        assert len(r) == 0

    def test_unregister_nonexistent_rejected(self) -> None:
        r = ToolRegistry()
        with pytest.raises(ToolNotFoundError) as exc:
            r.unregister("nope")
        assert exc.value.tool_name == "nope"

    def test_get_definition_nonexistent_rejected(self) -> None:
        r = ToolRegistry()
        with pytest.raises(ToolNotFoundError):
            r.get_definition("nope")


class TestRegistryListDefinitions:
    def test_list_definitions_does_not_expose_handler(self) -> None:
        """list_definitions 返回纯 DTO，**不含** Handler。"""
        r = ToolRegistry()
        d = ToolDefinition(
            name="t",
            description="d",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        r.register(d, GetInventoryHandler())
        listed = r.list_definitions()
        assert len(listed) == 1
        assert listed[0] is d
        # ToolDefinition DTO 字段验证
        assert listed[0].name == "t"
        assert "type" in listed[0].parameters

    def test_list_definitions_returns_tuple(self) -> None:
        r = ToolRegistry()
        r.register(ToolDefinition(name="t", description="d"), GetInventoryHandler())
        result = r.list_definitions()
        assert isinstance(result, tuple)

    def test_separate_registry_instances_are_independent(self) -> None:
        """不同实例互不影响（无全局状态）。"""
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        d = ToolDefinition(name="t", description="d")
        r1.register(d, GetInventoryHandler())
        assert "t" in r1
        assert "t" not in r2


# ============================================================
# 5. Registry.execute
# ============================================================

def _simple_definition() -> ToolDefinition:
    return ToolDefinition(
        name="echo",
        description="echo back arguments",
        parameters={
            "type": "object",
            "properties": {
                "msg": {"type": "string"},
                "n": {"type": "integer"},
            },
            "required": ["msg"],
        },
    )


class _EchoHandler:
    async def __call__(self, arguments: dict) -> dict:
        return {"echoed": arguments["msg"], "n": arguments.get("n", 0)}


class _AlwaysFailHandler:
    async def __call__(self, arguments: dict) -> dict:
        raise RuntimeError("boom-secret-info")


class _ToolErrorHandler:
    async def __call__(self, arguments: dict) -> dict:
        raise ToolExecutionError(
            tool_name="echo",
            reason="bad",
            cause_type="ValueError",
        )


class TestRegistryExecuteNormal:
    async def test_successful_execution(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _EchoHandler())
        result = await r.execute("echo", {"msg": "hi", "n": 3})
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == {"echoed": "hi", "n": 3}
        assert result.error is None
        assert result.tool_name == "echo"

    async def test_arguments_none_treated_as_empty(self) -> None:
        """arguments=None 时按空 dict 处理（不会触发 required 缺失）—— 应抛 ToolValidationError。"""
        r = ToolRegistry()

        # 用无 required 字段的 def 来验证 None → {} 行为
        d = ToolDefinition(name="noop", description="d", parameters={})
        h = _EchoHandler()
        r.register(d, h)
        # EchoHandler 期望 'msg' 必填；arguments=None → 走校验 → 缺 msg
        result = await r.execute("noop", None)
        # EchoHandler 不校验参数，msg 直接 KeyError → 归一为 ToolExecutionError
        assert result.success is False
        assert "KeyError" in (result.error or "") or "missing" in (result.error or "")


class TestRegistryExecuteValidation:
    async def test_missing_required_field_returns_failure(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _EchoHandler())
        result = await r.execute("echo", {})  # 缺 msg
        assert result.success is False
        assert result.tool_name == "echo"
        assert result.data is None
        assert "missing" in (result.error or "").lower() or "required" in (result.error or "").lower()

    async def test_unknown_field_returns_failure(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _EchoHandler())
        result = await r.execute("echo", {"msg": "x", "extra": 1})
        assert result.success is False
        assert "unknown" in (result.error or "").lower()

    async def test_type_mismatch_returns_failure(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _EchoHandler())
        result = await r.execute("echo", {"msg": 123})  # msg 应为 string
        assert result.success is False
        assert "string" in (result.error or "").lower()


class TestRegistryExecuteNotFound:
    async def test_tool_not_found_returns_failure(self) -> None:
        r = ToolRegistry()
        result = await r.execute("ghost", {"x": 1})
        assert result.success is False
        assert result.data is None
        assert "ghost" in (result.error or "")
        assert "Tool 未注册" in (result.error or "")


class TestRegistryExecuteHandlerExceptions:
    async def test_handler_raising_tool_error(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _ToolErrorHandler())
        result = await r.execute("echo", {"msg": "x"})
        assert result.success is False
        assert result.data is None
        assert result.tool_name == "echo"
        assert "ToolExecutionError" in (result.error or "")

    async def test_handler_raising_unexpected_error_does_not_leak(self) -> None:
        r = ToolRegistry()
        r.register(_simple_definition(), _AlwaysFailHandler())
        result = await r.execute("echo", {"msg": "x"})
        assert result.success is False
        assert result.data is None
        # 不暴露原始异常 message
        assert "boom-secret-info" not in (result.error or "")
        # 不暴露 traceback 关键标识
        assert "Traceback" not in (result.error or "")
        assert "RuntimeError" in (result.error or "")


# ============================================================
# 6. Mock Tools 集成
# ============================================================

class TestMockToolsIntegration:
    async def test_get_inventory_returns_expected_data(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        result = await r.execute(
            "get_inventory", {"material_code": "MAT001"}
        )
        assert result.success is True
        assert result.data == {
            "material_code": "MAT001",
            "warehouse_code": None,
            "quantity": 1000,
            "unit": "PCS",
        }

    async def test_get_inventory_with_warehouse(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        result = await r.execute(
            "get_inventory",
            {"material_code": "MAT001", "warehouse_code": "WH01"},
        )
        assert result.success is True
        assert result.data["warehouse_code"] == "WH01"

    async def test_get_inventory_missing_required_fails(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        result = await r.execute("get_inventory", {})
        assert result.success is False

    async def test_get_inventory_unknown_field_fails(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        result = await r.execute(
            "get_inventory", {"material_code": "MAT001", "qty": 1}
        )
        assert result.success is False
        assert "unknown" in (result.error or "").lower()

    async def test_get_work_order_returns_expected_data(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        result = await r.execute(
            "get_work_order", {"work_order_no": "WO-2024-001"}
        )
        assert result.success is True
        assert result.data == {
            "work_order_no": "WO-2024-001",
            "status": "RELEASED",
        }

    def test_register_mock_tools_registers_both(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        assert len(r) == 2
        assert "get_inventory" in r
        assert "get_work_order" in r

    def test_mock_definitions_have_descriptions(self) -> None:
        assert GET_INVENTORY_DEFINITION.description.strip()
        assert GET_WORK_ORDER_DEFINITION.description.strip()

    def test_mock_definitions_are_isolated_from_unrelated_state(self) -> None:
        """两次调用 register_mock_tools 注册到不同实例应互不影响。"""
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        register_mock_tools(r1)
        # r2 没注册
        assert "get_inventory" not in r2


# ============================================================
# 7. 安全：DTO / Result / Error 不暴露敏感信息
# ============================================================

_SECRET_KEYS = [
    "sk-1234567890",
    "API_KEY=sk-abcdef",
    "Authorization: Bearer xxx",
    "DATABASE_URL=postgresql://user:pass@host:5432/db",
    "SELECT * FROM users",
    "sk-REAL-KEY-do-not-leak",
    "traceback",
    "Traceback (most recent call last):",
    "embedding vector",
    "[0.123, 0.456, ...]",
]


class TestToolFrameworkSecurity:
    """确认 ToolResult / ToolDefinition / 不暴露敏感信息。"""

    def test_definition_to_dict_has_no_sensitive_keys(self) -> None:
        for d in (GET_INVENTORY_DEFINITION, GET_WORK_ORDER_DEFINITION):
            as_dict = {
                "name": d.name,
                "description": d.description,
                "parameters": d.parameters,
            }
            text = str(as_dict)
            for secret in _SECRET_KEYS:
                assert secret not in text, (
                    f"sensitive secret {secret!r} leaked in ToolDefinition"
                )

    async def test_execute_failure_not_leak_handler_secret(self) -> None:
        """Handler 抛异常时，error 字段不包含 Handler 内秘密字符串。"""

        class _SecretHandler:
            async def __call__(self, arguments):
                raise RuntimeError(
                    "leaked: sk-REAL-KEY-do-not-leak, password=hunter2"
                )

        r = ToolRegistry()
        r.register(_simple_definition(), _SecretHandler())
        result = await r.execute("echo", {"msg": "x"})
        assert result.success is False
        assert result.data is None
        # error 字段不含原始敏感串
        assert "sk-REAL-KEY-do-not-leak" not in (result.error or "")
        assert "hunter2" not in (result.error or "")
        # 但 cause_type 应被记录
        assert "RuntimeError" in (result.error or "")

    async def test_tool_definition_not_logging_handler(self) -> None:
        """list_definitions 不暴露 Handler。"""

        class _PrivateHandler:
            async def __call__(self, arguments):
                return "secret-data"

        r = ToolRegistry()
        d = ToolDefinition(name="priv", description="d")
        r.register(d, _PrivateHandler())
        listed = r.list_definitions()
        assert len(listed) == 1
        # ToolDefinition 没有 handler 字段
        assert not hasattr(listed[0], "handler")
        assert not hasattr(listed[0], "_handler")
        # 通过 dir() 验证不存在 handler 属性
        assert "handler" not in listed[0].__dict__
        assert "_handler" not in listed[0].__dict__


# ============================================================
# 8. Module surface area（防回归）
# ============================================================

class TestModuleSurface:
    """框架导出面稳定：所有列在 ``__all__`` 的符号均可导入。"""

    def test_core_ingestables(self) -> None:
        from backend.app.tools import (
            ToolDefinition,
            ToolHandler,
            ToolResult,
            ToolRegistry,
            ToolError,
            ToolAlreadyRegisteredError,
            ToolNotFoundError,
            ToolValidationError,
            ToolExecutionError,
            validate_arguments,
            GET_INVENTORY_DEFINITION,
            GET_WORK_ORDER_DEFINITION,
            GetInventoryHandler,
            GetWorkOrderHandler,
            register_mock_tools,
        )

        assert ToolDefinition is not None
        assert ToolHandler is not None
        assert ToolResult is not None
        assert ToolRegistry is not None
        assert validate_arguments is not None
        assert all(
            cls is not None
            for cls in (
                ToolError,
                ToolAlreadyRegisteredError,
                ToolNotFoundError,
                ToolValidationError,
                ToolExecutionError,
            )
        )