# Phase 4.1 Step 2 — Conversation Data Model Audit

> 状态：**Design / Freeze Only**（不创建表 / 不创建 ORM / 不创建 API）
> 范围：Conversation / ConversationTurn 最小数据模型设计与 Contract Freeze
> 产物：本文件 + `tests/test_conversation_model_contract.py`（纯离线）
> 纪律：**DB = unchanged · API = unchanged · Orchestrator = unchanged ·
> RAG = unchanged · Tool = unchanged · LLM = unchanged**
> 本 Step 运行指标：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0
>
> 前置：Phase 4.1 Step 1（Conversation 架构审计）已确认：
>
> ```text
> Conversation → Assistant Request × N
> conversation_id ≠ assistant_request_id ≠ LLM provider request_id
> ```
>
> 本 Step 唯一的产出是**冻结** Conversation / ConversationTurn 的最小模型，
> 并把"字段职责 / 生命周期 / 删除归档 / 索引 / 并发 / 幂等"写成可被测试锁定的
> Contract。**不实现**任何 production 代码。

---

## 0. Executive Summary（冻结结论）

| # | 结论 | 章节 |
| --- | --- | --- |
| 1 | Conversation = 5 字段容器（conversation_id / project_id / created_at / updated_at / status） | §1 |
| 2 | ConversationTurn = 6 字段，**一条消息一行**（不是 user+assistant 合并行） | §2 |
| 3 | Turn → assistant_request_id 可空：USER = NULL；ASSISTANT = 实际 request id | §3 |
| 4 | role 第一版仅 USER / ASSISTANT（Conversation History 不应等同于 LLM raw messages） | §4 |
| 5 | project_id NOT NULL；创建时绑定；创建后不可修改 | §5 |
| 6 | History 只保存用户输入 / Assistant 最终回答；不得保存完整 LLM prompt | §6 / §13 |
| 7 | Trace / Timeline / Outcome 读取键保持 assistant_request_id，全部不变 | §7 / §8 / §9 |
| 8 | 同一 User Turn 允许对应多个 Assistant Turn（retry / regenerate） | §10 |
| 9 | ARCHIVED = 生命周期终点（停止追加、仍可读）；不引入 DELETED 状态 | §11 |
| 10 | Conversation 不负责删除 Observability 历史记录 | §11 |
| 11 | History retention 与 Observability retention 是两套独立策略 | §12 |
| 12 | 索引最小集合：conversation_id PK + (conversation_id, created_at) | §14 |
| 13 | conversation_id UNIQUE、turn_id UNIQUE；assistant_request_id 不加全局 UNIQUE | §15 |
| 14 | turn_id / assistant_request_id 都不是幂等键；client_message_id Deferred | §16 |

---

## 1. Conversation Model

### 1.1 字段表（冻结；第一版只允许这 5 个字段）

| 字段 | 设计类型（对齐现有风格） | 约束 | 语义 |
| --- | --- | --- | --- |
| `conversation_id` | `String(128)`（→ PK） | 唯一、非空、不可变、服务端签发 | 会话容器身份 |
| `project_id` | `String(128)` | 非空（project_id NOT NULL）、创建时绑定、不可变 | 项目/数据域绑定 |
| `created_at` | `DateTime(timezone=True)` + `server_default=func.now()` | 非空 | Conversation 创建时间 |
| `updated_at` | `DateTime(timezone=True)` + `server_default=func.now()`（+ 更新时写入） | 非空 | Conversation 最近一次有效变更时间 |
| `status` | `String(32)` | 非空、default / server_default = `ACTIVE` | 会话状态（仅 ACTIVE / ARCHIVED） |

### 1.2 conversation_id

* 唯一：一个 `conversation_id` 只标识一个 Conversation（`conversation_id UNIQUE`，作为主键）；
* 不可变：创建后不得修改（设计 DTO 用 frozen dataclass 锁定该语义）；
* **不得使用 assistant_request_id**：两者是不同概念（`conversation_id ≠ assistant_request_id`）；
* 生成方：服务端签发（延续 Step 1 方案 C；客户端只读携带，不得伪造）；
* 长度上限 128（复用现有业务键列宽 `String(128)`，与 `assistant_request_id` /
  `project_id` / `tool_call_id` 一致）；
* 生成算法（建议 uuid4，与 `new_request_id()` 同风格）属未来实现 Step 的细节，
  本步只冻结"唯一 / 不可变 / 服务端签发"。

### 1.3 created_at 与 updated_at

* `created_at` = Conversation 创建时间。
  **不是第一条消息时间**，也不是最后一次请求时间。
* `updated_at` = Conversation 最近一次有效变更时间（可被显式更新的容器字段；
  与 4 张 append-only 观测表的"无 updated_at"形成有意区别）。

未来到底哪些操作会更新 `updated_at`（设计结论，不实现）：

| 操作 | 是否更新 updated_at | 说明 |
| --- | --- | --- |
| 创建 | 是（初值 = created_at） | 创建本身即第一次有效变更 |
| 新增 Turn | 是 | 会话内容发生有效变更 |
| 归档（ACTIVE → ARCHIVED） | 是 | 状态迁移 = 有效变更 |
| 读取（GET / List） | 否 | 读取不更新 updated_at（读不是变更） |
| 物理删除 | N/A | 行及其 Turn 消失（见 §11） |
| 未来标题 / 摘要修改 | 未来定义 | 本版模型无标题 / 摘要字段（Deferred） |
| 观测回写（Trace / Outcome） | 否 | Observability 不回写 Conversation（单向只读关联） |

### 1.4 status（仅两态）

```text
ACTIVE    会话正常；未来允许追加 Turn（读 / 追加均可用）
ARCHIVED  会话归档；未来停止追加 Turn，仍可读取历史
```

* 明确**不加**以下状态（无明确需求）：
  `DELETED` / `CLOSED` / `EXPIRED` / `LOCKED` / `PROCESSING` / `FAILED`；
* "删除"不用 status 表达（见 §11），因此不存在第三种状态；
* 状态迁移（未来实现时）：第一版仅定义 `ACTIVE → ARCHIVED` 单向；
  是否需要 `ARCHIVED → ACTIVE` 恢复语义属未来 Step 决策；
* 存储风格：`String(32)` + default / server_default（对齐 `knowledge_document.status`）；
  取值大写固定枚举（对齐 `AssistantOutcome` 的 StrEnum 风格：值即对外契约）。

### 1.5 DDL 草案（**只设计，不执行**）

```sql
-- 设计草案；本 Step 不执行、不迁移、不建表。
-- 未来落点：独立 schema（例如 conversation）；不得放入 public（见 §14）。
CREATE TABLE conversation (
    conversation_id  VARCHAR(128) PRIMARY KEY,
    project_id       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    status           VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
);
```

---

## 2. ConversationTurn Model

### 2.1 关系

```text
Conversation
    1
    │
    └────── N ConversationTurn
```

### 2.2 字段表（冻结；第一版只允许这 6 个字段）

| 字段 | 设计类型（对齐现有风格） | 约束 | 语义 |
| --- | --- | --- | --- |
| `turn_id` | `BigInteger` 自增（→ PK） | 唯一、非空 | Turn 行身份（**不是** request id） |
| `conversation_id` | `String(128)` | 非空（引用 Conversation） | 所属会话 |
| `role` | `String(32)` | 非空；仅 `USER` / `ASSISTANT` | 消息角色 |
| `content` | `Text` | 非空 | 用户输入 / Assistant 最终回答文本 |
| `assistant_request_id` | `String(128)` | 可空；按 role 规则（见 §3） | 关联 Assistant Request |
| `created_at` | `DateTime(timezone=True)` + `server_default=func.now()` | 非空 | 本条消息写入时间 |

### 2.3 结构选择：一条消息一行（冻结）

```text
Conversation
   │
   ├── Turn 1 USER      （assistant_request_id = NULL）
   ├── Turn 2 ASSISTANT （assistant_request_id = A）
   ├── Turn 3 USER      （assistant_request_id = NULL）
   └── Turn 4 ASSISTANT （assistant_request_id = B）
```

即：**一条消息一行**；"user 消息 + assistant 回复"是**两行**，不是一行。
不采用 `user_content + assistant_content + assistant_request_id` 的合并行模型。

比较（选定前必须回答的 6 个维度）：

| 维度 | 一条消息一行（选定） | 合并行（未选） |
| --- | --- | --- |
| 查询 | 按 role / conversation 直接过滤 | 需要"半行"语义，查询表达扭曲 |
| 排序 | 单一时间序（created_at, turn_id） | 两半共享一行，顺序语义被压扁 |
| 流式输出（未来） | 可先写 USER 行，ASSISTANT 行稍后追加 | 必须原地更新同一行两列 |
| 失败恢复 | USER 已写入、ASSISTANT 未生成 → 状态自然表达 | 出现"半行"（user 有 / assistant 空） |
| 重新生成 | 同一 USER 行后追加新的 ASSISTANT 行 | 需要版本列或覆盖旧值 |
| 工具调用 | 不进入 Turn（经 assistant_request_id → Trace） | 同样不进入，但行内结构更复杂 |

排序契约（未来实现）：`ORDER BY created_at ASC, turn_id ASC`
（`turn_id` 作为同秒 / 同时刻的稳定 tiebreaker，对齐现有
`created_at ASC, id ASC` 的读边界风格）。

### 2.4 DDL 草案（**只设计，不执行**）

```sql
-- 设计草案；本 Step 不执行、不迁移、不建表。
CREATE TABLE conversation_turn (
    turn_id              BIGSERIAL    PRIMARY KEY,
    conversation_id      VARCHAR(128) NOT NULL,
    role                 VARCHAR(32)  NOT NULL,
    content              TEXT         NOT NULL,
    assistant_request_id VARCHAR(128) NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 最小索引（见 §14；本 Step 不创建）
CREATE INDEX ix_conversation_turn_conversation_created
    ON conversation_turn (conversation_id, created_at);
```

FK 设计（未来实现，本步只记录）：推荐 `conversation_id` 上使用
`FOREIGN KEY → conversation.conversation_id ON DELETE CASCADE`
（同聚合、同 schema；对齐 `knowledge_chunk → knowledge_document` 的
`ON DELETE CASCADE` 先例）。注意：该 CASCADE **只作用于 Conversation 自身
的 Turn**，绝不指向 4 张观测表（见 §11）。

---

## 3. Turn / Request Relationship

### 3.1 assistant_request_id 可空（冻结）

```text
USER      turn → assistant_request_id = NULL（用户消息本身不是 AI Core execution）
ASSISTANT turn → assistant_request_id = 实际 request id（非空）
```

* 不采用"每一个 Turn 都必须绑定 Request"：用户消息在 AI 执行之前就已存在，
  强行绑定会伪造一个不存在的 request；
* 不采用"只有 assistant turn 存在"：用户输入是 History 的必要组成；
* 校验规则（未来实现 + 本步测试锁定）：`USER ⇒ NULL`，`ASSISTANT ⇒ 非空`。

### 3.2 关联链

```text
ConversationTurn（ASSISTANT）
        ↓ assistant_request_id
Assistant Request（AI Core，一次 execute）
        ↓
Assistant Trace / Timeline / Outcome（现有读边界，键不变）
```

### 3.3 turn_id ≠ assistant_request_id（冻结）

```text
assistant_request_id = 一次 AI Core execution 的标识
turn_id              = Conversation 中一行消息的身份
```

两者不可互换：不得用 `assistant_request_id` 当 Turn 主键，
也不得用 `turn_id` 去查询 Trace / Timeline / Outcome。

---

## 4. User / Assistant Role

* 第一版只允许两个 role：`USER` / `ASSISTANT`；
* 第一版**不加** `system` / `tool`：
  * Conversation History 不应等同于 LLM raw messages；
  * `system prompt` 不入 History（安全，见 §13）；
  * Tool 原始 payload 不入 History（经 `assistant_request_id` → Trace 间接可见）；
* 若未来确实需要更多 role（例如系统消息可视化），必须另开 Step 评估，
  不得在本模型上直接扩列；
* 大写枚举值与列宽 `String(32)` 对齐现有 `status` / `AssistantOutcome` 风格。

---

## 5. Project Binding

### 5.1 冻结契约

```text
project_id NOT NULL
Conversation 创建时绑定 project_id
创建之后不可修改（不可切换）
```

### 5.2 为什么 NOT NULL（以当前 ProjectContext 实现为准）

* `ProjectContext.project_id` 自身即"非空 str"（`projects/models.py` 校验）；
* 默认工厂总是提供项目身份（配置 `PROJECT_ID`，默认 `vietnam-wms`）；
* 每个 `/api/ai/chat` 请求总会解析出一个 project（默认或注册表）；
* 因此"无项目会话"没有语义 —— `project_id = null` 不被允许；
* **不扩大 ProjectContext**：本模型只增加 `conversation.project_id` 列，
  不改 `ProjectContext` / `ProjectRegistry` / 请求级 project 解析。

### 5.3 隔离语义（Contract，不实现授权）

* 同一个 `conversation_id` 不接受 `project_id = B` 来访问 `project A` 的会话；
* 未来所有 Conversation 读 / 追加操作都必须校验
  "请求 project_id == conversation.project_id"，不一致 → 拒绝；
* 本步只写 Contract；**不实现**任何授权系统（认证 / ownership 仍属 Deferred）。

---

## 6. History Boundary

```text
Conversation History  = ConversationTurn 行集合（用户输入 + Assistant 最终回答）
LLM Context           = 未来 Context Builder 产出的、进入 Prompt 的片段
```

* History 是事实；Context 是派生（选择 / 截断 / 摘要）；
* 本 Step 不实现 Context Builder / Memory / Summary；
* History 不保存：完整 LLM prompt、RAG chunks、Tool arguments / results、
  SQL、system prompt（见 §13）；
* History 与 Observability（Trace / Timeline / Outcome）分离：
  History 面向"会话回放"，Observability 面向"执行审计"；
  两者通过 `assistant_request_id` 关联，不互相复制正文。

---

## 7. Trace Boundary

```text
ConversationTurn（ASSISTANT）
    ↓ assistant_request_id
GET /api/observability/assistant-trace/{assistant_request_id}   ← 现有 API 不变
```

* 现有 Trace API 路径 / 参数 / 响应字段保持完全不变；
* Conversation 层不新增 conversation 级 Trace 端点；
* Turn 不存储 Trace 内容（llm_usage / tool_executions / rag_executions 均不复制）。

---

## 8. Timeline Boundary

```text
ConversationTurn（ASSISTANT）
    ↓ assistant_request_id
GET /api/observability/assistant-timeline/{assistant_request_id}  ← 现有 API 不变
```

* 不新增 conversation timeline；
* 跨请求时间线仍是"未来派生视图"（N 个 request timeline 的组合），
  不属于本 Step，也不改变现有 Timeline API。

---

## 9. Outcome Boundary

* ConversationTurn **不复制** Assistant Outcome（SUCCESS / EMPTY / REFUSED / FAILED）：
  Turn 模型无 outcome / success / error_code 字段；
* 未来读路径：

```text
ConversationTurn（ASSISTANT）
    ↓ assistant_request_id
AssistantOutcome（ai_ops.assistant_outcome_record；每请求一行）
```

* Outcome 的 4 态枚举 / 表 / 写路径 / 读路径全部不变；
* 不引入 Conversation Outcome（与 Step 1 §9 结论一致）。

---

## 10. Retry / Regenerate

### 10.1 场景（冻结）

```text
USER Turn 1
      │
      ├── Assistant Request A → FAILED（可无 ASSISTANT Turn，或有一条失败说明 Turn）
      └── Assistant Request B → SUCCESS（ASSISTANT Turn B）
```

结论：

* 同一个 User Turn 可以对应**多个 Assistant Turn**（retry / regenerate）；
  模型不强制 "1 user : 1 assistant"；
* 每个 Assistant Turn 绑定**不同的** assistant_request_id（A ≠ B）；
* 不应存在"同一个 assistant_request_id 出现在两行"（设计事实：
  一个 request 至多产生一条 ASSISTANT Turn；数据库护栏见 §15）；
* 不为 regenerate 增加任何字段（turn_id 即行身份；request id 即执行身份）。

### 10.2 T2SQL Retry（request 内部）

现状（Phase 3.12 已实现）：一个 Assistant Request 内可能产生多个 LLM usage
（例如 Text-to-SQL semantic retry 的多次生成尝试）。

冻结结论：

* **不允许**：一个 LLM attempt = 一个 Conversation Turn；
* ConversationTurn 只对应 `assistant_request_id`（request 级），
  不对应 LLM usage / attempt（LLM 级）；
* attempt 级事实只存在于 `ai_ops.llm_usage_record`（Trace 可读），
  不进入 Conversation History。

---

## 11. Delete / Archive

### 11.1 归档（ARCHIVED）

* ARCHIVED = 正常生命周期终点：未来停止追加 Turn，仍可读取；
* `ACTIVE → ARCHIVED` 是有效变更（更新 `updated_at`，见 §1.3）；
* 不引入 `DELETED` 状态（避免第三态）。

### 11.2 删除（设计原则，本步不实现）

* Conversation Layer **不负责删除** Observability 历史记录；
* 未来物理删除 Conversation 时：只删除 Conversation 自身 + 其 Turn 行
  （同聚合 CASCADE）；
* **不级联删除**：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

* 核心不变式：

```text
Conversation lifecycle ≠ Observability lifecycle
```

（会话可以归档或消失，但它触发过的 Assistant Request 的观测事实
仍按 Observability 自己的 retention 留存。）

---

## 12. Retention

* `Conversation History retention` 与 `Observability retention` 是**两个独立策略**：

| 维度 | Conversation History | Observability |
| --- | --- | --- |
| 内容 | 用户 / Assistant 文本 | llm_usage / tool / rag / outcome 事实 |
| 敏感度 | 高（正文） | 中（白名单元数据） |
| 归属 | Conversation Layer | AI Core 观测层 |
| 生命周期 | 会话生命周期（ACTIVE / ARCHIVED / 删除） | Observability 自身策略（现状：无自动清理） |
| 清理机制 | 未来定义 | 未来定义（本步不新增） |

* 本步不增加：TTL / Celery cleanup / Cron / partition / 任何后台任务；
* 删除策略与保留期限均属未来 Step。

---

## 13. Security

### 13.1 允许

* ConversationTurn.content 保存：普通用户输入、Assistant 最终回答文本。

### 13.2 禁止字段 / 禁止内容（冻结）

以下**不得**被设计为模型字段，也不得写入 `content`：

```text
api_key · password · database_url · Authorization · raw headers
SQL · embedding vector · DB Session / connection
RAG raw chunks · tool raw internal payload · system prompt
```

### 13.3 原则

* **不得保存完整 LLM prompt**（History ≠ Prompt）；
* 不得把 RAG chunks / Tool arguments / Tool results / SQL / System Prompt
  塞入 ConversationTurn；
* Conversation History 未来可能比 Trace 更敏感（正文内容 vs 白名单元数据），
  安全等级不得低于现有观测边界；
* Conversation 的 observability metadata（若未来存在）与 History 正文分离，
  且沿用 §13.2 禁止键集。

---

## 14. Index Strategy

### 14.1 访问模式（先于索引确认）

```text
GET conversation        → 按 conversation_id 精确查 1 行
GET conversation turns  → 按 conversation_id 取列表，按 created_at 排序（未来分页）
```

### 14.2 最小索引集合（冻结；本步不创建）

| 表 | 索引 | 类型 | 理由 |
| --- | --- | --- | --- |
| conversation | `conversation_id` | PK（隐含 UNIQUE） | GET conversation |
| conversation_turn | `turn_id` | PK（隐含 UNIQUE） | 行身份 / 排序 tiebreaker |
| conversation_turn | `(conversation_id, created_at)` | 复合 B-tree | 列表 + 时间排序 |

### 14.3 明确不建

* **不为 assistant_request_id 建索引**：Turn → Request 反查的访问模式本阶段未定义
  （Deferred；未来明确需要再评估）；
* 不为 status 预建索引（ACTIVE / ARCHIVED 列表查询未定义）；
* 不预建"20 个 index"；不建 text search / GIN / partition 索引。

### 14.4 schema 落点

* 未来 Conversation 表**不得放入 public**（业务 schema）：否则可能被
  Schema Explorer / Text-to-SQL 当作业务表（与 `ai_ops` 隔离的同一理由）；
* 推荐独立 schema（例如 `conversation`）或经评估后使用其他内部 schema；
  本步不决定最终 schema 名、不建表、不迁移。

---

## 15. Concurrency

### 15.1 唯一性（冻结）

```text
conversation_id UNIQUE（主键；服务端签发）
turn_id UNIQUE（自增主键）
assistant_request_id 不加全局 UNIQUE
```

* `assistant_request_id` 不加全局 UNIQUE 的原因：结合 retry / regenerate 语义
  —— 不同 request 各自产生自己的 ASSISTANT Turn（不冲突）；
  真正需要防止的是"同一 request 出现两行"，该护栏属未来评估
  （可选 partial unique：`WHERE assistant_request_id IS NOT NULL`），
  **本步不实现、不建索引**；
* 绝不为满足唯一性而复用 ID（turn_id 与 assistant_request_id 保持不同身份）。

### 15.2 并发写风险（分析，不实现）

| 风险 | 说明 | 未来方向 |
| --- | --- | --- |
| history ordering | 同会话两个并发追加的落库顺序可能不等于用户顺序 | 单会话串行化 / 序列号（未来） |
| duplicate turn | 重试 / 双端提交导致重复 USER 行 | 幂等键（§16，未来） |
| last-write-wins | 若未来存在 conversation 级可变字段（title / summary） | 乐观锁（未来） |

* 本步**不实现**锁 / 队列 / 版本列 / 序列号；
* 排序现状契约：`created_at ASC, turn_id ASC`（同刻稳定）；
* 与 Step 1 §11 结论一致（per-request 体系天然并发安全；Conversation 级写并发未来处理）。

---

## 16. Idempotency

| 候选 | 是否幂等键 | 结论 |
| --- | --- | --- |
| `conversation_id` | 否 | 会话容器身份；服务端签发；不承担请求去重 |
| `turn_id` | 否 | 行身份（数据库生成）；不能由客户端重放 |
| `assistant_request_id` | 否 | 服务端单次请求 ID；不是 message id |
| `client_message_id` | 未来候选 | **本步不新增**；未来若需要幂等，单独设计（客户端生成 + 服务端去重） |

* 重复 POST（未来 Conversation messages 端点）可能产生重复 USER Turn 的风险
  已记录（§15.2），解决方案属未来 Step；
* 现有服务端幂等先例（语义参考，不复制实现）：
  `llm_usage_record` 的 partial unique + ON CONFLICT DO NOTHING、
  `assistant_outcome_record` 的 first-write-wins。

---

## 17. Deferred Implementation

本 Step 结束后的不变式（测试 + git diff 双重确认）：

```text
DB = unchanged
API = unchanged
Orchestrator = unchanged
RAG = unchanged
Tool = unchanged
LLM = unchanged
```

明确 Deferred（全部未实现）：

| 事项 | 状态 |
| --- | --- |
| conversation / conversation_turn 建表 + migration | Deferred（本步禁止） |
| ORM Model / Repository / Service / API | Deferred（本步禁止） |
| FK 与 CASCADE 的实际落库 | Deferred（§2.4 仅设计） |
| 索引创建（§14 最小集合） | Deferred |
| Context Builder / Memory / Summary | Deferred |
| ARCHIVED 的写保护校验 | Deferred（§11 仅语义） |
| 物理删除与隐私删除流程 | Deferred（§11 仅原则） |
| Retention / TTL / 清理任务 | Deferred（§12） |
| 并发控制（锁 / 序列号 / 版本列） | Deferred（§15） |
| client_message_id 幂等 | Deferred（§16） |
| 认证 / 授权 / ownership | Deferred（§5.3 仅 Contract） |
| Conversation API（Step 1 §13 设计记录） | Deferred |

---

## 18. Field Style Evidence（复用现有字段风格；不重新发明）

| 维度 | 冻结设计 | 现有先例（代码证据） |
| --- | --- | --- |
| 业务键列宽 | `String(128)` | `request_id` / `assistant_request_id` / `project_id` / `tool_call_id`（`db/models/*`） |
| 主键 | `BigInteger` 自增；不引入 UUID / ULID / Snowflake | 全部 6 张表；`llm_usage_record.py` 文档注释明确"主键沿用项目既有 BIGINT 自增规范" |
| created_at | `DateTime(timezone=True)` + `server_default=func.now()` | 全部表 |
| updated_at | `server_default=func.now()` + 更新时写入（`onupdate=func.now()` 先例为 Python 端） | `knowledge_document.updated_at` |
| status 列 | `String(32)` + default / server_default + 大写枚举值 | `knowledge_document.status`（String(32)）+ `AssistantOutcome`（StrEnum 大写） |
| 文本内容 | `Text` | `knowledge_chunk.content`（Text） |
| 唯一约束命名 | `uq_<table>_<column>` 风格 | `uq_llm_usage_record_request_id` / `uq_assistant_outcome_record_assistant_request_id` |
| partial unique | `postgresql_where=text(...)` | `llm_usage_record` 的 request_id partial unique |
| 读排序 | `created_at ASC, id ASC` | LLM Usage / Trace 读边界 |

---

## 19. Contract Tests

新增文件：

```text
tests/test_conversation_model_contract.py
```

覆盖矩阵：

| 组 | 测试类 | 内容 |
| --- | --- | --- |
| 1 | `TestConversationModel` | 5 字段精确集合 / ID 不可变 / project NOT NULL / status 两态 / created_at·updated_at 语义与触发点 |
| 2 | `TestConversationTurnModel` | 6 字段精确集合 / turn_id BIGINT 风格 / role 两态 / content 限制 / request id 按 role 可空 / 列宽先例 |
| 3 | `TestTurnRequestRelationship` | 1:N / USER=NULL·ASSISTANT=非空 / 一条消息一行 |
| 4 | `TestRetryAndRegenerate` | 多 Assistant Turn / 无 attempt 字段 / T2SQL retry 不产生新 Turn |
| 5 | `TestSecurityBoundary` | 禁止字段集 / metadata 守卫 / 文档禁令 / History 敏感度 |
| 6 | `TestDesignDecisions` | 索引最小集 / UNIQUE 结论 / 生命周期分离 / public 禁令 / ID 边界标记 |
| 7 | `TestExistingBoundaryUnchanged` | Trace / Timeline / Outcome contract 不变 |
| 8 | `TestNoProductionImplementation` | db / api / services 无 conversation 代码 / 无路由 / 文档不变式 |
| 9 | `TestDesignDocument` | 17 个必需章节 / 引用 Step 1 |
| 10 | `TestSelfAudit` | 自身 import AST 离线自审 |

运行命令（唯一允许）：

```powershell
python -m pytest -q tests/test_conversation_model_contract.py
python -m compileall -q backend tests scripts
```

漂移报警：任何"进入 production 的表 / ORM / API / 路由、修改现有 Trace ·
Timeline · Outcome contract、敏感字段进入模型、ID 语义混用"的变化都会使
该文件失败。

---

## 20. Audit Evidence

### 20.1 实际阅读文件（本步）

```text
backend/app/db/models/assistant_outcome_record.py
backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
backend/app/db/models/knowledge_document.py   （status / updated_at 先例）
backend/app/db/models/knowledge_chunk.py      （Text content / FK CASCADE 先例）
backend/app/db/models/__init__.py
backend/app/dto/assistant_outcome.py          （StrEnum 枚举风格）
backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py
backend/app/projects/models.py                （ProjectContext.project_id 非空）
docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md（前置结论）
```

### 20.2 搜索记录（关键词）

```text
created_at / updated_at / UUID / request_id / assistant_request_id /
project_id / archived / deleted / status
```

关键发现：`updated_at` 唯一先例 = `knowledge_document`（含 `onupdate` 注释）；
`status` 唯一先例 = `knowledge_document.status`（String(32)）；
`archived` / `deleted_at` / `soft_delete` 在 backend 中**不存在**（本步不引入实现）。

### 20.3 本 Step 变更范围（git diff 证明）

```text
允许：
    tests/test_conversation_model_contract.py                      （新增）
    docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md（新增）

必须不变：
    backend/app/            = unchanged
    backend/app/db/         = unchanged
    backend/app/api/        = unchanged
    .github/                = unchanged
    database migration      = 无
```

### 20.4 运行记录

```text
python -m pytest -q tests/test_conversation_model_contract.py
    → 38 passed（纯离线断言；不驱动 /api/ai/chat、不写任何表、不调用 LLM）

python -m pytest -q tests/test_conversation_model_contract.py --noconftest
    → 38 passed in 0.88s（对照：测试文件自身 < 1s，零 DB / 零网络）

python -m compileall -q backend tests scripts
    → 通过
```

纯度口径（本 Step 产物）：DB writes = 0 · DB reads = 0 · network = 0 · LLM = 0。

环境说明（延续 Step 1 §16.4，如实记录）：仓库 `tests/conftest.py` 的既有
会话级残留守卫在配置了 `DATABASE_URL` 的环境下会对
`ai_ops.assistant_outcome_record` 做 1 次水位查询与 1 次"本次会话新增行"清理；
本 Step 测试不产生 outcome 行（该 DELETE 影响 0 行），且属既有测试基础设施行为。
本次运行中该 fixture setup 在本机环境耗时约 260s（DB 连接缓慢的环境波动；
`--noconftest` 对照运行 0.88s 证明测试内容本身无慢点）。
如需严格 0 接触，可使用 `--noconftest` 对照命令。

未运行：`RUN_DB_TESTS` / 全量 `pytest -q`（本 Step 明确排除）。

---

## 21. 参考资料

* `docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md`
* `docs/architecture.md` §20 Conversation Architecture / §21 Context Management（规划）
* `docs/requirements.md` FR-011 Conversation Context
* `tests/test_conversation_architecture_contract.py`（Step 1 契约测试）
* `tests/test_assistant_trace_timeline_contract.py`（既有 contract 风格来源）
