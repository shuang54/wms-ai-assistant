# Phase 6 Step 3 — Cancellation Concurrency Closure

基线：`26bc707`

当前已裁决：

* D1：批准 `workflow_run.input` nullable TEXT，按 ADR §8.2 对齐。
* D2：批准 UNKNOWN fail-closed；resume 不自动重跑，也不自动把 Run 置为 FAILED。
* OQ-1～OQ-13：按已批准的实现口径冻结。
* D3：取消原则已批准，但原判定树中“所有非终态 Run 均可直接终态化”的规则尚未批准。

## 本轮唯一目标

补齐 Workflow Runtime 的取消并发与崩溃恢复设计，使其能够明确区分：

1. 有活跃执行者的 RUNNING Run；
2. 执行者崩溃后遗留的 RUNNING Run；
3. 已收到取消信号但尚未安全收尾的 Run；
4. 取消与 resume、审批裁决、节点结果同时发生的竞争场景。

## 强制约束

* 只读审计与设计，不修改任何文件。
* 不修改 Domain、ORM、Repository、Runtime、ADR。
* 不提交、不 push、不 stash、不 rebase。
* 不新增 Agent、MCP、LangGraph、调度器或其他高级架构。
* 不假设外部 Tool 的副作用可以撤销。
* 不把“数据库状态为 RUNNING”当作活跃 worker 存在的证明。
* 不把 CAS 失败当作一定存在其他活跃执行者的证明。

## 必须检查

1. 当前已有字段、Repository CAS 能力和 Runtime 计划，是否足以识别活跃执行者。
2. 如果不足，提出最小安全方案，并明确是否需要新增字段、lease/claim 或新的持久化语义；不得擅自实现。
3. 明确取消信号由谁消费、何时将 Run 置为 CANCELLED、执行者崩溃后如何显式收尾。
4. 明确取消与 resume 并发时，如何防止两个执行者同时推进同一 Run。
5. 明确取消与在途 Tool 完成时的结果保留、Run 终态保护和外部副作用语义。
6. 明确 UNKNOWN attempt 是否允许任何自动恢复；答案必须保持不自动重跑。
7. 如果现有架构无法同时满足安全取消、崩溃恢复和不新增机制，请明确报告取舍，不要伪造“完全安全”。

## 输出格式

【Phase 6 Step 3 — Cancellation Closure Report】

1. 当前可用的并发保护机制
2. 活跃执行者能否被可靠识别
3. cancel / resume / node completion 的竞争矩阵
4. 崩溃后取消收尾方案
5. 是否需要最小 schema / ADR 变更
6. 推荐方案及其限制
7. 是否满足开始 S1 编码的条件

最后停止，等待确认。
