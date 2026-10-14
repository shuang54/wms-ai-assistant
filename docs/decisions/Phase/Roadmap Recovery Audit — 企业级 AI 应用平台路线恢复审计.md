# Roadmap Recovery Audit — 企业级 AI 应用平台路线恢复审计

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、审计目标

当前项目已经完成大量 AI Core、Conversation/Agent Runtime、Workflow、治理及真实 WMS 库存接入工作。

但最近的工作重点偏向 Phase 7 后端闭环、真实库存查询和部署安全审计，需要确认是否偏离原始企业级 AI 应用平台路线。

本次任务不是继续开发新功能，而是：

1. 恢复原始完整技术路线图。
2. 核实每个 Phase 的真实完成情况。
3. 核实多应用、多用户、项目隔离、RBAC 等平台能力。
4. 核实前端页面是否存在、完成到什么程度。
5. 识别已完成但尚未接入前端的后端能力。
6. 制定从当前状态到可用产品的最短交付路线。

**只读审计，不修改代码，不部署，不提交 Git，不创建新 Phase 实现。**

---

## 二、审计规则

必须遵守：

- 以当前 Git 仓库中的真实代码、测试、文档、Git 历史为依据。
- 优先恢复已有 Roadmap、ADR、Phase Completion Report 和架构冻结记录。
- 不要根据文件名或旧报告推断当前实现仍然存在。
- 不要把测试通过等同于生产功能完整。
- 不要把项目作用域等同于完整多租户。
- 不要把 API 存在等同于前端功能完成。
- 不要把接口契约、DTO 或空壳目录等同于已实现功能。
- 不要重复运行已经有可靠证据的全量测试。
- 如果证据不足，明确标记 `UNKNOWN`。
- 不要为了使路线图看起来完整而人为补造历史决策。

禁止执行：

```text
git reset
git checkout -- .
git clean
git commit
git push
git merge
数据库 migration
生产数据库写入
真实 LLM 调用
网络部署
```

不要修改任何文件，包括路线图、报告和测试。

---

## 三、Step 1：Git 与项目结构审计

检查：

```powershell
git status --short
git branch --show-current
git log --oneline -30
git diff --stat
```

确认：

- 当前分支、HEAD、工作区状态。
- 是否存在未提交代码。
- 当前分支与 origin/main 的关系；只有已有远程跟踪信息时才读取，不主动改变分支。
- 最近各 Phase 的实际提交。
- 是否有代码未提交导致旧报告与当前代码不一致。

检查项目目录：

```text
backend/
frontend/
web/
ui/
tests/
scripts/
docs/
docker-compose.yml
Dockerfile
AGENTS.md
```

目录不存在时如实记录，不要自动创建。

---

## 四、Step 2：恢复原始 Roadmap

搜索以下文档与历史记录：

```text
docs/architecture.md
docs/requirements.md
docs/decisions/
docs/evaluation/
docs/roadmap*
docs/plans*
AGENTS.md
README.md
```

同时搜索 Phase 3、4、5、6、7 的：

- Completion Report
- Architecture Frozen
- Capability Matrix
- Roadmap Recovery
- Merge Verification
- Deployment Readiness
- ADR

如果文档不完整，使用 Git 历史中的文件恢复真实决策。

输出原始路线图：

```text
Phase
目标
核心能力
前置依赖
完成证据
未完成事项
冻结约束
```

区分：

1. 原始规划。
2. 后续正式批准的调整。
3. 临时实施步骤。
4. 尚未批准的建议。

不要把临时 Step 编号当成正式产品路线图。

---

## 五、Step 3：后端能力审计

检查实际代码与测试，至少覆盖以下能力。

### A. AI Core

```text
LLM Provider
LLM Client
RAG
Tool Registry
Tool Authorization
Text-to-SQL
SQL Validator
Read-only SQL Executor
AI Router
AI Orchestrator
Structured Output
Refusal
LLM Observability
```

### B. Conversation Runtime

```text
Conversation
Message
Context
History
Evidence
Request / Response association
Idempotency
```

重点检查：

- 真实模型响应是否能够正确关联到 Conversation、Message、Run 和 Evidence。
- 是否存在 DTO 有字段但实际执行链路未写入的情况。
- 是否存在数据库持久化了结果，但 API 无法完整读取的情况。

### C. Agent Runtime

对照现有 Capability Matrix 检查：

```text
Agent Decision
Tool Authorization
Tool Execution
Tool Result Persistence
AgentRun
AgentStep
AgentDecision
AgentCheckpoint
Tool Replay
Resume
HITL Approval
Cooperative Cancellation
Agent Read API
Project Isolation
```

Phase 5 已冻结的能力必须以原有冻结决策为准，不得重新设计。

### D. Workflow Runtime

检查：

```text
Workflow Definition
Workflow Catalog
Workflow Execution
Workflow State
Workflow Read API
Failure / Cancellation
Authorization
```

区分：

- 仅有定义查询 API。
- 已有可执行 Workflow。
- 已有完整的用户可操作 Workflow 页面。

这三者不能混为一谈。

### E. 企业治理

必须单独检查：

```text
User Identity
Authentication
Organization / Tenant
Project
Role
Permission
Role Assignment
Resource Ownership
Tool Authorization
Audit Trail
Secret Management
```

重点核实：

- 是否存在真实 User Model / Repository / Service。
- 是否有登录、登出、身份验证和会话机制。
- 是否有真实 RBAC，而不只是单个 API Key。
- 是否有组织/租户模型和跨租户隔离。
- 用户身份是否能够贯穿 Conversation、AgentRun、ToolResult。
- 服务端是否对每个资源执行授权。
- 未配置认证时是否 fail-closed。

如果只有项目 ID 和 API Key，应标记为局部认证/项目作用域能力，不能标记为完整多用户平台。

---

## 六、Step 4：前端现状审计

如果存在前端目录，检查：

```text
package.json
src/
pages/
views/
router/
components/
api/
stores/
```

确认真实技术栈及可运行入口。

逐项检查：

| 功能 | 检查内容 |
|---|---|
| 应用首页 | 是否存在真实路由和页面 |
| 登录 | 是否接入真实认证 |
| 应用列表 | 是否有应用注册、选择与配置 |
| AI 对话 | 是否真实调用后端 |
| Agent 执行过程 | 是否展示运行状态和步骤 |
| Tool 调用 | 是否展示参数摘要、结果和错误 |
| 库存查询 | 是否展示真实查询结果表格 |
| 历史记录 | 是否从后端读取 |
| Run 详情 | 是否读取 Agent Read API |
| Workflow | 是否存在真实执行与状态页面 |
| 用户管理 | 是否有真实用户数据与管理操作 |
| 角色权限 | 是否与服务端授权一致 |
| 项目管理 | 是否支持选择并隔离项目 |
| 模型管理 | 是否能管理或选择可用模型 |
| 知识库管理 | 是否有真实配置和操作入口 |
| 工具管理 | 是否有注册、授权和状态展示 |
| 错误处理 | 是否有 loading、empty、error 状态 |

每项必须给出：

```text
代码路径
实际实现证据
API 是否接通
测试证据
结论
```

如果不存在前端，就明确标记 `MISSING`。

如果只有后端 API，不得标记前端已完成。

如果有页面但只是静态 Demo，标记 `PARTIAL`。

---

## 七、Step 5：真实 WMS 业务接入审计

重点核实：

```text
get_inventory
WMS_DATABASE_* configuration
business_session.py
Read-only transaction
SQL timeout
Schema allowlist
Parameter validation
Result limit / truncation
Project datasource isolation
```

检查真实库存查询是否仍使用：

- 独立业务数据库连接。
- 只读账号和只读事务。
- 参数化 SQL。
- 查询超时。
- 明确的最大返回行数。
- 失败关闭，无回退到应用数据库。
- 结果与原始库存报表 SQL 的验证证据。

不要打印任何密码、API Key、数据库 URL 或完整连接字符串。

不要重新执行真实 LLM Smoke。

不要写入业务数据库。

同时核实已知未解决项：

- 业务数据库 TLS 不可用或尚未解决。
- 公司内网 HTTPS 尚未配置。
- 应用入口仍可能使用开发模式 reload。
- DeepSeek 请求模型名与响应模型名的差异。

只更新事实状态，不在本次修复问题。

---

## 八、Step 6：产品完整度矩阵

建立审计结果矩阵，但不要保存到文件。

每项只能使用：

```text
READY
PARTIAL
MISSING
BLOCKED
DEFERRED
UNKNOWN
```

定义：

- `READY`：有当前代码、测试或运行证据。
- `PARTIAL`：存在实现，但核心能力或完整链路缺失。
- `MISSING`：确认没有实现。
- `BLOCKED`：实现或验收被外部条件阻塞。
- `DEFERRED`：有证据表明已明确推迟。
- `UNKNOWN`：证据不足，无法判断。

矩阵至少包含：

```text
AI Core
Conversation Runtime
Agent Runtime
Workflow Runtime
User Identity
Authentication
Multi-tenant
RBAC
Multi-project
Multi-application
Model Configuration
Knowledge Base Management
Tool Management
Audit Trail
Web UI
WMS Inventory Integration
Deployment Security
```

对每项列出：

```text
当前状态
完成证据
缺失内容
前置依赖
```

---

## 九、Step 7：判断产品是否已经完整

明确回答：

1. 是否已有可实际使用的 Web 页面？
2. 用户能否登录？
3. 是否支持多个用户？
4. 是否支持不同用户之间的数据隔离？
5. 是否支持多个应用？
6. 是否有完整的角色权限系统？
7. 用户是否能够通过页面发起 Agent 运行？
8. 是否能查看历史记录、步骤和 ToolResult？
9. WMS 库存查询是否从真实业务数据库读取？
10. 是否已经满足公司内网使用的安全条件？

每个回答必须基于代码证据，不能猜测。

---

## 十、Step 8：恢复最短交付路线

不要继续自动开发 Phase 8 或其他新 Phase。

根据真实差距，提出最短的交付顺序，优先级按以下原则：

1. 恢复已有架构承诺，不能绕开冻结约束。
2. 优先打通现有后端能力的用户操作入口。
3. 再补齐用户身份、资源归属和必要授权。
4. 补齐多应用的配置与切换能力。
5. 补齐管理界面和可观察性。
6. 最后解决公司内网 HTTPS、业务数据库安全连接及部署验收。

但如果真实审计发现用户/租户授权是前端联调的硬前置条件，应调整顺序并说明理由。

不要提出大规模重构。

不要重新开发已有后端能力。

每项后续工作必须说明：

```text
目标
为什么现在做
依赖
涉及模块
验收标准
```

---

## 十一、最终输出格式

### 1. 审计结论

明确是否偏离原始路线，以及偏离的具体部分。

### 2. 原始路线图

按 Phase 展示原始目标与正式调整。

### 3. 当前能力矩阵

使用状态标签，附代码证据。

### 4. 前端现状

给出实际页面、路由、组件、API 联调证据。

### 5. 多用户/多应用/权限审计

分别说明身份、认证、租户、角色、项目与资源授权。

### 6. WMS 业务链路

确认真实库存查询实现与安全边界。

### 7. 当前主要缺口

只列有证据支持的缺口，区分内部工作和外部阻塞。

### 8. 最短交付路线

最多 5 个后续里程碑，不实施。

### 9. 风险与未确认事项

明确 `UNKNOWN` 项。

### 10. 最终判断

回答：

```text
当前是否是完整企业级 AI 应用平台？
当前是否有可供用户使用的 Web 页面？
下一步最应该做什么？
```

### 11. Git 安全

报告：

```text
文件修改数 = 0
Git commit = 0
Git push = 0
部署 = 0
数据库写入 = 0
真实 LLM 调用 = 0
```

以上必须是实际执行结果，不能预先宣称。

## 十二、STOP

本次只读审计结束后立即停止。

不要实现任何新功能。
不要创建路线图文件。
不要修改 AGENTS.md。
不要进入下一 Phase。
等待用户确认后再开始下一项工作。