# Phase 3.12 Step 47 — Assistant Trace RAG Integration Audit

> **审计 + 设计**：只确定「RAG Persistent Read Model 如何安全进入 Assistant Trace」。
> **未修改** `AssistantTraceResponse` / `AssistantTraceQueryService` /
> `/api/observability/assistant-trace/{request_id}` / RAG Runtime / RAG Persistence /
> LLM Usage / Tool Execution；Production Code = 0；DB Schema unchanged；0 DB 写入；
> 0 DeepSeek / 0 网络。
>
> 契约测试：`tests/test_assistant_trace_rag_integration_contract.py`（34，纯离线）。

---

## 1. Current State

```text
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
AssistantTraceQueryService.get_trace(A)          （Read Model Composition）
        ├── LLMUsageQueryService.list_by_assistant_request_id(A)
        │       → ai_ops.llm_usage_record（created_at ASC, id ASC）
        └── ToolExecutionPersistentQueryService.list_by_request_id(A)
                → ai_ops.tool_execution_record（id ASC）
        ↓
AssistantTraceView（frozen；assistant_request_id / llm_usage / tool_executions）
        ↓ 显式逐字段映射
AssistantTraceResponse{ assistant_request_id, llm_usage[9], tool_executions[11] }
```

```text
字段/类型：assistant_request_id(str) · llm_usage(list[LLMUsageTraceResponse；9 字段])
          · tool_executions(list[ToolExecutionTraceResponse；11 字段])
序列化：  显式映射函数（_llm_usage_response / _tool_execution_response /
          _to_trace_response）；tuple → list；无 vars/asdict/model_dump
空结果：  任一侧或两侧空 → []（仍 200；**不是** 404）
查询失败：ValueError→400；路径长度越界→422（FastAPI Path）；
          LLMUsageRepositoryError / ToolExecutionRepositoryError → **502**；
          其它 → 500（不暴露 traceback / SQL / 凭据 / 内部模块路径）
排序：    LLM = created_at ASC, id ASC；Tool = 落库顺序（id ASC）；组合层**不重排**
安全过滤：无 prompt / messages / raw_response / Tool arguments / ToolResult.data /
          SQL / DB 连接 / Session / 凭据
注：`assistant_trace_read_model.py` 不存在 —— Read Model 即
    `AssistantTraceView`（定义在 `assistant_trace_query_service.py`）。
```

## 2. Proposed State（设计；未实现）

```text
AssistantTraceQueryService
        ├── LLMUsageQueryService                     （不变）
        ├── ToolExecutionPersistentQueryService      （不变）
        └── RagExecutionPersistentQueryService       （新增；**唯一** RAG 数据源）
                ↓
AssistantTraceView + rag_executions
        ↓
AssistantTraceResponse{ assistant_request_id, llm_usage, tool_executions,
                        rag_executions }              ← 仅**新增**一个数组
```

```text
RAG Trace DTO（设计态；建议名 RagExecutionTraceResponse / View）：
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids     （13）

刻意**裁剪**：数据库主键 `id`
    理由：① 与 ToolExecutionTraceResponse（11 字段、无 id）一致 —— Trace 消费方
          不需要存储主键；② 排序已由仓储 `ORDER BY id ASC` 保证；
          ③ 不把存储内部细节暴露给 HTTP 客户端。
    （对照：LLMUsageTraceResponse 保留 id —— 那是 LLM 侧既有契约，不改。）

字段必要性复核（§六）：
    started_at / finished_at / duration_ms —— **必需**：Trace 的核心价值是时间序与
        耗时；且 LLM/Tool 两侧都已有时间字段，缺失则无法对齐阶段耗时。
    result_count / used_chunks_count / top_k / context_truncated / context_chars /
        reranker_used / rerank_elapsed_ms —— **必需**：检索质量的诊断事实，
        可解释"为什么这次回答这样"；且全部为 Trace-safe 数值 / 布尔。
    chunk_ids / document_ids —— **谨慎评估后保留**，见 §5。
```

## 3. Read Sources

| Trace 段 | Query Service | Repository | 表 | 排序 |
| --- | --- | --- | --- | --- |
| `llm_usage` | `LLMUsageQueryService.list_by_assistant_request_id()` | `LLMUsageRepository` | `ai_ops.llm_usage_record` | created_at ASC, id ASC |
| `tool_executions` | `ToolExecutionPersistentQueryService.list_by_request_id()` | `ToolExecutionRepository` | `ai_ops.tool_execution_record` | id ASC |
| `rag_executions`（提案） | `RagExecutionPersistentQueryService.list_by_request_id()` | `RagExecutionRepository` | `ai_ops.rag_execution_record` | `id ASC` |

```text
约束（§十二）：
    * 不新增第二个 RAG Query Service（复用 Step 46 已有的
      RagExecutionPersistentQueryService，方法名 list_by_request_id 已与 Tool 一致）；
    * AssistantTraceQueryService **不得**直接访问 SQLAlchemy / rag_execution_record；
      只能经 Read Model → Query Service。
```

## 4. Correlation

```text
一次 Assistant 请求 = request_id = A（唯一生成点：AIOrchestratorService.execute）
    LLM Usage        assistant_request_id = A   （因 request_id 被 Provider id 占用）
    Tool Execution   request_id          = A
    RAG Execution    request_id          = A    （与 Tool 同名同义）

禁止新增：rag_request_id / retrieval_request_id / trace_request_id
（契约测试静态扫描 backend/app/**：无上述标识符）
```

## 5. Security

```text
允许（Trace-safe）：
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms

谨慎评估 → 保留 + 记录风险：
    chunk_ids / document_ids
      · 仅为知识库行的**数值 ID**（无正文 / 无 similarity / 无 embedding）；
      · 暴露级别与既有 `POST /api/ai/chat` RAG 响应 `data.sources[].chunk_id /
        document_id` **完全一致**（同一请求所有者），不因 Trace 扩大暴露面；
      · **风险记录**：若 Trace API 未来开放给更宽受众（多租户 / 第三方运维），
        ID 可间接反映"命中了哪些文档"，届时应降级为 `chunk_count` /
        `document_count` 或整体移除。本阶段不擅自放宽。

禁止（永不进入 Trace）：
    query · answer · chunk content · document content · similarity · embedding ·
    prompt · LLM messages · raw LLM response · SQL · DB connection · credentials ·
    Authorization · API key · password · database URL · Session / ORM 对象 ·
    internal traceback · 数据库主键 id

手段：显式逐字段映射（禁 vars / __dict__ / asdict / model_dump）；
      response_model 同时充当字段过滤边界。
```

## 6. Error Semantics

```text
原则不变：empty result ≠ query failure

RAG 侧：
    无匹配 → []（不是错误）
    DB 故障 → RagExecutionRepositoryError 原样透传（**绝不**降级为 []）
    非法 request_id → 校验先于 Repository（TypeError / ValueError）

组合层（未来）：
    LLM 成功 + Tool 成功 + RAG 失败 → 整个 Trace 请求按既有语义呈现为数据源不可用
    （不返回 200 + 半份 Trace；不隐藏数据库错误）
    POST /api/ai/chat 业务请求**不受影响**（Trace 是只读查询，与业务链路解耦）

⚠ 实现缺口（本阶段只记录，不修）：
    当前 api/assistant_trace.py 的 502 分支显式列举
    (LLMUsageRepositoryError, ToolExecutionRepositoryError)；
    RagExecutionRepositoryError **不在**其中 → 若直接接入会落到 catch-all
    `except Exception` → 500（语义误导：应为 502）。
    Step 48 实现时必须把该异常加入 502 元组（契约测试已锁定该缺口）。
```

## 7. Backward Compatibility

```text
提案 = additive change：新增 `rag_executions: []`（默认空数组）
    旧字段不改名 · 不删除 · 语义不变
    旧客户端：忽略未知字段即可（JSON 解析不受影响）
    空 Trace（llm_usage=[]、tool_executions=[]、rag_executions=[]）仍 200
本阶段**未**实际修改 HTTP Response（契约测试断言当前 schema 仍为 3 个字段）。
```

## 8. Runtime vs Persistent

```text
Assistant Trace 的 RAG 数据源必须是 **PostgreSQL**（RagExecutionPersistentQueryService），
**不是** InMemoryRagExecutionCollector：
    · restart / 多 worker / Runtime 窗口淘汰（max 1000）场景下，
      内存视图会缺失 → 出现"LLM/Tool 有历史、RAG 没历史"的假象；
    · 与既有 Tool 段一致（Step 41 起 Tool 已固定使用 Persistent 读边界）。
契约测试：Trace 组合层与 API 模块**不** import 任何 Collector /
    rag_observability_query_service（Runtime 读边界）。
```

## 9. Project Authorization Limitation

```text
现状记录（不自行新增 project_id）：
    · Trace 端点入参**只有** assistant_request_id（路径参数；长度 1–128，非空白）；
      没有任何 project 维度过滤或授权；
    · RAG Persistent Record **不含** project_id（Step 45/46 的明确设计选择）；
    · 因此：能否查到某条 Trace 完全取决于是否持有该 request_id
      （uuid4，不可枚举；当前单租户/内部运维场景可接受）；
    · 若未来引入多租户，必须**另开阶段**设计 project 级授权，
      而不是在 Trace 内隐式拼接 project 过滤。
```

## 10. Deferred

```text
HTTP API modification deferred       （不改 AssistantTraceResponse）
AssistantTraceQueryService 接入 deferred（不改组合层）
RAG Trace DTO 生产实现 deferred
Assistant Trace 排序方案 B（统一 events 列表）deferred —— 本阶段结论：采用方案 A
     （三个独立列表；不为了统一排序重构现有 Trace）
project-level authorization deferred  （另开阶段）
RAG Trace Metrics / Dashboard / OpenTelemetry / Conversation / Memory / Agent /
MCP / Streaming —— 均不做
```

## 11. Tests

```text
tests/test_assistant_trace_rag_integration_contract.py   34 passed（纯离线）
    CurrentTraceContract(6)：响应字段 / Read Model frozen + 2 源 /
        嵌套 DTO 字段数（9 / 11）/ 错误映射现状（含 RAG 缺口）/
        OpenAPI 无 rag_executions / 组合层无写能力
    RagReadBoundary(6)：list_by_request_id 存在且只读 / 空 → [] /
        失败 → typed error / 校验先于 Repository / ORDER BY id ASC / 显式列
    ProposedDtoMapping(4)：13 字段保留 / 主键被裁剪 / NULL 保持 / 顺序保持
    Security(4)：字段白名单 / 序列化白名单 / chunk·document 暴露级别与既有
        /api/ai/chat 一致（无 content）/ 无新的 correlation id
    Correlation(2)：request_id == assistant_request_id / 三源命名一致
    Ordering(2)：方案 A（三列表，无 events）/ 各列表保持自身顺序
    RuntimeVsPersistent + Compat(3)：Tool 段用 Persistent 服务 /
        Trace 不引用任何 Collector / 新增字段是 additive
    ProjectAuthorization(3)：Record 无 project_id / 端点仅 1 个参数 /
        组合层仅按 request_id 查询
```

## 12. Limitations

> 后续（Phase 3.12 Step 48）：本设计已**实现** —— `rag_executions` 已进入
> Assistant Trace 响应（additive），RAG 段数据源 = `RagExecutionPersistentQueryService`
> （PostgreSQL），`RagExecutionRepositoryError` 已加入 502 元组
> （见 `Phase 3.12 Step 48 — Assistant Trace RAG Integration.md`）。
> 仍 deferred：统一 events 排序 · project 级授权 · retention。

```text
* 本阶段为设计态：RAG Trace DTO / 映射函数均未在生产代码中实现
  （映射只存在于测试内的 `_ProposedRagExecutionTrace`）
* 502 映射缺口已记录但未修（实现阶段的必做项）
* chunk_ids / document_ids 的暴露风险已记录，未做降级
* 未评估 Trace 的跨项目 / 跨租户访问控制（另开阶段）
* 未评估 RAG 段与 LLM 段的时间对齐（RAG 内部 LLM 调用与 RAG 观测的时间关系）
* lint unavailable（环境限制）
```
