# Phase 6 Step 8D — Pre-Commit Review

项目路径：`D:\coding\ai\wms-ai-assistant`

## 1. 目标

对当前 Phase 6 的未提交变更进行完整的提交前审查，确认：

1. 所有变更均属于已批准的 Phase 6 范围。
2. 不存在凭证泄漏、临时产物或意外修改。
3. 8B-1 至 8B-3 的 residue 防护没有扩大清理范围。
4. 五个治理门失败的原因已准确定位。
5. 可以给出明确的提交文件清单和提交后验证计划。

**本步骤不负责提交，也不负责修复。**

## 2. 不可违反的约束

禁止执行：

```text
git add
git commit
git push
git reset
git clean
git stash
任何文件编辑
任何数据库 INSERT / UPDATE / DELETE / DDL
任何 migration
```

禁止修改测试断言、跳过测试、放宽安全条件或隐藏失败。

保留当前所有 tracked 和 untracked 内容。

## 3. 建立审查基线

执行并保存结果：

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

将当前结果与 Phase 6 Exit Readiness Audit 对比。

逐项说明：

* tracked 修改是否仍为预期的 18 个文件。
* untracked 文件中哪些是源代码、测试、文档、任务书或临时产物。
* 是否存在审计报告中没有提到的新变化。
* `git diff --check` 是否通过。

不要将 untracked 文件自动加入提交清单。

## 4. 审查所有 tracked diff

对每个修改文件检查完整 diff，而不只是文件统计。

重点关注：

### 4.1 `tests/conftest.py`

审查：

* session 级 residue 检查是否确实在清理守卫之后执行。
* fixture 的依赖关系是否明确。
* teardown 即使遇到异常是否仍能执行必要的清理。
* fresh connection 查询是否避免了 ORM/Session 缓存误判。
* `total <= snapshot` 的上界语义是否与本阶段约定一致。
* 是否存在检查失败被吞掉、异常被隐藏或清理范围扩大的情况。

### 4.2 `tests/observability_residue_contract.py`

审查：

* 四张 observability 表的检查范围。
* 是否包含所有约定的计数与异常信息。
* 是否错误地把未知状态视为零 residue。
* 是否存在只验证聚合总数、却遗漏必要单表断言的问题。

如果发现缺陷，只记录，不修复。

### 4.3 两个项目隔离测试文件

重点检查：

```text
tests/test_project_capability_isolation.py
tests/test_project_datasource_isolation.py
```

对 `_tool_execution_residue_guard` 逐项确认：

* 水位是否在测试执行前采集。
* 清理是否严格限定在 `id > watermark`。
* 是否使用正确的数据库连接和事务处理方式。
* 是否可能删除其他并发测试写入的数据。
* 清理失败时是否会明确报告。
* 是否存在自增 ID、并发执行或事务可见性导致的盲区。

特别注意：**水位清理只适用于能够证明数据归属的隔离环境。不能仅凭 `id > watermark` 就认定并发安全。**

### 4.4 其余修改

逐项检查是否：

* 改变了生产行为或 API contract。
* 修改了测试语义。
* 新增了与 Phase 6 无关的功能。
* 泄露密钥、连接字符串或个人数据。
* 删除了原有安全断言。
* 引入了不必要的依赖。

## 5. 审查 untracked 文件

逐个检查未跟踪文件的路径、类型、内容与用途。

分类为：

```text
KEEP — Phase 6 正式实现/测试/文档
OPTIONAL — 有价值但不属于本次提交必要内容
EXCLUDE — 任务书副本、日志、缓存、临时输出或无关文件
```

只提出建议，不删除文件、不移动文件、不修改文件。

对于不适合提交的任务书副本，给出具体路径，并说明建议排除的理由。

## 6. 五个治理门失败审查

检查以下测试及其依赖：

```text
test_11_backend_working_tree_is_unmodified
TestOfflineRegressionExecutionSummary
TestMatrixExecutionBaseline::test_baseline_matches_step89_actual_execution
TestMatrixBaselineGate::test_gate_passes_when_current_matches_baseline
TestRegressionSuite::test_offline_gate_suite_is_green
```

确认：

1. 哪个测试是原始失败。
2. 哪四个属于级联失败。
3. `git diff --name-only -- backend` 的断言究竟检查什么。
4. 当前未提交 backend 文件是否是直接触发条件。
5. 该治理门是否有意要求干净的 tracked backend 工作区。
6. 是否存在其他独立失败被错误归入级联。

不得通过修改断言、忽略工作区或模拟 Git 输出来通过治理门。

输出原始失败与级联失败的因果关系。

## 7. 数据库与 residue 安全审查

本步骤禁止任何数据库写操作。

只读检查现有代码与证据：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
agent_run
agent_step
agent_tool_result
agent_checkpoint
workflow_run
workflow_node_run
workflow_approval
```

确认：

* 清理守卫覆盖哪些表。
* 哪些表仅由会话级断言检查。
* 哪些写入路径可能没有清理守卫。
* 当前报告中的零计数是否有本轮只读查询证据。
* 历史 14 行消失的原因仍然未知时，是否如实保留未知状态。

本步骤不得执行 DELETE 来“验证”清理逻辑。

## 8. 运行限制

只允许执行不会修改项目内容的检查，例如：

```powershell
git diff --check
```

如需运行测试，必须先说明必要性并限制在最小范围；默认不运行全量测试，不运行任何会执行数据库写入或清理的测试。

不得因为某项测试需要干净工作区，就先提交代码来绕过失败。

## 9. 输出审查报告

严格按以下格式输出：

```text
【Phase 6 Step 8D — Pre-Commit Review】

1. Git 基线
- Branch / HEAD:
- Tracked modified:
- Untracked:
- diff --check:

2. 文件审查
- 已审查文件：
- 发现的问题：
- 生产逻辑变更：
- 安全风险：

3. Residue Guard 审查
- fixture 依赖：
- 清理范围：
- 并发风险：
- 误删风险：
- 仍未覆盖的路径：

4. Governance Gate
- 原始失败：
- 级联失败：
- 根因：
- 是否发现独立失败：

5. Untracked 文件建议
- KEEP:
- OPTIONAL:
- EXCLUDE:

6. 提交候选清单
- 建议纳入：
- 建议排除：
- 每项理由：

7. 阻塞项
- 严重性：
- 证据：
- 最小后续动作：

8. 审查结论
- APPROVE FOR USER REVIEW
- HOLD — ISSUES FOUND
- INCONCLUSIVE

9. 本步骤执行情况
- 文件修改：NONE
- 数据库写入：NONE
- Commit / Push：NOT EXECUTED
```

`APPROVE FOR USER REVIEW` 仅代表审查结果可交给用户决策，不代表已授权提交。

完成后立即停止，等待用户批准。不得自动执行下一步。
