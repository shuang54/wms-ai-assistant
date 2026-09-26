## Phase 3.10.2 — LLM 调用可靠性基础：Timeout / Retry 责任边界

## 一、目标

在现有 `wms-ai-assistant` 项目中，建立 LLM 调用的最小可靠性边界。

本阶段只解决一个问题：

> 明确 LLM 调用中的 Timeout 与 Retry 应该由哪一层负责，以及哪些错误允许重试。

当前架构：

```text
AI Core
  ↓
LLMProvider
  ↓
DeepSeekProvider
  ↓
OpenAICompatibleClient
  ↓
DeepSeek API
```

本阶段重点建立：

```text
业务层
  ↓
LLMProvider
  ↓
Provider / Client
  ↓
一次 LLM 调用
  ↓
Timeout / 可重试错误边界
```

---

# 二、开始前必须阅读

先阅读并理解：

1. `backend/app/llm/provider.py`
2. `backend/app/llm/deepseek_provider.py`
3. `backend/app/llm/client.py`
4. `TextToSQLService`
5. `RagService`
6. `ToolChatService`
7. `AIRouterService`
8. 当前 settings / LLM 配置
9. 当前 LLM tests
10. Phase 3.10.1 的 Provider 抽象说明

先确认当前真实调用链。

不要根据文件名猜测代码结构。

---

# 三、本阶段核心原则

必须明确：

## 1. Timeout 是基础设施层问题

LLM Provider / Client 应负责：

```text
HTTP request timeout
```

而不是：

```text
TextToSQLService
RagService
ToolChatService
```

各自实现 HTTP timeout。

---

## 2. Retry 不允许出现多层叠加

禁止出现：

```text
TextToSQL retry
    ↓
Provider retry
    ↓
HTTP client retry
```

导致一次失败实际产生：

```text
3 × 3 × 3 = 27
```

次请求。

本阶段必须明确：

> LLM 基础调用层只负责基础 transport-level reliability；业务层已有的语义 retry 必须与 transport retry 区分。

---

# 四、本阶段允许修改

允许修改：

```text
backend/app/llm/
tests/test_llm_provider.py
```

以及：

```text
docs/architecture.md
```

如果确实需要修改现有 settings/config，可以进行最小修改。

---

# 五、Timeout

首先检查现有 `OpenAICompatibleClient` 是否已经设置 timeout。

如果已经存在：

```text
timeout
```

不要重新实现。

应该：

1. 确认 timeout 的实际来源
2. 确认默认值
3. 确认是否能够通过配置覆盖
4. 增加测试证明 timeout 配置被正确传递

如果当前没有独立 timeout 配置：

可以增加一个最小配置，例如：

```text
LLM_TIMEOUT_SECONDS
```

但必须：

* 有合理默认值
* 不破坏现有 `.env`
* 不要求用户修改现有配置
* 不泄露 API Key
* 不改变 endpoint/model

不要建立复杂配置系统。

---

# 六、Retry 分类

建立一个最小的错误分类。

至少区分：

### 可重试

例如：

```text
网络连接暂时失败
连接超时
读取超时
HTTP 429
HTTP 5xx
```

### 不应重试

例如：

```text
HTTP 400
HTTP 401
HTTP 403
配置错误
API Key 无效
请求参数错误
代码 bug
```

注意：

不要凭空假设当前 SDK 的异常类型。

先检查当前实际使用的 OpenAI-compatible client / SDK 异常体系。

---

# 七、非常重要：本阶段不要真正增加自动 Retry

本阶段的主要目标是：

> **定义边界，而不是建立复杂重试机制。**

因此：

### 暂时不要修改为：

```text
call()
  ↓
失败
  ↓
sleep
  ↓
retry
  ↓
retry
```

不要增加：

* 指数退避
* jitter
* 最大重试次数
* retry queue
* circuit breaker
* fallback model
* rate limiter

这些以后单独处理。

本阶段最多增加：

```text
retry classification
```

例如一个纯函数：

```python
is_retryable_llm_error(error) -> bool
```

或者等价设计。

要求：

* 无网络
* 无 sleep
* 无副作用
* deterministic
* 容易测试

---

# 八、错误抽象

如果当前代码直接把 SDK 异常暴露到上层：

```text
OpenAI SDK Exception
```

可以考虑增加最小的 LLM 层异常抽象，例如：

```text
LLMError
LLMTimeoutError
LLMRateLimitError
LLMServiceError
LLMAuthenticationError
LLMInvalidRequestError
```

但：

### 不要为了“完整”而创建十几个异常类型。

只创建确实有必要的类型。

目标是让 AI Core 不依赖具体 SDK 异常。

---

# 九、Provider 层责任

最终希望形成类似：

```text
LLMProvider
    │
    ├── generate()
    └── chat()
```

Provider 对上层隐藏：

```text
OpenAI SDK
HTTP exception
HTTP status
transport implementation
```

但是：

### Provider 不负责业务语义 Retry。

例如：

```text
TextToSQL:
Validator rejected SQL
→ 重新生成 SQL
```

这是：

```text
TextToSQLService
```

的业务逻辑。

不能搬到 LLM Provider。

---

# 十、必须重点保护现有 Text-to-SQL Retry

当前 Text-to-SQL 已经存在：

```text
LLM
 ↓
SQL extraction
 ↓
Validator
 ↓
失败
 ↓
Retry
 ↓
LLM
```

这个 Retry 是：

> **语义级 Retry**

不是网络级 Retry。

必须保证本阶段之后：

```text
Validator reject
→ TextToSQL retry
```

仍然保持原来的行为。

特别不能变成：

```text
Validator reject
→ Provider retry
```

---

# 十一、Refusal 路径不能改变

Phase 3.9.25 已经建立：

```text
destructive request
       ↓
refusal
       ↓
TextToSQLResult(status="refusal")
       ↓
no retry
       ↓
no Validator
       ↓
no Executor
```

本阶段必须保证：

```text
refusal
```

不会被误判成：

```text
transport failure
```

也不能触发 LLM Retry。

---

# 十二、测试要求

至少增加以下测试。

## 1. Timeout configuration

验证：

```text
configured timeout
       ↓
OpenAICompatibleClient
```

参数正确传递。

不进行真实网络请求。

---

## 2. Retry classification

使用 fake exceptions / fake responses：

测试至少：

```text
timeout        → retryable
connection     → retryable
429            → retryable
500            → retryable
502            → retryable
503            → retryable

400            → not retryable
401            → not retryable
403            → not retryable
```

具体异常类型以当前 SDK 实际情况为准。

---

## 3. No automatic retry

验证当前 Client / Provider：

```text
one call
→ one underlying client invocation
```

本阶段不要产生隐式多次调用。

---

## 4. Provider abstraction

验证上层只依赖：

```text
LLMProvider
```

而不是 SDK exception。

---

## 5. Text-to-SQL semantic retry regression

验证：

```text
invalid SQL
→ Validator reject
→ LLM retry
→ second generation
```

仍然成立。

---

## 6. Refusal regression

验证：

```text
refusal
→ attempts=1
→ llm_calls=1
→ no retry
→ Validator=0
→ Executor=0
```

保持 Phase 3.9.25 行为。

---

## 7. Existing regression

执行现有：

```text
pytest
```

以及：

```text
python -m compileall backend tests scripts
```

不能出现现有测试回归。

---

# 十三、文档

在：

```text
docs/architecture.md
```

增加一个简短章节，说明：

```text
LLM Reliability Boundary
```

明确：

```text
Transport reliability
    ↓
LLM Client / Provider

Business semantic retry
    ↓
TextToSQLService / future business services
```

并说明：

```text
Provider 不负责业务语义 Retry
业务层不负责 HTTP transport retry
```

不要写成长篇理论。

---

# 十四、禁止事项

本阶段禁止：

* ❌ 修改 Prompt v1
* ❌ 修改 Prompt v2
* ❌ 修改 production prompt version
* ❌ 把 v2 切成 production default
* ❌ 修改 SQL Validator
* ❌ 修改 SQL Executor
* ❌ 修改 Text-to-SQL Dataset
* ❌ 修改 Ground Truth
* ❌ 修改 Evaluation Baseline
* ❌ 修改 RAG 检索
* ❌ 修改 Tool Registry
* ❌ 修改 Project Context
* ❌ 增加 Model Fallback
* ❌ 增加 Model Router
* ❌ 增加 Streaming
* ❌ 增加 Token Cost Tracking
* ❌ 增加 Circuit Breaker
* ❌ 增加 Rate Limiter
* ❌ 增加指数退避
* ❌ 增加复杂 Retry Framework
* ❌ 重新设计 LLM Provider
* ❌ 修改 API contract

尤其注意：

> 本阶段不追求“把 LLM 调用做成最终生产级可靠性框架”。

只建立正确的责任边界。

---

# 十五、完成后的报告

完成后报告：

### 1. 修改文件

```text
Modified:
...

Added:
...
```

### 2. Timeout

说明：

```text
Timeout owner:
Default:
Configuration:
```

### 3. Retry responsibility

明确：

```text
Transport retry:
Business retry:
```

### 4. Error classification

列出当前支持的：

```text
Retryable:
...

Non-retryable:
...
```

### 5. Tests

```text
pytest:
compileall:
```

### 6. Regression

```text
Text-to-SQL:
RAG:
Tool:
Router:
API:
Refusal:
Project Context:
```

### 7. 实际未完成项

如果没有：

```text
None
```

不要为了形式制造问题。

---

# 十六、强制 STOP

Phase 3.10.2 完成后：

**立即停止。**

不要自动进入：

* Phase 3.10.3
* Streaming
* Retry implementation
* Fallback
* Cost Tracking
* Model Router
* Observability

只返回本阶段完成报告，等待下一步指令。
