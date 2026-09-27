# Phase 3.11 Step 13 — Tool Execution Context Contract

> 目标：为 Tool 执行建立**最小、稳定、不可变**的 Execution Context
> （`request_id` / `project_id` / `tool_call_id` / `round`），并证明它
> **不进入** Tool arguments、**不进入** LLM messages、**不进入** API contract，
> Registry / Handler **不持有** Context。
>
> 本阶段**零持久化**：无落库、无 audit table、无 dashboard、无 tracing backend、
> 无 request-id middleware。

---

## 1. Context Schema

```text
backend/app/services/tool_execution_context.py

@dataclass(frozen=True)
class ToolExecutionContext:
    request_id: str                 # 一次 chat 一个（uuid4；非 DB id / 非 secret）
    round: int                      # Tool 执行轮次，从 1 开始（one round = one Tool execution）
    project_id: str | None = None   # 授权作用域（来自执行边界；LLM 不可控制）
    tool_call_id: str | None = None # LLM ToolCall.id 原值（不重新生成）

new_request_id() -> str             # 生成策略唯一入口（uuid.uuid4()）
assert_field_whitelist()            # 4 字段白名单自检（防未来扩展）
```

校验（`__post_init__`）：
`request_id` 非空 str；`round` 为 int（拒绝 bool）且 ≥ 1；
`project_id` / `tool_call_id` 为 None 或非空 str。

```text
字段白名单 = {request_id, project_id, tool_call_id, round}
```
本阶段**不扩展**字段（无 user / tenant / session / trace / deadline /
auth / permissions / retry）。

### 为什么不复用既有 request_id

项目既有的 `request_id`（`LLMResponse.metadata["request_id"]` /
`llm_usage_record.request_id`）语义是 **LLM Provider 响应 ID**：
每次 LLM 调用一个、可能为 None（Mock / 非 OpenAI 兼容响应）、
只能在 LLM 调用**之后**取得 —— 与本阶段要求的
"一次 chat 一个、覆盖该 chat 内全部 Tool 执行"生命周期不同，
因此**不复用、不混用**（也**不新建**第二套 Provider 幂等体系；
本 DTO 不落库、不参与幂等）。

---

## 2. Lifecycle

```text
ToolChatService.chat(message, registry, execution_service=…)
    ↓ request_id = new_request_id()            ← 一次 chat 生成一次
    ↓ LLM #1 → ToolCall(id=call_001, name, arguments)
    ↓ Round 1
ToolExecutionContext(request_id=R, project_id=<scope>, tool_call_id="call_001", round=1)
    ↓ ToolExecutionService.execute(name, arguments=…, context=ctx)
    ↓ LLM #2 → ToolCall(id=call_002, …)
    ↓ Round 2
ToolExecutionContext(request_id=R, project_id=<scope>, tool_call_id="call_002", round=2)
    ↓ …
```

```text
同一次 chat：request_id 相同
不同 round：tool_call_id / round 不同（每轮**新实例**，不 mutate 旧 Context）
新一次 chat：新的 request_id（round 从 1 重新计数）
```

---

## 3. Boundary

```text
ToolChatService                      （Context **创建者**：request_id / round / tool_call_id /
                                      project_id = 执行边界作用域）
    ↓ ToolExecutionContext（frozen DTO）
ToolExecutionService.execute(..., context=…)   （**接收并校验**；不生成 request_id /
                                     不改 Context / 不持久化 / 不 retry / 无 loop）
    ↓ ToolRegistry.execute(tool_name, arguments)  （**签名不含 context**）
    ↓ Tool Handler(arguments)                     （**只接收 arguments**）
```

边界校验（Step 13 新增）：

```text
context 类型非法（非 ToolExecutionContext / None） → TypeError（Registry 0 次调用）
context.project_id ≠ 边界授权作用域                → ValueError（Registry 0 次调用；
                                                     防止调用方伪造作用域）
context = None                                    → 保持旧调用形态（链路 A / 旧测试零改动）
```

---

## 4. Isolation

```text
LLM cannot control project_id     → LLM 在 arguments 里塞 project_id → Registry
                                    以 unknown field 拒绝（Handler 0 次）；
                                    Context.project_id 仍为服务器端作用域
LLM cannot control round / request_id → 同名 forged 字段被 Schema 拒绝；
                                    Context.round 仍从 1 开始
Tool arguments don't contain context → 记录到的 arguments 逐字 == LLM 原值
Registry doesn't own context      → ToolRegistry.execute 签名 = (self, tool_name, arguments)；
                                    registry 源码 0 处 context
Handler doesn't own context       → ToolHandler.__call__ / GetInventoryHandler.__call__
                                    签名 = (self, arguments)；get_inventory 源码 0 处
                                    ToolExecutionContext / request_id
LLM messages don't contain context → request_id / project scope 值不出现在任何 message；
                                    无 request_id / round 键
API contract unchanged            → ToolChatRequest 仍是 {message, project_id}
```

---

## 5. Tests

```text
tests/test_tool_execution_context.py                       52 tests（51 非 DB + 1 DB-gated）
tests/test_tool_chat_architecture_contract.py::TestC15…    7 tests（C15）

Context DTO        = PASS（创建 / frozen / 字段校验 / 白名单 / 无 secret）
request_id         = PASS（非空 / 唯一 / 一次 chat 一个 / 多轮共享 / 无 Tool 依赖）
project_id         = PASS（来自执行边界；LLM 不可控制；无 scope → None）
tool_call_id       = PASS（LLM ToolCall.id 原值；两轮不同）
round              = PASS（从 1 开始；逐轮递增；每轮新实例）
Lifecycle          = PASS（Round 1 → 2 → 3：request_id 相同 / round / id 不同）
Immutability       = PASS（frozen + 执行后字段不变 + 边界不生成 request_id）
Isolation          = PASS（arguments / messages / Registry / Handler / API）
Security           = PASS（只含 4 字段；无 API key / password / connection string）
Real Tool Regression = PASS（DB-gated：MAT-001 → 250.0 + Context 字段正确）
```

测试命令与结果：

```text
定向（5 文件）：                                   205 passed / 8 skipped
定向 DB（Step 13 文件，RUN_DB_TESTS=1）：            见 §7 测试结果
全量 no DB：                                       见 §7 测试结果
全量 DB：                                          见 §7 测试结果（环境限制见 §6）
compileall：                                       clean
LSP：                                              0 error / 0 warning
lint：                                             unavailable（未安装新工具）
```

---

## 6. Limitations

```text
No persistence        —— Context 不落库（无 tool_execution_log / audit table）
No audit log          —— 无写入、无事件流
No dashboard          —— 无 UI / 指标
No tracing backend    —— 不接 OpenTelemetry / Jaeger 等
No request middleware —— 不新增 HTTP header / middleware / API 字段
No Context propagation to Handler —— Registry / Handler 契约保持不变（本阶段明确不扩）
Chain A（AIOrchestrator → ToolExecutionService）不创建 Context（context=None）；
  链路 A 的 Context 化 = Deferred（本阶段严格范围只覆盖链路 B）
project_id 在链路 B 仍只决定**授权**（Engine / schema 绑定 = 后续阶段）
```

环境观察（与代码无关，如实记录）：本会话数据库 **TCP 连接建立 ~30s/次**
（Step 12 已记录）→ 含 DB 全量套件可能无法在会话内完成。

---

## 7. 测试结果

```text
定向（§二十六 5 文件，无 DB）：                     205 passed / 8 skipped
定向 DB（Step 13 文件，RUN_DB_TESTS=1）：            52 passed（含 1 个真实 get_inventory + Context 回归）
定向 DB 复验（Step 11 真实 Tool 套件 + 项目作用域）： 28 passed（301s，环境连接延迟所致）
全量 no DB：                                       2838 passed / 335 skipped
                                                   （Step 12 基线 2780/334 → +58 / +1）
全量 DB：                                          未跑完 —— 环境 TCP 连接 ~30s/次
                                                   （Step 13 变更面已由上述定向 DB 套件覆盖；
                                                    不修改代码解决环境问题）
compileall：                                       clean（exit 0）
LSP（3 生产 + 2 测试文件）：                         0 error / 0 warning
lint：                                             unavailable（未安装新工具）
DB writes：                                        0（Context 不落库）
Network：                                          0（Fake LLM；无新增网络调用）
Real LLM：                                         NO
```

---

## 8. Final State

```text
ToolExecutionContext = IMPLEMENTED（frozen；4 字段）

request_id   = PASS      project_id  = PASS
tool_call_id = PASS      round       = PASS

Persistence = NOT IMPLEMENTED    Audit Log = NOT IMPLEMENTED
Dashboard   = NOT IMPLEMENTED    Tracing Backend = NOT IMPLEMENTED

Real Tool = get_inventory        get_work_order = DEFERRED
Third Tool = NOT ADDED

Agent / MCP / LangGraph / Memory / Planning = NOT IMPLEMENTED
```
