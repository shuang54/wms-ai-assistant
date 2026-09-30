# Phase 3.12 Step 67 — Assistant Timeline HTTP Read API 可行性审计

> 只审计：**未实现** HTTP endpoint、未注册 router、未改 DTO / Trace API / DB。
> Production business changes = **0** · DB writes = **0** · Network = **0**。
> 审计测试：`tests/test_assistant_timeline_api_audit.py`（26；离线 · Fake 边界 · 静态检查）。

---

## 1. Current Projection

```text
AssistantTimelineQueryService（Step 66）
    ↓ 四个既有读边界（LLM / Tool 行 / RAG / Outcome）
AssistantTimeline（frozen）
    assistant_request_id
    llm_events[]      created_at ASC, id ASC
    tool_events[]     id ASC
    rag_events[]      id ASC
    outcome_event     0 或 1（UNIQUE）

AssistantTimelineEvent（frozen；9 字段）
    assistant_request_id · source · event_type · source_id
    started_at · finished_at · duration_ms · created_at · status

已锁定的限制（本阶段不解除）：
    NO merged events[] · NO event_id · NO sequence · NO span_id ·
    NO parent_event_id · NO cross-group ordering · NO pagination
```

字段矩阵（Step 65/66）：LLM / Outcome 只有 **DB 钟** `created_at`；
Tool / RAG 只有 **App 钟** `started_at / finished_at / duration_ms`；
DTO 层强制**钟域互斥**（`created_at` 绝不成 `finished_at`）。

## 2. API Candidate（NOT IMPLEMENTED）

```text
GET /api/observability/assistant-timeline/{assistant_request_id}

状态：NOT IMPLEMENTED（审计确认 App 中**不存在** timeline 路径；
     backend/app/api/ 无 assistant_timeline*.py；main.py 未注册）
装配约定（沿用既有）：app.include_router(router, prefix="/api",
                     tags=["observability"])
```

## 3. Response Candidate

```json
{
  "assistant_request_id": "...",
  "llm_events":    [ { "source": "llm_usage",        "event_type": "LLM",     "source_id": 1, "started_at": null, "finished_at": null, "duration_ms": null, "created_at": "2026-09-30T10:00:00+00:00", "status": null } ],
  "tool_events":   [ { "source": "tool_execution",   "event_type": "TOOL",    "source_id": 2, "started_at": "...", "finished_at": "...", "duration_ms": 40.0, "created_at": null, "status": "success" } ],
  "rag_events":    [ { "source": "rag_execution",    "event_type": "RAG",     "source_id": 3, "started_at": "...", "finished_at": "...", "duration_ms": 150.0, "created_at": null, "status": null } ],
  "outcome_event": { "source": "assistant_outcome",  "event_type": "OUTCOME", "source_id": 4, "started_at": null, "finished_at": null, "duration_ms": null, "created_at": "...", "status": "SUCCESS" } }
}
```

设计结论（不拍板，给出依据）：

```text
是否需要 wrapper DTO          → **是**（建议专用 API DTO + 显式逐字段映射，
                                 与 AssistantTraceResponse 的既有风格一致；
                                 不直接把 frozen dataclass 当响应模型）
是否直接复用 AssistantTimeline → 否（内部读模型；API 需要自己的字段白名单边界）
是否需要 API DTO              → 是（9 字段 → 8~9 字段映射；可随契约演进）
是否需要隐藏 source_id        → **开放决策**（见 §6）
```

## 4. Security

```text
允许输出：assistant_request_id · source · event_type · source_id ·
         timestamps · duration_ms · status
绝不输出：prompt / messages / system_prompt / SQL / query / RAG chunk content /
         embedding / similarity / tool arguments / tool result / API key /
         authorization / password / database URL / raw provider response /
         exception message / stack trace
审计方式：候选响应 JSON 全量字符串断言（含哨兵值）+ 字段白名单断言 +
         dataclasses.asdict 不含 Session/Engine/Connection
```

## 5. Ordering

```text
HTTP 契约必须写明：
    llm_events   created_at ASC, id ASC
    tool_events  id ASC
    rag_events   id ASC
    outcome_event 至多 1 条
    **Grouped ordering only** —— 四组各自有序，组间**无**全局顺序
实现约束：HTTP 层不得 sorted(llm + tool + rag + outcome)；
         不得生成 sequence；不得声称"完整时间线"
（Service 静态检查：无 enumerate / uuid4 / hash；sorted() 仅 3 次且均在组内）
```

## 6. source_id Decision（开放问题，**不改 DTO**）

```text
事实：source_id = 内部 BIGINT 主键
     （llm_usage_record.id / tool_execution_record.id /
      rag_execution_record.id / assistant_outcome_record.id）
     —— Step 66 §九 规定身份必须是真实主键，禁止生成 ID

风险：暴露内部存储主键（自增 BIGINT 可反映表规模 / 写入量级）
先例不一致：LLM Trace **暴露** id（Step 37）；Tool / RAG Trace **不暴露**（Step 48）
取舍：隐藏 → 事件**没有任何身份**（无 event_id / sequence 可替代）
建议（供实现阶段决定）：**暴露 source_id**，并在 API 文档中标注
     "内部事件标识，仅用于同一次请求的事件区分；不作为跨请求/跨系统引用"
     （与既有 LLM Trace 段一致；若未来引入 event_id 再切换）
本阶段：**只记录，不改 DTO**（Step 67 §五）
```

## 7. Error Contract

```text
沿用 Assistant Trace 既有约定（不重新设计全局异常系统）：
    400  assistant_request_id 非法（服务层 ValueError：非 str / 空白 / >128）
    422  路径参数长度越界（FastAPI Path 校验，1..128）
    502  持久化读边界不可用（LLMUsage / Tool / RAG / Outcome RepositoryError）
    500  其它未预期错误
要求：detail 固定文案（"助手链路观测数据不可用"类），
     **绝不**把 DB 异常文本 / SQL / 内部模块路径回给客户端
     Service 侧**原样透传**下游异常（不吞、不降级为空 Timeline）
```

## 8. Payload（synthetic；不实现分页）

```text
合成估算（候选 JSON，紧凑序列化；assistant_request_id="A"）：
    10  events（llm5/tool2/rag3）  → 2.11 KiB
    50  events（llm25/tool12/rag13）→ 9.53 KiB
    100 events（llm50/tool25/rag25）→ 18.82 KiB   （< 64 KiB 触发线）
    隐藏 source_id 后：2.0 / 8.8 / 17.4 KiB（量级不变）
真实规模（Step 54 实测单请求上限 6 条观测 → 7 events）→ **1.38 KiB**

结论：**Pagination = DEFER**（当前无触发证据；Step 54 触发条件仍适用：
      单请求 >10 条记录 / 响应 >64 KiB / 出现多轮 Tool / Agent / 统一时间线）
```

## 9. Authorization

```text
现状：项目**没有** request-level 认证 / 授权
     （全仓无 HTTPBearer / OAuth2 / APIKeyHeader / current_user / tenant_id；
      仅 db/session.py 有 Depends(get_db) 的 DB 会话依赖）

风险（必须记录）：
    知道 assistant_request_id ⇒ 可读取该请求的 Timeline（无归属校验）
    但这**不是** Timeline 新增的问题：既有 Assistant Trace API 同样是
    "知道 id 即可读"（同级别暴露）；Timeline 未扩大攻击面

本阶段：只记录，**不实现认证 / 授权系统**（Step 67 §十五）
```

## 10. Decision

```text
READY FOR API IMPLEMENTATION

依据：
  ① 契约稳定：AssistantTimeline / Event 均 frozen + 字段白名单 + 校验，
     Step 66 已用 33 项测试锁定（shape / 排序 / 隔离 / 安全）；
  ② 语义边界清晰：分组投影、无全局序、无 event_id/sequence、不合并；
  ③ 未知请求 / 历史缺段 / 错误映射均可复用既有 Trace 约定（无需新框架）；
  ④ 校验复用既有 1..128 ID 规则，不引入新 ID 框架；
  ⑤ 体积远低于分页触发线（真实规模 ≈1.4 KiB；100 events ≈19 KiB）。

实现阶段必须遵守（不是 blocker，是契约条件）：
  · 专用 API DTO + 显式逐字段映射（不用 dataclass 直接序列化）
  · 只做分组排序，绝不在 HTTP 层合并 / 排序四组 / 生成 sequence
  · unknown request → 200 + 空分组（**不是** 404）
  · source_id 按 §6 建议暴露并文档化（或由下一步明确"隐藏"并接受无身份）
  · 错误映射沿用 400/422/502/500；不泄漏 DB 异常文本
  · 不新增认证系统（如需 request-level 授权，另开阶段）
```

## 11. Limitations

```text
* source_id 暴露与否是**开放决策**（本阶段只记录风险与取舍）
* 无 request-level 授权：知道 id 即可读（与既有 Trace API 同级别）
* 体积为合成估算（真实 DTO 形状 + 合成值），非生产取样
* 未评估未来 HTTP 层的缓存 / 限流 / 观测 / 版本化策略
* 未评估 Timeline 与 Trace 并存时的客户端选择成本（是否合并端点另议）
* 钟域差异仍在：LLM/Outcome 用 DB 钟、Tool/RAG 用 App 钟 —— HTTP 文档必须写明
* 本阶段**未运行**完整 pytest / DB 套件（按 §十八 只跑审计文件 + compileall）
```
