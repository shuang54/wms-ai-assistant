# Phase 4.2 Step 7E — Context Selection Implementation

> 性质：**实现阶段**（把 Step 7C 冻结的 Context Window / Selection Contract 落地）。
> 本 Step 修改范围严格限定在 Selection 层 + ChatApplicationService 接线 + 对应测试/登记：

```text
backend : conversation_context_selection_service.py（新增）· chat_application_service.py（接线）
tests   : test_conversation_context_selection.py（新增）· 5 个登记/白名单断言同步
docs    : 本文档 · Roadmap §27（append-only）
不变    : Router · RAG · Text-to-SQL · Tool · Prompt · SQL Validator · SQL Executor ·
          ConversationContextBuilder · ConversationRepository · ConversationService ·
          Step 6 幂等契约 · DB schema · API · 环境变量
```

**本阶段明确：Context Selection implemented（已实现）；Prompt Consumption NOT implemented（未实现）。**

---

## 1. Scope

```text
IN  : Selection 层（窗口选择）实现 + 接入 ChatApplicationService 的
      history → selection → builder 链路 + 回归测试（T-7C-1 … T-7C-12）。
OUT : Router / RAG / Text-to-SQL / Tool 的 Context 消费（Step 7D 契约的落地）·
      Query Understanding（OD-35）· 可配置窗口（OD-38）· 观测（OD-39）·
      Memory / Summary / Embedding / Reranker / tokenizer · DB / API / Prompt 变更 · commit。
```

---

## 2. Selection Architecture

```text
ConversationRepository.list_turns_by_conversation_id()      ORDER BY created_at ASC, turn_id ASC
        ↓
ChatApplicationService：排除 current turn（按 turn_id；既有行为不变）
        ↓
select_history(previous_turns, current_turn_id=…)            ← 【本 Step 新增：Selection 层】
        ↓
ConversationContextBuilder.build_context(selected_turns)      ← 纯格式化（未修改）
        ↓
AIOrchestrator.execute(question, context=context)             （未修改）
```

* 新模块：`backend/app/services/conversation_context_selection_service.py`（纯函数 + 无状态类包装）；
* 顺序严格为 **Exclude → Order（Repository 负责）→ Select → Format**；
* Selection **不排序**（顺序来源 = Repository）、**不格式化**（Step 7B 冻结：Selection ≠ Formatting）。

---

## 3. Algorithm（Step 7C §16.4 的逐条实现）

```python
select_history(turns, *, current_turn_id=None,
               turn_cap=DEFAULT_HISTORY_TURNS, char_cap=DEFAULT_HISTORY_CHARS)

1) 校验：cap 为正整数；每个 turn 的 turn_id/role/content 合法（未知 role → ValueError）
2) 排除 current_turn_id（幂等：不存在则无变化）
3) newest → oldest 逐 turn 累加（**整 turn**）：
      * 最新候选 turn → **豁免** char_cap（直接入选、完整保留）
      * 其余候选 → 仅当 count < turn_cap 且 chars + len(content) <= char_cap 时入选
      * 否则 **break**（连续后缀：不跳过、不留空洞）
4) reverse → oldest → newest
5) USER-anchor：若首个入选 turn 为 ASSISTANT 且其后仍有 USER turn ⇒ 丢弃之（可重复）；
   若窗口内已无 USER ⇒ 不对齐（保持原样）
```

只读字段：`turn_id`（仅用于排除）· `role` · `content`；**不读** `conversation_id` /
`assistant_request_id` / `created_at` / `idempotency_key`（由 `_PoisonedTurn` 测试锁定）。

---

## 4. Current Turn Exclusion

```text
ChatApplicationService：previous_turns = history 排除 user_turn.turn_id（既有 Step 6 行为）
Selection           ：select_history(..., current_turn_id=user_turn.turn_id)（**幂等**二次防御）
```

结果：current USER Turn（含 retry 复用行）**永不进入 context**（T-7C-1 / T-7C-2）。

---

## 5. Retry

retry 复用既有 USER Turn（Step 6）⇒ 该 `turn_id` 在 Selection 前已排除：历史 `[U1, A1]` +
当前（复用的）`U2` ⇒ context = `"user: U1\nassistant: A1"`；不出现 `U2`，也不出现 `U2, U2`。

---

## 6. Duplicate

completed duplicate 在 `_resolve_user_turn` 内短路返回 `MessageReplay` ⇒
**不调用** `select_history` ⇒ 不读 history、不建 context、不调 AI（T-7C-3 由 spy 断言：
`calls == []`、`orchestrator.calls == []`）。Step 6 行为未改变。

---

## 7. Window Limits

```text
DEFAULT_HISTORY_TURNS = 20        （单位 = turn；不是 pair / message）
DEFAULT_HISTORY_CHARS = 12000     （口径 = content 字符 = len(turn.content)，Unicode 字符）
```

* 常量定义在 Selection 模块内（**不新增** env / settings —— OD-38 未决）；
* 两个上限互补：短消息由 turn 数收敛、长消息由字符数收敛；
* 格式化长度上界仍可计算：`formatted ≤ content_chars + 12·k − 1`（k = 入选 turn 数）。

---

## 8. Oversized Message

| 情形 | 行为 |
| ---- | ---- |
| **最新**历史 turn `len(content) > char_cap` | **完整保留**（不截断 / 不切片 / 不摘要）⇒ 允许 `content_chars > 12000` |
| **更旧** turn 超限 | **停止选择**（该 turn 及其更旧者全部排除）⇒ 语义停顿而非空洞 |

实测（离线 + DB）：`u * 13000` 作为最新历史 turn ⇒ context = `"user: " + 13000 chars`（> 12000，原样）。

---

## 9. EMPTY / FAILED

```text
EMPTY（无 ASSISTANT 的 USER Turn） ⇒ 保留（内容原样）
FAILED（异常后被保留的 USER Turn） ⇒ 保留（内容原样）
```

* DB 无 execution status ⇒ Selection **不尝试识别**状态，也不新增任何状态字段；
* context 中只出现 `user: <原始内容>` / `assistant: <原始内容>`；异常文本 / `request_id` /
  凭据等内部信息**不进入** context（测试用 `boom sk-secret postgresql://…` 断言不可见）。

---

## 10. Builder Boundary

`ConversationContextBuilder` **未修改**（git diff = 0）：

```text
Builder 仍只做：Selected Turns → "role: content" 行（纯函数 / 不排序 / 不裁剪）
Selection 承担：DB 读取之后的窗口选择（本 Step）
```

Selection 不渲染、Builder 不选择 —— 分层与 Step 7B contract #10 一致。

---

## 11. Tests

新增 `tests/test_conversation_context_selection.py`（**41 passed**：38 离线 + 3 DB-gated）：

| 契约 | 覆盖 |
| ---- | ---- |
| T-7C-1 | current turn 排除（函数级 + pipeline 级；含 25 turns 大窗口） |
| T-7C-2 | retry 复用行不进入 context（`U1/A1` 不变、无重复） |
| T-7C-3 | duplicate ⇒ `select_history` 调用 = 0（spy）+ AI = 0 |
| T-7C-4 | Selection 不重排；同 `created_at` 保持输入顺序（Repository 契约）；DB 实测 `m7..m25` 精确序列 |
| T-7C-5 | 同输入两次调用 ⇒ selected / context 完全一致 |
| T-7C-6 | 20 turns 全容纳；21 turns ⇒ 最新窗口；`12000` 字符可容纳 |
| T-7C-7 | EMPTY（无 ASSISTANT 的 USER）保留 |
| T-7C-8 | FAILED 后 USER 保留且 context 无 `FAILED/exception/sk-secret/postgresql` |
| T-7C-9 | 最新超长 turn 完整保留（`13000` 字符原样）；更旧超限 ⇒ 停止（连续后缀、无空洞） |
| T-7C-10 | 内部标识符（conversation_id / request_id / turn_id / created_at / idempotency_key）不入 context |
| T-7C-11 | USER-anchor：丢弃前导 ASSISTANT；无 USER 时不对齐 |
| T-7C-12 | 空历史 ⇒ context = `None` |

附加用例：精确 12000 / 12001 · 精确 20 / 21 turns · 混合 role 顺序 · 同时间戳 · Unicode（emoji 6000 = 12000
字节但 6000 字符）· 空 content `""` 保留 · 输入校验（cap 非法 / 未知 role）· `_PoisonedTurn`（只读三字段）·
原样不改写（含前导/尾随空白与换行）· DB 无残留（Selection 自身不写行）。

**同步更新的登记型断言**（Selection 相关，非行为改动）：

```text
tests/test_conversation_model_contract.py          ALLOWED_CONVERSATION_MODULES / _CLASS_FILES +新模块
tests/test_conversation_architecture_contract.py   _DECLARED_CONVERSATION_MODULES +新模块
tests/test_conversation_persistence_contract.py    ALLOWED_PRODUCTION_MODULES +新模块
tests/test_chat_application_service_architecture_audit.py  import 白名单 +新模块
tests/test_conversation_context_{budget,policy}_architecture.py
tests/test_conversation_context_selection_{architecture,strategy_architecture}.py  应用层依赖白名单 +新模块
```

---

## 12. Security

```text
I/O   ：无 DB 写 / 无 LLM / 无网络 / 无 embedding / 无 reranker / 无 tokenizer（纯函数）
内容  ：只读 role/content（+ turn_id 用于排除）；不读 idempotency_key / request_id / conversation_id
上下文：Selection 只决定"给模型看哪些历史"，不参与"允许做什么"（安全由 Validator / capability 保证）
```

---

## 13. Step 6 Regression

```text
tests/test_conversation_message_idempotency.py + tests/test_conversation_message_idempotency_db.py
（幂等 / duplicate replay / retry / conflict / concurrency）全部通过（见 §15 结果）。
Selection 不读 idempotency_key ⇒ idempotency_key 永不进入 context / Prompt（Step 6 边界保持）。
```

---

## 14. Step 7D Regression

```text
Router / RAG / Text-to-SQL / Tool / Prompt 文件 diff = 0 ⇒ 放置契约（OD-36）未被触碰，
Prompt Consumption 仍未实现（由后续 implementation step 落地 T-7D-1…8）。
```

---

## 15. 执行结果（本 Step 实测）

```text
tests/test_conversation_context_selection.py            : 41 passed（离线 38 + DB 3）
tests/test_conversation_context_builder.py              : 通过（未修改）
tests/test_conversation_message_idempotency.py (+DB)     : 通过（未修改）
全量离线（python -m pytest -q）                          : 6004 passed · 5 failed
全量 DB（RUN_DB_TESTS=1）                                : 6702 passed · 6 failed
compileall（backend tests scripts）                      : 0 errors
```

失败项与功能无关（**Expected until commit**）：`test_11_backend_working_tree_is_unmodified`
（backend 未提交）+ 其下游离线 suite / Matrix baseline 门（4 项）+ `test_db_residue_is_zero`
（顺序/残留 baseline，单独运行通过）。未修改 guard / baseline。

---

## 16. Limitations

```text
1) 窗口上限是**常量**（20 / 12000）——可配置性属 OD-38（本 Step 不实现）。
2) USER-anchor 的可见后果：当"最旧入选 turn"为 ASSISTANT 时会被丢弃 ⇒ 实际窗口可能为
   19 turns（≤ 20，符合契约）；测试已按此语义锁定。
3) EMPTY / FAILED 不可区分（无 execution status，且明确不新增）⇒ 一律保留 USER 文本。
4) Token 计量仍缺席（字符口径）；跨模型 token 预算需 OD-35/未来决策。
5) Prompt Consumption 仍未实现：Selection 已可独立产出 Selected Turns，
   但 Router / RAG / Text-to-SQL 尚未消费 context（Step 7D 契约待落地）。
6) 对话层 context 观测缺席（OD-39）。
```

**Context Selection implemented. Prompt Consumption NOT implemented.**

---

## 17. STOP

下一步（**不在本 Step**）：Step 7D 放置契约落地（T-7D-1…8）→ Prompt Consumption 接线。
未进入 Implement Consumption · 未实现 Query Understanding / Memory / Summary / OD-38 / OD-39 · 未 commit。
