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

## 8.22 Usage Analytics HTTP Read API（Phase 3.10.22，已实现）

在 Usage Analytics Read Facade 之上增加**只读 HTTP Application API**：

```text
HTTP GET /api/usage/analytics
  ↓
Usage Analytics API（只读 Router，仅 HTTP / Schema / 异常映射）
  ↓
LLMUsageAnalyticsReadFacade（§8.21 Application Read Boundary）
  ↓
LLMUsageQueryRuntimeBridge（async → sync DB boundary）
  ↓
LLMUsageQueryService → LLMUsageRepository
  ↓
LLMUsageAnalyticsService（Snapshot 唯一实现）
  ↓
PostgreSQL
```

### 契约要点

* 端点：`GET /api/usage/analytics`（沿用统一 `/api` prefix，
  `include_router(usage.router, prefix="/api", tags=["usage"])`）；
* HTTP Query 参数与 `LLMUsageQueryFilter` 字段一一映射
  （request_id / provider / model / created_at_from / created_at_to /
  limit / offset），复用 Filter 既有校验，不定义第二套 Filter
  或分页常量；
* 响应为 `LLMUsageAnalyticsSnapshot` 四视图的只读投影
  （API 层 Pydantic DTO 镜像；frozen Domain DTO 不变）；
* 分页语义保持 §8.21：Analytics = 当前 Filter 命中的本页记录集合，
  无 global_total / COUNT(*)；
* 错误映射沿用项目规范：Filter 输入非法 → 422；Repository
  故障（含 DB 未配置）→ 502；Analytics / Aggregation 内部输入
  错误 → 500；未知异常原样上抛（Starlette 兜底 500）。

### 依赖与安全边界

```text
允许：HTTP API → LLMUsageAnalyticsReadFacade（唯一下游入口）
禁止：HTTP API → Repository / Session / Engine / SQL / PostgreSQL
禁止：HTTP API → LLM 调用 / API Key / 数据库连接串 / DB 写操作
```

详细 Contract（参数映射 / Response 结构 / 错误映射 / 依赖注入 /
测试策略 / 非目标）：
`docs/evaluation/Phase 3.10.22 — HTTP Read API Contract.md`。

状态：**已实现**（Step 2：`backend/app/api/usage.py` +
`tests/test_usage_analytics_api.py`；实现与 Contract 一致）。

## 8.23 Tool Execution Boundary + Tool Selection Unification（Phase 3.11）

> Step 1（现状勘察）：`docs/evaluation/Phase 3.11 — Tool Execution Boundary.md`
> Step 2（执行边界）：`docs/evaluation/Phase 3.11 Step 2 — Tool Execution Boundary.md`
> Step 3（选择统一）：`docs/evaluation/Phase 3.11 Step 3 — Tool Selection Unification.md`
> Step 4（多参数契约）：`docs/evaluation/Phase 3.11 Step 4 — Multi-Parameter Tool Argument Contract.md`
> Step 5（第二个真实 Tool）：`docs/evaluation/Phase 3.11 Step 5 — Multiple Real Tools.md`
> Step 6（参数提取边界）：`docs/evaluation/Phase 3.11 Step 6 — Tool Argument Extraction Boundary.md`
> Step 7（契约清理 + 边界锁定）：`docs/evaluation/Phase 3.11 Step 7 — Tool Boundary Contract.md`
>   → 职责矩阵 / Contract C1–C12 见 §8.25

### 当前真实 Tool 执行链（链路 A = Orchestrator，Step 3 之后）

```text
Question
  ↓
AIOrchestratorService.execute
  ↓
AIRouterService.route（Rule-first：TOOL capability 匹配；LLM fallback；兜底 RAG）
  ↓
RouteDecision
  ├── RAG
  ├── TOOL + tool_name          ← Phase 3.11 Step 3：Tool 选择的唯一来源
  │       ↓
  │   AIOrchestratorService._run_tool
  │       ├── tool_name = decision.tool_name（未携带 → RouteError，不猜）
  │       ├── ToolArgumentExtractor.extract（Phase 3.11 Step 6：参数提取边界；
  │       │      参数 = tool_name + question + Tool 声明字段）
  │       ↓
  │   ToolExecutionService.execute（Phase 3.11 Step 2：应用层执行边界）
  │       ├── capability 校验（工具白名单；被拒 → Handler 0 次调用）
  │       ↓
  │   ToolRegistry.execute（唯一执行权威，不变）
  │       ├── validate_arguments（Argument Validation）
  │       ├── Handler
  │       └── 异常归一化
  │       ↓
  │   ToolResult（原样返回）→ AIOrchestrationResult → ChatResponse
  │
  └── TEXT_TO_SQL
```

链路 B（`POST /api/chat/with-tools`，ToolChatService，LLM function calling
多步循环）：Phase 3.11 Step 9 起执行点已改经
`ToolExecutionService`（**执行边界统一**），Step 10 起项目级 capability /
project_id 也在该边界生效（提供可选 `project_id`；未提供则不限制），
Step 11 起注册**真实只读** `get_inventory`（Mock Tools 仅供测试）。
**Orchestration 仍未合并**（不经过 Router / Extractor）；真实
`get_work_order` 接入仍 Deferred。
勘察与决策：§8.26；迁移：§8.27；项目能力：§8.28；真实 Tool：§8.29；
失败契约：§8.30；执行上下文：§8.31；执行记录：§8.32；观测出口：§8.33；
内存收集器：§8.34。

### 真实 Tool 清单

```text
get_inventory（真实，Phase 3.7.12）  —— 只读 PostgreSQL SELECT（READ ONLY 事务 + 绑定参数）
get_work_order（真实，Phase 3.11.5） —— 只读 PostgreSQL SELECT（READ ONLY 事务 + 绑定参数）
get_inventory（Mock，Phase 3.6.1）   —— 硬编码 quantity=1000，无 IO（**仅测试**注入；生产 Registry 已换真实版，Step 11）
get_work_order（Mock，Phase 3.6.1）  —— 硬编码 status=RELEASED，无 IO（**仅测试**注入；真实版接入 = Deferred）
```

### 已确立的边界（保持不变）

```text
ToolRegistry.execute = 全项目唯一 Tool 执行入口
    （Schema 校验 → Handler → 异常归一化 → ToolResult）
ToolExecutionService（backend/app/services/tool_execution_service.py）
    = 应用层执行边界（capability 校验 + 委派 Registry；无 Handler / DB / LLM 依赖）
Router 暴露的 Tool 元数据仅 name / description / aliases（不含 handler）
Tool 无写操作 / 无任意 SQL / 无任意 HTTP / 无 shell / 无文件访问
Tool 不调用 LLM；Tool 不调用其它 Tool
```

### RouteDecision（Phase 3.11 Step 3）

```text
RouteDecision(route, confidence, reason, source, tool_name=None)

TOOL          → tool_name = 被选中的 Tool 名称（tool_match 规则命中）
RAG           → tool_name = None
TEXT_TO_SQL   → tool_name = None
LLM fallback 判 TOOL → tool_name = None（不猜；由上层显式拒绝执行）
```

### Argument Contract（Phase 3.11 Step 4：多参数）

```text
Question
  ↓ AI Router
RouteDecision(tool_name="get_inventory")
  ↓ AIOrchestrator._run_tool
ToolArgumentExtractor.extract（Phase 3.11 Step 6 迁出；确定性规则；
                              非 NLP / 非 LLM）
  ├── warehouse_code：显式仓库表达（"A01 仓库" / "仓库 A01" /
  │                   "warehouse A01" / "warehouse_code=A01"）
  └── material_code：第一个不落在仓库表达区间内的合法字面量
  ↓ arguments（仅含匹配到的字段）
ToolExecutionService（capability；arguments 原样透传）
  ↓
ToolRegistry.validate_arguments（唯一 Schema 权威：
    required / 类型 / 未知字段拒绝；缺失必填 → ToolResult(False)）
  ↓
get_inventory Handler
    material_code（必填）+ warehouse_code（可选，契约保留）
    warehouse_code 非 None → 显式拒绝（ToolExecutionError，0 DB）
    （当前库存表无 warehouse 维度 → database-level warehouse
      filtering = NOT SUPPORTED；绝不静默忽略参数）
  ↓
ToolResult
```

### 多 Tool 统一执行边界（Phase 3.11 Step 5）

```text
                    Question
                       │
                       ▼
                AIOrchestrator
                       │
                       ▼
                    Router                （selection：tool_name 唯一来源）
                       │
                  RouteDecision
                  tool_name
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       get_inventory       get_work_order
             │                   │
             └─────────┬─────────┘
                       ▼
             ToolExecutionService        （execution boundary：capability + 委派）
                       │
                       ▼
                 ToolRegistry             （schema / execution authority）
                       │
                 Schema Validation        （validate_arguments）
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
          Tool A               Tool B      （business implementation；只读）
             │                   │
             └─────────┬─────────┘
                       ▼
                   ToolResult             （统一契约）
```

* `ToolExecutionService` 是两个真实 Tool 的**共同执行边界**
  （Step 5 零修改即接入第二个 Tool）；
* `ToolArgumentExtractor` 是两个真实 Tool 的**共同参数提取边界**
  （Phase 3.11 Step 6：提取实现从 Orchestrator 迁出，0 处残留，见 §8.24）；
* Tool-to-Tool 隔离：Handler 无 Registry / ExecutionService / Orchestrator
  依赖；Tool 不调用其它 Tool（Question → Router → ONE Tool）；
* `get_work_order` 真实化：ToolChatService 链路仍使用 mock 版（未迁移）。

### 已引入 / 未引入（如实记录）

```text
Tool Execution Boundary（最小抽取）        = introduced（Phase 3.11 Step 2）
Tool Selection Unification               = IMPLEMENTED（Phase 3.11 Step 3）
（Router 是 Tool 选择唯一来源；Orchestrator 的 _resolve_tool_name 已删除）
Multi-Parameter Tool Argument Contract   = IMPLEMENTED（Phase 3.11 Step 4）
（get_inventory：material_code 必填 + warehouse_code 可选）
第二个真实只读 Tool                        = IMPLEMENTED（Phase 3.11 Step 5）
（get_work_order 真实化；与 get_inventory 共用同一执行边界）
Deterministic Argument Extraction        = IMPLEMENTED（最小表达集合）
Tool Argument Extraction Boundary        = IMPLEMENTED（Phase 3.11 Step 6）
（ToolArgumentExtractor：提取职责已从 Orchestrator 迁出，Orchestrator 0 处残留）
LLM Argument Extraction                  = NOT IMPLEMENTED
Generic NLP Argument Parsing             = NOT IMPLEMENTED
Database-level warehouse filtering        = NOT SUPPORTED（库存表无 warehouse 维度）
Database-level get_work_order             = NOT SUPPORTED（真实 DB 无该业务表；
                                            非 DB 边界测试完整覆盖）
ToolChatService Migration                = NOT IMPLEMENTED
Agent / LangGraph / MCP / Memory / Planning = NOT IMPLEMENTED
多步 Tool Calling（Orchestrator 链路）      = NOT IMPLEMENTED
```

## 8.24 Tool Argument Extraction Boundary（Phase 3.11 Step 6）

> Contract / 迁移记录：`docs/evaluation/Phase 3.11 Step 6 — Tool Argument Extraction Boundary.md`

### 目的

```text
Phase 3.11 Step 1～5 已确立：
    Tool Selection      = Router（唯一来源）
    Execution Boundary  = ToolExecutionService
    Argument Contract   = get_inventory（material_code + warehouse_code）
                          get_work_order（work_order_no）

Step 6 只解决一件事：
    Tool 参数提取（Question → arguments）从 AIOrchestratorService 内部
    独立为 ToolArgumentExtractor（唯一定义位置），行为逐字不变。
```

### 最终链路（链路 A = Orchestrator，Step 6 之后）

```text
Question
   ↓
Router                              （Tool Selection Authority）
   ↓
RouteDecision(tool_name)
   ↓
AIOrchestratorService._run_tool     （编排；0 处提取实现）
   │   读取 ToolDefinition.parameters（声明字段，仅作字段门槛）
   ↓
ToolArgumentExtractor.extract       （Argument Extraction Boundary）
   │   确定性：字符串匹配 / 正则 / 字符区间计算（非 NLP / 非 LLM）
   ↓
arguments（仅含命中的字段；缺失字段不补）
   ↓
ToolExecutionService.execute        （Execution Boundary：capability + 委派）
   ↓
ToolRegistry.execute                （Schema Validation Authority）
   ├── validate_arguments（required / type / unknown field）
   ├── Handler
   └── 异常归一化
   ↓
Tool（Business Implementation；只读）
   ↓
ToolResult → AIOrchestrationResult → ChatResponse
```

链路 B（`POST /api/chat/with-tools`，ToolChatService）**未迁移**，仍走
LLM function calling 多步循环 + mock tools（Step 6 明确不处理）。

### 职责归属（Boundary）

```text
Router                  = Tool Selection Authority
                          （decision.tool_name 是唯一选择来源）

ToolArgumentExtractor   = Natural-language-to-argument candidate extraction
                          （确定性候选值识别；不校验合法性）

ToolRegistry            = Schema Validation Authority
                          （required / 类型 / 未知字段拒绝；唯一执行权威）

ToolExecutionService    = Execution Boundary
                          （capability 校验 + 委派 Registry）

Tool（Handler）          = Business Implementation
                          （只读业务查询 + 业务级第二层校验）
```

### 组件契约

```text
位置：backend/app/services/tool_argument_extractor.py
入口：ToolArgumentExtractor.extract(tool_name, question, *, parameters=None)
返回：dict[str, Any]（永不为 None；未命中 / 未知 Tool → {}）

支持字段（本阶段仅两个真实 Tool）：
    get_inventory  → material_code（第一个不在锚定区间内的字面量）
                     warehouse_code（显式仓库表达）
    get_work_order → work_order_no（显式工单表达）

字段来源优先级：
    parameters（ToolDefinition.parameters 的声明字段）
        > Tool 名称规则表（_TOOL_FIELD_RULES）
        > {}（未知 Tool：不猜、不 fallback）

禁止依赖（AST 静态测试锁定）：
    ToolRegistry / ToolExecutionService / Handler / DB / SQLAlchemy /
    Session / Engine / SQL / Router / LLM / HTTP / Socket / 文件系统 /
    subprocess / eval / exec / open
    （组件无任何 backend.app.* import；无状态：vars(instance) == {}）
```

### 行为不变性（Step 6 的核心约束）

```text
* warehouse / work_order 表达集合逐字保持（正则未改写，仅移动位置）；
* 锚定区间排除（spans_overlap + material interval exclusion）行为不变：
      "A01 仓库 MAT-001"       → warehouse=A01, material=MAT-001
      "warehouse A01 ... 10001" → warehouse=A01, material=10001
* 缺失字段不补齐（required 仍由 ToolRegistry 裁决）：
      "查询 A01 仓库库存" → {"warehouse_code": "A01"} → Registry 拒绝
* 未知 Tool → {}（不猜 Tool、不 fallback 到 get_inventory / get_work_order）
* 未注册 Tool 的 arguments 由 None 变为 {}：
      两者在 ToolRegistry.execute 内等价（None 归一为 {}），
      用户可见结果完全一致（"Tool 未注册"）。
```

### 零修改清单（Step 6 验证：SHA256 前后一致）

```text
backend/app/services/tool_execution_service.py   unchanged
backend/app/services/ai_router_service.py        unchanged（Router / RouteDecision）
backend/app/services/tool_chat_service.py        unchanged
backend/app/tools/registry.py                    unchanged
backend/app/tools/get_inventory.py               unchanged
backend/app/tools/get_work_order.py              unchanged
```

### 已引入 / 未引入（Step 6）

```text
Tool Argument Extraction Boundary    = IMPLEMENTED（Phase 3.11 Step 6；
                                       Orchestrator 0 处提取实现）
LLM Argument Extraction              = NOT IMPLEMENTED
Generic NLP Argument Parsing         = NOT IMPLEMENTED
Schema-driven NLP / Function Calling = NOT IMPLEMENTED
第三个 Tool                          = NOT IMPLEMENTED（规则表由测试锁定，防隐式扩展）
ToolChatService Migration            = NOT IMPLEMENTED
Agent / LangGraph / MCP / Memory / Planning / Multi-step Tool Calling = NOT IMPLEMENTED
```

## 8.25 Tool Boundary Contracts（Phase 3.11 Step 7）

> Contract Tests：`tests/test_tool_architecture_contract.py`（C1–C12）
> 阶段记录：`docs/evaluation/Phase 3.11 Step 7 — Tool Boundary Contract.md`
> 本阶段只做「契约清理 + 静态边界锁定」，**不**新增任何业务能力。

### 职责矩阵（唯一权威）

```text
Router                  = Selection Authority
                          （只读 Tool 元数据：name / description / aliases；
                            不持有 handler、不执行 Tool）

AIOrchestrator          = Orchestration
                          （RouteDecision.tool_name → Extractor → ExecutionService；
                            不绕过执行边界、不重选 Tool、无 Tool-specific 分支）

ToolArgumentExtractor   = Candidate Argument Extraction
                          （确定性候选值识别；不校验合法性、不执行、不触达
                            Registry / DB / LLM / HTTP）

ToolRegistry            = Schema Validation + Tool Dispatch Authority
                          （validate_arguments + Handler 调用 + 异常归一化；
                            唯一 Schema 校验入口、唯一执行权威）

ToolExecutionService    = Capability + Execution Boundary
                          （capability 校验 + 委派 Registry；不解析业务参数）

Tool（Handler）          = Business Implementation
                          （只读业务实现；不依赖任何上层模块）

ToolResult              = Unified Tool Output Contract
                          （success / data / error；frozen）
```

### 依赖方向（单向）

```text
Router                  → （无 Tool 执行依赖）
AIOrchestrator          → Router / ToolArgumentExtractor / ToolExecutionService
ToolArgumentExtractor   → 仅 Python 标准库（re / typing / collections.abc）
ToolExecutionService    → ToolRegistry（+ ProjectCapabilities 纯配置 DTO）
ToolRegistry            → Handler（执行权威；Handler 不被上层直接调用）
Tool（Handler）          → DB 抽象 / 配置 / 项目上下文（**不**依赖上层）
```

### Contract 清单（C1–C12，全部由测试锁定）

```text
C1  Router 不执行 Tool（Selection Only；capability 元数据只读）
C2  ToolArgumentExtractor 不执行 Tool（纯确定性；无 Registry / DB / LLM / HTTP）
C3  ToolExecutionService 不解析业务参数（Capability + Registry Delegation）
C4  Tool 不调用上层（无 Orchestrator / Router / Extractor / ExecutionService）
C5  Tool 之间不互调（cross-import / Handler 引用 / 装配层唯一例外）
C6  Orchestrator 只负责编排（不绕过 ToolExecutionService 直接执行 Tool）
C7  ToolRegistry 是唯一 Schema Authority（missing / unknown / invalid type）
C8  ToolExecutionService 原样透传 arguments（不 rename / filter / fill / parse）
C9  Tool Selection 唯一来源 = RouteDecision.tool_name（Orchestrator 不重选）
C10 Unknown Tool 不 fallback（不猜 Tool / 不落到 RAG / Text-to-SQL）
C11 单 Tool 执行（每请求 1 次执行；无 Tool→Tool / Tool→Router→Tool 循环）
C12 禁止 LLM 参数提取（Extractor 无 async / 无 LLM / 无 HTTP / 无 function calling）
```

### 允许的例外（如实记录，测试锁定归属）

```text
* Router 默认装配：get_default_router 内**延迟 import** ToolRegistry +
  mock_tools → 仅用于 capability 元数据（list_definitions），仍不执行 Tool
  （C1 锁定：模块级 import 不含 Registry；任何层级无 *.execute()）。
* ToolExecutionService → ai_orchestrator_service：仅在 _check_capability 内
  延迟 import 「AIOrchestratorCapabilityError」（定义在 Orchestrator 模块，
  模块级 import 会循环依赖），保持既有 capability 拒绝语义与 HTTP 403
  （C3 锁定：唯一延迟 import，且只取该异常类型）。
* get_inventory.build_default_tool_registry → get_work_order：模块级不交叉
  import；仅装配层延迟 import 注册助手 register_get_work_order_tool
  （不是 Handler、不是 Tool→Tool 调用）（C5 锁定）。
* AIOrchestrator 持有 ToolRegistry：仅用于构造 ToolExecutionService 与读取
  ToolDefinition.parameters（声明字段门槛），**不**调用 registry.execute()
  （C6 锁定：self._tools.get_definition 允许；self._tools.execute 禁止）。
```

### 边界不变量（任何后续阶段都不得破坏）

```text
1. Tool 选择只发生在 Router（decision.tool_name 是唯一来源）
2. Tool 参数提取只发生在 ToolArgumentExtractor（确定性，无 LLM）
3. Schema 校验只发生在 ToolRegistry（required / type / unknown field）
4. Tool 执行只发生在 ToolRegistry.execute（经 ToolExecutionService 进入）
5. 每个 TOOL 请求只执行 ONE Tool（无循环、无 Tool→Tool）
6. Tool 之间互不调用；Tool 不依赖任何上层模块
7. ToolResult 是唯一 Tool 输出契约
```

## 8.26 ToolChatService Boundary Survey（Phase 3.11 Step 8）

> 勘察记录 / 边界决策：`docs/evaluation/Phase 3.11 Step 8 — ToolChatService Survey.md`
> 契约测试：`tests/test_tool_chat_architecture_contract.py`（C1–C10）、
> `tests/test_tool_chat_service_characterization.py`（缺口补测）
>
> 本阶段（Step 8）**只勘察**：当时生产代码 0 修改；不迁移 / 不删除 ToolChatService。
> **后续变更**：Phase 3.11 Step 9 已按本节结论把链路 B 的执行点统一到
> `ToolExecutionService`（Orchestration 仍独立）——见 §8.27。

### 两条链路（**并存**，未统一 —— 不要画成已合并）

```text
【链路 A】主业务链路（Phase 3.11 Step 1–7，权威）
Question
  ↓
Router                                   （Selection Authority）
  ↓ RouteDecision(tool_name)
AIOrchestrator                           （Orchestration）
  ↓
ToolArgumentExtractor                    （确定性参数提取）
  ↓
ToolExecutionService                     （Capability + Execution Boundary）
  ↓
ToolRegistry                             （Schema Validation + Dispatch Authority）
  ↓
真实 Tool（get_inventory / get_work_order，只读 DB）
  ↓
ToolResult
入口：POST /api/ai/chat

【链路 B】Function Calling 链路（Phase 3.6.2–3.6.3；执行边界 Step 9 统一；
        真实 Tool Step 11 接入）
POST /api/chat/with-tools
  ↓ 模块级单例（registry = 真实只读 get_inventory；Mock 仅测试注入）
ToolChatService                          （LLM Function Calling 多步编排）
  ↓ while True（预算 max_rounds=TOOL_MAX_ROUNDS，默认 5，钳制 [1,20]）
LLM（tools=registry.list_definitions() 转换的 OpenAI schema）
  ↓ ToolCall(id, name, arguments)        （LLM 决定 Tool 与参数）
ToolExecutionService.execute(name, arguments=...)
                                          ← Phase 3.11 Step 9：统一执行边界
  ↓
ToolRegistry.execute（Schema 校验 → Handler → 异常归一化）
  ↓
Real get_inventory Handler → PostgreSQL（SELECT …；READ ONLY + 绑定参数）
  ↓ ToolResult → role=tool message → 下一轮 LLM
最终回答
```

> 两条链路的 **Orchestration 仍未合并**：链路 B 不经过 Router /
> ToolArgumentExtractor / AIOrchestrator；共享的只有 **ToolExecutionService**
> （单 Tool 执行边界）+ ToolRegistry + ToolResult。

### 事实对照（真实代码，非目标状态）

| 能力 | 链路 A（新 Tool Pipeline） | 链路 B（ToolChatService） |
|---|---|---|
| Tool Selection | Router（`RouteDecision.tool_name`） | LLM function calling（`ToolCall.name`） |
| Tool Argument Extraction | `ToolArgumentExtractor`（确定性） | LLM 结构化 arguments（Client 解析 JSON） |
| Schema Validation | ToolRegistry | ToolRegistry（相同，未绕过） |
| Capability | `ToolExecutionService._check_capability`（项目白名单） | **同一 Enforcement Point**（Step 10：提供 project_id 时生效；省略则不限制） |
| Tool Execution | ToolExecutionService → ToolRegistry | **ToolExecutionService → ToolRegistry（Step 9 已统一）** |
| Tool Result | ToolResult | ToolResult（相同） |
| Multi-step | 禁止（ONE Tool） | **允许**（sequential + 预算） |
| Project Context | Orchestrator（capabilities / scope / schema） | **project_id → capabilities（执行上下文；Step 10）**；仍不注入 Engine / Schema / Semantic |
| Tools | 真实只读 Tool（DB） | **真实只读 `get_inventory`（Step 11）**；`get_work_order` 仍 Deferred，Mock 仅测试注入 |
| 入口 | `POST /api/ai/chat` | `POST /api/chat/with-tools` |

共享组件（Step 9 起）：`ToolExecutionService`（单 Tool 执行边界，两条链路都经它）
+ `ToolRegistry`（同一类，两个独立实例）+ `ToolResult`（同一 DTO）。
未共享：Orchestration / Selection / Argument 语义 / Capability / ProjectContext。

### 迁移决策（Architecture Decision）

```text
Migration Option A（Deprecate）        = NOT CHOSEN（公开且已文档化接口，删除需产品决策）
Migration Option B（Reuse Execution）  = IMPLEMENTED（Phase 3.11 Step 9：
                                          执行点改经 ToolExecutionService）
Migration Option C（Keep Separate）    = ADOPTED（Orchestration 仍独立：
                                          Router/Extractor 与 Function Calling 不合并）
```

```text
Current architecture = C（Keep Separate）+ B（共享单 Tool 执行边界）

理由：Selection / Argument 语义根本不同（确定性 vs Function Calling），
      强行合并会产生"Router + Function Calling 双选择"的混合体（Step 3 已消除）；
      但"执行"是共同的：两条链路共享 ToolExecutionService（ONE Tool execution），
      loop / 预算 / 消息历史仍只属于各自的编排层。

仍待满足（接入真实 Tool 前，见 Step 8 evaluation §14）：
      project scope / capabilities / 错误映射 / 审计；
      ToolExecutionService 保持 "ONE Tool execution"（loop 留在 ToolChatService）。
```

### 未引入（Step 8 / Step 9 明确不做）

```text
ToolChatService 删除 / API 变更                = NOT DONE
Function Calling 改造 / Mock Tools 删除        = NOT DONE（Mock 保留给测试）
真实 get_inventory 接入链路 B                  = DONE（Phase 3.11 Step 11，见 §8.29）
真实 get_work_order 接入链路 B                 = NOT DONE（Deferred）
Capability / ProjectContext（链路 B）           = DONE（Phase 3.11 Step 10，见 §8.28）
Agent / LangGraph / MCP / Memory / Planning    = NOT IMPLEMENTED
Multi-step Tool Calling（链路 A）              = NOT IMPLEMENTED
```

## 8.27 Tool Execution Boundary Unification（Phase 3.11 Step 9）

> 迁移记录：`docs/evaluation/Phase 3.11 Step 9 — ToolChatService Execution Boundary Migration.md`
> 测试：`tests/test_tool_chat_execution_boundary.py`（迁移行为）+
> `tests/test_tool_chat_architecture_contract.py`（C2 更新为 PASS）

### Before → After

```text
Before：ToolChatService → ToolRegistry.execute                （绕过执行边界）
After ：ToolChatService → ToolExecutionService → ToolRegistry.execute
```

```text
ToolChatService（多步编排：while + 预算 + 消息历史）
    ↓
ToolExecutionService.execute(tool_name, *, arguments)         ← ONE Tool execution
    ├── capability 校验（capabilities=None → 不限制；Deferred）
    ↓
ToolRegistry.execute（validate_arguments → Handler → 归一化）
```

### 装配（module-level singleton，不改 API contract）

```text
backend/app/api/tool_chat.py
    _tool_registry            = ToolRegistry() + register_get_inventory_tool(...)   ← Step 11：真实只读
    _tool_execution_service   = ToolExecutionService(registry=_tool_registry)
    _tool_chat_service        = ToolChatService(execution_service=_tool_execution_service)

（Step 9 / Step 10 时点为 register_mock_tools(...)；Step 11 已换为真实 Tool，
  测试改为 monkeypatch 注入 Mock Registry。）
```

### 接口兼容（向后兼容 + 依赖注入）

```text
ToolChatService(llm_client=None, *, max_tool_rounds=None,
                execution_service=None)          # 新增可注入执行边界
chat(message, *, registry)                        # 签名不变

注入 execution_service → 复用之（传入 registry 必须是 execution_service.registry
                        同一实例，否则 ValueError：避免 Schema 源与执行源分裂）
未注入               → 每次 chat 构造 ToolExecutionService(registry=registry)
```

### 保持不变（回归锁定）

```text
Function Calling / Argument forwarding / Schema Validation / Multi-Step /
Budget / Multiple-Tool-Call 拒绝 / Tool failure continuation /
Malformed ToolCall 传播 / API contract / Tool message 序列化 = PASS
ToolExecutionService 仍为 ONE Tool（无 while / 无 LLM / 无 ToolChatService 依赖）
```

### Deferred（Step 9 时点未做；Step 10 已解决前两项，见 §8.28）

```text
Capability（capabilities=None 接入，**不等于**权限控制） = Deferred → DONE（Step 10）
ProjectContext（链路 B 仍不下发 project context）        = Deferred → DONE（Step 10）
真实 Tool 接入链路 B（Step 9 时点仍只挂 Mock Tools）      = DONE（get_inventory，Step 11，见 §8.29）
                                                          get_work_order 仍 Deferred
```

## 8.28 ToolChatService Capability & ProjectContext（Phase 3.11 Step 10）

> 记录：`docs/evaluation/Phase 3.11 Step 10 — ToolChatService Capability & ProjectContext.md`
> 测试：`tests/test_tool_chat_capability.py`、`tests/test_tool_chat_project_context.py`、
> `tests/test_tool_chat_architecture_contract.py`（C11 / C12）

### 授权链（两条链路共用同一 Enforcement Point）

```text
AIOrchestrator
    ↓
ToolExecutionService          ← 唯一 Capability Enforcement Point
    ├── capability 校验（ProjectCapabilities.allows_tool）
    ├── project_id（执行上下文）
    ↓
ToolRegistry → Tool

ToolChatService（LLM Function Calling + Multi-Step Loop）
    ↓
ToolExecutionService          ← 同一个 Enforcement Point（Step 10 起生效）
    ├── capability 校验 / project_id
    ↓
ToolRegistry → Tool（Step 11 起为真实只读 get_inventory）
```

### 项目作用域来源（服务器端，HTTP 不可注入）

```text
POST /api/chat/with-tools {message, project_id?}
    ↓
project_id → ProjectRegistry.get(project_id) → ProjectRegistration.capabilities
    ↓
ToolExecutionService(registry, capabilities, project_id)
    ↓
逐次传入 ToolChatService.chat(message, *, registry, execution_service=...)

未注册 → ProjectNotFoundError → 404
能力未启用 → AIOrchestratorCapabilityError → 403（Handler 0 次调用）
未提供 project_id → capabilities=None（不限制）= 旧行为，且不查注册表
```

### 边界不变量（Step 10 新增，测试锁定）

```text
1. capability 只在 ToolExecutionService 判断（ToolChatService / API 不做授权）
2. ToolChatService 不知道哪些 Tool 被允许（无白名单 / 无 Tool 名分支）
3. 授权失败不降级：capability 拒绝**不**转成 ToolResult(success=False)，
   也不让 LLM 继续下一轮（异常穿透 → HTTP 403）
4. project scope 只作为执行上下文：不进入 LLM messages / 不进入 Handler arguments
5. 能力字段无法由 HTTP 注入（DTO 仅 message + project_id）
6. 未提供 project_id 时行为与 Step 9 完全一致（旧调用零改动）
7. Multi-Step / 预算语义不变：执行边界仍是 ONE Tool execution
```

### 复用与未新增（如实记录）

```text
复用：ProjectCapabilities / ProjectRegistration / ProjectRegistry /
      AIOrchestratorCapabilityError（无第二种 CapabilityError）/ ToolExecutionService
未新增：ProjectContext DTO（已存在 projects.models.ProjectContext + ProjectRegistration）
未修改：ToolExecutionService（capabilities / project_id 参数 Step 2 已具备，SHA256 未变）
未修改：Router / AIOrchestrator / ToolArgumentExtractor / ToolRegistry / Tools / Mock Tools
```

## 8.29 Real `get_inventory` Integration（Phase 3.11 Step 11）

> 记录：`docs/evaluation/Phase 3.11 Step 11 — Real get_inventory Integration.md`
> 测试：`tests/test_tool_chat_real_get_inventory.py`（DB-gated）+
> `tests/test_tool_chat_architecture_contract.py`（C13）

### Before → After

```text
Before：ToolChatService → ToolExecutionService → Mock get_inventory（硬编码 quantity=1000，0 DB）
After ：ToolChatService → ToolExecutionService → Capability → ToolRegistry
                     → Real get_inventory Handler → PostgreSQL（READ ONLY）→ ToolResult
```

```text
POST /api/chat/with-tools
    ↓
ToolChatService（LLM Function Calling + Multi-Step Loop，未修改）
    ↓
ToolExecutionService（capability + project scope，未修改）
    ↓
ToolRegistry（真实 GET_INVENTORY_DEFINITION + GetInventoryHandler）
    ↓
PostgreSQL：SELECT COALESCE(SUM(qty),0) FROM "<schema>"."<table>"
            WHERE material_code = :material_code
            （BEGIN READ ONLY + SET LOCAL statement_timeout + 绑定参数 + rollback）
    ↓
ToolResult(success=True, data={material_code, qty, project_id})
    ↓
role=tool 消息 → LLM → Final Answer
```

### 生产装配（唯一修改点）

```text
backend/app/api/tool_chat.py
    _tool_registry = ToolRegistry()
    register_get_inventory_tool(_tool_registry)      ← Step 11（原 register_mock_tools）
    _tool_execution_service = ToolExecutionService(registry=_tool_registry)
    _tool_chat_service = ToolChatService(execution_service=_tool_execution_service)
```

```text
* 生产 Registry 只有 1 个 Definition：真实 get_inventory
  （同名 Mock Definition 无法覆盖：ToolRegistry.register 抛
   ToolAlreadyRegisteredError —— C13 锁定）；
* Mock Tools 保留给单元 / characterization 测试（测试自行构造 Mock Registry 注入）；
* 真实 get_work_order 接入 = DEFERRED。
```

### 行为事实（DB-gated 测试锁定）

```text
* ToolCall(get_inventory, {material_code}) → 真实 SELECT → qty 来自 seed 数据；
* arguments 原样转发（material_code 逐字到达 Handler；绑定参数 %(material_code)s）；
* missing required / unknown field → Registry 拒绝（**0 条 SQL**，Handler 0 次）；
* SQL 注入输入 → Handler 字符集校验拒绝（0 条 SQL，不回显注入值）；
* warehouse_code（可选字段）→ 真实 Handler 显式拒绝（当前库存表无该维度，
  不静默按全仓汇总；0 条 SQL）；
* Multi-Step：两次 ToolCall → 两条 SELECT（每次 ONE Tool execution）；
* DB writes = 0：public.knowledge_* 行数不变 + fixture 表行数不变 +
  语句审计（仅 SELECT / SET / ROLLBACK）。
```

### 未修改 / 未引入

```text
未修改：ToolChatService / ToolExecutionService / ToolRegistry / Router /
        AIOrchestrator / ToolArgumentExtractor / get_inventory（Handler + SQL）/
        get_work_order / mock_tools / DB schema（SHA256 与 Step 10 基线一致）
未引入：真实 get_work_order / 第三个 Tool / Real LLM（Fake LLM）/
        Agent / MCP / LangGraph / Memory / Planning / Retry / Cache / 并行 Tool
```

## 8.30 Tool Runtime Failure Contract（Phase 3.11 Step 12）

> 记录：`docs/evaluation/Phase 3.11 Step 12 — Tool Runtime Failure Contract.md`
> 测试：`tests/test_tool_runtime_failure_contract.py` +
> `tests/test_tool_chat_architecture_contract.py`（C14）

### 失败分类（唯一契约）

```text
授权失败（Capability denied）
    → 异常穿透（ToolChatService 0 个 try/except）→ HTTP 403
    → Handler 0 次 / DB 0 次 / 不继续下一轮 LLM

Tool 失败（Schema 拒绝 / Handler 业务拒绝 / DB 失败）
    → ToolResult(success=False) → role=tool 消息
    → 按 Multi-Step contract 继续（不是授权失败，也不降级授权）

协议失败（Malformed / Multiple ToolCall / 预算耗尽）
    → 异常（LLMToolCallFormatError / MultipleToolCallsError /
      ToolCallingBudgetExceededError）→ 任何 Tool 执行之前
```

### Failure Matrix（全部 PASS）

```text
A Capability denied   → AIOrchestratorCapabilityError；Handler 0 / SQL 0
B Unknown Tool        → ToolResult(False,"Tool 未注册")；不 fallback / 不改 name
C Missing required    → Registry reject；Handler 0 / SQL 0
D Unknown argument    → Registry reject（不静默删除字段）
E Invalid type        → Registry reject（无 Python coercion）
F Handler validation  → Schema PASS → Handler REJECT；SQL 0；不回显原始输入
G DB failure          → ToolResult(False)；仅 cause_type；connect 恰好 1 次（无 retry）
H Failure + Multi-Step→ 失败(ToolResult) → 继续 → 成功（1 条 SELECT）
I Budget 1 / 20       → 执行 == 预算；LLM == 预算 + 1；无 retry
J Multiple ToolCall   → 任何执行之前拒绝
K Malformed ToolCall  → LLMToolCallFormatError（真实 parser）；无执行
M Real DB happy path  → SELECT only / READ ONLY / timeout / 绑定参数 / rollback
L Error sanitization  → 无 API key / password / DATABASE_URL / 连接串 / traceback
```

### 静态锁定（C14）

```text
* ToolChatService：0 try/except（失败不被吞）、0 registry.execute(、
  0 allows_tool、执行调用点唯一 = execution.execute；
* 失败路径不触达 Handler（capability / schema 两类由边界与 Registry 前置拒绝）；
* Unknown Tool 不 fallback 到已注册 Tool（同 Registry 内其它 Handler 0 次调用）。
```

### 本阶段零生产修改

```text
未修改：ToolChatService / ToolExecutionService / ToolRegistry / Router /
        AIOrchestrator / ToolArgumentExtractor / get_inventory / API / DB schema
未新增：Tool / 参数 / 错误类型 / audit 表 / request_id 持久化 / Retry
```

## 8.31 Tool Execution Context Boundary（Phase 3.11 Step 13）

> 记录：`docs/evaluation/Phase 3.11 Step 13 — Tool Execution Context Contract.md`
> 测试：`tests/test_tool_execution_context.py` +
> `tests/test_tool_chat_architecture_contract.py`（C15）

### 链路（链路 B）

```text
POST /api/chat/with-tools                     （API contract **不变**）
    ↓
ToolChatService                               （Context **创建者**）
    │   request_id = new_request_id()         （一次 chat 一次；uuid4；不落库）
    │   round / tool_call_id / project_id     （每轮新实例）
    ↓
ToolExecutionContext（frozen；4 字段白名单）
    ├── request_id    str
    ├── project_id    str | None   ← 执行边界作用域（非 LLM）
    ├── tool_call_id  str | None   ← ToolCall.id 原值
    └── round         int（>=1）
    ↓
ToolExecutionService.execute(tool_name, arguments=…, context=…)
    ├── context 类型 / 作用域一致性校验（TypeError / ValueError）
    ├── capability 校验（不变）
    ↓
ToolRegistry.execute(tool_name, arguments)    （签名**不含** context）
    ↓
Tool Handler(arguments)                       （只接收 arguments）
    ↓
ToolResult
```

### Isolation 不变量（测试锁定）

```text
* Context 不进入 Tool arguments（arguments 逐字 == LLM 原值）
* Context 不进入 LLM messages（无 request_id / project scope / round 键）
* Context 不进入 API contract（ToolChatRequest 仍为 {message, project_id}）
* ToolRegistry.execute 签名 = (self, tool_name, arguments)
* ToolHandler.__call__ 签名 = (self, arguments)
* LLM 无法控制 project_id / round / request_id（伪造字段 → unknown field 拒绝）
* Context 与授权作用域必须一致（伪造 scope → ValueError，Registry 0 次调用）
* 每轮新实例；frozen DTO → 不可改写
```

### 零持久化（Step 13 明确不做）

```text
无落库 / 无 audit table / 无 tool_call_log / 无 dashboard /
无 tracing backend / 无 request-id middleware / 无 HTTP header /
无 API DTO 扩展 / 无 Retry / 无 Timeout 编排
链路 A（AIOrchestrator）暂不创建 Context（context=None）= Deferred →
      DONE（Phase 3.11 Step 18 接入，见 §8.36）
```

## 8.32 Tool Execution Record Contract（Phase 3.11 Step 14）

> 记录：`docs/evaluation/Phase 3.11 Step 14 — Tool Execution Record Contract.md`
> 测试：`tests/test_tool_execution_record.py` +
> `tests/test_tool_chat_architecture_contract.py`（C16）

### 组合关系

```text
ToolExecutionContext（§8.31：request_id / project_id / tool_call_id / round）
        +
ToolResult（§8.23/§8.30：success / data / error）
        +
Timing（started_at / finished_at / duration_ms）
        ↓ ToolExecutionRecord.from_execution()（纯函数）
ToolExecutionRecord（frozen；只描述执行事实）
```

### Contract（11 字段执行元数据）

```text
request_id / round / tool_name / started_at / finished_at / duration_ms /
success / project_id / tool_call_id / error_code / error_type

* tool_name 来自 execute() 实参（不推断）；success 来自 ToolResult.success（不推断）
* error_code = None（不发明错误码体系）；error_type = 既有 ToolError 类名
  allow-list（无法确定 → None，不解析 message 正文）
* 计时：now_utc()（UTC wall clock）+ elapsed_ms()（perf_counter 差值）
```

### 安全边界（测试锁定）

```text
Record 不含 arguments / ToolResult.data / SQL / prompt / response /
API Key / password / DATABASE_URL / connection string / Authorization /
Traceback / 原始 error message；error_type 仅允许异常类名形态。
```

### 兼容性（Step 14 时点：零生产接线）

```text
ToolResult / ToolExecutionService.execute() 返回类型 / ToolRegistry /
Tool Handler / ToolChatService / API = 全部不变；
Record 在 Step 14 时点**不被生产代码创建** → Step 15 引入**可选 observer**。
零持久化：无 DB / Repository / Migration / audit table / dashboard /
metrics 聚合 / tracing backend。
```

## 8.33 Tool Execution Observability Boundary（Phase 3.11 Step 15）

> 记录：`docs/evaluation/Phase 3.11 Step 15 — Tool Execution Observability Boundary.md`
> 测试：`tests/test_tool_execution_observer.py` +
> `tests/test_tool_chat_architecture_contract.py`（C17）

### 链路（唯一生产改动 = 执行边界新增可选 observer）

```text
ToolExecutionService.execute(tool_name, arguments, context)
    ├── context 类型 / 作用域校验（Step 13）
    ├── capability 校验
    ├── 若（context 存在 且 observer 存在）：
    │       started_at = now_utc() / timer = perf_counter()
    ├── ToolRegistry.execute(...) → ToolResult
    ├── 若上述条件成立：
    │       ToolExecutionRecord.from_execution(context, tool_name, result, timing)
    │       observer.on_execution(record)        ← 唯一回调点（try/except 隔离）
    ↓
    return ToolResult（**契约不变**）
```

### 事件规则

```text
成功 / ToolResult(False) → 1 个事件
capability 拒绝 / Registry 抛异常 / context=None → 0 个事件
observer=None → 不计时 / 不建 Record（零开销，旧行为逐字不变）
```

### 隔离与安全（测试锁定）

```text
* observer 失败 → 只 warning（tool_name + error_type，无 message / traceback）→
  ToolResult 不变、Tool 恰好 1 次执行、不 retry / 不 fallback；
* observer 只接收 Record（AST：1 处调用、1 个位置参数、无关键字参数）；
* observer 无 Registry / Handler / Engine / Session / DB / LLM / API 能力
  （import 白名单：{__future__, typing, tool_execution_record}）；
* Record 不含 arguments / ToolResult.data / SQL / secrets（C16 + C17 双向锁定）；
* 零持久化：无 DB / 消息队列 / tracing backend / 文件日志 / EventBus。
```

### 兼容性

```text
ToolResult / execute() 返回类型 / ToolRegistry / Tool Handler /
ToolChatService（request_id / round / tool_call_id 语义）/ API = 全部不变；
observer 为**可选**关键字参数（旧构造形态合法）；
AIOrchestrator 保持 context=None → 链路 A 无事件（Step 15 时点；
Phase 3.11 Step 18 已接入，见 §8.36）。
```

## 8.34 In-Memory Tool Execution Collector（Phase 3.11 Step 16）

> 记录：`docs/evaluation/Phase 3.11 Step 16 — In-Memory Tool Execution Collector.md`
> 测试：`tests/test_in_memory_tool_execution_collector.py` +
> `tests/test_tool_chat_architecture_contract.py`（C18）

### 链路

```text
ToolExecutionService（Step 15：observer 可选）
    ↓ ToolExecutionRecord
InMemoryToolExecutionCollector.on_execution(record)      （原样 append；O(1)）
    ↓
records() / records_by_request_id() / records_by_project_id() /
records_by_tool_name()
    ↓
tuple[ToolExecutionRecord, ...]（不可变快照；无匹配 → ()）
```

### Contract

```text
* 只保存 ToolExecutionRecord（非 Record → TypeError）
* 全部查询返回 tuple 快照（调用方无法 append / clear / pop）
* 顺序 = 写入顺序（不 sort / 不 deduplicate / 不 aggregate）
* records_by_project_id(None) 只匹配 project_id is None（≠"全部"）
* records_by_tool_name 严格相等（无大小写折叠 / 前缀 / 别名 / 模糊）
* clear() 是唯一清空入口（无 TTL / 无后台清理 / 无定时任务）
* 无统计 API（count / success_rate / 分位数属 Metrics，后续阶段）
* 无索引 / LRU / cache（append O(1)；查询 O(n)）
```

### 安全与依赖

```text
import 白名单：{__future__, threading, collections.abc, tool_execution_record}
无 sqlalchemy / db / api / llm / tools / httpx / kafka / redis / celery /
opentelemetry / os / pathlib；无 execute / registry / handler 标识符
（无 Tool 执行能力；无第二条执行路径，如 execute_and_collect）
```

### 生命周期与集成

```text
create（显式实例；**禁止**模块级单例 / get_default_* —— 防 test / request /
        project 污染与内存泄漏）
    ↓ collect → query → clear（可重复）
线程模型：单个 threading.Lock（与 InMemoryProjectRegistry 一致）；
          不引入 asyncio.Queue / 线程池 / event loop
集成方式：既有构造函数注入（ToolExecutionService(observer=collector)）；
          生产接线 = Deferred（Step 16 时点）；
          Phase 3.11 Step 18 起 AIOrchestrator 可注入 observer（§8.36），
          但仍**不**创建 Collector、不接 API（默认 0 事件）
```

## 8.35 Tool Execution Metrics Read Model（Phase 3.11 Step 17）

> 记录：`docs/evaluation/Phase 3.11 Step 17 — Tool Execution Metrics Read Model.md`
> 测试：`tests/test_tool_execution_metrics_service.py` +
> `tests/test_tool_chat_architecture_contract.py`（C19）

### 链路（只读分析层；执行链不变）

```text
ToolExecutionRecord（Step 14，frozen）
    ↓
InMemoryToolExecutionCollector.on_execution（Step 16：收集）
    ↓
collector.records()（不可变 tuple 快照）
    ↓
ToolExecutionMetricsService.snapshot(records)      （纯计算；Storage-agnostic）
    ↓
ToolExecutionMetricsSnapshot（frozen：counts / rates / durations）
```

```text
backend/app/services/tool_execution_metrics_service.py

@dataclass(frozen=True)
class ToolExecutionMetricsSnapshot:      # 字段白名单（8 个）
    total_count: int
    success_count: int
    failure_count: int
    success_rate: float | None
    failure_rate: float | None
    total_duration_ms: float
    average_duration_ms: float | None
    max_duration_ms: float | None

class ToolExecutionMetricsService:       # 无实例状态（@staticmethod snapshot）
    @staticmethod
    def snapshot(records: Iterable[ToolExecutionRecord]) -> ToolExecutionMetricsSnapshot
```

### Metrics 定义与空数据语义

```text
total_count         = 记录数
success_count       = count(success is True)
failure_count       = count(success is False)
                      success_count + failure_count == total_count（恒成立）
success_rate        = success_count / total_count   （total = 0 → None）
failure_rate        = failure_count / total_count   （total = 0 → None）
total_duration_ms   = sum(record.duration_ms)       （空 → 0.0）
average_duration_ms = total_duration_ms / total_count（空 → None）
max_duration_ms     = max(record.duration_ms)       （空 → None）

空数据集：rates / average / max 一律 None（0 条记录 ≠ 0% 成功率）；
          无 ZeroDivisionError / NaN / Infinity；无 round（保持原始精度）
时长来源：只读 record.duration_ms（不 perf_counter / 不 datetime.now）
```

### Contract（C19）

```text
C19.1  Metrics 接收 Iterable[ToolExecutionRecord]（不是 Collector / Session）
C19.2  Snapshot 是 frozen dataclass
C19.3  不重新测量时间（Identifiers / Imports 无 time · datetime · random）
C19.4  total_count = success_count + failure_count
C19.5  空数据集 → rates / average / max = None
C19.6  duration 来自 Record.duration_ms
C19.7  无 request / project / tool 维度（无 by_* / top_slowest_* API）
C19.8  无敏感字段（arguments / result data / error / SQL / credentials）
C19.9  无 DB / LLM / Tool execution（import 白名单 + 无 execute / registry）
C19.10 Deterministic（同一输入 → 同一 Snapshot；顺序无关）
C19.11 无持久化（无 json / pathlib / os / redis / kafka / 文件写）
C19.12 无全局单例（无模块级实例 / 无 get_default_* 工厂）
```

### 未修改 / 未引入

```text
未修改：ToolExecutionService / ToolExecutionRecord / ToolExecutionObserver /
        InMemoryToolExecutionCollector / ToolResult / ToolRegistry / Handler /
        ToolChatService / AIOrchestrator / Router / API / DB schema
未引入：Database / Repository / Migration / Redis / Kafka / Prometheus /
        OpenTelemetry / Grafana / Dashboard / HTTP API / WebSocket /
        Worker / Scheduler / Alert / 维度分析（by_tool / by_project）/
        分位数（p50 / p90 / p95 / p99）/ min / stddev
生产接线：Metrics 是 Read Model —— 执行链**不依赖**它（Metrics 计算失败
          不可能导致 Tool 失败 / retry / fallback）
```

## 8.36 AIOrchestrator Tool Observability Integration（Phase 3.11 Step 18）

> 记录：`docs/evaluation/Phase 3.11 Step 18 — AIOrchestrator Tool Execution Observability Integration.md`
> 测试：`tests/test_ai_orchestrator_tool_observability.py` +
> `tests/test_tool_architecture_contract.py`（C20）

### 链路（链路 A 的 TOOL 路径接入观测；执行链未变）

```text
Question
   ↓
AIOrchestrator.execute(question)
   ├── request_id = new_request_id()        （一次 execute 一个；唯一调用点）
   ↓
AI Router                                  （未修改）
   ↓ RouteType.TOOL → decision.tool_name
ToolExecutionContext                       （round=1 / project_id=边界作用域 /
                                            tool_call_id=None）
   ↓
ToolExecutionService.execute(..., context=Context)     （未修改）
   ↓
ToolRegistry → Tool → ToolResult            （未修改）
   +
ToolExecutionRecord                         （未修改）
   ↓
ToolExecutionObserver（注入；None = 旧行为）
   ↓
InMemoryToolExecutionCollector（调用方 / composition root 持有）
   ↓
ToolExecutionMetricsService.snapshot(collector.records())   （只读下游）
```

### 装配（`backend/app/services/ai_orchestrator_service.py`，唯一生产修改）

```text
AIOrchestratorService(
    ...,
    tool_execution_service=None,       # 显式注入优先（其 observer 由调用方装配）
    tool_execution_observer=None,      # Step 18 新增：仅构造默认边界时注入
)

    observer=None              → 默认边界不传 observer（0 Record；旧行为逐字不变）
    observer + 未注入边界       → ToolExecutionService(..., observer=observer)
    observer + 已注入边界       → 必须为**同一对象**，否则 AIOrchestratorInputError
    observer 形状非法           → AIOrchestratorInputError（无 on_execution）
```

### Context 语义（§四 ~ §六 / §十八）

```text
request_id    一次 execute() 一个（execute() 顶层创建；不落库 /
              不进 API response / 不进 Tool arguments 或 LLM messages）
round         恒为 1（one question → one route → one Tool）
project_id    **唯一权威** = 执行边界授权作用域（服务器端）；
              不来自 question / arguments / Router 决策 / LLM
tool_call_id  恒为 None（非 Function Calling 链路；**不伪造** call id）
```

### Event 语义（Record 计数，C20 锁定）

```text
Tool Success                → 1 Record（success=True）
Tool Failure（ToolResult False） → 1 Record（success=False；不 retry）
RAG / Text-to-SQL           → 0 Record（不创建 Context；SQL Executor 不是 Tool）
Capability denied           → 0 Record（拒绝先于执行；403 语义不变）
observer=None               → 0 Record（默认兼容）
Observer 抛异常              → ToolResult / AIOrchestrationResult 不变（Step 15 隔离）
两次 execute()              → 两个不同 request_id；一次 execute ≤ 1 Record
```

### 未修改 / 未引入

```text
未修改：ToolExecutionService / ToolExecutionRecord / ToolExecutionObserver /
        InMemoryToolExecutionCollector / ToolExecutionMetricsService /
        ToolExecutionContext / ToolRegistry / Tool / Handler / ToolResult /
        Router / API / DB schema / ToolChatService（链路 B）
未引入：Collector 内部创建 / Metrics API / HTTP / DB / Redis / Kafka / 持久化 /
        Dashboard / Prometheus / OpenTelemetry / Audit / Event Bus /
        Agent / MCP / LangGraph / Memory / Planning / Retry / Fallback
生产接线（Step 18 时点）：Deferred → DONE（Step 19 起由 Composition Root
        注入 Application-lifetime Collector，见 §8.37）
```

## 8.37 Observability Composition Root / Lifecycle Contract（Phase 3.11 Step 19）

> 记录：`docs/evaluation/Phase 3.11 Step 19 — Observability Composition Root Lifecycle Contract.md`
> 测试：`tests/test_tool_observability_composition.py` +
> `tests/test_tool_architecture_contract.py`（C21）

### 装配（Composition Root = `backend/app/api/orchestrator_chat.py`）

```text
_TOOL_REGISTRY            = build_default_tool_registry()          （既有）
_TOOL_EXECUTION_COLLECTOR = InMemoryToolExecutionCollector()        ← Step 19（唯一创建点）
_TOOL_EXECUTION_OBSERVER  = _TOOL_EXECUTION_COLLECTOR               （Protocol 引用）

_default_orchestrator = AIOrchestratorService(..., tool_execution_observer=…)

_build_orchestrator_for_project_id(project_id)
    → build_orchestrator_for_project(project_id, base=…)      （Factory 签名不变）
        （Factory 内部 resolved_observer = getattr(base, "tool_execution_observer", None)；
          **继承** base 的观测出口；不创建 Collector）

POST /api/ai/chat → ChatResponse{route, content, data, metadata}（contract 不变）
```

### 生命周期

```text
Collector          = Application lifetime（append / 只读查询 / 显式 clear；
                     **有限 retention 窗口**（Step 20 起 max_records 默认 1000，
                     FIFO 淘汰，见 §8.38）；
                     仍无 start·stop·flush·persist·close·TTL·自动 clear /
                     后台任务 / 持久化；无 get_default_collector()）
request_id         = Request lifetime（每次 execute() 独立；Collector 共享不共享 id）
ToolExecutionRecord = Request execution event（请求执行事件）
Metrics            = Read-only derived view（Composition Root 不创建 / 不缓存）
```

### 依赖方向与边界（C21 锁定）

```text
AIOrchestrator：只依赖 ToolExecutionObserver（Protocol）——
                import 层**不**出现 Collector；不创建 Collector
RAG / Text-to-SQL：0 Tool Record；TOOL：1 Record（成功 / 失败各 1）
capability denied：0 Record（拒绝先于执行）
observer / collector 失败：ToolResult 与 API response 不变（不 retry / 不 fallback；
                          不新增 try/except，复用 Step 15 隔离）
API response：不含 request_id / Record / metrics
```

### 未修改 / 未引入

```text
未修改：AIOrchestratorService / ToolExecutionService / ToolExecutionContext /
        ToolExecutionRecord / ToolExecutionObserver /
        InMemoryToolExecutionCollector / ToolExecutionMetricsService /
        ToolRegistry / Tool / Handler / ToolResult / Router / main.py / DB schema
未引入：第三方 DI 框架 / 全局单例 helper / 持久化 / HTTP Metrics API /
        Dashboard / Prometheus / OpenTelemetry / Audit / Event Bus /
        Redis / Kafka / Celery / Worker
```

## 8.38 Tool Execution Collector Retention Boundary（Phase 3.11 Step 20）

> 记录：`docs/evaluation/Phase 3.11 Step 20 — Tool Execution Collector Retention Boundary.md`
> 测试：`tests/test_in_memory_tool_execution_collector.py`（Retention 测试类）+
> `tests/test_tool_chat_architecture_contract.py`（C22）

### 有限内存窗口（唯一新增概念）

```text
InMemoryToolExecutionCollector(max_records=DEFAULT_MAX_RECORDS)   # keyword-only
DEFAULT_MAX_RECORDS = 1000
collector.max_records        # 只读属性（构造后不可变）

校验：bool → TypeError（isinstance(True, int) 必须显式排除）；
      非 int → TypeError；< 1 → ValueError
配置：仅构造参数；**无** settings.*.max_records 之类全局配置层；
      组合根使用默认值（Step 20 未改动 api/orchestrator_chat.py）
```

### FIFO 淘汰（O(1)）

```text
on_execution(record) → deque(maxlen=max_records).append(record)
    → 超限自动淘汰最旧（append O(1) / eviction O(1) / records() 快照 O(n)）

max_records=3：A B C → B C D → C D E（始终保留最新 N 条）
读取不刷新顺序（FIFO，非 LRU）；淘汰只由**条数**触发（与时间无关）
Evicted Record cannot be recovered（所有查询 API 与 Metrics 均不可见）
```

### 语义边界（C22 锁定）

```text
records() / records_by_request_id / records_by_project_id / records_by_tool_name
    → 只作用于当前 retention window；仍返回不可变 tuple 快照
clear()        → 显式清空窗口；之后仍受 max_records 约束；无自动 clear
Metrics        → 零修改；snapshot(collector.records()) 只统计当前窗口
Record         → frozen，从不被修改（淘汰只释放引用）
线程安全        → 单个 threading.Lock；append + 淘汰同临界区（原子）
```

```text
Retention = finite in-memory window
Persistence = NO
TTL = NO
Background cleanup = NO
Database = NO
```

### 未修改 / 未引入

```text
未修改：ToolExecutionService / ToolExecutionRecord / ToolExecutionObserver /
        ToolExecutionMetricsService / AIOrchestrator / Router / ToolRegistry /
        ToolResult / API / DB schema / main.py
未引入：TTL / 时间窗口 / 定时线程 / 后台 Worker / LRU / 分桶容量 /
        持久化 / DB / Redis / Kafka / Prometheus / OpenTelemetry / Audit
```

## 8.39 Tool Observability Read Query Boundary（Phase 3.11 Step 21）

> 记录：`docs/evaluation/Phase 3.11 Step 21 — Tool Observability Read Query Boundary.md`
> 测试：`tests/test_tool_observability_query_service.py` +
> `tests/test_tool_chat_architecture_contract.py`（C23）

### 只读读边界（Read Facade）

```text
ToolExecutionRecord
        ↓
InMemoryToolExecutionCollector（Record 存储 + Retention + 查询原语）
        ↓
有限 Retention Window
        ↓
ToolObservabilityQueryService            ← 本阶段唯一新增（无状态）
        ├── records()                    → tuple[ToolExecutionRecord, ...]
        ├── records_by_request_id / _project_id / _tool_name
        └── metrics()                    → ToolExecutionMetricsSnapshot

依赖方向（未来 HTTP API / Dashboard）：
    API → Query Service → Collector      （禁止反向依赖 API）
```

```text
构造：ToolObservabilityQueryService(collector, metrics_service=ToolExecutionMetricsService)
只读属性：collector / metrics_service        **无** clear() / on_execution()
```

### 不是什么（严格边界）

```text
不是第二份存储：内部仅 {_collector, _metrics_service}（无 list / deque / 缓存）
不是第二份 retention：无 FIFO / 淘汰 / max_records / 锁
不是第二份过滤：过滤 + 顺序 + 字段校验原样委托 Collector
不是第二份统计：metrics() = ToolExecutionMetricsService.snapshot(collector.records())
不是 Repository / Port / Store Protocol（不过度抽象）
```

### 语义一致性（C23）

```text
retention：query.records() == collector.records()（当前窗口；已淘汰不可恢复）
metrics  ：只统计当前窗口；与直接调用 Metrics Service 等价
只读     ：所有查询不改变 Collector；返回值是不可变 tuple 快照
安全     ：输出仅 Record + Snapshot；无 arguments / SQL / 凭据 /
           DB / LLM / Registry / API 依赖
```

### 未修改 / 未引入

```text
未修改：InMemoryToolExecutionCollector（retention 零变化）/ ToolExecutionService /
        ToolExecutionRecord / ToolExecutionObserver / ToolExecutionMetricsService /
        AIOrchestrator / Router / ToolRegistry / ToolResult / ToolChatService /
        API（api/orchestrator_chat.py 本阶段未动）/ main.py / DB schema
未引入：HTTP API / Dashboard / WebSocket / Database / Redis / Kafka / RabbitMQ /
        Prometheus / OpenTelemetry / Audit / Event Bus / settings / 环境变量 /
        后台线程 / 定时任务 / TTL / 新抽象（Repository / Port / Protocol）
生产接线：无（由测试 / 未来装配显式构造；无单例 / 无 FastAPI dependency）
```

## 8.40 Tool Observability Snapshot Read Model（Phase 3.11 Step 22）

> 记录：`docs/evaluation/Phase 3.11 Step 22 — Tool Observability Snapshot Read Model.md`
> 测试：`tests/test_tool_observability_snapshot.py` +
> `tests/test_tool_chat_architecture_contract.py`（C24）

### 对外 Read Model（与内部 Record 解耦）

```text
ToolExecutionRecord（内部 Execution Event；可演进）
        ↓ ToolExecutionSnapshot.from_record()（显式逐字段映射）
ToolExecutionSnapshot（frozen DTO；字段固定 11 项；对外契约）

ToolObservabilityQueryService
        ├── records*（内部 Execution Record 查询；不变）
        ├── snapshots*（对外 Read Model 查询；Step 22 新增）
        └── metrics()（聚合 Read Model；不变）
```

```text
字段：request_id / round / tool_name / started_at / finished_at /
      duration_ms / success / project_id / tool_call_id / error_code /
      error_type
禁止：vars(record) / asdict(record) / **record.__dict__
      （Record 新增字段不会自动进入对外模型）
```

### 语义边界（C24）

```text
独立 DTO：不是 ToolExecutionRecord 的子类 / 别名；不持有 Record
不持有：Collector / Metrics Service / DB / LLM / Tool 对象（内部只有 11 字段）
retention：snapshots() 与 collector.records() 完全一致（已淘汰不可恢复）
独立性  ：旧快照不随 Collector 的 append / 淘汰而变化
Metrics ：仍由 ToolExecutionMetricsService 基于 Records 计算（不经 Snapshot）
只读    ：snapshots*() 不改变 Collector；Query Service 仍无 clear()
```

### 未修改 / 未引入

```text
未修改：ToolExecutionRecord / ToolExecutionMetricsService /
        InMemoryToolExecutionCollector（retention）/ ToolExecutionService /
        ToolExecutionObserver / ToolExecutionContext / AIOrchestrator /
        Router / ToolRegistry / ToolResult / ToolChatService /
        API（0 修改）/ Composition Root / main.py / DB schema
未引入：HTTP API / Dashboard / WebSocket / Database / Redis / Kafka /
        Prometheus / OpenTelemetry / Audit / Event Bus / settings /
        环境变量 / 后台线程 / 定时任务 / 分页 / 排序 / filter DSL /
        序列化层（to_dict / JSON schema / Pydantic）
```

## 8.41 Tool Observability Architecture Audit（Phase 3.11 Step 23）

> 记录：`docs/evaluation/Phase 3.11 Step 23 — Tool Observability Architecture Hardening & Contract Audit.md`
> 测试：`tests/test_tool_observability_architecture_audit.py`（C25，20 用例）
> **生产代码修改 = 0**

### 静态依赖图（实测：顶层无环）

```text
context  → （无内部依赖）
record   → context, tools.base
observer → record
collector→ record          metrics → record        snapshot → record
service  → context, observer, record, registry, projects.capabilities
           + 延迟：ai_orchestrator_service（AIOrchestratorCapabilityError，Phase 3.8.2）
query    → collector, metrics, record, snapshot
orchestrator → context, extractor, observer, registry, service
tool_chat_service → context, registry, service
api/orchestrator_chat → collector, observer, orchestrator（+ 延迟：factory）
api/tool_chat → orchestrator, registry, service, tool_chat_service
```

```text
顶层依赖图 = DAG（无环）；函数内延迟 import 仅 2 处（历史决策，保持）
```

### 方向（Audit 结论）

```text
允许：Execution → Observability（service → observer → record）
      Query → Collector → Metrics（只读）
禁止（实测均为 NO）：
    Collector/Metrics/Query/Snapshot/Record/Observer → Execution·Orchestrator·API
    Service → Collector（只依赖 Observer Protocol）
    Orchestrator → Collector / Metrics / Query / Snapshot
    ToolRegistry → Orchestrator / Observability
```

### Contract Matrix

```text
Layer        Can Write                 Can Read    Execute Tool   DB   LLM
Service      execution only            ToolResult  YES          间接*  NO
Record       NO                        data        NO           NO    NO
Observer     NO（接收点）              Record      NO           NO    NO
Collector    Record 集合（内存）        Record      NO           NO    NO
Metrics      NO                        Record      NO           NO    NO
Query        NO                        Collector   NO           NO    NO
Snapshot     NO                        Record      NO           NO    NO
* DB 访问只可能发生在 Tool Handler 内部（由 Tool 定义决定）
```

### 审计结论（无真实缺陷）

```text
Record / Collector / Observer / Metrics / Query / Snapshot 全部 PASS
API：无 Tool Observability HTTP 端点；api 不依赖 Query · Snapshot
Composition：Collector 唯一创建点 = api/orchestrator_chat.py；
             Orchestrator 不创建 Collector / Observer / Query Service
Project：capability 拒绝 → 0 Record；Query 不做权限判断、不扩大权限
Security：Observability 层 import 白名单（无 DB / LLM / HTTP / 进程）
→ 生产代码 0 修改；历史残留以 Historical note 补充说明（不改写历史事实）
```

## 8.42 Tool Observability Serialization Boundary（Phase 3.11 Step 24）

> 记录：`docs/evaluation/Phase 3.11 Step 24 — Tool Observability Serialization Boundary.md`
> 测试：`tests/test_tool_observability_serialization.py`（32）+
> `tests/test_tool_chat_architecture_contract.py`（C26，11）
> **不开发 HTTP API**（可序列化 ≠ 应当暴露）

### 链路（只读单向）

```text
Execution
    ↓
Record
    ↓
Observer
    ↓
Collector
    ↓
QueryService
    ├── Snapshot Read Model
    │       ↓ snapshot_to_dict()
    │   Serialization Boundary → JSON-safe primitive dict
    │
    └── Metrics Read Model
            ↓ metrics_to_dict()
        Serialization Boundary → JSON-safe primitive dict
```

```text
Serialization
    ↓
JSON-safe primitive structure      （str / int / float / bool / None）

Serialization
    X
    ↓
Execution / Tool / DB / LLM / Network      （永不触发）
```

### Contract

```text
backend/app/services/tool_observability_serialization.py

    snapshot_to_dict(snapshot) -> dict[str, Any]   # 11 字段，显式映射
    metrics_to_dict(metrics)   -> dict[str, Any]   # 8 字段，显式映射

C26.1  Snapshot → JSON-safe dict      C26.2  Metrics → JSON-safe dict
C26.3  Explicit field mapping         C26.4  无 vars / asdict / __dict__
C26.5  datetime → ISO 8601            C26.6  timezone 保留（含非 UTC offset）
C26.7  None 语义保留（不写成 0）      C26.8  无多余字段
C26.9  无禁用字段                     C26.10 不改入参（Snapshot/Metrics/Record）
C26.11 deterministic                  C26.12~14 无 DB / LLM / Tool 执行
C26.15 无 HTTP API（api/** 不 import 本模块）

datetime → .isoformat()（沿用项目既有约定：cli/knowledge.py::_iso、
          text_to_sql_* generated_at）；naive datetime → ValueError
字段：不新增 / 不删除 / 不重命名 / 不改语义；契约漂移 → ValueError
无反序列化（dict → DTO）；模块不 import json（json.dumps 属调用方）
```

## 8.43 Tool Observability HTTP Read Boundary（Phase 3.11 Step 25）

> 记录：`docs/evaluation/Phase 3.11 Step 25 — Tool Observability HTTP Read Boundary.md`
> 接口：`docs/api.md` §2.5 / §2.6
> 测试：`tests/test_tool_observability_api.py`（31）+
> `tests/test_tool_chat_architecture_contract.py`（C27，11）
> **只做 Read API**（无写 / 无执行 / 无持久化）

### 链路

```text
Tool Execution
      ↓
ToolExecutionRecord
      ↓
Observer
      ↓
Collector（Application lifetime；唯一创建点 = api/orchestrator_chat.py）
      ↓
QueryService（只读）
      ↓
Snapshot / Metrics
      ↓
Serialization
      ↓
FastAPI Read API
      ↓
JSON
```

```text
GET /api/observability/tools           → {"records": [Snapshot × 11 字段]}
GET /api/observability/tools/metrics   → Metrics 8 字段（扁平；无维度）

第一版无 query parameter（无 project_id / tool_name / request_id /
limit / offset / sort / 时间范围）；不排序 / 不过滤 / 不分页 / 不聚合。
```

### 禁止方向

```text
FastAPI
   X → Collector            （API 只经 QueryService accessor）
   X → ToolExecutionService
   X → ToolRegistry
   X → Tool Handler
   X → ToolExecutionRecord / AIOrchestrator

Collector 仍由 Composition Root 唯一创建；
API 通过 ``get_tool_observability_query_service()`` 复用**同一个**
Application 级 Collector（不创建第二个）。
```

### Contract（C27）

```text
C27.1  GET records 只读        C27.2  GET metrics 只读
C27.3  API → QueryService only C27.4~7 不依赖 Collector / 执行边界 /
                               Registry / Handler
C27.8  使用 Snapshot Read Model（不调 query.records()）
C27.9  使用 Serialization Boundary（不自己 isoformat）
C27.10 API 不计算 Metrics      C27.11 API 不创建 Collector
C27.12 使用 Application Collector  C27.13 GET 不触发 Tool 执行
C27.14~16 无 DB / LLM / 持久化 C27.17 响应无凭据
C27.18 响应无 Tool args/result C27.19 Metrics 无 identifier
C27.20 无 Query DSL
```

## 8.44 Tool Observability Persistence Boundary（Phase 3.11.26 — Survey & Design）

> ADR：`docs/decisions/ADR-3.11.26-tool-observability-persistence-boundary.md`
> 记录：`docs/evaluation/Phase 3.11.26 — Tool Observability Persistence Boundary.md`
> 测试：`tests/test_tool_observability_persistence_architecture.py`（C28~C32，16）
> **状态：Proposed（设计已接受；实现 Deferred）—— 本阶段 0 生产代码修改、
> 0 新表 / 0 migration / 0 DB 连接**

### Survey（真实代码）

```text
DB 入口：backend/app/db/session.py（get_engine / get_session_factory /
         get_db / ping_database）
ORM：    backend/app/db/models/（knowledge_document · knowledge_chunk = public；
         llm_usage_record = ai_ops）
Repository：backend/app/db/llm_usage_repository.py（项目**唯一** Repository；
         注入的是 session_factory，事务内部化，返回内部 Row record）
迁移：   无 Alembic；init_db() = CREATE SCHEMA ai_ops + create_all + 幂等 DDL
UnitOfWork：无（0 处）；Transaction：有（单次 Repository 操作粒度）
```

### 推荐 Boundary（未来；本阶段不实现）

```text
ToolExecutionService（无 DB）
    ↓
ToolExecutionRecord（frozen）
    ↓
ToolExecutionObserver（端口不变）
    ├── InMemoryToolExecutionCollector   （现状：runtime store）
    └── PersistenceAdapter（未来）→ PersistenceService → Repository → ai_ops
```

```text
Decision = Option B（Observer → Adapter → Repository）
    驳回 A（Execution → Repository：执行边界获得 DB 依赖）
    驳回 C（Collector → Repository：Collector 变成写入者）
    驳回 D（QueryService → Repository：只读层获得写能力）

输入类型：ToolExecutionRecord（不是 Snapshot / dict）
失败隔离：DB 写失败 → warning；ToolResult 不变；无 retry / 队列 / 异步
Multi-process：现状 process-local（workers>1 各看各的）；
               持久化后目标 = 共享 PostgreSQL 全量视图
Retention：Runtime(内存 1000) 与 Persistent(数据库长期) **必须分离**
安全：落库字段 = Record 11 项；表落 ai_ops（避免被 Text-to-SQL 当业务表）；
      当前 Record 已足够安全 → 未修改 Record
```

### 契约（当前边界保护）

```text
C28  ToolExecutionService 无 SQLAlchemy / Repository / DB Session
C29  Tool Handler 不依赖 Observability Persistence
C30  Collector 无 SQLAlchemy / Repository / DB Session
C31  QueryService 无数据库写能力
C32  Observability API 不 import Repository / SQLAlchemy
```

## 8.45 Tool Observability Persistence Model & Repository（Phase 3.11.27）

> 记录：`docs/evaluation/Phase 3.11.27 — Tool Observability Persistence Model & Repository.md`
> 测试：`tests/test_tool_execution_repository.py`（29）+
> `tests/test_tool_execution_repository_db.py`（10，DB-gated）+
> `tests/test_tool_observability_persistence_architecture.py`（C33，8）
> **Runtime Integration = NOT IMPLEMENTED**

### 持久化底座（Step 27）

```text
backend/app/db/models/tool_execution_record.py
    class ToolExecutionRecordModel(Base)   → ai_ops.tool_execution_record
    列 = id(BIGINT 自增) + ToolExecutionRecord 的 11 个字段
    started_at / finished_at = DateTime(timezone=True)（TIMESTAMP WITH TIME ZONE）
    request_id **不是**主键 / 唯一键（一次 request 可多条执行）
    索引最小：request_id（已实现查询）+ started_at（时间序）
              project_id / tool_name **未预建**

backend/app/db/tool_execution_repository.py
    ToolExecutionRepository
        create(record) -> ToolExecutionRecordRow
        get_by_request_id(request_id) -> list[ToolExecutionRecordRow]
    事务：with factory() as session, session.begin()（与 LLMUsageRepository 一致）
    失败：SQLAlchemyError → ROLLBACK → ToolExecutionRepositoryError
    返回：内部 frozen Row（不返回 ORM）；不做校验 / 聚合 / 序列化 / retry
    初始化：init_db()（CREATE SCHEMA ai_ops + create_all），无 Alembic
```

### C33（Repository 边界）

```text
Repository 可以依赖：SQLAlchemy / Session / ORM Model / ToolExecutionRecord
ToolExecutionService 不得 import：Repository / SQLAlchemy / RecordRow
Collector 不得 import：Repository / backend.app.db
Observer / QueryService / Snapshot / Metrics / API 不得 import Repository
Adapter / PersistenceService：仍未创建（Deferred）
全 backend 扫描：除 db 层两个文件外，无任何模块 import tool_execution_repository
运行时：ToolExecutionService → ToolRegistry；Observer → InMemoryCollector（未变）
```

## 8.46 Tool Observability Persistence Integration（Phase 3.11 Step 28）

> 记录：`docs/evaluation/Phase 3.11.28 — Tool Observability Persistence Integration.md`
> 测试：`tests/test_tool_execution_persistence_service.py`（18）+
> `tests/test_tool_execution_persistence_adapter.py`（20）+
> `tests/test_tool_execution_persistence_integration.py`（10，DB-gated）+
> `tests/test_tool_observability_persistence_architecture.py`（C34，11）
> **QueryService → PostgreSQL = NOT IMPLEMENTED**（API 仍只读内存）

```text
AIOrchestrator
      ↓
ToolExecutionService（无 DB 依赖）
      ↓
ToolRegistry → ToolResult
      ↓
ToolExecutionRecord
      ↓
CompositeToolExecutionObserver（fan-out；子 observer 互相隔离）
      ├───────────────────────────┐
      ↓                           ↓
InMemoryCollector             PersistenceAdapter
      ↓                           ↓
QueryService                  PersistenceService
      ↓                           ↓
HTTP API                      Repository → ai_ops.tool_execution_record
```

```text
Composition Root（api/orchestrator_chat.py）装配：
    _TOOL_EXECUTION_COLLECTOR                （唯一；生命周期不变）
    _TOOL_EXECUTION_PERSISTENCE_SERVICE      （Step 28）
    _TOOL_EXECUTION_PERSISTENCE_ADAPTER      （Step 28）
    _TOOL_EXECUTION_OBSERVER = Composite(collector, adapter)

失败隔离（C34.6 / C34.7）：DB 写失败 → warning → ToolResult 不变；
    无 retry / queue / batch / 异步 / outbox（同步单行写入）
日志白名单：tool_name / request_id / round / project_id / error_type；
    不使用 logger.exception（无 traceback）
C34.1  Adapter 实现 Observer 协议          C34.2  无 SQLAlchemy/ORM/Repository
C34.3  Service 可依赖 Repository            C34.4  ExecutionService 无持久化依赖
C34.5  Collector 无持久化依赖               C34.6  Adapter 失败不传播
C34.7  Composite 子失败后继续               C34.8  仅 Composition Root 创建 Adapter
C34.9  无 API import Repository             C34.10 Tool Handler 无持久化依赖
DB residue = 0（测试数据 synthetic；只 TRUNCATE 本表）
```

## 8.47 Tool Observability Persistent Query Boundary（Phase 3.11 Step 29）

> 记录：`docs/evaluation/Phase 3.11.29 — Tool Observability Persistent Query Boundary.md`
> 测试：`tests/test_tool_execution_persistent_query_service.py`（20）+
> `tests/test_tool_execution_persistent_query_service_db.py`（9，DB-gated）+
> `tests/test_tool_execution_repository.py`（+12 list_recent）+
> `tests/test_tool_observability_persistence_architecture.py`（C35，10）
> **HTTP API = unchanged**（仍只读内存）

```text
Runtime（未变）                         Persistent（Step 29 新增）
──────────────                          ──────────────────────────
ToolObservabilityQueryService           ToolExecutionPersistentQueryService
      ↓                                         ↓
InMemoryCollector                       ToolExecutionRepository.list_recent()
      ↓                                         ↓
Snapshot / Metrics                      SELECT ... ORDER BY started_at DESC,
      ↓                                        id DESC LIMIT :limit
HTTP API                                        ↓
                                        ToolExecutionSnapshot（显式映射）
```

```text
Limit：MIN=1 / MAX=1000（模块级常量）/ DEFAULT=100；非法 → ValueError（不触达 DB）
错误：无数据 → []；DB 失败 → ToolExecutionRepositoryError（**不**返回 []）
安全：SELECT only + 显式列；Read Model 严格 11 字段（主键 id 不外泄）
C35.1  可依赖 Repository            C35.2  无 SQLAlchemy / Session / ORM
C35.3  Repository 可执行 SELECT      C35.4  查询无 INSERT/UPDATE/DELETE/DDL
C35.5  不 import Collector           C35.6  不 import 执行链 / Registry / Handler
C35.7  不修改 Collector              C35.8  HTTP API 不依赖本服务
C35.9  Snapshot 仍 11 字段           C35.10 ORM Model 不泄漏到 Service / API
两者并列（不 fallback / 不 merge）：Runtime = 近期进程内观测；
Persistent = 长期数据库历史；合并会引入去重 / 排序 / 窗口 / 分页语义冲突。
```

## 8.48 Tool Observability Persistent History API（Phase 3.11 Step 30）

> 记录：`docs/evaluation/Phase 3.11.30 — Tool Observability Persistent History API.md`
> 接口：`docs/api.md` §2.7
> 测试：`tests/test_tool_observability_api.py`（+12）+
> `tests/test_tool_observability_history_api_db.py`（8，DB-gated）+
> `tests/test_tool_observability_persistence_architecture.py`（C36，9）

```text
Runtime History API                          Persistent History API
─────────────────                            ───────────────────────
GET /api/observability/tools                 GET /api/observability/tools/history
      ↓                                            ↓
ToolObservabilityQueryService               ToolExecutionPersistentQueryService
      ↓                                            ↓
InMemoryCollector                           Repository → ai_ops.tool_execution_record
      ↓                                            ↓
Snapshot / Metrics                          Snapshot × 11 字段 → {"items": [...]}

parallel · not fallback · not merged · no dedup
```

```text
唯一 query param：limit（1~1000，默认 100；非法 → 422）
空库 → 200 {"items": []}；DB 失败 → 502（**不回退**内存视图）；
响应 = Snapshot 11 字段（无 id / created_at / arguments / sql / prompt / traceback）
C36.1  API 可依赖 PersistentQueryService     C36.2  不 import SQLAlchemy/Session/ORM/Repository
C36.3  不依赖 ToolExecutionService/Registry/Handler
C36.4  History 端点不 import Collector/Runtime QueryService
C36.5  History API 不修改 Runtime API 行为    C36.6  响应仅 Snapshot 11 字段
C36.7  无 Runtime → Persistent fallback       C36.8  无 Persistent → Runtime fallback
C36.9  无 POST / PUT / PATCH / DELETE         C36.10 只经 PersistentQueryService 读取
Composition Root 装配：模块级 PersistentQueryService + accessor
（API 不接触 Repository / SQLAlchemy / Session；不每请求新建）
```

## 8.49 Tool Observability Persistent History Pagination（Phase 3.11 Step 31）

> 记录：`docs/evaluation/Phase 3.11.31 — Tool Observability Persistent History Pagination.md`
> 接口：`docs/api.md` §2.7
> 测试：Repository（+6）/ Service（+5）/ API（+7）/
> `tests/test_tool_observability_history_api_db.py`（13，DB-gated）/
> C37（9）

```text
Persistent History
        ↓
PersistentQueryService.list_recent(limit, offset)      （不排序 / 不过滤）
        ↓
Repository
        ↓
SELECT <显式列> FROM ai_ops.tool_execution_record
ORDER BY started_at DESC, id DESC                      （跨页同一排序窗口）
LIMIT :limit OFFSET :offset
        ↓
GET /api/observability/tools/history?limit=100&offset=0
        ↓
{"items": [...11 字段...], "limit": 100, "offset": 0}
```

```text
limit  1~1000（默认 100）；offset >= 0（默认 0；无 MAX_OFFSET）
非法参数 → 422（Service / Repository / DB 均未触达）
空页 / 超大 offset → 200 + items = []（不是 404；不做 COUNT 判断）
DB 失败 → 502（无 fallback 到内存视图）
仅 LIMIT + OFFSET：无 cursor / keyset / page DTO / total_count / COUNT
C37.1  History API 允许 limit + offset
C37.2  无 filter（project_id / tool_name / success / 时间范围 / keyword）
C37.3  Repository：ORDER BY started_at DESC, id DESC + LIMIT + OFFSET
C37.4  Service 不自行排序        C37.5  API 不自行排序
C37.6  Runtime API 无分页        C37.7  Persistent API 不读 Collector
C37.8  无 fallback               C37.9  无 total_count / COUNT
C37.10 无 Metrics aggregation
Runtime 端点（GET /api/observability/tools · /metrics）仍 0 参数、仍读内存。
```

## 8.50 Tool Observability Persistent History Filtering（Phase 3.11 Step 32）

> 记录：`docs/evaluation/Phase 3.11.32 — Tool Observability Persistent History Filtering.md`
> 接口：`docs/api.md` §2.7
> 测试：Repository（+10）/ Service（+6）/ API（+6）/
> `tests/test_tool_observability_history_filtering_db.py`（14，DB-gated）/
> C38（10）

```text
Persistent History API
        ↓
PersistentQueryService.list_recent(limit, offset,
                                   project_id, tool_name, success)
        ↓
Repository
        ↓
WHERE project_id = :p [AND tool_name = :t] [AND success = :s]   ← bound parameters
        ↓
ORDER BY started_at DESC, id DESC
        ↓
LIMIT :limit
        ↓
OFFSET :offset
        ↓
GET /api/observability/tools/history?project_id=…&tool_name=…&success=…
                                   &limit=…&offset=…
```

```text
仅**精确**匹配；条件之间 AND；None = 不追加条件（无空 WHERE）
``""`` 是普通字符串值；success=False **≠** 未提供
非法参数（limit / offset / success）→ 422（不触达 Service / Repository / DB）
无匹配 / 空页 → 200 + items=[]（不是 404）；DB 失败 → 502（无 fallback）
SQL 安全：bound parameters（无字符串拼接 / 无 text() 拼接；注入串仅作字面值）
C38.1  History API 支持 limit/offset/project_id/tool_name/success
C38.2  Runtime API 不增加这些参数      C38.3  过滤在 Repository / SQL 层
C38.4  Service 无 Python filtering     C38.5  API 无 Python filtering
C38.6  过滤值必须 bound parameters     C38.7  过滤先于 LIMIT / OFFSET
C38.8  排序仍 started_at DESC, id DESC C38.9  无 COUNT / SUM / AVG / Metrics
C38.10 无 LIKE / ILIKE / regex / fuzzy / keyword
C38.11 无 Runtime / Persistent fallback C38.12 Snapshot 仍 11 字段
Runtime 端点（/tools · /metrics）仍 0 参数、仍只读内存；持久侧过滤不影响它们。
```

## 8.51 Tool Observability Persistent Metrics（Phase 3.11 Step 33）

> 记录：`docs/evaluation/Phase 3.11.33 — Tool Observability Persistent Metrics.md`
> 接口：`docs/api.md` §2.8
> 测试：Repository（+11）/ Service（+11）/ API（+12）/
> `tests/test_tool_observability_persistent_metrics_db.py`（13，DB-gated）/
> C39（12）

```text
Runtime Metrics                              Persistent Metrics
──────────────────────                       ────────────────────────────────
GET /api/observability/tools/metrics         GET /api/observability/tools/metrics/persistent
      ↓                                            ↓
InMemoryCollector（内存）                    PersistentQueryService.metrics(...)
      ↓                                            ↓
ToolExecutionMetricsService.snapshot()       Repository.get_metrics()（SQL 聚合）
      ↓                                            ↓
ToolExecutionMetricsSnapshot（8 字段）       PostgreSQL（ai_ops.tool_execution_record）

parallel · not merged · no fallback
```

```text
SQL：SELECT count(*) / count(*) FILTER (WHERE success) / sum / avg / max
     FROM ai_ops.tool_execution_record
     [WHERE project_id = :p] [AND tool_name = :t] [AND success = :s]
     （无过滤 → 无 WHERE；无 ORDER BY / LIMIT / OFFSET / GROUP BY / SELECT *）

过滤：与 History 完全一致的**精确**匹配（bound parameters；无 LIKE / regex / fuzzy）
参数：project_id · tool_name · success（**无** limit / offset）
空数据集：计数 0；rates / avg / max = None（不是 0）；total_duration_ms = 0.0
错误：非法值 → 422；无匹配 → 200 + 计数 0；DB 失败 → 502（无 fallback）
响应：8 个指标字段（无 identifier / Snapshot 字段 / args / SQL / traceback）
C39.1  Persistent Metrics API 存在    C39.2  Runtime Metrics 不变
C39.3  只依赖 PersistentQueryService  C39.4  Service 无 ORM / Session
C39.5  Repository 执行 SQL 聚合       C39.6  过滤在 SQL 层（bound）
C39.7  无 Python 全量聚合             C39.8  无 SELECT * / 无关列
C39.9  无 LIKE / ILIKE / regex / fuzzy C39.10 无 fallback / merge
C39.11 响应无敏感字段                 C39.12 无 limit / offset
```

## 8.52 Assistant Trace Contract（Phase 3.12 Step 35）

> 记录：`docs/evaluation/Phase 3.12 Step 35 — Assistant Trace Contract.md`
> 接口：`docs/api.md` §2.9（POST /api/ai/chat）
> 前置：Step 34 审计（`/api/ai/chat` = Canonical Assistant 入口；缺口 = 可关联性）
> 测试：`tests/test_chat_api.py`（C40 = 16）/ `tests/test_ai_orchestrator.py`（+6）
> **不新增 Chat API**

### 链路（成功响应）

```text
POST /api/ai/chat
      ↓
AIOrchestratorService.execute()          ← new_request_id()（**唯一生成点**）
      ├── RAG   ─ metadata.request_id
      ├── TOOL  ─ metadata.request_id ──→ ToolExecutionContext(request_id)
      │                                        ↓
      │                                  ToolExecutionRecord.request_id
      │                                        ↓ ai_ops.tool_execution_record
      └── T2S   ─ metadata.request_id

ChatResponse{ route, content, data, metadata }
      ↓
metadata.request_id = Assistant Trace ID   （API 层不生成 / 不覆盖）
```

```text
可关联：response.metadata.request_id
        == ToolExecutionRecord.request_id（TOOL 路径，实测）
        → GET /api/observability/tools/history 响应项含同一 request_id

不可关联（本阶段）：LLM usage（llm_usage_record.request_id 是 Provider 响应 ID、
                    且默认 Noop sink）→ 留待统一 Error/Observability 阶段
```

### Contract（C40）

```text
C40.1  成功响应含 metadata.request_id      C40.2  唯一来源 = Orchestrator
C40.3  API 不生成 / 不补齐 request_id      C40.4  AIOrchestrationResult 结构不变
C40.5  RAG / TOOL / T2S 三路径均携带       C40.6  TOOL：request_id == Record.request_id
C40.7  RAG / T2S → 0 Tool Record           C40.8  请求体不新增 request_id
C40.9  错误 HTTP contract 不变（错误响应无 request_id）
C40.10 metadata 无敏感信息                 C40.11 /api/chat 不变
C40.12 /api/chat/with-tools 不变（/api/rag/answer 同）
```

```text
metadata 只**新增** request_id 一个键；既有键（decision_source / route_reason /
knowledge_scope / tool_name / tool_success / sql / row_count / truncated /
execution_time_ms / selected_tables / project_id / rag_used_chunks / refused）
语义与内容完全不变；request_id 不进入 Tool arguments / LLM messages / Prompt。
```

## 8.53 Assistant Trace → LLM Usage → Tool Execution（Phase 3.12 Step 36）

> 记录：`docs/evaluation/Phase 3.12 Step 36 — LLM Usage Trace Correlation.md`
> 测试：`tests/test_assistant_trace.py`（18）/
> `tests/test_llm_usage_trace_correlation.py`（14，含 C41）/
> `tests/test_llm_usage_trace_correlation_db.py`（6，DB-gated）
> **不新增 HTTP API / 不改 analytics / 不改 RAG·Tool·T2S·Router**

```text
                    Assistant Trace
                         │
                   request_id = A          ← AIOrchestratorService.execute()
                         │                    （唯一生成点，Step 35）
        ┌────────────────┴────────────────┐
        ↓                                 ↓
  LLM Usage Record                 Tool Execution Record
  assistant_request_id = A         request_id = A
  request_id           = P         （ai_ops.tool_execution_record）
  （ai_ops.llm_usage_record）
```

```text
传播方式：services/assistant_trace.py
    execute() → with assistant_trace_scope(A)
        ↓ contextvar（per-task；asyncio.to_thread 会复制 context）
    LLM Usage Persistence Boundary
        ↓ current_assistant_request_id() → A
    LLMUsageRepository.create(assistant_request_id=A, request_id=P)

Data Model：ai_ops.llm_usage_record + assistant_request_id VARCHAR(128) NULL
    · 与 request_id（Provider 请求 ID）**两个维度**，互不覆盖
    · 不参与幂等（partial unique index 仍只约束 request_id）
    · 旧链路 / 历史数据保持 NULL；读边界（Row / View / analytics）未变
    · schema 演进：init_db() 幂等 ADD COLUMN IF NOT EXISTS（无 Alembic）

C41.1  Assistant request_id → LLM Usage      C41.2  provider ID 独立
C41.3  API 不生成 request_id                 C41.4  Orchestrator 唯一来源
C41.5  Tool Record 与 LLM Usage 同 ID        C41.6  RAG / T2S → 0 Tool Record
C41.7  历史 NULL 兼容（读边界未变）           C41.8  持久化失败不影响执行
C41.9  无 prompt / args / SQL / secrets
Failure Isolation：DB 写入失败 → warning → Assistant 结果不变（无 retry / 队列）
```

## 8.54 Assistant Trace → LLM Usage Read Boundary（Phase 3.12 Step 37）

> 记录：`docs/evaluation/Phase 3.12 Step 37 — Assistant Trace LLM Usage Read Boundary.md`
> 测试：`tests/test_llm_usage_trace_read.py`（34，含 C42）/
> `tests/test_llm_usage_trace_read_db.py`（8，DB-gated）
> **不新增 HTTP API**（应用层只读边界）

```text
Assistant Trace A
        ↓
LLMUsageQueryService.list_by_assistant_request_id("A")     ← 校验（DB 之前）
        ↓
LLMUsageRepository.list_by_assistant_request_id("A")
        ↓ build_trace_select()（唯一 trace SQL 构造点）
    SELECT id, assistant_request_id, request_id, provider, model,
           prompt_tokens, completion_tokens, total_tokens, created_at
    FROM ai_ops.llm_usage_record
    WHERE assistant_request_id = :assistant_request_id      （精确匹配；NULL 不匹配）
    ORDER BY created_at ASC, id ASC                          （调用发生顺序）
        ↓ [LLMUsageTraceRow]（frozen；非 ORM）
        ↓ [LLMUsageTraceRecordView]（9 字段；显式映射）
LLM Usage Records
```

```text
与既有读边界的关系（并列，不替代）
    LLM_USAGE_READ_COLUMNS / LLMUsageRecordRow / LLMUsageRecordView（8 字段）
        —— analytics 读路径（/api/usage/analytics 响应结构未变）
    LLM_USAGE_TRACE_READ_COLUMNS / LLMUsageTraceRow / LLMUsageTraceRecordView（9 字段）
        —— Assistant Trace 读路径（本小节）
    Repository / Query Service 均为**同一个类**新增方法（无第二套 Repository）
C42.1  精确匹配                    C42.2  NULL 不参与匹配
C42.3  bound parameter             C42.4  不 SELECT *（显式 9 列）
C42.5  返回 frozen Row DTO         C42.6  Service 无 ORM / Session
C42.7  不新增 HTTP endpoint        C42.8  request_id（Provider ID）语义不变
C42.9  历史 NULL 兼容              C42.10 不读取 prompt / messages / secrets
无分页 / 不聚合 / 不组装 Trace DTO（Tool · RAG 联接属未来阶段）
```

## 8.55 Assistant Trace Read Model（Phase 3.12 Step 38）

> 记录：`docs/evaluation/Phase 3.12 Step 38 — Assistant Trace Read Model.md`
> 测试：`tests/test_assistant_trace_query_service.py`（35，含 C43）/
> `tests/test_assistant_trace_query_service_db.py`（6，DB-gated）
> **Read Model Composition ≠ Trace System**；**不新增 HTTP API**

```text
Assistant Trace A
        │
        ├── LLM Usage[]          ← LLMUsageQueryService（PostgreSQL；Step 37）
        │
        └── Tool Execution[]     ← ToolObservabilityQueryService（Runtime 内存）
                ↓
        AssistantTraceView（frozen；tuple; 不排序/不去重/不聚合）

AssistantTraceQueryService
       │
       ├── LLMUsageQueryService            （只读 Query Service）
       └── ToolObservabilityQueryService   （只读 Query Service）

No Repository · No ORM · No SQL · No Session · No HTTP · No persistence
（Collector 仍由 api/orchestrator_chat.py 唯一创建；本服务不创建 Collector）
```

```text
AssistantTraceView(assistant_request_id: str,
                   llm_usage: tuple[LLMUsageTraceRecordView, ...],   # 9 字段
                   tool_executions: tuple[ToolExecutionSnapshot, ...]) # 11 字段

Empty Semantics：LLM=0/Tool=0 合法（返回空 tuple，不是错误）；未知 ID 同样返回空 View
Ordering：llm_usage = created_at ASC, id ASC（Step 37）；tool_executions = Collector 写入顺序
Errors：下游异常原样透传（**绝不**把 DB 故障降级为 []）；输入非法 → ValueError（下游零调用）
C43.1 只依赖 Query Service      C43.2 不依赖 Repository
C43.3 不依赖 SQLAlchemy         C43.4 不访问 Session（且不创建 Collector）
C43.5 不生成 request_id         C43.6 不执行 Tool
C43.7 不执行 LLM                C43.8 不新增 HTTP endpoint
C43.9 immutable result          C43.10 不暴露 secrets
```

## 8.56 Assistant Trace HTTP Read API（Phase 3.12 Step 39）

> 记录：`docs/evaluation/Phase 3.12 Step 39 — Assistant Trace HTTP Read API.md`
> 接口：`docs/api.md` §2.10
> 测试：`tests/test_assistant_trace_api.py`（21）/
> `tests/test_assistant_trace_api_db.py`（4，DB-gated）
> **No Trace Table · No Trace Repository · No OpenTelemetry · No Conversation · No Memory**

```text
HTTP GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
AssistantTraceQueryService.get_trace()        （Step 38 组合；不重新实现查询）
        ├── LLMUsageQueryService          → PostgreSQL（ai_ops.llm_usage_record）
        └── ToolObservabilityQueryService → Runtime Memory（应用级 InMemory Collector）
        ↓ AssistantTraceView
        ↓ AssistantTraceResponse（显式字段映射 + response_model 过滤）
        ↓ JSON
```

```text
装配（Composition Root 仍是 api/orchestrator_chat.py）
    api/assistant_trace.py → get_assistant_trace_query_service()
        → LLMUsageQueryService()
        → get_tool_observability_query_service() → **同一个** _TOOL_EXECUTION_COLLECTOR
    （实测未创建第二个 Collector；Step 23/24 契约保持）

响应：{assistant_request_id, llm_usage[9 字段], tool_executions[11 字段]}
空 Trace → 200 + 两个空数组（**不是** 404）
错误：400（纯空白）/ 422（长度越界）/ 502（LLM 数据源不可用）/ 500（其它）
      —— **绝不**把 DB 故障降级成 200 + 空 Trace
顺序：HTTP 层只做 tuple → list（不重排 / 不过滤 / 不聚合 / 不去重）
数据源：**Step 41 起两者均为 PostgreSQL**（见 §8.57）；刻意不与 Runtime 合并
```

## 8.57 Persistent Tool Trace Integration（Phase 3.12 Step 41）

> 记录：`docs/evaluation/Phase 3.12 Step 41 — Persistent Tool Trace Integration.md`
> 测试：`tests/test_assistant_trace_persistent_tool_db.py`（6，DB-gated）+
> Step 38/39/40 测试同步
> **No Trace DB · No Trace Repository · No OpenTelemetry · 无 migration**

```text
Assistant Request A
       │
       ├── LLM Usage  → ai_ops.llm_usage_record        （PostgreSQL）
       └── Tool       → ai_ops.tool_execution_record   （PostgreSQL；Step 41 切换）
              ↓
        AssistantTraceQueryService
        ├── LLMUsageQueryService                 → PostgreSQL
        └── ToolExecutionPersistentQueryService  → PostgreSQL
              ↓
        GET /api/observability/assistant-trace/{A}（endpoint / response 不变）
```

```text
新增读路径（最小扩展，复用既有 Repository 方法）
    ToolExecutionPersistentQueryService.list_by_request_id(request_id)
        → Repository.get_by_request_id()（既有；显式列 + WHERE + ORDER BY id ASC）
        → [ToolExecutionSnapshot]（11 安全字段；数据库主键 id 不外泄）
        · 空 → []；DB 失败 → ToolExecutionRepositoryError（**不降级为 []**）
        · 不合并 Runtime Collector（避免 duplicate Tool Execution）

Runtime vs Persistent（职责不变）
    /api/observability/tools（+ /metrics）                     → Runtime 内存（未变）
    /api/observability/tools/history（+ /metrics/persistent）  → PostgreSQL（未变）
    /api/observability/assistant-trace/{id}                    → LLM + Tool 均 PostgreSQL

Restart-like 实测：POST /api/ai/chat → Tool 落库 → Collector.clear()
    → GET trace 仍返回 tool_executions ≥ 1；同时 /api/observability/tools
      返回 {"records": []} ⇒ Trace ≠ Runtime Memory
```

## 8.58 RAG Trace Coverage（Phase 3.12 Step 42 — Audit Only）

> 记录：`docs/evaluation/Phase 3.12 Step 42 — RAG Trace Coverage Audit.md`
> 测试：`tests/test_rag_trace_coverage_audit.py`（22，audit-only）
> **Audit only**：无新表 / 无 Repository / 无 schema 变更 / 无 Trace API 变更

```text
Assistant Trace A（Step 41 现状）
    ├── LLM Usage   → ai_ops.llm_usage_record（assistant_request_id = A）✅
    ├── Tool        → ai_ops.tool_execution_record（request_id = A）✅
    └── RAG         → ?     ← 本阶段审计对象
```

```text
RAG 事实现状
    执行期可读：current_assistant_request_id() == A（Step 36 scope 覆盖整段执行）
                —— 关联键"读得到"，但没有记录点
    仅日志：    query_length · top_k · result_count · used_chunks_count ·
                context_truncated · context_chars · reranker_used ·
                rerank_elapsed_ms · elapsed_ms（**日志无 request_id**）
    响应：      /api/ai/chat RAG data = { sources[chunk_id·document_id·chunk_index·
                similarity·metadata], used_chunks_count }（content 被 HTTP 层剥离）
    持久化：    **无**（Current RAG has no persistent execution record）

Gap：Assistant Trace 不能重建 RAG 执行历史（是否走向 RAG / 命中哪些 chunk /
     检索与重排耗时）；旧链路 /api/rag/answer · /api/chat 连 trace id 也没有

Recommendation：**Defer implementation**
    Potential Boundary（仅记录）：future RAG observability could expose
    retrieval metadata only（used_chunks_count / top_k / elapsed_ms /
    chunk_id · document_id · similarity），不落 chunk 原文
安全分类：Trace-safe = 计数 / 耗时 / top_k；Potentially sensitive = query 原文 ·
    similarity 组合 · 知识库自定义 metadata；Must never expose = chunk / document
    正文 · embedding vector · prompt / messages / raw response · 凭据 / DB 对象
```

## 8.59 RAG Runtime Observability（Phase 3.12 Step 43）

> 记录：`docs/evaluation/Phase 3.12 Step 43 — RAG Runtime Observability.md`
> 测试：`tests/test_rag_runtime_observability.py`（44）
> **Runtime Only**：无表 / 无 Repository / 无 Migration / 无新 HTTP API /
> Assistant Trace 未修改；RAG 核心算法（Vector Search · Reranker ·
> ContextBuilder）0 修改。

```text
AIOrchestratorService.execute()
      │  request_id = A（唯一生成点）+ assistant_trace_scope(A)
      ↓
RagService.answer()                          ← 公开签名不变（0 新增参数）
      │  current_assistant_request_id() → A
      ├── observation = disabled?（无 observer / 未绑定 Trace）→ 0 观测
      ↓ _ObservationDraft（top_k → result_count → rerank → context → ids）
RagService._finish_observation(draft)        ← best-effort（失败只 warning）
      ↓ observer.record(observation)
InMemoryRagExecutionCollector（deque(maxlen=1000) + 单锁；FIFO；无 TTL）
      ↓
RagObservabilityQueryService（只读 Facade；校验先于查询；无 clear）
      ↓
tuple[RagExecutionObservation, ...]          （内部；**无** HTTP API）
```

```text
RagExecutionObservation（frozen；13 字段白名单）
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids
（**无** query / answer / content / embedding / similarity / prompt /
  messages / raw response / SQL / 凭据）

生命周期：成功 · 空检索（result_count=0）· 异常（记录后 **re-raise 原异常**）
关联：observation.request_id == 响应 metadata.request_id
      == llm_usage_record.assistant_request_id（同一 Scope）
隔离：observer 缺失 / 未绑定 Trace → 0 观测；观测失败绝不改变 RAG 成败
```

## 8.60 RAG Runtime Observation 生产接线（Phase 3.12 Step 44）

> 记录：`docs/evaluation/Phase 3.12 Step 44 — RAG Runtime Wiring.md`
> 测试：`tests/test_rag_runtime_observability_e2e.py`（16，真实装配 E2E）
> 改动：**1 个新 Service 层 runtime 模块** + Composition Root 2 行；
> RagService / Orchestrator / Router / Factory / DB Schema / 旧 API 全部未改。

```text
POST /api/ai/chat
      ↓
api/orchestrator_chat.py（Composition Root）
      │  _default_orchestrator = AIOrchestratorService(..., rag_service=get_observed_rag_service())
      ↓
services/rag_observability_runtime.py（应用级装配；唯一创建点）
      ├── _RAG_EXECUTION_COLLECTOR = InMemoryRagExecutionCollector()   ← 进程内单实例
      └── _RAG_SERVICE = RagService(observer=_RAG_EXECUTION_COLLECTOR)
      ↓
AIOrchestratorService.execute() → request_id = A + assistant_trace_scope(A)
      ↓ Router → RAG
RagService（应用级实例）→ RagExecutionObservation.request_id = A
      ↓
InMemoryRagExecutionCollector → RagObservabilityQueryService（内部只读；无端点）

项目级路径：build_orchestrator_for_project(..., base=_default_orchestrator)
            → rag_service=base._rag（同一实例；Factory 未改）
```

```text
/api/rag/answer（api/rag._rag_service）   → observer=None → 0 观测（无 trace）
/api/chat（ChatService._rag_service）     → observer=None → 0 观测（无 trace）
Assistant Trace                          → UNCHANGED（无 RAG 段）
Persistence                              → NO（无表 / 无 Repository）
Security                                 → 13 字段白名单逐字不变
Failure isolation                        → Collector.record 抛错 → HTTP 200（实测）
```

## 8.61 RAG Persistence Boundary（Phase 3.12 Step 45 — Audit / Design Only）

> 记录：`docs/evaluation/Phase 3.12 Step 45 — RAG Persistence Boundary Audit.md`
> 测试：`tests/test_rag_persistent_observation_contract.py`（32，纯离线）
> **Design Only**：无表 / 无 migration / 无 Repository / 无 HTTP API /
> Assistant Trace 未改 / **0 生产运行时代码改动**。

```text
Contract（Step 46 实现时必须满足）
    字段      13 字段 1:1（列名不变）＋ 数据库自增 `id`（沿用既有主键模式）
              rerank_elapsed_ms NULL ≠ 0；无 query / content / similarity /
              embedding / prompt / messages / raw response / SQL / 凭据
    关联      request_id == Assistant Trace ID（唯一来源 current_assistant_request_id()）
              命名沿用 Tool 侧（LLM 侧因 Provider id 占用才用 assistant_request_id）
    写侧      RagExecutionObserver（既有端口）→ 未来 CompositeRagExecutionObserver
              （内存 + 持久化并列；失败只 warning，绝不穿透）
    读侧      RagExecutionPersistentQueryService.list_by_request_id()
              → 仓储 get_by_request_id()；空 → []；DB 失败 → typed error
    索引      request_id（第一优先级）· started_at；无投机索引；无唯一约束
    Retention Runtime capacity ≠ persistent retention（defer）
    Deferred  表 / migration / Repository / Assistant Trace 集成 / HTTP API /
              retention / TTL / cleanup
```

## 8.62 RAG Persistent Execution Record（Phase 3.12 Step 46）

> 记录：`docs/evaluation/Phase 3.12 Step 46 — RAG Persistent Execution Record.md`
> 契约：Step 45（`docs/evaluation/Phase 3.12 Step 45 — …Audit.md`）
> 测试：`tests/test_rag_execution_persistence.py`（37）·
> `tests/test_rag_execution_persistence_db.py`（18，DB-gated）·
> `tests/test_rag_runtime_observability_e2e_db.py`（8，DB-gated）
> Schema：**新增 1 张表** `ai_ops.rag_execution_record`（create_all 创建；无 Alembic）

```text
POST /api/ai/chat → RagService → RagExecutionObservation（13 字段）
      ↓ observer.record()
CompositeRagExecutionObserver
      ├── InMemoryRagExecutionCollector（Runtime 视图）
      └── RagExecutionPersistenceAdapter（失败只 warning；绝不穿透）
                ↓ RagExecutionPersistenceService.persist()
                ↓ RagExecutionRepository.create()（内部事务；INSERT RETURNING）
                ↓
          ai_ops.rag_execution_record（id + 13 列；JSONB 存 chunk_ids/document_ids）
                ↓
          RagExecutionPersistentQueryService.list_by_request_id(request_id)
                （只读；空 → []；DB 失败 → RagExecutionRepositoryError）

装配（应用级单实例）：services/rag_observability_runtime.py
    RagService(observer=Composite(collector, adapter))
    accessors：get_observed_rag_service() · get_rag_execution_persistence_adapter()
        · get_rag_execution_persistent_query_service()

/api/rag/answer · /api/chat → observer=None → 0 写入（未人为生成 request_id）
Assistant Trace → UNCHANGED（不含 RAG；集成 deferred）
安全 → 无 query / content / similarity / embedding / prompt / SQL / 凭据 / project_id
```

## 8.63 Assistant Trace × RAG 集成（Phase 3.12 Step 47 — Audit / Design Only）

> 记录：`docs/evaluation/Phase 3.12 Step 47 — Assistant Trace RAG Integration Audit.md`
> 测试：`tests/test_assistant_trace_rag_integration_contract.py`（34，纯离线）
> **Design Only**：Assistant Trace HTTP API / QueryService / RAG 持久化全部未改；
> Production Code = 0 · DB Schema unchanged · 0 网络 / 0 DeepSeek。

```text
Current                                Proposed（未实现）
AssistantTrace                         AssistantTrace
    ├── llm_usage  → LLMUsageQueryService      ├── llm_usage  （不变）
    └── tool_executions → ToolPersistentQS     ├── tool_executions（不变）
                                               └── rag_executions
                                                     → RagExecutionPersistentQS
                                                       （PostgreSQL；非内存）

Correlation：LLM assistant_request_id = Tool request_id = RAG request_id = A
DTO（设计）：13 字段；**裁剪数据库主键 id**（与 ToolExecutionTraceResponse 一致）
    谨慎保留：chunk_ids / document_ids（暴露级别等同既有 /api/ai/chat sources）
    永不进入：query / answer / content / similarity / embedding / prompt /
             messages / raw response / SQL / 凭据 / ORM / traceback
Error：empty ≠ failure；RagExecutionRepositoryError 必须透传（**不为 []**）
    ⚠ 实现缺口：当前 502 分支只列举 LLM + Tool 仓储错误 → RAG 错误会落到 500
Compat：新增 rag_executions = additive（旧字段不改名 / 不删除 / 语义不变）
Ordering：方案 A（三个独立列表，各自顺序保持）→ 不引入统一 events
```

## 8.64 Assistant Trace RAG Integration（Phase 3.12 Step 48 — Implemented）

> 记录：`docs/evaluation/Phase 3.12 Step 48 — Assistant Trace RAG Integration.md`
> 契约：Step 47（§8.63）
> 测试：`tests/test_assistant_trace_rag_integration.py`（23）·
> `tests/test_assistant_trace_rag_integration_e2e_db.py`（6，DB-gated）
> 生产改动：`assistant_trace_query_service.py` · `api/assistant_trace.py` ·
> `api/orchestrator_chat.py`（Composition Root 注入）；RAG Runtime/Persistence、
> LLM Usage、Tool Execution、Router、Orchestrator、DB Schema 全未改。

```text
GET /api/observability/assistant-trace/{assistant_request_id}
      ↓
AssistantTraceQueryService.get_trace(A)
      ├── LLMUsageQueryService                 （ai_ops.llm_usage_record）
      ├── ToolExecutionPersistentQueryService  （ai_ops.tool_execution_record）
      └── RagExecutionPersistentQueryService   （ai_ops.rag_execution_record）← Step 48
      ↓
AssistantTraceView + rag_executions（tuple[RagExecutionTraceView]）
      ↓ 显式逐字段映射
AssistantTraceResponse{ assistant_request_id, llm_usage, tool_executions,
                        rag_executions }                ← additive（旧字段不变）

RagExecutionTraceResponse（13 字段）：request_id · started_at · finished_at ·
    duration_ms · result_count · used_chunks_count · top_k · context_truncated ·
    context_chars · reranker_used · rerank_elapsed_ms · chunk_ids · document_ids
    （**无**数据库主键 id；无 query / content / similarity / embedding / 凭据）

Error：RagExecutionRepositoryError 加入 502 元组（原会落 catch-all → 500）
Empty：rag_executions = [] → 200；DB 失败 → 502（**不为 []**）
Ordering：LLM created_at,id ASC · Tool id ASC · RAG id ASC（组合层不重排）
Source：Persistent only（不读 InMemoryRagExecutionCollector）
```

## 8.65 Assistant Trace Unified Timeline（Phase 3.12 Step 49 — Design Only / DEFER）

> 记录：`docs/evaluation/Phase 3.12 Step 49 — Assistant Trace Unified Timeline Design.md`
> **Design Only**：未实现 `events`；未改 AssistantTraceResponse / QueryService /
> 任何 Repository / 持久化 / Runtime；无新表 / 无 Event Bus / 无 OTel。
> 0 新增测试（Step 49 §十五允许）。

```text
事实：LLM 只有 created_at（完成/入库时刻）；Tool / RAG 有 started_at + duration_ms
     → 不存在统一"事件开始时间"；Tool/RAG Trace 读模型刻意无主键 → 无 source_id
排序现状：LLM created_at,id ASC · Tool id ASC · RAG id ASC（组合层不重排）
架构现状：单一 route、无并行、无多步 → 三个独立列表已完整

结论：**DEFER**（时间排序 ≠ 因果排序；正确实现需改 LLM Usage 持久化）
未来最小实现：仅在组合层派生 events（event_type ∈ {llm,tool,rag}；
    occurred_at = created_at / started_at；duration_ms = None(llm) 或原值；
    tie-break = occurred_at → source_priority(rag,tool,llm) → 段内序号）
红线：不暴露 query / answer / content / similarity / embedding / prompt /
     messages / raw_response / SQL / 凭据 / ORM / traceback / 数据库主键
```

## 8.66 Assistant Trace 读取扩展性（Phase 3.12 Step 50 — Design Only / DEFER）

> 记录：`docs/evaluation/Phase 3.12 Step 50 — Assistant Trace Read Scalability Design.md`
> **Design Only**：未实现分页 / 上限 / cursor；未改 Response / QueryService /
> Repository / Schema / 索引；无缓存 / 无新基础设施。Production Code = 0。

```text
现状：一次 Trace = 三次独立只读 SQL（显式列 · bound params · 稳定排序 · 无 N+1）
    排序：LLM created_at,id ASC · Tool id ASC · RAG id ASC
    上限：三个读边界均**无** LIMIT/OFFSET/cursor → unbounded by request_id
规模（真实架构）：RAG 路由 rag=1/llm≤1；Tool 路由 tool=1/llm≤1；
    T2S 路由 llm≤4（DEFAULT_MAX_ATTEMPTS=3 + fallback）→ 单请求 ≈6~10 行、KB 级
索引：Tool ✓ · RAG ✓ · LLM **assistant_request_id 无索引**（唯一实质风险点）

结论：**DEFER**（分页收益 < 成本；cursor 与"Tool/RAG 不暴露主键"冲突）
    真正待办（独立最小步骤）：llm_usage_record.assistant_request_id 索引
红线：不得为 cursor 重新暴露数据库主键；分页字段不得携带业务数据
```

## 8.67 LLM Usage Assistant Request ID Index（Phase 3.12 Step 51 设计 → Step 52 Implemented）

> 记录：`docs/evaluation/Phase 3.12 Step 51 — LLM Usage Request ID Index Audit.md`
> 测试：`tests/test_llm_usage_assistant_request_id_index.py`（3 离线 + 8 DB-gated）
> **Implemented**：索引已落地（Model 声明 + init_db 幂等 DDL）；
> 未改 API / DTO / 查询语义 / 列定义 / 历史数据；无 backfill；无复合索引。

```text
Index:    ix_llm_usage_record_assistant_request_id
Column:   assistant_request_id
Type:     B-tree（CREATE INDEX … USING btree (assistant_request_id)）
Unique:   No
Nullable: Yes（列仍 VARCHAR(128) NULL；索引包含 NULL，历史行不变）
Purpose:  Assistant Trace LLM Usage exact-match lookup
          （WHERE assistant_request_id = ? ORDER BY created_at ASC, id ASC）

实现：
    ① Model：llm_usage_record.py __table_args__ 声明该 Index（新库随 create_all 创建）
    ② 既有库：init_db.ensure_assistant_request_id_index() 幂等 DDL
       （CREATE INDEX IF NOT EXISTS；连续执行不失败）
    ③ 未引入 Alembic（沿用项目既有 create_all + ensure_* 机制）

不变：API contract unchanged · Query semantics unchanged ·
      Historical NULL values unchanged · No backfill · No composite index ·
      既有索引未删除 / 未改动（pkey · created_at · uq request_id）

LLM Usage 生产接线（Phase 3.12 Step 56 实施 → `docs/evaluation/phase-3.12-step-56-llm-usage-production-wiring.md`）：
    `get_default_llm_client()` → `get_default_accounting_sink()`（**进程级单例**）
        ├── `get_engine() is None`（无 DATABASE_URL）→ **NoopAccountingSink**（旧行为）
        └── DB 已配置 → **DatabaseLLMAccountingSink** → Repository → ai_ops.llm_usage_record
    `create_llm_client()` 默认语义**不变**（None → Noop）；无新增配置 / 环境变量
    实测：`/api/ai/chat` RAG → 1 行 usage（provider id 与 assistant_request_id 不同）
          → `GET /api/observability/assistant-trace/{A}` 读回；失败/无 DB → 0 行且业务不变
    测试：`tests/test_llm_usage_production_wiring.py`（14）·
          `tests/test_llm_usage_production_wiring_e2e_db.py`（9，DB-gated）

## 8.68 Assistant Outcome Contract（Phase 3.12 Step 63 — Implemented）

> 实现：`backend/app/dto/assistant_outcome.py`（StrEnum + 纯判定函数）·
> `ai_orchestrator_service.py`（4 处 metadata 站点）
> 测试：`tests/test_assistant_outcome.py`（22）
> （任务书建议编写为 §8.14；该编号已被 Phase 3.10.14 占用 → 使用下一个空位 §8.68）

```text
Assistant Outcome（4 态固定；minimal production contract）
    ├── SUCCESS   请求已完成并产生有效业务响应（含 T2SQL row_count=0）
    ├── EMPTY     正常完成但无可提供的业务结果（仅 RAG rag_used_chunks=0）
    ├── REFUSED   系统明确拒绝执行（预期安全行为；Text-to-SQL refusal）
    └── FAILED    无法正常完成（LLM / RAG / T2SQL 耗尽 / SQL 执行失败 / 能力禁用 /
                  **Tool 业务失败（HTTP 200 + tool_success=false）**）

Outcome ≠ HTTP status · Outcome ≠ LLM status · Outcome ≠ Tool status ·
Outcome ≠ RAG status  · Outcome ≠ SQL execution status

判定责任：AIOrchestratorService（Assistant 层唯一判定点）
判定输入白名单：route · refused · tool_success · rag_used_chunks
    （+ 调用方失败状态；禁止 exception.message / prompt / SQL / chunk 正文 / 凭据 /
      stack trace；**禁止 content 文本匹配**）
优先级：REFUSED > FAILED > EMPTY > SUCCESS

Current external representation: `metadata.outcome`（envelope 不变：
    route / content / data / metadata；HTTP 语义不变 —— Tool 失败仍 200、
    refusal 仍 200、RAG 空检索仍 200）
异常路径（4xx/5xx）：响应体仍为 `{"detail": ...}`（未追加字段）→
    FAILED 在错误响应上**不对外暴露**（已知限制，见 Step 63 报告）

error_class = **not implemented**（未来可选扩展，需 allowlist 枚举）
Trace persistence = **not implemented**（本阶段仅 Runtime Outcome）
```

---

Outcome 契约设计（Phase 3.12 Step 62 Audit → `docs/evaluation/phase-3.12-step-62-assistant-outcome-contract-audit.md`）：
    候选 Contract（**仅测试内 projection，未实现**）：outcome ∈ {SUCCESS, EMPTY, REFUSED, FAILED}
    优先级 **REFUSED > FAILED > EMPTY > SUCCESS**；判定白名单 = status_code · route ·
        refused · tool_success · rag_used_chunks（禁 detail/content/data/prompt/SQL/chunk/凭据）
    关键结论：**HTTP 200 ≠ 业务 SUCCESS**（TOOL 失败 = 200 + tool_success=false ⇒ FAILED）；
        REFUSED/EMPTY 均 = 200；T2SQL 0 行定为 SUCCESS（candidate decision）；
        LLM/Tool/RAG/Executor 状态均 ≠ Assistant outcome（Case J 当前 Not observable）
    未来增量推荐：`metadata.outcome`（envelope 不变）· Trace 走统一 Outcome Read Model（方案 3）
    error_class = 后续可选扩展（需 allowlist 枚举；当前异常类名非稳定契约）
    测试：`tests/test_assistant_outcome_contract_audit.py`（21 离线；含 A~J projection cases）

Outcome 边界（Phase 3.12 Step 61 Audit → `docs/evaluation/phase-3.12-step-61-assistant-trace-outcome-audit.md`）：
    `AIOrchestrationResult` = route + content + data + metadata（**无** outcome / status 字段）
    route = 能力选择结果 ≠ 业务 Outcome；outcome 语义分散：`metadata.tool_success`（TOOL）·
        `metadata.refused`（T2SQL refusal）· 空检索靠 `rag_used_chunks=0` + 固定文案推断 ·
        失败靠 HTTP 5xx（LLM / RAG / 生成耗尽 状态码相同，仅 detail 类名不同）
    Tool 业务失败 = **HTTP 200**；refusal = 200 + data=None；失败 detail 仅含**异常类名**（无原件）
    Trace 只能回答"调用了什么"（3 段 records），**不能**回答 route / success / refusal / empty /
        HTTP 结果 / 失败原因（Tool 记录 error_code·error_type 在通用异常时为 None）
    决策：**OUTCOME_BOUNDARY_MISSING**（Future Step Candidate 仅记录，未实现）
    测试：`tests/test_assistant_trace_outcome_audit.py`（10 离线）

多路径关联（Phase 3.12 Step 60 Audit → `docs/evaluation/phase-3.12-step-60-assistant-trace-multi-path.md`）：
    同一 `/api/ai/chat` 入口下：RAG → llm_usage 1 + rag_executions 1（request_id = A）
    · TOOL → tool_executions 1（request_id = B）· TEXT_TO_SQL → llm_usage **2**（真实重试 2 次）
    跨请求隔离 A/B/C 严格集合成立（无污染）· Empty = 200 + 三段 [] ·
    Failure（LLM 500）→ HTTP 500 + llm_usage []（usage=None 不落库）+ rag 1（异常路径设计）
    Provider request_id ≠ assistant_request_id（互不覆盖）
    测试：`tests/test_assistant_trace_multi_path_e2e.py`（2 离线 + 7 DB-gated；入口统一）

Trace 分页边界（Phase 3.12 Step 59 Audit → `docs/evaluation/phase-3.12-step-59-assistant-trace-pagination-audit.md`）：
    Pagination = **NOT IMPLEMENTED**（HTTP 仅 1 path 参数；无 limit/offset/page/cursor/total/has_more）
    单请求上限 = **3 条**（默认装配；代码级最坏 12）· Payload < 1.5 KiB
    排序稳定（LLM created_at,id · Tool id · RAG id）· 查询 = **3 次 SELECT**（无 N+1 / 无 lazy loading）
    索引：llm.assistant_request_id · tool.request_id · rag.request_id（均 btree，valid）
    当前 dev volume = 0 · 决策 = **DEFER**（触发条件见文档；OFFSET vs cursor 推迟）
    测试：`tests/test_assistant_trace_pagination_audit.py`（14 离线 + 2 DB-gated）

真实 Smoke（Phase 3.12 Step 58 → `docs/evaluation/phase-3.12-step-58-real-llm-usage-smoke.md`）：
    真实 DeepSeek（api.deepseek.com）→ **生产默认 Client**（非 Mock；sink = Database）→
    恰好 1 条 usage（provider=deepseek · model=**响应值** deepseek-flash · total_tokens=10）
    → `GET /api/observability/assistant-trace/{A}` 可读（llm_usage=1）
    assistant_request_id（step58-smoke-…）**≠** provider request_id（9368cae4…）✅
    清理：精确 DELETE（WHERE assistant_request_id = A AND provider）→ residue = 0
    测试：`tests/test_llm_usage_real_llm_smoke.py`（默认 SKIPPED；
        `RUN_REAL_LLM_TEST=1` 或 `RUN_REAL_LLM_TESTS=1` 显式 opt-in；未引入新 marker/配置）

DB 安全审计（Phase 3.12 Step 57 → `docs/evaluation/phase-3.12-step-57-llm-usage-production-db-safety.md`）：
    Session / Transaction / Connection **全部短生命周期**（per-record；实测 commit/rollback/close
    + 连接归还 Pool + 0 idle-in-transaction + 0 Session 泄漏）
    Pool（实测）：pool_size=5 · max_overflow=10 · pool_pre_ping=True ·
        pool_timeout=30s（SQLAlchemy 默认）· pool_recycle=-1（未配置）→ **15 并发连接上限**
    失败隔离：连接超时 / 断连 / INSERT 失败 / 事务失败 / **池超时**（TimeoutError ⊂ SQLAlchemyError）
        / 取消 → 全部收敛为 warning，**不改变 LLM 业务结果、不触发 retry**（未发现未隔离路径）
    幂等：ON CONFLICT DO NOTHING + `uq_llm_usage_record_request_id`（UNIQUE, request_id）；
        `assistant_request_id` **非唯一**（仅关联键，不参与幂等）
    测试：`tests/test_llm_usage_production_db_safety.py`（10 离线 + 3 DB-gated）

审计（Phase 3.12 Step 55 → `docs/evaluation/phase-3.12-step-55-llm-usage-production-wiring-audit.md`）：
    Implementation exists ✅（sink / service / bridge / repository / 幂等 / 隔离 / tests）
    Production wiring exists ❌（`get_default_llm_client()` 不传 accounting_sink
        → `LLMClient.__init__` 回落到 **NoopAccountingSink**；刻意设计，非 bug）
    ⇒ 生产默认 Assistant Trace `llm_usage[] = []`（Tool / RAG 段正常）
    最小接线点（仅设计）：`llm/client.py::get_default_llm_client()`（**不**改
        `create_llm_client()` 默认语义）；Recommendation = **DESIGN READY**

Trace 数据量 / 分页（Phase 3.12 Step 54 审计 → `docs/evaluation/phase-3.12-step-54-trace-pagination-audit.md`）：
    三段均**有界**（LLM ≤4 · Tool ≤1 · RAG ≤1 ⇒ 单 id 上限 **6** 条，默认配置）
    排序键不同（LLM created_at,id / Tool id / RAG id）· Tool·RAG **不暴露主键**
    响应体积：典型 <1.2 KiB · 上限场景 <1.5 KiB（实测真实 DTO）
    结论 **Pagination Status: DEFER**（无 page / cursor / LIMIT / OFFSET；Triggers A~H 见文档）

生产安全（Phase 3.12 Step 53 审计 → `docs/evaluation/phase-3.12-step-53-index-production-safety.md`）：
    init_db **不**被应用启动调用（main.py / api / 部署物 = 0 DDL 调用点）→ 保留 Option A
    局限：普通 CREATE INDEX 阻塞写且位于 init_db 单事务（锁窗口 = 整个事务）；无 lock_timeout
    CONCURRENTLY 与 `engine.begin()` 事务模型不兼容 → Option B 不可直接落地
    生产建索引 = 运维一次性操作（Option C Runbook：检查存在/定义/validity → 低峰 CONCURRENTLY → 复查）
```

---

## 8.69 Assistant Outcome Persistence（Phase 3.12 Step 64 — Implemented）

> 前置：§8.68（Step 62 Outcome Contract · Step 63 `metadata.outcome`）

```text
POST /api/ai/chat
    ↓
AIOrchestratorService.execute()             ← **唯一** Outcome 判定点（Step 63）
    │   成功：result.metadata["outcome"]（**只读，不重新推断**）
    │   失败（Router / RAG / Tool / T2SQL / 能力禁用 / SQL 执行）→ FAILED
    ↓ best-effort（失败只 warning；**绝不**变成业务失败，**不** retry）
BestEffortAssistantOutcomeRecorder
    ↓ AssistantOutcomePersistenceService（校验 + enum → str）
AssistantOutcomeRepository  （INSERT … ON CONFLICT (assistant_request_id) DO NOTHING）
    ↓
ai_ops.assistant_outcome_record             ← 新增表（request-level 终态）
    ↓ AssistantOutcomeQueryService（只读）
GET /api/observability/assistant-trace/{assistant_request_id}
    ↓
{ assistant_request_id, outcome, llm_usage[], tool_executions[], rag_executions[] }
```

```text
Schema（最小）：ai_ops.assistant_outcome_record
    id BIGINT PK · assistant_request_id VARCHAR(128) NOT NULL **UNIQUE**
    · outcome VARCHAR(16) NOT NULL · created_at TIMESTAMPTZ NOT NULL
    无 route / status / error_class / error_message / content / prompt / SQL
    全新表随 Base.metadata.create_all() 创建（无 Alembic / 无 backfill）

语义：
    Outcome is Assistant-level；LLM / Tool / RAG execution status remain independent
    Idempotent + **first-write-wins**（重复写入 DO NOTHING → 不覆盖终态）
    历史 Trace（无行）→ outcome = **null**（**不猜**：不看 llm_usage / Tool / RAG /
        HTTP status / route 推断 SUCCESS）
    终态四值：SUCCESS / EMPTY / REFUSED / FAILED（无 error_class）
    Persistence failure does not affect business result
        （recorder 收敛 warning + Orchestrator 兜底 try/except；
          HTTP status / error body / Refusal 200 / Tool 200 failure / RAG empty 200 全不变）

装配（Composition Root = api/orchestrator_chat.py）：
    DB 已配置 → _build_outcome_recorder() → BestEffortAssistantOutcomeRecorder（进程级单实例）
    无 DATABASE_URL → None（不注入 → 零开销跳过，行为与 Step 63 前一致）
    Trace 侧：get_assistant_trace_query_service() 注入 AssistantOutcomeQueryService
    读边界失败 → 既有 502 语义（**不**降级为 null）
    测试：tests/test_assistant_outcome_persistence.py（22 离线 + 10 DB-gated）
        · tests/conftest.py（Step 64 引入）：**任何** pytest 会话结束时，按 id 水位
          清理「本次会话新增」的 outcome 行（测试专用残留守卫；不触碰既有数据）
          —— 因为生产接线是真的：驱动真实 /api/ai/chat 的 E2E（含离线）都会写 1 行
```

---

## 8.70 Unified Timeline 可行性（Phase 3.12 Step 65 — Audit Only / BLOCKED）

> 记录：`docs/evaluation/phase-3.12-step-65-unified-timeline-audit.md`
> 测试：`tests/test_assistant_timeline_audit.py`（30；离线 · 无 DB · 无网络）
> **未实现 Timeline**：production code / DB schema / API / migration 变更 = 0。

```text
Correlation   ✅ 四源均可按 Assistant Request 关联
                 LLM: assistant_request_id + request_id(Provider，两维度严格分离)
                 Tool/RAG: request_id == Assistant Request ID（无独立 scope）
                 Outcome: assistant_request_id UNIQUE（至多 1 条终态）
组内排序       ✅ LLM (created_at,id) · Tool/RAG (id) · Outcome (至多 1 行)
跨源排序       ⚠️ 仅同钟域可比：LLM↔Outcome 同 DB 钟（DERIVED，反映**落库**序）；
                 Tool↔RAG 同 App 钟（DERIVED）；跨钟域 = UNSAFE_TO_DERIVE
event_id       ❌ NOT_AVAILABLE（四表 / 四 DTO / View / Snapshot 均无；禁止生成）
sequence       ❌ NOT_AVAILABLE（无列；读路径无 enumerate/sorted/merge）
缺失事件       ❌ Router 决策 / Validator 判定 / Executor 执行 / Tool 参数提取
缺时间字段     ❌ LLM·Outcome 无 started_at/finished_at/duration_ms；
                 Tool·RAG 无 created_at；attempt/route/success（LLM 侧）不可得

结论：**Assistant Timeline（按来源分组 + 组内有序 + 显式 not_available）= 可做**；
      **Unified（单一全局有序事件流）= BLOCKED**（缺事件身份 / 统一时间 / 缺失事件）
```

---

## 8.71 Assistant Timeline 分组投影（Phase 3.12 Step 66 — Implemented / Read Model Only）

> 前置：§8.70（Step 65 审计：Assistant Timeline = READY，Unified = **BLOCKED**）

```text
Assistant Trace（四段事实）
    ↓ AssistantTimelineQueryService（只读投影；**不** merge / **不**跨组排序）
AssistantTimeline（frozen）
    ├── llm_events      LLM Usage       created_at ASC, id ASC
    ├── tool_events     Tool Execution  id ASC
    ├── rag_events      RAG Execution   id ASC
    └── outcome_event   Outcome         0 或 1 条（UNIQUE）

AssistantTimelineEvent（frozen；9 字段）
    assistant_request_id · source · event_type · source_id
    started_at · finished_at · duration_ms · created_at · status
    source     ∈ {llm_usage, tool_execution, rag_execution, assistant_outcome}
    event_type ∈ {LLM, TOOL, RAG, OUTCOME}（无 ROUTER / VALIDATOR / EXECUTOR）
    source_id  = **真实数据库主键**（llm/tool/rag/outcome .id；禁止生成 / 伪造）
    时间映射（Step 65 §十）：LLM·Outcome 只有 created_at（DB 钟）；
                            Tool·RAG 只有 started/finished/duration（App 钟）—— **钟域互斥**
    status     ：LLM·RAG = None（表中无 success，不推断）·
                 Tool = success/failed · Outcome = SUCCESS/EMPTY/REFUSED/FAILED

不做的（Step 65 结论未变）：
    event_id · sequence · span_id · parent_event_id · 统一 started_at
    · 跨钟域排序 · 合并为 events[] · Validator/Executor 事件 · HTTP 端点
边界：只读（DB 只经既有 Query Service；无 create_engine / Session）·
     未知 request → 空 Timeline（非 404）· 无终态记录 → outcome_event=None（不推断）·
     跨请求严格隔离 · Assistant Trace API 与 LLM/Tool/RAG 持久化 schema 未改
测试：tests/test_assistant_timeline_projection.py（33；离线 · Fake 读边界）
```

---

## 8.72 Assistant Timeline HTTP API 可行性（Phase 3.12 Step 67 — Audit Only / READY）

> 记录：`docs/evaluation/phase-3.12-step-67-timeline-api-audit.md`
> 测试：`tests/test_assistant_timeline_api_audit.py`（26；离线 · 无 DB · 无网络）
> **未实现 endpoint**（App 中无 timeline 路径；`api/` 无 timeline 模块；main.py 未注册）。

```text
候选：GET /api/observability/assistant-timeline/{assistant_request_id}   ← NOT IMPLEMENTED
响应（候选）：{ assistant_request_id, llm_events[], tool_events[],
               rag_events[], outcome_event }      （**分组**：无 merged events[]）
顺序：Grouped ordering only（llm created_at,id · tool/rag id · outcome ≤1）；
       HTTP 层**不得**跨组排序 / 生成 sequence
错误：沿用 Trace 约定 400（非法 id）/ 422（长度）/ 502（读边界不可用）/ 500；
       不泄漏 DB 异常文本 · 未知 request → **200 + 空分组**（不是 404）
安全：只输出 identity / source / event_type / timing / status；
       source_id = 内部 BIGINT 主键（**唯一事件身份**，暴露并文档化；隐藏则无身份）
体积：真实规模 ≈1.38 KiB；100 events ≈18.8 KiB（< 64 KiB）⇒ Pagination = DEFER
授权：项目**无** request-level 认证（知道 id 即可读）—— 与既有 Trace API 同级别，仅记录
决策：**READY FOR API IMPLEMENTATION**（条件是遵守上述契约，不实现 Unified Timeline）
```

---

## 8.73 Assistant Timeline HTTP Read API（Phase 3.12 Step 68 — Implemented）

```text
GET /api/observability/assistant-timeline/{assistant_request_id}
    router: backend/app/api/assistant_timeline.py（tags=["observability"]，prefix="/api"）
    DTO   : backend/app/dto/assistant_timeline_api.py
             AssistantTimelineEventResponse（**9 字段**）· AssistantTimelineResponse（5 字段）
    装配   : orchestrator_chat.get_assistant_timeline_query_service()（Composition Root）

响应（Grouped Timeline Projection）：
    { assistant_request_id, llm_events[], tool_events[], rag_events[], outcome_event }
    事件 9 字段：assistant_request_id · source · event_type · source_id ·
                started_at · finished_at · duration_ms · created_at · status

**这是 Grouped Timeline Projection，不是 Unified Timeline**：
    没有 event_id · sequence · span_id · parent_event_id · trace_id
    没有全局排序（四组各自有序：llm created_at,id / tool·rag id / outcome ≤1）
    没有 pagination（DEFER）· 没有 merged events[]

source_id = **内部事件标识**（来源表 BIGINT 主键）：
    仅用于同一次 assistant_request_id 内区分事件；
    **不是** global event id、**不**表示顺序、**不得**跨请求 / 跨系统引用

契约：
    unknown request   → 200 + 四段空（**不是** 404）
    历史无 outcome    → outcome_event = null（不推断）
    错误映射          → 400（非法 id）/ 422（路径长度）/ 502（读边界）/ 500（未知）
                        detail 固定文案，不泄漏 DB 异常 / SQL / 路径 / 凭据
    显式逐字段映射    → 无 model_dump / asdict；API 不接触 Repository / Engine / Session
授权：本接口**没有** request-level authorization
    （知道 assistant_request_id 即可读取；与既有 Trace API 同一暴露级别；未引入认证）
```

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
