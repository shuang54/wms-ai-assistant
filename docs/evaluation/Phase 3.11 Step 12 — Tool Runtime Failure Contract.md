# Phase 3.11 Step 12 — Tool Runtime Reliability & Failure Contract

> 目标：验证 Tool Runtime 在**正常 / 拒绝 / 异常 / 边界**情况下具有稳定且明确的
> 失败契约；失败时**不绕过执行边界、不静默降级、不错误重试、不泄露内部信息**。
>
> 本阶段**未新增生产代码**（核心 Tool / 执行边界 / Registry / API / DB schema
> 全部零修改），只新增失败契约测试 + 架构契约 C14 + 本文档。

---

## 1. Scope

```text
Real Tool       = get_inventory（唯一真实 Tool；get_work_order = DEFERRED）
Real DB         = PostgreSQL（隔离 fixture schema inventory_tool_test；READ ONLY）
Fake LLM        = ScriptedLLMClient（tests/test_tool_chat_service.py）
Real LLM        = NO
New production code = 0（仅 tests / docs）
```

新增测试文件：`tests/test_tool_runtime_failure_contract.py`（36 tests：29 非 DB + 7 DB-gated）
架构契约新增：`tests/test_tool_chat_architecture_contract.py::TestC14ToolFailureBoundary`（8 tests）

---

## 2. Failure Matrix

```text
Case                    触发                                  真实结果                                  判定
A  Capability denied    project 白名单不含 get_inventory       AIOrchestratorCapabilityError（穿透）      PASS
                        （HTTP: 403；Handler 0 / SQL 0 / 不继续 LLM 下一轮）
B  Unknown Tool         LLM 调用 "unknown_tool"                ToolResult(False, "Tool 未注册: …")        PASS
                        （不猜 / 不 fallback / Handler 0 / 不改 ToolCall.name）
C  Missing required     {"material_code": 缺失}                Registry reject（missing required field）  PASS
D  Unknown argument     + "fake_field"                         Registry reject（unknown field，不静默删）  PASS
E  Invalid type         material_code=123                      Registry reject（expected string, got int）PASS
F  Handler validation   "' OR 1=1 --"（Schema PASS，Handler 拒） ToolValidationError → ToolResult(False)    PASS
                        （SQL 0；原始输入不回显）
G  DB failure           连接失败 Engine（stub，0 网络）         ToolResult(False, "[RuntimeError] …"）     PASS
                        （cause_type only；无连接串/password；connect 恰好 1 次 = 无 retry）
H  Failure + Multi-Step 失败 → 成功（真实 DB）                  ToolResult(False) → 下一轮 → 成功 1 条 SELECT PASS
I  Budget boundary      max_rounds = 1 / 20                    执行 == 预算；LLM == 预算 + 1；原错误      PASS
                        （ToolCallingBudgetExceededError；无 retry / 无多余 LLM）
J  Multiple ToolCall    一轮 2 个 ToolCall                     MultipleToolCallsError（任何执行之前）    PASS
K  Malformed ToolCall   invalid JSON / 非 object / 缺 id / >1   LLMToolCallFormatError（真实 parser）      PASS
                        （无 Tool 执行 / 无 DB / 不重试 LLM）
M  Real DB happy path   project-a + MAT-001 → 250.0            SELECT only + READ ONLY + timeout +        PASS
                        绑定参数 + rollback
L  Error sanitization   上述所有失败面                        无 API key / password / DATABASE_URL /     PASS
                        connection string / Authorization / traceback
```

```text
Result 汇总：A/B/C/D/E/F/G/H/I/J/K/M/L = PASS（无 SKIP；DB failure 采用
现有可注入点：Handler 的 engine 参数（生产注入点，非新增机制），0 网络）
```

---

## 3. Execution Boundary

```text
LLM Function Calling（Fake）
    ↓
ToolChatService（Multi-Step / 预算 / 单 ToolCall）
    ↓ 唯一执行调用点 execution.execute(name, arguments=…)
ToolExecutionService（capability + project scope；ONE Tool execution）
    ↓
ToolRegistry（Schema Validation Authority；异常归一化）
    ↓
Real get_inventory Handler → PostgreSQL（READ ONLY）
    ↓
ToolResult
    ↓ role=tool → 下一轮 LLM → Final Answer
```

失败类型与路径（**本阶段锁定的区分**）：

```text
Capability denied（授权失败）
    → 异常穿透 ToolChatService（无 try/except）→ HTTP 403 → 不继续 LLM

Schema / Handler / DB failure（Tool 失败）
    → ToolResult(success=False) → role=tool 消息 → 按 Multi-Step contract 继续

Malformed ToolCall / Multiple ToolCall（协议失败）
    → 异常（LLMToolCallFormatError / MultipleToolCallsError）→ 任何执行之前
```

静态锁定（C14）：`ToolChatService` 源码 **0 个 try/except**、
0 处 `registry.execute(`、0 处 `allows_tool`、执行调用点唯一
（`execution.execute`）。

---

## 4. DB Safety

```text
DB writes = 0
    * 失败用例：真实 DB 语句数 = 0（_StatementRecorder 断言 statements == []）
      —— unknown tool / missing required / unknown field / invalid type /
         Handler validation / capability denied（HTTP 403）
    * 成功用例：语句集 = SET TRANSACTION READ ONLY + SET LOCAL statement_timeout
      + 1 条 SELECT（含 SUM(qty) 与绑定参数 %(material_code)s）+ ConnectionEvents.rollback
    * 未见 INSERT / UPDATE / DELETE / DDL（recorder.assert_read_only）
DB schema = 未修改；未新增 audit / tool_call_log / request_id 持久化
```

---

## 5. 发现的问题 / 当前限制

```text
发现（均为既有设计事实，非缺陷，已在测试中如实锁定）：
1. ToolCall DTO 层已强制 arguments 为 dict（非 object 早在 DTO 拒绝）；
   Registry 的 "arguments 必须为 mapping" 分支属纵深防御（测试直接调用 Registry
   验证），经 LLM 路径不可达。
2. 真实 OpenAI 兼容 parser 对 >1 tool call 抛 LLMToolCallFormatError（更早），
   ToolChatService 的 MultipleToolCallsError 是协议违规 provider 的第二道防线；
   两者都在任何执行之前拒绝。
3. ROLLBACK 为 DBAPI 级调用（不触发 before_cursor_execute）→ 用
   ConnectionEvents.rollback 事件证明"结束即回滚"。

限制：
* Real LLM = NO（全部 Scripted Fake；未新增 RUN_REAL_LLM）
* Real Tool 仍为 1 个（get_inventory）；get_work_order = DEFERRED
* DB failure 使用 stub engine（0 网络）；未做真实网络中断 / 连接池耗尽模拟
* 未新增 Retry / Circuit Breaker / Timeout 编排（本阶段明确禁止）
* lint unavailable（环境无 ruff / flake8；未安装新工具）

环境观察（与代码无关，如实记录）：
* 本次会话后段数据库 **TCP 连接建立耗时 ~30s/次**（实测 connect 30044 ms，
  而 `select 1` 仅 1.5 ms）→ 含 DB 的全量套件在本会话无法跑完；
  定向 DB 套件已全绿（Step 12 文件 36 passed）。
* 期间我自身的多次并发后台测试运行曾造成数据库锁互等
  （idle-in-transaction 持锁 vs 测试 fixture TRUNCATE）；已终止并清理会话，
  与 Step 12 代码无关（Step 12 零生产修改）。
```

---

## 6. 本阶段未引入

```text
Agent / LangGraph / MCP / Memory / Planning / Multi-Agent  = NOT IMPLEMENTED
Retry / 自动修复 / 自动重规划 / Parallel Tool Calling       = NOT IMPLEMENTED
audit table / request_id persistence / tool_call_log table  = NOT IMPLEMENTED
第三个 Tool / 真实 get_work_order                            = NOT IMPLEMENTED（DEFERRED）
真实 LLM 调用                                                = NOT IMPLEMENTED
```

---

## 7. 测试结果

```text
定向（6 文件）：                                  175 passed / 7 skipped
定向 DB（Step 12 文件，RUN_DB_TESTS=1）：          36 passed
全量 no DB：                                     2780 passed / 334 skipped
全量 DB：                                         环境降级未跑完（见 §5 环境观察）；
                                                 Step 11 基线 3029 passed / 41 skipped
                                                 （Step 12 零生产修改，SHA256 一致）
compileall：                                      clean（exit 0）
LSP（2 个新增/修改测试文件）：                      0 error / 0 warning
lint：                                            unavailable（未安装新工具）
DB writes：                                       0
Network：                                         0（Fake LLM + stub engine）
Real LLM：                                        NO
```
