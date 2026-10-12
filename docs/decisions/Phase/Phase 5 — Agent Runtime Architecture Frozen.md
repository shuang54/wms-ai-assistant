# Phase 5 — Agent Runtime Architecture FROZEN

> 状态：**FROZEN**（冻结生效）
> 依据：`Phase 5 — Agent Runtime Exit Audit.md`（Audit COMPLETE · NO BLOCKER ·
> NO CODE CHANGE · 全部 Regression Gate PASS）
> 冻结基线 commit：`06cdebf`（分支 `phase5-1`）
> 适用范围：WMS AI Assistant — Agent Runtime（Phase 5 全部交付物）

---

## 1. 声明

```text
Phase 5 Agent Runtime = FROZEN
```

Phase 5 交付的 **Agent Runtime（模型驱动、有界、可恢复、可审批、可取消、可只读观察）**
自本文件生效起视为**冻结契约**：

* 任何**修改**（行为 / 契约 / schema / 错误码 / 语义）必须**先**新建
  Architecture Decision（`docs/decisions/Phase/…`）并获批；
* **禁止**"顺手修改"：不得在后续阶段夹带 Agent Runtime 的行为变更；
* 冻结的是**契约与语义**，不是禁止新阶段在其**之上**构建（Phase 6 Workflow
  Runtime 以既有内部 Service API 为边界，见 §6）。

---

## 2. 冻结清单（FROZEN）

| 组件 | 冻结内容 | 代码位置 |
| --- | --- | --- |
| **Agent Loop** | 单 Agent · 每轮 0/1 Tool 决策 · 有界（步数/成本/时间）· fail-closed | `agent/runtime.py`（`run` / `_run_scoped`） |
| **Decision Contract** | 严格 JSON 解析 · FINAL / TOOL_CALL · 无文本 fallback | `agent/decision.py` · `agent/llm.py` |
| **Tool Authorization** | `allowed_tools` 三重校验（装配 / Runtime / 执行边界）+ Approval 后**重新**授权 | `agent/runtime.py` · `services/tool_execution_service.py` |
| **Tool Execution boundary** | 唯一通道 `ToolExecutionService.execute`（Handler 不可直达） | `agent/runtime.py`（调用方）· `services/tool_execution_service.py` |
| **AgentRun** | 终态执行记录 · UNIQUE(run_id) · 13 字段 · 暂停期**不**写 | `db/models/agent_run.py` · `db/agent_run_repository.py` |
| **AgentStep** | 安全投影 · UNIQUE(run_id, step_index) · 无 arguments/output | `db/models/agent_step.py` · `db/agent_step_repository.py` |
| **AgentDecision** | Decision Replay Source · UNIQUE(run_id, step_index) + UNIQUE(decision_ref) | `db/models/agent_decision.py` · `db/agent_decision_repository.py` · `agent/decision_store.py` |
| **AgentToolResult** | Tool Result Replay Source · UNIQUE(run_id, step_index) · 32 KiB 内联 / 超限不可 Replay · 无 payload 读暴露 | `db/models/agent_tool_result.py` · `db/agent_tool_result_repository.py` · `agent/recorder.py`（写入点） |
| **AgentCheckpoint** | Recovery Control State · state_version CAS + claim_token · PAUSED/WAITING_APPROVAL · TERMINAL + `next_action=CANCELLED` · cancel 信号列 | `db/models/agent_checkpoint.py` · `db/agent_checkpoint_repository.py` · `agent/checkpoint.py` |
| **AgentApproval** | Human Approval State · PENDING → APPROVED/REJECTED（条件 UPDATE 单赢、终态不可逆）· 无 arguments | `db/models/agent_approval.py` · `db/agent_approval_repository.py` · `agent/approval.py` |
| **Resume** | Decision Replay + Tool Result Replay · 乐观 claim（不换 claim / 不 takeover）· 全矩阵 fail-closed | `agent/resume.py` · `agent/runtime.py::resume` · `services/ai_orchestrator_service.py::resume_agent_run` |
| **Replay** | 已完成 ⇒ Replay（LLM/Tool 0 次）；不可判定 ⇒ fail-closed；**禁止** blind rerun | 同上 · `agent/decision_store.py` |
| **Cancellation** | 协作式信号 + 安全边界 · running Tool 不强杀、结果保留 · `run.status=CANCELLED` · 终态不可逆 · 取消后禁止 Resume | `agent/cancellation.py` · `agent/runtime.py`（边界检查） |
| **Read API** | 2 个 GET 端点 · Query only（0 写入 / 0 命令）· project-scoped 归属 · 固定文案 404 | `api/agent_read.py` · `services/agent_read_service.py` · `dto/agent_read*.py` |
| **Timeline** | Read Projection（无表）· 可证明事实才有事件 · deterministic 排序 · `sequence` = 投影下标 · limit 1..100 / offset ≥ 0 | 同上 + `dto/agent_read.py::_build_timeline`（在 service） |

---

## 3. 冻结不变量（Invariants）

```text
1  AgentRuntime DB dependency          = 0（13 个内核模块 db_imports = []）
2  Tool Framework Agent dependency     = 0（tools/** + ToolExecutionService 0 agent import）
3  Conversation Agent dependency       = 0（双向 0 import）
4  Evidence dependency                 = 0（Agent 不写 Evidence；Evidence 不记执行）
5  Read API mutation                   = 0（AST 审计：无 create/update/delete/命令）
6  Timeline 持久化                     = 0（无 timeline 表 / 无 event store）
7  Resume Replay 语义                  = 已完成 ⇒ 0 次 LLM / 0 次 Tool handler；缺失 ⇒ fail-closed
8  HITL                                = Approval before Tool（暂停期 Tool = 0）；Approval ≠ Authorization
9  Cancellation                        = 协作式；不强杀；Tool Result 保留；终态不可逆
10 Project Isolation                   = project-scoped query boundary（同码 404，不泄露存在性）
11 Security Boundary                   = 禁入字段三层不暴露（DTO / mapper / response_model）
12 错误码 / 状态                        = 稳定码 + 明确状态；CANCELLED ≠ FAILED；WAITING_APPROVAL ≠ FAILED
```

---

## 4. 冻结校验（如何验证仍是 FROZEN）

```powershell
python -m compileall -q backend tests scripts                 # 0 errors
python -m pytest -q                                           # 全量离线（6702 passed）
$env:RUN_DB_TESTS="1"; python -m pytest -q                    # 全量 DB（7511 passed / 3 既有）
git --no-pager diff --check                                   # PASS
```

专项（Phase 5 Gate）：

```powershell
python -m pytest -q tests/test_agent_runtime.py tests/test_agent_decision_store.py `
    tests/test_agent_resume.py tests/test_agent_resume_integration.py `
    tests/test_agent_hitl.py tests/test_agent_hitl_cancellation_race.py `
    tests/test_agent_cancellation.py tests/test_agent_read_service.py `
    tests/test_agent_timeline.py tests/test_agent_read_api.py
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_agent_hitl_db.py `
    tests/test_agent_cancellation_db.py tests/test_agent_read_db.py
```

---

## 5. 变更程序（Change Procedure）

```text
1  新建 Architecture Decision（含：为什么冻结契约必须变 / 影响面 / 迁移 / 测试策略）
2  评审批准（用户明确下达）
3  最小实现 + 最小测试 + 更新本文件（FROZEN → 修订说明，保留历史）
4  全量离线 + 全量 DB + compileall + residue = 0
5  禁止：顺手重构 / 顺手优化 / 顺手抽象 / 顺带新增能力
```

---

## 6. Phase 6 边界（Workflow Runtime）

```text
Phase 5（FROZEN，模型驱动）
    LLM decides → Tool → LLM decides → Tool → … → FINAL

Phase 6（Workflow Runtime，开发者定义）
    deterministic business flow · branch / condition · approval gate ·
    parallelism · retry policy · scheduled execution · long-running workflow
```

规则：

* Phase 6 **只能**通过既有内部 Service API 触发 Agent（
  `AIOrchestratorService.execute` / `resume_agent_run` / `resolve_agent_approval` /
  `cancel_agent_run`），**不得**绕过 Runtime 直接写 6 张表；
* Phase 6 **不得**修改 Agent Loop 语义、Decision Contract、6 张表 schema、
  错误码语义、Read API / Timeline 语义；
* Workflow 的 retry / 并行 / 补偿**不得**下沉进 Agent Runtime；
* 若 Phase 6 需要新的 Agent 能力 ⇒ 走 §5 变更程序（新建 ADR），不能夹带。

---

## 7. 架构状态（FROZEN）

```text
AgentRun                 FROZEN
AgentStep                FROZEN
AgentDecision            FROZEN
AgentToolResult          FROZEN
AgentCheckpoint          FROZEN
AgentApproval            FROZEN
Resume                   FROZEN
Replay                   FROZEN
HITL                     FROZEN
Cancellation             FROZEN
Agent Read API           FROZEN
Agent Timeline           FROZEN
```

Deferred 项见 `Phase 5 — Agent Runtime Exit Audit.md` §12（非 Phase 5 blocker）。

---

## 8. STOP

Phase 5 至此收口。**不**自动进入 Agent UI / Streaming / Workflow Runtime /
Multi-Agent / LangGraph / MCP；下一阶段必须重新进行 Architecture Decision。
