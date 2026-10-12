# Phase 6 Exit Audit — EC1–EC10 逐条结论（Step 8C）

> 审计日期：2026-10-10 ｜ 分支 `phase5-1` ｜ HEAD `3747a66`（工作区含未提交的 S3–S8A 工作）
> 依据：`docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md` §26（EC1–EC10 **原文定义**，不修改）
> 配套：`docs/roadmap/Project-Recovery-Audit.md`（R1 缺口的发现来源）
> 证据口径：`【实测-今天】` = 本轮 Step 8 实际执行；`【实测-本会话】` = Phase 6 各步执行；
> `【提交证据】` = git；`【静态】` = 文件级统计。凡"提交后预期"一律**不**写作"已通过"。

---

## 1. EC 原文（ADR §26 逐字引用，不得改写）

```text
EC1  全部 IN SCOPE 项实现并通过全量离线 + 全量 DB 回归（0 破坏）
EC2  Agent Frozen 契约零修改（AST / diff 双证）
EC3  Workflow 与 Agent 命名/表/状态机零混淆（差分审计）
EC4  fail-closed 全矩阵通过（UNKNOWN / 冲突 / 越权 / 跨 project）
EC5  幂等性：resume / cancel / approve / retry 重复调用幂等（DB 实测）
EC6  崩溃恢复语义测试通过（RUNNING UNKNOWN 不自动重跑）
EC7  security 审计：Read DTO 禁入扫描 = 空；错误码白名单稳定
EC8  文档：architecture.md 更新 + Phase 6 Exit Audit + Frozen 声明
EC9  本 ADR 被批准并归档（PROPOSED → ACCEPTED）
EC10 任何 Deferred 项未被偷偷夹带（diff 审计）
```

## 2. 逐条结论表

| EC | 原始验收要求 | 当前证据 | 测试结果 | 状态 | 剩余问题 |
| -- | ------ | ---- | ---- | -- | ---- |
| EC1 | 全量离线 + 全量 DB 回归（0 破坏） | 见 §3 全部实测；治理门 5 红根因 = test_11（tracked 未提交）【实测-今天】 | 离线（排除 5 治理门）= **7275 passed / 0 failed**；DB 分片（排除同 5 门）= **8149 passed / 1 failed（结构性顺序依赖，非破坏）/ 44 skipped** | **BLOCKED** | ①5 个治理门在"未提交工作树"下必然红（语义如此）——**须在用户批准提交后重跑闭环**；②`test_db_residue_is_zero` 为结构性顺序依赖（见 §4）——需后续治理裁决（本轮不得修改既有测试） |
| EC2 | Agent Frozen 契约零修改（AST / diff 双证） | Phase 6 S1–S7.2 对 Agent 面零修改（全部测试锁定 + 本 Audit 期间 diff 核查）；**S8A 例外**：`api/agent_read.py` 增加认证依赖（仅访问控制） | Agent 契约测试全绿（P-8 集合 + S8A 新增安全用例）【实测-今天】 | **PASS（带已授权例外——冲突细节见 §6，不自行裁决）** | EC2 字面"零修改"与 S8A 的解冻在文本上存在张力；本例外由 Step 8 任务书 §4.3 显式授权（"本阶段明确允许：Agent Read API 认证依赖…"），其余 Agent 面（DTO 字段集 / 只读面形 / Runtime 语义）零变化 |
| EC3 | Workflow 与 Agent 命名/表/状态机零混淆 | 两域命名/表/状态机独立：`ai_ops.agent_*`（6 表）vs `ai_ops.workflow_*`（3 表）；差分断言（S1/S4 测试）【实测-本会话】 | workflow 全系 9 文件 + agent 相关测试全绿 | **PASS** | — |
| EC4 | fail-closed 全矩阵（UNKNOWN / 冲突 / 越权 / 跨 project） | S3（UNKNOWN 不重跑 / APPROVAL_CONFLICT）· S4（CAS 单赢 / 终态不可覆盖 / 取消三态）· S7.2（401/503/404 同构）· S8A（同构 + 未配置不回退） | 离线 + DB 用例全绿【实测-今天】 | **PASS** | — |
| EC5 | 幂等（resume / cancel / approve / retry 重复调用幂等，DB 实测） | S4/S5/S6/S7 的 DB 用例（重复裁决 / 重复恢复 / 幂等创建）【实测-本会话 + 今天分片含 workflow 组】 | 相关 DB 用例全绿（片 9/10 含 workflow 组） | **PASS** | — |
| EC6 | 崩溃恢复语义（RUNNING UNKNOWN 不自动重跑） | S4 执行权协议（attempt create 先于外部调用；未决 RUNNING ⇒ UNKNOWN fail-closed）| 离线并发测试（Barrier/Event）+ DB 用例全绿 | **PASS** | — |
| EC7 | security 审计（Read DTO 禁入扫描 = 空；错误码白名单稳定） | Workflow/Agent Read DTO 禁入键扫描（测试断言）+ 错误码白名单校验（构造期拒绝非法码） | 全绿【实测-今天：两域安全/契约测试 102 + 40】 | **PASS** | — |
| EC8 | 文档：architecture.md 更新 + Phase 6 Exit Audit + Frozen 声明 | 本文档（Exit Audit）+ `docs/architecture.md` Appendix（Agent/Workflow 索引）+ `docs/api.md` §8 + `README` 认证节 + `docs/security.md` §7 | 文档审查（人工） | **PASS** | Frozen 声明见 §5；后续文档债（README 历史段落）不阻断 |
| EC9 | ADR 批准归档（PROPOSED → ACCEPTED） | 提交 `26bc707` "docs: Phase 6 workflow runtime architecture decision (accepted)"【提交证据】 | — | **PASS** | — |
| EC10 | Deferred 未夹带（diff 审计） | 本轮全量 diff 审查：无 D1–D12 实现（无并行执行 / Scheduler / 写工具 / UI / worker 等） | — | **PASS** | — |

## 3. 回归实测记录（EC1 证据详情）

### 3.1 全量离线（RUN_DB_TESTS=0）【实测-今天】

```text
含治理门（工作区状态）：7275 passed / 5 failed / 919 skipped（235.9s；rc=1）
排除治理门（--deselect 5）：7275 passed / 0 failed / 919 skipped / 5 deselected（236.5s；rc=0）

5 个失败的根因（单一）：test_observability_http_allowlist_audit::TestBaseline::
test_11_backend_working_tree_is_unmodified 断言 `git diff --name-only -- backend`
为空——当前 backend 存在 6 个未提交 tracked 修改（config.py / init_db.py /
models/workflow_run.py / workflow_run_repository.py / main.py / api/agent_read.py）
⇒ 该门语义上"未提交即红"，非功能回归。4 个级联（offline gate suite / summary /
matrix baseline / gate）全部经由同一根因（其内部子进程跑 18 文件离线套件，
含 test_11）。证据：单跑该文件 = "1 failed, 18 passed"，失败文本逐字为
"backend 存在未提交修改: backend/app/api/agent_read.py"。
```

### 3.2 全量 DB（RUN_DB_TESTS=1；10 片执行，同一 deselect 集合）【实测-今天】

```text
片 1  :  821 passed / 2 skipped                      rc=0
片 2  :  766 passed / 1 failed（见下）/* 首跑另有 8 errors——见 §4 */  rc=1
片 3  :  820 passed / 1 skipped                      rc=0
片 4  :  750 passed / 1 skipped                      rc=0
片 5  :  704 passed / 3 skipped                      rc=0
片 6  :  586 passed / 4 skipped                      rc=0
片 7  :  692 passed / 25 skipped                     rc=0
片 8  :  887 passed / 6 skipped                      rc=0
片 9  : 1187 passed / 3 skipped                      rc=0
片 10 :  936 passed                                  rc=0
合计  : 8149 passed / 1 failed / 44 skipped（0 errors，修订口径见 §4）
```

**唯一失败**：`tests/test_assistant_trace_timeline_regression.py::TestMatrixExecutionBaseline::test_db_residue_is_zero`
——其语义为"全库四观测表总行数 = 0（且 = Step 89 baseline）"；而 `_db_residue_total()`
无任何命名空间过滤、conftest 的 outcome 残留守卫是**会话结束时**才清理——
在全量顶层顺序中，本会话早前测试写入的行此时尚未清理 ⇒ **结构性顺序依赖**（本测试自
Step 89 起即要求"运行时点零残留"，仅在其**单独跑 / 专属小集合**（Step 89 矩阵）下成立）。
证据：单独跑 = 1 passed；在片 2 顺序内 = 1 failed（非本轮引入；本轮未修改任何相关代码）。

### 3.3 DB 残留（residue）

```text
【实测-今天】清理前：llm_usage_record 0 / tool_execution_record 0 / rag_execution_record 0 /
  assistant_outcome_record **30**（created_at 全部落在 2026-10-09 10:29:33–10:31:21 UTC）
——证据链：30 行的 id 呈三组连续段（43161-43175 / 43339-43347 / 43371-43376），
与"一次被强制终止的测试会话中的三次批量写入"一致；对照执行日志，该时间窗 =
10-09 首次全量 DB 测试命令（因工具 idle-timeout 被**取消**，进程被杀 ⇒ conftest
会话守卫的 teardown 未执行）⇒ 判定为**孤儿测试数据**（非业务 / 非生产数据；
专用本地测试库）。
处置（已执行，精确 id 删除，护栏 = 数量必须 30）：deleted 30；remaining 0；
复跑 test_db_residue_is_zero ⇒ **1 passed**。
清理后残余：四观测表 **0**；workflow 三表与 agent 六表在本轮各 DB 用例后均为 0
（各套件自带前缀清理；S8A 新增 DB 用例含 residue 断言）。
```

## 4. 片 2 首跑 8 errors 的根因与闭环

```text
现象：首跑片 2 时 tests/test_assistant_trace_timeline_read_during_write.py
      出现 8 errors + 1 failed；独立单跑 = 15 passed（0 error）。
根因：首跑时数据库内存在上述 30 行孤儿数据（§3.3）——该文件的用例
      对"读取一致性"做全库断言，受污染数据影响 ⇒ errors/failed；**非顺序**，
      而是**库状态污染**。
闭环：清理孤儿数据后复跑片 2 ⇒ 该文件全部通过（8 errors 消失，7 failed→0），
      仅余 §3.2 的结构性顺序依赖 1 例。
```

## 5. Frozen 声明（EC8 组成）

```text
自本 Audit 起（HEAD 3747a66 + 工作区），以下契约冻结：
  · Agent Runtime 语义（状态机 / 决策 / Tool 授权 / Checkpoint / Resume / Replay /
    HITL / 取消）——**零修改**（S8A 例外仅限 Agent Read API 的访问控制层，
    经 Step 8 任务书 §4.3 显式授权）；
  · Workflow Domain（definition / states / policy / errors）——自 Step 1 提交后冻结；
  · Workflow Read API 认证契约（S7.2）与 Agent Read API 认证契约（S8A）：
    401 / 503 / 404 同构与"作用域仅来自服务端配置"语义冻结；
  · `ai_ops` schema（S1/S2 + GAP-1 的 input 列）——本阶段**零 schema 变更**。
任何解冻需独立任务书 + 显式授权，并在本目录留痕。
```

## 6. 冲突与不自行裁决事项（EC2 张力）

```text
EC2 原文要求"Agent Frozen 契约零修改"；Step 8 任务书 §4.3 明确授权
"Agent Read API 认证依赖 / API 层最小修改 / 必要的安全测试"。
⇒ 两者在文本层面存在张力：
   · 若按 EC2 字面（零修改）：S8A 对 api/agent_read.py 的修改属**例外**；
   · 若按安全优先（Recovery Audit R1）：该修改为**必要修复**。
本 Audit **不做裁决**：如实记录例外范围（仅认证依赖 + project_id 参数移除 +
错误契约增补 401/503；DTO 字段集 / 只读面形 / 业务语义零变化——由双份测试锁定），
并将"EC2 例外是否接受"提交用户确认（默认解释：EC2 的意图为"防止未经授权的
契约漂移"，本次修改已获显式授权且范围最小 ⇒ 不构成 EC2 违规；此解释待确认）。
```

## 7. 治理门矩阵（工作区状态 vs 提交后）

```text
【实测-今天｜工作区状态】
  test_11（backend tracked clean）           : FAIL（预期——6 个 tracked 修改未提交）
  4 个级联（offline suite / summary /
    matrix baseline / matrix gate）          : FAIL（同根因）
  其它治理门（DB suite / step74 残留等）      : DB 模式下见片 2-10（唯一失败 §3.2）

【提交后预期验证（尚未执行——不得写作已通过）】
  1) 用户批准 commit 后：`git diff --name-only -- backend` 为空 ⇒ test_11 预期恢复；
  2) 重跑全量离线 + 全量 DB（同 deselect 口径）⇒ 预期 0 failed（除 §3.2 顺序依赖，
     其需独立治理裁决）；
  3) 重跑 test_11 + 4 级联五件套 ⇒ 预期 5 passed（须实测后回填本节）。
```

## 8. Exit 总体判定

```text
EC1  = BLOCKED（治理门需提交后闭环；1 例结构性顺序依赖待治理裁决）
EC2  = PASS（已授权例外，例外细节待用户确认——§6）
EC3–EC7, EC9, EC10 = PASS
EC8  = PASS（本文档 + 文档同步完成）

Phase 6 Exit = BLOCKED（等待：用户批准提交 → 治理门复验闭环；§3.2 治理裁决）
—— 代码与测试面已达成实质完成（离线功能面 0 failed；DB 功能面 0 非顺序失败）。
```

**生产部署门禁（独立于代码 Exit，不因本 Audit 改变）**

```text
Application authentication   = PASS（应用层：Workflow 四端点 + Agent 两端点）
Project authorization        = PASS（服务端作用域绑定；客户端不可主张）
HTTPS                        = NOT VERIFIED
Network boundary             = NOT VERIFIED
PRODUCTION_DEPLOYMENT        = BLOCKED
```
