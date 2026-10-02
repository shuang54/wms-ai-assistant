# Phase 3.12 Step 100 — CI Gate Contract Freeze & Repository Readiness Audit

> 本阶段只做 **Contract / Readiness Audit**：
> 不新增 CI 功能 · 不修改 Matrix / Gate / Adapter / Workflow / Baseline。

---

## 1. CI Gate Contract

```text
MatrixExecutionBaseline          （冻结期望值）
        ↓
evaluate_matrix_baseline_gate()  （Gate 拥有判断：current vs baseline）
        ↓
MatrixBaselineGateResult         （status · drifts · current · baseline）
        ↓
adapt_gate_result_to_exit_code() （Adapter 只做 status → 0/1）
        ↓
0 / 1                            （由 CLI / GitHub Actions process boundary 退出）
```

所有权边界（禁止越界）：

```text
Gate：  唯一判断者（无容差 · 无"基本一致" · 不刷新 baseline）
Adapter：只读取 result.status；不重算 baseline / drift / matrix / residue；不 sys.exit()
CLI：   只编排 + 输出最小文本；不判断 PASS/DRIFT；不吞 exit code
Workflow：只执行既有 CLI；不在 YAML 内实现任何判断或映射
```

---

## 2. Baseline

```text
offline:
    375 total · 356 passed · 19 skipped · 0 failed · 0 errors · exit_code = 0 · status = PASS
db:
    180 total · 180 passed ·  0 skipped · 0 failed · 0 errors · exit_code = 0 · status = PASS
matrix_total:   555
matrix_status:  PASS
db_residue:     0
```

强调：

```text
这是**当前冻结的** Matrix Execution Baseline（Expected Contract），
不是"永远不能变化的测试数量"。
未来测试结构若有**有意**变化 → 必须进入新的明确 Phase 更新 baseline。
```

**Run ≠ Baseline（§十一）**：

```text
GitHub Run  = Evidence（证明"当前执行 == 冻结 baseline"）
Baseline    = Expected Contract
禁止：Run 结果 → 自动覆写 baseline（无 auto refresh / 无 auto regenerate）
```

---

## 3. Gate

```text
PASS ⇔ offline == baseline.offline
       ∧ db == baseline.db
       ∧ matrix_total == baseline.matrix_total
       ∧ matrix_status == baseline.matrix_status
       ∧ db_residue == baseline.db_residue
       ∧ db_residue == 0
       ∧ drifts == ("NO_BASELINE_DRIFT",)

DRIFT ⇔ 任一真实漂移：
       OFFLINE_EXECUTION_DRIFT · DB_EXECUTION_DRIFT · MATRIX_TOTAL_DRIFT
       MATRIX_STATUS_DRIFT · DB_RESIDUE_DRIFT
       → status = DRIFT → CI exit code = 1
       不得表达为 warning / continue / skip / success
```

---

## 4. Adapter

```text
PASS  → 0
DRIFT → 1
CI_ADAPTER_EXIT_CODES 的键集恰为 {"PASS","DRIFT"}，值集恰为 {0,1}
只读取 Gate Result.status；不重新计算 baseline / drift / matrix / residue
不调用 sys.exit()（退出由 CLI / 进程边界负责）
```

---

## 5. CLI

```text
scripts/run_matrix_gate.py
    执行既有 Regression Matrix → Gate → Adapter
    PASS → 0 · DRIFT → 1（raise SystemExit(exit_code)）
    DB 环境不满足 → 不伪造结果，输出 "Gate: not evaluated" 并 exit 1
禁止：重新判断 / 刷新 baseline / 输出 DTO · 凭据 · prompt · SQL · 原始响应
```

---

## 6. Workflow

```text
文件：.github/workflows/observability-matrix-gate.yml（目录内唯一 workflow）
job：observability-matrix-gate（唯一 job，name = Observability Matrix Gate）
trigger：push + pull_request（无 schedule / workflow_dispatch / release /
         deployment / repository_dispatch）
runner：ubuntu-latest · setup-python 3.13
步骤：python -m backend.app.db.init_db → python scripts/run_matrix_gate.py
不吞失败：无 continue-on-error · 无 `|| true` · 无 `exit 0` · 无 `if: failure()`
性能：无 cache / retry / strategy / matrix 分层（不做自动重跑）
安全：无 secrets.* · 无 GITHUB_TOKEN · 无 GitHub API / PR comment · 无 permissions 块
```

---

## 7. PostgreSQL

```text
service image = pgvector/pgvector:pg16（临时 CI 实例，job 结束即销毁）
health check  = pg_isready；5432:5432；POSTGRES_HOST_AUTH_METHOD=trust
DATABASE_URL  = postgresql+psycopg://postgres@localhost:5432/wms_ai（**仅** CI 实例）
禁止：AWS / Azure / Neon / Supabase / 生产 PostgreSQL
pgvector extension 由 init_db 幂等启用
```

---

## 8. Residue

```text
llm_usage_record        = 0
tool_execution_record   = 0
rag_execution_record    = 0
assistant_outcome_record = 0
```

CI Matrix 是 **temporary execution environment**，不是 production data validation；
residue ≠ 0 必须 DRIFT（不得通过改 baseline 接受）。

---

## 9. LLM / Network

```text
DeepSeek = 0 · SiliconFlow = 0 · OpenAI = 0 · external API = 0
Workflow 不需要任何 API Key / Secrets（secrets 引用数 = 0）
Matrix Gate = offline + local PostgreSQL evaluation，**不是** LLM benchmark
```

---

## 10. Concurrency Source Identity（Step 99 修复后冻结）

```text
request identity = assistant_request_id
source identity  = (source, source_id)

禁止：要求 source_id 跨表全局唯一
    ("llm_usage", 1) · ("tool_execution", 1) · ("rag_execution", 1)
    ("assistant_outcome", 1)
    是四条**不同**的持久化记录 —— 数值相同完全合法
（Step 98 真实 CI 的失败根因即此假设；Step 99 已只改测试判据修复）
```

---

## 11. Real GitHub Evidence

```text
Run #1 · id 36859788376 · commit 559ce63 · failure
        Step 1～6 全绿，Step 7（Matrix Gate）exit 1
        = Step 98 首次真实运行；根因为测试断言缺陷（非生产问题）
Run #2 · id 36949287263 · commit 73b3c71 · **success**
        Step 7 success（23s）→ Gate PASS → exit 0 → job SUCCESS（≈59s）
        = Step 99 代码修复
Run #3 · id 36949724371 · commit 510ab81 · **success**
        = Step 99 documentation-only follow-up
```

Evidence 只写本目录（docs/evaluation）；**Run ID 不得进入生产代码**
（`backend/` · `scripts/` · `.github/` 由 `tests/test_github_actions_matrix_gate.py
::TestStep100GateContractFreeze::test_run_ids_never_enter_production_code` 强制）。

---

## 12. Required Status Check

```text
最新 commit（510ab81）上的 check runs：
    Observability Matrix Gate —— status completed · conclusion **success**
    （check_run 110659745580 · 2026-10-02T01:11:26Z → 01:12:29Z）
⇒ 该 workflow 已经可以作为 required status check 的候选

Branch protection status:
    NOT AUDITED
    原因：GET /repos/shuang54/wms-ai-assistant/branches/main/protection → 401 Unauthorized
          （需认证；本阶段不猜测、不执行任何仓库治理设置）

Repository branch protection = NOT CHANGED
    本阶段**未**开启：required status check / protect main / required review /
    branches up-to-date（属 Repository Governance，需单独授权）
```

---

## 13. Readiness Audit（§十五 逐项）

| # | 项           | 结论                                                        |
| - | ------------ | ----------------------------------------------------------- |
| 1 | Workflow     | 唯一 workflow / 唯一 job / 无 retry / 无 continue-on-error / 无 `\|\| true` / 无 `exit 0` ✔ |
| 2 | Gate         | 纯 baseline comparison（五项精确相等 + residue == 0）✔      |
| 3 | Adapter      | 纯 status → exit code（PASS→0 / DRIFT→1）✔                  |
| 4 | CLI          | PASS→0 · DRIFT→1 · 不伪造结果 ✔                             |
| 5 | Database     | 临时 CI PostgreSQL（pgvector/pgvector:pg16）+ init_db ✔      |
| 6 | Secrets      | 0 ✔                                                          |
| 7 | Production   | `git diff -- backend` = 空 ✔                                 |

```text
Workflow CI readiness = PASS
Repository branch protection = NOT CHANGED / NOT AUDITED
```

---

## 14. 当前限制

* Branch protection / ruleset 无法读取（401）⇒ required check 是否启用未审计
* 真实 CI 日志需登录（logs API 403）⇒ Run #2/#3 的 Gate 文本输出未逐行核对，
  以 check-run conclusion + 本地空库等价复现交叉印证
* 未重新跑完整 DB Matrix（§十八：Step 99 已完成真实 DB Matrix + CI 验证）
* 未进行性能优化 / 缓存 / 并行；未引入重试或自动修复
* 测试数量（375/180/555）为**当前**冻结值；任何有意增减需新 Phase 授权
* lint unavailable（环境未安装 ruff / flake8）
