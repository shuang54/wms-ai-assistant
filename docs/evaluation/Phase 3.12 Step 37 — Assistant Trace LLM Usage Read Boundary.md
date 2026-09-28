# Phase 3.12 Step 37 — Assistant Trace LLM Usage Read Boundary

> Step 36 让 LLM Usage Record 能**写入** `assistant_request_id`。
> 本阶段只建立它的**只读查询边界**（应用层）：
>
> ```text
> A → LLM Usage Read Boundary → LLM Usage Records
> ```
>
> **不新增 HTTP API**、不修改 Analytics Response、不聚合 Tool / RAG、
> 不组装 Trace DTO。

---

## 1. Scope

```text
允许 / 实际修改
    backend/app/db/llm_usage_repository.py            （+ trace 只读方法 / Row / 列白名单）
    backend/app/services/llm_usage_query_service.py   （+ trace 只读方法 / View / 校验）
    tests/test_llm_usage_trace_read.py                （34 新增）
    tests/test_llm_usage_trace_read_db.py             （8 新增；DB-gated）
    docs/evaluation/…（本文件）· docs/architecture.md（§8.54）

明确不做
    新 endpoint（/api/usage/by-request · /api/trace · /api/assistant/trace）
    聚合 Tool + LLM / 聚合 RAG + LLM / Trace DTO / Conversation / Memory /
    Dashboard / OpenTelemetry / Trace Span / Prometheus / Redis / Kafka /
    Celery；不改 Analytics Response；不改 AI Router · Orchestrator ·
    ToolExecutionRecord · Tool Observability · Text-to-SQL · RAG · LLM Provider
```

## 2. Current Problem

```text
Step 36 之后

    ai_ops.llm_usage_record.assistant_request_id 已经写入 ✅
        但是：没有任何只读入口可以按它取回 usage 记录

    既有读边界（Step 3.10.17）只支持：
        request_id（Provider 请求 ID）/ provider / model / created_at 范围 + 分页
        → 无法回答"这次 Assistant 请求消耗了多少 LLM 调用"
```

## 3. Read Boundary

```text
LLMUsageQueryService.list_by_assistant_request_id(assistant_request_id)
        ↓ validate（DB 之前）
LLMUsageRepository.list_by_assistant_request_id(assistant_request_id)
        ↓ build_trace_select()（唯一 trace SQL 构造点）
    SELECT id, assistant_request_id, request_id, provider, model,
           prompt_tokens, completion_tokens, total_tokens, created_at
    FROM ai_ops.llm_usage_record
    WHERE assistant_request_id = :assistant_request_id
    ORDER BY created_at ASC, id ASC
        ↓ LLMUsageTraceRow（Repository 内部 frozen record；非 ORM）
LLMUsageTraceRecordView（9 字段；frozen；显式 from_row 映射）
        ↓
[View, …]（无匹配 → []）

两层职责
    Repository：DB Query → Row DTO（不聚合 / 不解释 / 不查 Tool / 不查 RAG）
    Query Service：校验 → Repository → View（不创建 Session / 不访问 ORM）

为什么**不**扩展 LLMUsageRecordRow（analytics 读 Row）：
    那会经 Row → View → Aggregation → `/api/usage/analytics` 传播，
    与"不修改既有 Analytics Response"冲突；因此新增**独立列白名单 +
    独立 Row + 独立 View**，两条读路径彼此隔离
    （LLM_USAGE_READ_COLUMNS 仍 8 列、LLM_USAGE_VIEW_FIELDS 仍 8 字段）。

为什么**不**扩展 LLMUsageAnalyticsReadFacade：
    Facade 是 analytics（聚合 + runtime bridge）的装配点；按 Assistant
    请求取原始 usage 记录属"记录读取"而不是"聚合分析"，
    因此归属既有记录读边界 LLMUsageQueryService。
```

## 4. Query Semantics

```text
* 精确匹配：WHERE assistant_request_id = :assistant_request_id
* bound parameter：不使用字符串拼接 / 不使用 text() 拼接用户输入
* 过滤下推 PostgreSQL：不在 Python 层取回后再过滤
* 不使用 LIKE / ILIKE / 正则 / 模糊 / 前缀匹配
* 无 time range / 无 provider / model 过滤（本阶段只按 Trace ID）
* 无 LIMIT / OFFSET：一次 Assistant 请求的 LLM 调用数量天然有限
  （Router LLM fallback + RAG / T2S 各 1~2 次）；分页属未来阶段
* 无匹配 → []（**不是错误**，不抛 not-found）
* 不自动查 Tool / RAG / Conversation；不组装 Trace DTO
```

## 5. Null Semantics

```text
assistant_request_id IS NULL 的记录 = 历史数据 / 旧链路
    （/api/chat · /api/rag/answer · /api/chat/with-tools / 未绑定 Scope）

查询 "A" 时：**NULL 永不匹配**（不生成 `NULL OR assistant_request_id = A`）
    → 历史 Usage 不会"粘"到某个 Assistant 请求上
    → 历史记录仍可经 analytics 读路径（request_id / provider / model / 时间范围）读取

输入校验（在 DB 之前失败）
    None / "" / "   " / 非 str / >128 字符 → 服务层 LLMUsageQueryInputError
                                        （Repository 层 → ValueError）
    不 truncate / 不 normalize / 不生成 UUID 兜底；返回值与入参逐字符一致
```

## 6. Ordering

```text
ORDER BY created_at ASC, id ASC

* created_at ASC：同一次 Assistant 请求内按 LLM 调用发生顺序
* id ASC：tie-breaker（同一时刻写入 / 相同时间戳）→ 顺序稳定可复现
* 与既有 analytics 读路径（created_at DESC, id DESC）**刻意不同**：
  分析列表按"最新在前"，Trace 明细按"时间顺序"
* 不依赖数据库默认返回顺序
```

## 7. Security

```text
View 字段白名单（9；与 SQL 显式列一致）：
    id · assistant_request_id · request_id（Provider 请求 ID，**未改名**）·
    provider · model · prompt_tokens · completion_tokens · total_tokens ·
    created_at

不返回：prompt / messages / raw_response / api_key / authorization /
        password / database_url / tool_args / tool_result / sql /
        rag_content / chunk_content（实测：AST 与字段白名单双断言）

不返回 ORM 对象 / Session / Connection / Engine / SQLAlchemy Result
（Repository 显式映射为 frozen Row；Service 不 import sqlalchemy）
```

## 8. Tests

```text
tests/test_llm_usage_trace_read.py      34 passed（0 DB）
    Service：精确透传 / 不在服务层重排序 / 空结果不是错误 / 非法输入 8 组
             （None·""·空白·非 str·bytes·list·超长）→ 未触达 Repository /
             长度边界 128 接受且不改写 / RepositoryError 透传 /
             View 显式映射与白名单 / frozen / 非法 assistant_request_id 拒绝
    Repository：列白名单（9 列，analytics 8 列未变）/ SQL 形态（显式列、
             精确匹配、无 NULL 分支、无 OR / LIKE、ORDER BY created_at ASC,
             id ASC、无 LIMIT / OFFSET）/ bound parameter /
             两组注入串保持字面值 / 非法输入在 DB 之前 ValueError
    C42：10 项（见下）

tests/test_llm_usage_trace_read_db.py    8 passed（RUN_DB_TESTS=1）/ 8 skipped 默认
    Test 1 A→P1,P2（B / NULL 不混入）· Test 2 不存在 → [] ·
    Test 3 NULL 永不匹配 · Test 4 排序（created_at ASC, id ASC）·
    Test 5 注入串 → []（表未被破坏）· Test 6 历史 NULL 行 analytics 读取正常 ·
    Test 7 同一 A 多 Provider 请求全部返回且原值保持 · Test 8 两个 A / B 不混
    清理：按 provider 前缀 step37- 定向 DELETE（**不用 TRUNCATE**）→ 归零

C42（tests/test_llm_usage_trace_read.py::TestC42AssistantTraceReadBoundary）
    C42.1  assistant_request_id 精确匹配（无 LIKE / 无范围）
    C42.2  NULL 不参与匹配（SQL 无 IS NULL / OR；AST 无 or_）
    C42.3  Repository 使用 bound parameter
    C42.4  Repository 不 SELECT *（SELECT 列表 == 列白名单；无 text() 拼接）
    C42.5  Repository 返回 frozen Row DTO（非 ORM）
    C42.6  Read Service 不访问 ORM / Session / SQLAlchemy
    C42.7  不新增 HTTP endpoint（OpenAPI + /api/usage/by-request、/api/trace → 404）
    C42.8  provider_request_id 语义不变（字段名 request_id 未改名）
    C42.9  历史 NULL 数据兼容（analytics 读模型未变）
    C42.10 不读取 prompt / messages / secrets

全量（no DB）                              3820 passed / 420 skipped（0 failed）
  （Step 36 基线 3786 / 412 → +34；skipped +8 = 新增 DB 文件）
DB-gated LLM Usage 家族（11 文件）           283 passed（0 failed）
```

## 9. Limitations

```text
* 无 HTTP API：查询能力只在应用层可用（`/api/usage/by-request` 属未来阶段）
* 不分页：按 Assistant Trace ID 取全部（未引入 limit / offset / cursor）
* 无 `assistant_request_id` 索引：当前查询为顺序扫描；
  数据量增长后需与 HTTP 入口一起评估（Step 36 起已记录）
* 不聚合：只返回原始记录（不返回 total_tokens 汇总 / 调用次数统计）
* 不组装 Trace DTO：不联接 ToolExecutionRecord / RAG 元数据（明确不在范围）
* analytics 读路径仍不含 correlation 字段（`/api/usage/analytics` 结构未变）
* LLM Usage 默认仍未接线（create_llm_client 默认 NoopAccountingSink）
* lint unavailable（环境未安装 ruff / flake8；未新增工具）
```
