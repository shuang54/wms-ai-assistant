# Phase 6 Step 8F — Matrix DB Residue Order-Dependence Fix

项目：`D:\coding\ai\wms-ai-assistant`

当前 HEAD：

```text
efbc8cb58220550ca726ccb5e9241c15d0d59074
```

## 一、目标

解决 DB 分片 2 中两个矩阵测试的顺序依赖：

```text
TestMatrixExecutionBaseline::test_baseline_matches_step89_actual_execution
TestMatrixBaselineGate::test_gate_passes_when_current_matches_baseline
```

最近证据：

```text
baseline.db_residue = 0
matrix current.db_residue = 15
result.status = DRIFT
```

报告显示，`matrix current` 在会话中途读取了全库 residue，而先前测试写入的记录尚未到会话收尾清理阶段。

本阶段目标是让矩阵评估使用稳定、定义明确且可重复的 residue 语义，避免把合法的会话内临时记录误判成历史 baseline 漂移。

## 二、严格范围

允许修改：

* 相关矩阵测试。
* 对应的测试辅助函数或 evaluation helper。
* 必要时增加针对顺序依赖的测试。

禁止修改：

```text
Agent Runtime
Tool Execution Service
Workflow Runtime
AI Orchestrator
API 认证与授权
数据库生产模型
生产数据库结构
生产业务逻辑
```

禁止：

* 修改历史 baseline 以简单接受 `15`。
* 关闭 residue 检查。
* 忽略 DRIFT。
* 增加 skip 或 xfail。
* 通过删除已有记录来让测试通过。
* 自动进入 Phase 7。

## 三、第一步：只读定位

先检查：

```text
MATRIX_EXECUTION_BASELINE
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
matrix current 的生成逻辑
db_residue 字段的计算逻辑
test_db_residue_is_zero
tests/conftest.py
tests/observability_residue_contract.py
```

查明：

1. `db_residue` 的原始定义是什么。
2. 历史 baseline 中的 `0` 代表什么：全库绝对计数、测试前后增量，还是会话最终残留。
3. 当前 `15` 来自哪些表。
4. 15 条记录是否属于本轮测试的合法临时记录。
5. 会话收尾后这些记录是否确实清零。
6. 矩阵测试是否在其他测试写入数据后运行。
7. 现有 fixture 是否已经提供可复用的会话起始快照或 residue contract。

必须基于实际代码和测试输出，不得只依据历史报告猜测。

## 四、修复原则

根据实际语义，选择最小且正确的修复。

优先考虑：

* 若指标目标是检测本轮测试造成的新增残留，则使用一致的起始快照与当前值计算增量。
* 若指标目标是验证整个测试会话结束后的 residue，则应在正确的会话收尾边界进行验证。
* 若矩阵本来就只应在专用干净数据库中执行，则明确其运行前置条件，并确保测试门不会把普通测试会话中的临时记录误判成 baseline 漂移。

不要在尚未理解 `db_residue` 语义前直接修改断言。

**不得把真实残留隐藏为零，也不得降低原有安全检查强度。**

## 五、测试要求

新增或调整测试，证明：

1. 相同初始状态和相同测试序列产生稳定结果。
2. 合法的会话内临时记录不会造成错误的 baseline DRIFT。
3. 真正未清理的残留仍然会被发现。
4. `test_db_residue_is_zero` 的现有契约不被削弱。
5. 矩阵 baseline 的其他字段和比较规则不受影响。
6. 相关测试在不同执行顺序下结果一致。

优先复用现有测试设施，不重新创建 Fake 或新的数据库框架。

## 六、验证步骤

先运行与修复直接相关的测试：

```powershell
python -m pytest -q <实际矩阵测试文件及相关节点>
```

再使用项目现有的 DB 分片机制运行：

1. 复现原失败的分片 2。
2. 运行相关 residue 生命周期测试。
3. 运行全量 DB 分片回归。

只有实际命令与结果全部记录后，才可报告通过。

不要把不同轮次的分片结果拼接成全量结果。

全量离线测试可在最后运行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

## 七、额外提交完整性检查

本次提交报告记录了：

```text
docs/roadmap/Project-Recovery-Audit.md:146
trailing whitespace
```

`git diff --check` 在干净工作区上不能单独证明已经提交的文件没有该问题。

请只读运行：

```powershell
git show --check --oneline HEAD
```

记录实际结果。本步骤不要顺手修改该文档；若确认存在问题，单独列为卫生项。

## 八、最终报告

```text
【Phase 6 Step 8F — Matrix Residue Fix】

1. Root cause
- db_residue 原始语义：
- 15 条记录的来源：
- 为什么触发 DRIFT：

2. Fix
- 修改文件：
- 修复设计：
- 为什么没有削弱 residue 检查：

3. Regression
- 原失败测试：
- 相关测试：
- DB shard 2：
- 全量 DB：
- 全量 offline：

4. Baseline
- 是否修改：
- 其他字段是否保持一致：

5. Commit integrity
- git show --check HEAD：

6. Remaining risks
- ...

7. Git
- 当前 HEAD：
- 未提交修改：
- Commit / Push：NOT EXECUTED
```

完成后立即停止。不要自动提交，不要推送，也不要进入 Phase 7。
