执行 Phase 6 Step 3 — Runtime Decision Closure Audit。

本轮只做只读审计和决策方案，不修改代码、不提交 Git。

### 目标

关闭以下两个阻塞项：

1. Workflow Run 输入及上下文的持久化与恢复。
2. 取消请求与活动节点执行之间的并发语义。

### 检查内容

1. 阅读已接受的 Phase 6 ADR、Discovery、Step 1 Domain 和 Step 2 Persistence 实现。
2. 逐条追踪 `start(input)` 的输入在首次执行、等待审批、进程中断、resume、Retry 时的流向。
3. 明确当前数据库是否能在进程重启后重建所需输入；如果不能，提出最小方案及其 ADR 影响。
4. 画出 RUNNING、WAITING、取消信号、活动节点完成、进程崩溃之间的状态竞争关系。
5. 确定取消入口在何种条件下可以直接终态化，以及活动执行者如何防止晚到的结果推进已终态化的 Run。
6. 检查 UNKNOWN fail-closed 与 Retry 白名单是否存在矛盾。

### 严格限制

* 不修改 Domain、ORM、Repository、Runtime 或 ADR。
* 不新增数据库字段或表。
* 不 commit、push、merge、rebase。
* 不开始 S1 编码。
* 不改变已批准的架构决策。

### 最终报告

输出：

【Phase 6 Step 3 Runtime — Decision Closure Report】

1. 输入持久化现状与缺口
2. 最小修复方案及其 ADR 影响
3. 取消并发状态图
4. UNKNOWN 与 Retry 的一致性检查
5. 需要用户批准的决策
6. 是否具备开始 S1 编码的条件

完成后停止，等待裁决。
