# Phase 3.12 Step 94 — CI Adapter Contract Freeze

> **本阶段不实现 Adapter、不接 CI。** 只冻结最后两层之间的 Contract：
> `MatrixBaselineGateResult → CI Adapter → exit code`

## 冻结链路

```text
Regression Matrix Execution
        ↓
MatrixExecutionSummary
        ↓
evaluate_matrix_baseline_gate()
        ↓
MatrixBaselineGateResult
        ↓
CI Adapter                ← 本阶段只冻结这一层的**责任边界**
        ↓
exit code
```

## Adapter 唯一职责

```text
读取 MatrixBaselineGateResult.status，映射为 process exit code。

只负责：
    MatrixBaselineGateResult → CI process result

不得负责：
    执行 Regression Matrix · 读取 PostgreSQL · 重新计算 Baseline ·
    重新判断 Drift · 刷新 Baseline · 调用 LLM · 网络请求 · 持久化输出

即：Adapter ≠ Runner · Adapter ≠ Gate · Adapter ≠ Baseline Manager
```

## Input / Output

```text
Input  ：仅 MatrixBaselineGateResult
         不接受：MatrixExecutionSummary · MatrixExecutionBaseline · pytest stdout/stderr ·
                 raw subprocess result · database connection · HTTP/LLM response
Output ：仅 process exit code
         不输出：JSON · HTML · DB record · HTTP response · Dashboard event · Telemetry event
         （输出格式不在本阶段冻结范围）
```

## Exit Code Contract

```text
PASS  → 0
DRIFT → 1

只允许 0 / 1；不引入 2 / 3 / 4 / 10 / 99 等业务退出码。
若发生 execution infrastructure failure，应由 Regression Matrix Execution 本身产生失败结果，
而不是在 Adapter 中创造新的业务状态；本阶段不改变 MatrixBaselineGateResult.status。
```

## PASS / DRIFT 的 CI 语义

```text
PASS  （drifts = ("NO_BASELINE_DRIFT",)）→ exit 0（成功）
DRIFT （任意真实 drift）                 → exit 1（CI 失败）
      OFFLINE_EXECUTION_DRIFT · DB_EXECUTION_DRIFT · MATRIX_TOTAL_DRIFT ·
      MATRIX_STATUS_DRIFT · DB_RESIDUE_DRIFT —— **一律 exit 1**
      （Adapter 不得按 drift 类型选择不同退出码）
```

## 禁止（冻结）

```text
No execution          —— 不执行 18 offline / 15 DB 文件
No DB                 —— 不读 PostgreSQL / SQLAlchemy / psycopg
No network / LLM      —— 无 HTTP / OpenAI / DeepSeek
No baseline access    —— 不读 MATRIX_EXECUTION_BASELINE（Baseline 所有权只在 Gate，
                        避免出现 Gate + Adapter 双重判断）
No drift calculation  —— 不做 `if current != baseline`，不检查
                        offline / db / matrix_total / matrix_status / db_residue
                        （这些属于 evaluate_matrix_baseline_gate 职责）
No baseline refresh   —— DRIFT → exit 1，**不能** refresh → exit 0，
                        也不能 overwrite MATRIX_EXECUTION_BASELINE
                        （Baseline Refresh 永远属于未来显式授权阶段）
No result mutation    —— result 只读；不得 setattr，不得创建"修正后的" Gate Result
No output persistence —— 不落 JSON / HTML / DB / HTTP / Dashboard / Telemetry
```

## Status 完备性

```text
合法 Gate Status 仅 {PASS, DRIFT}，映射域恰好覆盖两者；
WARNING / UNKNOWN / PARTIAL / SKIPPED / STALE 由 Gate Result Contract 构造期拒绝
（Adapter 不需要处理这些状态）。
```

## 未来 Adapter 形态（**仅作 Contract 示例，未实现**）

```python
def adapt_gate_to_exit_code(result):        # ← 不要实现 / 不要加入 production code
    if result.status == "PASS":
        return 0
    if result.status == "DRIFT":
        return 1
    raise ValueError("Unsupported gate status")
```

## Security

```text
Adapter 只读取 status；不处理 api_key · password · database_url · authorization ·
prompt · messages · SQL · RAG chunks · tool args · raw response。
未来若需打印信息，只允许打印 drift type，**不**打印 current / baseline 原始 DTO。
```

## 状态

```text
CI integration          = **NOT DONE**
Adapter implementation  = **NOT DONE**（无 .github/ · 无 CLI · 无注入式入口函数）
GitHub Actions          = **NOT DONE**
CLI                     = **NOT DONE**
Baseline refresh        = **NOT DONE**
DB migration / API 修改 = 无
```
