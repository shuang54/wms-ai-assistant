# Phase 6 Step 4 — S4 Workflow Runtime

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

基于已完成的 Workflow Domain、Persistence、S1 Ports、S2 Binding、S3 Execution，实现最小可验证的 Workflow Runtime。

本阶段只实现 Run 生命周期、节点调度、持久化状态推进、审批恢复、取消和失败恢复边界。

## 1. 开始前检查

先阅读真实代码，不猜接口：

* `backend/app/workflow/`
* `backend/app/workflow_runtime/ports.py`
* `backend/app/workflow_runtime/binding.py`
* `backend/app/workflow_runtime/execution.py`
* 当前 Workflow Repository / ORM / migration
* 对应测试和 Phase 6 ADR

先确认所有真实方法签名、状态枚举、错误码、Attempt 唯一约束和数据库事务行为。

不得修改已冻结的 Domain 规则。发现接口冲突先报告。

## 2. 实现范围

新增：

* `backend/app/workflow_runtime/runtime.py`
* `tests/test_workflow_runtime_runtime.py`

仅在真实实现需要且获得明确依据时增加最小辅助文件。

实现以下能力：

1. 启动新的 Workflow Run。
2. 验证 Workflow Definition 和输入。
3. 持久化 Run 初始状态、输入及首个节点。
4. 按 DAG 顺序调度节点。
5. 通过 `NodeExecutor` 执行节点。
6. 持久化 Node Attempt 和执行结果。
7. 处理 CONDITION 分支。
8. 处理 Workflow Approval 等待与恢复。
9. 处理嵌套 Agent 的等待与恢复，但不递归执行 Workflow。
10. 处理失败、终态和取消。
11. 严格使用已有 Ports，不直接调用 ORM、Registry、Tool Handler 或 Agent 内部实现。

不要把 Run 状态机逻辑塞进 `NodeExecutor`。

## 3. 并发安全——最高优先级

先分析并发 Resume 的竞态：

两个执行者可能同时读取同一 Run，计算出不同的 Attempt 编号，并同时调用外部服务。

不得仅依赖 `(workflow_run_id, node_id, attempt)` 唯一约束。

在调用 `NodeExecutor` 前，必须设计并验证一个可证明的执行权竞争机制。优先使用现有 Run CAS 与 Attempt 创建顺序，必要时先提出最小 Port / Repository 扩展方案，不得假定现有接口天然安全。

要求：

* 失败的竞争者不得调用外部 Tool / AI。
* 相同节点不能被两个并发 Resume 重复执行。
* CAS 失败后必须重新读取并分类。
* 不允许无限重试 CAS。
* 已存在未决 `RUNNING` Attempt 时，按 UNKNOWN 处理，不自动重新执行。
* 如果当前 schema / Port 无法提供可靠保证，停止并报告缺口，不要用测试 Fake 掩盖。

增加确定性并发测试，使用 Barrier / Event 控制竞态，不依赖随机 sleep。

## 4. 状态持久化

每次状态变更都使用 CAS 或条件更新。

必须保证：

* Run 状态和 `state_version` 一致。
* Node Attempt 在调用外部执行器前先创建。
* 终态不可被覆盖。
* 子记录 project_id 与父 Run 一致。
* 节点结果安全序列化。
* 失败结果记录正确的错误码。
* 状态推进失败不能伪装为成功。

明确处理外部调用成功但结果写入失败的情况：不得自动重跑外部调用；报告并遵循 UNKNOWN / 人工处理边界。

## 5. 节点调度

CONDITION：

* 读取 S3 输出 `"true"` / `"false"`。
* 只走对应 `on_true` / `on_false`。
* 不允许静默跳过无效目标。
* 服从 Domain DAG 校验结果。

TOOL：

* 只能通过现有 `ToolExecutionPort`。
* 不自动重试。

AI：

* 只能通过现有 `OrchestratorPort`。
* 不调用 Agent 内部服务。
* 不递归启动 Workflow。
* `WAITING_APPROVAL` 必须保留可恢复的 Agent Run / Approval 身份。

APPROVAL：

* 等待期间 Run 进入正确的 WAITING 状态。
* 审批裁决与 Run 恢复是不同操作。
* 同一终态审批重复提交必须幂等；相反决策必须冲突。
* 未获批准不得继续后续节点。

## 6. Retry

只对 ADR / Domain 明确允许的错误重试。

* TOOL 默认不重试。
* UNKNOWN 不重试。
* 重试前先持久化上一 Attempt 的 FAILED 结果。
* Attempt 编号不能由两个并发执行者各自无协调地生成。
* 不修改现有 RetryableClassifier 契约，除非先报告必要原因。

## 7. Cancellation

严格执行已批准的取消语义：

* `CANCELLED` 表示已确认终态。
* `CANCEL_REQUESTED` 只表示取消意图已持久化。
* `NOT_CANCELLABLE` 表示已终态或不符合取消条件。
* CAS 失败后读取并分类，不盲目覆盖。
* 不能声称物理中断所有外部调用。
* 已开始的外部副作用不能被假定已撤销。
* 允许 Run 已取消但在途 Attempt 尚未完成；Read 层需要能够解释这种状态。

## 8. UNKNOWN / 恢复

* RUNNING Attempt 没有可证明的最终结果时，禁止自动重新执行。
* 不增加恢复 Worker、Scheduler、Lease 或 Heartbeat。
* 不自动把 UNKNOWN 改成 FAILED。
* 通过明确错误码报告无法安全恢复的状态。

## 9. 测试要求

使用 Fake Ports 和 Fake NodeExecutor，覆盖：

* Run 启动成功与输入校验失败。
* 正常 DAG 顺序执行。
* CONDITION true / false 分支。
* 成功、失败、终态。
* Approval 等待、批准、拒绝、重复裁决。
* AI 等待与恢复。
* 合法重试与禁止重试。
* 取消及并发取消。
* CAS 冲突与重新读取分类。
* UNKNOWN Attempt 不重跑。
* 并发 Resume 不重复调用外部能力。
* 外部执行成功但持久化失败。
* project_id 传播。
* 终态不可覆盖。
* 不访问真实 DB、真实 LLM 或真实 Tool。

Fake 测试不能代替真实 Repository / PostgreSQL 对 CAS 与唯一约束的集成验证。若并发安全依赖数据库原子性，明确标注需要后续 DB-gated 测试验证。

## 10. 边界

禁止：

* 修改 AI Orchestrator 核心行为。
* 修改 Agent Runtime 状态机。
* 修改 Tool Framework。
* 新增 MCP / LangGraph / Scheduler / Worker。
* 新增 Workflow Checkpoint 表。
* 自动恢复 UNKNOWN。
* 在本阶段实现 API 或 Read 层。
* 未经确认修改 Ports / Domain / Schema。

## 11. 验证

运行：

```powershell
python -m pytest -q tests/test_workflow_runtime_runtime.py
python -m pytest -q tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_domain_boundary.py
python -m compileall -q backend tests scripts
```

如果需要修改 Ports / Repository 才能保证并发安全，先停止并提交设计报告，不要自行扩大范围。

完成后报告新增/修改文件、状态推进、并发保证、测试结果、未验证的 DB 行为、已知限制。

**S4 完成后立即停止，不开始 S5。**
