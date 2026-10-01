# Phase 3.12 Step 96 — 本地 CI Gate CLI（最小接入）

## 链路

```text
Regression Matrix
        ↓
Matrix Execution（Step 89 MatrixExecutionSummary）
        ↓
Matrix Baseline Gate（Step 91/92 evaluate_matrix_baseline_gate）
        ↓
MatrixBaselineGateResult
        ↓
CI Adapter（Step 95 adapt_gate_result_to_exit_code）
        ↓
Local CLI（scripts/run_matrix_gate.py）
        ↓
exit code
```

```text
PASS  → 0
DRIFT → 1
```

## 命令 / 实测

```powershell
python scripts/run_matrix_gate.py
```

```text
Matrix Gate
Status: PASS
Exit code: 0
```

```text
实测（2026-10-01 · 本机测试 PG）：Status: PASS · **exit code = 0**（shell 已验证）
```

## CLI 唯一职责（orchestration layer）

```text
执行已有 Regression Matrix → 取得 Gate Result → 交给 Adapter → SystemExit(code)

CLI **复用**：
    MatrixExecutionSummary / MatrixExecutionBaseline（Step 89/90）
    evaluate_matrix_baseline_gate()（Gate）
    adapt_gate_result_to_exit_code()（Adapter，唯一映射）

CLI **禁止重新实现**：
    offline / DB PASS 判断 · matrix_total 计算 · baseline 比较 · drift 分类 ·
    status 判断 · exit code 映射
CLI 不得出现：`if current != baseline` / `if summary.failed > 0` / `if residue != 0`
```

## API

```python
def main() -> int: ...                       # 只返回 0 / 1（内部不 sys.exit）
if __name__ == "__main__":
    raise SystemExit(main())                 # CLI 层唯一允许的 SystemExit 位置
```

## 输出契约

```text
PASS：
    Matrix Gate
    Status: PASS
    Exit code: 0

DRIFT：
    Matrix Gate
    Status: DRIFT
    Drifts:
    - <DRIFT_TYPE>            （仅 drift 类型，逐行）
    Exit code: 1

DB 环境不满足：
    Matrix Gate
    Gate: not evaluated (DB execution unavailable)
    Exit code: 1             ← fail-closed：**不**伪造 PASS、不创建新状态（ERROR/UNKNOWN…）

不得输出：current / baseline DTO · api_key · password · database_url · prompt · SQL ·
          RAG chunks · tool args · LLM response
```

## Baseline

```text
Step 96 **不刷新** Baseline：MATRIX_EXECUTION_BASELINE 只读（所有权仍在 Gate）；
CLI 不得 write / overwrite / generate / save / dump baseline（静态断言禁止）。
```

## 依赖 / 参数

```text
依赖：Python stdlib + 既有 Matrix / Gate / Adapter（无 openai · deepseek · sqlalchemy ·
      psycopg · redis · kafka · httpx · requests）
参数：**无**（无 argparse / click / typer / sys.argv；无 --refresh / --update-baseline / --db /
      --project / --environment）
```

## 测试

```text
tests/test_matrix_gate_cli.py（14 项）
    1  PASS（fake）→ main() == 0        2  DRIFT（fake）→ main() == 1
    3  __main__ 守卫 = raise SystemExit(main())（静态 + 语义）· 3b exit code 唯一来源 = Adapter
    4  CLI 调用 adapt_gate_result_to_exit_code（无第二份 PASS/DRIFT 映射）
    5  CLI 调用 evaluate_matrix_baseline_gate（无 current != baseline 等比较）
    6  无 baseline 刷新（禁 json.dump / .write( / save( / refresh / overwrite …）
    7  无隐藏依赖      8  可执行源码无 secrets（排除 docstring）
    9  确定性（同 fake 连续 5 次恒等）   10  无 CLI 参数 · 输出契约 · Matrix 注册 ·
       DB 不可用 fail-closed

回归：tests/test_matrix_ci_adapter.py（17 passed）
      tests/test_assistant_trace_timeline_regression.py（154 passed / 2 skipped）
      —— CI_GATE_CLI = "scripts/run_matrix_gate.py" 登记在现有 Matrix Contract Registry
```

## 本阶段实现 / 未实现

```text
✓ 本地 CLI（scripts/run_matrix_gate.py）
✓ 真实 exit code（PASS → 0 已实测）
✗ GitHub Actions（无 .github/ · 无 workflow · 无 GitHub CLI）
✗ Baseline Refresh
✗ Dashboard / Telemetry / OpenTelemetry / Prometheus / Langfuse
✗ PR Annotation / PR comment / GitHub annotation
```
