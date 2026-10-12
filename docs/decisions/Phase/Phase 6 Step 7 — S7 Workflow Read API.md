# Phase 6 Step 7 — S7 Workflow Read API

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

基于现有 Workflow Read Layer，新增最小只读 HTTP API。

目标架构：

```text
HTTP Client
    ↓
Existing Authentication / Authorization
    ↓
Workflow Read API
    ↓
WorkflowReadService
    ↓
Workflow Store Ports
    ↓
Store Adapters
    ↓
Repositories
    ↓
PostgreSQL
```

本阶段只完成：

* HTTP 请求和响应 DTO
* 只读查询端点
* 项目范围授权
* 统一错误映射
* API 层测试
* 最小必要的集成测试

不修改 Workflow Runtime 的执行行为。

## 二、开始编码前：检查工作区和 S6

先执行：

```powershell
git status --short
git diff --check
git diff --stat
```

注意：

S5、S6 的改动可能仍处于未提交状态，包含 tracked 修改和 untracked 文件。

必须：

1. 识别全部 S5、S6 文件。
2. 检查当前 HEAD 和工作区状态。
3. 阅读 S5、S6 的完成报告及相关测试。
4. 确认 `read_service.py` 和相关测试没有被遗漏。
5. 保留所有现有修改。

禁止：

* `git reset --hard`
* `git clean`
* 未经授权的覆盖、删除或清理
* 为了让测试通过而重写 S5、S6
* 未经明确授权擅自 commit 或 push

如果发现工作区内容与此前报告不一致，先列出差异并停止危险操作。

## 三、先检查现有 API 与认证体系

阅读：

```text
backend/app/api/
backend/app/
backend/app/workflow_runtime/read_service.py
backend/app/workflow_runtime/ports.py
backend/app/workflow_runtime/store_adapters.py
backend/app/workflow_runtime/factory.py
tests/
docs/architecture.md
docs/decisions/
```

重点确认：

1. 当前 FastAPI 应用入口。
2. Router 的注册方式。
3. 现有认证和授权依赖。
4. 当前项目范围 `project_id` 如何确定。
5. 现有 API 的错误响应规范。
6. 现有依赖注入、Service Factory 和测试客户端。
7. WorkflowReadService 的真实方法签名及异常类型。
8. 当前项目的 API 测试如何隔离数据库。

**不得假设项目已经具备身份认证或项目授权能力。**

### 安全阻断规则

如果现有系统没有可信的身份认证与项目授权机制：

* 不得将客户端传入的 `project_id` 直接当作已授权范围。
* 不得通过简单增加一个 `project_id` 查询参数就宣称完成授权。
* 不得为了推进进度而自行引入一套新的认证系统。
* 先停止生产 API 实现，报告缺失的安全依赖和最小解决方案。

可以继续编写不依赖真实身份的 API 单元测试，但不得把未授权的端点作为可部署的安全 API 交付。

## 四、严格范围

### 允许

* 新增 Workflow Read API Router。
* 新增必要的请求和响应 DTO。
* 新增依赖注入装配。
* 使用现有认证与授权依赖。
* 新增 API 单元测试和 DB-gated 集成测试。
* 按现有项目惯例注册 Router。
* 新增简短的 API 设计文档。

### 禁止

不修改：

```text
workflow_runtime/runtime.py
workflow_runtime/execution.py
workflow_runtime/binding.py
workflow_runtime/ports.py
workflow_runtime/store_adapters.py
workflow_runtime/factory.py
workflow_runtime/read_service.py
```

如果发现上述模块存在真实缺陷，先报告，不得擅自扩展修复范围。

同时禁止：

* 新增数据库表或迁移。
* 新增 Worker、Scheduler 或队列。
* 新增 Agent、MCP、LangGraph。
* 新增 Workflow 创建、启动、恢复、取消接口。
* 新增审批通过或拒绝接口。
* 新增全局 Run 列表。
* 绕过 Read Service 直接访问 Repository。
* 暴露 ORM、Session、Connection 或内部 metadata。

## 五、API 端点设计

根据项目现有路由规范实现以下只读能力。

建议路径如下，若现有 API 规范不同，以现有规范为准。

### 1. 查询 Run 详情

```http
GET /api/workflow-runs/{run_id}
```

调用：

```python
WorkflowReadService.get_run_detail(...)
```

返回经过显式投影的 RunDetail。

不得返回：

* `input`
* `request_id`
* ORM 对象
* 数据库连接信息
* 内部异常正文
* 未经筛选的 metadata

### 2. 查询 Run 的 Node Attempt 历史

```http
GET /api/workflow-runs/{run_id}/nodes
```

调用现有 `get_node_runs(...)`。

必须保留所有已持久化 Attempt 历史，不能只返回每个节点最后一次执行记录。

不得自行推断不存在的状态或时间。

### 3. 查询 Approval 详情

```http
GET /api/workflow-approvals/{approval_id}
```

调用现有 `get_approval_detail(...)`。

必须验证当前授权项目与 Approval 所属 Run 的项目一致。

不得泄漏其他项目的 Approval 是否存在。

### 4. 查询 Run Timeline

```http
GET /api/workflow-runs/{run_id}/timeline
```

调用现有 `get_run_timeline(...)`。

保留 Read Service 的稳定排序规则，不得在 API 层伪造事件或时间戳。

如果当前 Read Service 不支持某项查询，不得直接查询数据库绕过它。

## 六、项目授权和资源隔离

所有端点必须使用现有可信授权上下文获取 `project_id`。

授权流程：

```text
HTTP Request
    ↓
Authenticate Caller
    ↓
Resolve Authorized Project Scope
    ↓
WorkflowReadService
    ↓
Verify Resource Ownership
    ↓
Return Project-Scoped Result
```

要求：

1. 不信任客户端单独提交的 `project_id`。
2. 资源查询必须绑定已授权项目。
3. Run 不存在和 Run 不属于当前项目时，映射为相同的外部错误语义。
4. Approval 不存在和 Approval 属于其他项目时，也采用相同的外部错误语义。
5. 不向客户端返回内部 Repository、ORM 或数据库错误。
6. 未认证和无权访问的错误遵循项目现有规范。
7. 不得把项目授权失败映射为成功的空结果。

如果系统当前使用的身份模型不能表达项目范围，应先报告，不得虚构权限。

## 七、响应 DTO 与错误处理

所有 API 响应都必须显式序列化。

禁止：

```python
return orm_object
return model.__dict__
return dict(orm_object)
```

要求：

* 只返回 API 合约中明确允许的字段。
* 不暴露 Workflow 输入。
* 不暴露凭证、数据库 URL、Session 或 Connection。
* 不返回异常堆栈或数据库异常正文。
* 不把未知异常转换为 HTTP 200。
* 不把损坏的数据伪装成正常响应。
* 复用现有错误响应规范，不另建一套重复机制。

S6 已知限制：

* ApprovalRecord 缺少 `created_at`，不能伪造审批创建时间。
* Read Service 为同步接口，API 层应遵循项目现有的同步/异步适配模式，避免在异步请求处理中直接阻塞事件循环。
* 不支持全局 Run 列表。

这些限制不应通过修改 S6 核心模块来规避。

## 八、测试要求

### A. API 单元测试

新增独立测试文件，例如：

```text
tests/test_workflow_read_api.py
```

至少覆盖：

1. Run 详情正常响应。
2. Node Attempt 历史完整返回。
3. Approval 详情正常响应。
4. Timeline 稳定返回。
5. Run 不存在。
6. Approval 不存在。
7. 未认证请求。
8. 跨项目访问被拒绝。
9. Run 输入 `input` 不出现在响应中。
10. 凭证和数据库 URL 不泄漏。
11. Read Service 异常得到安全映射。
12. 无法访问的资源不会返回 HTTP 200。
13. API 不调用 Runtime 的 `start`、`advance`、`request_cancel`。
14. API 不调用 Tool、LLM 或 Orchestrator 执行路径。
15. 不存在的端点不会意外创建或修改 Workflow 状态。

使用现有测试客户端与 Fake Service/Store，避免真实 LLM 调用。

### B. DB-gated 集成测试

沿用项目当前机制：

```powershell
$env:RUN_DB_TESTS="1"
```

执行前必须确认数据库是专用测试库。

通过真实：

```text
HTTP Client
→ Router
→ Authorization Dependency
→ WorkflowReadService
→ Store Adapter
→ Repository
→ PostgreSQL
```

验证：

* Run 详情可读。
* Node Attempt 历史可读。
* Approval 详情可读。
* Timeline 可读。
* 跨项目请求被拒绝。
* 查询前后状态一致。
* 测试清理后数据库无残留。

不得用 Fake Store 测试代替真实 DB 集成测试。

## 九、架构边界

API 层可以依赖：

* FastAPI
* 现有认证与授权依赖
* WorkflowReadService
* API 响应 DTO
* 项目既有依赖注入装配

API 层不得直接依赖：

* SQLAlchemy ORM 模型
* SQLAlchemy Session / Connection
* Workflow Repository
* Tool Handler
* LLM Provider
* Agent Runtime 内部实现

API 层只能通过 WorkflowReadService 读取 Workflow 数据。

WorkflowReadService 不得反向依赖 FastAPI。

## 十、验证命令

先执行新增 API 测试：

```powershell
python -m pytest -q tests/test_workflow_read_api.py
```

再执行 S6 与 Runtime 相关离线回归，按仓库实际测试文件调整：

```powershell
python -m pytest -q tests/test_workflow_runtime_runtime.py tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_runtime_adapters.py tests/test_workflow_runtime_factory.py tests/test_workflow_runtime_read_service.py tests/test_workflow_read_api.py
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

继续运行项目现有的 lint、AST 边界测试和相关 DB-gated 回归。

准确报告：

* passed
* skipped
* failed
* 未执行项

不得把未执行的测试写成 PASS。

## 十一、完成报告

完成后严格报告：

```text
【Phase 6 Step 7 — S7 Workflow Read API Report】

1. S5 / S6 工作区检查
2. 新增文件
3. 修改文件
4. API 路由
5. 身份认证与项目授权
6. Run / Node / Approval / Timeline
7. 响应 DTO 与敏感字段隔离
8. API 单元测试
9. DB-gated 集成测试
10. Runtime / S6 回归
11. Compile / lint / AST boundary
12. DB residue
13. API 合约是否变化
14. 未验证项与限制
15. Git 状态

S7 完成后立即停止。
不得自动进入 Workflow 写操作 API、Agent 或 MCP 阶段。
```

## 十二、实施顺序

```text
检查 S5 / S6 工作区
        ↓
审查现有认证与授权体系
        ↓
检查 ReadService 合约
        ↓
确定最小 HTTP 合约
        ↓
实现 Router / DTO / DI
        ↓
API 单元测试
        ↓
真实 PostgreSQL 集成测试
        ↓
全量回归
        ↓
报告
        ↓
STOP
```

如果当前缺少安全的项目授权基础，停止生产 API 实现并报告阻塞原因，不得为了完成阶段而交付不安全的接口。
s