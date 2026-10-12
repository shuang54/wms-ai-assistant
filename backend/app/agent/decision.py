"""AgentDecision + 严格解析（Phase 5 Decision §5.3 / 任务书 §九–§十一）。

LLM 决策协议（本阶段**不使用** provider function calling；Provider contract 不变）：

```text
LLM output（纯文本）
        ↓  strict structured JSON parsing（json.loads；无修复 / 无正则 / 无猜测）
AgentDecision
        ├── FINAL      { "type": "FINAL", "content": "<最终回答>" }
        └── TOOL_CALL  { "type": "TOOL_CALL", "tool_name": "...",
                         "arguments": { ... }, "tool_call_id": "<可选>" }
```

严格性（**任何**违反 ⇒ :class:`AgentDecisionParseError` ⇒ Run = FAILED）：

    * 非 JSON / 多个 JSON 文档 / 前后夹杂自然语言；
    * 顶层不是 object；
    * 缺 ``type`` / ``type`` 不是 ``FINAL`` / ``TOOL_CALL``（大小写敏感）；
    * ``FINAL`` 缺 ``content`` 或 content 为空白；
    * ``TOOL_CALL`` 缺 ``tool_name`` / ``arguments``，或类型不符。

明确**容忍**（不构成错误）：JSON object 中的**未知字段**（例如 LLM 伪造的
``{"max_steps": 999999}``）—— 它们只是被忽略的数据，**永远不能**修改
RuntimePolicy / 授权 / 安全边界。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Mapping

from backend.app.agent.errors import AgentDecisionParseError

__all__ = [
    "AgentDecision",
    "parse_agent_decision",
    "DECISION_FINAL",
    "DECISION_TOOL_CALL",
]

#: 合法决策类型（大小写敏感；其他值一律非法）。
DECISION_FINAL: Final[str] = "FINAL"
DECISION_TOOL_CALL: Final[str] = "TOOL_CALL"

_LEGAL_TYPES: Final[frozenset[str]] = frozenset({DECISION_FINAL, DECISION_TOOL_CALL})


def _reject_json_constant(value: str) -> Any:
    """拒绝 JSON 中的 ``NaN`` / ``Infinity`` / ``-Infinity``（非标准 JSON）。"""
    raise ValueError(f"不支持的 JSON 常量: {value}")


@dataclass(frozen=True)
class AgentDecision:
    """LLM 决策（不可变；构造即校验）。

    Attributes:
        type:      ``FINAL`` 或 ``TOOL_CALL``。
        content:   仅 FINAL：最终回答（非空）。
        tool_name: 仅 TOOL_CALL：Tool 名称（非空）。
        arguments: 仅 TOOL_CALL：参数（JSON object）。
        tool_call_id: 可选关联 ID（LLM 提供时原样保留；不生成 / 不替换）。

    Raises:
        AgentDecisionParseError: 字段与类型不匹配 / 缺失 / 类型非法。
    """

    type: str
    content: str | None = None
    tool_name: str | None = None
    arguments: Mapping[str, Any] | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if self.type not in _LEGAL_TYPES:
            raise AgentDecisionParseError(
                f"非法 decision type: {self.type!r}"
                "（仅允许 'FINAL' / 'TOOL_CALL'）"
            )
        if self.tool_call_id is not None and (
            not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip()
        ):
            raise AgentDecisionParseError("tool_call_id 必须为非空 str 或 None")

        if self.type == DECISION_FINAL:
            if not isinstance(self.content, str) or not self.content.strip():
                raise AgentDecisionParseError(
                    "FINAL decision 必须携带非空 content"
                )
            if self.tool_name is not None or self.arguments is not None:
                raise AgentDecisionParseError(
                    "FINAL decision 不得携带 tool_name / arguments"
                )
            return

        # TOOL_CALL
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise AgentDecisionParseError(
                "TOOL_CALL decision 必须携带非空 tool_name"
            )
        if not isinstance(self.arguments, Mapping):
            raise AgentDecisionParseError(
                "TOOL_CALL decision 必须携带 arguments（JSON object）"
            )
        if self.content is not None:
            raise AgentDecisionParseError(
                "TOOL_CALL decision 不得携带 content"
            )


def parse_agent_decision(raw: str) -> AgentDecision:
    """把 LLM 文本输出严格解析为 :class:`AgentDecision`。

    Args:
        raw: LLM 原始输出（必须**恰好**是一个 JSON object）。

    Returns:
        ``AgentDecision``（FINAL / TOOL_CALL）。

    Raises:
        AgentDecisionParseError: 任何不符合协议的情形（**不修复 / 不猜测 / 不重试**）。

    说明：错误消息**不包含**原始输出（可能含敏感内容 / 注入文本）。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise AgentDecisionParseError("LLM 输出必须为非空 str（JSON object）")

    try:
        payload = json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, TypeError) as exc:
        raise AgentDecisionParseError(
            "LLM 输出不是合法 JSON object（禁止自然语言 / Markdown 包裹）"
        ) from exc

    if not isinstance(payload, dict):
        raise AgentDecisionParseError("LLM 输出顶层必须是 JSON object")

    if "type" not in payload:
        raise AgentDecisionParseError("LLM 输出缺少 'type' 字段")

    decision_type = payload["type"]
    if not isinstance(decision_type, str) or decision_type not in _LEGAL_TYPES:
        raise AgentDecisionParseError(
            f"非法 decision type: {decision_type!r}"
            "（仅允许 'FINAL' / 'TOOL_CALL'）"
        )

    tool_call_id = payload.get("tool_call_id")

    if decision_type == DECISION_FINAL:
        # 跨类型字段（FINAL 携带 tool_name / arguments）⇒ 决策自相矛盾 ⇒ 拒绝
        if "tool_name" in payload or "arguments" in payload:
            raise AgentDecisionParseError(
                "FINAL decision 不得携带 tool_name / arguments"
            )
        return AgentDecision(
            type=DECISION_FINAL,
            content=payload.get("content"),
            tool_call_id=tool_call_id,
        )

    # 跨类型字段（TOOL_CALL 携带 content）⇒ 拒绝（不猜测意图）
    if "content" in payload:
        raise AgentDecisionParseError(
            "TOOL_CALL decision 不得携带 content"
        )
    return AgentDecision(
        type=DECISION_TOOL_CALL,
        tool_name=payload.get("tool_name"),
        arguments=payload.get("arguments"),
        tool_call_id=tool_call_id,
    )
