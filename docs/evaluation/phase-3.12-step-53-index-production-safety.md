# Phase 3.12 Step 53 — LLM Usage Assistant Request ID Index Production Migration Safety Audit

> **只读审计**。本阶段**未执行任何 DDL**（无 `CREATE INDEX` / `ALTER` / `DROP` /
> `REINDEX`）· **未修改** `init_db.py` / `llm_usage_record.py` · 未改 API。
> DB writes = 0（仅 `SELECT` / `SHOW` 只读探针，临时脚本已删除）。
>
> 审计对象：Phase 3.12 Step 52 落地索引
> `ai_ops.ix_llm_usage_record_assistant_request_id`。

---

## 1. Current Implementation

```text
① ORM 声明（新库随 create_all 创建）
   backend/app/db/models/llm_usage_record.py
       _TABLE_ARGS__ → Index(LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX,
                             "assistant_request_id")          ← 非唯一 / 非 partial / 非复合
② 既有库幂等补齐（应用/CLI 手动调用）
   backend/app/db/init_db.py
       ensure_assistant_request_id_index(conn) -> bool
       SQL: CREATE INDEX IF NOT EXISTS ix_llm_usage_record_assistant_request_id
                 ON ai_ops.llm_usage_record (assistant_request_id);
       init_db() 步骤 7（与 create_all / ensure_* 同一事务）
③ 无 migration framework
   requirements.txt: SQLAlchemy>=2.0,<3.0 · psycopg[binary]>=3.1,<4.0
   **无 alembic**（项目一致选择：create_all + ensure_* 幂等 DDL）
```

## 2. init_db Execution Path

全仓（`*.py / *.md / *.yml / *.ps1 / *.sh / *.toml / Dockerfile`，排除 `.git` /
`.venv` / `__pycache__`）检索 `init_db` 的真实调用点：

| 调用点 | 性质 | 生产自动执行 |
| --- | --- | --- |
| `backend/app/db/init_db.py:main()` | CLI `python -m backend.app.db.init_db` | 否（人工） |
| `backend/app/db/init_db.py` docstring | Python API `init_db()` 示例 | 否（人工） |
| `docker-compose.yml:11` | **注释**：本地起库后"init the pgvector extension once: `python -m backend.app.db.init_db`" | 否（人工） |
| 16 个 `tests/*_db.py` fixture | 全部带 `RUN_DB_TESTS` 守卫 | 否 |

**应用启动路径（决定性证据）**：

```text
backend/app/main.py
    create_app()  →  FastAPI(...) + include_router × 8        ← 仅此
    **无** lifespan / @app.on_event("startup") / startup 钩子
    **无** Base.metadata.create_all / CREATE / ALTER
backend/app/api/**  ：无启动期 DDL（检索 CREATE INDEX / ALTER TABLE /
                      CREATE SCHEMA / create_all 仅命中 text_to_sql 的
                      SQL 校验器关键词表与测试样例字符串，非执行路径）
```

答案：

```text
1. 应用启动时自动执行 init_db？       NO
2. 每次启动都执行？                   NO（启动期 0 DDL）
3. 生产环境可能自动执行？             NO（无部署脚本 / 无应用 Dockerfile /
                                        scripts/ 仅 text-to-SQL 分析脚本）
4. SQLAlchemy 连接方式？              engine.begin() —— 单个事务上下文
5. CREATE INDEX 当前是否在事务块中？  YES（init_db 单一事务；见 §3）
6. 是否存在 migration framework？     NO（无 Alembic）
7. 部署脚本是否执行 init_db？         NO（唯一指引是 docker-compose 注释中的
                                        **人工** CLI 调用）
```

## 3. Transaction Boundary

```text
backend/app/db/init_db.py:  init_db()
    with engine.begin() as conn:              ← 单一事务（autobegin + commit）
        1. CREATE EXTENSION IF NOT EXISTS vector
        2. CREATE SCHEMA IF NOT EXISTS ai_ops
        3. Base.metadata.create_all(bind=conn)
        4. ensure_request_id_idempotency_index(conn)     (CREATE UNIQUE INDEX … WHERE)
        5. ensure_assistant_request_id_column(conn)      (ALTER TABLE … ADD COLUMN IF NOT EXISTS)
        6. ensure_assistant_request_id_index(conn)       (CREATE INDEX … assistant_request_id)
    except SQLAlchemyError → RuntimeError（失败整体回滚）
```

```text
init_db transaction model: 单事务 · 全部 DDL 原子提交 · 无 lock_timeout / statement_timeout
                            （全 backend 检索：lock_timeout = 0 命中；statement_timeout 仅出现在
                             只读 SQL 执行器 / Tool Handler，与本流程无关）

关键含义：普通 CREATE INDEX 获得的对表写锁，将持续到**整个 init_db 事务提交**为止
          （窗口 ⊇ 索引构建本身，还叠加 create_all / 列补齐 / 唯一索引检查）。
          CONCURRENTLY 是否可直接放进当前 init_db？ → **NO**（见 §8）。
```

## 4. Production Usage

```text
判定：情况 A（init_db 只用于开发 / 测试 / 人工运维），带一个必须记录的附加事实

A 成立的证据：应用启动不跑 init_db（§2）；无应用镜像 / 无部署脚本 / 无 CI DDL；
             测试调用全部 RUN_DB_TESTS 守卫；docker-compose 仅提供本地开发 PG16 + pgvector

A 的边界（→ 归入"C 的风险面"）：唯一被文档化的 schema provisioning 路径是
             **人工** 执行 `python -m backend.app.db.init_db`；
             若运维把该命令指向已存在大表的生产库，则 §7 的写阻塞风险真实发生

结论：**应用启动自动 DDL = 不存在**（无需按 §六-情况 B 处理）；
      但"人工执行 init_db 于生产"必须走 §13 Runbook，而非直接照抄本地指令。
```

## 5. Table Size

```text
本环境（docker-compose 本地开发库 · PostgreSQL 16.15）—— 只读：

    ai_ops.llm_usage_record  rows = 0
    pg_total_relation_size  = 32 kB
    索引 pg_relation_size(ix_llm_usage_record_assistant_request_id) = 8192 bytes（空索引）
    assistant_request_id IS NULL rows = 0
    relkind = r（普通表，**非分区**）· is_partition = False · owner = postgres

production table size: **unavailable**
（本仓库无生产 / 类生产数据库接入；无任何生产规模样本，故不做估算）
```

## 6. Current Index Definition

只读核验（`pg_indexes` / `pg_index`，schema=`ai_ops`，table=`llm_usage_record`）：

```text
ix_llm_usage_record_assistant_request_id
    CREATE INDEX ix_llm_usage_record_assistant_request_id
    ON ai_ops.llm_usage_record USING btree (assistant_request_id)
    ← index exists ✅ · column = assistant_request_id ✅ · method = btree ✅ · unique = false ✅
       indisvalid = true ✅ · indisready = true ✅ · indisprimary = false · indisunique = false

同表其余索引（均未改动）：llm_usage_record_pkey (id, UNIQUE) ·
    ix_llm_usage_record_created_at (created_at) ·
    uq_llm_usage_record_request_id (request_id) WHERE request_id IS NOT NULL
约束：仅 llm_usage_record_pkey（type=p）
```

**`IF NOT EXISTS` 的定义漂移风险（必须记录）**：该子句只按**索引名**判断存在性。
PostgreSQL 官方（CREATE INDEX，`IF NOT EXISTS`）原文：

> "Do not throw an error if a relation with the same name already exists. …
> **Note that there is no guarantee that the existing index is anything like the
> one that would have been created.**"

```text
→ 若同名索引已存在但定义不同（例如列不对、被建成 UNIQUE、或为 INVALID），
  init_db 会静默接受（仅发 NOTICE），**不会**修正或告警；
→ 因此 Runbook 必须"验证定义 + validity"，不能只验证"存在"；
→ 本环境不存在该漂移（定义与预期完全一致）。
```

## 7. CREATE INDEX Risk

PostgreSQL 16 官方（CREATE INDEX / Building Indexes Concurrently）原文：

> "a standard index build locks out writes (but not reads) on the table until it's
> done" / "if they try to insert, update, or delete rows in the table they will block
> until the index build is finished. This could have a severe effect if the system is
> a live production database. Very large tables can take many hours to be indexed …"

本项目的具体风险面：

```text
R1 写阻塞：普通 CREATE INDEX 期间 INSERT / UPDATE / DELETE 阻塞（SELECT 不受影响）
R2 锁窗口扩大：DDL 位于 init_db 单一事务 → 锁持续到事务提交（含 create_all 等步骤）
R3 无超时护栏：未设置 lock_timeout → 等待队列可无限延长；
              且 CREATE INDEX 会排在并发长事务之后（等锁 + 构建叠加）
R4 无生产可观测：init_db 只在 CLI 日志输出，无进度 / 耗时 / 锁等待指标
R5 定义漂移：见 §6（IF NOT EXISTS 只认名字）
R6 失败语义：普通 CREATE INDEX 失败 → 整个事务回滚 → **不留 INVALID 索引**
              （比 CONCURRENTLY 更"干净"，但整个 init_db 一起失败）
R7 无回滚需求：索引为附加对象，失败 / 误建只需 DROP INDEX，不涉及数据
不适用：
  - 唯一性失败风险（索引 **non-unique**；且 assistant_request_id 允许重复 / NULL）
  - 分区表并发限制（relkind = r，非分区）
  - 历史 NULL 行导致的构建阻塞（NULL 不改写行，无 backfill，非 partial 索引）
```

## 8. CONCURRENTLY Compatibility

```text
PostgreSQL 官方原文："
    Another difference is that a regular CREATE INDEX command can be performed
    within a transaction block, but CREATE INDEX CONCURRENTLY cannot."

⇒ 当前 init_db 是 `engine.begin()` 单事务 → 直接改为
  `CREATE INDEX CONCURRENTLY IF NOT EXISTS …` 会立即报错
  （不能在事务块内执行），**方案 B 现状不可行**。

若强行支持，需要（本阶段不做）：
    独立连接 + isolation_level="AUTOCOMMIT"（不进入 engine.begin() 事务）
    + 单独错误处理 + INVALID 索引处置策略 + 与其余幂等 DDL 分离编排
    —— 项目已有 AUTOCOMMIT 显式事务控制先例（sql_executor_service），
       但那是只读运行路径，不是 schema provisioning 路径

代价与副作用（官方）
    额外代价：并发构建需跨 3 个事务（1 次目录登记 + **2 次表扫描**）→ 更慢、
              CPU / IO 更高；且构建期间对表结构修改不允许
    INVALID：失败会留下被查询忽略、**仍然产生更新开销**的 INVALID 索引
             （官方："ignored for querying … still consume update overhead"）
    恢复：官方建议 DROP 后重试，或 `REINDEX INDEX CONCURRENTLY`（PG ≥ 12）
    可用性延迟：即使标记 valid，仍可能因早于构建开始的事务而暂时不可用
    组合注意：官方 CREATE INDEX 页对 `CONCURRENTLY` + `IF NOT EXISTS`
              **未给出专门限制说明**；两种写法的语义差异须在目标实例核验，
              本阶段**未在数据库执行**任何形式验证（禁止 DDL）
```

## 9. Option A — 保留现状（`CREATE INDEX IF NOT EXISTS` 于 init_db）

```text
优点：简单 · 零代码变更 · 幂等（已存在 → NOTICE 跳过，无构建）·
      新库 / 空表近似瞬时（本地实测：0 行 → 8 kB 索引）·
      失败原子回滚，不留 INVALID · 与既有 ensure_* 机制一致
风险：对**既有大表**执行会阻塞写入（R1）· 锁窗口含整个事务（R2）·
      无 lock_timeout（R3）· 只认名字（R5）
适用：开发 / 测试 / 全新库 / 小表（本仓库当前状态；无生产部署）
```

## 10. Option B — `CREATE INDEX CONCURRENTLY IF NOT EXISTS`

```text
优点：不阻塞普通 INSERT / UPDATE / DELETE
风险：**与当前事务边界不兼容**（§8）→ 需架构调整；
      更慢 / 更多扫描 / 更高 CPU·IO；失败可能遗留 INVALID 索引（需 DROP 后重试）；
      使"应用/CLI 自动 DDL"承载并发构建的长耗时与半失败状态；
      另需版本核验（组合 IF NOT EXISTS 的官方语义未在文档中专门说明）
结论：**不推荐，且当前实现下不可直接落地**
```

## 11. Option C — 生产使用显式一次性 migration / deployment operation

```text
deployment（人工/运维窗口）
        ↓ pre-migration：检查存在性 → 检查定义 → 检查 validity → 评估表规模
        ↓ CREATE INDEX CONCURRENTLY（**独立连接 / 非事务块 / 低峰期**）
        ↓ 验收：validity 复查
        ↓ application startup（不执行任何 DDL —— 保持现状）
优点：生产 DDL 可控 · 可单独观察耗时与锁等待 · 可择时（低峰）·
      可单独失败 / 回滚处理 · 无需引入 Alembic（一次手工操作即可）
缺点：项目无 migration framework → 依赖运维纪律与文档（无版本化记录）
适用：生产 / 类生产已存在大表的场景（即 A 的风险面）
```

## 12. Recommendation

```text
Recommendation: **Option A**（保留 Step 52 现状，零代码变更）
             + 生产 provisioning 走 **Option C**（§13 Runbook）

依据（来自真实证据）：
  1) init_db 不会被应用启动自动执行（main.py / api / 部署物 = 0 DDL 调用点）
     → §六-情况 A 成立 → 自动路径生产风险 = 不存在
  2) 唯一被文档化的执行方式是人工 CLI（docker-compose 注释 + init_db docstring）
  3) 本环境表规模 0 行 / 32 kB（幂等跳过路径无构建成本）；
     生产规模不可得（production table size: unavailable）
  4) CONCURRENTLY 与当前 engine.begin() 事务模型不兼容 → Option B 不可直接实施
  5) 无 migration framework（无 Alembic）→ 不引入新框架（超出范围）

明确不做（本阶段未改代码，也不建议现在改）：
  × 把 CONCURRENTLY 塞进 init_db（需重构成 AUTOCOMMIT + 半失败状态处理）
  × 引入 Alembic / Flyway 等框架
  × 在 init_db 中加自动 DROP / REINDEX / 修复逻辑
  × 应用启动自动创建索引

一句话边界：
  production migration remains an operational responsibility
  （应用启动保持 0 DDL；生产建索引由运维按 §13 执行）
```

## 13. Production Runbook（只设计，未执行）

```text
前置：确认 DATABASE_URL 指向目标库（**禁止**在下方步骤中输出凭据）；
      由具 DDL 权限的运维 / DBA 账号执行；全程避免业务高峰。

Step 1  存在性检查（只读）
        SELECT indexname, indexdef FROM pg_indexes
        WHERE schemaname='ai_ops' AND tablename='llm_usage_record'
          AND indexname='ix_llm_usage_record_assistant_request_id';
        → 已存在：跳 Step 2~5，直接做 Step 3 的 validity 检查

Step 2  定义检查（防止 §6 名同义不同）
        期望：USING btree (assistant_request_id) · 非 UNIQUE · 非 partial（无 WHERE）
        不符 → **停止并向变更负责人确认**（不自动 DROP / 不自动重建）

Step 3  validity 检查（只读）
        SELECT c.relname, i.indisvalid, i.indisready
        FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = 'ai_ops.llm_usage_record'::regclass;
        → 期望 indisvalid = t AND indisready = t
        → 出现 f：见 §14（Index exists but is invalid.）

Step 4  表规模与窗口评估（只读）
        SELECT COUNT(*) FROM ai_ops.llm_usage_record;
        SELECT pg_size_pretty(pg_total_relation_size('ai_ops.llm_usage_record'));
        → 据此判断是否需要 CONCURRENTLY（大表 / 有写入流量 → 必须）
        → 建议同时确认无长时间运行事务（避免建索引排在其后长时间等锁）

Step 5  低峰期执行（**人工一次性操作**，独立连接、非事务块、单语句）
        CREATE INDEX CONCURRENTLY IF NOT EXISTS
            ix_llm_usage_record_assistant_request_id
            ON ai_ops.llm_usage_record (assistant_request_id);
        注意：不要包在 BEGIN…COMMIT 中；不要放入应用启动或 init_db；
              该语句可能耗时较长（两次扫描），执行期间不阻塞写

Step 6  等待完成（前台等待或轮询 pg_stat_activity；不设 lock_timeout 强杀，
        若必须中断，视为 Step 7 的失败路径）

Step 7  validity 复查（= Step 3）；必须 indisvalid = t / indisready = t

Step 8  功能验收：对一条已知 assistant_request_id 走 Assistant Trace 读路径
        （GET /api/observability/assistant-trace/{id}），确认 LLM Usage 返回正常、
        排序与之前一致、空结果语义不变

Step 9  记录：执行时间 / 耗时 / 表规模 / 索引大小（pg_relation_size）/ 结果
        （建议留档到变更记录；本仓库不新增表来存这些信息）

回滚（仅当需要撤销索引）：DROP INDEX CONCURRENTLY
        ai_ops.ix_llm_usage_record_assistant_request_id;
        （只影响执行计划，无数据影响）
```

## 14. Failure Recovery（只设计，未执行）

```text
1) 如何发现 INVALID
   SELECT c.relname, i.indisvalid, i.indisready, i.indisunique
   FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
   WHERE i.indrelid = 'ai_ops.llm_usage_record'::regclass;
   → indisvalid = f  ⇒ "Index exists but is invalid."
   （另可用 psql `\d ai_ops.llm_usage_record` —— 会显示 INVALID）
   ⚠ 重要：INVALID 索引**仍会被 IF NOT EXISTS 视为"已存在"** → 后续重跑
     init_db 或 Runbook Step 5 都不会自动修复 → 必须人工处置

2) 谁负责处理
   运维 / DBA（本仓库不提供自动修复；应用启动流程**永不**执行 DROP / REINDEX）

3) 是否需要 DROP INDEX
   需要（官方建议：DROP 后重试）。确认无其它会话依赖后执行：
   DROP INDEX CONCURRENTLY ai_ops.ix_llm_usage_record_assistant_request_id;
   （该语句同样不能在事务块内执行、不能在应用启动中执行）

4) 是否重新执行 CREATE INDEX CONCURRENTLY
   是 —— 排查失败原因（等锁超时 / 死锁 / 连接中断 / 资源不足）后，按 §13 Step 5 重跑；
   PG ≥ 12 的替代方案：REINDEX INDEX CONCURRENTLY
        ai_ops.ix_llm_usage_record_assistant_request_id
   （仅当索引**已存在且非 INVALID** 时才有意义；INVALID 首先 DROP）

5) 应用不受影响的事实
   索引缺失 / INVALID 只影响 Assistant Trace 的 LLM Usage 读路径性能
   （退化为顺序扫描），**不改变查询结果与排序语义**；
   因此失败不应触发业务回滚，只需按运维流程修复索引

6) 禁止事项
   × 在 init_db / 应用启动中写入 DROP INDEX / REINDEX / INVALID 自动修复
   × 使用普通（非 CONCURRENTLY）DROP 于高写入表（会阻塞写）
```

## 15. Limitations

```text
* 无生产 / 类生产实例：production table size = unavailable；构建耗时、锁等待、
  写阻塞时长均**未经实测**（本地库 0 行，无法外推）
* 本环境 PostgreSQL 16.15；生产版本未知（`REINDEX INDEX CONCURRENTLY` 需 PG ≥ 12）
* `CONCURRENTLY` + `IF NOT EXISTS` 组合：官方 CREATE INDEX 页未给出专门说明，
  本阶段**未在数据库执行任何 DDL 验证**（禁止）
* 未评估具体约束（如维护窗口 / 复制延迟 / 连接池占用）—— 属部署环境职责
* `init_db` 无 lock_timeout：该结论来自源码检索 + 官方锁语义，非实测
* 审计基于本地 docker-compose 开发库的只读查询（含 `is_superuser=on` 的本地账号），
  与生产权限模型无关
* 本阶段未修改任何代码，未执行生产迁移（结论中的建议项均为"待后续阶段决定/运维执行"）
```
