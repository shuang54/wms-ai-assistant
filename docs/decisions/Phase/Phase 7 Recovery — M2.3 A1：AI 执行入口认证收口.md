# Phase 7 Recovery — M2.3 A1：AI 执行入口认证收口

## 一、阶段状态与授权

项目：

`D:\coding\ai\wms-ai-assistant`

当前已知基线：

```text
Branch: phase7
HEAD: bbb5d7508b64d1b12e5215c25f8e9cf61b108726
Latest completed milestone: M2.2 + D1
```

实施前必须重新核验实际 Git 状态。

本任务书由用户明确选择候选 A 后制定。

**状态：PROPOSED TASK SPEC — 待确认实施授权。**

本任务只定义范围、认证策略、兼容性、测试及停止条件，不代表已经开始实施。

## 二、目标

为以下 4 个 AI 执行入口建立明确的身份认证边界：

| HTTP 方法 | 路径 | 本阶段目标 |
|---|---|---|
| POST | `/api/ai/chat` | 必须通过认证后才能进入 AI 处理 |
| POST | `/api/chat` | 必须通过认证后才能进入 AI 处理 |
| POST | `/api/chat/with-tools` | 必须通过认证后才能进入 Tool/AI 处理 |
| POST | `/api/rag/answer` | 必须通过认证后才能进入 RAG 处理 |

具体路由和行为必须以当前代码为准。如果路径、方法或实际职责与上表不一致，先报告差异，不要自行猜测。

本阶段只解决：

> 未认证调用者不能通过这 4 个入口匿名触发 LLM、RAG、Tool 或 Text-to-SQL 业务执行。

不将本阶段扩大为整个 API 的认证改造。

## 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/api/
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py

backend/app/auth/
backend/app/core/
backend/app/projects/

tests/
tests/conversation_auth_support.py
tests/test_conversation_ownership.py
tests/test_observability_http_allowlist_audit.py

docs/api.md
docs/security.md
docs/architecture.md
AGENTS.md
```

如果实际文件位置不同，以仓库搜索结果为准。

重点确认：

1. 四个入口各自的真实调用链。
2. 是否存在调用 LLM、Tool、RAG、Text-to-SQL 的其他间接路径。
3. M1 `CurrentPrincipal`、`require_session`、`require_csrf` 的实际接口。
4. 当前 API-Key 认证依赖及服务间调用契约。
5. 当前请求 DTO、响应 DTO、异常映射和幂等行为。
6. 现有测试中有哪些匿名调用方。
7. 可观测性 allowlist 与 Matrix Gate 的冻结规则。

优先复用 M1/M2.2 已有认证机制和测试装配，不重复建立身份系统。

## 四、认证策略

### 4.1 默认策略：M1 Session

对于浏览器用户调用，优先复用：

```text
CurrentPrincipal
require_session
require_csrf
```

禁止自行实现：

- 新 Session 系统
- JWT/OAuth 替代机制
- 新认证中间件
- 新的用户身份 DTO
- 与 M1 并行的 Token 机制

认证必须发生在任何 AI 业务执行之前。

预期调用顺序：

```text
HTTP Request
    ↓
Session Authentication
    ↓
CSRF Validation（适用于需要保护的状态变更请求）
    ↓
Existing Request Validation
    ↓
Existing AI / RAG / Tool Service
    ↓
Existing Response Contract
```

### 4.2 服务间调用兼容性必须先审计

四个端点目前可能被脚本、内部服务或自动化任务调用。

不得在未检查调用方的情况下，直接将所有调用一律改成只能使用浏览器 Session。

实施前必须形成调用方清单，至少包含：

```text
调用方
调用端点
当前认证方式
是否依赖匿名访问
是否为浏览器调用
是否为服务间调用
预期兼容策略
```

认证方案遵守以下规则：

1. 浏览器用户使用 M1 Session。
2. 如果存在合法服务间调用，必须先核实当前项目是否已经有适用的 API-Key 契约。
3. 不得把 API-Key 当作人类用户身份。
4. 不得为了兼容调用方而让匿名请求继续放行。
5. 不得未经审查就新增 API-Key 认证路径。
6. 如果当前契约无法同时满足安全要求和兼容性要求，先报告并停止，等待用户决定。

**不能把“保持兼容”解释成“保留匿名 AI 执行”。**

### 4.3 CSRF

四个入口均为 POST，但 POST 不自动意味着一定需要新增 CSRF 逻辑。

应根据实际 Session/Cookie 认证模式和端点的副作用确定。

凡是依赖浏览器自动携带 Session Cookie 的调用，必须按现有 M1 CSRF 契约执行，不能仅凭 `SameSite=Lax` 或自定义前端请求头就认定已完成保护。

CSRF 的基本原则可参考 OWASP 官方指南：对依赖 Cookie 认证的状态变更请求使用服务端校验的 Token，并拒绝缺失或不匹配的 Token。  
参考：[OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)。

## 五、认证失败的行为契约

所有四个入口都必须遵守：

### 5.1 未认证请求

预期：

```text
HTTP 401
固定且不泄露内部信息的错误响应
LLM calls = 0
RAG calls = 0
Tool executions = 0
Text-to-SQL calls = 0
```

无效、过期、已撤销 Session 必须遵循现有 M1 语义。

不要为了本阶段方便而修改 M1 的认证错误契约。

### 5.2 CSRF 失败

适用 CSRF 的请求：

```text
Missing CSRF token → 403
Invalid CSRF token → 403
Mismatched CSRF token → 403
```

必须证明请求没有进入业务执行层，且没有发生副作用。

### 5.3 故障处理

认证服务异常时，必须沿用当前 M1 的 fail-closed 语义。

禁止：

```text
认证异常
    ↓
自动降级为匿名访问
    ↓
继续调用 AI
```

如果认证依赖的实际错误映射无法明确确认，先核对现有实现，不要在本阶段重新设计异常体系。

## 六、兼容性要求

本阶段允许改变的外部行为：

> 原本允许匿名调用的 4 个 AI 执行入口，在没有合法认证时拒绝执行。

除此之外，应尽量保持既有 API 契约不变。

必须保持：

- 请求 DTO 字段不变。
- 成功响应 DTO 不变。
- 正常业务结果结构不变。
- 现有 Tool 参数契约不变。
- RAG 检索与 Text-to-SQL 语义不变。
- Router 路由策略不变。
- Prompt 不变。
- 幂等行为不变。
- 现有错误码中与本阶段无关的语义不变。

### 6.1 破坏性变更声明

在任务书和最终报告中明确记录：

```text
Breaking change:
The four AI execution endpoints no longer permit anonymous execution.
```

同步记录受影响的内部脚本、测试客户端和集成调用方。

如果存在合法的服务间调用，不得擅自将它们改为匿名调用或混入人类 Session 身份。

### 6.2 OpenAPI / Swagger

更新端点的认证说明，使其与真实实现一致。

必须检查：

- 认证依赖是否正确展示。
- 登录后 Cookie 如何携带。
- CSRF Header 如何携带。
- 401/403 响应是否正确描述。
- 现有成功响应 Schema 是否保持不变。

Swagger 验证只用于检查契约，不得记录或暴露真实 Session Cookie、CSRF Token 或 API Key。

## 七、实现范围

### 允许修改

原则上只允许修改：

```text
四个 AI 执行入口所在的 API 模块
对应的测试与测试辅助代码
docs/api.md
docs/security.md
```

如果现有项目结构要求修改其他文件，必须先说明原因，并确认仍在 A1 范围内。

### 必须复用

- M1 Session 认证。
- M1 CSRF 机制。
- M2.2 测试装配和断言方法。
- 当前项目的错误响应契约。
- 现有 LLM、RAG、Tool 和 Text-to-SQL Service。

### 明确禁止

本阶段不修改：

```text
AI Router 策略
AI Orchestrator 核心语义
RAG 核心算法
Text-to-SQL Generator
SQL Validator
SQL Executor
Tool Registry 核心实现
Agent Runtime
Workflow Runtime
Conversation / Turn / Evidence 冻结模型
历史 Evaluation Baseline
Matrix Execution Baseline
Offline Execution Summary Snapshot
28 文件矩阵注册表
可观测性 6 路由 allowlist 契约
```

如果测试发现上述模块存在问题，先报告根因和影响，不得顺手重构。

## 八、测试计划

测试必须使用真实的认证依赖和业务调用边界，避免仅通过直接调用内部函数证明安全。

### 8.1 每个端点的认证矩阵

四个端点分别覆盖：

| 场景 | 预期 |
|---|---|
| 未登录 | 401 |
| 无效 Session | 401 |
| 过期 Session | 401 |
| 已撤销 Session | 401 |
| 合法 Session | 按既有业务契约执行 |
| 认证服务故障 | Fail-closed |
| 合法请求参数 | 既有响应契约不变 |
| 无效请求参数 | 既有参数校验语义不变 |

所有失败场景必须验证业务 Service 未被调用。

### 8.2 CSRF 矩阵

对于实际依赖浏览器 Session 的入口，覆盖：

```text
缺少 Token
错误 Token
与 Session 不匹配的 Token
正确 Token
```

验证：

- 失败时返回正确状态码。
- 失败时不调用 LLM、RAG、Tool 或 Text-to-SQL。
- 不产生会话、业务写入或其他副作用。
- 正常请求仍然成功。

### 8.3 四类成功路径

分别证明：

```text
/api/ai/chat
    → 既有 AI 业务路径成功

/api/chat
    → 既有 Chat 业务路径成功

/api/chat/with-tools
    → 既有 Tool Chat 路径成功

/api/rag/answer
    → 既有 RAG 路径成功
```

使用现有 Fake/Stub 或项目既有测试基础设施。

不得为了让测试通过而跳过真实认证依赖，也不得 Fake 认证结果后将其宣称为完整 E2E。

### 8.4 副作用断言

匿名、无效 Session 和 CSRF 失败场景至少证明：

```text
LLM calls = 0
Tool executions = 0
```

RAG、Text-to-SQL 和相关数据库调用按各自真实调用链设置断言。

对于数据库测试，使用现有 `RUN_DB_TESTS=1` 门控；只检查本阶段必要的测试数据库状态，不能连接生产数据库。

### 8.5 回归测试

先运行四个端点的定向测试及受影响的 API 测试，再运行：

```powershell
$env:PYTHONUTF8="1"
python -m pytest -q
python -m compileall backend tests scripts
```

随后运行：

```powershell
python scripts/run_matrix_gate.py
```

如项目既有门控要求 DB 测试，则使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

不能使用 Unix Shell 环境变量赋值语法。

### 8.6 历史失败基线

当前已知 3 个 Step 8 历史失败：

- `test_tool_argument_contract.py` 两项。
- `test_tool_argument_extractor.py` 一项。

M2.3 A1 不得默认修复这三项，也不得修改历史基线。

必须实际运行并确认失败集合。如果出现新的失败，必须单独报告，不能直接归入历史遗留。

## 九、数据库与安全残留

如果测试使用数据库：

- 必须复用现有测试数据库。
- 不连接生产数据库。
- 不修改冻结表结构。
- 不新增 migration。
- 不导入真实 WMS 业务数据。
- 测试结束后执行只读残留检查。

至少检查与本阶段实际触及路径相关的：

```text
app_user
user_session
resource_owner
conversation
conversation_turn
以及现有 observability 表
```

只报告实际查验结果，不得在未检查时声称 residue 为 0。

## 十、文档要求

更新：

```text
docs/api.md
docs/security.md
```

只增加 A1 直接相关的契约说明。

必须说明：

1. 四个端点不再允许匿名执行。
2. 浏览器调用使用现有 M1 Session。
3. 适用 CSRF 的请求必须携带合法 Token。
4. 合法服务间调用必须使用已确认的认证契约。
5. 401/403 语义。
6. 未认证请求不会触发 AI 执行。
7. A1 不等于全部 API 已完成认证。
8. A2 的 7 个只读端点尚未纳入本阶段。
9. HTTPS/TLS 尚未验证，因此本阶段完成不代表生产部署就绪。

不要把当前单部署项目作用域误写成用户级 RBAC。

## 十一、Git 与提交边界

本任务书不授予代码实施、提交或推送权限。

执行本任务书的规划与审查时：

```text
Git add: NOT AUTHORIZED
Git commit: NOT AUTHORIZED
Git push: NOT AUTHORIZED
PR: NOT AUTHORIZED
Deployment: NOT AUTHORIZED
A2: NOT STARTED
```

如果未来获得实施授权，实施阶段仍必须单独执行提交前审查，并获得明确的提交授权。

## 十二、完成标准

只有同时满足以下条件，才能报告 A1 实施完成：

1. 四个端点均有明确的认证策略。
2. 匿名与无效 Session 均无法触发业务执行。
3. CSRF 保护适用时真实有效。
4. 合法认证请求仍保持既有业务语义。
5. API 请求和响应契约无非预期变化。
6. 定向测试通过。
7. 全量测试没有新增失败。
8. compileall 通过。
9. Matrix Gate 结果真实记录。
10. DB residue 已按实际测试范围核验。
11. 文档与实现一致。
12. 未修改冻结模块、历史 baseline 或治理快照。

如果服务间调用方尚未识别清楚，或认证策略会破坏合法调用契约，则必须报告：

```text
M2.3 A1: BLOCKED
Reason: Caller authentication contract unresolved
```

不得用匿名放行规避阻塞。

## 十三、最终报告格式

```text
【Phase 7 Recovery — M2.3 A1 Completion Report】

1. Branch / HEAD
2. 修改文件
3. 四个端点的认证策略
4. 服务间调用兼容性
5. CSRF 边界
6. 请求 / 响应兼容性
7. 定向测试
8. 全量测试
9. 历史失败与新增失败
10. compileall
11. Matrix Gate
12. DB residue
13. 文档变化
14. Git diff
15. 剩余风险
16. 最终状态：READY / BLOCKED / NOT READY

Git add: NOT AUTHORIZED
Git commit: NOT AUTHORIZED
Git push: NOT AUTHORIZED
Deployment: NOT AUTHORIZED
A2: NOT STARTED
STOP
```

## 十四、强制停止条件

本任务书制定后立即停止。

不要修改代码，不要新建文件，不要运行会改变数据库状态的测试，不要执行 Git 写操作。

等待用户明确授权开始 M2.3 A1 实施。