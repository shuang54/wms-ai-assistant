# WMS AI Assistant

> 企业级 WMS / ERP 自然语言助手 —— MVP 阶段（Phase 1：项目骨架）

通过自然语言查询企业 WMS / ERP 数据，并基于受控 Tool 调用企业系统 API。
本项目遵循 `AGENTS.md` 与 `docs/` 下的最高级约束，**不会绕过企业业务规则和权限体系**。

---

## 当前阶段

**Phase 1：Project Foundation（项目骨架）**

- ✅ Python + FastAPI 工程结构
- ✅ Health Check API
- ✅ Chat API（Mock LLM，预留真实 Provider 扩展点）
- ✅ 环境变量统一管理（API Key 仅从环境读取）
- ✅ pytest 基础测试

明确**未实现**（后续阶段按 `docs/requirements.md` Phase 2+ 逐步推进）：

- ❌ 真实 LLM 调用（Phase 2）
- ❌ RAG / Embedding / pgvector（Phase 3）
- ❌ Tool Calling / WMS 集成（Phase 4–5）
- ❌ Agent / LangGraph / MCP（Phase 6+）
- ❌ 复杂权限 / 写操作 / 私有化部署（Phase 6+）

---

## 技术栈

| 类别       | 技术                |
| ---------- | ------------------- |
| 语言       | Python 3.12+        |
| Web 框架   | FastAPI             |
| ASGI       | Uvicorn             |
| 数据校验   | Pydantic v2         |
| 配置       | python-dotenv       |
| HTTP 客户端 | httpx              |
| 测试       | pytest              |

---

## 目录结构

```text
wms-ai-assistant/
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
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
│       ├── api/               # HTTP 路由
│       ├── services/          # 业务逻辑
│       ├── llm/               # LLM Client 抽象
│       ├── rag/               # RAG（占位，Phase 3 实现）
│       ├── tools/             # Tool Calling（占位，Phase 4 实现）
│       ├── integrations/      # WMS / ERP 集成（占位）
│       ├── db/                # 数据库（占位，Phase 2 实现）
│       ├── schemas/           # Pydantic 模型（按需扩展）
│       ├── prompts/           # Prompt 模板
│       └── utils/
│
├── tests/
│
├── knowledge/                 # 企业知识库原文（Phase 3 引入）
│
└── scripts/                   # 辅助脚本（按需添加）
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
python -m pip install -r requirements.txt
```

---

## 启动

```bash
# 默认监听 0.0.0.0:8000
python -m uvicorn backend.app.main:app --reload
```

启动后可访问：

- 接口根地址：<http://127.0.0.1:8000>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>
- ReDoc 文档：<http://127.0.0.1:8000/redoc>

---

## API 速览

### Health Check

```http
GET /api/health
```

```json
{
  "status": "ok",
  "service": "wms-ai-assistant"
}
```

### Chat（Mock）

```http
POST /api/chat
Content-Type: application/json

{
  "message": "你好"
}
```

```json
{
  "answer": "[mock] 已收到消息：你好（当前未接入真实 LLM，Phase 2 实现）"
}
```

---

## 测试

```bash
python -m pytest
```

---

## 环境变量

复制 `.env.example` 为 `.env` 并按需填写，**`.env` 不会进入版本控制**。

| 变量           | 用途                            | Phase 1 是否使用 |
| -------------- | ------------------------------- | ---------------- |
| `APP_NAME`     | 服务名                          | ✅               |
| `APP_ENV`      | 环境标识                        | ✅               |
| `APP_HOST`     | 监听地址                        | ✅               |
| `APP_PORT`     | 监听端口                        | ✅               |
| `LLM_PROVIDER` | LLM Provider 标识               | 占位             |
| `LLM_MODEL`    | 模型名                          | 占位             |
| `LLM_API_KEY`  | LLM API Key（严禁提交）         | 占位             |
| `LLM_BASE_URL` | LLM API 基础地址                | 占位             |

---

## 后续开发路线

严格按 `docs/requirements.md` 的 Phase 推进：

```text
Phase 1 项目骨架          ← 当前
Phase 2 LLM API + Chat
Phase 3 RAG + Embedding + pgvector
Phase 4 Tool Calling
Phase 5 WMS API 集成
Phase 6 Agent / LangGraph / 权限 / 写操作 / 私有化部署
```

**任何未来能力都必须建立在真实需求基础上**，不为技术炫技提前复杂化。
