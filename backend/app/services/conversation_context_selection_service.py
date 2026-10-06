"""Conversation Context Selection Service（Phase 4.2 Step 7E —— Selection 层实现）。

契约来源（**已冻结**，本模块是唯一实现）：

    * Phase 4.2 Step 7C — Context Window & Selection Policy Decision（OD-37 CLOSED）
      `MAX_TURNS = 20` · `MAX_CONTEXT_CHARS = 12000`（content 字符）·
      newest → oldest 选择、恢复 oldest → newest · 连续后缀 · 最新 turn 豁免 ·
      USER-anchored window · EMPTY / FAILED 保留。
    * Phase 4.2 Step 7B — Selection ≠ Formatting（Builder 保持纯格式化）。

职责（**只有一件**）：

```text
ordered history（oldest → newest；由 Repository 保证）
        ↓  排除 current turn（幂等；调用方通常已排除）
        ↓  newest → oldest 逐 turn 累加（整 turn，永不 partial）
        ↓  恢复 oldest → newest
Selected Turns（完整 turn 序列）
```

边界（严格）：

* **纯函数**：deterministic / stateless / 无 IO / 无环境依赖 / 无随机 / 无时间；
* **不排序**（顺序是 Repository 的契约：``ORDER BY created_at ASC, turn_id ASC``）；
* **不格式化**（不做 ``"role: content"`` 渲染 —— 那是 ``ConversationContextBuilder`` 的职责）；
* **不截断**（无 substring / slice / summary）；
* **不读取** ``conversation_id`` / ``assistant_request_id`` / ``created_at`` /
  ``idempotency_key``（只读 ``turn_id``（仅用于排除）/ ``role`` / ``content``）；
* **不接触** DB / Repository / Session / LLM / Embedding / Reranker / tokenizer；
* 单位 = **整个 turn**（不是 pair / message / token）；度量 = ``len(content)``
  （Python Unicode 字符数，**不是** bytes / tokens）。

不在本层（Deferred）：Query Understanding（OD-35）· Context Placement / Prompt 消费（Step 7D 契约，
由后续 implementation step 落地）· 可配置窗口（OD-38）· 观测（OD-39）· Memory / Summary。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Protocol

__all__ = [
    "ConversationContextSelectionService",
    "SelectableTurn",
    "select_history",
    "DEFAULT_HISTORY_TURNS",
    "DEFAULT_HISTORY_CHARS",
    "USER_TURN_ROLE",
    "ASSISTANT_TURN_ROLE",
]

#: 窗口上限（Phase 4.2 Step 7C §17 冻结；**不**引入 env / settings —— OD-38 未决）。
DEFAULT_HISTORY_TURNS: Final[int] = 20

#: 内容字符上限（content 字符，非 token；Step 7C §16.2 冻结）。
DEFAULT_HISTORY_CHARS: Final[int] = 12000

#: Turn role 字面量（= 持久化层 TURN_ROLE_USER / TURN_ROLE_ASSISTANT；
#: 本模块**不 import** ``backend.app.db`` —— 一致性由契约测试锁定）。
USER_TURN_ROLE: Final[str] = "USER"
ASSISTANT_TURN_ROLE: Final[str] = "ASSISTANT"


class SelectableTurn(Protocol):
    """输入契约：Selection **只**读取 ``turn_id`` / ``role`` / ``content``。

    ``turn_id`` 仅用于排除当前 turn（绝不进入 context 内容）。
    """

    @property
    def turn_id(self) -> int: ...

    @property
    def role(self) -> str: ...

    @property
    def content(self) -> str: ...


def _validate_caps(turn_cap: object, char_cap: object) -> tuple[int, int]:
    """校验窗口上限（正整数）；非法 → ``ValueError``（不静默修正）。"""
    for name, value in (("turn_cap", turn_cap), ("char_cap", char_cap)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"{name} 必须是 int（当前: {type(value).__name__}）"
            )
        if value < 1:
            raise ValueError(f"{name} 必须 >= 1（当前: {value}）")
    return int(turn_cap), int(char_cap)


def _validate_turn(turn: object) -> tuple[int, str, int]:
    """校验并抽取（turn_id, role, content 长度）；只读三字段。"""
    turn_id = getattr(turn, "turn_id", None)
    if not isinstance(turn_id, int) or isinstance(turn_id, bool):
        raise ValueError(
            f"turn.turn_id 必须是 int（当前: {type(turn_id).__name__}）"
        )
    role = getattr(turn, "role", None)
    if not isinstance(role, str):
        raise ValueError(f"turn.role 必须是 str（当前: {type(role).__name__}）")
    if role not in (USER_TURN_ROLE, ASSISTANT_TURN_ROLE):
        raise ValueError(
            f"不支持的 turn role: {role!r}"
            f"（允许: {USER_TURN_ROLE} / {ASSISTANT_TURN_ROLE}）"
        )
    content = getattr(turn, "content", None)
    if not isinstance(content, str):
        raise ValueError(
            f"turn.content 必须是 str（当前: {type(content).__name__}）"
        )
    return turn_id, role, len(content)


def select_history(
    turns: Sequence[SelectableTurn],
    *,
    current_turn_id: int | None = None,
    turn_cap: int = DEFAULT_HISTORY_TURNS,
    char_cap: int = DEFAULT_HISTORY_CHARS,
) -> tuple[SelectableTurn, ...]:
    """从有序历史中选择进入 context 的 turn 窗口（Step 7C §16.4 规范实现）。

    Args:
        turns:           历史 turn（**已按** ``created_at ASC, turn_id ASC`` 排序；
                         本函数**不排序**）。允许包含当前 turn（见 ``current_turn_id``）。
        current_turn_id: 当前 USER turn 的 id；非 None 时从候选中排除
                         （调用方通常已排除 ⇒ 本层为**幂等**防御，语义不变）。
        turn_cap:        最多入选 turn 数（默认 20）。
        char_cap:        入选 turn 的 content 字符上限（默认 12000；**最新 turn 豁免**）。

    Returns:
        ``tuple``（保持 oldest → newest）；历史为空 → ``()``。

    Raises:
        ValueError: turn 非 SelectableTurn / role 非法 / cap 非法。

    行为（冻结）：

        1. 排除 ``current_turn_id``；
        2. ``newest → oldest`` 逐 turn 累加（**整 turn**）：
           * 最新候选 turn **豁免**字符上限（永不截断、永不丢弃）；
           * 其余候选仅在 ``count < turn_cap`` 且 ``chars + cost <= char_cap`` 时入选，
             否则**停止**（连续后缀 —— 不跳过、不留空洞）；
        3. 恢复 ``oldest → newest``；
        4. USER-anchored：若首个入选 turn 为 ASSISTANT 且其后仍有 USER turn，
           则丢弃之（可重复）；窗口内已无 USER 时不对齐。
    """
    validated_turn_cap, validated_char_cap = _validate_caps(turn_cap, char_cap)

    candidates: list[SelectableTurn] = []
    for turn in turns:
        turn_id, _role, _length = _validate_turn(turn)
        if current_turn_id is not None and turn_id == current_turn_id:
            continue
        candidates.append(turn)

    selected: list[SelectableTurn] = []
    accumulated = 0
    for index, turn in enumerate(reversed(candidates)):  # newest → oldest
        if len(selected) >= validated_turn_cap:
            break
        _, role, length = _validate_turn(turn)
        if index == 0:  # 最新历史 turn：豁免字符上限（完整保留）
            selected.append(turn)
            accumulated += length
            continue
        if accumulated + length > validated_char_cap:
            break  # 连续后缀：停止（不跳过、不截断）
        selected.append(turn)
        accumulated += length

    selected.reverse()  # 恢复 oldest → newest（Builder 契约要求）

    # USER-anchored window（Step 7C §16.3）：不在窗口内清空 USER 的前提下丢弃前导 ASSISTANT。
    while (
        selected
        and getattr(selected[0], "role", None) == ASSISTANT_TURN_ROLE
        and any(
            getattr(turn, "role", None) == USER_TURN_ROLE for turn in selected[1:]
        )
    ):
        selected.pop(0)

    return tuple(selected)


class ConversationContextSelectionService:
    """无状态包装（便于 DI 注入；行为与 :func:`select_history` 完全一致）。"""

    def select(
        self,
        turns: Sequence[SelectableTurn],
        *,
        current_turn_id: int | None = None,
        turn_cap: int = DEFAULT_HISTORY_TURNS,
        char_cap: int = DEFAULT_HISTORY_CHARS,
    ) -> tuple[SelectableTurn, ...]:
        """见 :func:`select_history`。"""
        return select_history(
            turns,
            current_turn_id=current_turn_id,
            turn_cap=turn_cap,
            char_cap=char_cap,
        )
