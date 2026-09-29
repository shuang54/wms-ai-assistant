# Phase 3.12 Step 62 — Assistant Outcome Contract Design Audit

> **Contract audit only · No production implementation**
> 新增：`tests/test_assistant_outcome_contract_audit.py`（21 离线；含**测试内**候选 projection）。
> **Production code changes = 0 · API contract changes = 0 · DB schema changes = 0 ·
> Index changes = 0 · Prompt changes = 0**（未新增 Outcome DTO / 字段 / API 参数）
> 0 真实 LLM · 0 网络（MockTransport）· 0 生产数据库写入

---

## 1. Scope

```text
目标：设计并审计 **Assistant-level Outcome Contract**（SUCCESS / EMPTY / REFUSED / FAILED），
      验证该定义是否**覆盖当前全部真实行为**（用真实驱动出来的响应包络验证，不靠推断）。
方法：候选判定函数只存在于**测试内**（project_outcome），输入白名单
      {status_code, route, refusal, tool_success, rag_used_chunks}；
      生产 DTO / API / Trace / 存储**未做任何改动**。
边界：不实现 Outcome DTO / 不新增字段 / 不改 HTTP 契约 / 不改 DB。
```

## 2. Current Behavior（现状信号，Step 61/62 实测）

| 场景 | HTTP | 现状可判定信号 | 现状缺失 |
| --- | --- | --- | --- |
| RAG 正常 | 200 | `route=rag` · `rag_used_chunks≥1` · content=答案 | 无显式 success |
| RAG 空检索 | 200 | `rag_used_chunks=0` + 固定文案 + LLM 0 次 | 无显式 empty |
| TOOL 成功 | 200 | `tool_success=true` | — |
| TOOL 失败 | **200** | `tool_success=false` + content="[Tool … 失败] …" | 记录内无失败原因（error_code/error_type=None） |
| T2SQL 成功（重试后） | 200 | `row_count` · `truncated` · `execution_time_ms` · LLM 2 次 | 无统一 outcome |
| T2SQL refusal | 200 | `refused=true` · `data=None` · Validator/Executor 0 次 | 无统一 outcome |
| T2SQL 重试耗尽 | 500 | detail 含异常类名（`TextToSQLRetryExceededError`） | 无结构化 error |
| LLM / RAG runtime 失败 | 500 | detail 含异常类名 | 同上 |
| 能力禁用 | 403 | detail 固定短语 | 同上 |

## 3. Outcome Definitions（候选契约）

```text
SUCCESS  请求**已经完成**并产生有效业务响应
         RAG：检索有结果 + 生成答案（LLM ≥1）· TOOL：tool_success=true
         T2SQL：SQL 执行成功并返回业务数据/摘要（含 0 行 —— 见 §6 candidate decision）
         注意：**LLM success 不等于 Assistant SUCCESS**（须 assistant-level 完成）

EMPTY    请求**正常完成**，但没有可提供的业务结果
         当前唯一可判定场景：RAG 检索 0 chunks（LLM **0 次**，固定提示语，HTTP 200）
         EMPTY ≠ FAILED（没有出错）· EMPTY ≠ 404（资源存在，只是无内容）

REFUSED  系统**明确拒绝**执行用户请求，且这是**预期的安全行为**
         当前：T2SQL refusal —— HTTP 200 + metadata.refused=true + data=None +
               Validator 0 次 / Executor 0 次 / LLM 1 次
         REFUSED ≠ FAILED（不是故障）· REFUSED ≠ EMPTY（有明确业务语义的答复）

FAILED   请求**无法正常完成**，且**不是**预期的安全拒绝
         当前：LLM provider 失败 · RAG runtime 失败 · T2SQL 重试耗尽 · SQL 执行失败 ·
               能力禁用（403）· **以及 HTTP 200 + tool_success=false 的 Tool 业务失败**
```

## 4. Outcome Precedence

```text
Outcome precedence:
    REFUSED  >  FAILED  >  EMPTY  >  SUCCESS
```

| 优先级 | 理由 | 现状验证 |
| --- | --- | --- |
| 1. REFUSED | 业务**意图**信号（明确拒绝执行）——属于预期结果，不应被"失败/空结果"覆盖；refusal 是系统**主动**给出的终态 | refusal+HTTP200 ✓（无冲突）；与 tool_success=false 同时出现时 REFUSED 胜出（合成用例） |
| 2. FAILED | 传输/执行失败优先于"没有结果"：失败时"空"是**症状**而非独立语义（如 RAG 失败时 result_count=0） | failure + partial execution ✓（RAG 500 时仍有 RAG observation，但 outcome=FAILED）；FAILED 覆盖同包络的 EMPTY（合成用例） |
| 3. EMPTY | 只有在**未失败**的前提下，"没有业务结果"才有意义 | empty+HTTP200 ✓ |
| 4. SUCCESS | 其余正常完成 | 正常三路径 ✓ |

```text
说明（重要）：当前真实路径中 REFUSED 与 FAILED **不会同时出现**
    （refusal 恒为 200 + refused=true；失败恒为 ≥400 且无包络）——
    因此该优先级是**为未来兼容**而定义（防止未来出现混合信号时语义漂移）。
"不要直接接受建议顺序"的审计结论：建议顺序成立，但**必须**补充两条：
    (a) FAILED 的判定必须包含"HTTP 200 + tool_success=false"（不是只有 HTTP≥400）；
    (b) EMPTY 目前**只能**由 RAG 的 rag_used_chunks=0 判定（不可由 content 推断）。
```

## 5. HTTP Mapping

| Business Outcome | HTTP Status | 是否合理 | 说明 |
| --- | --- | --- | --- |
| SUCCESS | 200 | **合理** | 传输成功 + 业务成功 |
| EMPTY | 200 | **合理** | 正常完成、无可提供结果（非错误） |
| REFUSED | 200 | **合理** | 预期安全行为；拒绝是"成功响应的业务语义" |
| FAILED | 4xx / 5xx | **合理**（当前实现） | 传输层如实反映失败 |
| FAILED | **200** | **现状例外（Tool 业务失败）** | 不改变传输语义，但 **HTTP 200 ≠ 业务 SUCCESS** |

```text
结论：HTTP status 是**传输层**事实，Business Outcome 是**业务层**事实；两者**不一一对应**。
建议（未来，不在本阶段）：**保持既有 HTTP 语义不变**（避免破坏客户端/重试策略），
业务结果由 outcome 字段表达；仅当未来统一 REST 语义时才考虑把 Tool 业务失败改为 5xx（属破坏性变更）。
```

## 6. Route Mapping

```text
route = **capability selection**（选中了哪种能力）      ← 不代表结果
outcome = **request result**（请求最终是什么结果）      ← 与 route 正交
```

| Route | 合法 Outcome 组合 | 现状覆盖 |
| --- | --- | --- |
| `rag` | SUCCESS · EMPTY · FAILED | ✓（正常 / 空检索 / LLM·RAG 失败） |
| `tool` | SUCCESS · FAILED | ✓（tool_success true/false；**Note**：200+false 的 FAILED 目前未被任何字段统一表达） |
| `text_to_sql` | SUCCESS · REFUSED · FAILED ·（EMPTY?） | ✓ 前三者；EMPTY 见下 |

```text
candidate decision（需产品确认）：T2SQL 成功执行但 **row_count = 0** ⇒ 判定 **SUCCESS**
    理由：查询**已回答**"无匹配数据"（LLM + Validator + Executor 全部完成）；
          与 RAG 空检索不同（RAG 空检索时 LLM **未被调用**，根本没有生成结果）
    ⇒ EMPTY 仅用于"系统无法给出任何业务答复"的情形（当前 = RAG 0 chunks）
```

## 7. Execution Mapping

```text
LLM execution status   ≠ Assistant outcome
Tool execution status  ≠ Assistant outcome
RAG execution status   ≠ Assistant outcome
SQL executor status    ≠ Assistant outcome
```

| 组合 | 结果 | 现状 |
| --- | --- | --- |
| LLM SUCCESS + Tool SUCCESS + Assistant **FAILED** | 必须可表达 | **Not observable today**（无已知触发路径：组装/序列化失败会抛异常 → 500 且**无包络**）→ projection 用例验证 Contract **能容纳**（status≥400 ⇒ FAILED，即便携带 success 信号） |
| LLM attempt1 FAIL(validator) + attempt2 SUCCESS | Assistant SUCCESS | ✓（2 次 LLM 调用仍是 SUCCESS；**LLM call 数不是 outcome 依据**） |
| LLM SUCCESS + refusal | REFUSED | ✓ |
| Tool SUCCESS + HTTP 200 | SUCCESS | ✓ |
| Tool FAILED + HTTP 200 | **FAILED** | ✓（tool_success=false 判定） |

## 8. Metadata Classification

| field | category | can_determine_outcome | reason |
| --- | --- | --- | --- |
| `refused` | **明确业务语义** | ✓ → REFUSED | 系统主动拒绝的终态标记 |
| `tool_success` | **明确业务语义** | ✓ → FAILED（false 时） | Tool 业务结果布尔；HTTP 200 亦可能是 FAILED |
| `rag_used_chunks` | 辅助信号 | ✓（0 → EMPTY，**仅 RAG**） | 空检索唯一可判定信号；但语义绑定 rag |
| `row_count` / `truncated` | 辅助信号 | ✗ | 0 行仍为 SUCCESS（见 §6 candidate decision） |
| `decision_source` / `route_reason` | 技术元数据 | ✗ | 路由说明（规则命中），与业务结果无关 |
| `request_id` | 技术元数据 | ✗ | 关联 ID（Trace 用） |
| `project_id` / `knowledge_scope` / `selected_tables` | 技术元数据 | ✗ | 作用域/上下文标识 |
| `execution_time_ms` | 技术元数据 | ✗ | 性能指标（非结果） |
| `sql` | 技术元数据（当前对客户端可见） | ✗ | 生成的只读 SQL；**不得**进入 outcome payload |

```text
原则：技术元数据**不得**被误当作 Business Outcome（尤其 execution_time_ms / row_count /
      request_id 都不能推断 success）。
```

## 9. Error Classification（仅评估，不实现）

```text
问题：FAILED 是否需要 error_class 辅助字段？
候选（枚举）：LLM_ERROR · RAG_ERROR · TOOL_ERROR · TEXT_TO_SQL_ERROR ·
             SQL_EXECUTION_ERROR · CONFIG_ERROR · INTERNAL_ERROR
```

| 维度 | 结论 |
| --- | --- |
| 是否需要 | **中度需要**（诊断/告警分流有价值；客户端 UI 分流多数只需 outcome 本身） |
| 是否安全 | 只有**服务端 allowlist 映射**才安全；禁止透传异常原文 |
| 是否足够稳定 | **当前异常类名不是稳定公共契约**：`LLMRequestError` / `TextToSQLRetryExceededError` 等属内部实现类，重构即变；且现状已通过 HTTP `detail` 暴露类名（粒度不稳定、非结构化） |

```text
结论：error_class 应作为 **后续可选扩展**（需先冻结枚举 + 明确映射层 + 版本策略），
      本阶段不实现、不写入任何字段。
```

## 10. Security

```text
候选 Contract 的**输入白名单**（超出即为设计缺陷）：
    status_code · route · refused · tool_success · rag_used_chunks
禁止进入判定或输出：
    exception message · prompt · messages · system prompt · SQL（作为 outcome 输入）·
    Tool arguments · ToolResult.data · RAG chunk 正文 · API key / Authorization /
    password / DATABASE_URL · stack trace · raw provider response · HTTP detail 原文
（测试内静态断言：projection 的 AST 标识符/字符串常量不含上述 token；且不读 detail / content / data）
```

## 11. Projection Cases（A ~ J）

| Case | 输入（真实驱动包络，除 J） | Outcome | 备注 |
| --- | --- | --- | --- |
| A | RAG 正常（chunks=1，LLM 1） | **SUCCESS** | ✓ |
| B | RAG 空检索（chunks=0，LLM 0） | **EMPTY** | ≠ FAILED ✓ |
| C | T2SQL refusal（refused=true，data=None） | **REFUSED** | ≠ FAILED ✓ |
| D | T2SQL 重试后成功（2 次 LLM，executor 成功） | **SUCCESS** | LLM 次数不参与判定 ✓ |
| E | T2SQL 重试耗尽（HTTP 500） | **FAILED** | ✓ |
| F | TOOL 成功（tool_success=true） | **SUCCESS** | ✓ |
| G | **TOOL 失败（HTTP 200 + tool_success=false）** | **FAILED**（审计确定） | HTTP 200 ≠ SUCCESS ✓ |
| H | RAG runtime 失败（HTTP 500） | **FAILED** | ✓（同包络的 EMPTY 被覆盖） |
| I | LLM 失败（HTTP 500） | **FAILED** | ✓ |
| J | LLM 成功 + Tool 成功 + 最终组装/传输失败 | **FAILED**（**Not observable today**） | projection 用例证明 Contract 可容纳（status≥400 ⇒ FAILED） |

```text
另含边界用例：REFUSED 与 tool_success=false 同时出现 → REFUSED（优先级）；
              content/data 不同但信号相同 → 相同 outcome（content/data 仅辅助）；
              metadata 缺失的 200 包络 → SUCCESS（不猜测失败）。
```

## 12. Candidate Contract：Option A vs Option B

| 维度 | Option A：仅 `outcome` | Option B：`outcome` + `error_class` |
| --- | --- | --- |
| 表达能力 | 覆盖 4 态（本阶段全部真实行为） | 更强（FAILED 可细分） |
| 安全性 | 高（4 个固定枚举，无泄露面） | 需 allowlist 映射层（否则易泄露类名/细节） |
| 客户端复杂度 | 低（单字段 4 值） | 中（需处理二级枚举 + 未知值兜底） |
| 向后兼容 | 纯增量（可忽略即兼容） | 同上，但枚举需长期冻结 |
| 未来 Trace 使用 | 足够（请求级终态） | 需额外存储/映射 |
| 稳定性风险 | 低 | 中（枚举 = 长期契约；当前类名不稳定） |

```text
Recommended future contract（**本阶段不实现**）：
    Assistant Outcome = 单一枚举 outcome ∈ {SUCCESS, EMPTY, REFUSED, FAILED}
        ⇒ **Option A**（最小方案）
    判定优先级：REFUSED > FAILED > EMPTY > SUCCESS（§4）
    判定输入白名单：§10（4 个业务信号 + status_code）
    error_class：作为**后续可选扩展**（Option B），仅在出现真实诊断/分流需求时，
        以服务端 allowlist 枚举形式引入（禁止透传异常类名/原文）
```

## 13. HTTP Contract 变化方案 & Trace 集成方向（只比较，不实现）

```text
chat 响应暴露方案：
    (a) 顶层新增 `outcome` 字段 —— 最清晰，但**改变 4 字段 envelope**
        （既有严格断言与客户端校验会受影响）⇒ 仅在允许破坏性变更时考虑
    (b) `metadata.outcome` —— **增量且兼容**（envelope 不变；metadata 无类型约束，需文档约束）
        ⇒ 未来**最小增量**推荐
    (c) 仅 Trace 内部提供 —— 不改 chat 契约，但客户端拿不到 outcome（仅观测场景可用）

Trace 集成方案：
    方案 1 直接复用 Chat Outcome —— 需要该 outcome **已被持久化或可推导**；
        现状 Trace 只读 records，refusal / empty 无记录 ⇒ 仍需新存储
    方案 2 Trace 单独产生 —— 同一概念两处产生 ⇒ 语义漂移风险（不推荐）
    方案 3 **统一 Assistant Outcome Read Model**（Trace 从该读模型查询）—— 单一事实来源，
        与 Trace 既有"只读组合"模式一致，安全边界集中 ⇒ **推荐方向**
        代价：需要新的持久化边界（表/保留期/索引/迁移评估）—— 属未来阶段
```

## 14. Current Limitations

```text
当前系统**无法表达**（Step 61 结论，本阶段未解决）：
    * 请求级终态（success / empty / refused / failed）—— 分散在 HTTP status、metadata
      布尔、固定文案与缺省值中
    * route 与 outcome 的组合（Trace 无 route）
    * "底层成功 + 请求级失败"（Case J，Not observable today）
    * 失败原因的**结构化**表达（Tool 记录 error_code/error_type 在通用异常时为 None；
      T2SQL 生成失败 / Validator 耗尽 / Executor 失败**状态码相同**，仅 detail 类名不同）
    * Trace 侧 outcome（无请求级记录；三段 records 均为执行事实）
本阶段**未评估**（属未来阶段）：
    * error_class 枚举冻结与映射层设计 · T2SQL 0 行语义的产品确认
    * outcome 的存储边界（表/保留期/索引/迁移）与多实例一致性
    * 客户端兼容成本（引入 metadata.outcome 的文档与 SDK 影响）
    * 与 Pagination（Step 59 DEFER）的相互作用
测试覆盖：本阶段为**契约级**审计（离线 21 用例）；未新增 DB-gated 用例
    （持久化层关联已由 Step 60/61 覆盖）
```
