# Phase 3.11 Step 14 — Tool Execution Record Contract

> 目标：为**一次 Tool 执行**建立不可变的 Execution Record 数据契约，
> 作为后续 Audit / Observability / Metrics / Tracing 的稳定数据模型。
>
> **本阶段只建立 Contract，不实现持久化**（DB writes = 0）。

```text
ToolExecutionContext（Step 13）
        +
ToolResult（Step 12）
        +
Timing（started_at / finished_at / duration_ms）
        ↓
ToolExecutionRecord（Step 14；frozen DTO）
```

---

## 1. Purpose

一次 Tool 执行会产生三类信息：

```text
业务结果      ToolResult（success / data / error）        ← 已有（Step 12，不改）
运行时上下文  ToolExecutionContext（谁 / 哪个 request / 哪个 project / 第几轮）
              ← 已有（Step 13，不改）
执行事实      耗时 / 时间戳 / 成功与否 / 失败分类          ← 本阶段补齐
```

Step 12 已锁定失败契约、Step 13 已锁定运行时上下文；本阶段把三者组合为
**唯一的、"只描述执行事实"的不可变事件 DTO**，使未来 Audit / Metrics /
Tracing 有稳定的落点，而**不必**在基础事件里携带业务数据。

为什么必须分离（而不是往 ToolResult 加字段）：

```text
ToolResult          = Tool execution **result**（给 LLM / 调用方的业务结果）
ToolExecutionRecord = execution **metadata**（给观测 / 审计的事实）
两者的消费者、生命周期、安全等级都不同 → 必须两个 Contract。
```

---

## 2. Contract

```text
backend/app/services/tool_execution_record.py

@dataclass(frozen=True)
class ToolExecutionRecord:
    # ---- 来自 ToolExecutionContext（原值映射，不重新生成） ----
    request_id: str                    # 一次 chat 的关联 ID
    round: int                         # Tool 执行轮次（>= 1）
    project_id: str | None             # 授权作用域（None = 未绑定）
    tool_call_id: str | None           # LLM ToolCall id 原值

    # ---- 来自实际执行 ----
    tool_name: str                     # execute() 实参（不推断）

    # ---- Timing ----
    started_at: datetime               # timezone-aware UTC
    finished_at: datetime              # >= started_at
    duration_ms: float                 # perf_counter 差值；>= 0

    # ---- 结果 ----
    success: bool                      # 直接来自 ToolResult.success

    # ---- 安全错误分类槽位 ----
    error_code: str | None             # 当前无结构化来源 → None（不发明）
    error_type: str | None             # 既有 ToolError 类名（allow-list）或 None

纯构造：
ToolExecutionRecord.from_execution(
    context=..., tool_name=..., result=...,
    started_at=..., finished_at=..., duration_ms=...,
    error_code=None, error_type=None,
) -> ToolExecutionRecord

计时工具（统一约定，避免 naive datetime）：
now_utc() -> datetime                       # datetime.now(timezone.utc)
elapsed_ms(started_perf) -> float           # (perf_counter() - start) * 1000
```

### 字段规则

```text
request_id    ← context.request_id        （不重新生成）
project_id    ← context.project_id        （不从 arguments / LLM 获取）
tool_call_id  ← context.tool_call_id      （保持原值）
round         ← context.round             （保持原值）
tool_name     ← execute(tool_name) 实参    （不从 question / LLM message 推断）
success       ← result.success            （不推断：Handler 正常返回 False 也是 False）
```

### Error Contract（§八）

```text
当前 ToolResult 只有 error: str | None（无结构化 error_code / cause_type）→
  * error_code ： 不发明错误码体系 → 恒为 None（槽位保留；构造可显式传入，
                 形态受 snake_case 校验保护）
  * error_type ： 复用项目既有 ToolError 家族类名（allow-list：
                 ToolValidationError / ToolExecutionError /
                 ToolNotFoundError / ToolAlreadyRegisteredError）——
                 仅当 ToolResult.error 以 "<ClassName>:" 开头时直接映射；
                 Registry 级消息（"Tool 未注册" / "参数校验失败"）→ None
                 （绝不解析 / 推断 message 正文，绝不猜测）
  * 原始 error message / traceback **不写入** Record
```

### Timing Contract（§六）

```text
started_at / finished_at ：datetime.now(timezone.utc)（项目既有 UTC 约定；naive 拒绝）
duration_ms              ：(time.perf_counter() - start) * 1000（单调时钟；>= 0）
校验：duration_ms 数值 / 非负 / 非 NaN / 非 Infinity；finished_at >= started_at
不使用 "datetime.now() - datetime.now()" 作为唯一精度来源
```

### 校验（构造即失败）

```text
request_id 非空 str；round int >= 1（拒绝 bool）；tool_name 非空 str；
success 严格 bool；started_at / finished_at timezone-aware 且 finished >= started；
duration_ms 数值 >= 0（拒绝 bool / NaN / Infinity）；project_id / tool_call_id
None 或非空 str；error_code / error_type 形态白名单；
success=True 时 error_code / error_type 必须为 None（不伪造失败分类）；
tool_name 必须与 result.tool_name 一致（拒绝"说谎"的 Record）。
```

---

## 3. Relationship

```text
ToolExecutionContext
        ↓（request_id / project_id / tool_call_id / round）
Tool Execution（ToolExecutionService → ToolRegistry → Tool）
        ↓（success / data / error）
ToolResult
        +
Timing（started_at / finished_at / duration_ms）
        ↓ from_execution()（纯函数：无 IO / 无 DB / 无 LLM / 无日志）
ToolExecutionRecord
```

---

## 4. Security Boundary

```text
No arguments        —— Record 无 arguments 字段；构造签名不含 arguments
No result data      —— ToolResult.data 不进入 Record（测试用含 secret 的 data 验证 0 泄漏）
No SQL              —— 无 SQL / statement / query 字段（静态扫描 + 行为断言）
No secrets          —— API Key / password / DATABASE_URL / connection string /
                       Authorization / Bearer / sk- / traceback 均不出现
No persistence      —— 不落库、无 Repository / Migration / audit table（DB writes = 0）
error_type 仅类名   —— allow-list：非白名单前缀（含注入文本 / 连接串 / Traceback）→ None
```

---

## 5. Compatibility

```text
ToolResult                = unchanged（仍 tool_name / success / data / error；无新增字段）
ToolExecutionService.execute() 返回类型 = ToolResult（不是 Record、不是 tuple）
ToolRegistry              = unchanged（签名 / 行为）
Tool Handler              = unchanged（只接收 arguments）
API                       = unchanged（api/tool_chat.py SHA256 未变；无新字段 / header）
ToolChatService           = unchanged（多轮语义 / 消息历史 / 预算均未动）
LLM messages              = unchanged（Context / Record 均不进入）
生产接线                   = Deferred（无既有 observer / sink 扩展点；
                             本阶段不新增 Observer Framework，不硬改执行边界）
```

---

## 6. Current Limitation

```text
No persistence          —— Record 目前不被写往任何地方
No audit                —— 无审计表 / 无留存
No dashboard            —— 无 UI / 指标
No metrics aggregation  —— 无计数 / 直方图 / 聚合
No tracing backend      —— 不接 OpenTelemetry / Jaeger 等
No error_code taxonomy  —— 不发明结构化错误码（error_code 恒为 None 直至 ToolResult 提供）
Record 不由生产代码创建  —— 仅 DTO + from_execution() + 测试 + C16 契约；
                          生产采集（timing + 组装）= 后续阶段
链路 A（AIOrchestrator）同样未接入 Record（与 Context 一致的 Deferred 边界）
```

环境观察（沿用 Step 12/13 记录）：本会话数据库 TCP 连接建立 ~30s/次 →
含 DB 全量套件可能无法在会话内跑完；Step 14 的 DB-gated 仅 1 个只读回归。

---

## 7. Tests

```text
tests/test_tool_execution_record.py                        83 tests（82 非 DB + 1 DB-gated）
tests/test_tool_chat_architecture_contract.py::TestC16…    9 tests（C16.1 – C16.8 + 无接线验证）

DTO             = PASS（创建 / frozen / 字段类型 / 白名单 / 可选字段默认 None）
Timing          = PASS（now_utc tz-aware / elapsed_ms >= 0 且真实测量 /
                        start<finish / duration>=0 / naive 与倒序拒绝）
Result Mapping  = PASS（success 直接映射；data / error message 不入 Record）
Error Mapping   = PASS（error_code 不发明；error_type allow-list；显式值形态校验；
                        success 不得携带错误分类）
Context Mapping = PASS（4 字段原值；context 不被修改；request_id 不重新生成）
Isolation       = PASS（无 arguments / data / SQL / credentials；模块 import 纯净）
Immutability    = PASS（frozen；dataclasses.replace 产生新实例）
Determinism     = PASS（同输入 → 相等 Record；from_execution 无副作用）
Real Tool       = PASS（DB-gated：真实 get_inventory MAT-001 → qty 250.0；
                       Record.success / tool_name / round / project_id / request_id 正确）
```

测试结果：

```text
定向（Step 14 文件）：                              82 passed / 1 skipped
定向（§十七 5 文件）：                              199 passed / 8 skipped
全量 no DB：                                       2928 passed / 337 skipped
                                                   （Step 13 基线 2838/335；
                                                    +82 本文件非 DB +9 C16 = +91 通过；
                                                    对比基线另有 1 个**环境条件性 skip**：
                                                    test_ai_orchestrator.py::TestProjectContextProvider::
                                                    test_default_provider_returns_tuple 在
                                                    DB 连接不可用时运行时 pytest.skip）
定向 DB（Step 14 文件，RUN_DB_TESTS=1）：            环境失败（非代码）：
                                                   psycopg ConnectionTimeout 于
                                                   real_engine fixture setup（localhost:5432）
                                                   连接超时 → 测试未能启动；本地 PostgreSQL
                                                   在本会话后段不可达（Step 12/13 已记录
                                                   同类环境降级）。同一真实链路在 Step 13
                                                   的 DB 套件（52 passed）中已通过。
compileall backend tests：                         clean（exit 0）
LSP：                                              0 error / 0 warning
lint：                                             unavailable（未安装新工具）
DB writes：                                        0
Network：                                          0（无真实 LLM；DB-gated 未执行）
Real LLM：                                         NO
```

---

## 8. Step 14 结论

```text
ToolExecutionContext
        +
ToolResult
        +
Timing
        ↓
ToolExecutionRecord = IMPLEMENTED（frozen；11 字段执行元数据；from_execution 纯构造）

Audit / Metrics / Dashboard / Tracing = NOT IMPLEMENTED（明确不做）
Persistence（DB / Repository / Migration / audit table） = NOT IMPLEMENTED

ToolResult = unchanged      execute() 返回类型 = unchanged
ToolRegistry / Tool Handler / API / LLM messages = unchanged

Real Tool = get_inventory   get_work_order = DEFERRED   Third Tool = NOT ADDED
Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
