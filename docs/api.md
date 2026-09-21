# WMS AI Assistant — API 设计

> 当前文档对应 MVP Phase 1 实现。后续阶段（Phase 2+）按 `docs/requirements.md` 演进。

---

## 1. 文档信息

| 项目     | 内容                |
| -------- | ------------------- |
| 项目名称 | WMS AI Assistant    |
| 当前阶段 | MVP — Phase 1       |
| Base URL | `/api`              |
| 数据格式 | JSON                |
| 鉴权     | Phase 1 无；Phase 6+ 引入 JWT/RBAC |

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
| service  | string | 服务标识           |

---

### 2.2 POST /api/chat

极简对话接口。Phase 1 由 Mock LLM 返回，**未接入真实 LLM**。

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
  "answer": "[mock] 已收到消息：你好"
}
```

| 字段    | 类型   | 说明                                    |
| ------- | ------ | --------------------------------------- |
| answer  | string | AI 回答（当前为 Mock）                  |

---

## 3. 统一错误格式（Phase 1 约定）

```json
{
  "detail": "message"
}
```

FastAPI 默认 `HTTPException` 走 `{"detail": ...}` 形式。
后续阶段会扩展为：

```json
{
  "code": "ERROR_CODE",
  "message": "可理解的错误描述",
  "details": {}
}
```

---

## 4. 后续计划中的接口（**当前未实现**）

按 `docs/requirements.md` Phase 2+ 演进：

| 方法 | 路径                          | 阶段    | 说明                       |
| ---- | ----------------------------- | ------- | -------------------------- |
| GET  | `/api/conversations`          | Phase 2 | 会话列表                   |
| GET  | `/api/conversations/{id}`     | Phase 2 | 会话详情                   |
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
- **不暴露内部细节**：错误信息对用户友好，敏感信息（API Key、SQL、堆栈）禁止出现在响应中。
