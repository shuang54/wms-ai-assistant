# Phase 3.10.3 — LLM Structured Output / Response Contract

## 一、阶段目标

在现有 `wms-ai-assistant` 项目中建立最小的：

```text
LLM Response Contract
```

本阶段只解决：

> 如何把 LLM 返回的原始字符串，安全、可验证地转换成 AI Core 可以使用的结构化对象。

目标架构：

```text
LLMProvider
    ↓
Raw Response
    ↓
Response Parser
    ↓
Schema Validation
    ↓
Typed / Structured Result
```

本阶段不做完整 Structured Output Framework。

不做多 Provider JSON Mode。

不做 Tool Calling 重构。

不做 Text-to-SQL 重构。

---

# 二、开始前必须阅读

先阅读真实代码：

```text
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/client.py

backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py

当前 Pydantic models / DTO
当前 tests
docs/architecture.md
```

重点确认：

1. 当前 LLM Provider `generate()` / `chat()` 返回什么。
2. 当前项目已经有哪些 Pydantic DTO。
3. 当前项目是否已经存在 JSON parsing 工具。
4. 当前 Tool Calling 是否已经有自己的结构化参数解析。
5. 当前 Text-to-SQL 是否有自己的结果 DTO。
6. 当前异常体系如何定义。

**不要根据文件名猜测接口。**

---

# 三、严格范围

本阶段允许：

```text
backend/app/llm/
backend/app/services/
tests/
docs/architecture.md
```

但只允许修改与：

```text
Structured Response Contract
```

直接相关的内容。

允许新增：

```text
backend/app/llm/structured.py
tests/test_llm_structured.py
```

如果现有目录结构已有更合理位置，优先复用。

---

# 四、核心设计

建立一个非常小的结构化响应能力。

建议：

```text
StructuredResponseParser
```

负责：

```text
raw string
   ↓
parse
   ↓
validate
   ↓
typed object
```

但不要让 Parser 与 DeepSeek 绑定。

不能出现：

```text
DeepSeekStructuredResponseParser
```

应该是通用能力。

---

# 五、Pydantic

优先使用项目当前已经使用的：

```text
Pydantic
```

不要引入新的 Schema Framework。

目标：

```python
class SomeResponse(BaseModel):
    ...
```

然后：

```text
raw LLM output
        ↓
Pydantic validation
        ↓
typed object
```

---

# 六、最小接口

设计一个最小的通用能力即可。

例如：

```python
parse_structured_response(
    raw: str,
    model: type[T],
) -> T
```

具体 API 根据项目现有风格决定。

必须满足：

### 输入

```text
raw LLM string
```

### 输出

```text
Pydantic model instance
```

### 失败

抛出：

```text
LLMStructuredOutputError
```

或项目已有等价 LLM 层异常。

不要把：

```text
json.JSONDecodeError
pydantic.ValidationError
```

直接泄漏到 AI Core。

---

# 七、JSON 格式

第一版只支持：

```text
JSON object
```

例如：

```json
{
  "route": "text_to_sql",
  "confidence": 0.92
}
```

不需要支持：

```text
YAML
XML
CSV
Markdown table
```

---

# 八、Markdown Fence

LLM 很容易返回：

````text
```json
{
  "route": "text_to_sql"
}
````

````

Parser 应支持：

```text
bare JSON
````

以及：

````text
```json
JSON
````

````

但是必须严格。

例如：

```text
hello
{"route": "text_to_sql"}
world
````

不要简单使用：

```python
find("{")
find("}")
```

把中间 JSON 强行截出来。

这种做法容易把自然语言中的 JSON 片段误判为结构化结果。

---

# 九、严格 JSON

Parser 必须拒绝：

```text
multiple JSON objects
```

例如：

```text
{"a": 1}
{"b": 2}
```

拒绝。

也拒绝：

```text
JSON + explanation
```

例如：

```text
Here is the result:
{"a": 1}
```

除非项目明确规定这是合法格式。

本阶段建议：

> structured output 必须是“纯 JSON object”或完整 JSON code fence。

---

# 十、类型校验

必须真正验证 Pydantic schema。

例如：

```python
class RouteResponse(BaseModel):
    route: Literal["rag", "tool", "text_to_sql"]
    confidence: float
```

以下应该：

```text
valid
```

```json
{
  "route": "rag",
  "confidence": 0.95
}
```

以下应该失败：

```json
{
  "route": "unknown",
  "confidence": 0.95
}
```

以及：

```json
{
  "route": "rag",
  "confidence": "high"
}
```

不要依赖 Python 类型转换把错误输入“修好”。

如果当前 Pydantic 配置会自动 coercion：

根据项目当前版本和风格决定是否需要 strict types。

不要为了本阶段引入复杂 strict-mode framework。

---

# 十一、额外字段

明确处理：

```json
{
  "route": "rag",
  "confidence": 0.95,
  "hack": "ignore this"
}
```

本阶段需要定义：

> Schema 是否允许 extra fields？

优先选择：

```text
extra = forbid
```

原因：

LLM 输出属于不可信输入。

未知字段应该暴露出来，而不是静默吞掉。

如果项目当前 Pydantic 版本 / BaseModel 基础配置已有统一策略，则遵循项目现有安全策略。

不要全局修改所有 Pydantic Model。

只影响本阶段新增的 Structured Response Model。

---

# 十二、不要把 Parser 做成 LLM Client

禁止：

```text
StructuredResponseParser
    ↓
DeepSeek API
```

Parser 只负责：

```text
raw string
    ↓
parse
    ↓
validate
```

LLM 调用仍然由：

```text
LLMProvider
```

负责。

---

# 十三、不要修改现有生产路径

这是本阶段最重要的限制。

**不要把 Structured Parser 强行接入：**

```text
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestratorService
```

本阶段只建立基础能力和测试。

原因：

当前 Text-to-SQL、RAG、Tool 已经有稳定 Evaluation / Regression 基线。

不要因为引入 Structured Output 而让历史行为发生变化。

---

# 十四、不要重构 Tool Calling

当前项目已经有：

```text
Tool Framework
LLM Tool Calling
Multi-Step Tool Calling
```

不要修改。

不要把：

```text
ToolCall
```

重新实现成：

```text
StructuredResponse
```

它们是不同层次：

```text
Structured Response
    = LLM 普通输出的结构化解析

Tool Calling
    = 模型请求调用工具的协议
```

本阶段只建立前者。

---

# 十五、测试模型

新增一个最小测试模型，例如：

```python
class DemoStructuredResponse(BaseModel):
    answer: str
    confidence: float
```

如果需要测试 enum：

```python
class DemoRouteResponse(BaseModel):
    route: Literal["rag", "tool", "text_to_sql"]
```

测试模型只放：

```text
tests/
```

或者测试模块内部。

不要把 Demo Model 放入 production domain。

---

# 十六、必须覆盖的测试

至少覆盖：

### 1. Bare JSON

```json
{"answer":"hello","confidence":0.9}
```

PASS。

---

### 2. JSON Code Fence

````text
```json
{"answer":"hello","confidence":0.9}
````

````

PASS。

---

### 3. Invalid JSON

```text
{"answer":
````

FAIL。

---

### 4. Wrong type

```json
{
  "answer": "hello",
  "confidence": "high"
}
```

FAIL。

---

### 5. Missing required field

```json
{
  "answer": "hello"
}
```

FAIL。

---

### 6. Unknown field

```json
{
  "answer": "hello",
  "confidence": 0.9,
  "hack": "unexpected"
}
```

FAIL。

---

### 7. Multiple JSON objects

```text
{"answer":"a"}
{"answer":"b"}
```

FAIL。

---

### 8. Natural language around JSON

```text
Here is the result:
{"answer":"hello","confidence":0.9}
```

按照本阶段严格规则：

FAIL。

---

### 9. Empty output

```text
""
```

FAIL。

---

### 10. Whitespace

前后存在普通 whitespace：

```text
  {"answer":"hello","confidence":0.9} 
```

PASS。

---

### 11. Wrong model

同一个 JSON 使用不匹配的 Pydantic Model。

FAIL。

---

### 12. No network

整个测试：

```text
0 network
```

不得调用 DeepSeek。

---

# 十七、异常 Contract

定义清晰的异常：

```text
LLMStructuredOutputError
```

至少包含：

```text
reason
```

可以区分：

```text
invalid_json
schema_validation_failed
unsupported_format
empty_output
```

但不要建立十几个异常类。

建议：

```python
LLMStructuredOutputError(
    reason="invalid_json"
)
```

即可。

不要把原始 LLM 输出完整放入异常 message。

原因：

LLM 输出可能包含：

* 敏感信息
* 超长内容
* Prompt injection
* 内部数据

错误信息应当保持简洁。

---

# 十八、安全要求

Structured Output 必须被视为：

```text
untrusted input
```

禁止：

```python
eval()
```

禁止：

```python
exec()
```

禁止：

```python
pickle.loads()
```

禁止任何动态 Python 执行。

只允许：

```text
JSON parsing
+
Pydantic validation
```

---

# 十九、测试 Regression

完成后执行：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

确保：

```text
Text-to-SQL PASS
RAG PASS
Tool PASS
Router PASS
API PASS
Project Context PASS
Refusal PASS
```

尤其关注：

```text
Phase 3.9.x
Phase 3.10.1
Phase 3.10.2
```

已有测试不得回归。

---

# 二十、文档

在：

```text
docs/architecture.md
```

增加一个简短章节：

```text
LLM Structured Response Contract
```

说明：

```text
Raw LLM Output
      ↓
JSON Parser
      ↓
Pydantic Validation
      ↓
Typed Structured Result
```

以及：

```text
Structured Response
≠
Tool Calling
```

并说明：

> Structured Output 当前作为独立基础能力存在，尚未接入现有 Text-to-SQL / RAG / Tool production path。

不要写成长篇文章。

---

# 二十一、禁止事项

本阶段禁止：

* ❌ 修改 Prompt v1
* ❌ 修改 Prompt v2
* ❌ 修改 production prompt version
* ❌ 把 v2 切换成 production default
* ❌ 修改 TextToSQLService
* ❌ 修改 SQL Validator
* ❌ 修改 SQL Executor
* ❌ 修改 RAG 检索
* ❌ 修改 Tool Registry
* ❌ 修改 Tool Calling
* ❌ 修改 AI Router
* ❌ 修改 AI Orchestrator
* ❌ 修改 Project Context
* ❌ 修改 Evaluation Dataset
* ❌ 修改 Ground Truth
* ❌ 修改 Historical Baselines
* ❌ 引入新 JSON Schema 第三方框架
* ❌ 引入 LangChain / LangGraph
* ❌ 引入 Instructor / Outlines 等结构化输出框架
* ❌ 增加自动 Retry
* ❌ 增加 Fallback
* ❌ 增加 Streaming
* ❌ 增加 Token Cost Tracking
* ❌ 增加 Model Router

尤其注意：

> 本阶段只是把“LLM 输出如何变成可信 DTO”这个基础打牢。

---

# 二十二、完成后报告

完成后只报告：

### 1. 修改文件

```text
Modified:
...

Added:
...
```

### 2. Structured Response 架构

```text
LLMProvider
  ↓
Raw Output
  ↓
Structured Parser
  ↓
Pydantic Validation
  ↓
Typed Result
```

### 3. Parser Contract

说明：

```text
支持：
...

拒绝：
...
```

### 4. Error Contract

```text
LLMStructuredOutputError
reason:
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
Project Context:
Refusal:
```

### 7. Production Path

明确：

```text
是否修改 TextToSQLService:
是否修改 RAG:
是否修改 Tool:
是否修改 Router:
是否修改 Orchestrator:
```

预期全部：

```text
NO
```

### 8. 网络 / DB

```text
LLM calls:
Network calls:
DB calls:
```

预期：

```text
0
```

### 9. 未完成项

如果没有：

```text
None
```

---

# 二十三、强制 STOP

完成 Phase 3.10.3 后：

**立即停止。**

不要自动进入：

* Phase 3.10.4
* Structured Output 接入 Router
* Structured Output 接入 Text-to-SQL
* JSON Mode
* Streaming
* Retry
* Fallback
* Cost Tracking
* Model Router
* Observability

只返回 Phase 3.10.3 完成报告，等待下一步指令。
