# WMS AI Assistant — 安全设计

> 当前文档对应 MVP 演进路径。Phase 1 安全约束最简，重点是**不引入安全风险**。

---

## 1. 文档信息

| 项目     | 内容                          |
| -------- | ----------------------------- |
| 当前阶段 | MVP — Phase 1                 |
| 适用范围 | API、配置、密钥、Tool、权限    |

---

## 2. 核心安全原则（来自 `AGENTS.md`）

1. **API Key 不入代码**：仅从环境变量读取。
2. **`.env` 不入 Git**：通过 `.gitignore` 强制隔离。
3. **LLM 不直接访问业务数据库**：所有数据访问必须经 Tool。
4. **写操作必须二次确认**：Phase 1 无写操作；未来引入时严格按 `AGENTS.md` §14 流程执行。
5. **敏感数据需要权限控制 + 日志 + 必要时脱敏**：Phase 6+ 引入 RBAC 后落地。
6. **不向用户暴露内部错误**：API 响应中不出现 API Key、SQL、堆栈、Token 等。

---

## 3. Phase 1 安全清单

### 3.1 配置与密钥

| 项                              | Phase 1 状态                       |
| ------------------------------- | ---------------------------------- |
| `.env` 已加入 `.gitignore`      | ✅                                 |
| `.env.example` 仅列变量名与占位 | ✅                                 |
| `LLM_API_KEY` 读取走环境变量    | ✅（当前无真实 Key，仅占位）        |
| 进程内不打印 API Key            | ✅                                 |
| 不写入日志的 API Key            | ✅（Phase 1 暂无日志框架）          |

### 3.2 输入校验

- 所有 API 入参由 Pydantic 校验（FastAPI 原生）。
- `POST /api/chat` 的 `message` 字段不允许空字符串。
- 异常路径使用统一错误响应，不暴露内部信息。

### 3.3 错误处理

- **不吞异常**：禁止 `try: ... except Exception: pass`。
- 抛出 `HTTPException` 或领域异常，由 FastAPI 统一转为响应。
- 后续阶段 Tool 异常需返回结构化错误码（`AGENTS.md` §16）。

### 3.4 依赖安全

- 仅引入 Phase 1 必需的依赖（fastapi、uvicorn、pydantic、python-dotenv、httpx、pytest）。
- 不引入 LangChain / LangGraph / 任何向量库（`AGENTS.md` §20）。

---

## 4. Phase 1 明确**不**实现的安全能力

| 能力                  | 引入阶段        | 说明                              |
| --------------------- | --------------- | --------------------------------- |
| 用户登录 / 鉴权       | Phase 6         | JWT + bcrypt                      |
| RBAC 权限模型         | Phase 6         | 用户 / 角色 / 工具权限            |
| 审计日志              | Phase 6         | 谁、什么时候、调用了什么 Tool     |
| 敏感操作二次确认      | Phase 6+        | 写操作前用户确认                  |
| HTTPS / TLS           | 部署阶段        | 由反向代理（Nginx）终结           |
| 限流                  | Phase 6+        | 防止滥用                          |
| 安全响应头（CORS 等） | Phase 6+        | 前后端分离后完善                  |

---

## 5. 未来写操作的安全流程（规划）

按 `docs/architecture.md` §25.2：

```text
用户发起写操作请求
    ↓
AI 生成操作计划
    ↓
向用户展示操作内容（确认界面）
    ↓
用户确认
    ↓
权限校验
    ↓
业务校验（WMS API）
    ↓
执行
    ↓
审计日志
```

LLM 仅负责"提议调用哪个能力"，**不负责决定权限与业务校验结果**。

---

## 6. 当前 TODO

- Phase 1：
  - ✅ `.gitignore` 隔离 `.env`
  - ✅ `.env.example` 不含真实 Key
  - ✅ Pydantic 入参校验
  - ✅ 不在代码中硬编码任何凭据
- Phase 2+：
  - 引入结构化日志 + request_id
  - 引入 JWT 鉴权
  - 引入 Tool 调用审计表
  - 完善 CORS、HTTPS、限流
