Phase 3.12 Step 90：Regression Execution Baseline 冻结

本步骤只做：

冻结 Step 89 的 Matrix 实际执行结果为一个独立的 Phase 3.12 Matrix Execution Baseline。

不要修改已有 Step 84 Offline Snapshot。

一、核心目标

当前已有两个层次：

Step 84/85
Offline Execution Snapshot
    ↓
375 / 356 / 19 / 0 / 0

以及：

Step 89
Matrix Execution Summary
    ↓
Offline + DB
    ↓
555 PASS

本步骤建立第三层：

Phase 3.12
Matrix Execution Baseline

结构：

Registration Matrix
        ↓
Actual Execution
        ↓
MatrixExecutionSummary
        ↓
Frozen Matrix Execution Baseline
二、严格范围

允许修改：

tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md

禁止：

backend/

生产代码 diff 必须：

empty

禁止新增：

新的 test framework
新的 runner
新的 regression collector
新的 Matrix
新的 API
新的数据库表

禁止修改：

FILES
CATEGORIES
EXPECTED_MATRIX_SCALE
NODE_HOSTED_CONTRACT_CATEGORIES
NODE_HOSTED_REPRESENTATIVE_NODES
OFFLINE_EXECUTION_SUMMARY_CONTRACT
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT

特别注意：

Step 84 的 Offline Snapshot 必须保持原值。

三、Baseline 与 Snapshot 必须分离

这是本步骤最重要的边界。

Step 84：

OFFLINE_EXECUTION_SUMMARY_SNAPSHOT

继续保持：

total = 375
passed = 356
skipped = 19
failed = 0
errors = 0
exit_code = 0
status = PASS

不要修改。

本步骤新增的是：

MATRIX_EXECUTION_BASELINE

它描述：

Offline + DB + Matrix

二者不是同一个 Contract。

四、Baseline DTO

优先检查当前是否已经存在等价 immutable DTO。

如果没有，新增最小 DTO：

MatrixExecutionBaseline

建议字段：

offline: RegressionExecutionSummary
db: RegressionExecutionSummary
matrix_total: int
matrix_status: str
db_residue: int

如果当前项目更适合：

db_residue_by_table

可以使用现有简单结构。

不要创建：

MatrixExecutionRecord
MatrixExecutionSnapshot
RegressionBaselineRecord

等重复 DTO。

只允许一个新的 Baseline DTO。

五、Immutable

必须：

frozen=True

或者复用项目已有 immutable DTO 模式。

测试：

baseline.offline = ...

必须失败。

baseline.db = ...

必须失败。

六、Matrix Total

严格定义：

matrix_total
=
offline.total
+
db.total

当前：

375 + 180 = 555

不要使用：

passed

计算 Matrix Total。

即：

Matrix Scale
    ≠
Matrix Execution Total
七、Matrix Status

定义：

PASS

当：

offline.status == PASS
AND
db.status == PASS

否则：

FAIL

不要重新创建第三套 status classifier。

优先复用 Step 84 已有：

RegressionExecutionSummary.status
八、DB Residue Contract

Baseline 中记录：

db_residue

当前必须：

0

但：

不要在 DTO 中保存数据库连接、Session 或 Query。

只保存最终整数/简单 immutable 数据。

九、Baseline Drift

复用已有：

classify_snapshot_drift()

但是：

不要修改它的原有 4 种 Drift：
NO_DRIFT
COUNT_DRIFT
EXIT_CODE_DRIFT
STATUS_DRIFT

如果现有 classifier 只能处理：

RegressionExecutionSummary

则：

不要强行扩展成 Matrix Baseline 专用复杂 classifier。

可以在 Step 90 增加一个非常小的：

compare_matrix_execution_baseline()

只比较：

offline
db
matrix_total
matrix_status
db_residue

并返回简单 drift 类型。

如果已经存在等价比较逻辑，直接复用。

十、Frozen Baseline 内容

新增一个明确的：

MATRIX_EXECUTION_BASELINE

固定当前真实结果：

Offline:
total = 375
passed = 356
skipped = 19
failed = 0
errors = 0
exit_code = 0
status = PASS

DB:
total = 180
passed = 180
skipped = 0
failed = 0
errors = 0
exit_code = 0
status = PASS

Matrix:
total = 555
status = PASS

DB residue:
0

不要把：

duration

放入 Baseline Contract。

duration 继续：

informational
compare=False
十一、禁止伪造

Baseline 必须来源于：

Step 89 实际执行结果

不能：

手工构造 PASS

测试必须证明：

MATRIX_EXECUTION_BASELINE
        ==
Step 89 actual execution summary
十二、测试

只增加必要测试。

建议：

1. baseline immutable

2. baseline matches Step 89 actual execution

3. matrix_total == offline.total + db.total

4. matrix_status derives from offline/db status

5. db_residue == 0

6. baseline does not modify Offline Snapshot

7. baseline does not modify Matrix Scale

8. baseline does not include Node-hosted execution

9. baseline does not contain duration

10. synthetic execution drift is detected

11. baseline comparison is deterministic

12. no DB/network/LLM dependency in baseline logic

目标：

10~15 tests

不要为了数量堆测试。

十三、Node-hosted Boundary

必须继续保持：

OFFLINE_EXECUTION_CONTRACT

不进入：

MatrixExecutionBaseline.offline
MatrixExecutionBaseline.db
MatrixExecutionBaseline.matrix_total

Node-hosted 只存在：

Contract Audit

不要把：

1 category
2 representative nodes

加入：

555
十四、Matrix Scale Boundary

继续保持：

EXPECTED_MATRIX_SCALE =

categories = 13
registered_files = 28
offline_files = 18
db_files = 15

不能改成：

14 categories

因为 Node-hosted category 不属于 execution matrix。

也不能把：

375 / 180 / 555

写入：

EXPECTED_MATRIX_SCALE

Scale 是：

registration topology

Baseline 是：

execution result

两者严格分离。

十五、Step 84 Offline Snapshot Boundary

必须增加测试：

MATRIX_EXECUTION_BASELINE
不会修改
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT

确保：

375 / 356 / 19

仍保持原值。

十六、Documentation

继续使用：

docs/evaluation/phase-3.12-trace-timeline-regression.md

新增：

§7.10 Matrix Execution Baseline

说明：

Matrix Execution Baseline
=
Offline Execution
+
DB Execution

当前：

Offline = 375 / 356 / 19 / 0 / 0
DB      = 180 / 180 / 0 / 0 / 0
Matrix  = 555 PASS
DB residue = 0

明确：

Step 84 Offline Snapshot 不变
Matrix Scale 不变
Node-hosted 不计入 execution
duration 不属于 baseline contract
十七、不要再次真实执行完整 Matrix

本步骤主要是：

Baseline Freeze

不要重新运行：

18 offline files
15 DB files

除非测试确实必须读取 Step 89 已存在的 execution helper。

优先：

复用 Step 89 的 execution summary

避免一次步骤重复执行几十个测试文件。

十八、测试命令

执行：

python -m pytest -q tests/test_assistant_trace_timeline_regression.py

然后：

python -m compileall -q backend tests scripts

不要执行：

pytest -q
RUN_DB_TESTS=1 pytest -q

本步骤不是重新跑完整 Regression。

十九、Git Diff

检查：

git status --short
git diff --stat
git diff -- backend

要求：

backend diff = empty

允许：

tests/test_assistant_trace_timeline_regression.py
docs/evaluation/phase-3.12-trace-timeline-regression.md

不要新增文件。

二十、最终报告

严格输出：

Phase 3.12 Step 90 COMPLETE

1. Matrix Execution Baseline
   - Offline:
   - DB:
   - Matrix:
   - DB residue:

2. Baseline Contract
   - immutable:
   - duration excluded:
   - status rule:

3. Offline Snapshot
   - unchanged:
   - value:

4. Matrix Scale
   - unchanged:
   - value:

5. Node-hosted
   - execution:
   - contract audit:

6. Drift
   - classifier:
   - synthetic drift:

7. Tests
   - passed:
   - skipped:
   - failed:

8. Compileall

9. Backend Diff

10. Network / DeepSeek / DB

11. Previous Contract
   - Step 73~89 unchanged

12. Current Limitations

Phase 3.12 Step 90 READY
Phase 3.12 Step 90 STOP
二十一、硬停止

完成后立即：

STOP

不要进入：

Unified Event ID
Sequence
Span
Parent Event
Pagination
Conversation
Memory
Agent
MCP
OpenTelemetry

这些都不属于 Step 90。