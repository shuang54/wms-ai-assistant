# Phase 6 Step 6 — S6 Workflow Read Layer

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

为现有 Workflow Runtime 建立独立、只读的查询层。

目标架构：

```text
Application / Future API
          ↓
WorkflowReadService
          ↓
Workflow Store Ports
          ├── WorkflowRunStore
          ├── WorkflowNodeRunStore
          └── WorkflowApprovalStore
          ↓
Existing Store Adapters
          ↓
Existing Repositories
          ↓
PostgreSQL
```

本阶段只负责读取、授权范围校验、DTO 投影和结果组装。

不得实现 HTTP API，不得修改 Workflow Runtime 的执行行为。

## 二、开始前必须完成的检查

### 1. 保护 S5 工作区

先执行：

```powershell
git status --short
git diff --check
git diff --stat
```

逐一检查 S5 的 tracked 修改、untracked 文件和测试证据。

S5 变更目前尚未提交：

* 不得 reset、clean、stash 或覆盖现有改动。
* 不得删除未跟踪的 Runtime 文件或测试。
* 按项目既有治理流程完成 S5 检查与必要的 checkpoint。
* S5 尚未解决的问题不得伪装成已验证。

### 2. 阅读真实接口

逐字检查：

```text
backend/app/workflow/
backend/app/workflow_runtime/ports.py
backend/app/workflow_runtime/store_adapters.py
backend/app/workflow_runtime/factory.py
backend/app/workflow_runtime/runtime.py
backend/app/db/models/
backend/app/db/workflow_*_repository.py
backend/app/db/session.py
tests/
docs/decisions/Phase/
```

确认以下真实字段和方法：

* `RunRecord`
* `NodeRunRecord`
* `ApprovalRecord`
* `WorkflowRunStore.get`
* `WorkflowNodeRunStore.list_for_run`
* `WorkflowNodeRunStore.get_latest_attempt`
* `WorkflowApprovalStore.get_for_node`
* 所有记录实际包含的时间戳、状态、错误码、节点标识和项目标识。

不要假设 DTO 字段、排序规则或 Repository 方法存在。

**如果现有 Ports 无法满足某项查询，不要擅自修改 Ports 或 Repository。先报告具体缺口、原因和最小修复方案。**

## 三、严格范围

允许：

* 新增 `backend/app/workflow_runtime/read_service.py`，或遵循项目现有命名约定。
* 新增只读 Query DTO。
* 新增只读查询服务。
* 新增 Fake Store 单元测试。
* 新增真实 PostgreSQL DB-gated 查询测试。
* 新增简短的 Read Layer 设计文档。

禁止：

* 新增 HTTP API、FastAPI Router 或 Read API。
* 修改 Workflow Runtime、NodeExecutor、AI Orchestrator 或 Agent Runtime。
* 修改 Tool Framework。
* 新增数据库表、迁移或 Checkpoint。
* 在查询过程中修改 Run、Attempt、Approval。
* 暴露 ORM 对象、Session、Connection。
* 在 Read DTO 中暴露 Workflow 启动输入 `input`。
* 暴露凭证、连接字符串、敏感 metadata 或内部异常正文。
* 为方便查询而绕过已有 Store Ports。

## 四、Read DTO 设计

先根据真实记录字段定义最小、稳定的只读 DTO。

建议支持以下视图，但具体字段必须来自真实模型。

### A. Workflow Run Detail

包含必要的：

* workflow_run_id
* project_id
* workflow_id / workflow_version
* status
* current_node_id
* next_action
* state_version
* 已有的节点数、Attempt 数等可证明统计信息
* 真实存在且有意义的创建、更新时间

不得包含：

* `input`
* ORM 实例
* Session / Connection
* 内部敏感 metadata
* 未经过安全投影的异常详情

### B. Node Run Detail

包含必要的：

* node_id
* 节点类型
* 当前节点状态
* Attempt 序号
* Attempt 状态
* 已持久化的安全输出
* error_code
* 真实存在的时间字段

如果同一节点有多个 Attempt，必须保留历史 Attempt，不得只返回最后一次而导致历史事实丢失。

不要自行重建未持久化的执行状态。

### C. Approval Detail

包含必要的：

* approval_id
* 所属 Run / Node 标识
* status
* 已持久化的决策信息
* 真实存在的创建、裁决时间

不得泄漏审批记录中的内部对象或未授权信息。

### D. Run Timeline

如果现有 Ports 能支持，提供一个按持久化事实构建的 Timeline。

要求：

* Run、Node Attempt、Approval 事件均来自真实持久化记录。
* 按实际时间字段排序；时间相同的情况使用稳定的次级排序规则。
* 不得把当前状态推断成历史事件。
* 不得伪造执行开始、执行完成或审批时间。
* 如果现有模型缺少时间字段，不要修改 Schema 来补造 Timeline；先报告限制。

**不实现全局 Run 列表。** 当前 Run Store 未必具有项目范围的列表查询能力。未经检查不得新增全表扫描或自行扩展 Repository。

## 五、项目隔离与访问控制

所有公开查询入口必须要求调用方明确提供授权范围所需的 `project_id`。

要求：

1. 读取 Run 后验证 `run.project_id` 与请求的 `project_id` 一致。
2. 不一致时 fail closed，不返回 Run 的其他字段。
3. 读取 Node Attempt 和 Approval 后，验证其项目标识与父 Run 一致。
4. 如果子记录不携带 `project_id`，通过已读取的父 Run 建立归属校验，不得虚构字段。
5. 未找到记录与项目不匹配应采用明确、安全的错误处理，不能泄漏其他项目是否存在该资源。
6. 不允许调用者通过任意 `run_id` 读取其他项目的数据。

本阶段只实现项目范围校验，不擅自引入新的身份认证系统。未来 HTTP API 的身份认证和授权仍由后续阶段负责。

## 六、查询语义

实现最小只读查询能力，例如：

```python
get_run_detail(...)
get_node_runs(...)
get_approval_detail(...)
get_run_timeline(...)
```

以上仅为能力示例，不是要求照抄的最终签名。

必须基于现有 DTO、Port 和项目风格确定实际签名。

要求：

* 只通过 Store Ports 读取。
* 不直接调用 ORM / Repository。
* 不调用 Runtime 的 `advance()`、`start()` 或 `request_cancel()`。
* 不调用 Tool、LLM 或 Orchestrator。
* 不隐式触发恢复或状态推进。
* 查询失败不能改变数据库状态。
* 对不存在的记录使用明确的项目异常，不返回虚构的默认对象。

## 七、数据投影与安全

所有 Read DTO 都必须显式构造。

禁止：

```python
return orm_object
return model.__dict__
return dict(orm_object)
```

输出投影必须避免：

* `input`
* API Key / password
* database URL / connection string
* Authorization Header
* SQLAlchemy Session / Connection
* 未经筛选的内部 metadata
* 内部异常正文或堆栈

对节点输出，沿用现有 JSON-safe 边界和已持久化结果。不要在 Read 层截断或自动修复损坏数据。

如果发现数据库记录违反现有契约，必须明确报错或安全失败，不得伪装成正常结果。

## 八、测试要求

### A. 单元测试

新增：

```text
tests/test_workflow_runtime_read_service.py
```

至少覆盖：

* 正常读取 Run。
* 正常读取 Node Attempt 历史。
* 正常读取 Approval。
* Timeline 的稳定排序。
* Run 不存在。
* Project 不匹配。
* 子记录归属不一致。
* `input` 不出现在 Read DTO。
* ORM / Session 不泄漏。
* 敏感 metadata 不泄漏。
* 查询不会调用 Runtime 执行方法。
* 查询不会调用 Tool / LLM。
* 查询失败不会写入状态。
* 损坏输出 fail closed。
* 空结果与多条记录的行为。
* 不支持的查询能力明确失败，不静默返回假数据。

使用 Fake Store，不调用真实 LLM 或 Tool。

### B. DB-gated 集成测试

沿用：

```powershell
$env:RUN_DB_TESTS="1"
```

执行前确认连接的是专用测试数据库。

使用真实 PostgreSQL、真实 Repository、真实 Store Adapter 和 Read Service，验证：

1. 创建 Run 后可以读取。
2. 多个 Node Attempt 按正确顺序读取。
3. Approval 状态与裁决时间读取正确。
4. 跨 Project 查询被拒绝。
5. 查询前后 Run / Node Attempt / Approval 状态不变。
6. 清理后 DB residue 为零。

不得把 Fake Store 测试当成真实 DB 集成证据。

如果现有数据模型无法验证某项能力，先报告缺口，不得为了测试而修改生产 Schema。

## 九、架构边界测试

新增 Read Service 的 AST / Import 边界测试，遵循项目既有约定。

Read Service 可以依赖：

* Workflow Domain 中的必要 DTO / 类型
* `workflow_runtime.ports`
* 本阶段新增的只读 DTO

不得依赖：

* FastAPI / API Router
* AI Orchestrator
* Tool Handler
* LLM Provider
* Agent 内部 Repository
* ORM Session / Connection
* Runtime 执行入口

Read Service 只能通过 Store Ports 获取数据。

如果必须依赖 Factory 或 Adapters 才能完成只读查询，先检查架构是否发生了反向依赖，不要直接放宽白名单。

## 十、验证命令

新增测试：

```powershell
python -m pytest -q tests/test_workflow_runtime_read_service.py
```

Workflow Runtime 离线回归：

```powershell
python -m pytest -q tests/test_workflow_runtime_runtime.py tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_runtime_adapters.py tests/test_workflow_runtime_factory.py tests/test_workflow_runtime_read_service.py
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

再按项目现有机制运行新增 DB-gated 测试及相关 Persistence 回归。

准确报告 passed、skipped、failed，不得把未执行的测试写成 PASS。

## 十一、完成报告

完成后汇报：

```text
【Phase 6 Step 6 — S6 Read Layer Report】

1. S5 工作区 / checkpoint 核查
2. 新增文件
3. 修改文件
4. Read DTO
5. Run / Node / Approval 查询
6. Timeline 支持情况
7. Project Isolation
8. 敏感信息与 input 隔离
9. 离线测试结果
10. DB-gated 测试结果
11. DB residue
12. Compile / lint / AST boundary
13. API 是否修改
14. 未验证项与限制
15. Git 状态

S6 完成后立即停止，不进入后续 API 阶段。
```

最重要的规则：

```text
检查 S5
→ 阅读真实接口
→ 最小 Read DTO
→ Read Service
→ 单元测试
→ DB-gated 验证
→ 报告
→ STOP
```

如果现有 Ports 无法支持安全、完整的只读查询，停止并报告，不要擅自扩大范围。
