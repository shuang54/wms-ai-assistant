# Phase 3.10.4 — LLM Response Metadata / Usage Contract

你现在开始执行：

`D:\coding\ai\wms-ai-assistant`

本阶段目标：

> 在已有 LLMProvider、Timeout/Retry Boundary、Structured Response Parser 基础上，建立统一的 LLM Response Metadata / Usage Contract。

本阶段只解决：

```text
LLM Provider
    ↓
Raw Provider Response
    ↓
Normalized LLM Response
    ├── content
    ├── model
    ├── finish_reason
    ├── usage
    └── metadata
```

为后续 Observability / Cost Tracking / Debugging 提供稳定基础。

---

# 一、开始前必须阅读

先阅读真实代码：

```text
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/client.py
backend/app/llm/structured.py
backend/app/llm/retry.py
backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py
tests/test_llm_provider.py
tests/test_llm_structured.py
docs/architecture.md
```

同时搜索项目中：

```text
usage
model
finish_reason
response
completion
prompt_tokens
completion_tokens
total_tokens
```

先确认当前 OpenAI-compatible client 实际能够拿到哪些字段。

**不要根据 OpenAI/DeepSeek 文档猜测当前代码结构。**

---

# 二、阶段目标

建立一个统一的内部 DTO，例如：

```text
LLMResponse
```

建议至少包含：

```text
content: str
model: str | None
finish_reason: str | None
usage: LLMUsage | None
```

其中：

```text
LLMUsage
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
```

如果当前 provider 能可靠取得更多通用字段，可以保留，但不要过度设计。

---

# 三、核心架构

最终希望形成：

```text
LLMProvider
    ↓
LLMResponse
    ├── content
    ├── model
    ├── finish_reason
    └── usage
```

而不是：

```text
Service
 ↓
DeepSeek/OpenAI SDK Response
```

上层业务不得依赖：

```text
openai.ChatCompletion
httpx.Response
SDK-specific usage object
SDK-specific choice object
```

---

# 四、Provider Contract

检查当前：

```python
LLMProvider.generate()
LLMProvider.chat()
```

如果当前返回：

```text
str
```

不要贸然让所有生产服务同时大规模重构。

设计最小兼容方案。

优先考虑：

```text
LLMProvider
    ↓
LLMResponse
```

并保持现有业务行为：

```text
response.content
```

仍然能够被现有调用链正确使用。

如果必须修改 Provider return type：

必须同步更新：

```text
DeepSeekProvider
OpenAICompatibleClient
FakeLLMProvider
相关 tests
```

但：

**不要修改业务 Prompt。**

---

# 五、LLMUsage Contract

新增：

```text
LLMUsage
```

字段：

```text
prompt_tokens
completion_tokens
total_tokens
```

要求：

* 字段可以为 `None`
* 不允许负数
* 如果三个字段都存在，可以验证：

```text
total_tokens == prompt_tokens + completion_tokens
```

但注意：

**如果实际 provider 返回的数据存在不完整情况，不要为了通过校验而虚构数值。**

例如：

```text
prompt_tokens = 100
completion_tokens = None
total_tokens = None
```

这是合法的。

---

# 六、Model Contract

`model`：

```text
str | None
```

优先使用：

```text
Provider 实际响应中的 model
```

而不是：

```text
配置文件中的 model
```

原因：

实际响应才代表服务器真正返回的模型。

如果当前 mock 没有 model：

```text
None
```

不要强行填：

```text
deepseek-chat
```

---

# 七、Finish Reason

统一：

```text
finish_reason: str | None
```

例如：

```text
stop
length
tool_calls
```

当前 provider 没有返回：

```text
None
```

不要人为构造。

不要修改 Tool Calling 行为。

---

# 八、Metadata

可以设计：

```text
metadata: dict[str, Any]
```

但必须非常克制。

允许：

```text
provider
request_id
```

如果当前底层真实 response 能可靠获得。

禁止加入：

```text
API key
Authorization
password
database URL
prompt secret
完整原始 SDK response
完整 HTTP headers
```

尤其：

> 不允许把完整 provider response 塞进 metadata。

---

# 九、Request ID

如果当前 DeepSeek/OpenAI-compatible response 提供：

```text
id
```

可以将其规范化为：

```text
request_id
```

否则：

```text
None
```

不要自行生成 UUID 冒充 provider request ID。

本阶段不要增加 distributed tracing。

---

# 十、Raw Response 隔离

必须保证：

```text
SDK Response
```

只存在于：

```text
LLM Client / Provider
```

离开 Provider 后只能看到：

```text
LLMResponse
```

上层不得访问：

```text
response.choices
response.usage
response.id
```

等 SDK-specific 字段。

---

# 十一、兼容 Structured Response

Phase 3.10.3 已经存在：

```text
parse_structured_response(raw, model)
```

本阶段不要修改其职责。

正确关系：

```text
LLMProvider
    ↓
LLMResponse
    ↓
LLMResponse.content
    ↓
parse_structured_response()
    ↓
Pydantic Typed Result
```

不要把 Structured Parser 和 Provider Response Metadata 混在一起。

---

# 十二、错误处理

不要修改 Phase 3.10.2 的错误边界。

继续保持：

```text
LLMRequestError
LLMConfigError
LLMResponseError
LLMToolCallFormatError
LLMStructuredOutputError
```

如果 provider response 缺少 usage：

**不是错误。**

如果 provider response 缺少 finish_reason：

**不是错误。**

Metadata 是辅助信息，不得因为 metadata 缺失导致正常 LLM 调用失败。

---

# 十三、测试要求

新增：

```text
tests/test_llm_response.py
```

如果现有测试结构更适合，也可以放入：

```text
tests/test_llm_provider.py
```

至少测试：

## 1. Normal response

验证：

```text
content
model
finish_reason
usage
```

全部正确映射。

---

## 2. Missing usage

```text
usage = None
```

调用仍然成功。

---

## 3. Partial usage

例如：

```text
prompt_tokens = 100
completion_tokens = None
total_tokens = None
```

必须允许。

---

## 4. Missing model

```text
model = None
```

仍然成功。

---

## 5. Missing finish reason

```text
finish_reason = None
```

仍然成功。

---

## 6. Provider response isolation

Fake SDK response 包含：

```text
choices
usage
id
model
secret_field
```

最终：

```text
LLMResponse
```

不得暴露 SDK-specific object。

---

## 7. No raw response leakage

验证：

```text
LLMResponse.metadata
```

不包含：

```text
API key
Authorization
完整 raw response
```

---

## 8. Usage validation

测试：

```text
negative tokens → reject
```

如果三个 token 都存在：

```text
100 + 20 = 120 → pass
100 + 20 != 130 → reject
```

但不要阻止：

```text
partial usage
```

---

## 9. Fake provider

现有：

```text
FakeLLMProvider
```

需要能够构造：

```text
LLMResponse
```

但不要修改其测试语义。

---

## 10. Structured Response regression

确保：

```text
LLMResponse.content
        ↓
parse_structured_response()
```

仍然正常工作。

不允许因为本阶段导致：

```text
Phase 3.10.3 tests
```

回归。

---

# 十四、业务层兼容性

本阶段原则：

**不要让业务层为了读取 metadata 而大规模修改。**

以下服务：

```text
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestrator
```

如果当前不需要 metadata：

**不要修改。**

只有当类型变化确实要求兼容时，才做最小修改。

---

# 十五、禁止事项

本阶段严格禁止：

```text
❌ 修改 Prompt v1
❌ 修改 Prompt v2
❌ Promotion v2
❌ 修改 Text-to-SQL Generator
❌ 修改 SQL Validator
❌ 修改 SQL Executor
❌ 修改 RAG 检索
❌ 修改 Tool Registry
❌ 修改 Router 策略
❌ 修改 Orchestrator 策略
❌ 修改 Project Context
❌ 修改 Evaluation Dataset
❌ 修改 Ground Truth
❌ 自动 Retry
❌ Fallback Model
❌ Model Router
❌ Streaming
❌ Cost Tracking
❌ Metrics exporter
❌ OpenTelemetry
❌ Prometheus
❌ Logging framework 重构
❌ Distributed tracing
```

特别注意：

> 本阶段虽然保存 token usage，但不做成本计算。

例如禁止：

```text
token × price = cost
```

Cost Tracking 留到后续专门阶段。

---

# 十六、Documentation

在：

```text
docs/architecture.md
```

增加简短章节：

```text
§8.5 LLM Response Metadata Contract
```

说明：

```text
LLM Provider
    ↓
LLMResponse
    ├── content
    ├── model
    ├── finish_reason
    └── usage
```

并明确：

```text
LLMResponse 是 AI Core 内部统一 DTO
上层不依赖 SDK response
usage ≠ cost tracking
metadata ≠ observability system
```

不要写成长篇理论。

---

# 十七、网络 / DB

单元测试：

```text
LLM calls = 0
Network calls = 0
DB calls = 0
```

除非项目已有明确的 real LLM smoke test。

本阶段不要新增真实 API 测试。

---

# 十八、Regression

完成后执行：

```powershell
python -m pytest -q
```

以及：

```powershell
python -m compileall backend tests scripts
```

如果项目已有：

```text
RUN_DB_TESTS=1
```

机制，也执行完整 DB regression。

Windows PowerShell 使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

不要使用：

```text
RUN_DB_TESTS=1 pytest
```

---

# 十九、必须验证的历史能力

至少确认：

```text
Phase 3.9.x          PASS
Phase 3.10.1         PASS
Phase 3.10.2         PASS
Phase 3.10.3         PASS

Text-to-SQL           PASS
RAG                   PASS
Tool                  PASS
Router                PASS
API                   PASS
Refusal               PASS
Project Context       PASS
Structured Response   PASS
```

尤其确认：

```text
Refusal
→ attempts=1
→ no retry
→ no Validator
→ no Executor
```

没有变化。

---

# 二十、完成报告

完成后严格按照以下格式：

```text
Phase 3.10.4 完成报告

1. 修改文件
Modified:
...

Added:
...

2. LLMResponse Contract
说明：

3. LLMUsage Contract
说明：

4. Model / Finish Reason
说明：

5. Metadata
说明：

6. SDK 隔离
说明：

7. Structured Response 兼容
说明：

8. Tests
pytest:
compileall:

9. Regression
Text-to-SQL:
RAG:
Tool:
Router:
API:
Refusal:
Project Context:
Structured Response:

10. Network / DB
LLM calls:
Network:
DB:

11. Production Path
是否修改业务生产路径：

12. 未完成项
...

13. Git Diff
...

Phase 3.10.4 STOP
```

---

# 二十一、强制 STOP

完成本阶段后：

**立即停止。**

不要自动进入：

```text
Observability
Cost Tracking
Streaming
Fallback
Model Router
Prompt Optimization
Structured Output Production Integration
```

只返回 Phase 3.10.4 完成报告，等待下一步指令。
