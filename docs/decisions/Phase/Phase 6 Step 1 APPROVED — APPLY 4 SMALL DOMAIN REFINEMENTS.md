【Phase 6 Step 1 APPROVED — APPLY 4 SMALL DOMAIN REFINEMENTS】

批准当前 Step 1 Implementation Plan，可以开始编码。

但编码前只允许对原计划做以下 4 项修正，其余设计保持不变：

1. WorkflowDefinition 图模型

明确 Phase 6 当前只允许 DAG：

* 禁止环
* 禁止 back-edge
* 禁止 loop node
* 禁止隐式循环边
* retry 不属于 graph edge
* retry 只通过同一 node_id 的新 attempt 表达

例如：

A → B → C

B retry：

B attempt=1
B attempt=2
B attempt=3

不得表示成：

A → B → B → B → C

2. NodeRunOutcome output boundary

不要让 Domain 层出现无约束的 Any output。

新增一个轻量的 NodeOutput / JsonValue 边界，确保：

* 可序列化
* 可持久化
* 不携带 DB Session / Connection
* 不携带 ORM Entity
* 不携带 Python 任意对象
* 不允许无限制 object graph

如果现有项目已有稳定 JSON value 类型/工具，只允许复用，不新增重复抽象。

3. WorkflowApprovalRecord

保持 DTO/fact 语义：

WorkflowApprovalRecord = approval state representation

Runtime 才是状态转换 authority。

不要在 DTO 中实现：

approve()
reject()
cancel()

Domain 只定义：

PENDING
APPROVED
REJECTED

以及必要的合法状态语义。

实际 CAS / 单赢 / persistence transition 留给 Step 2/3。

4. WorkflowRegistry version API

不要只设计：

get(name)

至少提供明确的两个语义：

resolve_current(name)
resolve(name, version)

新建 WorkflowRun：

resolve_current(name)

Resume：

resolve(name, persisted_definition_version)

禁止 Resume 隐式获取 current version。

其余原 Step 1 Plan 全部保持不变。

特别保持：

* workflow 与 agent 0 import
* workflow 与 db 0 import
* workflow 与 services 0 import
* workflow 与 tools 0 import
* workflow 与 projects 0 import
* WorkflowRun ≠ AgentRun
* WorkflowNodeRun ≠ AgentStep
* retry owner = Workflow
* Parallel = Deferred
* Scheduler = Deferred
* Write Tool = BLOCK
* 无 checkpoint
* 无 event store
* 无 API
* 无 ORM
* 无 DB
* 无 migration
* 无现有文件修改

执行顺序：

1. 修改 Step 1 Plan 中上述 4 项设计
2. 开始实现 7 个 Domain 源文件
3. 实现 4 个测试文件
4. 每完成一个小模块立即运行对应测试
5. 最后运行全部 Step 1 Exit Gates：

python -m pytest -q tests/test_workflow_*.py

python -m pytest -q

python -m compileall -q backend tests scripts

git diff --check

6. 检查：

git diff --stat

git status --short

确认：

* 只有 11 个新增文件
* 无任何既有文件修改
* workflow → agent = 0 import
* agent → workflow = 0 import
* workflow → db/services/tools/projects/api = 0 import

完成后只输出：

【Phase 6 Step 1 COMPLETE】

并报告：

1. 新增文件
2. Domain Model
3. State Machine
4. Registry Version Semantics
5. Output Boundary
6. Test Results
7. Boundary Audit
8. Existing File Modifications
9. Frozen Boundary Check
10. Step 2 Readiness

不要进入 Step 2。

STOP。
