# Phase 7 Recovery M2.3 A2 — Post-Cleanup Final Verification

## 一、目标

确认 15 行残留清理后，DB 回归、Matrix Gate 和四表绝对 residue 均保持通过，并审查本轮新增的清理脚本。

## 二、执行前检查

1. 确认 HEAD 仍为 `c91e895f0a6c2d20cce8c6e569e2d7ea4a03203b`。
2. 确认分支为 `phase7`。
3. 检查 `git status --short` 和 `git diff --check`。
4. 不执行 Git add、commit、push。
5. 检查 `scripts/cleanup_test_db_residue.py`：
   - 必须硬断言数据库为 `wms_ai_test`、端口为 `5433`。
   - 删除操作必须使用显式 ID 白名单。
   - 不得按 `project_id IS NULL` 批量删除。
   - 不得执行全表 DELETE、TRUNCATE 或卷清理。
   - 不要为了审查脚本而再次执行清理。

## 三、清理后 Matrix Gate

先设置非空 `DATABASE_URL` 指向 `localhost:5433/wms_ai_test`，并在执行前验证实际 Engine URL。

然后执行：

```powershell
python scripts/run_matrix_gate.py
```

记录准确的退出码、offline / DB / matrix 统计。

预期冻结结果：

```text
offline: total=375, passed=356, skipped=19, failed=0, errors=0
DB:      total=180, passed=180, skipped=0, failed=0, errors=0
Matrix:  total=555, status=PASS
```

不得修改冻结常量、Gate 测试或生产代码来适配结果。

## 四、检查绝对 residue

Matrix Gate 完成后，使用独立只读审计脚本再次核验：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
ai_ops.assistant_outcome_record
```

四张表都必须为 0。

必须区分：

- Matrix Gate 的增量 residue
- 四张表的绝对 residue

不能以 `residue=0` 的 Gate 结果代替绝对计数。

如果测试再次产生残留，不要自动清理；先记录 ID、时间范围和归属证据并报告。

## 五、最终状态核验

执行：

```powershell
git diff --check
git status --short
```

确认：

- HEAD 未变化。
- 没有新增生产代码修改。
- 没有修改冻结基线。
- 没有数据库 Schema 变化。
- 测试库四表绝对 residue 为 0。
- 不清理或停止测试数据库容器与卷。

## 六、完成报告

报告：

1. Matrix Gate 退出码与完整统计
2. 四表绝对 residue
3. 清理脚本安全审查结果
4. HEAD / branch / Git 状态
5. 是否出现新的测试残留
6. 是否修改代码或 Schema
7. M2.3 A2 最终验收状态

全部通过后，将状态标记为 `M2.3 A2 — ACCEPTED`，然后停止。

不进入下一阶段，不提交 Git。