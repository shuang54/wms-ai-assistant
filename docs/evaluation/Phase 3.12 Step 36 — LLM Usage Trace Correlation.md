# Phase 3.12 Step 36 — LLM Usage Trace Correlation

> Step 35 让 `/api/ai/chat` 的成功响应带上 Assistant Trace ID（`metadata.request_id`），
> 并与 `ToolExecutionRecord.request_id` 对齐。本阶段把**同一个 ID** 也写入
> LLM Usage Record（`ai_ops.llm_usage_record.assistant_request_id`），
> 使一次 Assistant 请求的 **LLM 调用**与 **Tool 执行**都能被同一个 trace id 关联。
> **不新增 HTTP API / 不改 analytics / 不改 RAG · Tool · Text-to-SQL · Router。**

---

## 1. Current Problem

```text
Step 35 之后

    /api/ai/chat → request_id = A
        ├── Tool 执行 → ai_ops.tool_execution_record.request_id = A     ✅ 可关联
        └── LLM 调用  → ai_ops.llm_usage_record.request_id = P（Provider 请求 ID）
                                                                    ❌ 无法关联到 A

    且 LLM Usage 落库默认未接线（create_llm_client 默认 NoopAccountingSink），
    即使接线，Usage Record 与 Assistant 请求之间也没有共享 key。
```

## 2. Trace ID definitions（两个 ID 必须严格区分）

```text
Assistant request_id（A）             provider request_id（P）
──────────────────────────            ─────────────────────────────
语义：一次 /api/ai/chat 请求           语义：一次 LLM Provider 请求
来源：AIOrchestratorService.execute()  来源：LLMResponse.metadata["request_id"]
      new_request_id()（唯一生成点）        （Provider 返回，例如 chatcmpl-…）
格式：uuid4 字符串                    格式：Provider 自有格式；缺失 → None
用途：Assistant 级 trace               用途：Provider 侧对账 / 去重
落库：llm_usage_record.assistant_    落库：llm_usage_record.request_id
      request_id · tool_execution_         （既有列，**未改名 / 未改语义**）
      record.request_id

两者**不互相替代**：assistant_request_id 绝不写成 P；
request_id 也绝不覆盖为 A（Step 36 未修改该列的任何行为）。
```

## 3. Data Model

```text
ai_ops.llm_usage_record（既有表 + 1 个 nullable 列）

    id                     BIGINT PK（未变）
    request_id             VARCHAR(128) NULL  ← Provider 请求 ID（未变；仍参与幂等）
    provider               VARCHAR(64)  NULL  （未变）
    model                  VARCHAR(128) NULL  （未变）
    prompt_tokens          INTEGER      NULL  （未变；NULL ≠ 0）
    completion_tokens      INTEGER      NULL  （未变）
    total_tokens           INTEGER      NULL  （未变）
    created_at             TIMESTAMPTZ  NOT NULL（未变）
    assistant_request_id   VARCHAR(128) NULL  ← **Step 36 新增**

* nullable：旧链路（/api/chat · /api/rag/answer · /api/chat/with-tools）与
  历史数据没有 Assistant Scope → 保持 NULL（不生成 / 不回填 / 不猜）；
* **不参与幂等**：partial unique index 仍只约束 request_id（first-write-wins 不变）；
* **未新增索引**：本阶段没有"按 assistant_request_id 查询"的 HTTP 入口
  （`GET /api/usage/by-request` 属未来阶段，届时一并评估索引）；
* schema 演进沿用既有机制（**无 Alembic**）：
  `init_db()` 追加幂等 DDL
  `ALTER TABLE ai_ops.llm_usage_record ADD COLUMN IF NOT EXISTS
   assistant_request_id VARCHAR(128)`（对已存在的表显式补齐；不动历史数据）。
```

## 4. Correlation Flow

```text
POST /api/ai/chat
      ↓
AIOrchestratorService.execute()
      request_id = A = new_request_id()          ← 唯一生成点（Step 35）
      ↓ with assistant_trace_scope(A)            ← contextvar（per-task / 线程可传播）
      ├── Router（含 LLM fallback）
      ├── RAG      → LLM 调用 ─┐
      ├── TOOL     → ToolExecutionRecord.request_id = A
      └── T2S      → LLM 调用 ─┤
                               ↓
                     LLM Client → LLMObservation
                               ↓  DatabaseLLMAccountingSink
                        LLMUsagePersistenceService.persist()
                               ↓  current_assistant_request_id()  → A
                        LLMUsageRepository.create(assistant_request_id=A,
                                                  request_id=P)
                               ↓
                        ai_ops.llm_usage_record

设计要点
* correlation 在 **Orchestrator + LLM Usage application boundary** 完成；
  API 层**不**修改 Usage Record、**不**读 Usage 再补 ID（§九）；
* 不修改 LLM 领域模块（client / observability）签名，不给 RagService /
  TextToSQLService / ToolExecutionService 增加参数 —— 用
  `services/assistant_trace.py` 的 contextvar 隐式传播；
* `asyncio.to_thread`（Phase 3.10.15 Runtime Bridge）会复制 context，
  因此 `arecord()` 在 worker 线程内仍读到同一个 A（DB 测试实测）。
```

## 5. Backward Compatibility

```text
* 未绑定 Scope（旧链路 / 直连 Service / 后台脚本 / 测试）→ assistant_request_id = NULL
  （历史行同样 NULL；读路径 **未变**：LLM_USAGE_READ_COLUMNS 仍 8 列，
   LLMUsageRecordRow / LLMUsageRecordView / analytics 响应结构未改）
* 幂等语义未变：同一个 Provider request_id 第二次写入仍 DO NOTHING
* usage=None → 仍不写入（不因 correlation 退化为 audit log）
* 既有契约测试更新（当前状态契约，非放宽）：
    - _APPROVED_COLUMNS（DB 列白名单）6 → 7 列（+ assistant_request_id）
    - _APPROVED_WRITE_KEYS（Repository 入参白名单）6 → 7 项
    - 一处精确 kwargs 断言 + 一处 Fake Repository 签名同步
```

## 6. Failure Isolation

```text
Usage 持久化失败（DB 不可用 / 连接超时 / insert 失败）
      ↓ DatabaseLLMAccountingSink 捕获 → logger.warning
      ↓
Assistant 执行结果、LLM 回答、ToolResult、RAG Answer、T2S Result **完全不变**

实测：Repository 恒抛 LLMUsageRepositoryError 时
  · /api/ai/chat 仍 200（route / content / metadata.request_id 正常）
  · 尝试写入恰好 1 次（**无 retry / 无队列 / 无后台补偿**）
无新增 retry / sleep / queue / Celery。
```

## 7. Security

```text
Usage Record 只增加一个字段（assistant_request_id）；以下仍然禁止进入：
    prompt / messages / tool arguments / tool result / SQL / RAG chunk /
    API key / authorization / database URL / password / raw response / headers

实测（Repository 入参 keys 级断言 + AST 断言）：
    kwargs 白名单 = {request_id, provider, model, prompt_tokens,
                     completion_tokens, total_tokens, assistant_request_id}
    禁用键（prompt / messages / sql / tool_args / arguments / tool_result /
    rag_content / chunk_content / raw_response / headers / api_key /
    authorization / password / database_url）全部不存在
assistant_trace 模块：只保存 / 读取一个不可变字符串；不 import DB / LLM /
    网络 / 时间 / 随机 / uuid（不生成 ID）
```

## 8. Tests

```text
tests/test_assistant_trace.py                18 passed（+18 新增）
    Scope：未绑定 → None / 绑定-恢复 / 嵌套 / 异常仍恢复 / 非法值（None·""·
    非 str·超长）→ ValueError / 并发任务隔离（A·B·C）/ to_thread 传播
    Persistence：绑定 → assistant_request_id=A 且 request_id=P 不变 /
    未绑定 → None / 同一 Scope 两次调用同 A 而 P1·P2 不同 / 不同 Scope 不串 /
    usage=None 仍不写入 / Repository 失败 → sink 只 warning（sync + async 各一次尝试）

tests/test_llm_usage_trace_correlation.py    14 passed（+14 新增，含 C41）
    真实 Orchestrator + 真实执行边界 + Fake LLM 边界（0 real LLM / 0 DB）
    RAG  ：metadata.request_id == usage.assistant_request_id；P 独立
    TOOL ：metadata.request_id == ToolExecutionRecord.request_id
                          == usage.assistant_request_id（三方相等；round=1；
                            tool_call_id=None）
    T2S  ：metadata.request_id == usage.assistant_request_id；0 Tool Record
    两次请求不串；Repository 恒失败 → 仍 200 且尝试 1 次

tests/test_llm_usage_trace_correlation_db.py  6 passed（RUN_DB_TESTS=1；6 skipped 默认）
    绑定 Scope → 落库 assistant_request_id=A、request_id=P（未覆盖）；
    未绑定 → NULL；arecord（worker 线程）仍带 A；幂等不受影响（第二次 None）；
    历史 NULL 行读路径不受影响（Row 契约未变）；两次请求不串
    清理：按 provider 前缀 step36- 定向 DELETE；断言归零 + 表总行数回到基线

C41（tests/test_llm_usage_trace_correlation.py::TestC41LLMUsageTraceCorrelation）
    C41.1  Assistant request_id → LLM Usage              ✅
    C41.2  provider_request_id 保持独立（A ≠ P）          ✅
    C41.3  API 不生成 request_id（无 uuid / 不绑定 Scope） ✅
    C41.4  Orchestrator 是唯一来源（trace 模块不生成 ID） ✅
    C41.5  Tool Record 与 LLM Usage 同一 request_id       ✅
    C41.6  RAG / T2S 不产生 Tool Record                   ✅
    C41.7  历史 Usage（NULL）兼容（读边界未变）            ✅
    C41.8  Usage persistence failure 隔离                 ✅
    C41.9  无 prompt / args / SQL / secrets（白名单 7 项） ✅
    （附加：无新端点 / analytics 结构未变）

全量（no DB）                                3754 + 32 = 3786 passed / 412 skipped（0 failed）
DB-gated（LLM Usage 全家族 + 本阶段文件）     233 passed（0 failed）
0 real LLM（DeepSeek / SiliconFlow 未调用）
```

## 9. Limitations

```text
* 读侧未暴露：LLMUsageRecordRow / View / Query Service / `/api/usage/analytics`
  仍不含 assistant_request_id（§十三：本阶段只保证"能保存"）；
  按 assistant_request_id 查询 / `GET /api/usage/by-request` 属未来阶段
* 未新增 assistant_request_id 索引（无查询入口 → 不提前建索引）
* LLM Usage 默认仍未接线（create_llm_client 默认 NoopAccountingSink）——
  生产启用仍需显式装配 DatabaseLLMAccountingSink（本阶段未改该默认值）
* 一次 Assistant 请求可能产生 0 条 Usage（usage=None 不写入）或 1 条以上
  （多调用路径，如 Router LLM fallback + RAG）；均带同一个 A
* 未做跨进程 / 多 worker 的 trace 聚合（correlation 是数据库行级字段）
* Router 的 LLM fallback 属同一 Scope（会带 A），但本阶段未单独加测试
* 未引入 OpenTelemetry / Span / Trace Parent（明确不在范围内）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
