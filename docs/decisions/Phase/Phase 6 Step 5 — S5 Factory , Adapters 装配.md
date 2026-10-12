# Phase 6 Step 5 — S5 Factory / Adapters 装配

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

把已完成的 Workflow Domain、Persistence、Ports、Binding、Execution、Runtime 组装成可由应用层调用的 Workflow Runtime 实例。

目标链路：

```text
Application Composition Root
          ↓
WorkflowRuntimeFactory
          ↓
WorkflowRuntime
          ├── WorkflowRunStore
          ├── WorkflowNodeRunStore
          ├── WorkflowApprovalStore
          ├── NodeExecutor
          ├── OrchestratorPort Adapter
          └── ToolExecutionPort Adapter
```

本阶段只完成装配、真实适配器接线和集成验证。

**不要开发 Workflow API、Read API、Worker 或 Scheduler。**

## 二、编码前检查

先阅读真实代码，不要假设接口：

```text
backend/app/workflow/
backend/app/workflow_runtime/
backend/app/db/
backend/app/services/
backend/app/api/
backend/app/main.py
tests/
```

重点检查：

1. Workflow ORM / Repository 的真实构造参数。
2. Repository 使用同步还是异步 Session。
3. `WorkflowRuntime`、`NodeExecutor` 的完整构造函数。
4. 当前应用创建数据库 Session 的方式和生命周期。
5. 当前 `AIOrchestratorService` 的构造方式、调用签名和返回结构。
6. 当前 `ToolExecutionService` 的构造方式、执行签名和权限上下文。
7. 当前项目是否已有 composition root / dependency provider / factory 约定。
8. `asyncio.to_thread` 是否与现有 Session、事务及 Repository 的线程安全约束兼容。
9. Workflow Registry / Definition 如何注入和解析。

先给出真实接口与依赖关系清单，再编码。

## 三、严格范围

允许：

* 新增 Workflow Runtime Factory。
* 新增实现现有 Ports 的最小 Adapters。
* 在现有依赖装配位置接入 Factory，但必须先确认不会改变现有 API 行为。
* 新增 Factory / Adapter 单元测试。
* 新增真实 Repository 的 DB-gated 集成测试。
* 新增简短的装配文档。

禁止：

* 修改 Workflow Domain 状态机。
* 修改 AI Orchestrator 核心行为。
* 修改 Agent Runtime。
* 修改 Tool Framework 核心实现。
* 新增 API / Read API。
* 新增 Worker / Scheduler / Lease / Heartbeat。
* 新增 Workflow Checkpoint 表。
* 修改既有数据库 Schema，除非发现已确认的阻塞问题并先报告。
* 引入新的依赖注入框架。
* 为通过测试而修改既有业务逻辑。

如果现有 Port 不足以完成真实装配，先报告接口缺口和最小修复建议，停止编码，等待确认。

## 四、Factory 设计

根据现有项目结构选择最小、显式的工厂接口。

建议概念接口：

```python
create_workflow_runtime(...)
```

参数必须基于真实依赖确定，不要照抄示例后猜签名。

Factory 负责组装：

* WorkflowRunStore
* WorkflowNodeRunStore
* WorkflowApprovalStore
* OrchestratorPort Adapter
* ToolExecutionPort Adapter
* NodeExecutor
* WorkflowRuntime

Factory 不负责：

* 启动 Run。
* 推进 Run。
* 修改状态机。
* 自动恢复 UNKNOWN。
* 创建全局共享的请求级 Session。
* 隐式创建新的数据库 Engine。
* 隐式调用外部 LLM。

## 五、数据库 Session 与线程边界

S4 使用 `asyncio.to_thread` 调用同步 Port。本阶段必须检查真实数据库 Session 的生命周期和线程约束。

要求：

1. 不得将同一个非线程安全 Session 随意交给多个线程并发使用。
2. 不得在一个线程创建 Session，再在另一个线程中违反驱动约束地使用它。
3. 不得让单个 Runtime 实例意外持有跨请求共享的 Session。
4. 明确 Session 是每次操作、每个 Runtime 调用还是其他生命周期。
5. Repository 每次操作是否独立提交，必须以真实实现为准。
6. 不能因为 Fake 测试通过就认定真实线程与事务边界安全。

若现有 Repository / Session 设计无法满足要求，先停止并报告，不得自行重写 Persistence。

## 六、Orchestrator Adapter

通过现有 `OrchestratorPort` 对接真实 `AIOrchestratorService`。

要求：

* 严格遵循已有 Port 签名。
* 不直接访问 Agent 内部 Repository 或 ORM。
* 不泄漏 API Key、数据库 URL、Session、连接对象。
* 不新增递归 Workflow 调用。
* 不擅自改变 Agent 的审批、恢复或取消语义。
* 清晰区分执行失败、等待审批与成功结果。
* 如果真实返回结构无法无损映射到现有 Port，报告字段差异，不得猜测状态值。

## 七、Tool Execution Adapter

通过现有 `ToolExecutionPort` 对接真实 `ToolExecutionService`。

要求：

* 不直接调用 Tool Handler。
* 不绕过现有权限校验。
* 不自动扩大调用者权限。
* 传递真实所需的 Project / Request / Tool 调用上下文。
* Tool 失败不得被转换成成功。
* 不增加自动重试。
* 不把内部异常正文写入持久化结果。

若当前 ToolExecutionService 的授权上下文与 Workflow Runtime 不兼容，先报告最小差异，不得绕过授权。

## 八、Definition / Registry 装配

检查现有 Workflow Registry 和 Definition 的真实实现。

要求：

* Factory 使用项目现有 Registry / Definition 机制。
* 不复制第二套 Registry。
* 明确 current version 与 exact version 的解析路径。
* 不允许项目 A 的 Workflow Definition 被项目 B 错误使用。
* 不在 Factory 中硬编码 `vietnam-wms`。
* 不允许未知 Workflow 被静默替换成默认 Workflow。

若 Definition Registry 尚未形成可用于 Runtime 的稳定接口，先报告。

## 九、测试要求

### A. Factory 单元测试

使用 Fake 依赖验证：

* 所有必需 Port 都被正确装配。
* `WorkflowRuntime` 获得同一组预期依赖。
* 不创建真实 Engine / Session。
* 不调用真实 LLM / Tool。
* 不发生模块导入循环。
* 不引入新的 DI 框架。

### B. Adapter 契约测试

分别验证：

* Orchestrator 成功、失败、等待审批映射。
* Tool 成功、失败映射。
* Project / Request / Tool 上下文正确传播。
* 非法或未知状态 fail closed。
* 敏感信息不会进入结果 DTO。

### C. DB-gated 集成测试

沿用项目现有 `RUN_DB_TESTS=1` 机制。

使用真实 PostgreSQL、真实 Workflow Repository、真实 Factory / Adapter 可替换边界，验证：

1. 创建 Run 后可从 Repository 读取。
2. Run 的 CAS 竞争只有一个成功。
3. 相同 `(run,node,attempt)` 并发创建只有一个成功。
4. 父子 `project_id` 一致性。
5. Run / Attempt / Approval 读写与状态版本一致。
6. 运行完成后没有测试数据残留。

本阶段不得调用真实 DeepSeek。真实 Tool 测试只允许使用项目已有的安全、只读测试 Tool；若不存在，使用 Fake Adapter 并明确标注真实 Tool 集成尚未验证。

## 十、Runtime 并发风险的最终验收

S4 报告中明确指出，Fake 并发测试不能替代真实 PostgreSQL 的原子性验证。

S5 必须验证真实 Repository 的：

* `update_cas` 版本条件。
* Attempt 唯一约束。
* 事务提交与失败行为。

此外，必须验证 Runtime 的并发 `advance`、Approval 恢复和取消不会因为装配后的真实 Repository 行为而重复调用外部能力。

如果测试发现真实 Runtime 的单赢保证失效：

**停止并报告根因，不要通过修改测试或 Fake 掩盖。**

如果现有 Repository API 无法安全完成验证，先报告，不要自行修改 Repository。

## 十一、边界测试

运行已有 Workflow Runtime boundary 测试，并检查新增模块的 AST import：

允许依赖：

* Workflow Domain
* `workflow_runtime.ports`
* `workflow_runtime.binding`
* `workflow_runtime.execution`
* `workflow_runtime.runtime`
* 当前项目已有的明确基础设施适配器

禁止 Domain 或纯 Binding 反向依赖：

* ORM / Repository
* FastAPI / API
* AI Orchestrator
* Tool Handler
* LLM Provider

不允许循环导入。

## 十二、执行命令

先执行新增测试：

```powershell
python -m pytest -q tests/test_workflow_runtime_factory.py
```

实际文件名可按项目既有约定调整。

再执行 Workflow Runtime 离线回归：

```powershell
python -m pytest -q tests/test_workflow_runtime_runtime.py tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_domain_boundary.py
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

DB 集成：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q <新增的 DB-gated 测试文件>
```

执行前必须确认环境指向专用测试数据库，不得在生产数据库上执行测试。

## 十三、最终报告

完成后汇报：

```text
【Phase 6 Step 5 — S5 Factory / Adapters Report】

1. 编码前确认的真实接口
2. 新增文件
3. 修改文件
4. Factory 装配结构
5. Orchestrator Adapter
6. Tool Adapter
7. Session / 线程安全审查
8. Workflow Registry / Project Isolation
9. 离线测试
10. DB-gated 测试
11. Runtime 并发集成结果
12. 编译 / lint / boundary
13. DB residue
14. 未验证项和限制
15. API 是否修改
16. Git 状态

S5 完成后立即停止，不进入 S6。
```

最重要的原则：

```text
先读真实接口
→ 确认 Session / 事务边界
→ Factory
→ Adapters
→ 离线测试
→ PostgreSQL 集成验证
→ 报告
→ STOP
```

如果真实 Port 或 Repository 不足以安全装配，先报告接口缺口，不允许为了推进进度而绕过安全边界。
