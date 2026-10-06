# Phase 4.2 Step 2 — AI Result → Assistant Turn Semantics

> **ONLY CONTRACT ANALYSIS**：未修改 ChatApplicationService / AIOrchestrationResult /
> ConversationTurn / Repository / DB / API。
> 目标：OD-16（AI Result 持久化语义）+ OD-19（AI 失败 / 拒答语义）。

---

## 1. Scope

定义 `AIOrchestrationResult → ConversationTurn(ASSISTANT)` 的边界：
何时创建、content 来源、EMPTY / REFUSED / FAILED 分别如何处理、request_id 语义、
API 行为、与幂等完成语义的关系、给 Step 3 的输入语义。

---

## 2. Current Runtime Behavior（真实代码）

```text
User Message → USER Turn（TX1 commit）→ AIOrchestrator.execute()
                                              ↓
                                     AIOrchestrationResult
                                              ↓
                    _assistant_content(result)        ← content.strip() 非空？
                    _assistant_request_id(result)     ← metadata["request_id"] 合法？
                                              ↓
                    两者均满足 → ASSISTANT Turn（TX2）     CURRENT
                    任一不满足 → 不创建，仍返回原 result   CURRENT（记 info/warning）
                    Orchestrator 抛异常 → 异常原样上抛     CURRENT（不包装 / 不 retry）
```

判定函数（真实）：

```python
def _assistant_content(result):      # content 为 str 且 .strip() 非空 → 返回；否则 None
def _assistant_request_id(result):   # metadata["request_id"] 为 str 且非空 → 返回；否则 None
```

→ **当前不读取 `metadata.outcome` 决定是否创建 Turn**（只看 content + request_id）。

---

## 3. AIOrchestrationResult Contract（不改）

```text
route      : RouteType（rag / tool / text_to_sql）
content    : str | None（用户可见自然语言 / 摘要；RAG 空结果可为 None）
data       : Any（结构化数据：ToolResult / SQLExecutionResult / RagResponse）
metadata   : Mapping（含 request_id、outcome；不含凭据）
```

可靠判定"是否可持久化为 ASSISTANT Turn"的字段：

* **`content`（strip 后非空）** —— 唯一"用户可见内容"判据；
* **`metadata["request_id"]`（合法）** —— Trace 关联必备；
* `route` / `data` / `outcome` **不用于**创建判定（当前实现）。

---

## 4. Assistant Turn Meaning

采用 **Option A**：ASSISTANT Turn = **用户最终看到的 assistant message**。

证据：

* 异常路径（AI 未产生可展示结果）**不创建** Turn → 它不是"AI 执行记录"；
* content 为空时不创建、且明确"**不伪造** `[EMPTY]`" → 它只承载可见内容；
* 拒答（有 content）会创建 → 与"用户应看到拒答"一致。

→ 不是 Option B（执行记录），也不是 Option C（执行 + 可见，因执行状态未落库）。

---

## 5. SUCCESS

```text
content 非空 + request_id 合法 → ASSISTANT Turn（content = AIOrchestrationResult.content）
```

* ASSISTANT.content **来自 `result.content`**，不是 `data`；
* `data` / `route` / `metadata` **不进** ConversationTurn（当前无对应列）；
* content 非空 ⇒ **必然创建**（只要 request_id 合法）—— 当前实现的确定答案。

---

## 6. EMPTY

| Case | 当前行为 |
| ---- | -------- |
| A：data 非空 + content 空 | **不创建** ASSISTANT Turn（data 不入库） |
| B：data 空 + content 空 | **不创建** |
| C：content = whitespace | 视为 EMPTY（`strip()` 后为空）→ **不创建** |

定义（derived classification，非 runtime enum）：

```text
EMPTY = AI 正常执行完成，但无可展示内容（content 为 None / 空 / 纯空白）
```

已知缺口：**`data` 非空但 content 空时，数据对用户不可见且未持久化** → 记录为 gap（不实现）。

---

## 7. REFUSED

Phase 3 既有：Text-to-SQL 只读拒绝 → `metadata.refused=True`，返回拒绝说明文本。

```text
REFUSED + content 非空（拒绝说明）+ request_id 合法 → 创建 ASSISTANT Turn（正常保存 content）
REFUSED + content 空 → 不创建
```

* 拒答属于**用户应看到**的内容 → 与 SUCCESS 同路径持久化；
* 当前 `ConversationTurn` **无 status 字段** → 不新增字段区分 REFUSED；
  拒答在 runtime 侧由 `metadata.refused` / `outcome=REFUSED` 表达（不进 Turn）。

---

## 8. FAILED

区分两种：

| 类型 | 行为（真实） |
| ---- | ------------ |
| FAILED（outcome，AI 返回但业务失败） | 仍按 content + request_id 判定；content 空 → 不创建 |
| FAILED（Orchestrator **抛异常**：LLM timeout / provider error / RAG / Tool / SQL 异常） | **不创建** ASSISTANT Turn；USER Turn **保留**；异常原样上抛 |

→ **不在持久化层伪造错误消息**（如"系统暂时无法处理"）：
当前无正式 error response contract，本 Step 不新增；错误语义由 API 层统一处理（§12）。

---

## 9. request_id Semantics

```text
ASSISTANT Turn.assistant_request_id = AIOrchestrationResult.metadata["request_id"]
```

* 继承 Phase 4.1 冻结：**CORRELATION ONLY**（非 FK、非 Evidence identity、非幂等键）；
* 缺失 / 非法 → **不创建** ASSISTANT Turn，并记 warning；
* **禁止**在持久化层生成 / 伪造 / 随机化 request_id（只能来自 AI Runtime）。

---

## 10. Result State Matrix（真实行为 + 契约）

| Result State | AI 执行 | content | request_id | ASSISTANT Turn |
| ------------ | ------- | ------- | ---------- | -------------- |
| SUCCESS | 成功 | 非空 | 合法 | **YES** |
| SUCCESS | 成功 | 空 / 空白 | 合法 | **NO（EMPTY 路径）** |
| EMPTY | 成功 | 空 | 合法 | **NO** |
| EMPTY | 成功 | 非空（异常组合） | 合法 | YES（按 content 判定） |
| REFUSED | 成功 | 非空（拒绝说明） | 合法 | **YES** |
| REFUSED | 成功 | 空 | 合法 | **NO** |
| FAILED（outcome） | 返回失败 | 依 content | 合法 | 依 content（空 → NO） |
| FAILED（异常） | 抛异常 | 无 | 无 | **NO**（USER 保留，异常上抛） |
| 任意 | — | 非空 | **缺失** | **NO**（不伪造 ID） |

---

## 11. Idempotency Completion Semantics（与 Step 1A 的关系）

Step 1A 定义：`completed = USER turn 存在 + ASSISTANT turn 存在`。

本 Step 校验：

```text
SUCCESS + ASSISTANT      → completed（可 duplicate replay）✓
REFUSED + ASSISTANT      → completed（可 duplicate replay）✓
FAILED（异常）+ 无 ASSISTANT → not completed（允许 retry）✓
EMPTY + 无 ASSISTANT     → **冲突**： EMPTY 是合法终态，但按 Step 1A 判定为
                             not completed → 同 key 重试会再次执行 AI
```

→ 新增 **OD-26（EMPTY 的 completion 语义）= OPEN**：
EMPTY 是否应视为 completed（避免重复执行 AI），需 Step 1A 与 Step 2 联合决策。

---

## 12. API Behavior（现有，不改）

| 场景 | HTTP | 持久化 |
| ---- | ---- | ------ |
| SUCCESS | **200** | USER + ASSISTANT |
| EMPTY（AI 返回空） | **200**（outcome ≠ HTTP 状态） | USER only |
| REFUSED | **200** | USER + ASSISTANT（拒答可见） |
| FAILED（outcome） | **200** | 依 content |
| 异常（AI / Repository） | **500**（不暴露 traceback / SQL / prompt / 凭据） | USER only |
| Conversation 不存在 | 404 | — |
| Conversation 已归档 | 409（写入前拒绝） | — |
| 参数非法 | 422 | — |

API 注释原文：`Outcome（SUCCESS / EMPTY / REFUSED / FAILED）不是 HTTP 状态`。

---

## 13. Evidence Boundary（不实现）

* ASSISTANT Turn **不含** Evidence identity / provenance；
* 正式顺序（Step 3 输入）：

```text
AI Result → Assistant Turn（用户可见内容）
AI Result → Evidence Builder（独立评估产物）     ← Step 3 定义
```

→ **不是** `AI Result → Evidence → Assistant Turn`；两者从 AI Result 分叉，互不依赖。

---

## 14. Step 3 Input Contract（给 Evidence Builder 的输入语义）

Step 2 输出给 Step 3 的确定语义：

```text
1) AI Result 是否"可展示"由 content.strip() 决定（与 Turn 创建同一判据）
2) ASSISTANT Turn 只承载 content + request_id（不含 route / data / metadata）
3) EMPTY / REFUSED / FAILED 是 AI Result 的分类，不影响 Turn 之外的领域对象
4) request_id 仅作 correlation，不得进入 Evidence
5) Evidence 与 Assistant Turn 是 AI Result 的两个独立下游
```

---

## 15. Test Matrix（设计，不实现）

| ID | 场景 | 期望 |
| -- | ---- | ---- |
| T1 | SUCCESS + content | ASSISTANT turn 存在 |
| T2 | SUCCESS + empty content | 无 ASSISTANT turn（不伪造） |
| T3 | REFUSED + content（拒答） | ASSISTANT turn 存在（拒答可见） |
| T4 | REFUSED + empty | 无 ASSISTANT turn |
| T5 | FAILED（异常） | 无 ASSISTANT turn；USER turn 保留 |
| T6 | request_id 合法 | `assistant_request_id` 持久化 = metadata request_id |
| T7 | request_id 缺失 | 不创建 turn，**不伪造** ID |
| T8 | completed duplicate | 与 Step 1A replay 一致 |
| T9 | failed + retry | 允许重试（无 ASSISTANT turn） |
| T10 | ASSISTANT Turn 内容 | 不含 Evidence identity / provenance |

复用既有 `FakeOrchestrator`（Step 15 / Step 46）：可构造 SUCCESS / EMPTY / 异常；
**REFUSED 需 `metadata.refused=True` + content** —— 现有 Fake 可设置 metadata，
但未提供开箱即用的 refusal fixture → 记录为 **Test Infrastructure Gap**（不修改 Fake）。

---

## 16. OD-16 Decision

```text
OD-16 = CLOSED
```

依据（全部由真实代码确定）：创建规则（content.strip() + request_id 合法）·
SUCCESS 语义 · EMPTY 语义 · REFUSED 语义 · request_id 语义 · API 行为。

---

## 17. OD-19 Decision

```text
OD-19 = CLOSED
```

依据：REFUSED（AI 正常执行 + 业务拒绝，content 可见 → 建 Turn）与
FAILED（异常 → 无 Turn、USER 保留、500、不暴露细节）**严格区分**；
不新增 error message 持久化，不新增 Turn status 字段。

---

## 18. Open Questions

| ID | 问题 | Status |
| -- | ---- | ------ |
| OD-26 | EMPTY（无 ASSISTANT turn）是否视为 idempotency completed | **OPEN**（影响 Step 1A） |
| — | `data` 非空但 content 空时是否需持久化/展示 | **OPEN（gap，不实现）** |
| — | FakeOrchestrator 缺少开箱即用的 refusal fixture | **OPEN（test infra gap，不改 Fake）** |
