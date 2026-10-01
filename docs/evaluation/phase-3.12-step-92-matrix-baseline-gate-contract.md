# Phase 3.12 Step 92 — Matrix Baseline Gate Contract Freeze

> Step 91 已实现并通过验证；本步骤**只冻结语义契约**（未新增 Runner / Baseline / CI）。
> 本步骤：`pytest execution = 0`（合成 DTO）· `DB execution = 0` · `Network = 0` · `LLM = 0`

## Gate Result

```text
status ∈ { **PASS** | **DRIFT** }        （无 WARNING / UNKNOWN / PARTIAL / SKIPPED / STALE）
drifts  ⊆ Drift Types（见下）
current / baseline  = 只读引用（Gate 不修改输入；无 current → baseline 覆盖）

PASS ⇔ current.offline == baseline.offline
        AND current.db == baseline.db
        AND current.matrix_total == baseline.matrix_total
        AND current.matrix_status == baseline.matrix_status
        AND current.db_residue == baseline.db_residue
        AND current.db_residue == 0

DRIFT ⇔ 上述任一不成立（含：offline / DB 执行结果变化、matrix_total 变化、
        matrix_status 变化、db_residue 变化、或 residue != 0）
```

## Drift Types（冻结集合；逐字）

```text
NO_BASELINE_DRIFT
OFFLINE_EXECUTION_DRIFT
DB_EXECUTION_DRIFT
MATRIX_TOTAL_DRIFT
MATRIX_STATUS_DRIFT
DB_RESIDUE_DRIFT
```

禁止新增：`PERFORMANCE_DRIFT` · `DURATION_DRIFT` · `COLLECTABILITY_DRIFT` ·
`NETWORK_DRIFT` · `LLM_DRIFT`（如需新增必须另开阶段明确设计）。

## 冻结项

```text
Duration excluded   —— duration_seconds 不进入 Baseline 字段 / equality / drift / gate status
                       （仅 duration 变化 → PASS）
Node-hosted excluded—— OFFLINE_EXECUTION_CONTRACT 仍属 NODE_HOSTED_CONTRACT_CATEGORIES，
                       不进入 FILES / CATEGORIES / offline·DB suite /
                       MatrixExecutionSummary / MatrixExecutionBaseline / Gate 计数
Baseline immutable  —— MATRIX_EXECUTION_BASELINE 在 Gate 前后完全一致（本步骤未修改 Step 90）
Current immutable   —— current（含 nested DTO）在 Gate 前后完全一致
No automatic refresh—— 无 baseline refresh / 无 self-healing / 无 current → baseline
Determinism         —— 相同输入连续调用结果完全一致（无 time / random / network / DB）
Residue safety      —— current == baseline 但 residue != 0 ⇒ **仍 DRIFT**
Composite drift     —— 多处同时偏离 ⇒ 返回**全部** drift 类型（非只报第一个）
No tolerance        —— 仅等值比较；无 >= / <= / > / <、无容差 / 百分比 / 通过率阈值
Security            —— Gate DTO / repr / nested DTO 不含 api_key · password · database_url ·
                       authorization · prompt · messages · sql · rag_chunk · tool_args ·
                       raw_response（复用 Step 91 断言风格，未重造 scanner）
```

## Gate 当前结果（Step 91 实测，本步骤未重跑）

```text
status = PASS · drifts = (NO_BASELINE_DRIFT,)
Offline  375 / 356 / 19 / 0 / 0 / exit 0 / PASS
DB       180 / 180 /  0 / 0 / 0 / exit 0 / PASS
Matrix   555 / PASS · Residue 0
```

## 说明（Step 92 期间的一处一致性修正）

```text
evaluate_matrix_baseline_gate 的 drifts 现在只表达"为何不是 PASS"：
    · 过滤掉与真实违规共存的 NO_BASELINE_DRIFT；
    · residue != 0 时登记 DB_RESIDUE_DRIFT（**不重复登记**）。
status 语义（PASS / DRIFT）与 Step 91 的既有断言**全部不变**；
唯一变化：current == baseline ∧ residue != 0 时 drifts 由
    ("NO_BASELINE_DRIFT", "DB_RESIDUE_DRIFT") → ("DB_RESIDUE_DRIFT",)（status 仍为 DRIFT）。
```
