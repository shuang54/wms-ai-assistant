请先初始化当前 WMS AI Assistant 项目。

在开始编写代码之前，必须依次阅读并理解：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`

这三个文件是当前项目的最高级开发约束。

## 一、当前目标

现在只执行 **Phase 1：项目基础骨架初始化**。

不要提前实现：

* RAG
* Embedding
* Tool Calling
* Agent
* LangGraph
* MCP
* 复杂权限
* WMS 业务接口
* AI 自动写入业务数据
* 微服务
* Kubernetes

这些功能按照 `docs/requirements.md` 和 `docs/architecture.md` 的阶段规划，后续逐步实现。

---

## 二、初始化目标

请创建一个可以直接运行的 Python + FastAPI 项目。

技术要求：

* Python 3.12+
* FastAPI
* Uvicorn
* Pydantic
* python-dotenv
* pytest

暂时不要引入没有实际用途的大型依赖。

---

## 三、创建项目结构

按照架构文档建立基础目录：

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
│   ├── requirements.md
│   ├── architecture.md
│   ├── api.md
│   ├── database.md
│   ├── security.md
│   └── decisions/
│
├── backend/
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── health.py
│       │   └── chat.py
│       │
│       ├── services/
│       │   ├── __init__.py
│       │   └── chat_service.py
│       │
│       ├── llm/
│       │   ├── __init__.py
│       │   └── client.py
│       │
│       ├── rag/
│       │   └── __init__.py
│       │
│       ├── tools/
│       │   └── __init__.py
│       │
│       ├── integrations/
│       │   ├── __init__.py
│       │   ├── wms/
│       │   │   └── __init__.py
│       │   └── erp/
│       │       └── __init__.py
│       │
│       ├── db/
│       │   └── __init__.py
│       │
│       ├── prompts/
│       │   └── system.txt
│       │
│       ├── schemas/
│       │   └── __init__.py
│       │
│       └── utils/
│           └── __init__.py
│
├── tests/
│   ├── __init__.py
│   └── test_health.py
│
├── knowledge/
│
└── scripts/
```

如果某个目录当前没有实际代码，可以只创建 `__init__.py`，不要为了“完整”而生成无意义代码。

---

## 四、Phase 1 必须实现的功能

### 1. FastAPI 应用

创建：

```text
backend/app/main.py
```

要求：

* 创建 FastAPI application
* 注册 health router
* 注册 chat router
* 提供清晰的 API metadata
* 保持代码简单

---

### 2. Health Check

创建：

```text
backend/app/api/health.py
```

提供：

```http
GET /api/health
```

返回类似：

```json
{
  "status": "ok",
  "service": "wms-ai-assistant"
}
```

这个接口不依赖 LLM、数据库或 WMS。

---

### 3. Chat API

创建：

```text
backend/app/api/chat.py
backend/app/services/chat_service.py
```

先实现最简单的 Chat API。

接口：

```http
POST /api/chat
```

请求：

```json
{
  "message": "你好"
}
```

响应至少包含：

```json
{
  "answer": "..."
}
```

目前可以使用一个简单的 mock response。

不要在这一阶段强行接入真实 LLM。

但是代码结构必须为后续：

```text
Chat API
    ↓
Chat Service
    ↓
LLM Service
```

预留清晰边界。

---

### 4. Configuration

创建：

```text
backend/app/config.py
```

统一管理环境变量。

至少预留：

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

API Key 只能从 `.env` / 环境变量读取。

禁止硬编码。

---

### 5. LLM Client

创建：

```text
backend/app/llm/client.py
```

当前只需要定义清晰的 LLM Client 接口/基础结构。

不要马上实现复杂 Agent。

例如未来应该能够形成：

```text
ChatService
    ↓
LLMClient
    ↓
OpenAI / Qwen / DeepSeek / Ollama
```

当前如果没有真实 API Key，可以保留最小实现或 mock。

---

### 6. Prompt

创建：

```text
backend/app/prompts/system.txt
```

放置基础系统 Prompt。

不要把大量 Prompt 硬编码到 Python 文件中。

---

### 7. requirements.txt

只添加当前真正需要的依赖。

例如：

```text
fastapi
uvicorn
pydantic
python-dotenv
pytest
httpx
```

具体版本请根据当前 Python 3.12+ 环境选择合理且稳定的版本。

不要提前加入：

```text
langchain
langgraph
chromadb
qdrant
milvus
transformers
torch
```

除非当前 Phase 1 确实需要。

---

### 8. .env.example

创建：

```text
.env.example
```

示例：

```text
APP_NAME=WMS AI Assistant
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000

LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
```

不要填写真实 API Key。

---

### 9. .gitignore

至少忽略：

```text
.env
.venv/
venv/
__pycache__/
.pytest_cache/
*.pyc
.idea/
.vscode/
```

---

### 10. README.md

README 只需要介绍：

* 项目是什么
* 当前项目阶段
* 技术栈
* 如何安装
* 如何启动
* Health API
* Chat API
* 后续开发路线

不要写成几十页文档。

---

## 五、测试

创建：

```text
tests/test_health.py
```

至少测试：

```text
GET /api/health
```

确保返回：

```text
HTTP 200
status = ok
```

如果方便，再增加 Chat API 的基础测试。

---

## 六、运行验证

项目创建完成后，请实际执行：

```bash
python --version
```

然后安装依赖：

```bash
pip install -r requirements.txt
```

启动：

```bash
uvicorn backend.app.main:app --reload
```

验证：

```text
GET http://127.0.0.1:8000/api/health
```

并运行：

```bash
pytest
```

确保测试通过。

---

## 七、重要开发规则

整个初始化过程中严格遵守：

### 规则 1

不要修改：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
```

除非发现明确的架构冲突。

如果发现冲突，先告诉我，不要自行修改。

### 规则 2

不要提前开发 Phase 2 以后功能。

### 规则 3

不要直接连接 WMS 数据库。

### 规则 4

不要把 LLM API Key 写进代码。

### 规则 5

不要为了“看起来完整”创建大量空业务代码。

### 规则 6

保持模块边界清晰。

### 规则 7

优先简单、可运行、可测试。

---

## 八、完成后必须给我报告

完成后不要继续开发下一阶段。

请向我报告：

```text
1. 创建了哪些文件
2. 当前项目结构
3. 安装了哪些依赖
4. 如何启动项目
5. Health API 测试结果
6. Chat API 测试结果
7. pytest 测试结果
8. 当前还有哪些 TODO
9. 下一步建议做什么
```

**只完成 Phase 1 初始化，不要自动进入 RAG、Tool Calling 或 Agent 开发。**
