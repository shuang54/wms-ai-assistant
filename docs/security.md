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

---

## 7. Workflow Read API 认证（Phase 6 Step 7.2；已实现）

四个只读端点（`GET /api/workflow-runs/{run_id}`、`…/nodes`、`…/timeline`、
`GET /api/workflow-approvals/{approval_id}`）采用**最小单凭据绑定单项目**模型
（背景与审计证据：`docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md`）：

| 项 | 配置 / 行为 |
| --- | --- |
| 认证头 | `X-API-Key`（`secrets.compare_digest` 恒定时间比较） |
| 凭据配置 | `WORKFLOW_READ_API_KEY`（环境变量；**无任何默认值**） |
| 授权作用域 | `WORKFLOW_READ_API_PROJECT_ID`（该凭据**唯一**授权的项目） |
| 未配置 | 任一为空/空白 ⇒ 四个端点一律 **503 fail closed**（绝不匿名放行；**不**回退默认项目） |
| 缺 / 错凭据 | **401**（固定文案；不泄露目标资源是否存在；日志与响应不记录原始凭据） |
| 客户端 project_id | **被忽略**——作用域只来自服务端配置（不接受 Query / 普通 Header / Body，`workflow_read.py` 已移除该查询参数） |
| 跨项目资源 | 404（与"不存在"同构——不泄露存在性） |

明确边界（**不**宣称已达成）：
- API Key ≠ 用户身份系统（无用户 / 角色 / RBAC；多项目映射与凭据轮换属后续阶段）；
- HTTPS = **NOT VERIFIED**、网络边界 = **NOT VERIFIED**（仓库内无网关 / 反向代理
  / TLS 配置）⇒ **PRODUCTION_DEPLOYMENT 仍为 BLOCKED**，直到部署层完成独立验证
  （外部客户端不可绕过网关 / 只有可信网关可传递身份 / 网关覆盖而非盲信客户端身份头）。

配置示例（写入 `.env`，值**不得**提交到 Git；变量名见 `.env.example` 占位）：

```text
WORKFLOW_READ_API_KEY=<服务端生成的强随机值>
WORKFLOW_READ_API_PROJECT_ID=vietnam-wms
```
