你现在开始实现项目 **Phase 3.6.3：Multi-Step Tool Calling**。

项目目录：

`D:\coding\ai\wms-ai-assistant`

---

# 一、本阶段目标

将 Phase 3.6.2 的：

```text
LLM
 ↓
最多 1 个 Tool
 ↓
LLM
 ↓
结束
```

升级为：

```text
LLM
 ↓
Tool Call
 ↓
Tool Result
 ↓
LLM
 ↓
Tool Call
 ↓
Tool Result
 ↓
LLM
 ↓
最终回答
```

支持有限次数的多步骤 Tool Calling。

例如：

```text
用户：
先查询 MAT001 库存，然后查询工单 MO001 的状态。
```

允许：

```text
LLM #1
 ↓
get_inventory
 ↓
Tool Result
 ↓
LLM #2
 ↓
get_work_order
 ↓
Tool Result
 ↓
LLM #3
 ↓
最终答案
```

---

# 二、严格控制范围

本阶段禁止：

* 不接真实 WMS
* 不接真实 ERP
* 不接数据库 Tool
* 不做 Agent
* 不做 LangGraph
* 不做 MCP
* 不做自动规划器
* 不做并行 Tool
* 不做 Tool 依赖图
* 不做 Tool Retry
* 不做 Tool Cache
* 不做 Tool Timeout
* 不做对话历史
* 不修改 RAG
* 不修改 `/api/chat`
* 不修改 `/api/rag/answer`
* 不修改 Phase 3.6.1 Tool Framework 的核心契约

本阶段只解决：

**多轮、顺序、有限预算的 Tool Calling。**

---

# 三、重要设计约束

## 1. 总轮数必须有硬上限

默认：

```python
MAX_TOOL_ROUNDS = 5
```

含义：

```text
最多执行 5 个 Tool Calling round
```

不是无限循环。

例如：

```text
LLM #1 → Tool 1
LLM #2 → Tool 2
LLM #3 → Tool 3
LLM #4 → Tool 4
LLM #5 → Tool 5
LLM #6 → 最终回答
```

如果达到预算后 LLM 仍然要求调用 Tool：

**立即停止 Tool 执行。**

不要继续循环。

需要定义明确的预算耗尽行为。

---

# 四、仍然只允许单个 Tool Call

注意：

Phase 3.6.2 已经明确拒绝：

```text
多个 tool_calls
```

本阶段不要同时改变这个约束。

仍然：

```text
每个 LLM response
最多 1 个 Tool Call
```

所以：

```text
LLM #1 → get_inventory
LLM #2 → get_work_order
```

允许。

但是：

```text
LLM #1 → get_inventory + get_work_order
```

仍然拒绝。

也就是说：

```text
多轮 ≠ 并行 Tool Calling
```

本阶段只做：

**sequential tool calling**

---

# 五、不要修改 LLMResponse 的核心契约

当前：

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
```

保持兼容。

不要为了多轮调用重构 DTO。

---

# 六、ToolChatService 改造

重点修改：

```text
backend/app/services/tool_chat_service.py
```

从：

```text
LLM #1
 ↓
Tool
 ↓
LLM #2
 ↓
结束
```

改为：

```text
while rounds < MAX_TOOL_ROUNDS:

    LLM

    如果没有 tool_call:
        返回最终答案

    如果有 tool_call:
        execute Tool
        append assistant tool-call message
        append tool result message
        rounds += 1
```

但是必须注意：

**不能写一个没有预算保护的无限 while。**

必须存在明确的：

```python
MAX_TOOL_ROUNDS
```

---

# 七、消息历史必须正确保留

这是本阶段最重要的地方之一。

例如：

第一次：

```text
system
user
assistant(tool_call get_inventory)
tool(get_inventory result)
```

第二次 LLM 必须看到完整上下文：

```text
system
user
assistant(tool_call get_inventory)
tool(get_inventory result)
```

然后它返回：

```text
assistant(tool_call get_work_order)
```

下一轮必须继续：

```text
system
user
assistant(tool_call get_inventory)
tool(get_inventory result)
assistant(tool_call get_work_order)
tool(get_work_order result)
```

最后：

```text
assistant(final answer)
```

不能只把最新 ToolResult 发给 LLM。

---

# 八、原始 messages 不允许被意外污染

建议：

```python
messages = list(initial_messages)
```

在 Tool Chat Service 内部维护独立消息列表。

不要修改调用方传入的原始 list。

增加测试：

```text
initial_messages
    ↓
ToolChatService
    ↓
initial_messages 内容保持不变
```

---

# 九、每轮 Tool Call 流程

每一轮：

```text
1. LLM
2. 检查 tool_calls
3. 没有 tool_calls
      → final answer
4. 超过 MAX_TOOL_ROUNDS
      → budget exhausted
5. tool_calls 数量 > 1
      → MultipleToolCallsError
6. execute Tool
7. append assistant message
8. append tool message
9. 下一轮
```

---

# 十、Tool Schema

每一轮 LLM 都需要知道可用 Tools。

继续使用：

```python
registry.list_definitions()
```

转换：

```text
ToolDefinition
 ↓
OpenAI Tool Schema
```

不要重复实现转换。

---

# 十一、预算耗尽

定义明确异常，例如：

```python
ToolCallingBudgetExceededError
```

建议继承现有：

```text
ToolChatError
```

或者项目中最合理的 Tool Calling 异常基类。

行为：

如果：

```text
MAX_TOOL_ROUNDS = 5
```

已经执行：

```text
Tool 1
Tool 2
Tool 3
Tool 4
Tool 5
```

然后 LLM 仍然返回：

```text
Tool 6
```

不要执行 Tool 6。

不要再次请求 LLM。

返回明确的服务层错误。

API 层映射为项目现有的 5xx 错误风格。

---

# 十二、Tool 执行失败仍然允许继续

例如：

```text
LLM #1
 ↓
get_inventory
 ↓
ToolResult(success=False)
 ↓
LLM #2
```

如果 LLM #2 决定：

```text
get_work_order
```

允许继续。

也就是说：

```text
Tool failure ≠ 整个 Tool Chat 必须失败
```

ToolResult 已经提供了：

```json
{
  "success": false,
  "error": "..."
}
```

把它作为 tool message 反馈给 LLM。

---

# 十三、Mock Tool

继续使用：

```text
get_inventory
get_work_order
```

不要修改现有 Mock Tool 的业务行为。

为了测试多轮流程，可以增加测试专用 Handler，但不要增加正式 Tool。

---

# 十四、测试场景

新增或扩展：

```text
tests/test_tool_chat_service.py
tests/test_tool_chat_api.py
```

至少覆盖：

## Case 1：无需 Tool

```text
LLM #1
 ↓
final answer
```

结果：

```text
LLM = 1
Tool = 0
```

---

## Case 2：一个 Tool

```text
LLM #1
 ↓
get_inventory
 ↓
LLM #2
 ↓
final answer
```

结果：

```text
LLM = 2
Tool = 1
```

---

## Case 3：两个顺序 Tool

模拟：

```text
LLM #1 → get_inventory
LLM #2 → get_work_order
LLM #3 → final answer
```

结果：

```text
LLM = 3
Tool = 2
```

并且验证每一次 LLM 调用收到的 messages 都包含完整历史。

---

## Case 4：三个顺序 Tool

验证：

```text
Tool 1
Tool 2
Tool 3
```

全部正常执行。

---

## Case 5：预算耗尽

设置：

```python
MAX_TOOL_ROUNDS = 2
```

模拟：

```text
LLM #1 → Tool 1
LLM #2 → Tool 2
LLM #3 → Tool 3
```

验证：

```text
Tool 3
```

绝对不能执行。

---

## Case 6：多个 Tool Call

模拟：

```text
LLM #1:
get_inventory
get_work_order
```

必须仍然拒绝。

确保：

```text
Tool 执行次数 = 0
```

并保持 Phase 3.6.2 行为。

---

## Case 7：Tool 执行失败

```text
LLM #1
 ↓
Tool
 ↓
success=False
 ↓
LLM #2
 ↓
final answer
```

验证 Tool failure 可以进入下一轮。

---

## Case 8：未知 Tool

LLM 返回：

```text
get_unknown
```

验证：

```text
ToolRegistry
 ↓
ToolResult(success=False)
 ↓
LLM
```

不能直接导致整个服务崩溃。

---

# 十五、API

继续使用：

```text
POST /api/chat/with-tools
```

不要新增 endpoint。

保持：

```json
{
  "message": "..."
}
```

Response 保持：

```json
{
  "answer": "...",
  "tool_calls": [
    {
      "tool_name": "get_inventory"
    },
    {
      "tool_name": "get_work_order"
    }
  ]
}
```

这里：

`tool_calls`

表示**本次请求实际执行过的 Tool**。

顺序必须保持：

```text
get_inventory
get_work_order
```

不要返回 arguments。

不要返回内部 ToolResult。

---

# 十六、日志

扩展现有日志：

```text
llm_rounds
tool_call_count
tool_name
tool_success
elapsed_ms
```

增加：

```text
tool_round
```

例如：

```text
tool_round=1
tool_name=get_inventory

tool_round=2
tool_name=get_work_order
```

禁止记录：

```text
API Key
Authorization
DATABASE_URL
traceback 到 API
embedding
完整 Tool arguments
```

---

# 十七、配置

建议增加：

```text
TOOL_MAX_ROUNDS=5
```

进入现有配置体系。

要求：

* 默认 5
* 必须 >= 1
* 设置过大时可以合理限制上限，例如 20
* 不要把数字散落在 Service 中

最终：

```python
settings.tool.max_rounds
```

或者遵循项目现有配置结构。

---

# 十八、真实 DeepSeek Smoke Test

新增：

```text
RUN_REAL_MULTI_TOOL_CALLING_TEST=1
```

默认 SKIP。

使用现有 Mock Tools。

目标验证：

```text
DeepSeek
 ↓
get_inventory
 ↓
ToolResult
 ↓
DeepSeek
 ↓
get_work_order
 ↓
ToolResult
 ↓
DeepSeek
 ↓
final answer
```

但是要注意：

**不能假设 DeepSeek 一定会按照测试问题调用两个 Tool。**

因此真实测试如果模型行为不稳定：

可以设计明确 prompt，使其尽量要求：

```text
先查询库存，再查询工单。
```

但测试不能因为模型自然语言变化而错误判断。

验证重点是：

* 是否能解析连续 tool call
* 是否能执行第二个 Tool
* 是否能正确构造消息历史
* 是否最终得到 answer

仍然只使用 Mock Tool。

---

# 十九、性能与安全

本阶段暂时不做性能优化。

但必须确保：

```text
MAX_TOOL_ROUNDS
```

真正限制最坏情况。

例如：

```text
MAX_TOOL_ROUNDS = 5
```

最多：

```text
5 Tool execution
6 LLM calls
```

不能出现无限调用。

同时确保：

```text
Tool → LLM → Tool → LLM
```

不会因为 ToolResult 中的异常信息导致敏感信息泄露。

---

# 二十、回归

执行：

```bash
pytest tests/test_tool_chat_service.py \
       tests/test_tool_chat_api.py \
       tests/test_llm_tool_calling.py -q
```

然后：

```bash
pytest -q
```

如果已有 DB 回归机制，也执行当前项目规定的相关 DB 测试。

如果真实测试：

```bash
RUN_REAL_MULTI_TOOL_CALLING_TEST=1
```

单独执行并报告结果。

---

# 二十一、必须验证旧功能

确认：

```text
/api/chat
/api/rag/answer
```

行为不变。

确认：

```text
Tool Framework
LLM Client
RAG
Embedding
Vector Search
Reranker
```

没有出现不必要耦合。

---

# 二十二、最终汇报

完成后只汇报：

```text
Phase 3.6.3 COMPLETE

1. 多轮 Tool Calling 是否实现
2. 最大 Tool Round
3. 最大 LLM Round
4. 是否支持顺序 Tool
5. 是否支持并行 Tool
6. 消息历史是否完整保留
7. Tool failure 行为
8. Budget exceeded 行为
9. /api/chat/with-tools 行为
10. Mock 测试结果
11. Real DeepSeek 多轮测试结果
12. 全量测试结果
13. /api/chat 是否兼容
14. RAG 是否兼容
15. 安全检查
16. Git diff / 未提交文件
17. 下一阶段建议

完成后立即停止。

**不要自动进入 Phase 3.6.4。**

这一阶段完成后，你的架构就从：

**RAG + 单 Tool Calling**

变成：

**RAG + 有预算约束的多步骤 Tool Calling**。

这时再进入真实 WMS Tool，会顺很多。
```
