# Phase 6 Step 7.2 — Workflow Read API 最小安全修复

你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

依据：

`docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md`

修复 Workflow Read API 已确认的核心安全缺口：

1. 未认证请求可以读取 Workflow Run / Approval。
2. 客户端可以任意修改 `project_id`，访问其他项目的数据。
3. 项目作用域没有与认证凭据绑定。
4. 当前没有仓库证据证明部署环境存在可信网关或网络边界保护。

本阶段只实现**最小应用层 API Key 认证 + 服务端项目作用域授权**。

完成代码修复不代表生产环境已经安全。HTTPS、网络边界和部署配置仍需单独验证。

## 二、开始前必须先阅读

先阅读真实代码，不要假设接口：

```text
docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md
backend/app/api/workflow_read.py
backend/app/dto/workflow_read_api.py
backend/app/workflow_runtime/read_factory.py
backend/app/workflow_runtime/read_service.py
backend/app/main.py
backend/app/api/
backend/app/core/
backend/app/config/
tests/test_workflow_read_api.py
tests/test_workflow_read_api_db.py
tests/test_workflow_read_api_security_audit.py
```

如果目录或文件不存在，先检查实际项目结构，不要因为路径不存在就自行创建一套平行架构。

同时阅读：

```text
docs/security.md
docs/architecture.md
docs/decisions/
```

确认现有配置、异常处理、日志和测试习惯。

## 三、严格保护当前工作区

此前 Phase 6 Step 5、6、7 的工作仍有未提交修改。

开始和结束时执行：

```powershell
git status --short
git diff --check
git diff --stat
```

禁止：

```text
git reset
git clean
git stash
覆盖或删除已有工作
擅自 commit / push
```

不得覆盖此前的 Workflow Runtime、Read Service、Read API 或相关测试。

## 四、认证方案

### 4.1 最小实现

优先使用项目现有配置体系，增加服务端配置，例如：

```text
WORKFLOW_READ_API_KEY
WORKFLOW_READ_API_PROJECT_ID
```

具体命名和类型应遵循现有配置规范。

要求：

* API Key 必须从服务端配置读取，不能写死在源码、测试默认值或文档中。
* 请求必须提供正确的认证凭据，否则拒绝访问。
* 未配置必要认证参数时必须 fail closed，绝不能退化为匿名可访问。
* 不允许通过请求参数、普通请求头或请求体直接指定自己的授权项目。
* 不在日志、异常、响应体或审计文档中输出 API Key。
* 比较凭据时使用适当的恒定时间比较方式。
* 不要把 `project_id` 默认值 `"vietnam-wms"` 当作授权机制。

如果现有配置框架不支持上述命名，可以按项目实际设计调整，但必须保持相同安全语义。

### 4.2 项目作用域绑定

本阶段先采用最小的**单凭据绑定单项目**模型：

```text
有效 API Key
    ↓
服务端认证配置
    ↓
服务端授权 project_id
    ↓
Workflow Read Service
```

服务端配置中的 `WORKFLOW_READ_API_PROJECT_ID` 才是授权作用域的来源。

如果 API 当前仍接受客户端传入 `project_id`：

* 不得直接信任客户端值。
* 可以移除该参数，或为兼容性保留但严格校验。
* 请求的项目与授权项目不一致时必须拒绝。
* 不得因为客户端提交了一个合法格式的项目 ID，就允许读取该项目的数据。
* 不得因缺少 `project_id` 而回退到未经授权的全局查询或默认项目。

如果现有实现支持多项目查询，必须确保本阶段新增的认证机制不会绕过项目隔离；不要为了维持旧行为而扩大授权范围。

### 4.3 本阶段不做什么

禁止新增：

```text
完整登录系统
用户注册
密码管理
OAuth2 Authorization Server
JWT 签发系统
RBAC 管理后台
新的用户数据库表
Workflow 写接口
Agent / MCP / LangGraph
```

不需要为实现 API Key 而引入大型安全框架。

API Key 仅代表持有该凭据的调用方，不等同于完整的用户身份系统。

## 五、四个端点必须全部受保护

当前端点：

```text
GET /api/workflow-runs/{run_id}
GET /api/workflow-runs/{run_id}/nodes
GET /api/workflow-runs/{run_id}/timeline
GET /api/workflow-approvals/{approval_id}
```

要求：

1. 四个端点全部使用同一套认证与项目作用域授权依赖。
2. 不得只保护其中一个端点。
3. 认证与授权应发生在读取敏感数据之前。
4. 不得依赖前端隐藏按钮或客户端自行传递正确的项目 ID。
5. 不得把可信授权作用域来源于可被客户端伪造的 `X-Project-ID` 等普通请求头。
6. 不得相信未经验证的代理身份头。
7. 保留现有 DTO 映射、Read Service、Adapter、Runtime 和数据库结构。
8. 保留现有的敏感字段隔离、子记录归属校验及安全错误响应。

如果使用 FastAPI `Depends()` / `Security()`，必须确认依赖实际挂载到了全部四个端点，并且认证失败时请求确实被拒绝，而不是只在 OpenAPI 中显示安全方案。

## 六、错误处理与日志

按项目既有 API 错误规范实现，并明确区分认证失败、授权失败和资源不存在的处理。

要求：

* 缺失或错误凭据不能返回业务数据。
* 项目作用域不匹配不能返回其他项目的数据。
* 不暴露 API Key、内部配置、堆栈、数据库连接信息。
* 不因为认证失败而泄露目标 Run / Approval 是否存在。
* 对认证失败和授权拒绝保留必要的安全事件记录。
* 日志不得包含原始凭据。
* 不得将客户端提供的项目 ID 直接记录为已经验证的身份或授权结论。

本阶段不要求建设完整日志平台。

## 七、测试要求

优先扩展现有测试基础设施，禁止为了让测试通过而构造一个实际上没有认证保护的 Fake。

### 7.1 离线安全测试

至少验证：

1. 四个端点缺少 API Key 均拒绝访问。
2. 错误 API Key 均拒绝访问。
3. 服务端未配置 API Key 时 fail closed。
4. 正确 API Key 可以访问其授权项目中的资源。
5. 正确 API Key 不能访问其他项目的 Run。
6. 正确 API Key 不能访问其他项目的 Node Run。
7. 正确 API Key 不能访问其他项目的 Timeline。
8. 正确 API Key 不能访问其他项目的 Approval。
9. 客户端修改 `project_id` 不能扩大授权范围。
10. 缺少项目作用域配置时不能回退到全局或默认项目。
11. 不存在的资源和越权资源的响应不会泄露不必要的存在性信息。
12. 响应体、异常和日志中不出现 API Key 或内部凭据。

如果某项现有测试需要更新，应先确认原断言是否与新的安全契约冲突，不要简单删除失败测试。

### 7.2 数据库集成测试

复用现有 DB fixture，至少验证：

* 授权项目中的真实 Run / Approval 可以读取。
* 跨项目访问被拒绝。
* Node Run 与 Timeline 不能绕过父 Run 的项目归属。
* 所有读取仍然只读。
* 数据库结构没有变化。
* 关键表测试前后行数一致。

不要向开发或生产数据库写入测试数据。

### 7.3 OpenAPI 验证

检查实际应用生成的 OpenAPI：

* 认证安全方案真实存在。
* 四个端点均声明需要认证。
* OpenAPI 声明与实际运行时行为一致。

OpenAPI 中显示了安全方案不代表认证已经正确实现，必须结合请求测试验证。

## 八、配置与部署限制

### 本阶段必须做到

* 新增配置有明确的说明。
* 不提供可用于生产的硬编码默认密钥。
* 不把真实 API Key 写入 `.env.example`、文档或测试报告。
* 在 README 或安全文档中说明配置方式。
* 测试环境可以使用测试专用凭据，但不能把它们当作生产凭据。

### 生产部署仍然保持 BLOCKED

当前审计没有找到可信网关、HTTPS 或实际网络边界的仓库证据。

因此，即使本阶段代码测试全部通过，最终仍须明确：

```text
Application authentication = PASS / FAIL
Project authorization = PASS / FAIL
HTTPS = NOT VERIFIED
Network boundary = NOT VERIFIED
PRODUCTION_DEPLOYMENT = BLOCKED
```

不要因为新增 API Key 就声称生产环境已经安全。

不要仅通过修改 `APP_HOST` 或添加一个未经验证的代理配置来宣称网络边界已得到保护。

如果后续采用网关终结 TLS，必须验证：

* 外部客户端无法绕过网关直接访问应用。
* 只有可信网关可以传递身份或项目作用域。
* 网关会覆盖而不是盲目信任客户端提供的身份头。
* 内部链路和凭据管理符合实际部署要求。

这些属于后续部署验证，不要在本阶段虚构验证结果。

## 九、允许修改的范围

允许：

* Workflow Read API 的认证和项目授权依赖。
* 必要的配置模型与配置读取。
* API 层少量修改。
* 相关 DTO / OpenAPI 声明的必要修改。
* 安全测试。
* 安全配置说明及审计报告更新。

禁止修改：

```text
Workflow Runtime 核心逻辑
Workflow Read Service 的业务语义
Store Adapters
数据库 Schema / Migration
Agent Runtime
其他无关业务 API
```

如果实现过程中发现必须修改上述核心组件才能完成授权，先报告原因和证据，不要扩大范围。

## 十、测试命令

先执行新增安全测试：

```powershell
python -m pytest -q tests/test_workflow_read_api_security_audit.py
```

注意：完成修复后，原先用于证明安全缺口存在的审计测试可能需要调整为验证缺口已修复。保留其威胁场景和测试覆盖，不要保留与新安全契约相矛盾的旧期望。

然后执行：

```powershell
python -m pytest -q tests/test_workflow_read_api.py
python -m pytest -q tests/test_workflow_read_api_db.py
python -m pytest -q tests/test_workflow_runtime_read_service.py
python -m pytest -q tests/test_workflow_runtime_read_service_db.py
python -m compileall backend tests
git diff --check
```

如果项目有统一 lint 命令，按现有配置运行。

如需执行全量测试：

```powershell
python -m pytest -q
```

不要因为已有测试较多而省略报告实际运行的测试范围。

## 十一、最终报告格式

完成后按以下格式汇报：

```text
【Phase 6 Step 7.2 — Workflow Read API 最小安全修复】

1. 认证实现
- 认证方式：
- 配置项：
- 未配置时的行为：
- 四个端点的保护情况：

2. 项目授权
- 授权作用域来源：
- 客户端 project_id 的处理：
- 跨项目访问测试：

3. 安全测试
- 未认证请求：
- 错误凭据：
- 项目越权：
- 资源枚举：
- 敏感信息泄漏：

4. OpenAPI
- 安全方案：
- 四个端点的声明：
- 与运行时行为是否一致：

5. 测试结果
- 新增安全测试：
- API 回归：
- Read Service 回归：
- DB 测试：
- compileall / lint：
- diff check：

6. 数据库
- Schema 是否变化：
- DB writes：
- Residue：

7. 新增文件
- ...

8. 修改文件
- ...

9. Git 状态
- 工作区是否保留：
- 是否 commit / push：

10. 部署状态
- Application authentication：
- Project authorization：
- HTTPS：
- Network boundary：
- PRODUCTION_DEPLOYMENT：

11. 剩余风险与限制
- ...
```

## 十二、完成后立即停止

只完成本阶段定义的最小安全修复。

不要进入 Step 7.3。

不要开发登录系统、JWT 签发服务、RBAC 后台、Workflow 写接口、Agent 或 MCP。

先实现、测试、报告；生产部署继续等待 HTTPS 与网络边界的独立验证。
