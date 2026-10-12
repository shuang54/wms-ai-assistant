# Phase 6 Step 3 — S3 Node Execution

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

新增：

* `backend/app/workflow_runtime/execution.py`
* `tests/test_workflow_runtime_execution.py`

仅实现单节点执行器。开始前必须阅读真实的 Domain Definition、S1 ports 和 S2 binding，禁止猜测字段名、枚举值或方法签名。

## 二、节点执行范围

### 1. CONDITION

* 复用 `binding.evaluate_condition()`。
* 不复制条件求值逻辑。
* 不访问数据库或外部服务。
* 返回可由 Runtime 消费的明确执行结果。

### 2. TOOL

* 通过 `ToolExecutionPort` 调用。
* 使用现有参数映射逻辑生成 arguments。
* 构造完整的 `ToolCallContext`，包括 request_id、round、project_id、tool_call_id。
* 不直接导入或调用 Tool Registry、Tool Handler。
* 不在执行器内部自动重试。
* 参数映射失败时不得调用 Tool。
* 明确区分参数错误、Tool 执行错误与输出不符合 JSON-safe 边界。

### 3. AI

* 通过 `OrchestratorPort` 调用。
* 严格使用 ports 中已有的方法签名。
* 按已冻结的 OQ-4 投影必要结果。
* Agent 进入 WAITING 时，保留恢复所需的 agent_run_id / agent_approval_id。
* 不自动批准 Agent，不自动递归执行，不增加多步 Tool Calling。
* 不暴露敏感 metadata、数据库对象或内部 Session。

### 4. APPROVAL

* 创建待审批记录。
* 返回明确的 WAITING_APPROVAL 结果。
* 不自动批准或拒绝。
* 不在本层修改 Workflow Run 状态。
* 审批记录已存在、创建冲突或参数非法时，必须返回明确的可处理错误，不得静默创建重复记录。

## 三、执行结果

优先复用 S1 已有 DTO。只有在真实接口无法表达必要结果时才提出扩展方案；未经确认不得修改 ports.py。

结果必须明确表达：

* 成功完成
* 等待 Workflow Approval
* 等待嵌套 Agent
* 执行失败

所有成功输出必须符合 Domain 的 JSON-safe 约束。失败不得伪造成功输出。

## 四、安全与并发边界

* 本层只执行一个节点，不决定下一个节点。
* 不负责 Run 的 CAS、状态推进、重试、取消或恢复。
* 不把节点执行失败自动转换为可重试错误。
* 不对 UNKNOWN attempt 发起第二次执行。
* 不允许执行器自行决定 attempt 编号。
* 不修改 Agent Runtime 或 Workflow Domain。

## 五、测试要求

全部使用 Fake Port，离线确定性测试。

至少覆盖：

* 四种节点分别成功
* CONDITION 为 true / false
* TOOL 参数映射失败时不调用 Tool
* TOOL 只调用一次，参数与上下文正确
* AI 普通结果投影
* AI WAITING 恢复信息
* APPROVAL 创建待审批记录
* 各类执行错误
* JSON-safe 输出边界
* 不访问数据库、真实 LLM 或真实 Tool
* import boundary 不越界

若真实 Domain 定义与上述描述冲突，停止并报告，不要擅自修改 Domain。

## 六、验收

执行：

`python -m pytest -q tests/test_workflow_runtime_execution.py`

`python -m pytest -q tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_domain_boundary.py`

`python -m compileall -q backend tests scripts`

报告实际执行结果，并说明未运行的测试。

完成后输出：

【Phase 6 Step 3 — S3 Execution Report】

1. 新增文件
2. 四种节点执行行为
3. 执行结果 DTO
4. 错误处理
5. 测试结果
6. Import boundary
7. Git 状态
8. 未解决问题

完成 S3 后立即停止。不得开始 S4 Runtime。
