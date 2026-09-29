"""Assistant Outcome Contract（Phase 3.12 Step 63）——最小生产实现。

设计来源：Phase 3.12 Step 61（Outcome 边界审计，结论 OUTCOME_BOUNDARY_MISSING）
         + Step 62（Outcome Contract 设计审计，结论 = Option A 最小方案）。

```text
Assistant Outcome（**4 态固定**，不得扩展）
    ├── SUCCESS   请求已完成并产生有效业务响应
    ├── EMPTY     请求正常完成，但没有可提供的业务结果（RAG 空检索）
    ├── REFUSED   系统明确拒绝执行（预期的安全行为）
    └── FAILED    请求无法正常完成（且非预期拒绝）
```

**Outcome ≠ 其它任何状态**：

```text
Outcome ≠ HTTP status      （Tool 业务失败 = HTTP 200 + FAILED）
Outcome ≠ LLM status       （2 次 LLM 调用（含重试）仍可能 SUCCESS）
Outcome ≠ Tool status      （Tool 成功只是底层执行状态）
Outcome ≠ RAG status       （RAG 执行完成 ≠ 请求成功）
Outcome ≠ SQL execution status
```

判定责任（Step 62 §7）：

```text
AIOrchestratorService（Assistant 层）**唯一**决定 outcome；
RagService / ToolExecutionService / TextToSQLService / LLMProvider **不**产生 outcome
（它们只提供底层执行事实）。
```

判定输入白名单（**不得扩大**）：

```text
route · refused · tool_success · rag_used_chunks
（+ 调用方自身的异常/失败状态；异常路径不产生 Result → 由 HTTP 层表达，见 §limitations）

禁止：exception.message · prompt · messages · SQL · RAG chunk 正文 ·
      Tool arguments · ToolResult.data · API key / Authorization / DATABASE_URL ·
      stack trace · raw provider response
特别禁止：**用 content 文本匹配推断 outcome**（例如 "失败" in content）。
```

优先级（Step 62 §4）：

```text
REFUSED  >  FAILED  >  EMPTY  >  SUCCESS
```

本阶段**不实现**：error_class / Trace Outcome persistence / 新 HTTP 字段
（对外表示 = ``/api/ai/chat`` 的 ``metadata.outcome``，envelope 不变）。
"""
from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AssistantOutcome",
    "determine_assistant_outcome",
]


class AssistantOutcome(StrEnum):
    """Assistant-level 业务结果（4 态固定；str 子类 → JSON 直接输出字符串）。

    * **stable**：枚举值即对外契约（``"SUCCESS"`` / ``"EMPTY"`` / ``"REFUSED"`` / ``"FAILED"``）；
    * **small**：仅 4 个成员，不引入 UNKNOWN / PARTIAL / TIMEOUT / CANCELLED / RETRYING；
    * **serializable**：``StrEnum`` ⇒ FastAPI / pydantic 序列化为普通字符串。
    """

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


def _is_zero(value: object) -> bool:
    """``== 0`` 但**排除 bool**（``False == 0`` 会误判）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def determine_assistant_outcome(
    *,
    route: str,
    refused: bool = False,
    tool_success: bool | None = None,
    rag_used_chunks: int | None = None,
) -> AssistantOutcome:
    """由**业务信号**判定 Assistant Outcome（纯函数；无 IO / 无时间 / 无随机）。

    优先级：``REFUSED > FAILED > EMPTY > SUCCESS``（Step 62 §4）。

    Args:
        route: 实际路由（``"rag"`` / ``"tool"`` / ``"text_to_sql"``）。
        refused: 是否为首类拒绝（仅 Text-to-SQL refusal 路径为 ``True``）。
        tool_success: Tool 业务结果布尔；``False`` ⇒ FAILED（**即便 HTTP 200**）；
            ``None`` = 非 Tool 路径 / 无该信号（不判定）。
        rag_used_chunks: RAG 命中的 chunk 数；``0`` ⇒ EMPTY（**仅 RAG 路径**）；
            ``None`` = 无该信号（不判定）。

    Returns:
        ``AssistantOutcome``（四态之一）。

    Note:
        * **不读取** content / data / prompt / SQL / chunk 正文 / 异常消息；
        * Tool 失败（HTTP 200 + ``tool_success=False``）⇒ ``FAILED``：
          HTTP 200 不代表业务成功；
        * Text-to-SQL ``row_count == 0`` **不**判定 EMPTY（查询已给出"无匹配数据"
          这一有效业务结果）—— 本函数**不接受** row_count 参数，从接口层面杜绝误用。
    """
    # 1) REFUSED：业务意图信号（预期安全行为）优先于一切失败/空结果信号
    if refused is True:
        return AssistantOutcome.REFUSED
    # 2) FAILED：明确的底层业务失败（即使传输层是 200）
    if tool_success is False:
        return AssistantOutcome.FAILED
    # 3) EMPTY：仅 RAG 空检索（LLM 未被调用，没有可提供的业务结果）
    if route == "rag" and _is_zero(rag_used_chunks):
        return AssistantOutcome.EMPTY
    # 4) SUCCESS：其余正常完成（含 T2SQL row_count=0）
    return AssistantOutcome.SUCCESS
