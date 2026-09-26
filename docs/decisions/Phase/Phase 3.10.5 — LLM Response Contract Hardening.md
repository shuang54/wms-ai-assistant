# Phase 3.10.5 — LLM Response Contract Hardening

## 一、目标

在 Phase 3.10.4 已完成 `LLMResponse / LLMUsage` 的基础上，对 LLM 响应契约进行一次**边界加固**。

本阶段只解决：

> 不同 Provider / Mock / 异常 Provider Response → 稳定、可预测的 LLMResponse

目标不是增加新业务能力，而是保证现有 Contract 在异常输入和未来新增 Provider 时仍然稳定。

---

## 二、开始前必须先阅读

不要直接修改代码。

先完整阅读并理解：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/structured.py
backend/app/llm/retry.py

tests/test_llm_response.py
tests/test_llm_provider.py
tests/test_llm_structured.py

backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py

docs/architecture.md
```

同时搜索：

```text
LLMResponse
LLMUsage
tool_calls
finish_reason
metadata
request_id
usage
generate(
chat(
```

必须以当前实际代码为准，不要根据任务书假设代码结构。

---

# 三、本阶段核心 Contract

最终保证：

```text
Provider
   │
   ▼
LLMResponse
   │
   ├── content
   ├── tool_calls
   ├── model
   ├── finish_reason
   ├── usage
   └── metadata
```

上层业务不得依赖：

```text
choices
message
response.json()
httpx.Response
OpenAI SDK response object
DeepSeek SDK response object
```

---

# 四、LLMResponse 不变量

检查并保证以下规则。

## 4.1 content

允许：

```text
str
None
```

不得偷偷进行：

```text
str(...)
strip(...)
JSON parse
```

除非当前既有代码已经明确这样做。

本阶段不要改变既有 content 语义。

---

## 4.2 tool_calls

保持：

```text
tuple[ToolCall, ...]
```

默认：

```text
()
```

必须保证 Provider 层不会向上层泄漏原始 tool-call SDK 对象。

不要修改 Tool Calling 的业务逻辑。

---

## 4.3 model

保持：

```text
str | None
```

来源必须是 Provider 实际响应中的 model。

不能使用配置中的 model 作为 fallback。

例如：

```text
response.model = None
```

是合法结果。

不要：

```text
response.model or settings.model
```

---

## 4.4 finish_reason

保持：

```text
str | None
```

允许：

```text
stop
length
tool_calls
None
```

不要建立新的业务枚举，也不要改变已有值。

未知字符串原则上保持原值，不做猜测性转换。

---

# 五、LLMUsage 不变量

检查并强化：

```text
prompt_tokens: int | None
completion_tokens: int | None
total_tokens: int | None
```

必须满足：

### 允许

```text
None, None, None

100, None, None

None, 20, None

100, 20, 120
```

### 拒绝

```text
-1, 20, 19

100, 20, 130
```

### 类型

必须拒绝：

```text
True
False
"100"
100.0
[]
{}
```

不能发生隐式类型转换。

---

# 六、Metadata 边界

检查当前 metadata 白名单机制。

允许的内容必须仍然限定在：

```text
provider
request_id
```

如果当前代码已经存在其他明确、安全、稳定的字段，可以保留，但必须说明来源和用途。

禁止加入：

```text
api_key
authorization
password
headers
cookies
raw_response
prompt
system_prompt
messages
database_url
connection_string
```

不得保存完整 Provider Response。

不得通过：

```text
vars()
dict(response)
response.__dict__
```

等方式把 SDK 对象整体暴露出来。

---

# 七、增加 Provider Contract Tests

新增测试，验证未来新增 Provider 时必须遵守相同 Contract。

至少覆盖：

### 1. 最小响应

```text
content only
```

得到：

```text
LLMResponse(
    content=...,
    model=None,
    finish_reason=None,
    usage=None,
    metadata=...
)
```

### 2. 完整响应

验证：

```text
content
model
finish_reason
usage
metadata
```

全部正确。

### 3. 缺失字段

逐个验证：

```text
model missing
finish_reason missing
usage missing
request_id missing
```

不会报错。

### 4. 非法字段类型

验证：

```text
model = 123
finish_reason = {}
request_id = 123
```

不会污染最终 Contract。

### 5. usage 异常

验证：

```text
negative
bool
string
float
sum mismatch
```

符合 Phase 3.10.4 的既定行为。

### 6. Raw SDK isolation

构造包含：

```text
choices
secret_field
authorization
api_key
headers
```

的模拟 Provider Response。

确认最终：

```text
LLMResponse
```

无法访问这些原始字段。

---

# 八、Mock Provider Contract

检查现有：

```text
MockLLMClient
FakeLLMProvider
```

不要重新设计 Mock。

只要求：

```text
Mock → LLMResponse
```

时遵守相同 Contract。

特别验证：

```text
model
usage
finish_reason
metadata
```

不存在不合理的伪造数据。

不要生成假的：

```text
request_id
token usage
cost
latency
```

---

# 九、Provider Isolation Test

新增一个测试，模拟未来 Provider：

```text
FakeProvider
```

它只能向上层返回：

```text
LLMResponse
```

测试证明业务层不需要知道：

```text
DeepSeek
OpenAI
HTTP
SDK
choices
message
```

等具体实现。

如果发现当前代码存在 SDK 泄漏，只修复**最小必要范围**。

不要进行大规模重构。

---

# 十、向后兼容

必须确保以下旧行为保持不变：

```text
generate() → str

chat(messages) → str
```

如果当前实现是：

```text
chat(messages, tools=...) → LLMResponse
```

继续保持。

不要为了统一 DTO 而破坏 Phase 2 / Phase 3.6.2 的既有 API。

---

# 十一、Structured Response

只做 Contract regression。

保持：

```text
LLMResponse.content
        ↓
parse_structured_response()
        ↓
typed Pydantic result
```

禁止：

```text
LLMResponse
        ↓
自动 JSON parse
```

禁止 Provider 负责 Structured Parsing。

禁止把 metadata 和 structured output 混在一起。

Phase 3.10.3 保持独立。

---

# 十二、业务层禁止修改

本阶段原则上不得修改：

```text
backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py
AIOrchestrator
```

除非测试证明存在真实 Contract 不兼容。

如果必须修改：

1. 说明原因
2. 只做最小修改
3. 不改变业务逻辑
4. 不改变 Prompt
5. 不改变 Retry
6. 不改变 Router

---

# 十三、严格禁止

本阶段不要做：

```text
Observability
Cost Tracking
Streaming
Fallback
Model Router
Multi-model routing
Prompt optimization
Automatic model selection
OpenTelemetry
Prometheus
Langfuse
Tracing system
Database persistence of usage
Token billing
Cost calculation
```

尤其不要顺手加入：

```text
latency
price
cost
tokens × price
```

Phase 3.10.4 已明确：

> usage ≠ cost tracking

继续保持边界。

---

# 十四、文档

只修改：

```text
docs/architecture.md
```

如确实需要。

增加或完善：

```text
§8.5 LLM Response Metadata Contract
```

重点说明：

```text
Provider Response
        ↓
LLMResponse
```

以及：

```text
Raw SDK response remains inside provider boundary.
Upper layers depend only on LLMResponse.
```

不要提前写 Observability / Cost / Streaming 架构。

---

# 十五、测试要求

默认：

```powershell
python -m pytest -q
```

必须通过。

然后：

```powershell
python -m compileall backend tests scripts
```

必须通过。

如果项目支持：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

也执行完整 DB regression。

---

# 十六、必须验证的回归范围

至少确认：

```text
Phase 3.9.x
Phase 3.10.1
Phase 3.10.2
Phase 3.10.3
Phase 3.10.4

Text-to-SQL
RAG
Tool Calling
Router
AI API
Project Context
Refusal Handling
Structured Response
```

尤其确认：

```text
Refusal:
attempts = 1
retry = 0
validator = 0
executor = 0
```

保持不变。

---

# 十七、Network / DB

默认单元测试：

```text
LLM network calls = 0
DB calls = 0
```

全部使用：

```text
Fake Provider
MockTransport
in-memory object
```

DB regression 如果存在：

```text
RUN_DB_TESTS=1
```

按现有机制执行。

不得新增数据库写入。

不得新增 usage persistence。

---

# 十八、Git Diff 检查

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

重点检查：

1. 是否修改了业务 Prompt
2. 是否修改 Validator
3. 是否修改 Executor
4. 是否修改 Router
5. 是否修改 Project Context
6. 是否修改历史测试数据
7. 是否出现无关文件
8. 是否引入新依赖

发现无关修改必须清理。

---

# 十九、完成报告格式

最终只报告，不自动进入下一阶段。

使用：

```text
Phase 3.10.5 完成报告

1. 修改文件

2. LLMResponse Contract

3. LLMUsage Contract

4. Provider Isolation

5. Mock Provider

6. Metadata Boundary

7. Structured Response Compatibility

8. Tests

9. Regression

10. Network / DB

11. Production Path

12. Git Diff

13. 未完成项

Phase 3.10.5 STOP
```

如果存在任何失败：

```text
Phase 3.10.5 NOT READY
```

不得宣布完成。

如果全部通过：

```text
Phase 3.10.5 READY
```

然后：

```text
STOP
```

不要自动开始 Phase 3.10.6。
