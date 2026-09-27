# WMS AI Assistant — Architecture

## 1. 文档信息

| 项目           | 内容                    |
| ------------ | --------------------- |
| 项目名称         | WMS AI Assistant      |
| 文档名称         | 系统架构设计                |
| 当前版本         | 1.0                   |
| 当前阶段         | MVP                   |
| 后端语言         | Python 3.12+          |
| Web 框架       | FastAPI               |
| 数据库          | PostgreSQL            |
| 向量数据库        | PostgreSQL + pgvector |
| LLM          | 可配置的 LLM API          |
| Embedding    | 可配置的 Embedding API    |
| RAG          | MVP 支持                |
| Tool Calling | MVP 支持                |
| Agent        | 后续阶段                  |
| LangGraph    | 后续阶段                  |
| 部署           | MVP 优先单体部署            |

---

# 2. 架构目标

WMS AI Assistant 是一个面向企业 WMS/ERP 场景的 AI 应用系统。

系统通过自然语言理解用户需求，根据用户问题自动选择：

1. 普通 LLM 对话
2. RAG 知识库检索
3. WMS/ERP 数据查询 Tool
4. 后续扩展 Agent 和 LangGraph

核心目标：

> 让用户通过自然语言访问企业知识、业务数据和业务能力，同时保证 AI 不绕过企业原有业务规则和权限体系。

例如：

```text
用户：
越南仓 A001 现在还有多少库存？

↓

AI 判断：
这是库存查询问题

↓

调用 Tool：
get_inventory(
    material_code="A001",
    warehouse_code="VN01"
)

↓

Tool 调用：
WMS API

↓

WMS 返回：
库存 1250

↓

LLM：
根据结构化数据生成自然语言回答

↓

用户：
A001 在越南仓还有 1250 个。
```

---

# 3. 总体架构

## 3.1 MVP 总体架构

```text
┌──────────────────────────────┐
│            用户              │
└──────────────┬───────────────┘
               │
               │ Natural Language
               ▼
┌──────────────────────────────┐
│          Frontend            │
│      Web / Chat Interface    │
└──────────────┬───────────────┘
               │ HTTP
               ▼
┌──────────────────────────────┐
│          FastAPI             │
│       API / Auth / Session   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│        AI Service            │
│                              │
│  ┌────────────────────────┐  │
│  │      Intent / Router    │  │
│  └───────────┬────────────┘  │
│              │               │
│      ┌───────┴────────┐      │
│      ▼                ▼      │
│    RAG             Tool      │
│   Service          Calling   │
│      │                │      │
│      └───────┬────────┘      │
│              ▼               │
│             LLM              │
└──────────────┬───────────────┘
               │
        ┌──────┴───────────┐
        │                  │
        ▼                  ▼
┌───────────────┐   ┌────────────────┐
│  PostgreSQL   │   │   WMS / ERP    │
│               │   │      API       │
│ Business Data │   │                │
│ Vector Data   │   │ Existing Rules │
│ Conversation  │   │ Permission     │
└───────────────┘   └────────────────┘
```

---

# 4. 核心架构原则

## 4.1 AI 不直接操作企业数据库

这是整个项目最重要的架构原则。

禁止：

```text
LLM
 ↓
直接连接 WMS PostgreSQL
 ↓
SELECT inventory ...
```

正确方式：

```text
LLM
 ↓
Tool
 ↓
WMS API
 ↓
WMS Business Service
 ↓
Database
```

原因：

* 避免 AI 绕过业务规则
* 避免 SQL 注入
* 避免越权查询
* 复用现有 WMS 业务逻辑
* 方便权限控制
* 方便审计
* 后续支持写操作时更加安全

---

# 5. 系统分层

系统采用简单的分层架构。

```text
API Layer
    ↓
Application Layer
    ↓
AI Layer
    ↓
Tool Layer
    ↓
Integration Layer
    ↓
External WMS / ERP
```

---

## 5.1 API Layer

负责：

* HTTP API
* 请求参数
* 用户会话
* 用户身份
* API Response
* 异常处理

例如：

```text
POST /api/chat
GET  /api/health
GET  /api/conversations
```

不负责：

* LLM Prompt
* RAG
* WMS 业务逻辑
* 数据库业务查询

---

# 6. Application Layer

Application Layer 负责组织业务流程。

例如：

```python
chat_service.chat(...)
```

负责：

```text
接收用户问题
    ↓
获取用户上下文
    ↓
调用 AI Service
    ↓
处理 Tool / RAG
    ↓
生成最终响应
    ↓
保存会话
```

Application Layer 不应该包含具体的 WMS SQL。

---

# 7. AI Layer

AI Layer 是整个系统的核心。

主要包含：

```text
LLM Service
Prompt Service
RAG Service
Tool Calling Service
Context Service
```

未来增加：

```text
Agent
LangGraph
Memory
Planning
```

---

# 8. LLM Service

LLM Service 负责统一封装不同的大模型。

建议不要在业务代码中直接写：

```python
openai.chat.completions.create(...)
```

而应该封装成：

```python
llm_service.chat(...)
```

或者：

```python
llm_service.generate(...)
```

这样未来可以切换：

```text
OpenAI
Claude
Qwen
DeepSeek
Ollama
企业私有模型
```

而不需要修改整个业务系统。

---

## 8.1 LLM 配置

模型配置统一使用环境变量。

例如：

```text
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
```

禁止：

```python
API_KEY = "xxxxx"
```

禁止把 API Key 提交到 Git。

---

## 8.2 LLM Provider 抽象（Phase 3.10.1）

AI Core 不直接依赖具体 LLM 实现，依赖抽象 `LLMProvider`：

```text
AI Core
  ↓
LLMProvider         （backend/app/llm/provider.py，Protocol）
  ↓
DeepSeekProvider    （backend/app/llm/deepseek_provider.py，delegation）
  ↓
OpenAICompatibleClient（backend/app/llm/client.py，
                       现有 DeepSeek OpenAI-compatible Client）
```

要求：

* DeepSeek 只是具体的 Provider 实现，通过
  `LLM_BASE_URL / LLM_MODEL / LLM_API_KEY` 配置；
* Provider 接口不暴露 DeepSeek 专属概念、不泄漏 HTTP /
  OpenAI SDK 类型、不包含 API Key / Project Context /
  RAG / Tool / Text-to-SQL 逻辑；
* 核心服务（`RagService` / `TextToSQLService` / `ToolChatService` /
  `AIRouterService`）通过依赖注入接收 `LLMProvider`；
* `client.LLMClient` 保留为 `LLMProvider` 的向后兼容别名；
* 实例由 composition/root 层决定（`create_llm_client()`）；
  未来再增加 Provider Factory / Registry，本阶段不实现。

---

## 8.3 LLM Reliability Boundary（Phase 3.10.2）

```text
Transport reliability（HTTP timeout / 网络错误 / 状态码分类）
    ↓ 由 LLM Client / Provider 层负责

Business semantic retry（如 Validator 拒绝后重新生成 SQL）
    ↓ 由 TextToSQLService / 未来业务服务负责
```

* **Timeout**：由 `OpenAICompatibleClient` 统一负责
  （httpx.Timeout；`LLM_TIMEOUT_CONNECT / READ / WRITE / POOL`
  可配置，默认 10 / 60 / 10 / 10 秒）。业务服务不各自实现
  HTTP timeout。
* **Retry 分类**（只分类，不自动重试）：
  `llm.retry.is_retryable_llm_error()` 纯函数——
  网络级失败（连接失败 / 超时）、HTTP 429、HTTP 5xx 为可重试；
  其它 4xx、配置错误（`LLMConfigError`）、响应结构错误
  （`LLMResponseError`）不可重试。
* **Provider 不负责业务语义 Retry**；**业务层不负责
  HTTP transport retry**。两者不得叠加（避免多层重试放大）。
* **Refusal**（Phase 3.9.25）是业务层语义结果，
  不是 transport failure，不触发任何 retry。

---

## 8.4 LLM Structured Response Contract（Phase 3.10.3）

```text
Raw LLM Output（str）
      ↓
Structured Parser（llm/structured.py，严格 JSON object 提取）
      ↓
Pydantic Validation（schema + extra=forbid）
      ↓
Typed Structured Result（Pydantic model 实例）
```

* **支持**：bare JSON object、完整 ```` ```json ```` code fence
  （前后仅允许 whitespace）；未知字段一律拒绝（LLM 输出视为
  untrusted input）；只用 `json.loads` + Pydantic，禁止动态执行。
* **拒绝**：JSON 语法错误、多个 JSON object、JSON 前后夹杂
  自然语言、未闭合 / 夹带文字的 fence、非 object JSON、
  类型错误 / 缺字段 / 未知字段 / Literal 越界。
* **失败**：抛 `LLMStructuredOutputError`（`LLMError` 子类），
  `reason` ∈ `empty_output / invalid_json / unsupported_format /
  schema_validation_failed`；message 不携带原始 LLM 输出。
* **Structured Response ≠ Tool Calling**：前者解析 LLM 普通文本
  输出，后者是模型请求调用工具的协议，两者独立。
* Structured Output 当前作为独立基础能力存在，**尚未接入**现有
  Text-to-SQL / RAG / Tool production path（保持既有行为与
  评估基线不变）。

---

## 8.5 LLM Response Metadata Contract（Phase 3.10.4）

```text
LLM Provider
    ↓
LLMResponse（AI Core 内部统一 DTO）
    ├── content
    ├── tool_calls        （Tool Calling 路径）
    ├── model             （实际响应返回的 model，非配置值）
    ├── finish_reason     （stop / length / tool_calls ...）
    ├── usage             （LLMUsage：prompt/completion/total tokens）
    └── metadata          （白名单：provider / request_id）
```

* `chat(messages, tools=[...])` 返回 `LLMResponse`；
  `generate()` / 无 tools 的 `chat()` 仍返回 `str`
  （Phase 2 契约不变，业务层零修改）。
* SDK（httpx / OpenAI-compatible raw response）只存在于
  Client / Provider 层；上层不得访问 `choices` / `usage` /
  `id` 等 SDK 专属字段。
* 缺失即 `None`：usage / model / finish_reason / metadata 缺失
  **不是错误**，不虚构数值、不影响主调用；usage 数据非法
  （负数 / total 不一致）→ 降级为 `None` + warning。
* `usage ≠ cost tracking`（成本计算留待后续阶段）；
  `metadata ≠ observability system`（不含 tracing / metrics）；
  metadata 绝不包含 API Key / Authorization / 原始 SDK
  response / HTTP headers。

Contract 不变量（Phase 3.10.5 加固）：

* `content` 原样透传（无 strip / str() / JSON parse 等隐式转换）；
* `model` 只来自实际响应，**不用配置 model 兜底**（缺失即 None）；
* `finish_reason` 未知字符串原样保留（不做猜测性转换 / 枚举过滤）；
* `usage` 字段严格类型（int | None，拒绝 bool / str / float /
  负数 / total 不一致，无隐式转换）；usage dict 无任何有效字段时
  → `None`（不构造空壳对象）；
* `request_id` 空字符串视同缺失；metadata 白名单只有
  `provider` / `request_id`；
* Raw SDK response 只存在于 Provider 边界内——上层只依赖
  `LLMResponse`；未来新增 Provider 只要产出本 DTO 即接入
  同一消费路径。

---

## 8.6 LLM Observability Contract（Phase 3.10.6）

```text
LLM Request → LLMProvider → LLMResponse / Exception
                                   ↓
                     build_llm_observation_safe（llm/observability.py）
                                   ↓
                          LLMObservation（内部 DTO）
```

`LLMObservation` 字段：`provider / model / latency_ms / success /
finish_reason / usage / request_id / error_type`。

* **内部 DTO**：Observation 是结构化数据对象，只记录**调用元数据**，
  不是 Logging / Metrics / Tracing System；
* **不记录调用内容**：无 messages / system_prompt / tool definitions /
  SQL / RAG chunks；
* **不记录 secrets**：无 API Key / Authorization / password / headers /
  raw response；失败只记 `error_type = type(exc).__name__`
  （不含 exception message / stack trace）；
* **usage 与 cost 分离**：usage 复用 `LLMUsage`，
  无 cost / price / currency 字段；
* **Observation failure 不影响业务**：`build_llm_observation_safe`
  构造失败返回 `None` + warning，绝不抛出；
* **当前不持久化**（无 DB / Redis / 文件写入），不接
  OpenTelemetry / Prometheus / Langfuse；无新增依赖；
* latency 由调用方以 `time.perf_counter()` 起点传入（无法测量时
  `None`，不伪造）；`request_id` 只来自 `LLMResponse.metadata`。

---

## 8.7 LLM Observation Integration（Phase 3.10.7）

```text
LLM call（OpenAICompatibleClient.chat）
    ↓ started_at = perf_counter()
    ↓ _chat_impl（原有调用逻辑，零改动）
    ├── success → build_llm_observation_safe(response=result)
    └── failure → build_llm_observation_safe(error=exc) → raise（原样）
    ↓
LLMObservationSink.record(observation)（默认 No-op）
```

* **一次实际 Provider request 最多一个 Observation**：
  `chat()` 是唯一观测边界；`generate()` 内部复用 `chat()`，
  不重复记录；
* **success / failure 都产生 Observation**：失败时
  `success=False + error_type=类名`，原始异常**原样继续抛出**
  （不被 Observation 替换 / 吞掉）；
* **Observation failure 不影响业务**：builder 与 sink 双层防御，
  任何观测层故障只记 warning（返回值 identity / 异常行为与
  Phase 2 / 3.6.2 完全一致）；
* **暴露方式**：per-client 注入 `LLMObservationSink`（构造参数，
  默认 `NoopObservationSink`）——request-scoped `record()`，
  无共享 last_observation 状态 → 并发安全；
  返回契约（`generate()→str` / `chat()→str|LLMResponse`）与
  `LLMResponse.metadata` 白名单均不变；
* **不持久化、不外发**、不做 cost calculation、不做 tracing
  （Observation ≠ Logging / Metrics / Tracing System）。

---

## 8.8 LLM Observation Evaluation（Phase 3.10.8）

Observation 是 **request-level** telemetry DTO：

```text
1 actual LLM request  →  1 Observation
```

（不是 business-operation level：T2S semantic retry 的两次
LLM request 产生 2 个 Observation，而非一个"整个 T2S 操作"
的 Observation。）

业务链路验证原则（`tests/test_llm_observation_integration.py`）：

* **RAG**：规则内 1 次 LLM request → 1 Observation（success=True）；
  空检索 0 request → 0 Observation；
* **Tool Calling**：每轮 LLM 各 1 个 Observation
  （`finish_reason="tool_calls"` 与最终 `"stop"` 各自对应）；
* **Text-to-SQL**：正常生成 1 个；semantic retry 每个实际
  request 各 1 个（`LLMError` 直接透传，不进入语义 retry）；
* **Refusal**：LLM 调用 1 次 → 1 个 Observation 且 `success=True`
  （refusal 是业务结果，不是 LLM 调用失败），Validator=0；
* **Router**：规则命中 0 request → 0 Observation；
  LLM fallback 1 request → 1 Observation；分类结果不因 sink 改变；
* **Orchestrator**：`observation_count == actual_llm_request_count`；
* 失败链路：原始业务异常原样传播 + failure Observation；
  builder / sink 故障不影响业务结果与异常；
* 无 sink（No-op 默认）与 collecting sink 的业务结果完全一致；
* 并发请求无 Observation 交叉；Observation 不含 prompt / SQL /
  RAG chunks / tool arguments / secrets。

当前 Observation 仍然**不持久化、不外发、不做 cost calculation**。

---

## 8.9 LLM Token & Cost Accounting Contract（Phase 3.10.9）

两个不同层次（务必区分）：

```text
层次 1（provider 事实）：
    LLMResponse → LLMUsage
    （LLMUsage 不知道价格；LLMResponse / LLMObservation
      均不携带 cost / price / currency）

层次 2（显式输入 Pricing，llm/accounting.py）：
    LLMUsage + LLMPricing → calculate_llm_cost → LLMCost
```

* **Token Accounting 复用 `LLMUsage`**（frozen、`int | None`、
  total 一致性构造校验已满足 Contract——不新增重复 DTO）；
  Accounting 不重新解析 provider response（SDK isolation
  仍由 Client 负责），不推断 / 不补全 partial usage；
* **LLMPricing**：`input_price_per_1m_tokens` /
  `output_price_per_1m_tokens`（Decimal，>= 0，拒绝 NaN / Inf /
  bool / string / float 隐式转换）+ 显式 `currency`（无默认币种，
  不做货币转换）；**本阶段不含任何真实 Provider 价格**
  （禁止 model → price 硬编码；测试仅用 synthetic pricing）；
* **`calculate_llm_cost` 是纯函数**（无网络 / 无 DB / 无全局状态 /
  deterministic）；公式按 input / output 分别计价
  （绝不用 total_tokens 乘统一价）；partial usage：缺失侧成本为
  `None`（缺失 ≠ 0）；`usage=None → cost=None`（不估算 token）；
* **Decimal 计算（标准库），不自动 rounding**——本阶段 Cost 是
  计算 Contract，不定义账单展示 / 结算精度；
* **Observation ≠ Cost**：同一 request 可套用多套 Pricing，
  因此 LLMObservation / LLMResponse 契约保持不变；
* **不持久化、不结算账单、不做汇率转换**、无新依赖。

---

## 8.10 LLM Accounting Integration（Phase 3.10.10）

```text
LLMResponse
    ↓
LLMUsage
    ├──→ Observation（runtime facts，success 时 usage 同一对象）
    └──→ Token Accounting（token_accounting_from_usage，usage 同一对象）

LLMPricing + LLMUsage → calculate_llm_cost → LLMCost
（显式、纯函数；synthetic pricing 仅测试使用）
```

* **request-level 关联**：`1 request → 1 Observation → 1 Accounting`；
  Observation.usage 与 Accounting 是同一次 request 的同一
  `LLMUsage` 对象（identity 链测试锁定）；
* **str 契约路径**（`generate()` / 无 tools 的 `chat()` / T2S /
  RAG / Router）无 usage → `observation.usage is None` →
  `accounting=None`——不虚构 usage、不估算、不补 0；
* **语义 retry / Tool Calling**：每个实际 request 独立
  Observation + Accounting，不做任何合并 / aggregation；
* **Accounting 不在生产关键路径**：Accounting / Cost 计算故障
  不影响 LLM response、业务结果与 Observation；
* **生产 Client 不自动计算 Cost**（client.py 不 import
  accounting——结构性隔离 + spy 零调用双重锁定）；Cost 计算
  始终是显式纯函数行为；
* Observation / LLMResponse 契约保持不变（无 cost / pricing /
  currency 字段）；当前仍无 Aggregation / Persistence / Billing。

---

## 8.11 LLM Accounting Lifecycle（Phase 3.10.11）

```text
LLM Request
    ↓
LLMResponse / str
    ↓
LLMObservation
    ├──→ ObservationSink（runtime facts；默认 No-op）
    └──→ AccountingSink（token facts；默认 No-op）
              ↓  token_accounting_from_usage(observation.usage)
              ↓  LLMUsage | None（request-level accounting）
```

* **独立 Sink Contract**：`LLMAccountingSink.record(observation)`
  与 `LLMObservationSink` 相互独立、互不影响；实现只读取
  `observation.usage` 并通过 `token_accounting_from_usage()` 派生
  （usage=None → accounting=None，不估算 / 不生成 0 tokens /
  不伪造）；
* **每次实际 Provider 请求恰好一个 accounting event**（成功与
  失败都会 emit；失败 observation usage=None → accounting=None）；
  semantic retry / Tool Calling 每轮独立，**不做合并 / aggregation**；
* **默认行为不变**：`accounting_sink=None` → `NoopAccountingSink`
  ——与 Phase 3.10.10 行为完全一致（`create_llm_client` 新增
  optional 参数，旧调用不失效）；
* **Sink failure isolation**：任一 sink（Observation / Accounting）
  抛异常只记 warning，绝不影响 LLM 调用结果与原始异常传播；
* **request-scoped**：无共享 last_accounting 状态；并发请求
  Observation A ↔ Accounting A 一一对应；
* Accounting = request-level；Cost = explicit calculation
  （lifecycle 不自动调用 `calculate_llm_cost`）；Aggregation /
  Persistence / Billing = not implemented。

---

## 8.12 LLM Usage Visibility Bridge（Phase 3.10.12）

```text
Provider Response
        ↓
LLMResponse（内部，统一含 usage / model / finish_reason / metadata）
        ↓ Compatibility Boundary（chat() 派生）
        ├──→ existing str API（业务调用方；tools 非空 → LLMResponse 原对象）
        │
        └──→ Observation（usage / model / finish_reason / request_id 全可见）
                 ↓
             Accounting（token_accounting_from_usage）
```

* `_chat_impl` 统一返回**内部 LLMResponse**：无 tools 路径的
  content 提取语义与 Phase 2 完全一致（`_extract_answer`；
  缺失 / 非 str 仍抛 `LLMResponseError`），同时响应元数据进入
  request lifecycle——不重复解析、不新增网络 / 全局状态；
* **Public business API remains backward compatible**：
  `generate() → str`、`chat(messages) → str`、
  `chat(messages, tools=...) → LLMResponse`（identity 不变）；
* Provider Usage 在 request lifecycle 边界内部消费：
  str 契约路径的 Observation 现在携带实际 usage / model /
  finish_reason / request_id（不回退配置值、不虚构）；
* Failure 不伪造 usage；Tool Calling / semantic retry 各 request
  独立；Refusal 的 usage 不丢失；
* **No Usage persistence / automatic Cost calculation /
  aggregation** 被引入。

---

## 8.13 LLM Usage & Cost Consumption Boundary（Phase 3.10.13）

```text
LLMResponse → LLMObservation → LLMUsage
                                  ↓
                        Usage Consumer（纯函数，request-level）
                                  ↓
                             LLMUsage | None

LLMUsage + 显式 LLMPricing → calculate_llm_cost → LLMCost
（Cost Consumer 只做边界转发，复用现有计算）
```

* **三层语义**：Usage = provider 事实（非估算 / 非计算 / 非 billing
  token）；Pricing = 外部显式输入；Cost = 计算结果；
* **Consumer**（`llm/accounting_consumer.py`，optional API）：
  `consume_usage(observation)` / `consume_cost(observation, pricing)`
  ——纯函数、无 IO、无状态、request-level；
* **Identity 保持**：`consume_usage(observation) is observation.usage`
  ——不复制、不重算 total、不补全、不 round、不估算；
  usage=None → None（不是 0）；partial 原样；
* **Cost 必须显式 Pricing**：`pricing=None` → `None`（不猜价、
  不硬编码 model→price、不自动获取、不做货币转换）；
  currency 由 `LLMPricing` 显式提供（不默认 USD/CNY/VND）；
* **不新增重复 DTO**：消费结果就是 `LLMUsage` / `LLMCost`
  （无 UsageRecord / AccountingRecord）；
* **No persistence / aggregation / billing / automatic pricing /
  automatic cost**；Client 不负责 Cost；现有
  `LLMObservationSink` / `LLMAccountingSink` / `create_llm_client`
  契约不变（不使用 Consumer 行为完全不变）。

---

## 8.14 LLM Usage Persistence Contract（Phase 3.10.14）

```text
LLMObservation
      ↓
LLMAccountingSink（DatabaseLLMAccountingSink）
      ↓
LLMUsagePersistenceService（复用 consume_usage 取 usage 事实）
      ↓
LLMUsageRepository.create()
      ↓
PostgreSQL: llm_usage_record
```

* **表**：`llm_usage_record`，一行 = 一次 LLM 请求的 Provider usage
  事实。字段（精确白名单）：`id / request_id / provider / model /
  prompt_tokens / completion_tokens / total_tokens / created_at`；
* **独立 schema**：表位于 `ai_ops`，**不在业务 schema `public`**——
  `PostgreSQLMetadataProvider.inspect(schema="public")` 会读取 public
  下全部基表作为 Text-to-SQL 的业务 schema；若 AI 内部运维表混入
  public，会被 LLM 当成业务表（并破坏业务表枚举）。`init_db()` 会
  `CREATE SCHEMA IF NOT EXISTS ai_ops`（幂等）；
* **来源约束**：`request_id` 来自 `LLMObservation.request_id`
  （缺失存 NULL，**绝不生成 UUID 冒充**）；`provider` 原样保存
  （不填 unknown / default）；`model` 来自实际响应
  （禁止 `settings.model` 兜底）；token 字段原样保存 provider 值
  （**NULL ≠ 0**，不重算 `total = prompt + completion`）；
* **写入条件**：`usage is None` → 不写入。本表是
  **LLM Usage Storage**，不是 request audit log（失败请求 /
  无 usage 成功请求都不产生行）；
* **Cost / Pricing**：本阶段**不落库**（数据库无 price / cost /
  currency 字段，无 `llm_pricing` 表）；Cost 仍由显式
  `LLMPricing` + `calculate_llm_cost()` 计算；
* **Persistence failure must not change LLM business result**：
  仓储写入失败 → 事务回滚 + sink 记 warning，LLM 业务结果与返回
  值不变，且**无 retry / sleep / 队列**；
* **默认不接入**：`create_llm_client()` 默认仍是
  `NoopAccountingSink`，不会自动连接数据库；持久化由调用方显式
  注入 `DatabaseLLMAccountingSink` 启用；
* **Idempotency is not guaranteed in this phase**：同一
  observation 被 `record()` 两次会产生两条记录
  （at-least-once / caller-controlled）；`request_id` 允许 NULL，
  不依赖唯一约束伪造幂等；
* **不落库内容**：prompt / messages / SQL / RAG chunks /
  tool arguments / tool results / raw response / headers /
  API key / exception message / stack trace。

---

## 8.15 LLM Usage Persistence Runtime Boundary（Phase 3.10.15）

Phase 3.10.14 解决了"Usage 能不能正确落库"；本小节解决
"落库**在什么线程里发生**"。

```text
Async LLM Request（event loop 线程）
        │
        ▼
   LLMObservation
        │
        ▼
 DatabaseLLMAccountingSink
        │  record()   同步入口（契约不变）
        │  arecord()  异步入口（async 请求路径选用）
        ▼
 LLMUsagePersistenceRuntimeBridge        ← 唯一的运行时边界
        │  await asyncio.to_thread(service.persist, observation)
        ▼  ── 线程边界 ────────────────────────────────
 LLMUsagePersistenceService（同步，逻辑不变）
        ▼
 LLMUsageRepository.create()
        │  with factory() as session, session.begin():
        ▼
 ai_ops.llm_usage_record
```

关键原则：

```text
LLM Business Result
        │
        ├──────────────► caller
        │
        └──────────────► optional persistence（失败不影响上面这条）
```

规则：

1. **Persistence 不属于 LLM Business Result**：持久化成功或失败都不
   改变 `chat()` / `generate()` 的返回值与异常行为；
   `DatabaseLLMAccountingSink` 内部任何异常一律降级为 warning；
2. **Repository Session 不跨线程共享**：bridge 搬运的只有
   `LLMObservation`（frozen 数据），**不搬运** Session / Connection /
   Transaction；worker 线程内部由既有 `session_factory()` 创建新的
   Session（`with factory() as session, session.begin():`），
   commit / rollback 都在该线程内完成；
3. **只有 runtime bridge 建立线程边界**（`asyncio.to_thread`，与项目
   `SqlExecutorService` / `get_inventory` 既有做法一致）：LLM Client /
   Repository / Persistence Service 都不自行 `to_thread`；
4. **不使用 fire-and-forget**：`arecord()` 必须由调用方 `await`
   （禁止 `create_task` / `ensure_future`），避免 task 生命周期、
   shutdown、异常逃逸、数据丢失问题；
5. **不做 retry**：repository 调用次数恒为 1；无 backoff / sleep /
   retry queue；
6. **不做 queue / worker / outbox**：无 Kafka / Redis / RabbitMQ /
   Celery 等任何基础设施；
7. **不做 aggregation**：仍然是 request-level 一行；无 daily /
   monthly / user / project 汇总；
8. **不做 billing**：无 cost / price / currency 落库
   （Cost 仍只在 `accounting.calculate_llm_cost` 计算层）；
9. **默认仍为 `NoopAccountingSink`**：`create_llm_client()` 不传
   `accounting_sink` 时不会连数据库、不会产生线程边界；必须显式注入
   `DatabaseLLMAccountingSink` 才启用持久化；
10. **同步 Contract 不变**：`sink.record(observation)` 仍然是同步调用
    （既有测试与同步调用方零改动），`arecord()` 是 optional 增量；
11. **不新增 Persistence DTO**：继续复用 `LLMObservation` / `LLMUsage` /
    `LLMUsageRecord`；字段白名单与 §8.14 完全一致。

已知限制（本阶段刻意不做）：

* 持久化仍发生在业务返回之前被 await（与 Phase 3.10.14 的先后顺序一致），
  改善的是"不占用 event loop 线程"，不是"从关键路径移除"——
  后者需要 queue / worker / outbox，与 §二 禁止项冲突；
* caller cancellation 落在持久化窗口时，由 persistence boundary
  吸收（`CancelledError` → warning），保证**已完成的** LLM Business
  Result 不被破坏；worker 线程内的事务自然结束
  （独立 Session，无共享资源）。

---

## 8.16 LLM Usage Persistence Idempotency（Phase 3.10.16）

解决"同一个 `request_id` 被重复持久化"的问题；幂等由 **PostgreSQL**
保证，不由应用内存保证。

```text
Repository.create()
    ↓
INSERT INTO ai_ops.llm_usage_record (...)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL
DO NOTHING
RETURNING id
```

```text
Index Name : uq_llm_usage_record_request_id
Schema     : ai_ops
Table      : llm_usage_record
Column     : request_id
Predicate  : request_id IS NOT NULL
Unique     : true
type       : partial unique index（普通 UNIQUE 不满足本语义）
```

Contract：

1. `request_id IS NOT NULL` → 一个 request_id **最多一行**；重复写入
   `DO NOTHING`，Repository 返回 `None`（正常幂等结果，**不是错误**）；
2. `request_id IS NULL` → **没有幂等身份**，不参与幂等，每次持久化都
   允许产生新行（有意设计，不是 bug）；不为伪造幂等 key 生成
   UUID / hash / timestamp / provider+model 合成值；
3. **First-write-wins**：禁止 `DO UPDATE`，重复写入不得修改已存在的
   provider / model / token（不做 conflict resolution / reconciliation）；
4. 不用 `SELECT → IF NOT EXISTS → INSERT`（并发仍会重复），不用
   `IntegrityError + rollback` 作为正常控制流；
5. 幂等逻辑只存在于 **Repository / DB**：Runtime Bridge / Service /
   Sink 不得做 in-memory 去重（无 dict / set / lock / cache ——
   进程重启失效且无法解决多进程并发）；
6. `Base.metadata.create_all()` 对**已存在**的表不会补建新增索引，因此
   `init_db()` 会显式执行幂等 DDL
   （`ensure_request_id_idempotency_index()`）；
7. 已有库存在重复 `request_id` 时：`init_db()` **检测 + 明确报告 + 停止**，
   绝不自动 DELETE / MERGE / UPDATE（不引入 Alembic，不删数据）；
8. 写入字段与 §8.14 完全一致：`request_id / provider / model /
   prompt_tokens / completion_tokens / total_tokens`
   （幂等不得扩大字段集）。

---

## 8.17 LLM Usage Query Read Boundary（Phase 3.10.17）

Usage Persistence 现在有两条互不干扰的路径：

```text
LLM Usage Write:

Observation
 ↓
Accounting
 ↓
Runtime Bridge
 ↓
Persistence
 ↓
PostgreSQL


LLM Usage Read:

PostgreSQL
 ↓
LLMUsageRepository（SQL / Session / Row）
 ↓
LLMUsageRecordRow（db 层内部记录，不是 ORM 对象）
 ↓
LLMUsageQueryService（validate → repository → DTO）
 ↓
LLMUsageRecordView（frozen DTO，只读边界）
```

明确：

```text
Read Boundary
≠ Analytics
≠ Billing
≠ Dashboard
```

规则：

1. **上层不得直接访问 ORM / Session**：不得 `session.query()` /
   `session.execute()` / 直接持有 `LLMUsageRecord` 去读 Usage
   Persistence；只能经 `LLMUsageQueryService`；
2. **Query DTO 安全白名单**：`id / request_id / provider / model /
   prompt_tokens / completion_tokens / total_tokens / created_at`
   ——不含 prompt / messages / response / SQL / RAG / tool /
   API key / password / authorization / database URL /
   Session / Connection / Engine / cost / price / currency；
3. **禁止返回 ORM 对象**：Repository 返回内部 `LLMUsageRecordRow`，
   Service 转成 `LLMUsageRecordView`（frozen dataclass，项目 DTO 约定）；
4. **过滤下推 PostgreSQL**：`request_id / provider / model /
   created_at_from / created_at_to` 全部生成 `WHERE`，
   不在 Python 里取回全表再过滤；
5. **稳定排序**：`ORDER BY created_at DESC, id DESC`
   （`LIMIT/OFFSET` 缺少稳定排序时返回顺序不确定）；
6. **分页参数边界**：`limit` 1~100（默认 50），`offset >= 0`，
   非法参数在进入数据库之前被 `LLMUsageQueryInputError` 拒绝；
7. **时间范围**：`created_at >= from`、`created_at <= to`（含边界）；
   `from > to` 明确 reject（不交换、不修正、不静默返回空）；
   统一 timezone-aware（项目已有约定）；
8. **Session 隔离**：继续"一次查询一个独立 Session"（Repository 管理，
   Service 不创建 Session），读路径不开启写事务；
9. **READ ONLY**：只增加 SELECT，无 INSERT / UPDATE / DELETE；
10. **不做统计 / Cost / 权限**：无 `sum_tokens()` / `daily_usage()` /
    `monthly_usage()` / `LLMCost` / user / role / tenant ACL；
11. **无 HTTP / Dashboard**：本阶段不建 FastAPI Router、不做图表；
12. **不为 provider / model 新建索引**（先观察真实 Query Pattern）。

---

## 8.18 LLM Usage Query Async Runtime Boundary（Phase 3.10.18）

Phase 3.10.17 的 Query Read Boundary 是**同步**的；在 async 业务链路里
直接调用会阻塞 event loop。本阶段只加一层**线程边界**（与 §8.15 的
Persistence Runtime 同构），不改数据库体系：

```text
Synchronous DB Query Layer（保持不变）
        ↓
LLMUsageQueryService（仍然是 def：CLI / Test / sync code 可直接用）
        ↓ asyncio.to_thread（唯一新增的一步）
LLMUsageQueryRuntimeBridge（Async Runtime Boundary）
        ↓
async caller（必须 await，禁止 fire-and-forget）
```

```text
event loop thread
   ↓ asyncio.to_thread()
worker thread
   ↓ session_factory()        ← Session 只在 worker 线程内创建
Repository（同步）
   ↓
PostgreSQL
```

明确：

```text
Repository remains synchronous
Database driver remains unchanged
No AsyncSession introduced
No asyncpg introduced
```

规则：

1. **Bridge 不复制 Query 逻辑**：不写 SQL、不持有 Session，只调用
   `LLMUsageQueryService` 现有方法（Repository = DB，Service = Query
   Contract，Bridge = Async Boundary）；
2. **同步 Service 不变**：`query()` / `list_records()` /
   `get_by_request_id()` 仍是同步 `def`；async 入口是
   `query_async()` / `list_records_async()` / `get_by_request_id_async()`；
3. **Session 不跨线程 / 跨 task 共享**：一次查询 = 一个 worker 执行 =
   一个独立 Session（用完即关闭）；Session 绝不在 event loop 线程创建；
4. **无 fire-and-forget**：无 `create_task` / `ensure_future` /
   background task；调用方必须 `await`；
5. **无进程级状态**：无 global Session / Connection / 结果缓存 /
   LRU / dict / set（本阶段不做缓存）；
6. **错误可观察（与 Persistence 不同）**：Persistence 是 best-effort
   accounting（失败降级为 warning）；Query 是显式读操作——
   `LLMUsageQueryError` / `LLMUsageRepositoryError` **原样传播**给调用方，
   绝不吞掉、绝不返回空结果伪装成功；
7. **Cancellation**：Query 是主动读取请求，caller cancellation **允许
   向调用方传播**（不复制 Persistence 的 absorb 语义）；
8. **READ ONLY**：仍然只有 SELECT（无 INSERT / UPDATE / DELETE）；
9. **Contract 不变**：`LLMUsageQueryFilter` / `LLMUsageRecordView` 字段、
   frozen、timezone-aware `created_at` 全部不变；
10. **Repository SQL 不变**：`build_record_select()` / `list_records()` /
    `get_by_request_id()`、显式 8 列、`ORDER BY created_at DESC, id DESC`、
    request_id 唯一契约、索引全部不变。

---

## 8.19 LLM Usage Aggregation Contract（Phase 3.10.19）

在**不修改数据库、不新增 API、不做 Dashboard、不引入 Billing** 的前提下，
为 Usage 建立纯内存聚合 Contract：

```text
Persistence
    ↓
Idempotency
    ↓
PostgreSQL
    ↓
Query Repository
    ↓
Query Service
    ↓
Query Runtime
    ↓
LLMUsageRecordView
    ↓
Aggregation（backend/app/services/llm_usage_aggregation_service.py）
    ↓
LLMUsageAggregate / 分组结果
```

明确：

```text
Aggregation ≠ Billing
Aggregation ≠ Cost Calculation
Aggregation ≠ Dashboard
Aggregation ≠ API
```

规则：

1. **Pure / Deterministic / In-memory**：无 IO / 无网络 / 无 DB /
   无 SQL / 无 Session / 无 Repository——Aggregation 不知道
   PostgreSQL 的存在；不做缓存、无全局状态；
2. **基础聚合**：`aggregate(records)` → `total_requests` +
   `prompt_tokens` / `completion_tokens` / `total_tokens` 求和；
3. **三个 token 列是独立观测值**：`total_tokens` 直接对 Provider
   原值求和，**绝不重算为 `prompt + completion`**（不修改 Usage
   Contract，§十三）；
4. **NULL ≠ 0（§七）**：token 为 `None` = 未知 / 不可用——
   不计入求和、也绝不当作 0；未知信息通过
   `prompt_tokens_known` / `completion_tokens_known` /
   `total_tokens_known` 显式保留（`False` = 至少一条记录该列未知，
   求和只是已知部分）；空输入 → 零值合法结果（不 None、不抛异常）；
5. **分组**：`aggregate_by_provider()` / `aggregate_by_model()` /
   `aggregate_by_provider_model()`；`None` 维度构成独立分组
   （原样保留，不合并成 unknown 字符串）；分组 DTO 为 frozen tuple，
   **不对外暴露裸 dict**；
6. **确定性排序**：分组按 key 升序，`None` 分组在该维度排在最后；
   同一输入（任意顺序）恒得同一输出——不依赖数据库返回顺序 /
   dict insertion order / 线程完成顺序；
7. **不去重**：两条完全相同的记录 = 两次 Usage；
8. **DTO frozen**：`LLMUsageAggregate` 及分组 DTO 全部 immutable；
9. **无 Cost**：不接 `LLMPricing` / `LLMCost` /
   `calculate_llm_cost()`，无 currency / price / cost 字段
   （Billing 属于后续独立阶段）；
10. **职责分离**：Query Service 保持 `list → List[View]` 不变，
    Aggregation 独立成层；未来 Analytics 由上层组合
    `Query → Aggregation`（Aggregation 本身永远不接触数据库）。

---

## 8.20 LLM Usage Analytics Contract（Phase 3.10.20）

在 Query + Aggregation 之上组合出 **Usage 统计能力**，仍然纯内存：

```text
LLM Request
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
Idempotency
    ↓
PostgreSQL
    ↓
Query Repository
    ↓
Query Service
    ↓
Query Runtime
    ↓
LLMUsageRecordView
    ↓
Usage Aggregation
    ↓
Usage Analytics
```

职责边界（按当前真实实现）：

```text
Query       = 获取 Usage Records（LLMUsageQueryService / QueryRuntime）
Aggregation = 纯内存 sum / count / grouping（LLMUsageAggregationService）
Analytics   = 组合多个 Aggregation View（LLMUsageAnalyticsService）
```

入口：`backend/app/services/llm_usage_analytics_service.py`

```text
LLMUsageAnalyticsService
    ├── snapshot(records)             → LLMUsageAnalyticsSnapshot
    ├── summary(records)              → LLMUsageAggregate
    ├── by_provider(records)          → tuple[ProviderUsageAggregate, ...]
    ├── by_model(records)             → tuple[ModelUsageAggregate, ...]
    └── by_provider_model(records)    → tuple[ProviderModelUsageAggregate, ...]
```

### 当前真实 DTO（以代码为准）

```text
LLMUsageAnalyticsSnapshot（frozen）
    ├── total:            LLMUsageAggregate            （复用 3.10.19 DTO）
    ├── by_provider:      tuple[ProviderUsageAggregate, ...]
    ├── by_model:         tuple[ModelUsageAggregate, ...]
    └── by_provider_model: tuple[ProviderModelUsageAggregate, ...]

LLMUsageAggregate（frozen，3.10.19）
    total_requests / prompt_tokens / completion_tokens / total_tokens
    prompt_tokens_known / completion_tokens_known / total_tokens_known
```

Analytics **不重复定义** `LLMUsageAggregate` 等 DTO（summary 直接复用），
唯一新增的 `LLMUsageAnalyticsSnapshot` 承载"一次快照 → 四个视图"的
一致性语义。

### Snapshot（§十 / §十一）

```text
Iterable[LLMUsageRecordView]
        ↓  _materialize（generator 只消费一次）
immutable tuple snapshot
        ↓
summary / provider / model / provider+model（四个视图共享同一份快照）
```

**generator 只被消费一次**——四个视图不会因 iterable 二次消费而产生
`total_requests` 与各分组 requests 的不一致。

### 规则

1. **Analytics 不重复实现聚合算法**：sum / group-by / 排序 / NULL
   处理全部复用 `LLMUsageAggregationService`（§五 / §七 ~ §九）；
2. **Analytics 不接触数据库**：不创建 Session / 不执行 SQL /
   不访问 Repository / 不访问 PostgreSQL（§四）；
3. **NULL ≠ 0**（沿用 3.10.19 Contract）：token 为 `None` = 未知，
   不计入求和、也绝不当作 0；`*_known = False` 表示"该字段存在
   NULL / unknown，当前 sum 只是已知值之和"；
4. **三个 token 列独立观测**：`total_tokens` 直接求和，不重算为
   `prompt + completion`（§十三）；
5. **确定性排序**：分组按 key 升序、`None` 分组在该维度排在最后；
   同一输入（任意顺序）恒得同一输出，不依赖 dict insertion order /
   输入顺序 / 线程顺序；
6. **一致性**：对同一 snapshot，Σ provider / Σ model /
   Σ provider+model 的 requests 与 token 都回溯到 `summary`；
   `None` provider/model 也作为独立分组参与统计；
7. **不去重**：两条完全相同的记录 = 两次 Usage；
8. **无 Cost**：不接 `LLMPricing` / `LLMCost`，无 price / currency /
   cost 字段（Usage Analytics ≠ Cost Calculation ≠ Billing）；
9. **无时间聚合**：不做 hour / day / week / month / time_series；
10. **错误 Contract**：Analytics 输入非法（None / 不可迭代 / 字符串）
    → `LLMUsageAnalyticsInputError`；底层 `LLMUsageAggregationError`
    族**原样传播**，不做无意义包装；
11. **不可变 / 无全局状态**：Snapshot 与全部 DTO frozen，
    分组结果为 tuple，无缓存 / 无 singleton。

### Database / Test Boundary

```text
Analytics Service:  无 Session / 无 SQL / 无 Repository / 无 PostgreSQL
测试:               DB Access = 0、DB Writes = 0、Network = 0
                    （纯内存，直接构造 LLMUsageRecordView，无 RUN_DB_TESTS）
```

明确：

```text
Usage Analytics  ≠  Cost Calculation  ≠  Billing  ≠  Dashboard  ≠  API
```

---

## 8.21 Usage Analytics Application Read Facade（Phase 3.10.21）

在 Query / Aggregation / Analytics 之上增加**应用层只读入口**：

```text
LLM Request
  ↓
Observation
  ↓
Accounting
  ↓
Persistence
  ↓
Idempotency
  ↓
PostgreSQL
  ↓
Query Repository        （负责数据库读取）
  ↓
Query Service           （负责 Query Contract / Filter / Pagination）
  ↓
Query Runtime           （负责 async → sync DB boundary）
  ↓
LLMUsageRecordView
  ↓
Usage Aggregation       （纯内存 sum / count / grouping）
  ↓
Usage Analytics         （Analytics / Aggregation composition）
  ↓
Usage Analytics Read Facade（Application Read Boundary）
  ↓
Future API / Dashboard / Admin / AI Ops   ← 仅未来消费者，本阶段未实现
```

入口：`backend/app/services/llm_usage_analytics_facade.py`

```text
LLMUsageAnalyticsReadFacade(query_runtime, analytics_service=None)

async query_snapshot(query_filter=None)   → LLMUsageAnalyticsSnapshot
async query_summary(query_filter=None)    → LLMUsageAggregate
async query_by_provider(query_filter=None)
async query_by_model(query_filter=None)
async query_by_provider_model(query_filter=None)
```

### 四层职责（Facade 之前已各司其职）

```text
Query Repository         → 数据库读取
Query Service            → Query Contract / Filter / Pagination
Query Runtime            → async → sync DB boundary
Analytics Service        → Analytics / Aggregation composition
Analytics Read Facade    → Application Read Boundary（只组合，不计算）
```

**Facade 不负责数据库访问。**

### 依赖边界

```text
允许：Facade → LLMUsageQueryRuntime（唯一 DB 途经）
           → LLMUsageAnalyticsService（Snapshot 唯一实现）

禁止：Facade → Repository
           → SQLAlchemy Session / Engine / Connection / SQL / PostgreSQL
```

构造校验：`query_runtime` 为 None 或缺少 `query_async()` → `TypeError`；
`analytics_service=None` → 默认 `LLMUsageAnalyticsService()`。

### Snapshot 语义（一次查询 → 完整四视图）

```text
query_snapshot(query_filter)
    ↓ Runtime 查询 1 次（不是四次数据库查询）
records: list[LLMUsageRecordView]
    ↓ LLMUsageAnalyticsService.snapshot(records)
LLMUsageAnalyticsSnapshot（frozen）
    ├── total              （LLMUsageAggregate）
    ├── by_provider        （tuple[ProviderUsageAggregate, ...]）
    ├── by_model           （tuple[ModelUsageAggregate, ...]）
    └── by_provider_model  （tuple[ProviderModelUsageAggregate, ...]）
```

Facade 不重新实现 Snapshot（无 `tuple(records)` 物化逻辑），
Snapshot 的唯一实现仍是 `LLMUsageAnalyticsService`。

### Pagination 语义

```text
Facade Analytics = 当前 Query Filter 对应记录集合的 Analytics
```

例如 `limit = 100` → Analytics 只代表查询得到的 **100 条记录**，
**不是整个数据库的全局统计**。本阶段没有
`COUNT(*)` / `global_total` / `total_count`。

`LLMUsageQueryFilter` **原样传递**（含 limit / offset），
Facade 不创建第二套 Filter / PageSize 常量。

### 便捷方法（convenience read methods）

`query_summary()` / `query_by_provider()` / `query_by_model()` /
`query_by_provider_model()` 属于**单视图快捷方式**（各自一次
Runtime 查询）。完整 Analytics 推荐使用 `query_snapshot()`——
一次 Query 即可得到完整四视图。

### 错误传播

```text
Runtime error   → Facade → caller（原样）
Analytics error → Facade → caller（原样）
```

Facade 不吞异常、不包装、不转换成 None。

### Immutability / Security Boundary

* 返回值仍是既有 frozen DTO（`LLMUsageAnalyticsSnapshot` /
  `LLMUsageAggregate` / `ProviderUsageAggregate` /
  `ModelUsageAggregate` / `ProviderModelUsageAggregate`）与 tuple，
  Facade 绝不重新包装成 dict / list；
* Facade 返回的数据不包含：API Key / Password / Database URL /
  Connection String / Authorization Header / SQLAlchemy Session /
  Connection / Engine / Raw SQL——Facade 只是 application read
  boundary，数据仍是 §8.19 的 Usage 白名单字段；
* 无缓存 / 无全局状态 / 无 fire-and-forget（调用方必须 `await`）。

---

# 9. Prompt Architecture

Prompt 不应该散落在 Python 代码中。

建议：

```text
backend/app/prompts/
├── system.txt
├── chat.txt
├── rag.txt
└── tool.txt
```

后续可以进一步拆分：

```text
prompts/
├── system/
├── rag/
├── inventory/
├── purchase/
└── outbound/
```

Prompt 与业务代码分离。

---

# 10. RAG Architecture

RAG 用于解决企业知识问答问题。

例如用户：

```text
采购入库应该怎么操作？
```

系统不应该单纯依赖 LLM 自己回答。

应该：

```text
用户问题
   ↓
Embedding
   ↓
向量检索
   ↓
获取相关企业文档
   ↓
构建 Context
   ↓
LLM
   ↓
答案
```

---

# 11. RAG 数据流程

## 11.1 文档入库

```text
PDF / DOCX / TXT / MD
          ↓
      Document Parser
          ↓
        Chunking
          ↓
       Embedding
          ↓
 PostgreSQL + pgvector
```

当前实现（Phase 3.5.2）：`backend/app/services/knowledge_ingestion_service.py`
提供 `ingest_one(file_path)` 单文件导入（Parser → Chunker → EmbeddingClient →
单事务写库）；按 `content_hash` 检测重复文档（重复时不调用 Embedding API）。
批量导入 / 后台任务与向量检索（Retrieval）为后续 Phase。

每个 Chunk 至少保存：

```text
id
document_id
content
embedding
source
metadata
created_at
```

Metadata 可以包含：

```text
document_name
document_type
department
warehouse
version
page
section
```

---

# 12. RAG 查询流程

用户：

```text
越南仓怎么进行盘点？
```

执行：

```text
Question
   ↓
Embedding
   ↓
Vector Search
   ↓
Top K Documents
   ↓
Context
   ↓
LLM
   ↓
Answer
```

如果检索结果不足：

```text
不要编造企业制度。
```

应该明确告诉用户：

```text
知识库中没有找到足够的信息。
```

---

# 13. Tool Calling Architecture

Tool Calling 用于获取实时业务数据。

例如：

```text
get_inventory
get_purchase_orders
get_outbound_orders
```

---

## 13.1 Tool 结构

每个 Tool 应包含：

```text
Tool Name
Description
Input Schema
Validation
Execution
Result Schema
Error Handling
Permission
Logging
```

例如：

```text
Tool:
get_inventory

Description:
查询指定物料在指定仓库的库存。

Parameters:
material_code
warehouse_code

Return:
material_code
warehouse_code
qty
unit
```

---

# 14. Tool 调用流程

```text
User
 ↓
FastAPI
 ↓
AI Service
 ↓
LLM
 ↓
判断需要 Tool
 ↓
Tool Calling
 ↓
Tool Registry
 ↓
Permission Check
 ↓
WMS API
 ↓
WMS Business Logic
 ↓
Return Structured Data
 ↓
LLM
 ↓
Natural Language Answer
```

---

# 15. Tool Registry

所有 Tool 统一注册。

例如：

```text
tools/
├── inventory.py
├── purchase.py
├── outbound.py
└── registry.py
```

Registry：

```python
TOOLS = [
    get_inventory,
    get_purchase_orders,
    get_outbound_orders,
]
```

未来可以扩展：

```text
get_stocktake_task
get_material_info
get_transfer_orders
get_production_orders
get_quality_records
```

---

# 16. WMS / ERP Integration

AI 系统不直接实现 WMS 业务逻辑。

例如：

```text
AI Assistant
      ↓
Tool
      ↓
WMS API
      ↓
WMS Service
      ↓
Database
```

如果现有 WMS 已经存在 API：

```text
优先复用现有 API。
```

不要重新实现：

```text
库存计算
单据状态
业务校验
权限
库存扣减
库存锁定
```

---

# 17. WMS API Adapter

建议增加 Integration Layer：

```text
backend/app/integrations/
├── wms/
│   ├── client.py
│   ├── inventory.py
│   ├── purchase.py
│   └── outbound.py
└── erp/
```

例如：

```python
class WMSClient:

    async def get_inventory(
        self,
        material_code: str,
        warehouse_code: str
    ):
        ...
```

Tool 不直接处理 HTTP 细节。

正确：

```text
Tool
 ↓
WMS Client
 ↓
HTTP API
```

而不是：

```text
Tool
 ↓
requests.get(...)
```

到处散落。

---

# 18. PostgreSQL Architecture

PostgreSQL 主要负责：

```text
系统数据
会话数据
消息数据
RAG 文档
Vector
系统配置
日志
```

建议初期使用：

```text
PostgreSQL
+
pgvector
```

而不是一开始就引入：

```text
Qdrant
Milvus
Elasticsearch
Redis
Kafka
```

当系统规模确实需要时再拆分。

---

# 19. 数据库逻辑划分

建议：

```text
conversation
conversation_message

knowledge_document
knowledge_chunk

tool_call_log

system_config
```

后续：

```text
user
role
permission
audit_log
agent_execution
workflow_execution
```

---

# 20. Conversation Architecture

聊天系统需要保存上下文。

例如：

```text
User:
查询 A001 库存

AI:
A001 越南仓库存 1250。

User:
其中多少是待检？

AI:
...
```

第二个问题需要结合第一轮上下文。

因此保存：

```text
conversation
    ↓
messages
    ↓
role
content
timestamp
metadata
```

---

# 21. Context Management

不能无限制把历史聊天全部发送给 LLM。

采用：

```text
Recent Messages
+
Conversation Summary
+
Current Question
+
RAG Context
+
Tool Result
```

例如：

```text
System Prompt

Conversation Summary

最近 5 条消息

Retrieved Knowledge

Tool Result

Current Question
```

---

# 22. Agent Architecture

Agent 不属于 MVP 第一阶段。

MVP：

```text
User
 ↓
LLM
 ↓
RAG / Tool
 ↓
Answer
```

后续：

```text
User
 ↓
Agent
 ↓
Planning
 ↓
Tool 1
 ↓
Tool 2
 ↓
Tool 3
 ↓
Result
```

例如：

```text
用户：

帮我分析越南仓 A001 为什么库存不足。
```

Agent 可以：

```text
1. 查询库存
2. 查询最近采购订单
3. 查询生产领料
4. 查询销售出库
5. 综合分析
```

---

# 23. LangGraph Architecture

LangGraph 在 Agent 复杂后再引入。

例如：

```text
START
  ↓
Analyze Question
  ↓
Query Inventory
  ↓
Query Purchase
  ↓
Query Outbound
  ↓
Analyze
  ↓
Generate Answer
  ↓
END
```

LangGraph 不应该为了“使用 AI 技术”而强行加入 MVP。

只有当流程出现：

* 多步骤
* 状态管理
* 循环
* 条件分支
* 人工确认
* 多 Tool
* 长流程

时再引入。

---

# 24. Permission Architecture

权限必须位于 AI 与企业数据之间。

```text
User
 ↓
AI
 ↓
Tool
 ↓
Permission Check
 ↓
WMS API
```

例如：

```text
用户 A：

查询越南仓库存
```

系统检查：

```text
用户 A
    ↓
是否允许访问 VN01？
    ↓
Yes
    ↓
执行
```

如果：

```text
No
```

直接拒绝 Tool 调用。

不能让 LLM 自己决定用户是否有权限。

---

# 25. Read / Write Separation

系统严格区分：

```text
Read
Write
```

---

## 25.1 Read

例如：

```text
查询库存
查询采购订单
查询出库单
查询盘点任务
```

经过权限验证后可以自动执行。

---

## 25.2 Write

例如：

```text
创建出库单
修改库存
提交盘点
审核单据
删除数据
```

默认必须：

```text
用户确认
+
权限验证
+
业务校验
+
执行
+
审计日志
```

例如：

```text
用户：
帮我创建 A001 调拨单 100 个。

AI：
准备创建：

物料：A001
来源仓：VN01
目标仓：VN02
数量：100

请确认是否执行？

用户：
确认。

↓

Permission Check

↓

Business Validation

↓

WMS API

↓

Audit Log
```

---

# 26. Security Boundary

系统安全边界：

```text
┌─────────────────────┐
│        LLM          │
│                     │
│ 不可信决策组件       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│       Tool          │
│                     │
│ 参数验证             │
│ 权限验证             │
│ 数据验证             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      WMS API        │
│                     │
│ 企业业务规则         │
│ 企业权限             │
│ 数据校验             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      Database       │
└─────────────────────┘
```

核心原则：

> LLM 可以决定“调用哪个能力”，但不能决定“用户有没有权限”和“业务是否允许执行”。

---

# 27. Error Handling

AI 系统必须区分：

```text
LLM Error
RAG Error
Tool Error
WMS API Error
Permission Error
Business Error
Database Error
```

例如 WMS API 返回：

```text
库存查询失败
```

AI 不得生成：

```text
库存大约是 1000。
```

应该返回真实错误：

```text
当前无法获取 A001 的库存信息，请稍后重试。
```

---

# 28. Logging

至少记录：

```text
request_id
user_id
conversation_id
question
selected_tool
tool_arguments
tool_result_status
latency
error
created_at
```

涉及敏感信息时：

```text
禁止完整记录密码
禁止记录 API Key
禁止无控制地记录敏感业务数据
```

---

# 29. API Design

MVP API：

```text
GET  /api/health

POST /api/chat

GET  /api/conversations

GET  /api/conversations/{conversation_id}

POST /api/knowledge/documents

POST /api/knowledge/search
```

未来：

```text
GET  /api/tools

POST /api/tools/{tool_name}

GET  /api/audit/logs
```

---

# 30. Chat API

请求：

```json
{
  "conversation_id": "xxx",
  "message": "越南仓 A001 还有多少库存？"
}
```

响应：

```json
{
  "conversation_id": "xxx",
  "answer": "A001 在越南仓当前库存为 1250 个。",
  "tool_calls": [
    {
      "tool": "get_inventory",
      "status": "success"
    }
  ]
}
```

---

# 31. 推荐项目结构

```text
wms-ai-assistant/
│
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
│
├── docs/
│   ├── architecture.md
│   ├── requirements.md
│   ├── api.md
│   ├── database.md
│   ├── security.md
│   └── decisions/
│
├── backend/
│   └── app/
│       ├── main.py
│       ├── config.py
│       │
│       ├── api/
│       │   ├── chat.py
│       │   ├── health.py
│       │   └── knowledge.py
│       │
│       ├── services/
│       │   ├── chat_service.py
│       │   ├── llm_service.py
│       │   ├── rag_service.py
│       │   └── tool_service.py
│       │
│       ├── llm/
│       │   ├── client.py
│       │   └── models.py
│       │
│       ├── rag/
│       │   ├── loader.py
│       │   ├── splitter.py
│       │   ├── embedding.py
│       │   └── retriever.py
│       │
│       ├── tools/
│       │   ├── registry.py
│       │   ├── inventory.py
│       │   ├── purchase.py
│       │   └── outbound.py
│       │
│       ├── integrations/
│       │   ├── wms/
│       │   │   ├── client.py
│       │   │   ├── inventory.py
│       │   │   ├── purchase.py
│       │   │   └── outbound.py
│       │   │
│       │   └── erp/
│       │
│       ├── db/
│       │   ├── database.py
│       │   ├── models.py
│       │   └── repositories/
│       │
│       ├── prompts/
│       │   ├── system.txt
│       │   ├── chat.txt
│       │   ├── rag.txt
│       │   └── tool.txt
│       │
│       ├── schemas/
│       │
│       └── utils/
│
├── knowledge/
│
├── tests/
│   ├── test_chat.py
│   ├── test_rag.py
│   └── test_tools.py
│
└── scripts/
```

---

# 32. 请求处理流程

## 32.1 普通问题

```text
User
 ↓
POST /api/chat
 ↓
Chat Service
 ↓
LLM
 ↓
Answer
 ↓
User
```

例如：

```text
什么是 WMS？
```

---

## 32.2 企业知识问题

```text
User
 ↓
Chat Service
 ↓
LLM / Router
 ↓
RAG
 ↓
Vector Search
 ↓
Knowledge Context
 ↓
LLM
 ↓
Answer
```

例如：

```text
越南仓盘点流程是什么？
```

---

## 32.3 实时业务数据问题

```text
User
 ↓
Chat Service
 ↓
LLM
 ↓
Tool Calling
 ↓
Permission
 ↓
WMS API
 ↓
Tool Result
 ↓
LLM
 ↓
Answer
```

例如：

```text
A001 越南仓还有多少？
```

---

# 33. RAG 与 Tool 的选择

系统需要区分：

### 知识类问题

```text
怎么操作？
什么规则？
流程是什么？
制度是什么？
```

使用：

```text
RAG
```

### 实时数据问题

```text
现在库存多少？
有哪些采购订单？
今天出了多少货？
```

使用：

```text
Tool
```

### 混合问题

例如：

```text
为什么越南仓 A001 库存不足？
```

未来可以：

```text
RAG
+
Tool
+
Agent
```

---

# 34. MVP 不采用的架构

当前阶段不采用：

```text
Microservices
Kubernetes
Kafka
Redis Cluster
Milvus Cluster
Qdrant Cluster
Multi-Agent
复杂 LangGraph
MCP 全面接入
复杂 Memory
自动决策系统
```

原因：

> 当前重点是验证 AI + WMS 的核心业务闭环，而不是搭建复杂基础设施。

---

# 35. 部署架构

MVP 建议单体部署。

```text
                    Internet / LAN
                          │
                          ▼
                    ┌──────────┐
                    │  Nginx   │
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │ FastAPI  │
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        PostgreSQL      LLM       WMS API
          + pgvector
```

后续规模增长后再考虑：

```text
API Service
AI Service
RAG Service
Tool Service
Worker
Vector DB
Redis
```

---

# 36. 可扩展性设计

虽然 MVP 保持简单，但需要预留扩展点。

## LLM

```text
LLMService
```

可以替换不同模型。

## RAG

```text
RAGService
```

可以替换：

```text
pgvector
Qdrant
Milvus
```

## WMS

```text
WMSClient
```

可以适配：

```text
REST API
GraphQL
内部 API
ERP API
```

## Agent

在：

```text
AI Service
```

层增加：

```text
AgentService
```

不需要重构整个系统。

---

# 37. 技术演进路线

## Phase 1

```text
Python
FastAPI
LLM API
```

实现：

```text
AI Chat
```

---

## Phase 2

加入：

```text
PostgreSQL
Embedding
pgvector
RAG
```

实现：

```text
企业知识问答
```

---

## Phase 3

加入：

```text
Tool Calling
WMS API
```

实现：

```text
库存查询
采购查询
出库查询
```

---

## Phase 4

加入：

```text
Agent
```

实现：

```text
多步骤任务
```

---

## Phase 5

加入：

```text
LangGraph
```

实现：

```text
复杂业务流程
状态管理
人工确认
多步骤工作流
```

---

## Phase 6

加入：

```text
Permission
Audit
Write Operations
```

实现：

```text
AI 执行业务操作
```

---

# 38. 一个完整示例

用户：

```text
帮我查一下越南仓 A001 的库存，并告诉我最近有没有采购订单。
```

系统：

```text
                    User
                      │
                      ▼
                  FastAPI
                      │
                      ▼
                 ChatService
                      │
                      ▼
                     LLM
                      │
             ┌────────┴────────┐
             ▼                 ▼
      get_inventory      get_purchase_orders
             │                 │
             ▼                 ▼
          WMS API           WMS API
             │                 │
             └────────┬────────┘
                      ▼
                 Tool Results
                      │
                      ▼
                     LLM
                      │
                      ▼
                  Final Answer
```

最终：

```text
A001 越南仓当前库存为 1250 个。

最近采购订单：
PO20260918001：500 个
PO20260919002：1000 个

其中 PO20260918001 当前状态为待入库。
```

这里的数据全部来自 Tool/WMS API。

LLM 只负责：

```text
理解问题
选择 Tool
组织结果
生成自然语言
```

不负责：

```text
自己猜库存
自己计算业务状态
自己修改数据库
自己绕过权限
```

---

# 39. 架构决策

## ADR-001：使用 Python

原因：

* AI 生态成熟
* LangChain/LangGraph 支持
* RAG 生态成熟
* LLM SDK 丰富
* 用户已有 Python 基础

---

## ADR-002：使用 FastAPI

原因：

* Python 原生异步支持
* API 开发简单
* Pydantic 数据验证
* 适合 AI API 服务
* 后续容易容器化

---

## ADR-003：MVP 使用 PostgreSQL + pgvector

原因：

* 已有 PostgreSQL
* 降低基础设施复杂度
* 业务数据和向量数据可以统一管理
* MVP 数据量通常不需要独立向量数据库

---

## ADR-004：LLM 不直接访问数据库

原因：

* 安全
* 权限
* 业务规则
* 审计
* 防止错误操作

统一：

```text
LLM
 ↓
Tool
 ↓
WMS API
```

---

## ADR-005：MVP 不使用 Agent

原因：

Agent 会增加：

```text
复杂度
调试成本
Token 消耗
不确定性
```

先验证：

```text
LLM
+
RAG
+
Tool Calling
```

成功后再增加 Agent。

---

## ADR-006：MVP 不使用微服务

原因：

当前项目规模较小。

优先：

```text
单体应用
```

而不是：

```text
多个微服务
```

---

# 40. 开发原则

遵循：

```text
Simple First
        ↓
Working MVP
        ↓
Real WMS Integration
        ↓
Collect Feedback
        ↓
Agent
        ↓
LangGraph
        ↓
Enterprise Deployment
```

不要：

```text
先搭建复杂架构
        ↓
开发几个月
        ↓
最后才验证业务价值
```

---

# 41. 当前 MVP 架构边界

当前只要求：

```text
                    ┌───────────────┐
                    │     User      │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │    FastAPI    │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │  AI Service   │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │      LLM      │
                    └───────┬───────┘
                       ┌────┴────┐
                       ↓         ↓
                     RAG       Tools
                       │         │
                       ↓         ↓
                  PostgreSQL   WMS API
```

暂时不加入：

```text
Agent
LangGraph
MCP
复杂权限
写操作
多智能体
微服务
Kubernetes
```

---

# 42. 当前开发优先级

严格按照以下顺序：

```text
1. 项目骨架
        ↓
2. FastAPI
        ↓
3. Health Check
        ↓
4. LLM API
        ↓
5. Chat API
        ↓
6. PostgreSQL
        ↓
7. Embedding
        ↓
8. RAG
        ↓
9. Tool Calling
        ↓
10. WMS API
        ↓
11. Inventory Tool
        ↓
12. Purchase Tool
        ↓
13. Outbound Tool
        ↓
14. Agent
        ↓
15. LangGraph
        ↓
16. Permission
        ↓
17. Write Operations
        ↓
18. Private Deployment
```

每一步完成后必须：

```text
运行
 ↓
测试
 ↓
确认
 ↓
再进入下一阶段
```

---

# 43. 最终架构目标

最终系统希望形成：

```text
                    WMS AI Assistant
                           │
              ┌────────────┼────────────┐
              │            │            │
             RAG         Tools        Agent
              │            │            │
              │            │        LangGraph
              │            │            │
              └────────────┼────────────┘
                           │
                          LLM
                           │
                     Permission
                           │
                       WMS / ERP
                           │
                       Enterprise
```

核心思想：

> LLM 是“大脑”，RAG 是“知识库”，Tool 是“手”，WMS/ERP API 是“业务执行系统”，权限和业务规则是“安全边界”。

系统不是重新做一个 WMS，而是在现有 WMS/ERP 之上增加一层 AI 能力。

---

# 44. CodeBuddy 开发规则

CodeBuddy 开始编写代码前必须：

1. 阅读 `AGENTS.md`
2. 阅读 `docs/requirements.md`
3. 阅读 `docs/architecture.md`
4. 理解当前阶段
5. 不提前实现未来阶段功能
6. 不擅自修改架构原则
7. 不直接连接 WMS 数据库
8. 不在代码中硬编码 API Key
9. 每次只实现一个明确功能
10. 实现后运行测试
11. 发现架构冲突时先说明，不要自行改变架构

当前阶段优先实现：

```text
Phase 1:
项目骨架
+
FastAPI
+
Health Check
+
LLM API
+
最简单 Chat API
```

完成 Phase 1 后，再进入 RAG。

---

# 45. Definition of Done

一个功能只有满足以下条件才算完成：

```text
代码完成
+
类型/参数正确
+
异常处理
+
测试通过
+
API 可运行
+
文档同步
+
没有破坏现有功能
```

对于 AI 功能，还需要验证：

```text
正常问题
异常问题
空参数
错误参数
LLM Error
Tool Error
WMS Error
```

---

# 46. 最终原则

本项目遵循以下原则：

```text
安全 > 正确性 > 业务规则 > 架构一致性
> 可维护性 > 简单性 > 性能 > 开发便利
```

最重要的原则：

> 不为了使用 AI 技术而使用 AI 技术。

> 不为了使用 Agent 而使用 Agent。

> 不为了使用 LangGraph 而使用 LangGraph。

> 先解决真实 WMS/ERP 问题，再逐步增加 AI 能力。

最终目标不是做一个“聊天机器人”，而是：

```text
自然语言
    ↓
AI 理解
    ↓
企业知识
    ↓
企业实时数据
    ↓
企业业务能力
    ↓
安全地辅助用户完成工作
```
