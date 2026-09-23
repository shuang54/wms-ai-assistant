你现在开始实现项目 **Phase 3.6.2：LLM Function Calling 接入 Tool Framework**。

项目目录：

`D:\coding\ai\wms-ai-assistant`

---

# 一、本阶段目标

把 Phase 3.6.1 已完成的 Tool Framework 接入现有 LLM Client，实现最小 Function Calling 闭环。

目标：

```text
用户问题
    ↓
LLM
    ↓
判断是否需要 Tool
    ↓
返回 tool_call
    ↓
ToolRegistry.execute()
    ↓
ToolResult
    ↓
再次调用 LLM
    ↓
最终自然语言回答
```

本阶段只实现：

```text
LLM Function Calling
+
ToolRegistry
+
Mock Tools
```

最终可以实现类似：

```text
用户：
查询 MAT001 的库存

LLM：
调用 get_inventory
{
    "material_code": "MAT001"
}

Tool：
{
    "material_code": "MAT001",
    "warehouse_code": null,
    "quantity": 1000,
    "unit": "PCS"
}

LLM：
MAT001 当前库存为 1000 PCS。
```

---

# 二、严格禁止扩大范围

本阶段禁止：

* 不接真实 WMS
* 不接真实 ERP
* 不接真实业务数据库
* 不修改现有 `/api/chat` 行为
* 不修改 `/api/rag/answer`
* 不修改现有 RAG Pipeline
* 不做 Agent
* 不做 LangGraph
* 不做 MCP
* 不做多 Agent
* 不做循环 Agent
* 不做自动规划
* 不做 Query Rewrite
* 不做 Reranker 生产集成
* 不做 Hybrid Search
* 不做前端
* 不做 Tool 权限正式实现
* 不做 Tool 持久化
* 不做后台任务

尤其禁止把本阶段实现成：

```text
while True:
    LLM → Tool → LLM → Tool → ...
```

本阶段最多允许：

```text
第一次 LLM
    ↓
0 或 1 次 Tool Call
    ↓
第二次 LLM
    ↓
最终答案
```

**只支持单轮、单个 Tool Call。**

---

# 三、先阅读现有代码

实现前必须阅读：

```text
AGENTS.md

docs/requirements.md
docs/architecture.md

backend/app/llm/
backend/app/services/chat_service.py
backend/app/api/chat.py

backend/app/tools/base.py
backend/app/tools/registry.py
backend/app/tools/errors.py
backend/app/tools/mock_tools.py

tests/
```

重点确认：

1. 当前 `LLMClient` 接口
2. 当前 `OpenAICompatibleClient`
3. DeepSeek 配置
4. 当前 ChatService
5. ToolDefinition
6. ToolRegistry
7. ToolResult
8. 当前异常体系
9. 当前测试 Mock LLM 的写法

不要重复创建已有 LLM 基础设施。

---

# 四、先确认 OpenAI-Compatible Tool Calling 能力

现有 LLM Client 已经是 OpenAI-compatible。

需要扩展其能力，使其能够支持：

```python
tools=[...]
```

以及返回：

```text
tool_calls
```

但是必须保持现有：

```python
LLMClient.chat(messages)
```

调用方式兼容。

---

# 五、不要直接把 ToolDefinition 传给 OpenAI

Phase 3.6.1：

```python
ToolDefinition(
    name="get_inventory",
    description="查询库存",
    parameters={...}
)
```

LLM API 需要的是 OpenAI-compatible schema：

```json
{
  "type": "function",
  "function": {
    "name": "get_inventory",
    "description": "查询库存",
    "parameters": {
      "type": "object",
      "properties": {
        "material_code": {
          "type": "string",
          "description": "物料编码"
        }
      },
      "required": ["material_code"]
    }
  }
}
```

因此新增一个明确的转换层。

例如：

```text
ToolDefinition
      ↓
LLM Tool Schema
```

不要让 ToolDefinition 本身变成 LLM Provider 专用 DTO。

建议位置：

```text
backend/app/llm/
```

或者根据现有项目结构选择最合理的位置。

---

# 六、定义 LLM Tool Call DTO

需要能够表达：

```text
LLM 普通回答
LLM 请求 Tool
```

建议设计不可变 DTO，例如：

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
```

同时定义：

```python
@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
```

如果现有 LLM Client 已经存在类似 response abstraction，应优先复用，而不是重复设计。

---

# 七、兼容现有 LLMClient

当前项目已有：

```python
LLMClient.chat(messages)
```

不要破坏现有调用。

可以扩展为：

```python
chat(
    messages,
    *,
    tools=None,
)
```

要求：

```python
chat(messages)
```

现有代码行为完全不变。

新增：

```python
chat(
    messages,
    tools=...
)
```

用于 Function Calling。

---

# 八、解析 LLM Tool Call

OpenAI-compatible 返回通常类似：

```json
{
  "choices": [
    {
      "message": {
        "content": null,
        "tool_calls": [
          {
            "id": "call_xxx",
            "type": "function",
            "function": {
              "name": "get_inventory",
              "arguments": "{\"material_code\":\"MAT001\"}"
            }
          }
        ]
      }
    }
  ]
}
```

需要转换为：

```python
ToolCall(
    id="call_xxx",
    name="get_inventory",
    arguments={
        "material_code": "MAT001"
    }
)
```

要求：

* JSON arguments 解析失败时进入明确异常
* tool name 缺失时进入明确异常
* tool call id 缺失时进入明确异常
* 不允许静默吞掉 malformed response

---

# 九、创建独立 Tool Chat Service

不要修改现有：

```text
ChatService
```

新增：

```text
backend/app/services/tool_chat_service.py
```

建议：

```python
class ToolChatService:
    async def chat(
        self,
        message: str,
        *,
        registry: ToolRegistry,
    ) -> ToolChatResponse:
        ...
```

职责：

```text
1. 校验 message
2. 获取 registry.list_definitions()
3. 转换成 LLM tools
4. 调用 LLM
5. 判断是否存在 tool_call
6. 如果没有：
       直接返回最终回答
7. 如果有：
       execute Tool
8. 构造 tool message
9. 再调用一次 LLM
10. 返回最终回答
```

---

# 十、严格限制 Tool Calling 次数

本阶段：

```text
max_tool_calls = 1
max_llm_rounds = 2
```

例如：

```text
LLM #1
 ↓
1 Tool Call
 ↓
Tool Execute
 ↓
LLM #2
 ↓
最终回答
```

如果第一次 LLM 返回多个 Tool Call：

本阶段不要并行执行。

应明确处理为：

```text
ToolCallingError
```

或者使用项目已有异常体系设计一个明确异常。

不要偷偷执行多个 Tool。

---

# 十一、Tool Message 格式

Tool 执行后，需要把结果反馈给 LLM。

应形成类似：

```json
{
  "role": "tool",
  "tool_call_id": "call_xxx",
  "content": "{\"material_code\":\"MAT001\",\"quantity\":1000,\"unit\":\"PCS\"}"
}
```

注意：

ToolResult 本身是内部 DTO。

发送给 LLM 前再序列化。

不要把：

```text
traceback
DATABASE_URL
API KEY
SQL
embedding
```

放进 tool message。

---

# 十二、Tool Result 错误处理

例如：

```text
用户：
查询库存

LLM：
get_inventory({})
```

Tool Framework：

```text
success=false
error="参数校验失败..."
```

这时不要让 Python 异常直接打断整个请求。

应该把 ToolResult 转成 Tool Message：

```json
{
  "role": "tool",
  "tool_call_id": "...",
  "content": "{\"success\":false,\"error\":\"...\"}"
}
```

然后让 LLM 根据 Tool 错误生成自然语言回答。

---

# 十三、必须防止 Tool Schema 泄露内部信息

提供给 LLM 的 tools 只能来自：

```python
registry.list_definitions()
```

禁止把：

```text
ToolHandler
Python module
Python class
exception object
database information
```

传给 LLM。

---

# 十四、Mock Tool 场景

使用 Phase 3.6.1 已经存在：

```text
get_inventory
get_work_order
```

不要创建第三个 Tool。

需要测试：

### 场景 1：无需 Tool

用户：

```text
你好
```

期望：

```text
LLM #1
↓
普通回答
↓
结束
```

LLM 调用次数：

```text
1
```

Tool：

```text
0
```

---

### 场景 2：库存查询

用户：

```text
查询 MAT001 的库存
```

期望：

```text
LLM #1
↓
get_inventory
↓
ToolRegistry.execute()
↓
LLM #2
↓
最终回答
```

LLM：

```text
2 次
```

Tool：

```text
1 次
```

---

### 场景 3：工单查询

用户：

```text
查询工单 MO001
```

应该调用：

```text
get_work_order
```

---

### 场景 4：Tool 参数错误

模拟 LLM 返回：

```json
{
  "material_code": 123
}
```

Tool Framework 应拒绝。

但 Tool Chat Service 不应该崩溃。

应该：

```text
ToolResult(success=False)
↓
tool message
↓
LLM #2
↓
自然语言错误说明
```

---

### 场景 5：未知 Tool

模拟 LLM 返回：

```text
get_unknown_tool
```

不能执行未知工具。

最终必须安全失败。

---

# 十五、增加独立 API

不要修改：

```text
POST /api/chat
```

新增：

```text
POST /api/chat/with-tools
```

Request：

```json
{
  "message": "查询 MAT001 的库存"
}
```

Response：

```json
{
  "answer": "...",
  "tool_calls": [
    {
      "tool_name": "get_inventory"
    }
  ]
}
```

注意：

API Response 不要暴露：

```text
API key
DATABASE_URL
Python traceback
Tool handler
SQL
embedding
```

Tool arguments 是否返回给前端，需要根据当前安全设计判断。

本阶段建议：

```json
{
  "tool_name": "get_inventory"
}
```

只返回 Tool 名称，不返回内部执行细节。

---

# 十六、API 错误映射

保持现有 API 风格。

至少处理：

```text
LLM configuration
LLM request
LLM response
Tool validation
Tool execution
Tool calling protocol error
```

不要返回 traceback。

HTTP 状态码沿用项目已有 LLM/API 错误映射原则；如果新增异常没有合适映射，增加最小映射，不要重构整个错误体系。

---

# 十七、测试要求

新增：

```text
tests/test_tool_chat_service.py
tests/test_llm_tool_calling.py
tests/test_tool_chat_api.py
```

至少覆盖：

### LLM Client

* 无 tools 调用保持原行为
* tools 正确传给 OpenAI-compatible client
* 普通 response 解析
* tool_calls response 解析
* arguments JSON 解析
* malformed tool call
* 多 tool call 被拒绝

### ToolChatService

* 普通回答
* 单 Tool Call
* ToolResult 成功
* ToolResult 失败
* Unknown Tool
* 第二次 LLM 调用
* 不超过 2 次 LLM
* 不超过 1 个 Tool
* Tool Schema 正确传递
* 不直接持有数据库连接

### API

* `/api/chat/with-tools` 正常
* 普通问题
* Tool 问题
* Tool 错误
* 参数错误
* LLM 错误
* response 不泄露敏感信息

---

# 十八、真实 DeepSeek Smoke Test

如果项目已有真实 LLM smoke test 机制：

```text
RUN_REAL_LLM_TEST=1
```

新增一个可选测试：

```text
RUN_REAL_TOOL_CALLING_TEST=1
```

使用：

```text
get_inventory
```

验证 DeepSeek 是否真实返回 Tool Call。

注意：

真实测试只能使用 Mock Tool。

绝对不要接真实 WMS / ERP。

默认情况下测试必须 SKIP。

---

# 十九、回归测试

至少执行：

```bash
pytest tests/test_tool_framework.py -q
```

然后：

```bash
pytest tests/test_tool_chat_service.py \
       tests/test_llm_tool_calling.py \
       tests/test_tool_chat_api.py -q
```

然后完整：

```bash
pytest -q
```

如果已有 DB 测试机制，再按照当前项目方式执行相关回归。

---

# 二十、重点检查

完成后必须确认：

```text
/api/chat
```

行为不变。

```text
/api/rag/answer
```

行为不变。

```text
RagService
VectorSearchService
EmbeddingClient
KnowledgeIngestionService
RerankerClient
ToolRegistry
```

之间没有出现不必要耦合。

尤其不能出现：

```text
RagService → ToolRegistry
ToolRegistry → LLMClient
Tool → Database
```

本阶段正确依赖应该是：

```text
ToolChatService
    ↓
LLMClient
    ↓
ToolRegistry
    ↓
Tool
```

---

# 二十一、日志

增加必要日志，但不要记录敏感内容。

可以记录：

```text
tool_name
tool_call_count
llm_round
success
elapsed_ms
```

不要记录：

```text
API key
Authorization
DATABASE_URL
完整用户敏感内容
完整 Tool arguments（如果可能包含敏感数据）
traceback 到 API response
```

---

# 二十二、最终汇报

完成后必须停止，不得自动进入 Phase 3.6.3。

只汇报：

```text
Phase 3.6.2 COMPLETE

1. LLM Tool Calling 是否打通
2. LLM Client 修改内容
3. ToolCall DTO
4. ToolChatService
5. /api/chat/with-tools
6. Mock Tool 测试结果
7. Real DeepSeek Tool Calling 是否成功
8. LLM 调用次数
9. Tool 调用次数
10. 全量测试结果
11. 原 /api/chat 是否保持兼容
12. RAG 是否保持不变
13. 安全检查
14. Git diff / 未提交文件
15. 下一阶段建议

完成后立即停止，等待用户确认。

这一阶段完成后，你的项目会从 **“RAG 问答系统”** 正式跨到 **“RAG + Tool Calling AI 应用”**。这一步对你后面做真正的 WMS AI 助手很关键。
```
