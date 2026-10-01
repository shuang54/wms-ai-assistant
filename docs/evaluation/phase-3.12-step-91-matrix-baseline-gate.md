# Phase 3.12 Step 91 — Regression Matrix Actual Baseline Gate

## 链路

```text
Step 89 Actual Execution
        ↓
Step 90 Frozen Baseline（MATRIX_EXECUTION_BASELINE）
        ↓
Step 91 Regression Gate（evaluate_matrix_baseline_gate → PASS / DRIFT）
```

## Current Actual

```text
Offline  375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB       180 / 180 /  0 / 0 / 0 / exit 0 / PASS
Matrix   555 / PASS
Residue  0
```

## Frozen Baseline

```text
same values（Step 90 冻结；本步骤**未**修改）
Offline  375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB       180 / 180 /  0 / 0 / 0 / exit 0 / PASS
Matrix   555 / PASS
Residue  0
```

## Gate

```text
evaluate_matrix_baseline_gate(current, baseline) -> MatrixBaselineGateResult
    status  ∈ {PASS, DRIFT}（由 drifts 推导，构造期校验）
    drifts  ⊆ { NO_BASELINE_DRIFT · OFFLINE_EXECUTION_DRIFT · DB_EXECUTION_DRIFT ·
                MATRIX_TOTAL_DRIFT · MATRIX_STATUS_DRIFT · DB_RESIDUE_DRIFT }
    current / baseline = 两个 MatrixExecutionBaseline（只读引用）

PASS ⇔ offline == baseline.offline ∧ db == baseline.db
        ∧ matrix_total 相同 ∧ matrix_status 相同
        ∧ db_residue 相同 ∧ current.db_residue == 0
否则 DRIFT（**无**容差 / 无"基本一致" / 无自动更新 baseline）

结果：**PASS**（current == baseline）
duration_seconds 不参与 equality / drift / gate status
```

## 明确边界

```text
No test execution in Step 91     （未重跑 18 offline / 15 DB 文件）
No DB execution in Step 91       （仅复用 Step 89 的会话内缓存结果 + 只读残留计数）
No LLM · No network
No baseline update               （无 baseline refresh；无 current → baseline 覆盖）
Node-hosted Contract（OFFLINE_EXECUTION_CONTRACT）仍仅为 Contract Audit，
    不进入 FILES / CATEGORIES / suites / MatrixExecutionSummary / MatrixExecutionBaseline
Matrix Scale（13 / 28 / 18 / 15）与 Step 84 Offline Snapshot（375 / 356 / 19）均未修改
```

## 合成 Drift 覆盖（Step 91 测试）

```text
Case A  完全一致                         → PASS
Case B  offline passed 356→355 (skip+1)   → OFFLINE_EXECUTION_DRIFT + DRIFT
Case C  db skipped 0→1 (passed-1)         → DB_EXECUTION_DRIFT + DRIFT
Case D  db total 180→181                  → DB_EXECUTION_DRIFT + MATRIX_TOTAL_DRIFT + DRIFT
Case E  db failed 0→1 / exit 1            → DB_EXECUTION_DRIFT + MATRIX_STATUS_DRIFT + DRIFT
Case F  residue 0→1（其余完全一致）        → DB_RESIDUE_DRIFT + DRIFT
Case G  仅 duration 9.15→20.0             → PASS
Case H  offline + db + residue 同时变化    → 4 类 drift 全部出现 + DRIFT
```

说明：`MATRIX_TOTAL_DRIFT` / `MATRIX_STATUS_DRIFT` 在构造期由部分状态推导（DTO 不变式），
故它们必然与对应 part 的 `*_EXECUTION_DRIFT` **共现**（组合式分类，非互斥）。
