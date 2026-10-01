# Phase 3.12 Step 97 — GitHub Actions 最小 CI 接入

## 链路

```text
Pull Request / Push
        ↓
GitHub Actions（.github/workflows/observability-matrix-gate.yml）
        ↓
PostgreSQL CI Service（pgvector/pgvector:pg16，临时实例）
        ↓
python scripts/run_matrix_gate.py
        ↓
MatrixExecutionSummary → MatrixExecutionBaseline → evaluate_matrix_baseline_gate()
        ↓
MatrixBaselineGateResult
        ↓
adapt_gate_result_to_exit_code()
        ↓
exit code
```

```text
PASS  → 0 → CI success
DRIFT → 1 → CI failure（GitHub Actions 默认按 exit code 判定 step 成败）
```

## Workflow

```text
文件：.github/workflows/observability-matrix-gate.yml（**唯一** workflow / 唯一 job）
触发：pull_request · push（无 schedule / workflow_dispatch / release / deployment）
Runner：ubuntu-latest
Steps：
    1. actions/checkout@v4
    2. actions/setup-python@v5  → python-version "3.13"（与本地运行时 3.13 一致）
    3. pip install -r requirements.txt（复用既有依赖清单，不新增依赖）
    4. python -m backend.app.db.init_db（复用既有 DB 初始化：pgvector extension + ORM 表）
    5. python scripts/run_matrix_gate.py（唯一核心 step；**无** continue-on-error）
```

## PostgreSQL（CI 临时实例）

```text
services.postgres.image = pgvector/pgvector:pg16（与项目 docker-compose 一致，未升级版本）
env: POSTGRES_USER / POSTGRES_DB + POSTGRES_HOST_AUTH_METHOD: trust（CI 内免密，不落口令）
DATABASE_URL = postgresql+psycopg://postgres@localhost:5432/wms_ai（仅指向 CI service）
不连接任何生产 / 开发数据库
```

## LLM / Network / Secrets

```text
LLM = 0 · Network = 0 · DeepSeek = 0
不配置：DEEPSEEK_API_KEY · SILICONFLOW_API_KEY · LLM_API_KEY · EMBEDDING_API_KEY
不出现：openai / deepseek / siliconflow / curl / wget / requests / httpx
可执行内容（排除注释）不含：api_key · authorization · password
```

## Baseline / 输出

```text
Baseline 只读：workflow 不出现 MATRIX_EXECUTION_BASELINE = · json.dump · refresh/update/write baseline
输出沿用 Step 96 CLI：
    Matrix Gate
    Status: PASS
    Exit code: 0
    —— 或 ——
    Matrix Gate
    Status: DRIFT
    Drifts:
    - <DRIFT_TYPE>
    Exit code: 1
无 JSON / HTML / Dashboard event / PR comment
```

## 未实现（本阶段边界）

```text
✗ Baseline Refresh
✗ PR Comment / GitHub Annotation / GITHUB_TOKEN / GitHub REST·GraphQL API / gh CLI
✗ Dashboard · Telemetry · OpenTelemetry · Prometheus · Langfuse · Sentry
✗ Release / Deploy
✗ Matrix 分层 CI（不做 offline-gate / db-gate / rag-gate / tool-gate / t2s-gate / trace-gate / timeline-gate）
```

## 测试

```text
tests/test_github_actions_matrix_gate.py（22 项，静态 YAML 审计，**不访问 GitHub**）
    文件存在 · 触发仅有 pull_request/push · checkout/setup-python · 核心命令 ·
    无 continue-on-error · 无 `|| true` / `exit 0` / `if: failure()` · 无刷新参数 ·
    单一 gate job（禁止拆分）· 无 LLM 凭据 · 无 openai/deepseek/siliconflow ·
    无 curl/wget/requests/httpx · ubuntu-latest · python-version == 当前解释器主次版本 ·
    postgres image == pgvector/pgvector:pg16 · DATABASE_URL 仅 localhost ·
    requirements.txt · backend.app.db.init_db · 安全（无凭据 / 无 GITHUB_TOKEN）·
    baseline 只读 · YAML 不重新实现判断逻辑

回归入口（同步更新 `.github` 断言：Step 97 授权**一个** workflow）：
    tests/test_matrix_ci_adapter.py + tests/test_matrix_gate_cli.py → 31 passed
    tests/test_assistant_trace_timeline_regression.py → 154 passed / 2 skipped
```

## 本地 CI 等价验证

```powershell
python scripts/run_matrix_gate.py
$LASTEXITCODE
```

```text
Matrix Gate
Status: PASS
Exit code: 0     → $LASTEXITCODE = 0
```
