# WMS AI Assistant

> 企业级 WMS / ERP 自然语言助手 —— MVP 阶段（Phase 3.5.6：Chat + RAG）

通过自然语言查询企业 WMS / ERP 数据，并基于受控 Tool 调用企业系统 API。
本项目遵循 `AGENTS.md` 与 `docs/` 下的最高级约束，**不会绕过企业业务规则和权限体系**。

---

## 当前阶段

**Phase 3.5.7：RAG Retrieval Evaluation**

- ✅ Python + FastAPI 工程结构（Phase 1）
- ✅ Health Check API（Phase 1）
- ✅ Chat API + ChatService + LLM Client 抽象（Phase 1）
- ✅ **真实 LLM 调用（OpenAI 兼容协议）**（Phase 2）
- ✅ **异常处理 + 超时 + 结构化日志**（Phase 2）
- ✅ **LLM Client / ChatService / API 全链路测试**（Phase 2）
- ✅ `.env` 环境变量驱动，未配置 Key 时自动回退 Mock
- ✅ **PostgreSQL + pgvector 知识库基础设施**（Phase 3.1–3.2）
- ✅ **文档解析 + Chunking + Embedding 入库流水线**（Phase 3.3–3.5.2）
- ✅ **向量检索（Vector Search）**（Phase 3.5.3）
- ✅ **最小 RAG（RagService：检索 → Context → LLM）**（Phase 3.5.4）
- ✅ **RAG API（POST /api/rag/answer）**（Phase 3.5.5）
- ✅ **Chat + RAG（/api/chat 经知识库回答，含来源元数据）**（Phase 3.5.6）
- ✅ **RAG 检索质量评估（Baseline：Top-K Keyword Hit Rate）**（Phase 3.5.7）

明确**未实现**（后续阶段按 `docs/requirements.md` 逐步推进）：

- ❌ Tool Calling / WMS 集成（Phase 4–5）
- ❌ Agent / LangGraph / MCP（Phase 6+）
- ❌ 复杂权限 / 写操作 / 私有化部署（Phase 6+）
- ❌ 多轮会话上下文 / Intent 分类 / 闲聊兜底（Phase 2.5+）
- ❌ Streaming（Phase 6+）
- ❌ Reranker / Hybrid Search / Query Rewrite（评估 → 优化阶段）

---

## 技术栈

| 类别       | 技术                    |
| ---------- | ----------------------- |
| 语言       | Python 3.12+            |
| Web 框架   | FastAPI                 |
| ASGI       | Uvicorn                 |
| 数据校验   | Pydantic v2             |
| 配置       | python-dotenv           |
| LLM 客户端 | httpx（OpenAI 兼容）    |
| 测试       | pytest + pytest-asyncio |

> Phase 2 不引入 openai SDK / LangChain / LangGraph，统一通过 httpx 调 OpenAI Chat Completions 协议。

---

## 目录结构

```text
wms-ai-assistant/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── pytest.ini
│
├── docs/                      # 设计文档
│   ├── requirements.md
│   ├── architecture.md
│   ├── api.md
│   ├── database.md
│   ├── security.md
│   └── decisions/
│
├── backend/
│   └── app/
│       ├── main.py            # FastAPI 入口
│       ├── config.py          # 环境变量配置
│       │
│       ├── api/               # HTTP 路由
│       │   ├── health.py
│       │   └── chat.py
│       │
│       ├── services/
│       │   └── chat_service.py
│       │
│       ├── llm/
│       │   └── client.py      # LLMClient 抽象 + Mock + OpenAICompatible
│       │
│       ├── rag/               # 占位（Phase 3）
│       ├── tools/             # 占位（Phase 4）
│       ├── integrations/      # 占位（Phase 5）
│       ├── db/                # 占位（Phase 2.5+）
│       ├── schemas/
│       ├── utils/
│       └── prompts/
│           └── system.txt     # 系统 Prompt
│
├── tests/
│   ├── test_health.py
│   ├── test_chat.py
│   ├── test_chat_service.py
│   └── test_llm_client.py
│
├── knowledge/                 # 企业知识库原文（Phase 3 引入）
│
└── scripts/                   # 辅助脚本
```

---

## 安装

要求 Python 3.12+（已在 3.13.2 上验证）。

```bash
# 创建并激活虚拟环境（可选但推荐）
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux / macOS
# source .venv/bin/activate

# 安装依赖
python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
```

---

## 启动

```bash
python -m uvicorn backend.app.main:app --reload
```

启动后可访问：

- 接口根地址：<http://127.0.0.1:8000>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>
- ReDoc 文档：<http://127.0.0.1:8000/redoc>

---

## LLM 配置

复制 `.env.example` 为 `.env` 并填写 LLM 相关变量。

| 变量                 | 必填 | 说明                                                  |
| -------------------- | ---- | ----------------------------------------------------- |
| `LLM_PROVIDER`       | 推荐 | 人类可读标识（`openai` / `deepseek` / `qwen` / `ollama` / 自定义），仅用于日志 |
| `LLM_MODEL`          | ✅   | 模型名（请求体 `model` 字段）                          |
| `LLM_API_KEY`        | ✅   | API Key；**严禁提交到 Git**；留空自动回退 Mock          |
| `LLM_BASE_URL`       | ✅   | API 基础地址（自动追加 `/chat/completions`）           |
| `LLM_TIMEOUT_CONNECT` | 否   | 连接超时（秒），默认 `10`                              |
| `LLM_TIMEOUT_READ`    | 否   | 读超时（秒），默认 `60`                                |
| `LLM_TIMEOUT_WRITE`   | 否   | 写超时（秒），默认 `10`                                |
| `LLM_TIMEOUT_POOL`    | 否   | 连接池超时（秒），默认 `10`                            |

### 常见 OpenAI 兼容服务示例

```ini
# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.openai.com/v1

# DeepSeek
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1

# Qwen (DashScope OpenAI 兼容模式)
LLM_PROVIDER=qwen
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# Ollama (本地)
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=ollama                       # 占位，Ollama 通常不校验
LLM_BASE_URL=http://localhost:11434/v1

# 不填任何 LLM_* → 自动回退 MockLLMClient
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
```

---

## API 速览

### Health Check

```http
GET /api/health
```

```json
{ "status": "ok", "service": "wms-ai-assistant" }
```

### Chat

```http
POST /api/chat
Content-Type: application/json

{
  "message": "请用一句话介绍 WMS。"
}
```

**成功响应 200**：

```json
{
  "answer": "WMS（仓库管理系统）用于管理仓库的入库、出库、库存和盘点等业务。"
}
```

**异常响应**：

| HTTP | 含义                        | 触发条件                              |
| ---- | --------------------------- | ------------------------------------- |
| 422  | 请求参数校验失败            | `message` 为空 / 缺字段 / 类型错误     |
| 502  | LLM 上游错误                | 连接失败 / 超时 / HTTP 非 2xx / 响应解析失败 |
| 503  | LLM 配置错误                | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 缺失 |
| 500  | 未预期的 LLM 错误（兜底）   | 其它异常                              |

---

## 测试

```bash
python -m pytest
```

测试组成：

- `tests/test_health.py` — Health API
- `tests/test_chat.py` — Chat API（含 503 / 502 异常路径）
- `tests/test_chat_service.py` — ChatService 单元测试（FakeLLMClient）
- `tests/test_llm_client.py` — OpenAICompatibleClient 单元测试（httpx.MockTransport）

---

## LLM 调用架构

```text
HTTP POST /api/chat
        ↓
  ChatService（构造 messages：system + user）
        ↓
  LLMClient（Protocol 抽象）
        ├─→ MockLLMClient          （无 Key 回退）
        └─→ OpenAICompatibleClient  （httpx → Chat Completions）
                    ↓
            OpenAI / DeepSeek / Qwen / Ollama / 自部署
```

- **解耦**：ChatService 不直接依赖任何 LLM SDK
- **可扩展**：未来新增 Anthropic / Google 等厂商时，只需新增一个 Client 类并在 `create_llm_client` 中按 provider 分发
- **可观测**：所有 LLM 调用记录 `provider` / `model` / `elapsed_ms` / `error_type`；**不记录** Authorization / API Key

---

## 后续开发路线

```text
Phase 1 项目骨架          ← 已完成
Phase 2 LLM API + Chat   ← 当前
Phase 3 RAG + Embedding + pgvector
Phase 4 Tool Calling
Phase 5 WMS API 集成
Phase 6 Agent / LangGraph / 权限 / 写操作 / 私有化部署
```

**任何未来能力都必须建立在真实需求基础上**，不为技术炫技提前复杂化。