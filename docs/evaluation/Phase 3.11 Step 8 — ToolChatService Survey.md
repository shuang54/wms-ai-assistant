# Phase 3.11 Step 8 — ToolChatService Survey（勘察 + 边界决策）

> 目标：完整勘察历史 Tool 链路（LLM Function Calling），确定它与
> Phase 3.11 新 Tool Boundary 的关系，并形成**迁移决策**。
>
> 本 Step：**只勘察、测试、记录、设计**。生产代码 0 修改；
> 不迁移 ToolChatService、不删除、不改 API、不改 Function Calling、不改 mock tools。

---

## 1. Current ToolChatService

| 项 | 事实（来自真实代码） |
|---|---|
| 文件 | `backend/app/services/tool_chat_service.py`（Phase 3.6.2 引入；3.6.3 升级 Multi-Step） |
| 入口方法 | `ToolChatService.chat(message: str, *, registry: ToolRegistry) -> ToolChatResponse` |
| 构造 | `ToolChatService(llm_client: LLMProvider | None = None, *, max_tool_rounds: int | None = None)` |
| LLM 依赖 | `LLMProvider`（默认 `get_default_llm_client()` → `OpenAICompatibleClient`） |
| Registry | **逐次调用传入**（非构造期注入）→ 无项目级 Registry 归属 |
| DTO | `ToolChatResponse(answer, tool_calls: tuple[ToolChatCallInfo, ...])`；`ToolChatCallInfo` 只含 `tool_name` |
| 异常 | `ToolChatError` / `MultipleToolCallsError(count)` / `ToolCallingBudgetExceededError(max_rounds, requested_tool)` |
| 预算 | `settings.tool.max_rounds`（`TOOL_MAX_ROUNDS`，默认 5，钳制 [1,20]，见 `backend/app/config.py::ToolSettings`） |
| 消息历史 | Service 内部独立构建（只 append，不改写；不接收调用方 list） |

不在本层做（源码明确声明）：并行 Tool / Tool 依赖图 / Retry / Cache / Timeout /
对话 session / RAG / Tool 权限 / 持久化 / 审计。

---

## 2. Call Graph（每层真实代码）

```text
HTTP POST /api/chat/with-tools                      backend/app/api/tool_chat.py::chat_with_tools
    ↓ 模块级单例 _tool_chat_service / _tool_registry（register_mock_tools）
ToolChatService.chat(message, registry=_tool_registry)
                                                    backend/app/services/tool_chat_service.py
    ↓ registry.list_definitions()
definitions_to_openai_tools(definitions)            backend/app/llm/tool_schema.py
    ↓ await llm.chat(messages, tools=tools)
LLMProvider（默认 get_default_llm_client / OpenAICompatibleClient）
                                                    backend/app/llm/client.py
    ↓ _parse_tool_calls → ToolCall(id, name, arguments: dict)
    ↓        （arguments: JSON 字符串 → dict；缺 id / name / 非法 JSON → LLMToolCallFormatError）
ToolRegistry.execute(call.name, call.arguments)      backend/app/tools/registry.py
    ↓ validate_arguments → Handler（Mock Tools）
ToolResult
    ↓ _build_tool_messages（assistant tool_call + role=tool）
messages.append(...)  → 下一轮 LLM（携带完整历史 + tools）
    ↓ 无 tool_call → 最终回答
ToolChatResponse → ToolChatApiResponse（answer + tool_calls[名称]）
```

调用方（全项目搜索结论）：

```text
backend/app/api/tool_chat.py            唯一入口（HTTP，注册于 backend/app/main.py）
backend/app/api/ai_orchestrator_service 不含（主链路与链路 B 无交叉 import）
frontend/                               目录为空 → 无 UI 调用方
docs/api.md §2.4                        公开文档化接口
```

---

## 3. Current Tools（链路 B 实际注册的 Tool）

`backend/app/api/tool_chat.py` 模块级 `_tool_registry = ToolRegistry();
register_mock_tools(_tool_registry)` → **仅 Phase 3.6.1 Mock Tools**：

| Tool | Definition | Input Schema | Handler | Real / Mock | Read / Write |
|---|---|---|---|---|---|
| `get_inventory` | `mock_tools.GET_INVENTORY_DEFINITION` | `material_code`(必填) + `warehouse_code`(可选) | `mock_tools.GetInventoryHandler` | **Mock**（硬编码 `quantity=1000, unit=PCS`） | Read-only（0 IO） |
| `get_work_order` | `mock_tools.GET_WORK_ORDER_DEFINITION` | `work_order_no`(必填) | `mock_tools.GetWorkOrderHandler` | **Mock**（硬编码 `status=RELEASED`） | Read-only（0 IO） |

```text
结论：ToolChatService currently uses separate mock tools
* 真实 get_inventory（Phase 3.7.12 只读 PostgreSQL）**未**接入本链路；
* 真实 get_work_order（Step 5）**未**接入本链路；
* 本链路 registry 与 Orchestrator 链路的 registry 是**两个独立实例**。
```

---

## 4. Function Calling Contract

```text
请求侧（tools schema）：
    definitions_to_openai_tools(definitions)
        → [{"type": "function",
            "function": {"name", "description", "parameters"}}]
    （parameters 深拷贝；不含 handler / module / class）

响应侧（LLM 提供 name + arguments，**没有**第二次映射层）：
    message.tool_calls[i].id                → ToolCall.id（回传给 role=tool）
    message.tool_calls[i].function.name     → ToolCall.name（Tool 名称）
    message.tool_calls[i].function.arguments→ JSON 字符串 → json.loads → dict
    （缺失 id / name / 非 object / 非法 JSON → LLMToolCallFormatError）
    单次响应 > 1 个 tool call → LLMToolCallFormatError（Client 层，先于 Service）

Service 层：直接消费 ToolCall（不做名称映射、不做参数重写）
    → registry.execute(call.name, call.arguments)
```

**回答 §七 的问题**：LLM **同时**提供 `tool_name` 与 `arguments`；
ToolChatService **没有** "function call → Tool 名映射" 的第二步。

---

## 5. Argument Contract

```text
arguments 由 LLM 结构化给出（dict），由 LLM Client 解析（json.loads）；
ToolChatService 不解析、不改名、不补默认值、不过滤字段；
Handler 最终收到的是 LLM 给的 dict（经 Registry Schema 校验后）。

characterization（tests/test_tool_chat_service_characterization.py）：
    {"material_code": "MAT001", "warehouse_code": "A01"} → Handler 收到同一 dict
    {"material_code": "MAT001"} → 不额外补 warehouse_code
    {"material_code": "MAT001", "batch": "B-001"} → Registry 拒绝（unknown field）
```

---

## 6. Schema Validation

```text
ToolChatService → ToolRegistry.execute → validate_arguments（唯一 Schema 权威）
                                          ├── required
                                          ├── type
                                          └── unknown field
```

* Service 源码不含 `validate_arguments` / `required` / `additionalProperties`；
* 校验失败 → `ToolResult(success=False)` → role=tool 消息 →**链路继续**（不中断）；
* **Schema Authority 未被绕过**（与主链路一致）。

---

## 7. Capability Boundary

```text
链路 B：**不存在** capability 校验
    * ToolChatService 源码无 capability 概念（签名无参数、无 import、无判断）；
    * 传入的 Registry 里的**任何** Tool 都会被暴露给 LLM 并可被执行；
    * 通过 /api/chat/with-tools 的注册表是 API 模块级单例（仅 Mock Tools）。

主链路（对照）：
    ToolExecutionService._check_capability → ProjectCapabilities.allows_tool()
    非白名单 → Handler 0 次调用 + AIOrchestratorCapabilityError（HTTP 403）
```

结论：**C5 = NO（缺口事实）**。当前即使真实 Tool 被注册进该链路，
也不会经过项目能力白名单；这是链路 B 与 Phase 3.11 边界的实质差异之一。
（本 Step 只记录，不修复。）

---

## 8. Execution Boundary

```text
链路 B：ToolChatService → ToolRegistry.execute        （直接调用）
主链路：Orchestrator → ToolExecutionService → ToolRegistry.execute
```

```text
结论：CURRENT VIOLATION（绕过 ToolExecutionService）
      —— 不是因为代码错误，而是该链路早于 Step 2 的统一执行边界存在。
```

可行性分析（仅分析，不实施）：

```text
调用点兼容性：
    ToolChatService:      await registry.execute(call.name, call.arguments)   # 位置参数
    ToolExecutionService: await self._registry.execute(tool_name, arguments=arguments)
    ToolRegistry.execute(self, tool_name, arguments: dict | None = None)
    → 签名兼容；arguments 为 dict / {} → 行为可保持一致（capabilities=None 时不限制）

差异点（迁移前必须决策）：
    * capability：ToolExecutionService 支持 capabilities；链路 B 目前无项目语义
      → 若传入 capabilities，则新增「能力拒绝」行为（Handler 0 次调用）；
        若不传（None），则与今天完全等价（无变化）；
    * 错误语义：capability 拒绝抛 AIOrchestratorCapabilityError（HTTP 403），
      链路 B 目前的 API 未映射该异常（会落到 500 / 通用映射）→ 需要 API 映射决策；
    * ProjectContext：ToolExecutionService 不碰 project context，迁移不解决该缺口。

结论：Migration feasibility = HIGH（机制上低风险），但**语义决策未完成**
      → 本 Step 不迁移。
```

---

## 9. Multi-step Behavior

```text
while True:                                   ← 顺序多步（LLM → Tool → LLM → ...）
    LLM(tools)                                ← 每轮都携带 tools（可继续请求 Tool）
    if no tool_calls: return answer           ← 终止条件 1（正常终止）
    if tool_round >= max_tool_rounds: raise ToolCallingBudgetExceededError
                                              ← 终止条件 2（预算耗尽，先于执行）
    if len(tool_calls) > 1: raise MultipleToolCallsError
                                              ← 终止条件 3（多 Tool call：不支持并行）
    tool_round += 1
    result = registry.execute(name, arguments) ← Tool 失败不中断（归一为 ToolResult）
    messages.extend(assistant tool_call + role=tool)
```

```text
max_rounds（真实值）：settings.tool.max_rounds（TOOL_MAX_ROUNDS，默认 5，钳制 [1,20]）
loop condition：LLM 每轮返回 tool_calls 且预算未耗尽
worst case：max_rounds 次 Tool 执行 + max_rounds+1 次 LLM 调用
终止后：answer（str）或 上述两个异常之一（不静默截断）
```

`Multi-step = YES`（与主链路「ONE route → ONE Tool」不同）。

---

## 10. Security

| 检查项 | 事实 | 结论 |
|---|---|---|
| 绕过 ToolRegistry | 否（唯一执行调用点 = `registry.execute`） | PASS |
| 绕过 Schema Validation | 否（arguments 全部交给 Registry） | PASS |
| 绕过 Capability | **是（该链路无 capability 概念）** | **FAIL（相对新边界）** |
| 绕过 ProjectContext | 未使用 project context（Mock Tool 不需要） | PARTIAL（缺能力，不是绕过） |
| 直接执行 Handler | 否（Service 不 import Handler / 不触 `_handlers`） | PASS |
| LLM 指定任意 tool_name | 允许，但未注册 → `ToolResult(success=False)`（安全失败，无 fallback） | PASS |
| LLM 传 unknown fields | 允许，但 Registry 拒绝（Handler 0 次调用） | PASS |
| LLM 传任意 arguments | 允许，但 Registry 类型校验 + Handler 业务校验 | PASS |
| LLM 触发第二个 Tool | 允许（顺序、预算内；设计如此） | PARTIAL（intended，但属于多步能力） |
| 敏感信息 | tool message 仅 success/data/error；API 错误体无 Key/URL/SQL/traceback | PASS |

```text
Security Boundary: PARTIAL
理由：执行/Schema 边界完好，但**无 capability 门禁**、无 project scope，
      且当前仅 Mock Tool（无真实业务面）→ 现状风险低，接入真实 Tool 前必须补齐。
```

---

## 11. Comparison with New Tool Pipeline

| 能力 | 新 Tool Pipeline（Step 1–7） | ToolChatService（链路 B） |
|---|---|---|
| Tool Selection | Router（`RouteDecision.tool_name`，规则优先 + 可选 LLM fallback） | **LLM function calling**（`ToolCall.name`） |
| Tool Argument Extraction | `ToolArgumentExtractor`（确定性正则） | **LLM 结构化 arguments**（Client 解析 JSON） |
| Schema Validation | ToolRegistry | **ToolRegistry（相同）** |
| Capability | `ToolExecutionService._check_capability`（ProjectCapabilities 白名单） | **无** |
| Tool Execution | ToolExecutionService → ToolRegistry | **ToolRegistry 直连（绕过执行边界）** |
| Tool Result | ToolResult | **ToolResult（相同）** |
| Multi-step | 禁止（ONE route → ONE Tool） | **允许（sequential + max_rounds 预算）** |
| LLM | 仅 Router fallback（可选）；参数提取无 LLM | **核心（选择 + 参数 + 最终回答）** |
| Project Context | Orchestrator（capabilities / knowledge scope / schema） | **无**（Handler 只收 arguments） |
| Tools | 真实 get_inventory / get_work_order（只读 DB） | Mock get_inventory / get_work_order（硬编码） |
| 入口 | `POST /api/ai/chat`（orchestrator_chat） | `POST /api/chat/with-tools` |
| 测试 | Step 4–7 契约 + 回归（含 DB） | `test_tool_chat_service.py` / `test_tool_chat_api.py` + Step 8 characterization |

```text
共享面（今天）：ToolRegistry（同一类，不同实例）+ ToolResult（同一 DTO）
未共享面：ToolExecutionService（执行边界）、Capability、Orchestration、ProjectContext
```

---

## 12. Migration Options

```text
Option A — Deprecate（删除链路 B）
    依据检查：
        有独立 API ?        YES（/api/chat/with-tools，已在 main.py 注册，docs/api.md §2.4 文档化）
        有真实调用方 ?      未知（frontend/ 为空；无其它后端调用方）
        有测试 ?            YES（service + API 两套，覆盖充分）
        有业务用途 ?        仅 Mock Tools → 当前无生产业务价值
    风险：删除公开且已文档化的接口需要产品决策；本 Step 无权删除（§二十 禁止）。
    → 不推荐（无「无调用方」证据；且有维护价值：Function Calling 抽象已完整）

Option B — Reuse New Execution Boundary
    LLM → ToolChatService → ToolExecutionService → ToolRegistry → Tool(Mock)
    * 机制兼容（见 §8：调用点签名兼容，capabilities=None 时行为等价）；
    * 需要决策：capabilities 是否注入（决定是否新增 403 语义）、异常映射、
      project scope；否则迁移只是换一层壳（收益 = 边界统一）。
    * loop 必须留在 ToolChatService（ToolExecutionService 保持 ONE Tool 执行）。
    → 可行但**无当前驱动**（无真实 Tool / 无调用方 / 无能力需求）→ 记为「未来迁移路径」

Option C — Keep Separate
    两条链路并存：
        主链路：Router → ToolArgumentExtractor → ToolExecutionService → ToolRegistry → Real Tool
        链路 B：LLM FC → ToolChatService(loop) → ToolRegistry → Mock Tool
    共享：ToolRegistry（契约）/ ToolResult（契约）
    不强行合并：Orchestration / Selection / Argument 语义完全不同
    → 现状即如此（0 修改），且与项目「Simple First, Evolve Later」一致
```

---

## 13. Recommended Architecture

```text
Recommended next architecture: C（Keep Separate）
```

判断依据（按 §十七：真实调用方 / 职责边界 / Security / Project Context / 测试覆盖 / 维护成本）：

```text
1. 职责边界：两条链路的 Selection / Argument 语义根本不同
   （确定性规则 vs LLM Function Calling）。合并会产生"Router + Function Calling
   双选择"的混合体 —— 正是 Step 3 已消除的反模式。
2. 真实调用方：链路 B 只有 HTTP 入口与 Mock Tools（frontend/ 为空），
   当前无生产业务需求 → 迁移/删除都缺乏驱动（不按"代码更少"决策）。
3. Security：链路 B 的缺口（无 capability / 无 project scope）在**只挂 Mock Tool**
   时不可利用；一旦接入真实 Tool 才成为实质风险 → 迁移必须与能力门禁一起做，
   而不是先换壳。
4. Project Context：链路 B 完全不下发项目上下文；真实 Tool（如 get_inventory）
   依赖 ProjectContextProvider 与只读 DB 事务 —— 接入前需要项目级设计。
5. 测试覆盖：两条链路各自有完整测试；Step 8 补齐了 characterization，
   未来迁移有行为基线（可安全重构）。
6. 维护成本：链路 B 代码量小、边界清晰（无 DB / 无 RAG / 无写操作），
   保留成本低于合并后的双语义复杂度。
```

**并且明确（未来迁移路径）**：若链路 B 需要接入**真实 Tool**，则执行点必须
改经 `ToolExecutionService`（即 Option B）——这属于 Step 9+ 的实施范围，
本 Step 只记录。

---

## 14. Migration Preconditions（未来真正迁移前必须满足）

```text
[1] 产品决策：链路 B 的定位（保留 / 弃用 / 与主链路合并），以及是否有真实调用方
[2] Project scope 决策：链路 B 是否绑定 project_id / ProjectCapabilities；
    若绑定 → capability 拒绝语义（HTTP 403）与 API 错误映射必须先定义
[3] Tool 范围决策：链路 B 允许挂哪些 Tool（仅 Mock / 只读真实 / 全部）；
    写入型 Tool 一律禁止（当前全链路只读）
[4] 执行边界迁移：ToolChatService → ToolExecutionService（capabilities 注入与否），
    并保持 "ONE Tool execution"（loop 留在 ToolChatService）
[5] 行为基线：先保留 Step 8 characterization（已有）+ 迁移后更新
    tests/test_tool_chat_architecture_contract.py 的 C2/C10 期望
[6] 观测/审计：多步链路目前无 request_id / tool 调用审计（主链路有 metadata）；
    若进入生产需补齐日志字段（tool_name / round / success / error_code）
[7] 测试：迁移后必须保持 test_tool_chat_service.py / test_tool_chat_api.py 全绿
```

---

## 15. Risks（如实记录）

```text
R1  capability 缺口：一旦真实 Tool 注册进链路 B 的 registry，LLM 可直接调用，
    绕过项目能力白名单（无 Handler 0 次调用保护）。
R2  ProjectContext 缺口：Handler 只能自行解析上下文（如 GetInventoryHandler 的
    GetInventoryProjectContextProvider 兜底），链路 B 不提供项目 scope。
R3  双 registry 并存：主链路与链路 B 各持一个 ToolRegistry 实例（不同 Tool 集合），
    真实 Tool 是否出现在哪个入口取决于装配，容易产生"同名 Tool 行为不同"的困惑。
R4  多步 + 预算：max_rounds 硬上限（默认 5）已防止无限循环，但每次请求最多
    5 次 Tool + 6 次 LLM → 成本/延迟显著高于主链路（单次 LLM 最多 1 次）。
R5  错误映射差异：capability 拒绝异常在链路 B 无映射（若迁移会落到 500），
    需要按 §14[2] 先定义。
R6  契约测试锁定现状：tests/test_tool_chat_architecture_contract.py 的
    C2/C10 明确断言"尚未共享执行边界"，迁移时必须同步更新（防止静默漂移）。
```

---

## 16. Tests（本 Step 新增）

```text
tests/test_tool_chat_architecture_contract.py      34 tests（C1–C10 静态/接线事实）
tests/test_tool_chat_service_characterization.py    8 tests（仅补 5 个缺口）
（既有 tests/test_tool_chat_service.py 30+ / tests/test_tool_chat_api.py 20+ 覆盖不重复）
```

```text
python -m pytest -q tests/test_tool_chat_service.py               → 全部通过（未修改）
python -m pytest -q tests/test_tool_chat_api.py                   → 全部通过（未修改）
python -m pytest -q tests/test_tool_chat_architecture_contract.py → 34 passed
python -m pytest -q tests/test_tool_chat_service_characterization.py → 8 passed
```

DB writes = 0；Network = 0（0 DB / 0 真实 LLM）。
