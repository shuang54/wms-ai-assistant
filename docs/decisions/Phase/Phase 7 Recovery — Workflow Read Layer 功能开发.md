
# Phase 7 Recovery — Workflow Read Layer 功能开发

项目：`D:\coding\ai\wms-ai-assistant`

## 一、开发目标

停止重复进行纯验证工作，开始交付实际功能。

本轮目标：实现 Workflow Runtime 的只读查询服务，为后续工作流详情、节点执行历史、审批状态和执行时间线提供稳定的数据读取能力。

预期调用链：

```text
Application
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

本阶段实现 Read DTO、Read Service 和测试。

**不开发 HTTP API，不修改 Runtime 执行行为，不进入下一阶段。**

## 二、先做最小检查，然后立即开发

先检查：

```powershell
git status --short
git diff --check
git diff --stat
```

随后阅读：

```text
AGENTS.md
docs/architecture.md
docs/database.md
docs/decisions/Phase/
backend/app/workflow/
backend/app/workflow_runtime/
backend/app/db/models/
backend/app/db/workflow_*_repository.py
tests/
```

重点确认：

- 当前 Workflow Runtime 实际提供哪些 Store Ports。
- Run、Node Attempt、Approval 的真实字段和查询方法。
- 当前是否已经存在等价 Read Service。
- 当前路线图中 S6 Read Layer 的状态和剩余范围。
- 当前工作区已有的未提交修改和未跟踪文件。

检查的目的只是确定实现边界，不是再生成一份长篇审计报告。

**完成必要检查后，在同一任务中继续编码，不要停下来等待我再次批准。**

如果 Read Layer 已经实现部分功能，只补齐真实缺口，不重复造轮子。

如果关键 Store Port 不支持安全查询，则停止相关功能并说明具体缺口；不得擅自修改 Repository、Port 或数据库 Schema。

## 三、本轮必须交付的功能

### 1. Workflow Run Detail

通过现有 Store Port 读取指定 Run，并显式投影为只读 DTO。

仅包含真实存在的字段，例如：

- workflow_run_id
- project_id
- workflow_id / workflow_version
- status
- current_node_id
- next_action
- state_version
- 创建、更新时间
- 能够可靠计算的节点数和 Attempt 数

不得返回 Workflow 启动输入 `input`、ORM 对象、Session 或 Connection。

### 2. Node Attempt History

提供指定 Run 的节点执行历史查询。

要求：

- 保留同一节点的全部历史 Attempt。
- 使用真实持久化字段排序。
- 不自行推断未持久化的执行状态。
- 安全投影节点输出和错误信息。
- 对损坏的持久化输出明确报错或安全失败，不自动修复。

### 3. Approval Detail

如果现有 Ports 支持，读取指定 Node 的审批记录，包括真实存在的：

- approval_id
- 所属 Run / Node
- status
- 已持久化的决策信息
- 创建时间、裁决时间

不得伪造审批记录或推断未保存的审批时间。

### 4. Run Timeline

只有在现有数据模型足以支持时才实现。

Timeline 必须由真实持久化记录组成，至少考虑：

- Run
- Node Attempt
- Approval

使用实际时间字段排序；时间相同则采用稳定的次级排序规则。

如果缺少必要时间字段，明确记录限制，不修改 Schema 补造时间。

**不实现全局 Run 列表，不做无界全表扫描。**

## 四、项目隔离和数据安全

所有查询入口必须要求调用方提供 `project_id`。

必须执行：

1. 读取 Run 后校验 `run.project_id`。
2. 项目不匹配时 fail closed，不返回 Run 的其他字段。
3. 查询 Node Attempt 和 Approval 时验证其与父 Run 的归属关系。
4. 子记录没有 `project_id` 时，通过父 Run 校验归属，不得虚构字段。
5. 不存在的记录和无权限访问采用明确、安全的异常语义。
6. 不允许仅凭 `run_id` 读取其他项目的数据。

所有 DTO 必须显式构造，禁止：

```python
return orm_object
return model.__dict__
return dict(orm_object)
```

不得泄露：

- `input`
- API Key、密码、连接字符串
- 未筛选的敏感 metadata
- 内部异常正文和堆栈
- ORM、Session、Connection

本阶段不新建身份认证系统，也不开发 HTTP API。

## 五、测试要求

新增或扩展：

```text
tests/test_workflow_runtime_read_service.py
```

优先复用现有 Fake Store 和测试 fixture。

至少覆盖：

- 正常读取 Run。
- Node 多 Attempt 历史完整保留。
- Approval 查询。
- Timeline 稳定排序（如果支持）。
- Run 不存在。
- Project 不匹配。
- 子记录归属不一致。
- `input` 和敏感 metadata 不泄露。
- ORM / Session / Connection 不泄露。
- 查询不调用 Runtime 的 `start()`、`advance()`、`request_cancel()`。
- 查询不调用 LLM、Tool 或 Orchestrator。
- 查询失败不改变持久化状态。
- 损坏输出 fail closed。
- 空结果、多条结果和不支持的查询能力。

新增 AST / Import 边界测试，确保 Read Service 只通过 Store Ports 读取，不直接依赖 ORM、Repository、FastAPI Router、LLM Provider 或 Runtime 执行入口。

### DB 集成测试

沿用项目现有的 DB-gated 测试机制：

```powershell
$env:RUN_DB_TESTS="1"
```

只有在确认连接的是专用测试数据库后，才执行真实 PostgreSQL 集成测试。

验证真实 Repository、Store Adapter 和 Read Service 的集成，以及查询前后状态不变。

不得连接生产数据库，不得引入业务写操作，不得新增迁移。

如果环境无法安全执行 DB 测试，明确标记为未执行，不得虚报通过。

## 六、验证命令

先执行新增测试：

```powershell
python -m pytest -q tests/test_workflow_runtime_read_service.py
```

再执行相关 Runtime 回归：

```powershell
python -m pytest -q tests/test_workflow_runtime_runtime.py tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_runtime_adapters.py tests/test_workflow_runtime_factory.py tests/test_workflow_runtime_read_service.py
```

最后：

```powershell
python -m compileall -q backend tests scripts
git diff --check
```

根据当前项目实际情况执行 DB-gated 测试。

不要为了通过测试而修改断言、放宽授权或改变既有业务行为。

## 七、Git 和阶段边界

- 保留现有工作区修改和未跟踪文件。
- 不执行 `reset`、`clean`、`stash`。
- 不修改冻结基线和治理门。
- 不执行 `git add`、`commit`、`push`。
- 不处理与本功能无关的文档治理事项。
- 不修改 M2.4 已提交内容。
- 不进入后续 API 阶段。

## 八、完成报告

只汇报：

1. 实际新增和修改的文件。
2. 本轮实现的 Read DTO。
3. Run、Node Attempt、Approval 查询完成情况。
4. Timeline 支持情况和限制。
5. Project Isolation 与敏感数据隔离。
6. 新增测试数量及结果。
7. Runtime 回归结果。
8. DB-gated 测试结果及是否实际执行。
9. Compile / AST 边界 / diff check。
10. 当前 Git 状态。
11. 尚未实现的具体功能及原因。

完成后立即 STOP。

**本轮的成功标准是交付可运行、经过测试的 Read Service，而不是再输出一份架构分析报告。**
