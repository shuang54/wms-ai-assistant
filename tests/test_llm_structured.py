"""LLM Structured Response Contract 测试（Phase 3.10.3）。

覆盖（任务书 §十六）：

    1. Bare JSON                PASS
    2. JSON Code Fence          PASS
    3. Invalid JSON             FAIL
    4. Wrong type               FAIL
    5. Missing required field   FAIL
    6. Unknown field            FAIL
    7. Multiple JSON objects    FAIL
    8. Natural language around  FAIL（严格规则）
    9. Empty output             FAIL
    10. Whitespace              PASS
    11. Wrong model             FAIL
    12. No network              ——（全部为纯函数测试，0 网络 / 0 LLM / 0 DB）

附加：Literal 校验、非 object JSON、未闭合 fence、fence 夹带文字、
异常层级、message 防泄漏（不携带原始输出）、纯度（输入不被修改）。

测试模型（§十五）只定义在本测试模块，不进 production domain。
"""
from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel

from backend.app.llm.client import LLMError
from backend.app.llm.structured import (
    REASON_EMPTY_OUTPUT,
    REASON_INVALID_JSON,
    REASON_SCHEMA_VALIDATION_FAILED,
    REASON_UNSUPPORTED_FORMAT,
    LLMStructuredModel,
    LLMStructuredOutputError,
    parse_structured_response,
)


# ============================================================
# 测试模型（§十五：仅测试模块使用）
# ============================================================

class DemoStructuredResponse(LLMStructuredModel):
    """最小结构化响应测试模型。"""

    answer: str
    confidence: float


class DemoRouteResponse(LLMStructuredModel):
    """Literal enum 测试模型。"""

    route: Literal["rag", "tool", "text_to_sql"]
    confidence: float


class PlainBaseModelResponse(BaseModel):
    """未继承基类的普通 model：parser 仍强制 unknown-field 拒绝。"""

    answer: str


# ============================================================
# Helpers
# ============================================================

def _expect_error(
    raw: Any,
    model: type[BaseModel],
    reason: str,
) -> LLMStructuredOutputError:
    """断言解析失败且 reason 正确，返回异常供进一步断言。"""
    with pytest.raises(LLMStructuredOutputError) as exc_info:
        parse_structured_response(raw, model)
    assert exc_info.value.reason == reason
    return exc_info.value


# ============================================================
# PASS 场景
# ============================================================

def test_bare_json_object_passes() -> None:
    """场景 1：bare JSON。"""
    result = parse_structured_response(
        '{"answer":"hello","confidence":0.9}', DemoStructuredResponse
    )
    assert isinstance(result, DemoStructuredResponse)
    assert result.answer == "hello"
    assert result.confidence == 0.9


def test_json_code_fence_passes() -> None:
    """场景 2：```json code fence。"""
    raw = '```json\n{"answer":"hello","confidence":0.9}\n```'
    result = parse_structured_response(raw, DemoStructuredResponse)
    assert result.answer == "hello"
    assert result.confidence == 0.9


def test_code_fence_without_language_tag_passes() -> None:
    """无语言标记的 ``` fence 同样支持。"""
    raw = '```\n{"answer":"hello","confidence":0.9}\n```'
    result = parse_structured_response(raw, DemoStructuredResponse)
    assert result.answer == "hello"


def test_surrounding_whitespace_passes() -> None:
    """场景 10：前后普通 whitespace。"""
    result = parse_structured_response(
        '  {"answer":"hello","confidence":0.9} \n', DemoStructuredResponse
    )
    assert result.answer == "hello"


def test_literal_enum_valid_passes() -> None:
    """Literal 枚举合法值。"""
    result = parse_structured_response(
        '{"route":"text_to_sql","confidence":0.92}', DemoRouteResponse
    )
    assert result.route == "text_to_sql"
    assert result.confidence == 0.92


def test_typed_result_is_model_instance() -> None:
    """输出是 Pydantic model 实例（typed result），不是 dict。"""
    result = parse_structured_response(
        '{"answer":"hello","confidence":0.9}', DemoStructuredResponse
    )
    assert type(result) is DemoStructuredResponse


# ============================================================
# FAIL 场景
# ============================================================

def test_invalid_json_fails() -> None:
    """场景 3：JSON 语法错误。"""
    _expect_error('{"answer":', DemoStructuredResponse, REASON_INVALID_JSON)


def test_wrong_type_fails() -> None:
    """场景 4：字段类型错误（"high" 不是合法 float）。"""
    raw = '{"answer":"hello","confidence":"high"}'
    _expect_error(raw, DemoStructuredResponse, REASON_SCHEMA_VALIDATION_FAILED)


def test_missing_required_field_fails() -> None:
    """场景 5：缺少必填字段。"""
    _expect_error(
        '{"answer":"hello"}', DemoStructuredResponse,
        REASON_SCHEMA_VALIDATION_FAILED,
    )


def test_unknown_field_fails() -> None:
    """场景 6：未知字段拒绝（不静默吞掉；错误暴露字段名但不含值）。"""
    raw = '{"answer":"hello","confidence":0.9,"hack":"unexpected"}'
    err = _expect_error(
        raw, DemoStructuredResponse, REASON_SCHEMA_VALIDATION_FAILED,
    )
    assert "hack" in str(err)          # 未知字段名被暴露
    assert "unexpected" not in str(err)  # 字段值不进入异常信息


def test_multiple_json_objects_fail() -> None:
    """场景 7：多个 JSON object 拒绝。"""
    raw = '{"answer":"a","confidence":0.1}\n{"answer":"b","confidence":0.2}'
    _expect_error(raw, DemoStructuredResponse, REASON_INVALID_JSON)


def test_natural_language_around_json_fails() -> None:
    """场景 8：JSON 前后夹杂自然语言 —— 严格规则拒绝
    （不做 find("{")/find("}") 截取）。"""
    _expect_error(
        'Here is the result:\n{"answer":"hello","confidence":0.9}',
        DemoStructuredResponse,
        REASON_INVALID_JSON,
    )


def test_empty_output_fails() -> None:
    """场景 9：空输出 / 纯空白。"""
    _expect_error("", DemoStructuredResponse, REASON_EMPTY_OUTPUT)
    _expect_error("   \n  ", DemoStructuredResponse, REASON_EMPTY_OUTPUT)
    _expect_error(None, DemoStructuredResponse, REASON_EMPTY_OUTPUT)


def test_wrong_model_fails() -> None:
    """场景 11：同一 JSON 用不匹配的 model 校验。"""
    _expect_error(
        '{"answer":"hello","confidence":0.9}',
        DemoRouteResponse,
        REASON_SCHEMA_VALIDATION_FAILED,
    )


def test_literal_enum_invalid_value_fails() -> None:
    """Literal 越界值拒绝。"""
    _expect_error(
        '{"route":"unknown","confidence":0.92}',
        DemoRouteResponse,
        REASON_SCHEMA_VALIDATION_FAILED,
    )


def test_literal_enum_wrong_type_fails() -> None:
    """Literal 字段类型错误拒绝。"""
    _expect_error(
        '{"route":"rag","confidence":"high"}',
        DemoRouteResponse,
        REASON_SCHEMA_VALIDATION_FAILED,
    )


def test_non_object_json_fails() -> None:
    """第一版只支持 JSON object：数组 / 标量拒绝。"""
    _expect_error("[1, 2, 3]", DemoStructuredResponse, REASON_UNSUPPORTED_FORMAT)
    _expect_error('"just a string"', DemoStructuredResponse, REASON_UNSUPPORTED_FORMAT)


def test_unclosed_fence_fails() -> None:
    """未闭合 fence 拒绝。"""
    raw = '```json\n{"answer":"hello","confidence":0.9}\n'
    _expect_error(raw, DemoStructuredResponse, REASON_UNSUPPORTED_FORMAT)


def test_fence_with_trailing_text_fails() -> None:
    """fence 之后附带其它文字拒绝（必须恰好是一个完整 fence）。"""
    raw = '```json\n{"answer":"hello","confidence":0.9}\n```\nHope this helps!'
    _expect_error(raw, DemoStructuredResponse, REASON_UNSUPPORTED_FORMAT)


def test_plain_base_model_unknown_field_still_rejected() -> None:
    """未继承基类的普通 model：parser 仍强制 unknown-field 拒绝
    （LLM 输出不可信，禁止静默吞掉额外字段）。"""
    _expect_error(
        '{"answer":"hello","extra":1}',
        PlainBaseModelResponse,
        REASON_SCHEMA_VALIDATION_FAILED,
    )


def test_non_string_non_none_input_fails() -> None:
    """输入类型非法（如 dict 直传）拒绝——调用方必须传 raw 字符串。"""
    _expect_error(
        {"answer": "hello"}, DemoStructuredResponse, REASON_UNSUPPORTED_FORMAT,
    )


# ============================================================
# 异常 Contract / 安全 / 纯度
# ============================================================

def test_structured_error_is_llm_error() -> None:
    """LLMStructuredOutputError 属于既有 LLMError 家族
    （AI Core 可统一捕获，无需感知新的异常分支）。"""
    assert issubclass(LLMStructuredOutputError, LLMError)


def test_error_message_does_not_leak_raw_output() -> None:
    """异常 message 不携带原始 LLM 输出
    （防敏感信息 / 超长内容 / prompt injection 泄漏）。"""
    raw = '{"answer":"SECRET-TOKEN-ABC","confidence":"<injection-payload>"}'
    err = _expect_error(
        raw, DemoStructuredResponse, REASON_SCHEMA_VALIDATION_FAILED,
    )
    text = str(err)
    assert "SECRET-TOKEN-ABC" not in text
    assert "<injection-payload>" not in text


def test_invalid_json_message_does_not_leak_raw_output() -> None:
    """invalid_json 的 message 同样不携带原文。"""
    raw = 'Here is the result: {"answer":"SENSITIVE-CONTENT"}'
    err = _expect_error(raw, DemoStructuredResponse, REASON_INVALID_JSON)
    assert "SENSITIVE-CONTENT" not in str(err)


def test_parser_is_pure_and_deterministic() -> None:
    """纯函数：无副作用（输入不被修改）、deterministic（同输入同结果）。"""
    raw = '{"answer":"hello","confidence":0.9}'
    r1 = parse_structured_response(raw, DemoStructuredResponse)
    r2 = parse_structured_response(raw, DemoStructuredResponse)
    assert r1 == r2
    assert raw == '{"answer":"hello","confidence":0.9}'  # 原文未被修改
