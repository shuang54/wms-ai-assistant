# Phase 6 Step 7.1 — Workflow Read API Security Audit

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

对 Phase 6 Step 7 已实现的 Workflow Read API 做一次基于真实代码的安全审计。

本阶段只做：

* 阅读代码与配置
* 核实现有认证和授权边界
* 检查项目级数据隔离
* 检查错误信息和敏感数据暴露
* 编写必要的安全审计测试
* 输出风险清单和部署结论

**本阶段默认不修改生产逻辑，不实现新的认证系统。**

最终回答：

1. 未认证用户能否读取 Workflow Run 和 Approval？
2. 修改 `project_id` 能否访问其他项目的数据？
3. 是否存在默认项目或默认作用域导致的越权？
4. 是否存在可信网关、反向代理或网络边界保护？
5. 当前 API 是否可以部署到生产环境？

## 二、保护现有工作区

首先执行：

```powershell
git status --short
git diff --check
git diff --stat
```

当前工作区包含之前阶段尚未提交的 S5、S6、S7 工作。

必须：

* 保留全部现有修改和未跟踪文件
* 不执行 `git reset`
* 不执行 `git clean`
* 不执行 `git stash`
* 不覆盖已有文件
* 不提交或推送
* 不删除旧测试、文档或 baseline

完成后再次检查 Git 状态。

## 三、阅读真实代码

优先阅读：

```text
backend/app/main.py
backend/app/api/workflow_read.py
backend/app/dto/workflow_read_api.py
backend/app/workflow_runtime/read_factory.py
backend/app/workflow_runtime/read_service.py
```

同时检查：

```text
backend/app/api/
backend/app/config/
backend/app/
tests/
docs/architecture.md
docs/decisions/
```

如果某个目录不存在，记录实际项目结构，不要假定它存在。

搜索：

```text
Depends(
Security(
HTTPBearer
APIKeyHeader
OAuth2
get_current_user
current_user
project_id
authentication
authorization
middleware
trusted proxy
```

核实运行时实际生效的控制，不要只根据 OpenAPI 文档、注释或 README 判断。

## 四、认证审计

检查四个 Workflow Read API：

```text
GET /api/workflow-runs/{run_id}
GET /api/workflow-runs/{run_id}/nodes
GET /api/workflow-runs/{run_id}/timeline
GET /api/workflow-approvals/{approval_id}
```

逐个确认：

1. 是否要求身份认证？
2. 未提供认证信息时，真实响应是什么？
3. 是否依赖全局 FastAPI dependency 或 middleware？
4. 是否存在应用外部的认证保护？如果有，提供实际配置或部署证据。
5. OpenAPI security scheme 是否与真实运行时保护一致？

不要因为 Swagger 中出现 Security Scheme 就认定 API 已受保护。

如果无法启动应用或执行 HTTP 测试，明确标记为 `NOT VERIFIED`，不要编造响应结果。

## 五、项目级授权审计

检查：

* `project_id` 来自 URL、query、header、token 还是可信服务端上下文？
* Run 和 Approval 是否关联明确的项目？
* 查询是否始终限定在已授权项目范围内？
* 用户能否通过修改 `project_id` 查询其他项目？
* 是否存在默认 project、缺省 project 或空 project 的回退逻辑？
* 是否存在通过 Run ID 或 Approval ID 枚举对象的风险？

重点区分：

**项目筛选条件不等于项目授权。**

如果接口只接收客户端传入的 `project_id`，但没有经过身份验证并核验该身份对项目的访问权限，应明确报告该缺口。

## 六、网关和部署边界

检查仓库中是否存在实际部署配置，例如：

```text
Dockerfile
docker-compose.yml
compose.yaml
nginx.conf
deployment manifests
reverse-proxy configuration
```

只检查实际存在的文件。

确认：

* API 是否监听所有网络接口
* 是否有可信网关或反向代理
* 后端能否被绕过网关直接访问
* 是否有真实网络隔离配置
* 是否信任客户端可任意设置的身份或项目 header
* 是否有相关集成测试或部署验证

不能仅凭“计划通过网关认证”就认定已经受到保护。

如果没有证据，标记为 `NOT VERIFIED`。

## 七、敏感数据和错误响应

检查响应 DTO、异常处理和日志，确认：

* 是否返回内部 input 或 request_id
* 是否暴露数据库连接、SQLAlchemy Session 或 Connection
* 是否返回堆栈、数据库异常或内部路径
* 是否记录 Authorization、Token、Cookie 等凭证
* Run ID / Approval ID 不存在时，错误响应是否泄露内部信息
* 跨项目对象与不存在对象是否可能形成不必要的信息枚举差异

不要把所有字段都直接视为敏感数据；结合实际数据流和业务用途判断。

## 八、测试要求

优先复用现有测试：

```text
tests/test_workflow_read_api.py
tests/test_workflow_read_api_db.py
tests/test_workflow_runtime_read_service.py
tests/test_workflow_runtime_read_service_db.py
```

先检查实际文件名是否一致。

按现有测试基础设施增加最少量测试，重点覆盖：

1. 未认证请求是否能读取资源
2. 项目 A 的身份是否能读取项目 B 的 Run
3. 项目 A 的身份是否能读取项目 B 的 Approval
4. 修改客户端 `project_id` 是否会改变授权范围
5. 默认项目逻辑是否可能扩大访问范围
6. 不存在的 ID 是否泄露内部异常
7. 现有 Read API DTO 与响应契约是否保持
8. 现有项目隔离回归测试是否继续通过

如果项目当前没有真正的认证机制：

* 不要创建 Fake Auth 并将它当成生产安全证据
* 不要添加形同虚设的测试依赖
* 可以增加明确暴露缺口的审计测试或审计报告
* 不要为了让测试通过而把风险标记为 PASS

测试应区分：

```text
PASS
FAIL
NOT VERIFIED
NOT APPLICABLE
```

## 九、严格禁止

本阶段不做：

* 新增 JWT/OAuth 登录系统
* 新增用户数据库或角色数据库
* 新增 workflow 写 API
* 修改 Workflow Runtime 的执行行为
* 修改 Agent Runtime
* 接入 MCP、LangGraph 或 Temporal
* 修改现有 Run/Approval 数据模型
* 修改已有业务结果或测试数据
* 通过关闭测试或放宽断言消除安全问题
* 把未验证的网关配置写成已生效的保护措施

如果发现必须修改生产代码才能补上认证缺口，先记录具体缺口和最小修复方案，等待独立的实施阶段。

## 十、回归测试

先运行相关离线测试：

```powershell
python -m pytest -q tests/test_workflow_read_api.py tests/test_workflow_runtime_read_service.py
```

如果文件名不同，使用实际文件名。

如果项目支持 DB-gated 测试，再按项目实际机制运行。PowerShell 示例：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_read_api_db.py tests/test_workflow_runtime_read_service_db.py
```

不要使用 Unix Shell 风格的环境变量赋值。

最后执行：

```powershell
python -m compileall backend tests
git diff --check
git status --short
git diff --stat
```

不要宣称执行了实际未运行的测试。

## 十一、审计报告

新增一份独立报告：

```text
docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md
```

如果目录不存在，可以创建。

报告至少包含：

### 1. Scope

本次审计覆盖哪些代码、接口和部署配置。

### 2. Authentication

逐个接口列出认证状态和证据。

### 3. Project Authorization

说明项目隔离是否依赖可信身份，以及跨项目访问的验证结果。

### 4. Threat Model

用表格列出：

```text
风险
代码证据
复现方法或验证方式
影响
状态
最小缓解方案
```

### 5. Sensitive Data

列出响应、异常和日志的检查结果。

### 6. Deployment Boundary

列出已确认的保护和无法确认的保护。

### 7. Tests

记录真实运行的命令、通过数、失败数、跳过数和未验证项。

### 8. Git Changes

列出新增、修改文件以及 `git diff --check` 结果。

### 9. Deployment Gate

结论只能根据实际证据给出：

* `PASS`：认证、授权和部署边界均已验证
* `BLOCKED`：存在明确的未认证访问或项目授权缺口
* `NOT VERIFIED`：关键保护无法确认

如果未认证请求能够读取敏感工作流数据，或者没有可验证的项目级授权，必须标记：

`PRODUCTION_DEPLOYMENT = BLOCKED`

不能因为现有回归测试通过就将安全审计判定为 PASS。

### 10. Minimum Next Step

只提出修复当前确认问题所需的最小实施阶段，不扩展到其他功能。

## 十二、停止条件

完成审计和报告后立即停止。

不实施新的认证系统。

不新增 Workflow 写接口。

不进入 Agent/MCP 开发。

等待审计报告确认后，再决定是否安排独立的安全修复阶段。
