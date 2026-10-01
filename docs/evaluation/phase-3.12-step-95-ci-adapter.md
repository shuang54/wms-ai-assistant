# Phase 3.12 Step 95 — CI Adapter（最小实现）

## 链路

```text
MatrixBaselineGateResult
        ↓
CI Adapter（backend/app/services/matrix_ci_adapter.py）
        ↓
exit code
```

```text
PASS  → 0
DRIFT → 1
```

> GitHub Actions 后续可直接利用该 exit code 判断 step 成败（0 = 成功，非 0 = 失败）。
> 本阶段**不**创建 GitHub Actions / CLI。

## API

```python
adapt_gate_result_to_exit_code(result: MatrixBaselineGateResult) -> int
```

```text
input : MatrixBaselineGateResult（严格类型契约；不做 dict / str 自动转换）
output: int ∈ {0, 1}
```

唯一映射（**单一来源**，测试侧复用同一对象）：

```text
CI_ADAPTER_EXIT_CODES = {"PASS": 0, "DRIFT": 1}（只读 MappingProxyType）
```

Adapter 只读取 `result.status`；不对 drift 类型做分支（所有 drift 一律 1）。

## Adapter does not

```text
- execute matrix              （不执行 18 offline / 15 DB 文件）
- query DB                    （无 SQLAlchemy / psycopg / PostgreSQL）
- calculate baseline          （不读取 MATRIX_EXECUTION_BASELINE）
- calculate drift             （不调用 evaluate_matrix_baseline_gate / compare_...）
- refresh baseline            （DRIFT → 1；绝不 refresh → 0）
- call LLM                    （无 DeepSeek / OpenAI）
- call network                （无 httpx / requests）
- sys.exit()                  （只返回值；是否 raise SystemExit 由未来 CLI 决定）
- persist output              （无 JSON / HTML / DB / HTTP / Dashboard / Telemetry）
```

## 依赖

```text
生产 Adapter 依赖：MatrixBaselineGateResult（结构契约）+ Python stdlib（types / typing）
模块级 import ⊆ {__future__, types, typing}；无第三方依赖；无 pytest 依赖
```

## Step 95 does not implement

```text
- CLI            （无 main / cli / run_gate；无 argparse / click / typer）
- GitHub Actions （无 .github/ · 无 workflow）
- Baseline Refresh
- Dashboard · Telemetry · OpenTelemetry · Prometheus
```

## 测试

```text
tests/test_matrix_ci_adapter.py（17 项）
    §十四 Test 1  PASS → 0                         Test 2  DRIFT → 1
    §十四 Test 3  5 类 drift → 1                   Test 4  非法输入（None/dict/str/tuple/
                                                          Summary/Baseline/object）→ TypeError
    §十四 Test 5  调用前后 result 不变             Test 6  Gate Result 仍 frozen
    §十四 Test 7  同输入 100 次 → 恒 0 / 恒 1
    §十四 Test 8  可执行源码无 sys.exit/subprocess/sqlalchemy/psycopg/httpx/openai/deepseek/get_engine
    §十四 Test 9  不引用 MATRIX_EXECUTION_BASELINE / MatrixExecutionBaseline /
                  compare_matrix_execution_baseline / evaluate_matrix_baseline_gate
    §十四 Test 10 不检查 current/baseline/offline/db/matrix_total/matrix_status/db_residue
    §十四 Test 11 复用 Step 94 的 CI_ADAPTER_EXIT_CODES（同一对象）
    §十四 Test 12 无隐藏执行（静态 + 运行时）
    附加         模块 import ⊆ stdlib · 无 CLI 入口 · 无 .github/ · 合成 helper 纯净

回归入口：tests/test_assistant_trace_timeline_regression.py 登记
    CI_ADAPTER_IMPLEMENTATION = adapt_gate_result_to_exit_code
    （作为现有 Matrix Contract 的一个节点；未建立第二套 Contract Registry）
    —— 154 passed / 2 skipped
```

## 运行边界

```text
Adapter 调用时：Regression Matrix = 0 · DB = 0 · Network = 0 · LLM = 0
（纯函数：stateless · deterministic · 无 time / random / 环境变量 / sleep / retry）
```
