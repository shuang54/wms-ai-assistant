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

---

## 8. Conversation API 认证 / 所有权 / 项目作用域（Phase 7 Recovery M2.2；已实现）

五个端点（`POST /api/conversations`、`GET /api/conversations/{id}`、
`GET …/{id}/messages`、`POST …/{id}/messages`、`POST …/{id}/archive`）从
**无认证**改为 **M1 用户会话 + 会话所有权 + 单部署项目作用域**：

| 项 | 行为 |
| --- | --- |
| 身份来源 | 服务端会话（`wms_ai_session` HttpOnly Cookie → `ai_ops.user_session`）；**不**接受 API Key / Query / Body 身份 |
| 未 / 失效会话 | **401**（固定文案；不区分未登录 / 无效 / 过期 / 已撤销；不泄露账号存在性） |
| CSRF | 写操作（创建 / 追加 / 归档）要求双提交头 `X-CSRF-Token`；缺失 / 不匹配 ⇒ **403**，副作用为零 |
| 项目作用域 | 创建时 `project_id` 必须精确等于**服务端配置项目**（`PROJECT_ID`）；其他项目**即使已注册**也不授权（已注册 ≠ 已授权）⇒ **422**（同一固定文案，不泄露项目存在性） |
| 作用域配置缺失 | 配置不可解析 / 配置项目在注册表不可解析 ⇒ **503 fail closed**（**不**回退默认项目） |
| 归属性校验 | 读取 / 追加 / 归档先校验 `ai_ops.resource_owner`（归属行与 Conversation **同事务**创建） |
| 越权 / 不存在 | **404 同构**（同 status + 同 body）——不泄露资源是否存在；**不**自动认领无归属的历史会话 |
| 归属服务故障 | **503 fail closed**（绝不放行、绝不降级为成功） |
| 落库作用域 | `conversation.project_id` 与 `resource_owner.project_id` 均为服务端**已验证**值 |
| 日志 | 只记录固定事件与 `error_type`；不记录凭据 / Cookie / Token / 消息正文 / SQL / 连接串 |

明确边界（**不**宣称已达成）：

- **当前是"单部署项目作用域"，不是用户级 RBAC**：授权判断只回答
  "该项目是否是本部署配置的项目"，不回答"该用户是否有权访问该项目"；
- **任意已认证用户**仍可在本部署允许的项目中创建**自己的**会话并获得其所有权
  （这是当前有意的最小模型：会话级隔离 + 部署级项目作用域）；
- 其他项目**不会**因为"已在 Project Registry 注册"而被自动授权；
- 未授权或不存在的会话**不会**被自动认领（无 `owner` 的历史会话在用户上下文
  一律拒绝）；
- **多项目授权需要未来建立真实的"用户—项目"授权模型**（项目成员关系 /
  角色 / 权限表）——当前 **NOT IMPLEMENTED**，不得以"已注册"代替；
- 会话身份与 X-API-Key 服务身份**并存不替代**（本 API 不接受 API Key；
  Agent / Workflow 端点不接受用户会话）；
- HTTPS / 网络边界仍为 **NOT VERIFIED** ⇒ **PRODUCTION_DEPLOYMENT 仍为
  BLOCKED**（与 §7 相同结论，需部署层独立验证；`AUTH_COOKIE_SECURE` 生产
  必须保持 `true`）。

配置（写入 `.env`；值**不得**提交到 Git；说明见 `.env.example`）：

```text
PROJECT_ID=vietnam-wms        # 本部署允许创建会话的项目（唯一授权来源）
AUTH_COOKIE_SECURE=true       # 生产必须为 true（TLS 内网例外需显式评估）
```

测试证据：`tests/test_conversation_ownership.py`（离线 31 例 + DB 真链 6 例）、
`tests/test_conversation_message_api_architecture_audit.py`、
`tests/test_conversation_api_db_e2e.py`、`tests/test_conversation_multiturn_db_e2e.py`、
`tests/test_conversation_context_e2e_db.py`（`RUN_DB_TESTS=1`）。

---

## 9. AI 执行入口认证（Phase 7 Recovery M2.3 A1；已实现）

四个 **AI 执行入口**（`POST /api/ai/chat`、`POST /api/chat`、
`POST /api/chat/with-tools`、`POST /api/rag/answer`）从**可匿名执行**改为
**必须认证**（对 Step 12 / 早期冻结契约的正式修订，先例：S7.1→S7.2 的
"审计缺口 → 语义翻转"）：

| 项 | 行为 |
| --- | --- |
| 匿名 | **不再允许**（未认证 ⇒ 401；LLM / Tool / RAG / Text-to-SQL 调用 = 0） |
| 身份来源 | M1 服务端会话（`wms_ai_session` HttpOnly Cookie → `ai_ops.user_session`）；**不**接受 API Key / Query / Body 身份字段 |
| 无效 / 过期 / 已撤销会话 | **401**（固定文案；不区分原因、不泄露账号存在性） |
| CSRF | 四个端点均为 POST 且**有服务端副作用**（LLM 成本 / 真实 Tool 执行 / SQL 只读查询 / 观测写入）⇒ 要求会话绑定双提交头 `X-CSRF-Token`（Cookie 值 == 头 == 服务端重算 HMAC）；缺失 / 不匹配 / 伪造 / 换会话 ⇒ **403**，副作用为零 |
| 认证服务故障 | **503 fail closed**（**绝不**降级为匿名继续调用 AI，见 §9 下方反模式） |
| 机器调用 | 当前**无**适用 API-Key 契约；本阶段**不**新增 API-Key 路径。若未来需要服务间调用，必须先建立并评审独立契约（**不得**以"兼容"为由保留匿名） |
| 响应 / 参数契约 | 除认证外不变（DTO / 校验语义 / Router 策略 / Prompt / Tool 契约 / 幂等行为均未修改） |

明确边界（**不**宣称已达成）：

- **A1 ≠ 全部 API 已完成认证**：A1 之后仍为只读匿名的端点 =
  `/api/usage*`（1）+ `/api/observability/*`（6，allowlist 冻结）——已由
  **A2（§10）**收口；`/api/health` 保持公开；
- 当前项目作用域仍是**单部署项目作用域**（§8），**不是**用户级 RBAC——
  不得表述为"已实现用户级授权"；
- HTTPS / 网络边界仍为 **NOT VERIFIED** ⇒ A1 完成**不代表**生产部署就绪
  （`PRODUCTION_DEPLOYMENT` 仍为 **BLOCKED**）。

禁止的反模式（本阶段明确不采用）：

```text
认证异常 / 未认证
    ↓
自动降级为匿名访问
    ↓
继续调用 LLM / Tool / RAG
```

测试证据：`tests/test_ai_execution_api_auth.py`（四端点认证 / CSRF 矩阵 +
失败路径零业务执行断言 + "不使用 dependency_overrides" 元断言）；
26 个调用方测试文件改用认证客户端（`tests/api_session_auth.py`，仅替换会话
存储 Fake，生产依赖仍真实执行）；四个入口 Python 源码侧认证说明见
`docs/api.md` §13。

---

## 10. 只读用量／可观测性接口认证与授权（Phase 7 Recovery M2.3 A2；已实现）

A2 之前，7 个只读端点**匿名可达**，可读取全局 LLM 用量、跨项目 Tool 执行历史
与 Assistant Trace。A2 按**数据维度的可信度**把 7 个端点分成两类收口——
**不新增 API-Key 旁路、不新增 `AppUser.role` 列、不引入 RBAC**。

### 10.1 两类访问模型

| 端点 | 访问模型 | 依据 |
| --- | --- | --- |
| `GET /api/usage/analytics` | Session + **运维白名单** | `llm_usage_record` 无 `project_id` 维度 ⇒ 只能全局 |
| `GET /api/observability/tools` | Session + **运维白名单** | 进程内 Collector 全局快照 |
| `GET /api/observability/tools/metrics` | Session + **运维白名单** | Metrics Read Model **无** `by_project` 维度 |
| `GET /api/observability/tools/history` | Session + **服务端 `PROJECT_ID`** | `tool_execution_record.project_id` 为服务端可信维度 |
| `GET /api/observability/tools/metrics/persistent` | Session + **服务端 `PROJECT_ID`** | 同上（SQL 聚合下推） |
| `GET /api/observability/assistant-trace/{id}` | Session + **运维白名单** | 见 §10.4 残留风险 |
| `GET /api/observability/assistant-timeline/{id}` | Session + **运维白名单** | 同上 |

### 10.2 认证与授权行为

| 项 | 行为 |
| --- | --- |
| 匿名（无 / 空 / 无效 / 过期 / 已撤销会话） | **401**（固定文案；与资源是否存在无关；认证前零数据读取） |
| 会话服务不可用 | **503 fail closed**（**绝不**降级为匿名放行） |
| 运维白名单缺失 / 空白 / 无法解析（含空条目、尾逗号、条目超长、条目数超限） | **503 fail closed**（固定文案；不回显白名单内容） |
| 已认证但 `user_id` 不在白名单 | **403**（固定文案；不泄露白名单内容） |
| 部署项目作用域（`PROJECT_ID`）缺失 / 空白 | **503 fail closed**（固定文案；**不**回退默认项目） |
| 身份来源 | M1 服务端会话 `Principal.user_id`（服务端签发 uuid4，不可枚举）；**绝不**接受客户端角色 / 管理员标志 / `is_admin` / 用户 ID 声明 |
| 有效 Session ≠ 运维权限 | 认证与授权**分离**：登录不等于获得运维读取授权 |
| CSRF | 7 个端点均为 **GET（安全方法）** ⇒ **不适用** CSRF；缺少 `X-CSRF-Token` 或携带错误值均不产生 403（避免人为回归） |
| API-Key | **不新增**旁路（与 Agent / Workflow Read 的 X-API-Key 域并存不替代） |

配置（写入 `.env`；值**不得**提交 Git；说明见 `.env.example`）：

```text
OBSERVABILITY_READ_OPS_USER_IDS=<逗号分隔 user_id 白名单>   # 无默认值；未配置 ⇒ 503
PROJECT_ID=vietnam-wms                                      # 服务端唯一授权项目（§8）
```

### 10.3 服务端项目作用域与客户端 `project_id`

- A2-04 / A2-05 的**客户端 `project_id` 查询参数已移除**（Step 32 / Step 33
  曾提供）——它不能扩大授权范围；与 `workflow_read`（§7）同形同语义。
- `tool_name` / `success` 仅作为**业务过滤条件**，与服务端作用域 AND 后下推 SQL。
- **`project_id IS NULL` 的记录对任何会话均不可见**：`ToolExecutionContext.project_id`
  允许为 `None`（该执行边界未绑定项目），与服务端项目不匹配即被排除。这是
  **有意的 fail-closed 取舍**（宁可不可见，不可越权可见）。
- 越权访问不单独返回 403：范围外记录只是**不在结果集内**（与"不存在"同构，
  不泄露存在性）。

### 10.4 残留风险（**明确记录，不得表述为已解决**）

1. **A2-01 / A2-02 / A2-03 是运维专用全局视图**，不具备项目级数据过滤：
   `llm_usage_record` 无 `project_id` 列；Runtime Metrics Read Model 无
   `by_project` 维度。当前阶段**不得**虚构项目过滤能力。
2. **A2-06 / A2-07 无法验证资源级项目归属**：`ai_ops.llm_usage_record` 与
   `ai_ops.assistant_outcome_record` **均无 `project_id` 列**；
   `ai_ops.resource_owner` 的资源类型白名单只有
   `CONVERSATION` / `AGENT_RUN` / `WORKFLOW_RUN`，**不登记**
   `assistant_request_id`；`ai_ops.conversation` 为 5 冻结字段（**无**
   `assistant_request_id`）。因此无法在不"猜测"（例如用局部 Tool / RAG 记录的
   项目反推整体归属）的前提下判定 Trace 所属项目 ⇒ 只能整端点限定为运维专用。
   **"知道 `assistant_request_id` 即可读"已不再成立**（还必须是运维用户），
   但**资源级项目隔离仍未实现**。
3. **运维白名单是部署级授权，不是用户级 RBAC**：无角色层级、无项目成员关系；
   多项目 / 多角色授权模型仍 **NOT IMPLEMENTED**（§8）。
4. **运维授权配置是新的部署依赖**：未配置 `OBSERVABILITY_READ_OPS_USER_IDS`
   时，5 个运维端点一律 503（fail closed）。部署方必须显式配置，否则运维
   可观测性不可用（这是有意的默认拒绝）。
5. **A2 对 7 个端点的匿名调用方构成 breaking change**（与 A1 同性质），
   见 `docs/api.md` §14。
6. HTTPS / 网络边界仍为 **NOT VERIFIED** ⇒ `PRODUCTION_DEPLOYMENT` 仍为
   **BLOCKED**。

### 10.5 测试证据

`tests/test_observability_read_api_auth.py`（64 例；真实 M1 会话装配，
**不使用** `dependency_overrides`）：七端点匿名 401、无效 / 过期 / 已撤销会话
401、白名单 fail-closed 503、非白名单 403、运维用户正向契约（DTO 不变）、
客户端 `project_id` 不能扩大范围、`project_id` 不再是 Handler / OpenAPI 参数、
Trace / Timeline 运维保护、GET 无 CSRF 人为回归、错误响应不泄漏敏感信息、
只读无副作用（Collector 不变 / Facade 单次调用 / 无写方法）。
DB 级断言见 `tests/test_tool_observability_history_api_db.py`
（`test_null_project_record_is_invisible`）、
`tests/test_tool_observability_history_filtering_db.py`（服务端作用域 +
客户端 `project_id` 被忽略 + 范围外记录不可见）、
`tests/test_tool_observability_persistent_metrics_db.py`（聚合只在授权范围内）。
