# Phase 6 Step 3 — Final Decision Freeze & S1 Preparation

基线：`26bc707`

## 已批准决策

* D1：`workflow_run.input` 使用 nullable TEXT 持久化 JSON-safe 输入，对齐 ADR §8.2。
* D2：UNKNOWN fail-closed；不自动重跑，不自动置为 FAILED。
* OQ-1～OQ-13：采用已确认的实现口径。
* D3：批准 R-1 / R-2 / R-3，但必须落实下述约束。

## 取消协议冻结

1. cancel 首先持久化取消信号。
2. 对非终态 Run 尝试通过最新 `state_version` CAS 收尾。
3. CAS 失败后重新读取并分类；不得盲目覆盖、无限重试或假设存在活跃执行者。
4. 返回值必须区分：

   * `CANCELLED`：已确认终结；
   * `CANCEL_REQUESTED`：取消意图已持久化，尚未确认终结；
   * `NOT_CANCELLABLE`：Run 已终态或不符合取消条件。
5. 在途节点不能被假定为已物理中断。若 Run 已终态，迟到的节点结果不得推进 Run。
6. `cancel_agent_run()` 只能按既有协作式取消接口调用，不得假设其撤销已发生的副作用。
7. 所有 Run 状态写入均通过 CAS；终态不可逆。

## 并发 resume 与 attempt 约束

1. 创建节点 attempt 的 `RUNNING` 记录必须发生在调用外部执行器之前。
2. 同一 `(workflow_run_id, node_id, attempt)` 由唯一约束保证只创建一次。
3. 如果竞争的 resume 发现同一节点已有未决 `RUNNING` attempt，必须按 UNKNOWN fail-closed；不得再次调用 LLM、Agent 或 Tool。
4. 不新增 claim/lease 字段；但必须通过测试证明 attempt 创建竞争不会导致重复执行。
5. 对尚未创建 attempt 的并发入口，必须证明唯一约束与执行顺序能够阻止重复执行；若不能证明，先报告，不要自行添加机制。

## 本轮执行范围

开始 S1：实现 `backend/app/workflow_runtime/ports.py`。

要求：

* 只新增 ports 层 Protocol 与必要 DTO。
* 包含持久化 `input` 所需的 Repository 端口参数与读取结果。
* 明确取消返回语义及 CAS 失败语义。
* 不修改 Domain、ORM、Repository、Runtime、ADR。
* 不实现 binding、execution、runtime、factory。
* 不调用真实 LLM，不连接真实数据库。
* 不提交、不 push、不 stash、不 rebase。

开始编码前，先检查当前仓库基线与工作区。若基线不一致或存在不明代码变更，立即停止并报告。

完成后运行与 ports 相关的最小测试、编译检查和 Domain boundary 检查。只报告真实执行结果，不推断未运行测试通过。

输出：
【Phase 6 Step 3 — S1 Ports Report】

1. 新增文件
2. Protocol / DTO 列表
3. input 持久化接口
4. 取消与 CAS 语义
5. 并发 attempt 约束
6. 测试结果
7. 修改文件
8. Git 状态

完成 S1 后立即停止，等待下一步指令。不得自动开始 S2。
