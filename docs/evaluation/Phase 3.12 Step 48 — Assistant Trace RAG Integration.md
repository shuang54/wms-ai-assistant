# Phase 3.12 Step 48 — Assistant Trace RAG Integration

> 把 Step 47 的设计落地：RAG Persistent Read Model 进入 Assistant Trace。
>
> 修改面（**仅 3 个生产文件**）：
> `services/assistant_trace_query_service.py`（Read Model + 组合）·
> `api/assistant_trace.py`（HTTP DTO + 映射 + 502）·
> `api/orchestrator_chat.py`（Composition Root 注入）。
> RAG Runtime / RAG Persistence / LLM Usage / Tool Execution / Router /
> Orchestrator / RAG Core / DB Schema 全部未改；0 DeepSeek / 0 网络。

---

## 1. Assistant Trace DTO

```text
新增 RagExecutionTraceView（services 层；frozen dataclass；13 字段）
    request_id · started_at · finished_at · duration_ms · result_count ·
    used_chunks_count · top_k · context_truncated · context_chars ·
    reranker_used · rerank_elapsed_ms · chunk_ids · document_ids
    · **不含**数据库主键 id（排序由仓储 ORDER BY id ASC 保证）
    · from_row() 显式逐字段映射（无 vars / __dict__ / asdict / model_dump）

新增 RagExecutionTraceResponse（API 层；Pydantic；同 13 字段）
    _rag_execution_response(view) 显式逐字段映射
```

## 2. RAG Persistent Read Integration

```text
AssistantTraceQueryService.get_trace(A)
    ├── LLMUsageQueryService.list_by_assistant_request_id(A)      （不变）
    ├── ToolExecutionPersistentQueryService.list_by_request_id(A) （不变）
    └── RagExecutionPersistentQueryService.list_by_request_id(A)  （Step 48 新增）
            → ai_ops.rag_execution_record（WHERE request_id = :p ORDER BY id ASC）
    ↓
AssistantTraceView{ assistant_request_id, llm_usage, tool_executions,
                    rag_executions }
    · 不直接访问 SQLAlchemy / rag_execution_record；只经 Query Service
    · 不重排 / 不去重 / 不合并（不引入 events）
    · RAG 仓储错误原样透传（绝不为 []）
```

## 3. Composition Root

```text
api/orchestrator_chat.py::get_assistant_trace_query_service()
    rag_execution_query_service=get_rag_execution_persistent_query_service()
        ↑ 应用级同一实例（services/rag_observability_runtime.py 持有）
    · API 层不创建 RagExecutionPersistentQueryService() / Repository() / Session()
    · Tool 段同理（Step 41 模式）；Trace = 三个持久化读边界
```

## 4. HTTP API

```text
GET /api/observability/assistant-trace/{assistant_request_id}
    {
      "assistant_request_id": "A",
      "llm_usage":        [...],      ← 不变
      "tool_executions":  [...],      ← 不变
      "rag_executions":   [...]       ← **新增（additive）**
    }
    · 字段名固定 `rag_executions`（非 rag / rag_usage / retrievals）
    · 空 → []（仍 200；不是 404 / 502 / 500）
    · response_model 继续承担 schema / 序列化 / 字段过滤（未绕过）
```

## 5. Error Mapping

```text
修复 Step 47 记录的缺口：
    except (LLMUsageRepositoryError,
            ToolExecutionRepositoryError,
            RagExecutionRepositoryError) → HTTP 502
    · 只增加 RAG 仓储错误；**未**扩大为"所有 Exception → 502"
    · 其它语义不变：ValueError → 400；路径长度越界 → 422；未预期 → 500
    · 502 响应只有 {"detail"}：无 traceback / SQL / database_url / 内部路径
```

## 6. Empty Semantics

```text
rag_executions = [] → HTTP 200（空 Trace 合法）
无匹配 ≠ 查询失败；查询失败 = 502（实测）
```

## 7. Ordering

```text
LLM：created_at ASC, id ASC（不变）
Tool：id ASC（落库顺序；不变）
RAG ：id ASC（落库顺序；Step 48 沿用仓储稳定排序）
组合层不重排 / 不合并 / 不去重；未引入 events
```

## 8. Correlation

```text
LLM Usage      assistant_request_id = A
Tool Execution request_id          = A
RAG Execution  request_id          = A
（唯一生成点 = AIOrchestratorService.execute()；未新增任何 correlation id）
E2E 实测（Case 2）：同一 A 下 llm_usage ≥ 1 且 rag_executions ≥ 1
```

## 9. Backward Compatibility

```text
additive change：旧字段不改名 / 不删除 / 语义不变；新增 rag_executions
旧客户端忽略未知字段即可；空 Trace 仍 200
```

## 10. Runtime vs Persistent

```text
Assistant Trace 的 RAG 源 = PostgreSQL（RagExecutionPersistentQueryService）
    · 不使用 InMemoryRagExecutionCollector / rag_observability_query_service
    · restart / 多 worker 下与 LLM·Tool 段一致（不会出现"只有 RAG 没历史"）
契约测试：Trace 组合层与 API 模块不 import 任何 Collector / Runtime 读边界
```

## 11. Security

```text
暴露：request_id · timestamps · duration_ms · result_count · used_chunks_count ·
      top_k · context_truncated · context_chars · reranker_used ·
      rerank_elapsed_ms · chunk_ids · document_ids
禁止：id（数据库主键）· query · answer · content · similarity · embedding ·
      prompt · messages · raw_response · sql · password · api_key ·
      authorization · database_url · session · ORM · traceback · project_id
实测：502 / 200 响应均不含上述键与值（chunk 正文 sentinel 不出现）
注：chunk_ids / document_ids 的暴露级别与既有 /api/ai/chat RAG 响应
    data.sources[] 一致（仅 ID、无正文）
```

## 12. Tests

```text
tests/test_assistant_trace_rag_integration.py        23 passed（纯离线；0 DB）
    Mapping（View / DTO / HTTP 全链路 13 字段 + 无主键 + NULL 保持）·
    Empty / Ordering（id ASC 保持；200 + []）· Correlation ·
    Failure（service 透传 + HTTP 502 + 无泄漏；400/500 语义不变）·
    Security（禁止键 / 正文 sentinel / DTO 无 payload）·
    Composition（注入 Persistent 读边界；不 import Runtime Collector；
        API 不创建 Repository / Session）· BackwardCompat（3 旧字段不变、
        additive、无新 RAG API、/api/rag/answer 不变）
tests/test_assistant_trace_rag_integration_e2e_db.py  6 passed（RUN_DB_TESTS=1）
    Case 1 RAG only · Case 2 LLM+RAG（真实 Client + MockTransport +
        真实 Accounting Sink）· Case 3 Tool only（rag_executions == []）·
    Case 4 empty · Case 5 A/B isolation · Case 6 RAG 仓储失败 → 502
状态同步（非放宽契约，仅把"设计态"断言更新为实现态）：
    test_assistant_trace_api.py · test_assistant_trace_query_service.py ·
    test_assistant_trace_correlation_e2e.py · test_assistant_trace_api_db.py ·
    test_assistant_trace_correlation_e2e_db.py ·
    test_assistant_trace_persistent_tool_db.py ·
    test_assistant_trace_rag_integration_contract.py ·
    test_rag_persistent_observation_contract.py ·
    test_rag_runtime_observability.py · test_rag_trace_coverage_audit.py
    （离线文件同时注入 RAG 读边界替身 → 默认测试仍 0 DB）
```

## 13. DB Residue

```text
清理：定向 DELETE（rag / llm_usage / tool 三表按本模块产生的 request_id /
    provider request_id 前缀）；**未使用 TRUNCATE**；未触碰真实数据
teardown 断言 rag_execution_record 残留 = 0
```

## 14. Deferred

```text
RAG Trace Metrics / 聚合 · Trace 统一 events 排序（方案 B）·
project-level 授权 · RAG Trace HTTP 查询端点（按 request_id 之外）·
retention / TTL / cleanup · Conversation / Memory / Agent / MCP /
OpenTelemetry / Streaming / Dashboard
```

## 15. Limitations

```text
* 写入仍是同步 best-effort（RAG 持久化失败只 warning；Trace 读的是已落库数据）
* 未对 Trace 做 project 级授权；可查性仅依赖 request_id（uuid4）
* chunk_ids / document_ids 仍暴露（与既有 /api/ai/chat 一致）；若受众扩大需重评
* 三个列表独立排序，没有统一时间线（方案 A 的固有取舍）
* 无 retention：rag_execution_record 会持续增长
* lint unavailable（环境限制）
```
