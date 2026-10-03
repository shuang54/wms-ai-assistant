# Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（只审计"Context 保留多少历史"的决策归属；不实现）
- 前置：Step 16（Budget Audit）+ Step 17（Selection Contract）
- 状态：

```text
Production Code = 0
SelectionPolicy = Deferred
Budget = Deferred
Tokenizer = Deferred
Selector = Deferred
DB = 0 · Network = 0 · LLM = 0
```

核心问题：

> **未来"Context 应该保留多少历史"这个决策，到底由谁拥有？**

---

## 1. Current State

### 1.1 当前没有任何 History Budget

真实代码核实（AST + 全文搜索）：

```text
Conversation ORM          → 5 列：conversation_id / project_id / created_at / updated_at / status
ConversationRepository    → 无预算概念（只做 CRUD + ORDER BY）
ConversationService       → 5 方法（create / get / archive / append_turn / list_turns）
ChatApplicationService    → 全量 previous turns → Builder（无选择层）
ConversationContextBuilder→ build_context(turns) 只有 turns（无 budget 参数）
AIOrchestrator            → execute(question, *, context: str|None)（无 budget 参数）
```

搜索 `max_turns` / `context_budget` / `selection_policy` / `token_budget`：
**backend/app 零命中**。

`max_tokens` 仅出现在 Text-to-SQL 稳定性评估脚本中，且明确记录为
"not configured; OpenAI-compatible client default"（**未配置**）。

### 1.2 已存在的其它 Segment 预算（如实记录；**不是** History 预算）

项目已有若干 **segment 级字符预算**（不同用途，不可与 Conversation History Budget 混淆）：

```text
services/context_builder.py            DEFAULT_MAX_CONTEXT_CHARS = 12000   # RAG chunk 序列化
services/schema_serializer_service.py  DEFAULT_MAX_CHARS = 12000           # Schema 序列化
services/database_context_composer.py  DEFAULT_CONTEXT_MAX_CHARS = 12000   # Text-to-SQL DB context
services/sql_validator_service.py      DEFAULT_MAX_ROWS                    # SQL 行数限制
config.py ToolSettings                 ToolCallingBudgetExceededError      # Tool round 预算
```

结论：

```text
Conversation History Budget = 不存在（本 Step 不新增）
```

### 1.3 `backend/app/core/` 不存在

任务提示中的 `backend/app/core/` 目录在当前仓库**不存在**（已核实）；
配置集中点为 `backend/app/config.py`（Settings 家族中**无** Conversation/Selection 配置项）。

---

## 2. SelectionPolicy Concept

未来建议存在一个纯数据概念 `SelectionPolicy`（**本 Step 不创建 production DTO**）：

```text
SelectionPolicy 的职责：
    告诉 ContextSelector"允许如何选择历史"
```

未来可能表达（示例，字段未冻结）：

```text
strategy = recent_n            limit = N
strategy = recent_plus_first   recent_n = N
strategy = token_budget        budget = X     ← 依赖 tokenizer（当前 Deferred）
```

性质要求（§12 Validation）：

```text
deterministic · serializable · immutable · validated（纯数据）
禁止：callable / lambda / function / LLM prompt / DB connection / runtime object
```

---

## 3. Policy vs Budget

必须明确区分：

| 概念 | 回答的问题 | 示例 |
| --- | --- | --- |
| SelectionPolicy | **怎么选？** | Recent N / Recent + First / Token Budget |
| Budget | **最多允许多少？** | 未来 max_turns / max_tokens（口径未定） |

```text
SelectionPolicy + Budget
        ↓
Context Selection
```

**不要把两者混成一个"大配置对象"**：

```text
Policy = 选择算法与形状
Budget = 数量上限
```

两者可以独立演进（例如同一 policy 搭配不同预算；或同一预算搭配不同 policy）。

---

## 4. Conversation-level

```text
Conversation → context_budget
```

- 优点：每个会话可以不同（长项目会话 vs 短问答）；
- 缺点：**Conversation 持久化模型开始知道 LLM Context** —— 污染
  ConversationService / ORM / Repository 的纯持久化职责（Step 3 / Step 17 冻结）。

冻结结论（§八）：**Conversation 不应该拥有 Budget**：

```text
Conversation ORM        ✗ context_budget / selection_policy / max_tokens
ConversationRepository  ✗ 任何预算概念
ConversationService     ✗ 任何预算概念
```

除非未来出现**非常明确的产品需求**（届时必须单独发起设计，不在本 Step 预设）。

---

## 5. Project-level

```text
ProjectContext → context policy
```

- 优点：同一业务项目统一策略（WMS 会话通常较长；其它项目可能很短）；
- 缺点：不同模型 / 请求场景可能需要不同预算（需 Request override 或 Model constraint 补充）。

评估：**Project-level 是当前最自然的"默认策略"归属**（业务语义集中、不污染 Conversation 持久化），
作为 §9 推荐方案的组成部分；本 Step 不实现。

---

## 6. Model-level

```text
Model → context window
```

- 优点：接近真实模型能力上限；
- 缺点：

```text
model context window ≠ conversation history budget
```

不能把整个模型窗口都给 Conversation History（见 §10 公式）。
Model 只能作为**上限约束（cap）**，不构成业务 SelectionPolicy。

---

## 7. Global-level

```text
Application Config → context policy
```

- 优点：简单（一处配置）；
- 缺点：所有项目 / 模型 / 场景共用，长期扩展性差。

评估：只适合作为**最后兜底默认值**（在 Project 未定义时生效），不作为主归属。

---

## 8. Request-level

```text
AI Request → SelectionPolicy
```

- 优点：最灵活（例如"导出完整会话回顾"场景临时加大 recent_n）；
- 缺点：

```text
调用方必须知道策略
容易把复杂 Context 策略泄漏到 API
可能被用于放大资源消耗
```

评估：仅作为**内部 override**（Application Layer 显式传入）设计；
**不暴露为 HTTP 参数**（当前 Message API 请求体只有 content，实测锁定）。

---

## 9. Recommended Boundary

推荐优先级（仅候选方案，本 Step 不实现）：

```text
Request explicit policy        （内部显式 override；非 HTTP 字段）
        ↓
Project default policy         （业务默认；推荐主归属）
        ↓
Global default policy          （兜底）
        ↓
Model capability constraint    （上限 cap；不是业务 policy）
```

关键原则：

```text
Model capability 是上限约束，不是 SelectionPolicy 本身。
```

责任归属（冻结）：

```text
ChatApplicationService = policy composition 边界（选择"用哪个 policy"）
ContextSelector（未来） = 执行 policy（只做选择，不含算法配置）
Builder                 = 只格式化
ConversationService     = 只持久化 / 排序
```

---

## 10. Model Window vs History Budget

必须冻结的公式：

```text
Model Context Window
    - System Prompt
    - Project Context
    - Tool Definitions
    - Current User
    - RAG Context
    - Tool Results
    - Safety / Output Reserve
    ↓
Available History Budget
```

因此：

```text
model_context_window ≠ conversation_history_budget
```

未来 Selector 的 effective budget：

```text
effective budget = min(requested, available)
```

（requested = 需求侧；available = 模型侧剩余空间；不能直接把 requested 发送出去。）

本 Step 只冻结概念与公式，**不实现任何计算**（无 tokenizer，无量纲换算）。

---

## 11. Override Concept

默认 + 覆盖：

```text
Project default:
    recent_n = 20

特殊请求（内部）:
    recent_n = 50
```

必须评估的三个问题（记录为开放问题）：

```text
1. API 是否暴露这个参数？          → 当前：否（Message API 只有 content）
2. 普通用户是否可以控制？          → 当前：无 Auth，无法区分"普通用户"
3. 是否可能造成资源消耗？          → 是（更大 history → 更大 context → 更高成本）
```

本 Step 不实现 HTTP 参数；未来若引入，必须先有 Auth / 配额边界。

---

## 12. Validation

未来 SelectionPolicy 必须满足：

```text
deterministic    （同 policy + 同 history → 同 selected）
serializable     （可持久化 / 可日志 / 可比较）
immutable        （frozen；运行期不可被改写）
validated        （构造期校验：非法值在配置阶段拒绝）
```

禁止出现在 policy 中：

```text
callable / lambda / function
LLM prompt
DB connection / Session
任意 runtime object（client / handle / lock）
```

本 Step 不创建 DTO；测试用 Fake dataclass 表达上述性质。

---

## 13. Failure Semantics

必须区分两类失败：

```text
invalid policy（配置阶段）
    例：recent_n = -1 / strategy = unknown
    → 校验错误（ValueError），在进入选择流程之前拒绝

selection failure（运行阶段）
    例：valid policy + history 可用，但 selector 抛异常
    → business failure（异常原样上抛）
```

冻结（延续 Step 17）：

```text
Context Selection failure
        ↓
Business Failure
```

禁止：

```text
✗ fallback to full history
✗ ignore policy（静默按默认执行）
✗ silent degradation
✗ LLM summary retry
```

与 Step 14 一致：selection failure → AI = 0 / USER 保留 / 无 ASSISTANT turn。

---

## 14. Security

SelectionPolicy / Budget 不允许包含：

```text
API key / Authorization / DATABASE_URL / password
SQL / prompt / messages / RAG chunks / Tool arguments / raw LLM response
```

只允许描述：

```text
selection behavior（strategy / recent_n / …）
budget constraints（数量上限语义）
```

即：policy 是"行为描述"而非"执行载体"。

---

## 15. Project Isolation

Policy 不得改变：

```text
conversation.project_id
```

也不能：

```text
request.project_id → override conversation.project_id
```

未来形态：

```text
Conversation.project_id → Project default policy
```

但必须明确：

```text
Project binding ≠ Authorization
（conversation.project_id 是数据一致性边界，不等于授权）
```

当前仍无 Auth（用户身份 / 权限体系未建立），本 Step 不引入。

---

## 16. Deferred Decisions

本 Step **不实现**（全部延期）：

```text
SelectionPolicy（production DTO）
Budget（production 配置 / 字段 / 环境变量）
ContextSelector（production 实现）
Truncation（A / B / C 未选择）
Tokenizer / token counting
Policy 归属落点（Project / Global / Request 的最终组合）
Policy 校验器 / 序列化格式
Request override 的 API 暴露与配额
```

实现前必须回答（开放问题）：

```text
1. Policy 默认值放在哪里（Project 注册表 / config.py / DB）？
2. Budget 口径（条数 / 字符 / token）与是否分段（History vs RAG vs Tool）？
3. Override 是否需要 Auth / 配额前置条件？
4. invalid policy 的用户可见语义（错误码 / 文案）？
5. 与 Model capability cap 的换算方式（未来引入 tokenizer 时）？
```

---

## 附：审计证据（真实代码锚点）

```text
backend/app/db/models/conversation.py          5 列（无预算字段）
backend/app/db/conversation_repository.py      CRUD + ORDER BY（无预算概念）
backend/app/services/conversation_service.py   5 方法（无 policy / budget 参数）
backend/app/services/chat_application_service.py  全量 previous turns → Builder（无选择层）
backend/app/dto/conversation_api.py            ConversationMessageRequest 仅 content
backend/app/config.py                          Settings 家族无 Conversation/Selection 配置
backend/app/core/                              **不存在**（已核实）
```

测试：

```text
tests/test_conversation_context_policy_audit.py
    → Policy ≠ Budget / ownership（ORM·Service·AppService）/ request override 边界 /
      invalid policy ≠ selection failure / failure=business failure /
      deterministic·serializable·immutable / security 字段白名单 / 文档完整性
tests/test_conversation_context_policy_architecture.py
    → AST：Conversation ORM（5 列冻结）/ Turn ORM（6 列冻结）/
      Repository·Service（无 LLM·tokenizer·Builder·Selector）/
      AppService（允许依赖 + 当前无 policy·selector）/ 无 production policy 模块
```
