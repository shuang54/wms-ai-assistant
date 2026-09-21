现在进入项目的 **Phase 2：LLM API 接入**。

在开始修改代码之前，请重新阅读：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`

确认当前项目已经完成 Phase 1 初始化。

---

# 一、当前阶段目标

本阶段只实现：

```text
用户
 ↓
POST /api/chat
 ↓
ChatService
 ↓
LLMClient
 ↓
LLM API
 ↓
ChatService
 ↓
API Response
 ↓
用户
```

目标是让系统能够真正调用一个外部 LLM API，并返回模型回答。

---

# 二、严格限制

本阶段不要实现：

* RAG
* Embedding
* pgvector
* Tool Calling
* Agent
* LangGraph
* MCP
* WMS API
* ERP API
* 数据库业务查询
* 权限系统
* AI 自动执行企业操作

不要提前进入下一阶段。

---

# 三、先检查 Phase 1

开始开发前先检查：

```text
backend/app/main.py
backend/app/config.py
backend/app/api/chat.py
backend/app/services/chat_service.py
backend/app/llm/client.py
backend/app/prompts/system.txt
tests/
requirements.txt
.env.example
```

确认项目可以正常启动。

如果 Phase 1 存在问题：

1. 先修复阻塞当前 Phase 2 的问题
2. 不进行无关重构
3. 告诉我修复了什么

---

# 四、LLM Client

完善：

```text
backend/app/llm/client.py
```

LLM Client 是整个项目未来切换模型的抽象层。

目标结构：

```text
ChatService
      ↓
   LLMClient
      ↓
 ┌────┴───────────────┐
 │                    │
OpenAI-compatible   Ollama
API                  (future)
```

不要让 `ChatService` 直接调用具体 SDK。

例如：

```python
class LLMClient:
    async def chat(
        self,
        messages: list[dict[str, str]]
    ) -> str:
        ...
```

具体实现可以根据当前项目依赖选择合适方式。

---

# 五、模型兼容性

优先设计成支持 OpenAI-compatible API。

通过配置：

```text
LLM_PROVIDER
LLM_MODEL
LLM_API_KEY
LLM_BASE_URL
```

允许未来切换不同模型服务。

例如：

```text
OpenAI
DeepSeek
Qwen
其他 OpenAI-compatible API
```

不要把任何具体厂商写死在业务层。

---

# 六、Configuration

完善：

```text
backend/app/config.py
```

使用环境变量读取：

```text
APP_NAME
APP_ENV
APP_HOST
APP_PORT

LLM_PROVIDER
LLM_MODEL
LLM_API_KEY
LLM_BASE_URL
```

要求：

* `.env` 不进入 Git
* API Key 不允许硬编码
* 配置集中管理
* 业务代码不要直接 `os.getenv()`

例如业务代码应该：

```python
settings.llm_api_key
```

而不是到处：

```python
os.getenv("LLM_API_KEY")
```

---

# 七、LLM 请求结构

内部统一使用类似：

```python
messages = [
    {
        "role": "system",
        "content": system_prompt
    },
    {
        "role": "user",
        "content": user_message
    }
]
```

然后：

```text
ChatService
    ↓
messages
    ↓
LLMClient.chat()
```

这样以后加入：

```text
Conversation History
RAG Context
Tool Result
```

时不需要修改整体架构。

---

# 八、System Prompt

读取：

```text
backend/app/prompts/system.txt
```

不要把 System Prompt 硬编码到：

```text
chat_service.py
```

例如基础 Prompt 可以要求模型：

```text
你是 WMS AI Assistant。
你的任务是帮助用户理解和使用企业 WMS/ERP 系统。

当前阶段你只能进行普通自然语言对话。
对于没有提供的数据，不要假装知道。
不要编造企业业务数据。
```

具体内容请根据 `requirements.md` 和 `architecture.md` 保持一致。

---

# 九、ChatService

完善：

```text
backend/app/services/chat_service.py
```

职责：

```text
接收用户问题
      ↓
加载 System Prompt
      ↓
构造 messages
      ↓
调用 LLMClient
      ↓
返回模型结果
```

不要在 ChatService 中：

* 写 HTTP 请求细节
* 写数据库 SQL
* 写 WMS 业务逻辑
* 写 Tool Calling
* 写 RAG
* 写 Agent

---

# 十、Chat API

保持：

```http
POST /api/chat
```

请求：

```json
{
  "message": "你好，请介绍一下你自己"
}
```

响应：

```json
{
  "answer": "..."
}
```

如果当前项目已经定义了 Schema，请继续使用 Pydantic Schema。

不要让 API 层直接处理 LLM SDK。

正确：

```text
Chat API
   ↓
ChatService
   ↓
LLMClient
   ↓
LLM
```

错误：

```text
Chat API
   ↓
LLM SDK
```

---

# 十一、异常处理

至少处理：

```text
API Key 未配置
LLM Base URL 错误
LLM 请求失败
LLM 超时
LLM 返回异常
```

不要把底层异常堆栈直接返回给用户。

例如内部：

```text
ConnectionError
TimeoutError
HTTP 401
HTTP 429
HTTP 500
```

应该转换成合理的应用层错误。

同时保留日志方便调试。

---

# 十二、超时

LLM 请求必须设置合理 timeout。

不要让一次 LLM 请求无限等待。

建议：

```text
connect timeout
read timeout
overall request timeout
```

具体实现根据使用的 HTTP Client 决定。

---

# 十三、日志

本阶段至少记录：

```text
request_id
LLM provider
LLM model
请求耗时
成功 / 失败
错误类型
```

不要记录：

```text
API Key
Authorization Header
完整敏感信息
```

用户消息是否完整记录，请根据安全原则处理。

---

# 十四、测试

至少增加以下测试：

## 1. ChatService Mock LLM

不要让单元测试依赖真实 LLM。

例如：

```text
ChatService
    ↓
Mock LLMClient
```

验证：

```text
用户问题
 ↓
ChatService
 ↓
LLMClient
 ↓
正确返回 answer
```

---

## 2. Chat API

测试：

```http
POST /api/chat
```

验证：

```text
HTTP 200
answer 存在
```

---

## 3. 空消息

例如：

```json
{
  "message": ""
}
```

应该进行参数验证。

---

## 4. LLM 异常

模拟：

```text
LLMClient 抛出异常
```

验证 API 能够返回合理错误，而不是直接暴露 Python traceback。

---

# 十五、真实 API 测试

如果当前环境已经存在 `.env`：

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

可以进行一次真实 LLM 调用。

例如：

```json
{
  "message": "请用一句话介绍 WMS。"
}
```

确认能够得到真实模型回答。

如果没有 API Key：

不要要求我提供 Key。

保持 Mock 测试通过即可，并告诉我需要配置哪些环境变量。

---

# 十六、不要修改项目架构

严格保持：

```text
API
 ↓
Service
 ↓
LLM Client
 ↓
External LLM
```

不要因为接入 SDK 而破坏当前架构。

如果第三方 SDK 与当前架构冲突：

优先保持：

```text
Architecture
>
SDK Convenience
```

---

# 十七、依赖控制

只增加当前真正需要的依赖。

如果使用 OpenAI-compatible HTTP API，请选择合理的官方/稳定客户端或 HTTP Client。

不要因为这一阶段就加入：

```text
LangChain
LangGraph
Vector DB
Agent Framework
```

---

# 十八、完成后运行

执行：

```bash
pytest
```

确保全部测试通过。

然后启动：

```bash
uvicorn backend.app.main:app --reload
```

测试：

```http
GET /api/health
```

以及：

```http
POST /api/chat
```

---

# 十九、最终验收

必须达到：

```text
项目可以启动
       ↓
Health API 正常
       ↓
Chat API 正常
       ↓
ChatService 正常
       ↓
LLMClient 正常
       ↓
真实 LLM API 可以调用
```

如果没有真实 API Key：

```text
项目可以启动
       ↓
Health API 正常
       ↓
Chat API 正常
       ↓
Mock LLM 测试通过
```

也算完成。

---

# 二十、完成后停止

不要自动进入 Phase 3。

完成后只向我报告：

```text
1. 修改了哪些文件
2. 新增了哪些文件
3. 新增了哪些依赖
4. 当前 LLM 调用架构
5. 环境变量配置方式
6. pytest 测试结果
7. 是否成功调用真实 LLM
8. 如果失败，具体原因
9. 当前项目结构
10. 下一阶段是什么
```

**本阶段完成后立即停止，等待下一步指令。**
