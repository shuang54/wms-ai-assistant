# Phase 3.12 Step 52 — LLM Usage Assistant Request ID Index Implementation

## 一、阶段目标

实施 Phase 3.12 Step 51 已经确认的最小数据库优化：

```text
ai_ops.llm_usage_record.assistant_request_id
        ↓
B-tree Index
        ↓
Assistant Trace LLM Usage 查询
```

唯一目标：

> 为 `assistant_request_id` 增加普通、非唯一 B-tree 索引，并验证不改变现有查询结果、排序、API 和 Trace 语义。

Step 51 已经完成设计：

```text
IMPLEMENT
Option A
```

目标索引：

```text
ix_llm_usage_record_assistant_request_id
```

字段：

```text
assistant_request_id
```

---

# 二、严格范围

允许修改：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/init_db.py
```

以及当前项目实际对应的：

```text
migration / schema initialization
tests/test_llm_usage*
tests/test_assistant_trace*
```

如果项目实际初始化索引的位置与上述文件不同：

**先阅读真实代码，遵循现有项目结构。**

允许新增：

```text
tests/test_llm_usage_assistant_request_id_index.py
```

如果现有测试结构已有合适位置，则优先复用。

允许更新：

```text
docs/architecture.md
docs/evaluation/Phase 3.12 Step 51 — LLM Usage Request ID Index Audit.md
```

---

# 三、明确禁止

本阶段禁止修改：

```text
AssistantTraceResponse
AssistantTraceQueryService
LLMUsageQueryService 查询语义
LLMUsageRepository 查询语义
Tool Trace
RAG Trace
AIOrchestrator
LLM Provider
LLM accounting
request_id 生成
```

禁止：

```text
Pagination
Cursor
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Redis
Cache
Event Bus
Kafka
```

禁止：

```text
NOT NULL
UNIQUE
Backfill
复合索引
删除已有索引
修改已有索引
```

---

# 四、先阅读真实实现

开始修改前先阅读：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/init_db.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_query_service.py
```

确认：

1. ORM Model 当前 `__table_args__`
2. 当前索引定义
3. `init_db.py` 当前 ensure_* 模式
4. 项目是否存在 migration
5. 测试数据库初始化方式
6. DB fixture 如何创建/清理
7. 现有 LLM Usage 测试如何验证 schema

**不要假设 Step 51 的文件结构一定与当前代码完全一致。**

---

# 五、ORM Model

在：

```text
ai_ops.llm_usage_record
```

的 ORM Model 中增加：

```python
Index(
    "ix_llm_usage_record_assistant_request_id",
    "assistant_request_id",
)
```

要求：

```text
普通 B-tree
非 UNIQUE
```

不要：

```text
Index(..., unique=True)
```

不要：

```text
NOT NULL
```

不要复合：

```text
assistant_request_id + created_at + id
```

---

# 六、现有数据库初始化机制

如果项目当前没有 Alembic，而是使用：

```text
create_all()
+
ensure_*()
```

则沿用现有模式。

增加：

```text
ensure_assistant_request_id_index(conn)
```

或者项目当前最自然的等价命名。

目标 SQL：

```sql
CREATE INDEX IF NOT EXISTS ix_llm_usage_record_assistant_request_id
ON ai_ops.llm_usage_record (assistant_request_id);
```

`IF NOT EXISTS` 可以保证重复初始化不会因为索引已经存在而失败。PostgreSQL 官方明确支持这种形式。

---

# 七、重要：不要只依赖 create_all()

确认项目当前行为。

如果：

```text
llm_usage_record
```

已经存在，而只是 ORM Model 新增 Index：

不要假设：

```text
Base.metadata.create_all()
```

会自动给已有表补齐这个索引。

沿用项目现有 `ensure_*` 机制。

---

# 八、NULL 行为

保持：

```text
assistant_request_id = NULL
```

完全合法。

不要修改历史记录。

不要 backfill。

不要把：

```text
NULL
```

转换成：

```text
""
```

或其它值。

索引只是辅助查询，不改变业务数据。

---

# 九、测试

只增加最小测试集合。

至少覆盖：

## 1. Index exists

真实 DB 中确认：

```text
ix_llm_usage_record_assistant_request_id
```

存在。

并确认：

```text
column = assistant_request_id
unique = false
```

---

## 2. Idempotent initialization

连续执行：

```text
ensure_assistant_request_id_index()
ensure_assistant_request_id_index()
```

第二次不得失败。

---

## 3. Exact match

准备至少两个：

```text
assistant_request_id = A
assistant_request_id = B
```

查询：

```text
A
```

只能得到：

```text
A
```

不得混入 B。

---

## 4. NULL isolation

存在：

```text
assistant_request_id = NULL
```

查询：

```text
A
```

不得返回 NULL 行。

---

## 5. Stable ordering

准备相同 Assistant Request 的多条记录。

确认结果仍然：

```text
created_at ASC
id ASC
```

与 Step 51 完全一致。

---

## 6. Empty result

查询不存在的：

```text
assistant_request_id = unknown
```

结果：

```text
[]
```

---

# 十、API Regression

必须验证：

```text
GET /api/observability/assistant-trace/{A}
```

API contract 完全不变。

确认：

```text
assistant_request_id
llm_usage
tool_executions
rag_executions
```

字段不变。

不要新增：

```text
index
pagination
metadata
```

---

# 十一、Trace Regression

至少运行现有：

```text
test_assistant_trace*
```

重点确认：

```text
LLM usage correlation
Tool correlation
RAG correlation
cross-request isolation
empty trace
```

全部保持原行为。

---

# 十二、数据库验证

使用 DB 测试环境。

验证：

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'ai_ops'
  AND tablename = 'llm_usage_record'
ORDER BY indexname;
```

最终应该看到：

```text
ix_llm_usage_record_assistant_request_id
```

并确认定义类似：

```text
CREATE INDEX ix_llm_usage_record_assistant_request_id
ON ai_ops.llm_usage_record USING btree (assistant_request_id)
```

不要依赖 index name 单独判断。

同时确认：

```text
uq_llm_usage_record_request_id
```

仍然存在。

其它既有索引不得消失。

---

# 十三、数据安全

测试前后检查：

```text
llm_usage_record row count
```

必须：

```text
count_before == count_after
```

本阶段不需要业务数据写入。

测试 fixture 可以使用：

```text
BEGIN
→ INSERT test rows
→ test
→ ROLLBACK
```

或者复用现有 DB fixture。

最终报告：

```text
Production DB writes = 0
```

测试数据库产生的临时数据必须：

```text
residue = 0
```

---

# 十四、不要强求 Index Scan

不要写脆弱测试：

```text
EXPLAIN 必须出现 Index Scan
```

因为测试数据库可能非常小，PostgreSQL 仍然可能选择：

```text
Seq Scan
```

这是优化器的正常行为。

本阶段真正验证：

```text
Index exists
+
Definition correct
+
Query result unchanged
+
Initialization idempotent
```

---

# 十五、compile / regression

执行：

```powershell
python -m compileall -q backend tests
```

非 DB：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

至少重点执行：

```text
LLM Usage tests
Assistant Trace tests
RAG Trace integration tests
Tool Trace integration tests
```

如果全量测试耗时合理：

```text
python -m pytest -q
```

也执行。

---

# 十六、文档

更新：

```text
docs/architecture.md
```

将 Step 51：

```text
Design Only
```

更新为：

```text
Implemented
```

记录：

```text
Index:
ix_llm_usage_record_assistant_request_id

Column:
assistant_request_id

Type:
B-tree

Unique:
No

Nullable:
Yes

Purpose:
Assistant Trace LLM Usage exact-match lookup
```

同时记录：

```text
API contract unchanged
Query semantics unchanged
Historical NULL values unchanged
No backfill
No composite index
```

---

# 十七、Migration / Initialization 注意事项

如果当前项目明确没有 Alembic：

不要突然引入 Alembic。

只使用：

```text
当前项目已有 init_db / ensure_* 机制
```

保持一致。

如果项目已有 Alembic：

则使用现有 migration 机制。

**不要同时引入两套 schema migration 体系。**

---

# 十八、最终报告

严格使用：

```text
Phase 3.12 Step 52 COMPLETE

1. Index
- Name:
- Column:
- Type:
- Unique:
- Nullable:

2. Implementation
- ORM Model:
- Init / Migration:
- Idempotent:

3. Query
- Filter:
- Ordering:
- Semantics unchanged:

4. Tests
- Index existence:
- Idempotency:
- Exact match:
- NULL isolation:
- Ordering:
- Empty result:

5. Assistant Trace Regression
- LLM:
- Tool:
- RAG:
- Cross-request:
- API:

6. Database
- Production DB writes:
- Test DB writes:
- Residue:

7. compileall
8. lint
9. Git diff
10. API contract
11. DB schema change

12. Current Limitations

13. Phase 3.12 Step 52 STOP
```

---

# 十九、强制 STOP

完成后：

**立即停止。**

不要：

```text
Pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
Unified Timeline
```

不要继续增加其它索引。

不要自动进入 Step 53。

等待下一步指令。
