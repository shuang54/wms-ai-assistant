# Phase 3.12 Step 98 — GitHub Actions 首次真实运行审计

> 目的：**验证 Step 97 Workflow 在真实 GitHub Actions 环境中能否完整运行**。
> 本阶段不新增 CI 能力；不修改生产代码 / Gate / Adapter / Baseline。
> 数据来源：GitHub 公开 REST API（只读）+ 本地「全新库」等价复现。

---

## 1. Run

```text
Run:        #1（run_id 36859788376）
Commit:     559ce63876c8f9b21a71ff4eba610227a74d25a2
Trigger:    push（main）
Workflow:   Observability Matrix Gate
Job:        Observability Matrix Gate（唯一 job）
Result:     FAILURE（conclusion = failure）
Duration:   workflow 1m 27s · job 1m 22s
URL:        https://github.com/shuang54/wms-ai-assistant/actions/runs/36859788376
```

---

## 2. 完整链路（真实 CI 逐步结论）

| # | Step                             | Conclusion | 时间 (UTC)      |
| - | -------------------------------- | ---------- | --------------- |
| 1 | Set up job                       | success    | 12:08:27→12:08:28 |
| 2 | Initialize containers（PG service）| success    | 12:08:28→12:09:04 |
| 3 | Checkout                         | success    | 12:09:04→12:09:06 |
| 4 | Setup Python                     | success    | 12:09:06→12:09:06 |
| 5 | Install dependencies             | success    | 12:09:06→12:09:20 |
| 6 | Initialize database schema       | success    | 12:09:20→12:09:21 |
| 7 | **Run observability matrix gate**| **failure**| 12:09:21→12:09:45 |

```text
Python:      3.13（setup-python@v5）—— Step 4 success
PostgreSQL:  service pgvector/pgvector:pg16（Step 2 Initialize containers success）
pgvector:    extension 由 init_db 幂等启用 —— Step 6 success
init_db:     python -m backend.app.db.init_db —— success
Matrix:      执行（Step 7 耗时 24s，与本地完整执行 19.4s 同量级 ⇒ 非快速崩溃）
Gate:        DRIFT（exit 1 由 Adapter 映射，未被吞）
Adapter:     adapt_gate_result_to_exit_code → 1
Exit code:   1
Annotations: 1 error（Process completed with exit code 1 于 Step 7）
             + 1 warning（Node 20 deprecation）+ 1 notice（ubuntu-latest 将于 2026-10-19 迁移）
```

结论：**链路前 6 步全部通过** ⇒ 不是 Workflow 配置层问题（checkout / Python /
依赖安装 / PostgreSQL service / init_db 均正常）。失败点在 Gate 步骤本身。

---

## 3. 日志可读性限制（必须记录）

```text
GitHub Actions 日志需登录才能查看：
    GET /actions/runs/36859788376/logs        → 403（未认证）
    https://github.com/.../runs/36859788376   → "Sign in to view logs"
本机 gh CLI：未安装
⇒ 真实 CI 的 drift 文本**无法直接读取**；改用"全新库等价复现"定位根因。
```

---

## 4. 根因定位：本地「全新库」等价复现

复现方式（一次性探针，已删除；未修改任何仓库文件）：

```text
CREATE DATABASE wms_ai_ci98        ← 完全空库（等价 CI service 初始状态）
python -m backend.app.db.init_db   ← 只建 ORM 表 + extension（与 CI Step 6 一致）
python scripts/run_matrix_gate.py  ← 与 CI Step 7 同一命令
DROP DATABASE wms_ai_ci98          ← 复现后清理
```

结果：

```text
Matrix Gate
Status: DRIFT
Drifts:
- DB_EXECUTION_DRIFT
- MATRIX_STATUS_DRIFT
Exit code: 1
```

即 **CI 失败被本地等价复现**（DB suite 实际结果 ≠ 冻结 baseline 180/180）。

进一步定位（同一空库跑 DB suite）：

```text
FAILED tests/test_assistant_timeline_concurrency_db_e2e.py
       ::TestTimelineConcurrency::test_case_e_ten_concurrent_requests
1 failed, 179 passed
```

断言细节：

```text
tests/test_assistant_timeline_concurrency_db_e2e.py:561
    assert source_ids.isdisjoint(all_source_ids)   # 无跨请求 PK 重叠
E   assert False
E    where False = {1}.isdisjoint({1, 2, 3, 4, 5, 6, ...})
```

**根因**：该断言把 Timeline `source_id` 当作"跨表全局唯一 ID"来要求互不相交。
空库上四张表 `id` 序列均从 1 开始 ⇒ LLM 表 id=1 与 Tool/RAG/Outcome 表 id=1 必然重叠
⇒ 断言失败。本地开发库的序列已被历史运行推进，恰好不重叠 ⇒ 本地 PASS。

这与 Step 73 Source ID Contract 直接冲突：

```text
source_id = 当前 persistence record 的数据库主键
不是 event_id · 不是全局唯一时间线 ID · 不是排序号 · 不是数组下标 · 不是 UUID
```

跨请求隔离的正确判据是 `assistant_request_id`（同一测试前一个断言
`_assert_isolation(payload, request_id)` 已覆盖），而非 PK 跨表唯一性。

---

## 5. 分类与处置

```text
分类：C —— Matrix/Gate 真实失败（由测试断言缺陷在"全新数据库"上暴露）
      （不是 A：配置层全绿；不是 B 类环境噪声：属真实契约冲突）

CI failure:            Run #1 · Step 7 "Run observability matrix gate" · exit 1
Root cause:            tests/test_assistant_timeline_concurrency_db_e2e.py
                       ::test_case_e_ten_concurrent_requests 的
                       `source_ids.isdisjoint(all_source_ids)` 断言要求 source_id 跨表全局唯一，
                       与 Step 73 Source ID Contract 冲突；仅在空库（序列从 1 开始）暴露
Impact:                CI 首次运行 FAIL；Gate 行为正确（真实反映 DB suite != baseline）
                       本地开发库因序列已推进而"假绿"
Production code changed: NO
是否修改：否（Step 98 允许修改范围仅 Workflow / 本审计测试 / 本文档）
建议：                 单独起一步修正该断言（按 (source, request) 维度判定隔离，
                       不再要求跨表 PK 全局唯一）；不得通过改 baseline / 跳测试 /
                       关 DB suite / 关 residue 来"修好"CI
```

---

## 6. Workflow 静态审计（Step 98 §十六/§十二/§十三）

```text
唯一 workflow：.github/workflows/observability-matrix-gate.yml（目录内仅此 1 个文件）
唯一 job：observability-matrix-gate
trigger：push + pull_request（无 schedule / workflow_dispatch / release /
         deployment / repository_dispatch）
runner：ubuntu-latest · python 3.13（与本地解释器主次版本一致）
postgres service：pgvector/pgvector:pg16 · POSTGRES_HOST_AUTH_METHOD=trust · 5432:5432
                  · pg_isready health check
init_db：python -m backend.app.db.init_db（既有模块，不新增脚本）
matrix 命令：python scripts/run_matrix_gate.py（既有 CLI）
不吞失败：无 continue-on-error · 无 `|| true` · 无 `exit 0` · 无 `if: failure()`
安全：无 secrets.* · 无 GITHUB_TOKEN · 无 GitHub API/PR comment · 无 LLM 凭据
      · 无 permissions 块（不新增权限体系）
性能：无 actions/cache · 无 strategy/matrix 分层 · 无 retry · 无 timeout 覆盖
```

---

## 7. LLM / Network / Baseline

```text
DeepSeek calls = 0 · LLM calls = 0 · 外部 API = 0
（Workflow 无 DEEPSEEK_API_KEY / SILICONFLOW_API_KEY / OPENAI_API_KEY，无 secrets 引用）
Baseline：MATRIX_EXECUTION_BASELINE 未被刷新 / 覆写 / 重新生成
        （offline 375/356/19 · db 180/180 · matrix total 555 · status PASS · residue 0）
本次 CI 未修改 baseline；Gate 如实报 DRIFT
```

---

## 8. DB Residue

```text
本地等价复现中未出现 DB_RESIDUE_DRIFT ⇒ residue = 0
（drift 仅 DB_EXECUTION_DRIFT + MATRIX_STATUS_DRIFT，均由 1 个失败用例导致）
CI 侧 residue 无法直接读取（日志需登录）；复现结论为 0
```

---

## 9. 当前限制

* 真实 CI 日志需登录，无法逐行核对；结论由「逐步 conclusion + 本地空库等价复现」交叉印证
* 本地复现与 CI 仍存在 OS 差异（Windows vs ubuntu-latest）；本次失败点为空库 PK 冲突，与 OS 无关
* 未执行 destructive drift 验证（§九 允许仅以现有 fake 验证；既有 adapter/CLI 测试已覆盖 DRIFT→1）
* 未做性能优化、未加 cache / retry / 并行（§十四）
* 未修改失败断言（超范围），需单独授权一步修复

---

## 10. 结论

```text
GitHub Actions = FAIL（首次真实运行）
失败位置 = Step 7（Matrix Gate），前 6 步全绿
根因 = 测试断言缺陷（source_id 跨表全局唯一假设）在全新数据库上暴露
生产业务逻辑 / Gate / Adapter / Baseline / DB schema = 全部未改动
```
