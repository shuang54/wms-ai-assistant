现在进入 **Phase 2 Real LLM Smoke Test**。

项目已经完成 Phase 1 和 Phase 2，并且已有：

* FastAPI
* `/api/chat`
* ChatService
* OpenAICompatibleClient
* LLM 配置
* 单元测试
* Mock LLM 测试

现在我准备使用 **DeepSeek API** 进行一次真实 LLM 调用验证。

请严格按照以下步骤执行，不要进入 Phase 3。

## 一、先检查现有实现

先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

然后检查：

* `backend/app/config.py`
* `backend/app/llm/client.py`
* `backend/app/services/chat_service.py`
* `backend/app/api/chat.py`
* `.env.example`
* `.gitignore`

确认当前代码是否支持 OpenAI-compatible API。

## 二、配置 DeepSeek

DeepSeek 配置使用：

```env
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=<用户本地配置，不要输出实际值>
LLM_BASE_URL=https://api.deepseek.com
LLM_TIMEOUT=60
```

注意：

1. 不要创建或修改真实 API Key。
2. 不要在终端输出 API Key。
3. 不要把 API Key 写入代码。
4. 确认 `.env` 已经被 `.gitignore` 忽略。
5. 不要把 `.env` 提交到 Git。
6. 如果项目当前配置字段名称与上述不同，以现有 `config.py` 的设计为准，只做必要适配。

## 三、执行真实调用

启动 FastAPI：

```bash
uvicorn backend.app.main:app --reload
```

然后调用：

```http
POST /api/chat
```

请求：

```json
{
  "message": "你好，请简单介绍一下你自己，并说明你可以如何帮助我查询WMS库存数据。"
}
```

这里暂时只验证 LLM 是否正常返回。

注意：

此阶段**不要真的连接 WMS**。

也不要实现：

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

## 四、验证完整调用链

重点确认：

```text
POST /api/chat
       ↓
Chat API
       ↓
ChatService
       ↓
LLMClient
       ↓
DeepSeek API
       ↓
DeepSeek 返回
       ↓
ChatService
       ↓
API Response
```

确认不是 Mock 返回。

## 五、增加一个真实 LLM Smoke Test

如果当前测试结构适合，请增加一个独立的真实调用测试，但：

* 默认不要让 pytest 自动调用真实 API
* 不要把 API Key 写入测试代码
* 可以通过环境变量显式开启

例如：

```bash
RUN_REAL_LLM_TEST=true pytest
```

没有配置真实 API 或没有开启开关时，应自动跳过。

## 六、检查异常情况

至少确认以下情况仍然能够正确处理：

1. API Key 缺失
2. API Key 错误
3. Base URL 错误
4. 请求超时
5. DeepSeek API 返回错误
6. 空消息
7. 正常消息

不要为了测试异常情况消耗大量 API 额度。

## 七、完成后给我报告

请按照下面格式输出：

### Real LLM Smoke Test

* Provider:
* Model:
* Base URL:
* API 配置是否读取成功:
* 是否真实调用 DeepSeek:
* HTTP Status:
* 是否成功返回:
* 返回内容是否为空:
* Mock 测试结果:
* Real LLM Test 结果:
* `pytest` 结果:

### 修改文件

列出实际修改的文件及修改原因。

### 当前项目状态

明确告诉我：

* Phase 2 是否完成
* Real LLM 是否验证成功
* 是否可以进入 Phase 3

如果真实调用失败，请**先定位原因并修复**，不要直接进入 Phase 3。

本阶段完成后停止，不要自行扩展功能。
