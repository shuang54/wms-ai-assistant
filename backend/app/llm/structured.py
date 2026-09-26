"""LLM Structured Output / Response Contract（Phase 3.10.3）。

职责边界：

    LLMProvider（LLM 调用，另行负责）
        ↓ Raw Output（str）
    parse_structured_response（本模块：严格 JSON 解析 + Pydantic 校验）
        ↓ Typed Result（Pydantic model 实例）

设计约束（Phase 3.10.3）：

    * 通用能力：不与 DeepSeek / 任何具体 Provider 绑定；
    * Parser 不是 LLM Client：零网络、不调用 LLM、无副作用；
    * 第一版只支持 **JSON object**（bare JSON 或完整 ```` ```json ````
      code fence）；不支持 YAML / XML / CSV / Markdown table；
    * 严格模式：拒绝多个 JSON object、JSON 前后夹杂自然语言、
      未闭合 / 不完整 fence、非 object JSON
      （不使用 find("{")/find("}") 截取，避免把自然语言中的
      JSON 片段误判为结构化结果）；
    * LLM 输出视为 **untrusted input**：只允许 json.loads +
      Pydantic validation，禁止 eval / exec / pickle 等动态执行；
    * 未知字段一律拒绝（extra=forbid 语义：LLM 输出不可静默吞字段）；
    * 异常 message 保持简洁，**不携带原始 LLM 输出**
      （可能包含敏感信息 / 超长内容 / prompt injection）。

当前状态：独立基础能力，**尚未接入** Text-to-SQL / RAG / Tool /
Router / Orchestrator 生产路径（保持既有行为与评估基线不变）。
Tool Calling 是另一层协议（模型请求调用工具），与本能力无关。
"""
from __future__ import annotations

import json
import re
from typing import Final, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.llm.client import LLMError

__all__ = [
    "REASON_EMPTY_OUTPUT",
    "REASON_INVALID_JSON",
    "REASON_UNSUPPORTED_FORMAT",
    "REASON_SCHEMA_VALIDATION_FAILED",
    "LLMStructuredModel",
    "LLMStructuredOutputError",
    "parse_structured_response",
]

T = TypeVar("T", bound=BaseModel)


# ============================================================
# 失败原因（reason contract）
# ============================================================

REASON_EMPTY_OUTPUT: Final[str] = "empty_output"
"""LLM 输出为空 / 纯空白 / content=None。"""

REASON_INVALID_JSON: Final[str] = "invalid_json"
"""不是合法的（单个）JSON object：JSON 语法错误、多个 JSON、
JSON 前后夹杂自然语言等。"""

REASON_UNSUPPORTED_FORMAT: Final[str] = "unsupported_format"
"""结构上不支持：JSON 非 object、code fence 不完整 / 未闭合、
输入类型非法等。"""

REASON_SCHEMA_VALIDATION_FAILED: Final[str] = "schema_validation_failed"
"""JSON 合法但未通过 Pydantic schema 校验
（类型错误 / 缺字段 / 未知字段 / Literal 越界等）。"""


# ============================================================
# 异常
# ============================================================

class LLMStructuredOutputError(LLMError):
    """结构化输出解析 / 校验失败（Phase 3.10.3）。

    继承 ``LLMError``：AI Core 可按既有 LLM 异常体系统一捕获。

    Attributes:
        reason:  失败类别（REASON_* 常量之一）。
        message: 简洁错误说明；**不含**原始 LLM 输出内容
                 （防止敏感信息 / 超长内容 / prompt injection 泄漏）。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ============================================================
# 推荐基类
# ============================================================

class LLMStructuredModel(BaseModel):
    """Structured Response Model 推荐基类。

    ``extra="forbid"``：LLM 输出属于不可信输入，未知字段必须
    暴露为校验失败，而不是静默吞掉。仅影响继承本基类的
    Structured Response Model，不改变项目其它 Pydantic Model
    （API response 等）的既有策略。

    第一版约定：输入 JSON 的键 = 字段名（不支持 alias 输入键）。
    """

    model_config = ConfigDict(extra="forbid")


# ============================================================
# 内部实现
# ============================================================

#: 完整 JSON code fence（```json ... ``` 或 ``` ... ```）；
#: 用 fullmatch 锚定"整个输出恰好是一个 fence"，不截取。
_JSON_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?[ \t]*```",
    re.DOTALL,
)


def _fail(reason: str, message: str) -> NoReturn:
    raise LLMStructuredOutputError(reason, message)


def _extract_json_object_text(raw: str) -> str:
    """提取待解析的 JSON 文本（严格：纯 JSON object 或完整 fence）。

    规则：
        * 空输出 → empty_output；
        * 以 ``` 开头 → 整体必须是**一个完整闭合**的 fence
          （fence 后附带任何其它文字 → unsupported_format）；
        * 其余 → 原文（整体必须是单个 JSON object，
          前后仅允许普通 whitespace）。
    """
    text = raw.strip()
    if not text:
        _fail(REASON_EMPTY_OUTPUT, "LLM 输出为空或纯空白")

    if text.startswith("```"):
        match = _JSON_FENCE_RE.fullmatch(text)
        if match is None:
            _fail(
                REASON_UNSUPPORTED_FORMAT,
                "结构化输出必须是完整的 JSON code fence（未闭合或附带其它文字）",
            )
        return match.group(1).strip()

    return text


def _schema_error_summary(exc: ValidationError) -> str:
    """把 ValidationError 转成简洁摘要。

    只取 loc + msg；**不包含** input_value（Pydantic 错误详情中的
    原始输入值可能携带敏感内容 / 注入文本，不得进入异常 message）。
    """
    parts: list[str] = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(item) for item in err.get("loc", ()))
        parts.append(f"{loc or '<root>'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts)


# ============================================================
# 公共 API
# ============================================================

def parse_structured_response(raw: str, model: type[T]) -> T:
    """把 LLM 原始字符串输出解析并校验为指定 Pydantic model。

    流程：

        raw str
          ↓ 严格格式提取（bare JSON / 完整 ```json fence）
        json.loads（单个 JSON object）
          ↓
        未知字段拒绝（extra=forbid 语义，对任意 model 强制）
          ↓
        Pydantic 校验
          ↓
        Typed Result（model 实例）

    Args:
        raw:   LLM 原始输出（``chat()`` 返回的 content 字符串；
               ``None`` 视为空输出）。
        model: 目标 Pydantic model 类型（建议继承
               :class:`LLMStructuredModel`）。

    Returns:
        校验通过的 model 实例。

    Raises:
        LLMStructuredOutputError: 解析 / 校验失败
            （``reason`` 见 REASON_* 常量；message 不含原始输出）。
    """
    if raw is None:
        _fail(REASON_EMPTY_OUTPUT, "LLM 输出为空（content=None）")
    if not isinstance(raw, str):
        _fail(
            REASON_UNSUPPORTED_FORMAT,
            f"结构化输出输入类型非法: {type(raw).__name__}",
        )

    text = _extract_json_object_text(raw)

    # ---- 严格 JSON 解析（单 object；多 object / 夹杂文字在此失败）----
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        _fail(REASON_INVALID_JSON, "LLM 输出不是合法的单个 JSON object")

    if not isinstance(data, dict):
        _fail(
            REASON_UNSUPPORTED_FORMAT,
            f"结构化输出必须是 JSON object（got {type(data).__name__}）",
        )

    # ---- 未知字段检查（第二道防线：对任意 model 强制 forbid 语义）----
    known_fields = set(model.model_fields)
    unknown_fields = [key for key in data if key not in known_fields]
    if unknown_fields:
        _fail(
            REASON_SCHEMA_VALIDATION_FAILED,
            f"结构化输出包含未知字段: {sorted(unknown_fields)}",
        )

    # ---- Pydantic 校验 ----
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        _fail(
            REASON_SCHEMA_VALIDATION_FAILED,
            f"结构化输出未通过 schema 校验: {_schema_error_summary(exc)}",
        )
