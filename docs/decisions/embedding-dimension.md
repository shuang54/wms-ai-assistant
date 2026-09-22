# Embedding 维度迁移方案（1536 → 1024）

- 阶段：Phase 3.5.1.6（审计与设计）
- 状态：**已审计，未执行**（本文档仅为方案，未执行任何 ALTER / 写库 / API 调用）
- 决策：`knowledge_chunk.embedding` 目标维度 `vector(1024)`，配合 `BAAI/bge-m3`
  （SiliconFlow 真实返回 1024 维，HTTP 200，Phase 3.5.1.5 已验证）

---

## 1. 背景

Phase 3.5.1.5 真实 API 验证结论：`BAAI/bge-m3` 实际输出 **1024** 维，
而 Phase 3.2 的数据库 schema 与 `EMBEDDING_DIMENSION` 均为 **1536**，三者不兼容：

```text
当前配置：1536
当前数据库：1536
实际模型：1024
```

经决策采用：`BAAI/bge-m3` + **1024 维** + PostgreSQL pgvector `vector(1024)`。

## 2. 当前状态（静态审计）

| 项 | 值 |
|---|---|
| Model | `BAAI/bge-m3`（SiliconFlow） |
| Actual Dimension | 1024（真实 API Response） |
| DB Dimension | `vector(1536)` |
| ORM 定义 | `Vector(settings.embedding.dimension)` —— **配置驱动，无硬编码维度** |
| embedding 现有数据 | **0 行非 NULL**（见 §5 论证） |
| 向量索引 | 不存在（Phase 3.2 明确推迟） |
| Alembic | 未引入（Phase 3.1 明确决策） |

## 3. 决策

```text
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
knowledge_chunk.embedding → vector(1024)
```

## 4. 影响范围与逐文件清单

### 4.1 必须修改（迁移执行阶段，本阶段不改）

| 文件 | 位置 | 现状 | 目标 |
|---|---|---|---|
| `.env`（本地） | L11 | `EMBEDDING_DIMENSION=1536` | `1024` |
| `.env.example` | L81–82 | 注释 "Default 1536 = OpenAI ..."；`EMBEDDING_DIMENSION=1536` | 注释改为 bge-m3；值 `1024` |
| `backend/app/config.py` | L138 | 注释 "默认值 1536 = OpenAI ..." | 更新为 bge-m3/1024 |
| `backend/app/config.py` | L147 | `_get_int("EMBEDDING_DIMENSION", 1536)` 默认值 | 默认值改 `1024` |
| `backend/app/embedding/client.py` | L30–31 | 模块 docstring "`EMBEDDING_DIMENSION`（默认 1536）必须与 ... vector(1536) 一致" | 更新为 1024 / bge-m3 |
| `tests/test_embedding_client.py` | L440–441 | `test_default_dimension_is_1536`：`assert ...dimension == 1536` | 改为断言 `1024`（或改名 `test_default_dimension_is_1024`） |
| `tests/test_embedding_real.py` | L16, L90 | docstring `vector(1536)`；`CompatibleWithVector1536`（硬编码 1536 比较） | 改为 1024（或直接与 `settings.embedding.dimension` 比较） |

### 4.2 建议顺手统一（非必须）

| 文件 | 位置 | 说明 |
|---|---|---|
| `tests/test_embedding_client.py` | L88 | `dimension=1536` 仅是合法正整数 fixture，无业务断言；可与 4.1 一并改为 1024 保持语义一致 |
| `tests/test_embedding_client.py` | L119 | `dimension="1536"` 仅测试非 int 输入被拒；值本身无维度语义，可不改 |

### 4.3 不需要修改

| 文件 | 原因 |
|---|---|
| `backend/app/db/models/knowledge_chunk.py` | `Vector(settings.embedding.dimension)` 为配置驱动；**注意 `create_all()` 只创建缺失表，不会 ALTER 已有表**，故无需改代码但必须配合 §6 的 SQL |
| `backend/app/db/models/knowledge_document.py` | 无 embedding / 维度字段 |
| `backend/app/db/init_db.py` | 幂等 `CREATE EXTENSION + create_all`；注释已预留 Alembic 替换扩展点 |
| `backend/app/db/base.py` / `session.py` | 与维度无关 |
| `backend/app/embedding/exceptions.py` | 无维度数值硬编码（仅异常语义文本） |
| `backend/app/rag/**`（parsers / chunking） | 明确"不触碰 DB / Embedding / Vector Search"，无任何 1536 依赖 |
| `docs/architecture.md` / `docs/database.md` / `docs/requirements.md` | 全文检索无 1536 硬编码（无需随迁移修改） |
| `docs/decisions/Phase/*.md` | 历史任务书快照，保留原样 |
| `knowledge/` / `scripts/` / `docker-compose.yml` | 无维度相关内容 |

### 4.4 关键机制确认

- `EMBEDDING_DIMENSION=1024` 后 **Embedding Client 完全兼容**：维度全程经 `EmbeddingSettings.dimension` 注入，无任何 1536 硬编码参与校验路径；API 返回 1024 → 校验通过，返回 1536 → `EmbeddingDimensionError`。
- ORM 侧 `Vector(settings.embedding.dimension)` 在**模块导入时**求值：改环境变量后需重启进程/重新导入才生效；且**只影响新建表**。

## 5. 数据迁移策略（旧向量处理）

**当前数据量：0 行（embedding IS NOT NULL 的 knowledge_chunk 不存在）。**

静态论证（本阶段未连库）：

1. `.env` 的 `DATABASE_URL` 为空 → 所有 DB 功能禁用（`get_engine()` 返回 None，DB 相关测试默认 skip）；
2. Phase 3.2 仅建表（`init_db` 只 `CREATE EXTENSION` + `create_all`）；
3. Phase 3.3 Parser / Phase 3.4 Chunking 的模块 docstring 均声明"不触碰 DB / Embedding / Vector Search"，且代码无任何 ORM 写入调用；
4. 向 `knowledge_chunk.embedding` 写入向量的 Ingestion 属 Phase 3.5.2+，尚未实现。

因此**不存在需要转换的旧 1536 维向量**；迁移 SQL 的 `USING NULL` 仅作防御性语义。

若执行迁移时发现非空数据（防御分支）：**1536 维向量不可截断 / 补零 / 降维复用**（不同维度向量语义空间不同，且 1024↔1536 双向均不可逆），正确做法：pg_dump 备份相关表 → 清空 embedding → 切换 schema → 依据原始 `content` 重新 Embedding 生成 1024 维。

## 6. 数据库迁移 SQL（方案示例，本阶段禁止执行）

pgvector 不提供不同维度间的自动 cast，直接 `ALTER ... TYPE vector(1024)` 会因缺少 cast 路径而失败（即使当前无数据），因此采用显式 `USING NULL`（同时声明"丢弃旧向量"语义；当前数据为 0，无实际损失）：

```sql
-- ===== Phase 3.5.1.6 Embedding 维度迁移：vector(1536) → vector(1024) =====
-- 仅为方案示例：本阶段（3.5.1.6）禁止执行任何 ALTER / DDL。
-- 前置条件（执行阶段核验）：
--   1) 无写入流量（RAG/Ingestion 未实现，天然满足）
--   2) SELECT COUNT(*) FROM knowledge_chunk WHERE embedding IS NOT NULL;  -- 预期 0；非 0 则先 pg_dump 备份
--   3) 无向量索引需要重建（Phase 3.2 未建索引）

-- 推荐：单条 ALTER + USING NULL（保持列位置/注释；规避 pgvector 跨维度 cast 限制）
ALTER TABLE knowledge_chunk
    ALTER COLUMN embedding TYPE vector(1024) USING NULL;

-- 等价备选（效果相同）：
-- ALTER TABLE knowledge_chunk DROP COLUMN embedding;
-- ALTER TABLE knowledge_chunk ADD COLUMN embedding vector(1024);

-- 执行后校验（预期 typmod=1024）：
-- SELECT atttypmod FROM pg_attribute
--   WHERE attrelid = 'knowledge_chunk'::regclass AND attname = 'embedding';
```

执行顺序（后续阶段）：

```text
确认无写入 / 数据量统计（预期 0；非 0 先备份）
        ↓
.env：EMBEDDING_DIMENSION=1024
        ↓
执行 ALTER（USING NULL）
        ↓
校验 schema：pg_attribute.atttypmod = 1024，且 ORM 重启后 Vector(1024) 与库一致
        ↓
Phase 3.5.2+：重新 Embedding → 写入 1024 维向量
        ↓
向量写入/读取 roundtrip 测试 → cosine 检索验证
        ↓
数据量明确后再建 HNSW / IVFFlat 索引
```

## 7. 回滚方案

```sql
-- 回滚（同样显式 USING NULL）
ALTER TABLE knowledge_chunk
    ALTER COLUMN embedding TYPE vector(1536) USING NULL;
```

同时 `.env` 的 `EMBEDDING_DIMENSION` 改回 `1536`，并重启进程。

**不可逆性警示**：
- 已生成的 1024 维向量**无法**恢复为原 1536 维向量；
- 若迁移时清空了 1536 维旧向量且未备份，同样无法恢复；
- 因此执行阶段若 `COUNT(*) != 0`，必须先 `pg_dump` 备份（或确保可从原始文档重新生成）再清空。

## 8. 测试计划（迁移后执行）

| 类别 | 用例 |
|---|---|
| Embedding Client（Mock） | 默认 `dimension == 1024`；API 返回 1024 → 通过；API 返回 1536 → `EmbeddingDimensionError`（现有 mismatch 用例已覆盖泛化行为，建议补 1024/1536 字面量用例） |
| Real Smoke | `BAAI/bge-m3` → HTTP 200 → 1024 → `Compatible = YES`（`test_embedding_real.py` 按 §4.1 修改后自动覆盖） |
| DB Schema | `information_schema` 校验 `vector(1024)`；ORM 模型与实际列类型一致 |
| 向量写入/读取 | 1024 维向量 INSERT → SELECT roundtrip 数值一致 |
| 维度错误校验 | 写入 1536 维到 `vector(1024)` 列被拒 |
| 未来 RAG | 1024 维 cosine similarity 检索结果正确 |

## 9. Alembic 判断

**本次不引入，维持 Phase 3.1 决策。**理由：

1. 当前仅 2 张表、一次维度修正，无迁移历史；
2. `init_db()` 已保留替换为 Alembic 入口的扩展点；
3. 为单字段改动引入 Alembic（迁移目录、env.py、版本文件、CI 适配）属扩大项目范围，违反"最小修改"与 Simple First 原则；
4. 待出现真实 schema 演进需求（vector 索引、多租户、字段增删历史）再正式评估引入。

## 10. 待执行清单（等待确认后进入执行阶段）

1. `.env` / `.env.example` / `config.py`：1536 → 1024（含注释）
2. `client.py` docstring 更新
3. `test_embedding_client.py` / `test_embedding_real.py` 断言更新 + 1024/1536 用例补全
4. 执行 §6 SQL（前置校验 COUNT==0 或已备份）
5. schema / roundtrip / Real Smoke 验证
6. 之后才进入 Phase 3.5.2（批量 Embedding / 持久化）
