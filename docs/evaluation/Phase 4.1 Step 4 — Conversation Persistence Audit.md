# Phase 4.1 Step 4 — Conversation Persistence Audit

> 状态：**Design / Audit Only**（不创建 ORM / Repository / migration / 表 / FK / 索引）
> 范围：Conversation / ConversationTurn 持久化层的落地边界
> 产物：本文件 + `tests/test_conversation_persistence_contract.py`（纯离线）
> 纪律（本 Step 结尾指标）：
>
> ```text
> Production Code = 0
> DB Schema = unchanged
> Migration = 0
> DB Writes = 0
> Network = 0
> LLM = 0
> ```
>
> 前置：Step 1（架构）/ Step 2（数据模型）/ Step 3（Service Boundary）已冻结
> `Conversation`、`ConversationTurn`、`ConversationService`、`ConversationRepository`。
>
> 本 Step 只回答：**Conversation / ConversationTurn 持久化层应该如何落地，
> 以及 Repository / Transaction / ORM / Migration 的最小实现边界。**

```text
ConversationService
        ↓
ConversationRepository
        ↓
SQLAlchemy ORM
        ↓
PostgreSQL

Conversation transaction
        ≠
AI / LLM transaction
```

---

## 0. Executive Summary（冻结结论）

| # | 结论 | 章节 |
| --- | --- | --- |
| 1 | Base = `DeclarativeBase`（`backend/app/db/base.py`）；表由 `_MODELS` 注册 + `create_all` 创建 | §1 |
| 2 | 无 Alembic；当前策略 = `init_db()` + `create_all` + 幂等 `ensure_*` DDL | §14 |
| 3 | Transaction ownership = Repository（**方案 A**）；Service 不接触 Session / commit | §4 |
| 4 | `conversation_id` = `String(128)` PRIMARY KEY（本项目**首个**字符串主键） | §5 |
| 5 | `turn_id` = BigInteger 自增主键（禁止 UUID / String / assistant_request_id 作 PK） | §6 |
| 6 | Turn → Conversation 使用 FK + ON DELETE CASCADE；`assistant_request_id` **不是 ForeignKey** | §7 |
| 7 | 索引最小集合：conversation_id PK + `(conversation_id, created_at)` | §8 |
| 8 | 排序固定 `ORDER BY created_at ASC, turn_id ASC` | §9 |
| 9 | status / role 均为 string（`String(32)`），不创建 PG ENUM | §10 |
| 10 | Conversation 物理删除级联到 Turns；4 张观测表全部保留 | §11 |
| 11 | Repository 返回 frozen Row；不返回 ORM 对象；Session 不得逃逸 | §12 |
| 12 | Step 4 does not execute migration.（未来策略见 §14） | §14 |

---

## 1. Current DB Architecture

### 1.1 真实结构（代码为证）

```text
backend/app/db/
    ├── base.py                    DeclarativeBase（ORM 基类）
    ├── session.py                 get_engine / get_session_factory（懒加载）
    ├── init_db.py                 init_db()：extension + schema + create_all + ensure_* DDL
    ├── models/                    ORM Model（6 张表）+ _MODELS 注册
    └── <entity>_repository.py     仓储（扁平放置；**无 repositories/ 子目录**）
```

| 关注点 | 真实实现 | 证据 |
| --- | --- | --- |
| Base 来源 | `class Base(DeclarativeBase)`（SQLAlchemy 2.x） | `db/base.py:11` |
| 表注册 | `db/models/__init__.py` 的 `_MODELS` + `get_all_models()` | `models/__init__.py:27-51` |
| schema 指定 | `__table_args__ = (..., {"schema": <SCHEMA 常量>})` | 4 张观测表 |
| ai_ops schema | `LLM_USAGE_SCHEMA` / `TOOL_EXECUTION_SCHEMA` / `RAG_EXECUTION_SCHEMA` / `ASSISTANT_OUTCOME_SCHEMA` 均为 `ai_ops`；knowledge_* 在默认 public | `models/*.py` |
| 建表 | `init_db()`：`CREATE EXTENSION IF NOT EXISTS vector` → `CREATE SCHEMA IF NOT EXISTS ai_ops` → `Base.metadata.create_all(bind=conn)` → 幂等 DDL | `db/init_db.py:186-243` |
| Session | `get_engine()` / `get_session_factory()`（DATABASE_URL 为空 → `None`，不抛异常） | `db/session.py:54-92` |

### 1.2 用户 §三 的 14 个问题（逐条真实回答）

| # | 问题 | 回答 |
| --- | --- | --- |
| 1 | Base 从哪里来 | `backend/app/db/base.py`（`DeclarativeBase` 子类，导出 `Base`） |
| 2 | ORM Model 命名规范 | `db/models/<entity>.py`；类 `XxxRecord` / `XxxModel` / 业务名（混合）；`__tablename__` 蛇形；加入 `_MODELS` |
| 3 | schema 如何指定 | `__table_args__` 末尾 dict `{"schema": ...}`；常量集中定义 |
| 4 | 是否使用 ai_ops | 是：llm_usage / tool_execution / rag_execution / assistant_outcome 四张观测表 |
| 5 | UUID / String PK 实践 | **无**：全部 6 张表 PK = `BigInteger` 自增；`llm_usage_record.py` 注释明确"不引入 UUID / ULID / Snowflake" |
| 6 | BIGINT 自增实践 | 是（全部表；`autoincrement=True`） |
| 7 | timestamp 类型 | `DateTime(timezone=True)` |
| 8 | timezone 是否统一 | 统一（全部 `timezone=True` + `server_default=func.now()`；时间由 DB 端生成） |
| 9 | enum / string status | string：`knowledge_document.status`（`String(32)` + default/server_default）、`assistant_outcome_record.outcome`（`String(16)`）；**无 PG ENUM**；Python 侧 StrEnum 仅用于 DTO（如 `AssistantOutcome`） |
| 10 | 索引/约束命名 | `ix_<table>_<column(s)>`（普通）· `uq_<table>_<column>`（唯一）· partial unique 用 `postgresql_where=text(...)` |
| 11 | 是否 Alembic | **否**（无 `alembic.ini` / `alembic/` / `migrations/`）；`models/__init__.py` 注明"Alembic migration — Phase 3.5+"未实现 |
| 12 | init_db 如何建表 | `Base.metadata.create_all(bind=conn)`（幂等，checkfirst）+ 对已存在表用 `ensure_*` 幂等 DDL 补列/补索引 |
| 13 | test DB 如何初始化 | DB 集成测试用 `RUN_DB_TESTS=1` 门控（`pytest.mark.skipif`），测试内调用 `init_db()`（幂等）；`tests/conftest.py` 只有残留守卫 |
| 14 | repository 如何管理 Session | 构造注入 `session_factory: sessionmaker[Session] | None`；`_get_session_factory()` 兜底；写 `with factory() as session, session.begin():`；读 `with factory() as session:` |

---

## 2. ORM Conventions

| 维度 | 约定（沿用；不重新发明） |
| --- | --- |
| 基类 | `from backend.app.db.base import Base` |
| 列声明 | `X: Mapped[type] = mapped_column(...)`（SQLAlchemy 2.x 注解风格） |
| 主键 | `mapped_column(BigInteger, primary_key=True, autoincrement=True, comment=...)` |
| 业务键 | `mapped_column(String(128), nullable=...)` |
| 文本 | `mapped_column(Text, nullable=False)`（先例：`knowledge_chunk.content`） |
| 时间 | `mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())` |
| 更新时间 | `server_default=func.now()` + `onupdate=func.now()`（先例：`knowledge_document.updated_at`） |
| 状态/角色 | `mapped_column(String(32), nullable=False, default=..., server_default=...)` |
| 外键 | `mapped_column(BigInteger/String, ForeignKey("table.col", ondelete="CASCADE"), nullable=False)` |
| 索引 | `Index("ix_<table>_<cols>", ...)`；唯一 `Index("uq_...", ..., unique=True)` |
| schema | `__table_args__ = (..., {"schema": <常量>})` |
| 注释 | 每列 `comment=...`（项目现有全部模型均如此） |

不引入：PG ENUM 类型、JSONB（Conversation 无 metadata 字段）、复合主键、触发器、
`server_onupdate` DB 端 trigger。

---

## 3. Repository Conventions

现有 4 个仓储（`assistant_outcome_repository` / `llm_usage_repository` /
`rag_execution_repository` / `tool_execution_repository`）的共同约定：

| 维度 | 约定 |
| --- | --- |
| 位置 | `backend/app/db/<entity>_repository.py`（**扁平**，无 `repositories/` 子目录） |
| 构造 | `__init__(session_factory: sessionmaker[Session] | None = None)`；`None` → `_get_session_factory()` → 全局 `get_session_factory()`；未配置 → `XxxRepositoryError` |
| 写事务 | `with factory() as session, session.begin():`（**事务由 Repository 开启并提交**） |
| 读 | `with factory() as session:`（无写事务、无 commit） |
| SQL 构造 | 单独 `build_*` 方法（唯一 SQL 构造点，便于测试断言而不执行） |
| 执行 | `session.execute(statement)`（Core 风格；**无** `add` / `flush` / `commit` / `refresh` / `rollback` 显式调用） |
| 返回 | frozen dataclass `XxxRow`（**不是 ORM 对象**；字段 = 显式列白名单） |
| 错误 | `SQLAlchemyError` → `XxxRepositoryError`（链式 raise；事务已回滚） |
| 方法命名 | `create` / `get_by_*` / `list_by_*` / `list_recent` / `get_metrics`；观测层 append-only，**无** update / delete |

---

## 4. Transaction Ownership

### 4.1 结论

```text
方案 A（选定）：Repository 拥有事务边界

Service
    ↓
Repository
    ↓
Session（Repository 内部 factory() + session.begin()）
    ↓
commit（由 session.begin() 上下文提交）
```

**Transaction ownership = Repository**（与方案 B「Service / Unit of Work commit」相反）。

### 4.2 为什么（以当前项目风格为准，不自行选择）

* 现有 4 个仓储**全部**采用"Repository 内部 `session.begin()` 提交"的形态；
* Service 层（如 `ChatService` / `AssistantOutcomeQueryService` /
  `ToolExecutionPersistenceService`）**从未**接触 Session，也没有 UoW 抽象；
* Step 3 已冻结"ConversationService 不允许直接使用 SQLAlchemy Session / select / insert / update"——
  与方案 A 天然一致；
* 方案 B 需要引入 Unit of Work / 事务上下文传递，属于超出当前项目规模的新抽象
  （Simple First）。

### 4.3 Conversation 事务 ≠ AI / LLM 事务

```text
Conversation transaction（DB；短事务；Repository 内）
    ≠
AI / LLM transaction（网络 + 推理；不在任何 DB 事务内）
```

两段式（Step 3 §11 冻结）：写 USER Turn + commit → AI 执行（无事务）→
写 ASSISTANT Turn + 更新 updated_at + commit。

### 4.4 append_turn 原子性（设计）

```text
BEGIN
  INSERT conversation_turn
  UPDATE conversation.updated_at
COMMIT
```

* 两条语句必须在**同一个事务**内（由 `ConversationRepository.append_turn()`
  内部的 `session.begin()` 保证，见 §4.1 方案 A）；
* 若 `INSERT conversation_turn` 成功而 `UPDATE conversation.updated_at` 失败 →
  **ROLLBACK**，绝不允许留下"Turn 已存在但 updated_at 未更新"的中间状态；
* Service 不参与事务控制（无 Session、无 commit）。

### 4.5 ARCHIVED 会话追加（设计）

```text
Conversation.status == ARCHIVED 时调用 append_turn()
    → ConversationArchivedError
    → DB write = 0
```

* 优先在**写入前**判定状态（读 conversation → 状态检查 → 拒绝），
  不采用"先 insert turn 再 rollback"的写法；
* 因此 DB write = 0（不产生半写 / 不留回滚痕迹）。

### 4.6 archive 原子性（设计）

```text
ACTIVE → ARCHIVED
updated_at = now
```

* 状态更新与 `updated_at` 更新在同一事务内完成（Repository `update_status()`）；
* 第二次调用（`ARCHIVED → ARCHIVED`）保持幂等：不报错、不改变终态。

---

## 5. Conversation ORM Design

### 5.1 列定义（设计冻结；严格对应 Step 2 的 5 字段）

| 列 | 类型 / 约束 | 说明 |
| --- | --- | --- |
| `conversation_id` | `String(128)` PRIMARY KEY NOT NULL | 服务端签发；唯一、不可变 |
| `project_id` | `String(128)` NOT NULL | 创建时绑定；不可修改 |
| `created_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()` | 会话创建时间 |
| `updated_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()`（+ `onupdate=func.now()`） | 最近一次有效变更 |
| `status` | `String(32)` NOT NULL `default='ACTIVE'` | ACTIVE / ARCHIVED |

### 5.2 conversation_id 作为字符串主键（如实记录权衡）

* 本项目此前**没有** String / UUID 主键先例（全部为 BigInteger 自增）；
* Step 2 冻结的字段白名单只有 5 个字段，禁止新增 surrogate `id` →
  `conversation_id` 必须兼作主键；
* 选择 `String(128)` 而非自增整数：conversation_id 由服务端签发（uuid4 风格）、
  客户端可跨端携带，自增序号会被枚举/猜测，不适合作为对外会话身份；
* 与 `assistant_request_id`（同为 `String(128)` 服务端签发）保持同一 ID 风格。

### 5.3 DDL 草案（**只设计，不执行**）

```sql
-- 设计草案；本 Step 不执行、不迁移、不建表。
-- 未来落点：独立 schema（例如 conversation）；不得放入 public。
CREATE TABLE conversation (
    conversation_id  VARCHAR(128) PRIMARY KEY,
    project_id       VARCHAR(128) NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    status           VARCHAR(32)  NOT NULL DEFAULT 'ACTIVE'
);
```

SQLAlchemy 草案（形态对齐现有模型）：

```python
class Conversation(Base):                      # 设计草案；不实现
    __tablename__ = "conversation"

    conversation_id: Mapped[str] = mapped_column(String(128), primary_key=True, ...)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False, ...)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), ...)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), ...)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE", ...)

    __table_args__ = ({"schema": CONVERSATION_SCHEMA},)
```

### 5.4 明确不新增（全部延期）

```text
title · user_id · tenant_id · metadata · last_message · message_count
```

### 5.5 schema 落点

* 不得放入 `public`（否则可能被 `SchemaExplorerService.inspect(schema="public")`
  当作 Text-to-SQL 业务表）；
* 建议独立 schema（例如 `conversation`）或经评估后使用其他内部 schema；
* 若使用新 schema，未来 `init_db()` 需新增一条幂等
  `CREATE SCHEMA IF NOT EXISTS` 步骤（沿用 `ai_ops` 的既有做法）。

---

## 6. Turn ORM Design

### 6.1 列定义（设计冻结；严格对应 Step 2 的 6 字段）

| 列 | 类型 / 约束 | 说明 |
| --- | --- | --- |
| `turn_id` | `BigInteger` PRIMARY KEY autoincrement | **必须**使用项目 BigInteger 自增实践 |
| `conversation_id` | `String(128)` NOT NULL + FK → `conversation.conversation_id` ON DELETE CASCADE | 所属会话 |
| `role` | `String(32)` NOT NULL | USER / ASSISTANT |
| `content` | `Text` NOT NULL | 用户可见文本 |
| `assistant_request_id` | `String(128)` NULL | correlation；**不是 ForeignKey** |
| `created_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()` | 写入时间 |

### 6.2 turn_id 约束（冻结）

```text
turn_id = BigInteger 自增
禁止：UUID · String · assistant_request_id 作为 Turn 主键
```

理由：与项目主键规范一致（`llm_usage_record` 注释："主键沿用项目既有 BIGINT 自增规范，
不引入 UUID / ULID / Snowflake"）；`assistant_request_id` 是执行身份而非行身份。

### 6.3 DDL 草案（**只设计，不执行**）

```sql
-- 设计草案；本 Step 不执行、不迁移、不建表。
CREATE TABLE conversation_turn (
    turn_id              BIGSERIAL    PRIMARY KEY,
    conversation_id      VARCHAR(128) NOT NULL
        REFERENCES conversation (conversation_id) ON DELETE CASCADE,
    role                 VARCHAR(32)  NOT NULL,
    content              TEXT         NOT NULL,
    assistant_request_id VARCHAR(128) NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);
```

---

## 7. FK Design

### 7.1 Turn → Conversation（建立 FK）

```text
conversation_turn.conversation_id
    ↓ FOREIGN KEY（ON DELETE CASCADE）
conversation.conversation_id
```

* 先例：`knowledge_chunk.document_id → knowledge_document.id ON DELETE CASCADE`；
* 语义对齐 Step 2 §11：物理删除 Conversation 时同时删除其 Turns；
* CASCADE **只作用于** `conversation_turn` 一张表。

### 7.2 assistant_request_id 不是 ForeignKey（绝对禁止）

```text
assistant_request_id
    ↓
llm_usage_record / tool_execution_record / rag_execution_record /
assistant_outcome_record
```

**assistant_request_id 不是 ForeignKey**。理由：

* Observability lifecycle ≠ Conversation lifecycle（Step 1 §15 / Step 2 §11）：
  删除/归档会话**不得**删除观测记录；
* `assistant_request_id` 只是 correlation（关联键），不是所有权关系；
* 观测表位于 `ai_ops` schema，Conversation 位于独立 schema ——
  跨 schema / 跨生命周期的 DB FK 会把两套不相关的生命周期在数据库层硬绑定；
* 现有 4 张观测表**本身也没有任何 ForeignKey**（真实代码证据）。

---

## 8. Index Strategy

### 8.1 最小索引集合（Step 2 冻结，本 Step 确认）

| 表 | 索引 | 类型 | 服务的方法 |
| --- | --- | --- | --- |
| conversation | `conversation_id` | PK（唯一） | `get_by_conversation_id` |
| conversation_turn | `turn_id` | PK（唯一） | 行身份 / 排序 tiebreaker |
| conversation_turn | `ix_conversation_turn_conversation_id_created_at`（列序 `(conversation_id, created_at)`） | 复合 B-tree | `list_turns_by_conversation_id` |

### 8.2 明确不建（除非未来架构明确要求）

```text
project_id index
status index
assistant_request_id index
role index
created_at 全局索引
```

命名沿用 `ix_<table>_<columns>` 规范。

---

## 9. Ordering

Repository 必须保证（`list_turns_by_conversation_id`）：

```sql
ORDER BY created_at ASC, turn_id ASC
```

* 不能只 `ORDER BY created_at`：同一会话内并发/快速追加可能产生相同时间戳；
* 不能把 `ORDER BY turn_id` 作为业务语义依据（`turn_id` 只是存储稳定 tiebreaker）；
* 最终组合：`created_at ASC` + `turn_id ASC`（与既有读边界 `created_at ASC, id ASC` 同风格）。

---

## 10. Status / Role

### 10.1 status（String，不创建 PG ENUM）

```text
列：String(32) NOT NULL default 'ACTIVE'
值：ACTIVE · ARCHIVED
```

* 与 `knowledge_document.status`（`String(32)` + default）保持一致；
* 不创建 PostgreSQL ENUM 类型（项目现状无 ENUM 类型；Python 侧 StrEnum 仅用于 DTO）；
* 禁止新增：`DELETED` / `CLOSED` / `CANCELLED` / `SUSPENDED`。

### 10.2 role（String）

```text
列：String(32) NOT NULL
值：USER · ASSISTANT
```

* 第一版不加 `system` / `tool`（Conversation History 不等同于 LLM raw messages）；
* 与 status 同列宽风格（`String(32)`）。

---

## 11. Delete Semantics

```text
Conversation delete ≠ Observability delete
```

| 对象 | 删除行为 |
| --- | --- |
| `conversation` | 物理删除（未来；本步不实现 API） |
| `conversation_turn` | **随会话删除**（FK ON DELETE CASCADE） |
| `llm_usage_record` | 保留 |
| `tool_execution_record` | 保留 |
| `rag_execution_record` | 保留 |
| `assistant_outcome_record` | 保留 |

原因：**assistant_request_id 只是 correlation**，不是 Conversation 的外键；
观测记录由 Observability 自己的 retention 策略管理
（Step 2 §12：History retention 与 Observability retention 是两套独立策略）。

本 Step 不实现任何删除代码 / API，只冻结语义与 FK 上的 CASCADE 设计。

---

## 12. Project Binding

* `create` 时把 `project_id` 写入 DB（NOT NULL）；
* 之后 `get` / `append` / `archive` / `list` 全部 **by conversation_id**；
* 客户端传入的 project_id **不得覆盖**已有绑定（Repository / Service 的定位参数
  只有 conversation_id；一致性校验属未来 Application Layer）；
* 本阶段只验证 Contract，**不实现 Authorization**。

---

## 13. Security

### 13.1 持久化层禁止保存

```text
api_key · password · Authorization · headers · database URL
LLM prompt · system prompt · tool definitions · raw SQL
RAG chunk content · embedding · DB Session · ORM Session
raw provider response
```

`conversation_turn.content` 只允许**用户可见的普通 user/assistant 内容**。

### 13.2 ORM / DTO 分离

```text
Service = business lifecycle
Repository = persistence
```

* Repository 对外返回 frozen Row（`ConversationRow` / `ConversationTurnRow`）；
* **不返回 ORM 对象**（ORM 只存在于 db 层）；
* Session 不得逃逸出 Repository（不返回 Session / 不接受外部 Session 作为业务参数）；
* Repository **不允许**依赖 AIOrchestrator / RagService / ToolChatService / LLM /
  FastAPI / HTTPException（AST 守卫见测试 §15）。

---

## 14. Migration Strategy

### 14.1 当前策略（审计结论）

```text
当前：Base.metadata.create_all()（幂等）+ 幂等 ensure_* DDL
不是：Alembic
```

* 证据：`db/init_db.py`（`create_all` + `CREATE SCHEMA IF NOT EXISTS` +
  `ensure_request_id_idempotency_index` / `ensure_assistant_request_id_column` /
  `ensure_assistant_request_id_index`）；
* `db/models/__init__.py` 注明"Alembic migration — Phase 3.5+"（未实现）；
* 仓库中不存在 `alembic.ini` / `alembic/` / `migrations/`；
* `docs/database.md` §5 规划"迁移工具 Alembic（Phase 2+）"——**规划与落地现状存在差异**，
  本 Step 如实记录：实际尚未引入 Alembic。

### 14.2 Future migration strategy

```text
Future migration strategy:
  1) 全新库：Conversation / ConversationTurn 随 Base.metadata.create_all() 自动创建
     （新 Model 加入 db/models/__init__.py 的 _MODELS 即可）。
  2) 既有库：如需补建索引 / 列，沿用 init_db() 的幂等 ensure_* DDL 模式
     （CREATE INDEX IF NOT EXISTS / ADD COLUMN IF NOT EXISTS），不做数据回填。
  3) 若项目后续引入 Alembic：Conversation 两张表应在 Alembic 基线中显式记录，
     并把 init_db() 作为 Alembic 入口的替换点（init_db.py 已预留该扩展点）。
  4) 本阶段不选择 / 不引入任何 migration 工具。
```

### 14.3 本 Step 的执行声明

```text
Step 4 does not execute migration.
```

不运行 `alembic upgrade`、不运行 `init_db`、不建表、不改 schema。

---

## 15. Test DB Strategy

### 15.1 现状

| 项 | 现状 |
| --- | --- |
| DB 测试门控 | `RUN_DB_TESTS=1` 环境变量 + `pytest.mark.skipif`（如 `tests/test_assistant_outcome_contract_audit.py`、`tests/test_ai_orchestrator.py`） |
| 建表 | 测试内调用 `init_db_module.init_db()`（幂等；**不**改 schema） |
| 残留清理 | `tests/conftest.py` 的 `_assistant_outcome_residue_guard`（按 id 水位删除"本次会话新增"行，不影响既有数据） |
| 清理惯例 | 各 DB 测试各自定向清理自己写入的行（不使用 TRUNCATE） |

### 15.2 未来 Conversation DB 测试的约定（只设计）

1. 保持 `RUN_DB_TESTS` 门控；本 Step **不修改** `tests/conftest.py`；
2. 表由 `init_db()` 幂等创建（未来新 Model 注册到 `_MODELS` 后自动生效）；
3. 测试**不**创建/销毁 schema（schema 由 `init_db()` 负责）；
4. 每个测试结束时按"自己写入的 conversation_id / turn_id"精确清理
   （沿用既有测试惯例；不使用 TRUNCATE；不删除观测表数据）；
5. 不使用"测试内开启事务 + 回滚"的隔离策略：Repository 自己持有事务并 commit，
   回滚式 fixture 会与现有 Repository 形态冲突；
6. 本 Step 不创建任何 fixture、不建表。

---

## 16. Deferred Implementation

本 Step 结束后的指标（测试 + git diff 双重确认）：

```text
Production Code = 0
DB Schema = unchanged
Migration = 0
DB Writes = 0
Network = 0
LLM = 0
```

明确 Deferred（全部未实现）：

| 事项 | 状态 |
| --- | --- |
| Conversation / ConversationTurn ORM Model | Deferred（本步禁止） |
| 建表 / FK / 索引 / schema | Deferred（本步禁止） |
| ConversationRepository | Deferred（本步禁止） |
| `init_db()` 的新 schema / ensure_* 步骤 | Deferred |
| Delete / archive 的落库实现 | Deferred（语义见 §11 / Step 3） |
| Alembic 或其它 migration 工具 | Deferred（策略记录见 §14，不选择） |
| Conversation DB 测试 / fixture | Deferred（约定见 §15） |
| Conversation API / Chat API / Context Builder / Memory / Regenerate API | Deferred |
| 认证 / 授权 / client_message_id | Deferred |

---

## 17. Audit Evidence

### 17.1 实际阅读文件（本步）

```text
backend/app/db/base.py
backend/app/db/session.py
backend/app/db/init_db.py
backend/app/db/models/__init__.py
backend/app/db/models/assistant_outcome_record.py
backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py（列定义 / 索引）
backend/app/db/models/rag_execution_record.py（列定义 / 索引）
backend/app/db/models/knowledge_document.py（status / updated_at 先例）
backend/app/db/models/knowledge_chunk.py（Text / FK CASCADE 先例）
backend/app/db/assistant_outcome_repository.py（事务 / Row / 错误）
backend/app/db/llm_usage_repository.py（方法清单 / partial unique）
backend/app/db/tool_execution_repository.py（方法清单 / build_* SQL 构造点）
tests/conftest.py（残留守卫）
tests/test_assistant_outcome_contract_audit.py（RUN_DB_TESTS 门控）
docs/database.md（§5 迁移策略规划）
```

搜索关键词覆盖：`Base` / `DeclarativeBase` / `Mapped` / `mapped_column` /
`relationship` / `ForeignKey` / `Index` / `UniqueConstraint` / `server_default` /
`created_at` / `updated_at` / `status` / `BIGINT` / `String` / `Text`。

### 17.2 与任务提示的差异（如实记录）

| 任务提示 | 实际情况 |
| --- | --- |
| `backend/app/db/repositories/` | 不存在该子目录；仓储扁平放置在 `backend/app/db/`（Step 3 §17.2 已记） |
| "migration 当前是否使用 Alembic" | 否（无 Alembic 产物）；`docs/database.md` §5 为**规划**，与落地现状存在差异（§14.1） |

### 17.3 本 Step 变更范围（git diff 证明）

```text
允许：
    tests/test_conversation_persistence_contract.py                    （新增）
    docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md（新增）

必须不变：
    backend/app        = unchanged
    backend/app/api    = unchanged
    backend/app/db     = unchanged
    migrations         = 无（本仓库无 migration 目录）
    .github            = unchanged
```

### 17.4 运行记录

```text
python -m pytest -q tests/test_conversation_persistence_contract.py
    → 62 passed（纯离线：不连接 DB / 不执行 init_db / 不运行 alembic / 不建表；
      本机环境本次运行 session fixture setup ~260s，见下）

python -m pytest -q tests/test_conversation_persistence_contract.py --noconftest
    → 62 passed in 0.74s（对照：测试文件自身 < 1s，零 DB / 零网络）

python -m compileall -q backend tests scripts
    → 通过
```

纯度口径（本 Step 产物）：DB Writes = 0 · DB reads = 0 · network = 0 · LLM = 0。

环境说明（延续 Step 1~3）：仓库 `tests/conftest.py` 的既有会话级残留守卫在配置了
`DATABASE_URL` 的环境下会执行 1 次水位查询与 1 次"本次会话新增行"清理；
本 Step 测试不产生 outcome 行（DELETE 影响 0 行），且属既有测试基础设施行为。
如需严格 0 接触，可使用 `--noconftest` 对照运行。

未运行：`RUN_DB_TESTS` / 全量 `pytest -q` / `alembic upgrade` / `init_db`
（本 Step 明确排除）。

---

## 18. 参考资料

* `docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md`
* `docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md`
* `docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md`
* `docs/database.md`（表规划 / 迁移策略规划）
* `docs/architecture.md`
* `backend/app/db/init_db.py`（建表与幂等 DDL 的真实入口）
