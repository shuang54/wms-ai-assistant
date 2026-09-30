# Phase 3.12 Step 65 — Unified Timeline 可行性与 Contract Audit

> 只读审计：**未实现 Timeline**、未新增表 / 索引 / API / migration / 运行时逻辑。
> Production code changes = **0** · DB writes = **0** · Network calls = **0**。
> 审计测试：`tests/test_assistant_timeline_audit.py`（30 项；离线 · Fake 数据 · 静态检查）。

---

## 1. Audit Scope

回答：当前四类持久化事实（LLM Usage / Tool Execution / RAG Execution /
Assistant Outcome）能否构成一个**无歧义的** Unified Assistant Timeline？

原则：**不伪造顺序**（没有事件序就写 `NOT_AVAILABLE`，不用 `enumerate()`
把查询顺序当业务顺序）· **不伪造 ID**（没有 `event_id` 就不生成 uuid/hash）·
**不猜语义**（不从时间戳反推 route / attempt）。

---

## 2. 当前四类 Event 数据源

| source | 表 | 关联键 | 时间字段 | 生成方 |
| --- | --- | --- | --- | --- |
| LLM | `ai_ops.llm_usage_record` | `assistant_request_id` + `request_id`(Provider) | `created_at` | **DB** `now()`（插入时刻） |
| Tool | `ai_ops.tool_execution_record` | `request_id`(= Assistant Request) | `started_at` / `finished_at` / `duration_ms` | **App** `datetime.now(UTC)` + `perf_counter` |
| RAG | `ai_ops.rag_execution_record` | `request_id`(= Assistant Request) | `started_at` / `finished_at` / `duration_ms` | **App** `datetime.now(UTC)` + `perf_counter` |
| Outcome | `ai_ops.assistant_outcome_record` | `assistant_request_id`(**UNIQUE**) | `created_at` | **DB** `now()`（写入时刻） |

字段矩阵（`AVAILABLE` / `NOT_AVAILABLE`）在
`tests/test_assistant_timeline_audit.py::FIELD_MATRIX` 中逐字段锁定，
并由 `test_1*` 与真实 ORM 列**双向**核对。

---

## 3. Correlation Audit

```text
LLM      assistant_request_id ✅（Assistant Trace ID）· request_id ✅（Provider 请求 ID）
         ⇒ 两个维度**严格分离**：真实持久化边界读 observation.request_id（Provider）
           与 current_assistant_request_id()（Scope）；未绑定 Scope → NULL（不回填）
Tool     request_id == Assistant Request ID ✅（离线 E2E 实测：snapshot.request_id
         == metadata.request_id；无独立 Tool scope）
RAG      request_id == Assistant Request ID ✅（RagService 观测 request_id 唯一来源 =
         current_assistant_request_id()；未绑定 → **不记录**）
Outcome  assistant_request_id ✅ 且 UNIQUE（一次请求至多 1 条终态；first-write-wins）
```

结论：**四类记录都可按 Assistant Request 关联**；无跨请求串线风险（Step 64
交叉隔离测试已覆盖）。

---

## 4. Timestamp Audit

```text
created_at（LLM / Outcome）   DB 生成（server_default now()）· tz-aware ·
                              = **落库时刻**，不是请求/调用开始时刻
started_at / finished_at      App 生成（datetime.now(UTC)）· tz-aware · 无 server_default
duration_ms                   App 生成（perf_counter 差值）· 与墙钟无关
```

禁止互换：`created_at ≠ started_at`、`finished_at ≠ created_at`；LLM / Outcome
**没有**开始时间，Tool / RAG **没有**写入时间（审计以列存在性断言锁定；
并静态检查读路径不做 `created_at → started_at` 改名）。

---

## 5. Ordering Audit

| 范围 | 排序键（真实存在） | 可行性 |
| --- | --- | --- |
| LLM 行内 | `created_at ASC, id ASC`（Step 37 读路径） | ✅ 稳定（含 tie-breaker） |
| Tool / RAG 行内 | `id ASC`（落库顺序） | ✅ 稳定 |
| Outcome | UNIQUE 键 → 至多 1 行 | ✅ 无歧义 |
| LLM ↔ Outcome | 同 **DB 钟**（两者 `created_at` 均为 DB `now()`） | ⚠️ DERIVED（顺序=**落库**序，非执行序） |
| Tool ↔ RAG | 同 **App 钟**（同进程墙钟） | ⚠️ DERIVED（墙钟可比；无单调时钟保证） |
| {Tool,RAG} ↔ {LLM,Outcome} | **跨钟域**（App 钟 vs DB 钟） | ❌ UNSAFE_TO_DERIVE |
| 全局事件序 | —— | ❌ **NOT_AVAILABLE** |

**关键结论**：跨钟域比较在单机部署下"看起来能排"，但契约上不可保证
（应用 / 数据库可能不同主机 / 不同时钟源）⇒ 审计标记 `UNSAFE_TO_DERIVE`，
不允许在实现中默认使用。

---

## 6. T2SQL Retry 是否可排序

```text
落库顺序（created_at, id）        ✅ 可得（attempt1 < attempt2）
"这是第几次 attempt"             ❌ NOT_AVAILABLE（LLM 表无 attempt / round / route / success）
Validator 拒绝事件               ❌ 完全没有记录
Executor 执行事件                ❌ 完全没有记录
⇒ 只能得到「两次 LLM 调用发生的先后」，**不能**得到
  「attempt1 → Validator reject → attempt2 → Executor」这条业务时间线
```

## 7. RAG 是否可排序

```text
RAG 行内（id / started_at, finished_at）  ✅
与 LLM / Outcome 的关系                   ⚠️ 跨钟域（UNSAFE）
运行期失败时 RAG 行可能**不存在**          ❌（执行未走到观测写入）
  ⇒ 投影必须允许"RAG 段为空"，**不得**补造事件
```

## 8. Tool 是否可排序

```text
Tool 行内（id ASC；started_at/finished_at 自洽）  ✅
round 恒为 1（/api/ai/chat 单次执行）              ✅ 无 multi-round
与 LLM / Outcome 的关系                            ⚠️ 跨钟域（UNSAFE）
```

## 9. Outcome 是否可排序

```text
无行内排序问题（每请求至多 1 行）；与 LLM 同 DB 钟可比（DERIVED）
但 created_at = **写入时刻**（best-effort 持久化延迟之后）⇒ 不能当作"请求结束时刻"
```

## 10. Event ID 是否存在

```text
event_id / span_id / parent_event_id / trace_id：四表 + 四 DTO + Trace View +
Tool Snapshot **全部不存在** ⇒ event_id = **NOT_AVAILABLE**
审计同时静态检查读路径 / 持久化层**没有** uuid4 / uuid1 / event_id 标识符
```

## 11. Sequence 是否存在

```text
sequence / seq / step_index：四表与 Trace DTO **全部不存在** ⇒ **NOT_AVAILABLE**
读路径静态检查：无 enumerate / sorted / merge（即：既没有、也不伪造、也不重排）
```

## 12. 最小 Timeline Contract 建议（**仅设计，未冻结**）

```text
AssistantTimelineEvent（候选；本阶段不实现、不冻结）
    assistant_request_id : str          ✅ 四源齐备
    event_type           : LLM | TOOL | RAG | OUTCOME   ✅ 由 source 决定
    source_id            : BIGINT       ✅ 四表都有主键（Outcome 主键当前未暴露给读 DTO）
    started_at           : datetime     ⚠️ 仅 Tool / RAG 有；LLM / Outcome 只有 created_at
    finished_at          : datetime     ⚠️ 仅 Tool / RAG 有
    duration_ms          : float        ⚠️ 仅 Tool / RAG 有
    created_at           : datetime     ✅ LLM / Outcome（DB 钟）；Tool / RAG 无
    sequence             : ❌ 不可得（不得用查询序伪造）
    event_id             : ❌ 不可得（不得生成）
    parent / span        : ❌ 不可得
```

设计含义：**当前只能构造"按来源分组 + 组内有序"的投影**，
不能构造"单一全局有序事件流"。若要真正的 Unified Timeline，最小增量是：

```text
① 统一的**开始时间**语义（App 侧单调/墙钟 + 每事件 started_at 落库）
② 事件身份：event_id（或"来源+主键"的稳定复合键）+ 可选 parent/span
③ 缺失事件补记录：Router 决策 / Validator 判定 / Executor 执行
④ （可选）统一事件存储 / 视图 —— 属更大范围，未在本阶段评估
```

---

## 13. 当前缺失字段（汇总）

```text
event_id · span_id · parent_event_id · trace_id       ❌
sequence / 全局事件序                                   ❌
route（Outcome 表内无；只在内存 Result.metadata）        ❌
attempt / round（LLM 侧）                               ❌
success（LLM 侧：失败不落行）                            ❌
started_at / finished_at / duration_ms（LLM / Outcome）  ❌
created_at（Tool / RAG）                                ❌
Router / Validator / Executor / Tool 参数提取事件         ❌（完全没有记录）
跨钟域可比性（App 钟 vs DB 钟）                          ❌（UNSAFE_TO_DERIVE）
```

## 14. 是否可以进入 Timeline Implementation

```text
现状：Assistant Timeline 无歧义投影             ✅ **可做**（按来源分组 + 组内有序 +
                                                   显式标注 sequence/event_id = 不可得）
现状：Unified（单一全局有序事件流）Timeline      ❌ **BLOCKED**（缺少 §13 的身份 / 时间 / 事件）
若强行实现，唯一"合规"形态是：
    返回四段（llm / tool / rag / outcome）+ 每段内部有序 + 明确 not_available 标记
    （**不得**合并成一个 events[]，除非接受"顺序不可保证"）
```

## 15. Limitations

```text
* 排序可行性结论来自**字段与生成点**审计（静态 + 合成数据），未做真实多主机时钟偏移实验
* "跨钟域 UNSAFE" 是契约层结论：单机 docker-compose 下两钟可能实际一致，但不可依赖
* LLM ↔ Outcome 的 DERIVED 排序反映的是**落库顺序**，best-effort 持久化可能引入延迟
* 未评估 retention / 分区 / 归档对时间线完整性的影响
* 未设计 event_id 生成方案（何处生成、是否单调、是否跨进程唯一）
* 未评估统一事件存储（Event Store / 物化视图）的成本与运维边界
* 本阶段**未运行**完整 pytest / DB pytest（按任务书 §十六 只跑审计文件与 compileall）
```
