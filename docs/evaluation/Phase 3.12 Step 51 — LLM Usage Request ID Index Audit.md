# Phase 3.12 Step 51 — LLM Usage Assistant Request ID Index Audit

> **实施状态（Phase 3.12 Step 52）**：本审计的结论 **IMPLEMENT / Option A 已落地** ——
> `ix_llm_usage_record_assistant_request_id` 已在 Model 声明并经
> `init_db.ensure_assistant_request_id_index()` 幂等创建；
> 列定义 / 历史 NULL 数据 / API / 查询语义 / 排序**全部不变**（无 backfill、
> 无 UNIQUE、无复合索引、未删除既有索引）。
> 测试：`tests/test_llm_usage_assistant_request_id_index.py`
>
> 以下为**设计态**审计原文（保留历史结论）。
>
> 只做**索引必要性确认 + 最小索引设计**；**未执行 migration**、未改 API / DTO /
> 查询语义 / 数据 / 既有索引。Production Code = 0 · DB Schema = 0 · DB 写入 = 0。
>
> 触发来源：Step 50 发现 `ai_ops.llm_usage_record.assistant_request_id` 被
> Trace 查询用作过滤条件，但该列没有专用索引。

---

## 1. Current Query（真实代码）

```text
LLMUsageQueryService.list_by_assistant_request_id(A)
    → LLMUsageRepository.list_by_assistant_request_id(A)
        → build_trace_select(assistant_request_id=A)
```

```sql
SELECT id, assistant_request_id, request_id, provider, model,
       prompt_tokens, completion_tokens, total_tokens, created_at
FROM ai_ops.llm_usage_record
WHERE assistant_request_id = :assistant_request_id
ORDER BY created_at ASC, id ASC
```

```text
* 显式列（无 SELECT *）；bound parameter（无字符串拼接）；
* 过滤下推 PostgreSQL（不在 Python 里全表过滤）；
* 读路径不开启写事务；源码注释明确：**无 LIMIT / OFFSET**（分页属未来阶段）；
* 调用方：GET /api/observability/assistant-trace/{A}（每次 Trace 请求一次）。
```

## 2. Field Definition（真实定义）

| 项 | 值（实测 information_schema） |
| --- | --- |
| 类型 | `character varying(128)`（`String(128)`） |
| nullable | **YES**（`nullable=True`；旧链路 / 未绑定 Scope → NULL） |
| 唯一约束 | 无 |
| 外键 | 无 |
| 与 `request_id` 关系 | 两个**不同维度**：`request_id` = Provider 请求 ID；`assistant_request_id` = Assistant Trace ID |

```text
约束（本阶段与未来均不得改动）：
    · 不得改为 NOT NULL
    · 不得增加 UNIQUE（一次 Assistant Request 允许多条 usage）
    · 不得 backfill / 不得用 request_id 冒充或回填
```

## 3. Existing Indexes（真实 DDL，非按名称推断）

```text
llm_usage_record_pkey            UNIQUE btree (id)
ix_llm_usage_record_created_at   btree (created_at)
uq_llm_usage_record_request_id   UNIQUE btree (request_id)
                                  WHERE (request_id IS NOT NULL)   ← **Provider request_id**
```

```text
结论：`uq_llm_usage_record_request_id` 的键是 **Provider request_id**（`request_id`），
      不是 `assistant_request_id` —— 由 pg_indexes.indexdef 实测确认。
      ⇒ `assistant_request_id` **没有任何索引**。
```

## 4. Data Volume（当前环境只读统计）

```text
SELECT COUNT(*) FROM ai_ops.llm_usage_record;                       → 0
SELECT COUNT(*) ... WHERE assistant_request_id IS NOT NULL;         → 0
pg_class：est_rows = 0 · relpages = 0 · total_size ≈ 24 kB（空表 / 仅结构）

原因：此前各阶段的 DB 测试均在 teardown 定向清理（residue = 0），
      本环境是**开发/测试库**，当前没有真实累积数据 ⇒ **无法用真实行数证明成本**。
```

```text
增长模型（基于真实代码，非猜测）：
    · /api/ai/chat：RAG 路由 ≤1 条、Tool 路由 ≤1 条、T2S 路由 ≤4 条
      （TextToSQLService DEFAULT_MAX_ATTEMPTS = 3，钳制 [1,10]；+ Router fallback）
    · 旧链路（/api/chat · /api/rag/answer · /api/chat/with-tools）同样写入本表，
      但 assistant_request_id = NULL（不在 trace scope 内）
    ⇒ 表行数 ≈ Σ(全部 LLM 调用)，随使用线性增长；
      而单次 Trace 查询只取其中 1~4 行 → **选择性极高**（≈ 1e-5 量级起）。
```

## 5. EXPLAIN（仅 EXPLAIN，未用 ANALYZE；未执行数据修改）

```sql
EXPLAIN SELECT ... FROM ai_ops.llm_usage_record
WHERE assistant_request_id = :rid ORDER BY created_at ASC, id ASC;
```

```text
Sort  (cost=0.01..0.02 rows=1 width=996)
  Sort Key: created_at, id
  ->  Seq Scan on llm_usage_record  (cost=0.00..0.00 rows=1 width=996)
        Filter: ((assistant_request_id)::text = '…'::text)

解读（§十一）：
    · 当前表为空（relpages = 0）→ 优化器选择 Seq Scan 是**正常且最优**的；
    · 该结果**不能**用来断言"索引无效"，也**不能**用来断言"索引必需"；
    · 判断依据是选择性 + 增长模型：单次 Trace 只取 1~4 行 / 全表，
      无索引时每次 Trace 读取的成本随表增长线性上升（O(N)）。
```

## 6. Option A：单列索引

```sql
CREATE INDEX ix_llm_usage_record_assistant_request_id
ON ai_ops.llm_usage_record (assistant_request_id);
```

```text
优点：最小；精确匹配当前唯一过滤条件；写放大低（每 insert 维护 1 个 btree 键）；
      与现有索引语义一致（Tool / RAG 段同名风格：ix_<table>_<column>）
缺点：ORDER BY created_at, id 仍需对小结果集排序
      （每个 assistant_request_id 1~4 行 → 排序成本可忽略）
```

## 7. Option B：复合索引

```sql
CREATE INDEX ix_llm_usage_record_assistant_request_id_created_at_id
ON ai_ops.llm_usage_record (assistant_request_id, created_at, id);
```

```text
优点：过滤 + 排序一并覆盖（理论上可消除 Sort 节点）
缺点：索引更大；写入成本更高；当前每个 assistant_request_id 仅 1~4 行
      → 消除的排序成本微不足道，收益 < 成本（§十二：不为理论 ORDER BY 优化建复合索引）
```

## 8. Recommendation

```text
**IMPLEMENT（Option A）** —— 但**本阶段仍只做设计，不执行 migration**

理由：
    ① Trace 查询的唯一过滤条件是 assistant_request_id = ?，且当前无索引；
    ② 选择性极高（单请求 1~4 行 vs 全表），是 btree 索引的典型适用场景；
    ③ 表随所有 LLM 调用（含旧链路 NULL 行）线性增长 → 无索引时每次 Trace
       读取成本 O(N)，有索引后 O(log N + k)；
    ④ Option A 成本最低（单列、非唯一、不改动列定义、不 backfill）；
    ⑤ 与 Tool / RAG 段既有的 ix_*_request_id 索引保持同构（三段的 Trace 查询
       都有索引支撑，唯独 LLM 段缺失 → 补齐一致性缺口）。

不采纳 Option B：复合索引对本场景（每请求 1~4 行）收益极小，写入成本更高。
```

## 9. Migration Design（仅设计，未执行）

```text
Step 51.1（ORM Model，供**全新库**）：
    backend/app/db/models/llm_usage_record.py __table_args__ 增加：
        Index("ix_llm_usage_record_assistant_request_id", "assistant_request_id")
    · 非唯一 · 无 partial 谓词 · 不改动列定义（仍 VARCHAR(128) NULL）
    · create_all() 对新表随表创建

Step 51.2（**既有库**必须显式幂等 DDL）：
    backend/app/db/init_db.py 增加（沿用 ensure_assistant_request_id_column 模式）：
        ensure_assistant_request_id_index(conn) -> bool
            · 若表不存在 → False（由 create_all 随表创建）
            · 否则执行：
              CREATE INDEX IF NOT EXISTS ix_llm_usage_record_assistant_request_id
              ON ai_ops.llm_usage_record (assistant_request_id);
            · 与 init_db() 的其它 ensure_* 一并调用（幂等；可重复执行）
    理由：`Base.metadata.create_all(checkfirst=True)` 对**已存在**的表整体跳过，
          不会补建新增索引（项目既有结论：Step 36 的 assistant_request_id 列、
          Step 30.16 的 request_id 索引同理）。
```

```text
显式不做：
    · 不加 UNIQUE（一次 Assistant Request 允许多条 usage）
    · 不改列为 NOT NULL / 不 backfill / 不触碰历史 NULL 行
    · 不新增复合索引 / 不删除或改动既有索引
    · 不引入 Alembic（沿用项目既有 init_db 幂等 DDL 机制）
```

## 10. Security / Compatibility

```text
影响面：仅查询**执行计划**，不改变业务结果。
    · API response / DTO / 字段集合：不变
    · Trace 语义（过滤、排序、空 → []、DB 失败 → 502）：不变
    · request_id 生成方式：不变
    · 数据：不修改（NULL 行保持 NULL；btree 索引包含 NULL，不影响既有查询）
验证标准（若实施）：
    查询条件完全相同 · 返回结果完全相同 · 排序完全相同
    ⇒ 索引前后 row count = same、result = same
    不要求"必须 Index Scan"（优化器按成本决策；小表仍可能 Seq Scan）
```

## 11. Test Design（若实施，仅最小回归，不新增几十个测试）

```text
Repository / Query 回归：
    · assistant_request_id 精确匹配（命中）
    · 跨 request 隔离（A 的结果不含 B）
    · assistant_request_id IS NULL 的行**永不**匹配（历史 / 旧链路）
    · 稳定排序（created_at ASC, id ASC）
    · 空结果 → []（不是错误）
init_db 幂等：
    · ensure_assistant_request_id_index() 重复执行不报错、索引唯一
既有测试保持：
    · assistant trace 组合 / API / E2E 断言（结果集与排序不变）
⇒ 预计 ≤ 5 个新断言 + 现有 DB 测试直接覆盖
```

## 12. Verification（本阶段）

```text
python -m compileall -q backend tests → OK（0 errors）
只读 DB 检查：counts / NULL 统计 / 索引 DDL / 表统计 / EXPLAIN（未 ANALYZE）
生产代码 = 0 修改 · DB Schema = 0 · 数据 = 0 写入 · 索引 = 0 新增
```
