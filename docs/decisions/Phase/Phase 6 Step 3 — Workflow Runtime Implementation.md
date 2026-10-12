# Phase 6 Step 3 — Workflow Runtime Implementation

现在开始实现 `D:\coding\ai\wms-ai-assistant` 的 Phase 6 Step 3：Workflow Runtime。

## 一、第一轮只做实现前审计与计划

先阅读并以真实代码为准：

* `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md`
* `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Discovery.md`
* `backend/app/workflow/`
* `backend/app/db/models/workflow_run.py`
* `backend/app/db/models/workflow_node_run.py`
* `backend/app/db/models/workflow_approval.py`
* 三个 Workflow Repository
* `backend/app/db/init_db.py`
* 现有 `AIOrchestratorService`
* 现有 `ToolExecutionService`
* 相关 Agent Runtime 的入口、状态转换、CAS 和审批实现
* Workflow Domain、Persistence 及治理测试

先核对当前 Git 状态和提交链，不得覆盖现有工作。

## 二、Runtime 必须实现的职责

以 ADR 为准，设计并实现独立的 Workflow Runtime，负责：

1. 根据 Registry 精确解析 Workflow Definition 和版本。
2. 创建并推进 WorkflowRun。
3. 按 DAG 拓扑及 CONDITION 结果决定下一节点。
4. 持久化每次 Node Attempt，保留历史尝试记录。
5. 使用 Repository CAS 防止并发推进覆盖。
6. 按节点类型分发执行：

   * TOOL → 现有 ToolExecutionService
   * AI → 现有 AIOrchestratorService
   * CONDITION → Domain 定义的条件语义
   * APPROVAL → Workflow 自身的 Approval 状态机
7. 正确处理失败、取消和审批等待状态。
8. 所有 Run 与 Node 状态变化都必须遵守 Domain 状态转换规则。
9. Runtime 不得绕过 Repository 直接修改数据库。
10. 提供清晰、稳定的 Runtime 返回值和错误语义，具体形式遵守 ADR。

## 三、先解决风险，再编码

实现前明确回答：

* `update_cas` 没有状态转换校验时，Runtime 如何保证终态不可逆？
* `request_id` 的重复提交、并发提交和幂等范围是什么？严格按 ADR 实现，不自行扩大承诺。
* `approval.resolve` 返回 `None` 时，Runtime 如何读取当前状态并区分重复审批、冲突和记录不存在？
* Runtime 如何处理进程中断、并发执行与取消请求？
* Node Attempt 失败后，重试策略是否在 ADR 允许的范围内？不得增加自主重规划或 Agent 式循环。
* AI 节点调用 Agent Runtime 后，如何防止 Workflow 与 Agent 之间发生递归调用？

如果 ADR 对任一必要行为没有明确规定，先报告决策缺口，不得悄悄引入新语义。

## 四、严格范围

允许：

* 新增 Workflow Runtime 实现
* 新增必要的 Runtime 测试
* 必要时新增少量内部辅助代码
* 按需新增 Runtime 文档

禁止：

* 修改已经冻结的 Domain 契约，除非发现明确冲突并先报告
* 修改 Agent Runtime 核心行为
* 修改 Router、Orchestrator、Tool Framework 核心实现
* 新增 `workflow_checkpoint` 表
* 新增 PARALLEL、LOOP、WAIT 或 SUB_WORKFLOW 执行能力
* 引入 Temporal、LangGraph、MCP 或新调度框架
* 修改生产数据库
* 删除或重写已有测试基线
* push、merge、rebase

## 五、测试要求

测试必须覆盖：

* Workflow Registry 版本解析
* Run 创建和首次推进
* DAG 顺序执行与 CONDITION 分支
* TOOL 节点调用现有 ToolExecutionService
* AI 节点调用现有 AIOrchestratorService
* APPROVAL 节点等待、批准、拒绝与重复处理
* CAS 竞争与重复推进
* Node Attempt 历史保留
* 失败传播与终态不可逆
* 取消请求与协作取消
* 进程中断后的可恢复性
* 项目作用域隔离
* Workflow 与 Agent Runtime 边界
* 无隐式 Tool → Text-to-SQL 或 Agent → Workflow 递归路径

优先使用已有 Fake 和测试基础设施。真实数据库测试使用项目现有 `RUN_DB_TESTS=1` 机制；默认不得调用真实 LLM。

## 六、第一轮交付

本轮先提交：

`【Phase 6 Step 3 Runtime — Implementation Plan】`

内容必须包括：

1. 当前代码与接口核对结果
2. ADR 中对应的 Runtime 约束
3. 计划新增/修改文件
4. Runtime 组件边界与调用流程
5. 状态机和 CAS 设计
6. 幂等、审批、失败、取消与恢复策略
7. 测试矩阵
8. 分步实施顺序
9. 未决 ADR 问题与风险

**第一轮只审计、设计和输出计划，不修改任何代码，不提交 Git，不开始下一阶段。**
