# WMS AI Assistant — API 设计

> 当前文档对应 MVP Phase 2 实现。后续阶段（Phase 3+）按 `docs/requirements.md` 演进。

---

## 1. 文档信息

| 项目     | 内容                                              |
| -------- | ------------------------------------------------- |
| 项目名称 | WMS AI Assistant                                  |
| 当前阶段 | MVP — Phase 2（LLM API 接入）                     |
| Base URL | `/api`                                            |
| 数据格式 | JSON                                              |
| 鉴权     | Phase 2 无；Phase 6+ 引入 JWT/RBAC                |
| LLM 协议 | OpenAI Chat Completions（兼容 OpenAI / DeepSeek / Qwen / Ollama / 自部署） |

---

## 2. 当前已实现的接口

### 2.1 GET /api/health

服务健康检查，**不依赖** LLM、数据库或外部系统。

#### 请求

无请求体，无 Query 参数。

#### 响应 200

```json
{
  "status": "ok",
  "service": "wms-ai-assistant"
}
```

| 字段     | 类型   | 说明               |
| -------- | ------ | ------------------ |
| status   | string | 固定值 `"ok"`       |
| service  | string | 机器标识符         |

---

### 2.2 POST /api/chat

对话接口。Phase 2 根据 `.env` 配置调用真实 LLM，或在 `LLM_API_KEY` 缺失时回退 Mock。

#### 请求

```json
{
  "message": "你好"
}
```

| 字段     | 类型   | 必填 | 说明                              |
| -------- | ------ | ---- | --------------------------------- |
| message  | string | ✅   | 用户自然语言消息（不可为空字符串） |

#### 响应 200

```json
{
  "answer": "你好，我是 WMS AI Assistant。"
}
```

| 字段    | 类型   | 说明                                    |
| ------- | ------ | --------------------------------------- |
| answer  | string | AI 回答（来自真实 LLM 或 Mock）         |

---

## 3. 错误响应

### 3.1 统一错误格式

```json
{
  "detail": "可理解的错误描述"
}
```

FastAPI 默认 `HTTPException` 走 `{"detail": ...}` 形式。
后续阶段会扩展为 `{code, message, details}` 结构。

### 3.2 HTTP 状态码映射（Phase 2）

| HTTP | 触发条件                                                         | detail 示例                          |
| ---- | ---------------------------------------------------------------- | ------------------------------------ |
| 200  | 正常                                                              | —                                    |
| 422  | Pydantic 校验失败：`message` 为空 / 缺字段 / 类型错误            | FastAPI 默认错误列表                  |
| 502  | LLM 上游错误（连接失败 / 超时 / HTTP 非 2xx / 响应解析失败）      | `"LLM 请求失败: 无法连接 ..."`       |
| 503  | LLM 配置错误（`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 缺失）  | `"LLM 配置错误: LLM_API_KEY 未配置"` |
| 500  | 兜底：未预期的 LLM 异常                                           | `"LLM 调用异常: ..."`                |

> **不暴露**：响应中禁止出现 Authorization header、API Key、Python traceback、SQL 语句。

### 3.3 异常分层（`backend/app/llm/client.py`）

```
LLMError（基类）
    ├── LLMConfigError        配置缺失
    ├── LLMRequestError       网络 / 超时 / 非 2xx
    └── LLMResponseError      响应解析失败 / 结构异常
```

---

## 4. 后续计划中的接口（**当前未实现**）

按 `docs/requirements.md` Phase 3+ 演进：

| 方法 | 路径                          | 阶段    | 说明                       |
| ---- | ----------------------------- | ------- | -------------------------- |
| GET  | `/api/conversations`          | Phase 2.5 | 会话列表                   |
| GET  | `/api/conversations/{id}`     | Phase 2.5 | 会话详情                   |
| POST | `/api/knowledge/documents`    | Phase 3 | 知识库文档入库             |
| POST | `/api/knowledge/search`       | Phase 3 | 知识库检索（内部接口）     |
| GET  | `/api/tools`                  | Phase 4 | Tool 注册清单              |
| POST | `/api/tools/{tool_name}`      | Phase 4 | Tool 调用                  |
| POST | `/api/auth/login`             | Phase 6 | 用户登录                   |
| GET  | `/api/audit/logs`             | Phase 6 | 审计日志                   |

---

## 5. 设计原则

- **RESTful**：资源导向，HTTP 语义清晰。
- **OpenAPI 自动生成**：所有接口必须能被 FastAPI 自动文档化。
- **响应统一**：错误响应遵循一致结构。
- **可演进**：接口路径与字段命名考虑后续扩展，避免过早锁定。
- **不暴露内部细节**：错误信息对用户友好，敏感信息（API Key、SQL、堆栈、Authorization header）禁止出现在响应中。
- **LLM 抽象**：ChatService 不直接调用任何 LLM SDK；通过 `LLMClient` Protocol 解耦。