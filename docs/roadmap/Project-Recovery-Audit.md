# Project Recovery Audit — 全项目进度核实与后续技术路线规划

> 审计日期：2026-10-09
> 审计分支：`phase5-1` · HEAD `3747a66`
> 审计性质：**只读审计**（唯一新增物 = 本报告）；未修改任何业务逻辑 / Schema / 测试 / baseline；未 commit / push。
> 证据标注约定：
> `【实测】` = 本次审计实际执行的命令与结果；`【静态】` = 本次审计静态统计（文件计数等）；
> `【历史声称】` = 仅存于既有文档/报告的数字，本次未复核；`【提交证据】` = Git 提交记录。

---

## 1. 审计范围与方法

```text
范围：backend/app（233 py）/ tests（306 test_*.py + conftest）/ scripts（15 py）/
      docs（566 md：decisions 386 + evaluation 173 + security 1 + 顶层 5）/
      .github/workflows / pytest.ini / docker-compose.yml / .env.example / AGENTS.md
方法：
  ① 全量离线测试实测（RUN_DB_TESTS=0）——审计核心证据；
  ② Git 历史（247 commits）与工作区分层核对；
  ③ 三个并行代码侦察（Phase 3.x 基础层 / Phase 5 Agent / 测试门控体系），逐文件核对入口符号；
  ④ 文档分组盘点（按 Phase 前缀统计 + 空文件扫描）+ 关键文档抽样（ADR / Exit Audit / 任务书）；
  ⑤ 文档与代码一致性抽查（architecture.md / api.md / README 同步状态）。
未执行（如实声明）：全量 DB 回归（RUN_DB_TESTS=1）、真实 LLM / Embedding / Reranker 调用、
  部署环境验证（HTTPS / 网关 / 网络边界）。上述"未执行"项在文中一律标 NOT_VERIFIED。
```

## 2. 当前 Git / 工作区状态

```text
Current branch   : phase5-1
HEAD commit      : 3747a66 "Phase 6 Step"
Commits total    : 247 【实测 git rev-list --count】
git diff --check : PASS（无空白/冲突标记）【实测】

Modified tracked (10 —— 全部未提交):
  .env.example · backend/app/config.py · docs/security.md
  backend/app/db/init_db.py · backend/app/db/models/workflow_run.py
  backend/app/db/workflow_run_repository.py · backend/app/main.py
  tests/test_workflow_persistence.py · tests/test_workflow_persistence_db.py
  （合计 268 insertions / 1 deletion 【实测 diff --stat】）

Untracked（26 项，其中 7 项为 docs 任务书副本）:
  代码：api/workflow_read.py · api/workflow_read_auth.py · dto/workflow_read_api.py
        workflow_runtime/{adapters,factory,read_factory,read_service,store_adapters}.py
  测试：test_workflow_runtime_{adapters,factory,factory_db,read_service,read_service_db,
        store_adapters}.py · test_workflow_read_api{,_db,_security_audit}.py
  文档：docs/security/（S7.1 审计）· docs/decisions/Phase/ 下 7 份任务书副本
  （workflow_runtime 包的 S1/S2/S3/S4 四文件与 4 个测试此前已随 3747a66 前的工作进入仓库？——
   核对：workflow_runtime/{__init__,ports,binding,execution,runtime}.py 未在 untracked 清单 ⇒
   已包含于 3747a66 提交。）
```

**未提交工作的性质**（来源 = `docs/decisions/Phase/Phase 6 Step 5 — GAP-1, GAP-2 最小修复与验收.md`【静态】）：

```text
GAP-1：workflow_run 增加可空 input 列（模型 + Repository 校验 + init_db 幂等补列 + 持久化测试）
GAP-2：新增 store_adapters.py（3 个 Store Adapter）+ factory 接线 + adapter 测试
S6  ：read_service / read_factory / 读层测试（本会话实现，S6 任务书）
S7  ：read API / DTO / 契约测试（同上）
S7.1：安全审计报告（docs/security/）
S7.2：config WorkflowReadSettings + workflow_read_auth.py + 四端点认证 + 测试改造 + 文档
```

## 3. 全项目阶段清单（按提交证据重建）

```text
Phase 1       工程基础（FastAPI/健康检查/Chat 骨架）          【提交证据 + 全量离线绿】
Phase 2       LLM 客户端（OpenAI 兼容 + Mock 回退）/ 异常/日志  【提交证据】
Phase 3.1-3.5 PostgreSQL+pgvector / 入库流水线 / 向量检索 / 最小 RAG / RAG API / Chat+RAG
Phase 3.5.6-8 RAG 评估 baseline / 首份真实 WMS 知识库（CLI）    【提交证据】
Phase 3.6     Tool Calling 框架（Registry/Definition/执行）+ 权限
Phase 3.7     Project Context / Reranker / Text-to-SQL / 真实工具（inventory, work_order）
Phase 3.8     （对话/工具观测补强线）
Phase 3.9     Text-to-SQL 评测线（24 个测试文件 + 20 个基线 JSON + 15 个 scripts）【提交证据】
Phase 3.10    （E2E/评估/证据线，22 个 Step 文档）
Phase 3.11    工具观测持久化边界线（33 份文档）
Phase 3.12    CI 治理线（86 份文档：matrix gate + GitHub Actions + Required Check）【提交证据】
Phase 4.1     Conversation/Evidence 基础（Step 1-51；PR #8；Step 51 Merge Closeout）【提交证据】
Phase 4.2     Conversation Runtime 基础（幂等/事务/上下文选择 7A-7H；PR #10；7H closeout）【提交证据】
Phase 5       Agent Runtime（P-1..P-8 + S-1；28 份文档；Exit Audit；a50869c）【提交证据】
Phase 6 Step1 Workflow Domain 模型（72808d1）                  【提交证据】
Phase 6 Step2 Workflow Persistence（d9867ac）+ ADR（26bc707）  【提交证据】
Phase 6 S3-S4 NodeExecutor / WorkflowRuntime（3747a66 范围）   【提交证据】
Phase 6 GAP修复 / S5 / S6 / S7 / 7.1 / 7.2                    【未提交（工作区）】
部署 / 业务验收（HTTPS、网关、真实数据权限）                     NOT VERIFIED / NOT STARTED
```

**编号体系说明（文档债）**：Phase 6 ADR §24 使用能力编号 `S1..S12`（S5=Retry、S6=HITL、S8=Project Isolation、S9=Read…），而实际执行的任务书使用 `S1=Ports、S2=Binding、S3=Execution、S4=Runtime、S5=Factory、S6=Read Layer、S7=Read API` 的另一套编号。两套 `S` 编号并存于仓库，属**命名不一致**（建议在 Exit Audit 中做一次映射表，不重写历史文档）。

## 4. 统一能力矩阵

状态口径：`VERIFIED_COMPLETE` / `IMPLEMENTED_TESTED` / `IMPLEMENTED_UNVERIFIED` / `PARTIAL` / `BLOCKED` / `NOT_STARTED` / `NOT_VERIFIED`。

### A. 基础 AI 应用能力（Phase 3.x）

| 能力 | 状态 | 代码证据 | 测试证据 | 未满足验收 | 建议动作 |
|---|---|---|---|---|---|
| LLM Provider（含 Mock 回退） | VERIFIED_COMPLETE | `llm/client.py`（Mock/OpenAI 兼容/create 工厂） | `test_llm_client.py` 等 30 文件；本轮全量绿 【实测】 | — | 保持 |
| Embedding Provider | IMPLEMENTED_TESTED | `embedding/client.py` | 42 用例 + 真实链路 1 用例（默认 skip） | 真实 Embedding 闭环仅 skip 证据 | 低优先：纳入下次真实链路批次 |
| Knowledge Ingestion | VERIFIED_COMPLETE | `services/knowledge_ingestion_service.py` + CLI | 13 + 50（chunking）等；DB 部分门控 | — | 保持 |
| RAG / 检索 / Reranker | IMPLEMENTED_TESTED | `rag_service.py` / `vector_search_service.py` / `reranker/client.py` | 17+14+26+21…；真实 E2E 6 用例默认 skip | 真实 RAG E2E 仅 skip 证据（RERANKER_ENABLED 默认 false） | 低优先 |
| Tool Registry / Execution / 权限 | IMPLEMENTED_TESTED | `tools/registry.py` / `services/tool_execution_service.py` / `projects/capabilities.py` | 57+25+27…；真实工具仅 2 个 | 权限为项目级白名单，无 per-tool scope | 随业务需求扩展 |
| Router / Orchestrator（含 Tool Calling） | IMPLEMENTED_TESTED | `services/ai_router_service.py` / `ai_orchestrator_service.py`（execute/resume/cancel/resolve） | 53+45+39+17 | 多步 Tool Calling 真实闭环仅 1 个 skip smoke | 低优先 |
| Project Context / Registry | VERIFIED_COMPLETE | `projects/*`（registry/context/capabilities） | 23+18+27…；隔离测试部分 DB 门控 | — | 保持 |
| Text-to-SQL（生成/校验/只读执行） | IMPLEMENTED_TESTED | `text_to_sql_service.py` / `sql_validator_service.py` / `sql_executor_service.py` | 24 个相关文件；安全 E2E 11+13；真实 LLM 基线默认 skip | 真实模型 SQL 质量仅 skip 证据 | 低优先 |
| Evaluation / Regression | IMPLEMENTED_TESTED | `scripts/*`（15）+ 评测服务；20 个基线 JSON | 7 个评测测试文件 + matrix gate | 全量回归未纳入 CI（见 §6） | 收口后接入 CI |

### B. Agent Runtime（Phase 5）

| 能力 | 状态 | 代码证据 | 测试证据 | 缺口 | 建议动作 |
|---|---|---|---|---|---|
| Run/Step/Decision 持久化 | VERIFIED_COMPLETE | `db/models/agent_*` + repositories + `agent/recorder.py` | 22 文件约 603 用例；DB 专项 10 文件 | —（DB 数字为历史声称） | 保持 |
| Checkpoint / Resume | VERIFIED_COMPLETE | `agent/checkpoint.py` / `runtime.py:resume/_claim_for_resume` | `test_agent_resume*.py` 等 | — | 保持 |
| Tool 授权 / 执行 / Result 持久化 / Replay | VERIFIED_COMPLETE | `runtime.py:_validate_allowed_tools/_record_tool_result/_replay_*` | `test_agent_tool_result_*` | — | 保持 |
| HITL / Cancellation | VERIFIED_COMPLETE | `agent/approval.py` / `cancellation.py` | `test_agent_hitl*.py` / `test_agent_cancellation*.py` | — | 保持 |
| **Agent Read API 访问控制** | **PARTIAL（安全缺口）** | `api/agent_read.py`（2 端点，**无任何认证依赖**；`project_id` 为客户端 Query，缺省回退 settings） | `test_agent_read_api.py` 仅锁"只读"；**零 401/凭据断言** | 未认证可读 + 任意 project_id 主张（= S7.1 审计 T1/T2，Agent 侧未修复）；Phase 5 Exit Audit 未记入 | **最高优先修复（见 §11 Step 8）** |

### C. Workflow Runtime（Phase 6）

| 能力 | 状态 | 代码证据 | 测试证据 | 缺口 |
|---|---|---|---|---|
| Domain / States / Policy | VERIFIED_COMPLETE（已提交） | `workflow/{definition,states,policy,models,errors,registry}.py` | `test_workflow_*.py` 全绿【实测】 | — |
| Persistence（+GAP-1 input） | IMPLEMENTED_TESTED（未提交） | `db/models/workflow_run.py` / `workflow_*_repository.py` / `init_db.py` | `test_workflow_persistence{,_db}.py`（离线绿；DB 本轮未复核） | 未提交 |
| Ports / Binding / NodeExecutor / Runtime | IMPLEMENTED_TESTED（已提交至 3747a66） | `workflow_runtime/{ports,binding,execution,runtime}.py` | 61（runtime）+32（execution）+58+38（S1/S2）全绿【实测】 | 未 Exit |
| GAP-2 Store Adapters + Factory | IMPLEMENTED_TESTED（未提交） | `workflow_runtime/{store_adapters,factory}.py` | `test_workflow_runtime_{store_adapters,factory,factory_db}*` | 未提交 |
| Read Service / Timeline | IMPLEMENTED_TESTED（未提交） | `workflow_runtime/{read_service,read_factory}.py` | 28 离线 + 7 DB（本会话实测绿） | 未提交 |
| Read API（含 S7.2 认证） | IMPLEMENTED_TESTED（未提交） | `api/workflow_read.py` + `workflow_read_auth.py` + DTO | 25+29 离线 + 8 DB（本会话实测绿） | 未提交；未 Exit |
| **Workflow 命令面（start/advance/cancel/approve HTTP）** | **NOT_STARTED** | 仅 Runtime 进程内 API；无 HTTP 命令端点 | — | HTTP 无法启动/推进 Run（见 §11 P2） |
| Phase 6 Exit（EC1-EC10） | **PARTIAL** | ADR §26 | 见 §6（5 个治理门红 = EC1 未达） | EC8 文档 / 全量 DB 回归 / 提交 / Exit Audit |

### D. 企业级治理与交付

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| Workflow Read API 认证 + 项目授权 | IMPLEMENTED_TESTED | S7.2（本会话）：401/503/恒定时间/404 同构；25 离线 + 8 真链 DB 实测绿 | 未提交；未部署验证 |
| Agent Read API 认证 | **BLOCKED（缺口确认）** | 见 B 表 | 对称修复缺失 |
| HTTPS / 网络边界 | NOT_VERIFIED | 仓库无网关/TLS/反代配置；`TestDeploymentBoundaryEvidence` 保留断言 | 部署环境证据缺失 |
| 审计 / 可观测 / CI | PARTIAL | Phase 3.12：matrix gate CI + trace/timeline；**CI 不跑 pytest** | 回归未进 CI |
| 密钥管理 | PARTIAL | `.env` + `.env.example` 占位约定；S7.2 无默认凭据 | 无轮换/无 secret store |
| 业务交付（真实 WMS 数据/权限验收） | PARTIAL / NOT_VERIFIED | 4.1 证据基础已建；真实标注执行未见执行证据（Step 33 为任务书） | 业务验收未开始 |

## 5. 关键调用链与架构边界（追踪实证）

```text
对话链（HTTP）：main.py → api/{chat,rag,orchestrator_chat,conversation} 
  → AIOrchestratorService.execute → AIRouterService.route
      ├─ RAG   → RagService（VectorSearch → Reranker(可选) → Context → LLM）
      ├─ TOOL  → ToolExecutionService（capabilities 校验）→ Registry → Handler
      ├─ T2S   → TextToSQLService → SQLValidator → SQLExecutor（只读事务）
      └─ AGENT → AgentRuntime（bounded loop；经 build_orchestrator_for_project(agent_enabled=True)）
                  └ 生产唯一启用点 = workflow_runtime/factory.py（ai node）——HTTP chat 默认不启用 Agent
Workflow 链：api/workflow_read.py（4 GET，X-API-Key）
  → read_factory → WorkflowReadService → store_adapters → workflow_*_repository → ai_ops.*
  Runtime 链（进程内）：WorkflowRuntime.start/advance → NodeExecutor → {(binding),
      ToolExecutionPort(→ ToolExecutionService), OrchestratorPort(→ AIOrchestratorService)}
边界实证：workflow_read.py AST 禁入 db/services/tools/agent（测试断言）；
      workflow_read_auth.py 仅 stdlib+fastapi+config；读层零写路径（只读面形测试锁定）。
```

**递归/越权路径检查**：Workflow → Agent 仅经 `OrchestratorPort.execute/resume`（不触 Agent ORM）；Workflow → Tool 仅经 `ToolExecutionPort`（不触 Handler）；Workflow 读层与命令层分离（读 API 无写方法）。**未发现**未经授权的递归调用（AST 断言 + 端点只读面形测试）。

## 6. 测试证据与缺口

### 6.1 本轮实测（唯一可作为"当前可确认"的数字）

```text
【实测】python -m pytest -q（RUN_DB_TESTS=0，全量离线；2026-10-09）
结果：7257 passed / 5 failed / 915 skipped，219.46s（rc=1）

5 个失败全部为"工作区未提交"治理门及其级联（非功能回归）：
  × test_observability_http_allowlist_audit.py::test_11_backend_working_tree_is_unmodified
    （直接断言 tracked 工作树未被修改——当前 10 个 tracked 文件被修改 ⇒ 必红）
  × test_assistant_trace_timeline_regression.py::TestRegressionSuite::test_offline_gate_suite_is_green
  × …::TestOfflineRegressionExecutionSummary::test_real_offline_suite_execution_summary
  × …::TestMatrixExecutionBaseline::test_baseline_matches_step89_actual_execution
  × …::TestMatrixBaselineGate::test_gate_passes_when_current_matches_baseline
     （4 个级联门；其"tracked 干净时全绿"已在 Phase 6 S3/S4 期间实测验证过 5 passed）
  ⚠ 保留不确定性：其中 baseline 数字类断言若为**固定值**，则 S3-S7.2 新增的
    555 个测试（7257-6702）即使在提交后也可能导致 baseline 漂移 ⇒
    收口时必须重跑并（如需要）按治理流程更新 baseline，不得掩盖。
```

### 6.2 历史声称（未复核，仅记录）

```text
Phase 5 Exit Audit【历史声称】：Agent 离线专项 636 passed；Agent DB 专项 98 passed（7 文件）；
  全量离线 6702 passed / 856 skipped；全量 DB 7511 passed / 3 failed（既有 order-dependent）。
与本轮差值：7257-6702 = 555（Phase 6 S3-S7.2 新增测试），量级自洽。
```

### 6.3 测试基础设施（【静态】）

```text
规模：306 个 test_*.py（workflow 19 / agent 28 / rag 14 / tool 36 / text_to_sql 22 /
      llm 30 / project 12 / 其它 145）；conftest 仅 1 个（3 个 session autouse 残留守卫）。
门控：全部 RUN_* 默认 off（15 个开关）；`real_llm` marker 仅 1 处使用。
DB 门控：≈120-164 个文件引用 RUN_DB_TESTS（本轮 915 skipped 的主要来源）。
风险点：
  ① pytest.ini 无 timeout（真实开关误开会长时间挂起而非失败退出）；
  ② conftest 残留守卫只认 DATABASE_URL（不认 RUN_DB_TESTS）——若 .env 配了库，
     默认"离线"全量跑仍会连库并 DELETE 本会话新增行（保守，但确有 DB 交互）；
  ③ RUN_DB_TESTS 解析不统一（bool-like 集合 vs 严格 != "1"）；
  ④ CI（唯一 workflow = observability-matrix-gate.yml）**不跑 pytest**——回归全靠本地手工。
```

### 6.4 覆盖缺口清单

```text
未验证：全量 DB 回归（915 skipped 中的 DB 组本轮全部未跑）；真实 LLM/Embedding/RAG E2E（默认 skip）；
        Agent Read API 安全（无认证用例）；Workflow 命令面（不存在）。
弱证据：Embedding 真实链路 1 用例；多步 Tool Calling 真实闭环 1 用例；真实 RAG E2E 全 skip。
```

## 7. 安全风险与部署门禁

```text
Application authentication = PASS【本会话实测】（Workflow 四端点；401/503/恒定时间）
Project authorization      = PASS【本会话实测】（服务端作用域；客户端不可主张/扩宽）
HTTPS                      = NOT VERIFIED（无仓库/环境证据）
Network boundary           = NOT VERIFIED（无网关/反代/TLS 配置；APP_HOST 默认 0.0.0.0 启动路径存在）
PRODUCTION_DEPLOYMENT      = BLOCKED
```

**风险排序（本轮审计新增确认）**

```text
R1 ★★★ Agent Read API（GET /api/agent/runs/{id}(+/timeline)）无任何认证，
   且 project_id 由客户端 Query 声明（缺省回退 settings）——S7.1 审计 T1/T2 在 Agent 侧
   **仍然存在**；S7.2 修复未对称覆盖；Phase 5 Exit Audit 未记入此缺口（其 Gate 表中
   "Security Boundary PASS" 仅指字段泄露扫描，不含访问控制）。
R2 ★★  全量 DB 回归未跑（915 skipped）——Workflow/Agent 的 CAS、唯一约束、幂等、
   UNKNOWN 语义的 DB 级证据本轮未复核（S5/S6/S7.2 各自的 DB 子集曾绿）。
R3 ★★  治理门处于红灯（tracked 未提交）——EC1 未达成；baseline 漂移风险（见 §6.1）。
R4 ★   文档/CI 债务：architecture.md、api.md、README 均未同步 Phase 4.2/5/6；
   CI 不跑 pytest；无 timeout；2 份 0 字节文档 + 3 份 0 字节顶层 ADR
   （001-use-python / 002-use-postgresql-pgvector / 004-use-tool-calling）。
R5 ★   Workflow 无 HTTP 命令面（start/advance/cancel/approve）——当前只能进程内驱动，
   属"能力可用性缺口"而非安全缺口。
```

## 8. 未提交工作与技术债务

```text
未提交（详见 §2）：Phase 6 Step 5 GAP-1/GAP-2 修复 + S6 读层 + S7 读 API + S7.1 审计 + S7.2 安全修复
  （10 tracked 修改 + 19 个 untracked 代码/测试文件）——**不是已发布版本**，不得视为正式版。
技术债务清单：
  T-1  Phase 6 Exit Criteria（ADR §26）EC1/EC8/EC9 等未完成（无 Exit Audit 文档；文档未同步）。
  T-2  Phase 6 两套 S 编号并存（ADR S1-S12 vs 任务书 S1-S7.2）——需映射表消歧。
  T-3  workflow_runtime 无 `__init__` 导出策略文档（当前直接按模块导入）。
  T-4  NodeExecutionOutcome / RunOutcome 暂居 execution.py / runtime.py（S3/S4 提出的 U-1 未裁决）。
  T-5  docs/decisions/Phase 下任务书副本与正式报告混放（7 份 untracked 任务书按治理规则不提交）。
  T-6  重复/过期文档：README 停在 Phase 3.5.8；docs/api.md 未含 Agent/Workflow 端点；
      architecture.md Workflow 提及 1 词、无 workflow-runs/X-API-Key。
  T-7  空文档 5 份（Phase 3.11 Step 20 / Phase 4.1 Step 9 / decisions 001·002·004）——
      仅列出建议（补写或归档），本阶段不实施清理。
  T-8  测试门控/超时/CI 三项基础设施债（§6.3 ①②③④）。
```

## 9. 距离最终目标的差距（四层）

```text
A 基础 AI 应用能力
  已具备：9 项全实现 + 双证据（LLM/Embedding/Ingestion/RAG/Reranker/Tool/Router/Orchestrator/
          Project/T2S/Evaluation）【静态+实测】
  未具备：—（技术面完整）
  未验证：真实外部链路（Embedding/RAG E2E/多步 Tool 的真实闭环）默认 skip
  阻塞项：无
  必要依赖：下一次真实链路验证批次（可选）

B Agent 与 Workflow Runtime
  已具备：Agent 全生命周期（P-1..P-8）；Workflow S1-S7.2（含读 API + 认证）
  未具备：Workflow HTTP 命令面；Agent Read API 访问控制
  未验证：全量 DB 回归；Workflow Exit（EC1-EC10）
  阻塞项：EC1（治理门红）→ 待提交收口
  必要依赖：Phase 6 收口 + 安全对称修复

C 企业级治理与可靠性
  已具备：只读边界/字段过滤/固定文案/DB-gated 残留守卫/CI matrix gate/单凭据认证（workflow）
  未具备：Agent 认证；secret 轮换；审计日志平台；全量回归进 CI；timeout 防护
  未验证：HTTPS；网络边界；生产部署链路
  阻塞项：PRODUCTION_DEPLOYMENT（HTTPS / 网络边界）
  必要依赖：部署环境 + 运维证据（仓库外）

D 真实业务交付
  已具备：证据/标注/评审契约（4.1）；真实工具 2 个；真实知识库 1 份
  未具备：真实 WMS 数据端到端验收；权限体系验收；运维手册；可重复发布流程
  未验证：业务正确性（真实数据/真实标注执行）
  阻塞项：C 层部署门禁 + 业务方数据/权限
  必要依赖：C 完成后启动

总体完成率：**拒绝给出单一百分比**——四层进度不可通约且 D 层依赖仓库外条件；
如必须量化：仅可报告可验证代理指标（如"已提交阶段数 / 计划阶段数"），本报告不采用。
```

## 10. 后续阶段依赖图

```text
        ┌─────────────────────────────────────────────┐
        │ P1  Phase 6 Step 8 — Security Symmetry       │
        │     & Phase 6 Closure（★唯一推荐，见 §11）    │
        └───────────────┬─────────────────────────────┘
                        │（提交冻结 + EC 达成 + Agent 认证对称）
        ┌───────────────▼─────────────┐
        │ P2  Workflow 命令面（HTTP）  │   start/advance/cancel/approve + 认证
        └───────────────┬─────────────┘
                        │（Workflow 才真正"可被应用调用"）
        ┌───────────────▼─────────────┐   ┌──────────────────────────────┐
        │ P3  真实链路验证批次（可选，  │   │ P4  部署与生产门禁验证        │
        │     与 P2 并行不冲突）        │   │（HTTPS/网关/网络边界；依赖   │
        └───────────────┬─────────────┘   │  仓库外运维环境 —— 可与 P2/P3 │
                        │                 │  并行准备，但验收在部署侧）    │
        ┌───────────────▼─────────────────┴──────────────┐
        │ P5  真实业务验收（WMS 数据/权限/运维手册/发布） │
        └────────────────────────────────────────────────┘
前置约束：P1 未完成前不得启动 P2；P4 无环境证据持续 NOT_VERIFIED；
禁止项（Deferred，ADR D1-D12 + AGENTS.md）：并行执行 / Scheduler / Backoff /
节点超时强制 / 写工具 / MCP / LangGraph / Multi-Agent / Workflow UI 或 Streaming。
```

## 11. 唯一推荐的下一阶段

### ★ Phase 6 Step 8 — Agent Read Security Symmetry & Phase 6 Exit Closure

**选择依据**：① 原则 1（安全优先）——R1 是**已确认的活缺口**（Agent Read API 未认证）；
② 原则 7（先完成当前阶段验收）——Phase 6 EC1/EC8/EC9 未达，且工作区已积累 19 个未提交文件，
必须先收口才能安全进入任何新开发；③ 两件事共享同一批文件与验证回路，**一次提交、一次全量回归**
最经济且避免"半开状态"延长；④ 不引入任何新框架/新能力（纯收口 + 已有认证模式扩展）。

```text
阶段名称：Phase 6 Step 8 — Agent Read Security Symmetry & Phase 6 Exit Closure
目标：
  8A 安全对称：为 Agent Read API 两个只读端点引入与 S7.2 相同的最小认证
     （显式"安全例外解冻声明"——因 Agent Read API 属 Phase 5 Frozen 面，EC2 要求零修改，
      必须在任务书中明确本次解冻的范围=仅访问控制，其余契约不动）；
  8B 全量回归：全量离线（0 失败目标）+ 全量 DB（RUN_DB_TESTS=1）+ 治理门五件套恢复验证；
  8C 文档收口（EC8）：architecture.md / api.md 增补 Agent/Workflow 章节 + Phase 6 Exit Audit
     （含 S 编号映射表 T-2、Known Gap 记录、baseline 处置记录）；
  8D 提交收口：将 Phase 6 全部工作（含本步）按治理流程提交（用户批准后）。
解决的问题：R1（Agent 无认证）、R2（DB 未复核）、R3（EC1/治理门红）、T-1（无 Exit Audit）、
          T-2/T-6 的收口部分。
前置依赖：用户对"解冻 Agent Read API + 提交"的明确授权；专用测试库就绪。
允许修改的模块：backend/app/api/agent_read.py（仅加依赖）、config.py（复用/新增只读凭据配置）、
  agent_read 相关测试（新增安全用例）、docs/（architecture/api/Exit Audit）。
禁止修改的模块：Agent Runtime 状态机 / Agent 持久化 / Workflow Runtime 核心 /
  Read Service 业务语义 / Schema / 其他业务 API。
验收标准：
  · 无凭据/错凭据 ⇒ 401；未配置 ⇒ 503（fail closed）；跨项目 ⇒ 404 同构；
  · Agent 其余契约零变化（AST/diff 双证：字段集/只读面形/无写路径）；
  · 全量离线 0 failed（治理门绿）；全量 DB 0 破坏（含 CAS/唯一约束/幂等/UNKNOWN 语义）；
  · Exit Audit 文档落盘且 EC1-EC10 逐条结论（含未达项如实标注）。
测试要求：新增 Agent Read API 安全测试（对称复用 S7.2 用例结构）；全量离线 + 全量 DB；
  上述两轮结果原样录入 Exit Audit（禁止用 Fake 掩盖）。
完成后的交付物：认证后的 Agent Read API；Phase 6 Exit Audit；architecture.md/api.md 增补；
  一次治理合规的提交；治理门五件套实测绿。
```

## 12. 不应继续开发的重复或过早功能

```text
D1 并行执行 / D2 Scheduler / D3 Backoff 重试 / D4 节点超时强制（触发条件未出现）
D5 Tool Classification / D6 写工具（第一个 write/external tool 需求出现前）
D7 project-level agent_enabled / D8 对话触发 Workflow / D9 Evidence 关联
D10 Workflow UI / Streaming / D11 worker 执行驱动 / D12 Approval TTL（运营需求前）
MCP / LangGraph / Multi-Agent / 新 Checkpoint 表 / 新 DI 框架 / JWT 签发系统 / RBAC 后台
（依据：AGENTS.md 原则 + Phase 6 ADR §25 Deferred + 本审计确认的无触发条件）
```

---

*本报告为只读审计产物；所有"未执行/未验证"项如实标注，不代表已通过。*
*下一步动作需用户明确授权后方可开始（不自动进入 Step 8）。*
