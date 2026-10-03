# Phase 4.1 Step 5 — Conversation Persistence Implementation

> 状态：**Persistence Layer 已实现**（仅持久化层；不含 API / Chat 接入 / AI Core 改动）
> 范围：`Conversation` / `ConversationTurn` 的 ORM + Repository + Service 最小实现
> 产物：
>
> ```text
> backend/app/db/models/conversation.py          （新增 ORM）
> backend/app/db/models/conversation_turn.py     （新增 ORM）
> backend/app/db/models/__init__.py              （注册两个 Model）
> backend/app/db/conversation_repository.py      （新增 Repository）
> backend/app/services/conversation_service.py   （新增 Service）
> tests/test_conversation_persistence_db.py      （新增测试：离线契约 + DB-gated）
> docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md
> ```
>
> 未改动（不变式）：
>
> ```text
> API = unchanged            （backend/app/api 零改动；Conversation API 未实现）
> AI Core = unchanged        （AIOrchestrator / AIRouter 零改动）
> RAG = unchanged · Tool = unchanged · LLM = unchanged
> Trace / Timeline / Outcome = unchanged
> Migration = 0              （无 Alembic / 无迁移文件）
> ```

---

## 1. Summary

Phase 4.1 Step 5 首次修改生产代码，目标是完成：

```text
ConversationService
        ↓
ConversationRepository
        ↓
Conversation / ConversationTurn ORM
        ↓
PostgreSQL（ai_ops schema）
```

实现严格对齐 Step 2（模型）/ Step 3（Service 边界）/ Step 4（持久化设计）的冻结结论：

* ORM 只有 5 / 6 个字段；
* Repository 只有 5 个方法（无 delete / find_by_project / search / count / pagination）；
* Service 只有 5 个方法（无第六个业务方法）；
* 事务归属 = Repository（方案 A）；Service 不接触 Session；
* `assistant_request_id` **不是**数据库外键；
* 索引只有 2 个主键 + 1 个复合索引。

---

## 2. ORM

### 2.1 `ai_ops.conversation`

| 列 | 实现 |
| --- | --- |
| `conversation_id` | `String(128)` PRIMARY KEY NOT NULL（服务端签发；唯一 / 不可变） |
| `project_id` | `String(128)` NOT NULL |
| `created_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()` |
| `updated_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()` + `onupdate=func.now()` |
| `status` | `String(32)` NOT NULL `default='ACTIVE'` `server_default='ACTIVE'`（ACTIVE / ARCHIVED） |

文件：`backend/app/db/models/conversation.py`（`CONVERSATION_SCHEMA = "ai_ops"`）。

### 2.2 `ai_ops.conversation_turn`

| 列 | 实现 |
| --- | --- |
| `turn_id` | `BigInteger` PRIMARY KEY autoincrement |
| `conversation_id` | `String(128)` NOT NULL，FK → `ai_ops.conversation.conversation_id` ON DELETE CASCADE |
| `role` | `String(32)` NOT NULL（USER / ASSISTANT） |
| `content` | `Text` NOT NULL |
| `assistant_request_id` | `String(128)` NULL（correlation；**无 FK**） |
| `created_at` | `DateTime(timezone=True)` NOT NULL `server_default=func.now()` |

文件：`backend/app/db/models/conversation_turn.py`。

### 2.3 索引（最小集合）

```text
conversation          PRIMARY KEY (conversation_id)
conversation_turn     PRIMARY KEY (turn_id)
conversation_turn     ix_conversation_turn_conversation_id_created_at
                      (conversation_id, created_at)
```

无其它索引（不建 project_id / status / assistant_request_id / role / created_at 全局索引）。

### 2.4 风格复用（与既有模型一致）

`DeclarativeBase` + `Mapped[...]` / `mapped_column(...)` + 每列 `comment` +
`__table_args__ = (..., {"schema": ...})` + `BigInteger` 自增主键 +
`DateTime(timezone=True)` + string status（**不创建 PG ENUM**）。

---

## 3. ORM Registration / init_db

* `backend/app/db/models/__init__.py`：`_MODELS` 追加 `Conversation` /
  `ConversationTurn`（Conversation 先于 ConversationTurn：后者有指向前者的 FK），
  `__all__` 同步；**未修改**已有模型的注册行为；
* `Base.metadata` 因此能发现两张新表 → `init_db()` 的
  `Base.metadata.create_all(bind=conn)` 会幂等创建它们；
* schema：`ai_ops` **已由现有** `CREATE SCHEMA IF NOT EXISTS ai_ops` 保证
  （`db/init_db.py`），因此**不需要**新增 `ensure_*_schema()`；
  本 Step **未修改** `init_db.py`（最小修改原则）；
* **Migration = 0**：无 Alembic、无迁移文件、未执行 `alembic upgrade` / `init_db`。

---

## 4. Repository

文件：`backend/app/db/conversation_repository.py`。

### 4.1 方法（Step 4/3 冻结的 5 个）

| 方法 | 事务 | 语义 |
| --- | --- | --- |
| `create(*, conversation_id, project_id, status=ACTIVE)` | `session.begin()` | 插入会话；**不生成** conversation_id |
| `get_by_conversation_id(conversation_id)` | 读（无写事务） | 无记录 → `None` |
| `update_status(conversation_id, status)` | `session.begin()` | 状态 + `updated_at = now()`；无匹配 → `None` |
| `append_turn(*, conversation_id, role, content, assistant_request_id)` | 单事务 | INSERT Turn + UPDATE `updated_at`（原子） |
| `list_turns_by_conversation_id(conversation_id)` | 读（无写事务） | `created_at ASC, turn_id ASC`；会话不存在 → 抛错 |

* 构造：`ConversationRepository(session_factory=None)` → `_get_session_factory()`；
  DB 未配置 → `ConversationRepositoryError`（**不**返回 `[]` / `None`）；
* SQL 构造点：`build_conversation_select` / `build_conversation_insert` /
  `build_status_update` / `build_conversation_touch_update` / `build_turn_select` /
  `build_turn_insert` / `build_conversation_status_lookup`（唯一构造位置，便于测试）；
* 显式列：`CONVERSATION_READ_COLUMNS` / `CONVERSATION_TURN_READ_COLUMNS`
  （**无** `SELECT *`）；
* Row DTO：`ConversationRow` / `ConversationTurnRow`（frozen dataclass；
  **不**返回 ORM 对象 / Session）；
* 校验：`validate_conversation_id` / `validate_project_id` / `validate_status` /
  `validate_role` / `validate_content` / `validate_assistant_request_id`（`ValueError`）；
* 错误：`ConversationRepositoryError`（基类）+ `ConversationNotFoundRepositoryError` /
  `ConversationArchivedRepositoryError`（子类，供 Service 精确映射）；
  `SQLAlchemyError` 一律包装为 `ConversationRepositoryError`，**不泄漏**到 Service / API。

### 4.2 append_turn 原子性

```text
BEGIN
  1) SELECT ai_ops.conversation.status        （存在性 + 状态守卫）
  2) INSERT ai_ops.conversation_turn
  3) UPDATE ai_ops.conversation SET updated_at = now()
COMMIT
```

* 三步在**同一个** `with factory() as session, session.begin():` 内；
* 任一步失败 → 事务回滚（Session.begin() 在异常传播时 rollback），
  绝不留"Turn 已存在但 `updated_at` 未更新"；
* ARCHIVED 在**插入之前**拒绝（write before reject = 0），
  抛 `ConversationArchivedRepositoryError`。

---

## 5. Service

文件：`backend/app/services/conversation_service.py`。

### 5.1 方法（5 个，无第六个）

```text
create_conversation(*, project_id)                                    → ConversationView
get_conversation(conversation_id)                                     → ConversationView | None
archive_conversation(conversation_id)                                 → ConversationView
append_turn(*, conversation_id, role, content, assistant_request_id)  → ConversationTurnView
list_turns(conversation_id)                                           → tuple[ConversationTurnView, ...]
```

### 5.2 要点

* **ID 生成在 Service**：`new_conversation_id()`（`uuid.uuid4()`）；
  Repository 不生成 ID；客户端不可提供 conversation_id / status / created_at / updated_at；
* `create_conversation`：校验 project_id（非空 / ≤128）→ 生成 ID → `repository.create(status=ACTIVE)`；
* `get_conversation`：无记录 → `None`（沿用既有 QueryService 的"None 不猜"风格）；
* `archive_conversation`：不存在 → `ConversationNotFoundError`；
  ARCHIVED → **无 DB 写入**直接返回（幂等，第二次不报错）；否则 `update_status(ARCHIVED)`；
* `append_turn` 流程（Service 是业务规则 owner）：

```text
get conversation → 不存在 → ConversationNotFoundError
                 → ARCHIVED → ConversationArchivedError（DB write = 0）
                 → 校验 role / content / assistant_request_id（ValueError）
                 → USER：assistant_request_id 必须 None
                   ASSISTANT：assistant_request_id 必须非空
                 → repository.append_turn()
```

* `list_turns`：会话不存在 → `ConversationNotFoundError`（不与"存在但无消息"混同）；
  存在但无消息 → `()`；
* 错误映射：`ConversationNotFoundRepositoryError → ConversationNotFoundError`、
  `ConversationArchivedRepositoryError → ConversationArchivedError`；
  其它 `ConversationRepositoryError` 原样上抛（**不吞**）；
* View DTO：`ConversationView` / `ConversationTurnView`（frozen；API 未来消费 Service 类型，
  不依赖 db 层 Row）。

### 5.3 边界（实现层面已遵守）

* 不 import FastAPI；不接触 Session / select / insert / update / engine；
* 不调用 AIOrchestrator / Router / RAG / Tool / LLM；
* 不查询 Trace / Timeline / Outcome；
* 不生成 assistant_request_id（由 AIOrchestrator 生成后传入）。

---

## 6. Transaction

```text
Conversation transaction ≠ AI / LLM transaction
```

* Conversation 写事务由 Repository 持有（方案 A），短事务、不含网络调用；
* 未来一次 Chat 的两段式（Step 3 §11 冻结，本 Step 未接入）：

```text
[DB tx #1] 写 USER Turn → commit
[no tx]    AI / LLM execution
[DB tx #2] 写 ASSISTANT Turn + 更新 updated_at → commit
```

* 本 Step **没有**把任何 AI/LLM 调用包进数据库事务（也未接入 AI Core）。

---

## 7. Delete / Lifecycle

* 本 Step **未实现** Delete API / delete 方法；
* 数据库层面已按 Step 2 §11 / Step 4 §11 冻结：
  `conversation_turn.conversation_id → conversation.conversation_id ON DELETE CASCADE`
  → 未来物理删除会话会连带删除其 Turns；
* 4 张观测表**不受影响**（`assistant_request_id` 不是 FK，只是 correlation）：

```text
Conversation delete ≠ Observability delete
```

---

## 8. Ordering

`list_turns_by_conversation_id` 固定：

```sql
ORDER BY created_at ASC, turn_id ASC
```

（SQL 由 `build_turn_select` 构造；离线编译断言已锁定。）

---

## 9. Project Binding

* `create` 写入 `project_id`（NOT NULL）；
* `get` / `append` / `archive` / `list` 全部**只按 conversation_id** 定位
  （Repository / Service 方法签名中没有 project_id 定位参数）；
* 客户端 project_id **不得覆盖**已有绑定；本 Step 未实现授权校验（Authorization = Deferred）。

---

## 10. Security

* ORM 列与 Row / View 字段**严格等于** Step 2 冻结字段，不存在：
  `api_key` / `password` / `Authorization` / `headers` / `database URL` / `LLM prompt` /
  `system prompt` / `tool definitions` / `raw SQL` / `RAG chunk content` / `embedding` /
  `Session` / `ORM Session` / `raw provider response`；
* `conversation_turn.content` 只保存用户可见的普通 user / assistant 文本；
  Repository 不会自行扩展字段保存上述敏感内容（本 Step 不做 DLP / 不做内容改写）；
* Repository 不返回 ORM 对象 / Session（Session 不逃逸 db 层）。

---

## 11. DB Tests

文件：`tests/test_conversation_persistence_db.py`。

| 部分 | 门控 | 内容 |
| --- | --- | --- |
| 离线契约 | 始终运行 | ORM schema（列 / 类型 / schema / FK / 无观测 FK / 单一复合索引 / 注册）+ SQL 编译（显式列 / ORDER BY / INSERT / UPDATE 目标） |
| DB-gated 行为 | `RUN_DB_TESTS=1` | Conversation CRUD、Turn CRUD、校验规则、归档幂等、归档后拒绝追加（write = 0）、append_turn 原子性（UPDATE 失败 → 回滚）、删除语义（Turn 级联 + 观测保留）、正文往返 |
| 入参规则（离线） | 始终运行 | 非法 project_id / conversation_id 在触达 DB 前拒绝 |

离线运行结果：`17 passed, 19 skipped`（DB-gated 部分 SKIP，不访问 PostgreSQL）。

### 11.1 本机环境限制（如实记录）

尝试以 `RUN_DB_TESTS=1` 运行 DB 部分时，全部测试在 setup 阶段报

```text
psycopg.errors.ConnectionTimeout
- host: 'localhost', port: 5432, hostaddr: '::1': connection timeout expired
- host: 'localhost', port: 5432, hostaddr: '127.0.0.1': connection timeout expired
```

即**本机 PostgreSQL 当前不可达**（`ping_database()` 亦返回 False）。
该现象与 Step 1~4 中 `tests/conftest.py` 会话守卫的 ~260s setup 同源（连接超时）。

因此：

* DB-gated 用例**已实现但未在本机执行成功**（环境限制，非代码问题）；
* 本次实际 **DB Writes = 0**（连不上数据库，未写入任何数据）；
* 表创建（`init_db` → `create_all`）同样未在本机发生；
  待数据库可用后按 §29 命令重跑即可验证 CRUD / 原子性 / 删除语义。

---

## 12. Residue

* 清理机制（已实现）：每个 DB 用例创建的 conversation 由 `created` fixture 收集，
  结束时按 `conversation_id` 精确 `DELETE`（FK CASCADE 连带删 Turns）；
  不使用 TRUNCATE；不删除观测表数据；
* 观测行：删除语义用例写入的 `assistant_outcome_record` 行在用例内显式清理
  （`tests/conftest.py` 的既有水位守卫亦会兜底清理本次会话新增行）；
* 本次运行残留：**0**（DB 不可达，未产生任何写入）。

---

## 13. Deferred

| 事项 | 状态 |
| --- | --- |
| Conversation API（`POST /api/conversations` 等） | Deferred（本 Step 明确不实现） |
| Chat API 接入 Conversation / AIOrchestrator 组合 | Deferred |
| Context Builder / Memory / Summary | Deferred |
| Regenerate API | Deferred |
| Authentication / Authorization / ownership | Deferred |
| `client_message_id` 幂等键 | Deferred |
| Delete / 归档的业务 API | Deferred（语义已冻结） |
| Alembic / 迁移文件 | Deferred（当前策略：`create_all` + 幂等 `ensure_*` DDL） |
| Conversation 读模型与 Trace / Timeline 的组合端点 | Deferred |

---

## 14. Invariants（本 Step 结束时）

```text
API = unchanged            （backend/app/api 零改动）
AI Core = unchanged        （ai_orchestrator_service.py 零改动）
RAG = unchanged · Tool = unchanged · LLM = unchanged
Trace / Timeline / Outcome = unchanged
Migration = 0
Network = 0 · LLM = 0
```

回归验证：`tests/test_conversation_persistence_contract.py`（Step 4 契约）+
`tests/test_conversation_service_boundary_contract.py`（Step 3）+
`tests/test_conversation_model_contract.py`（Step 2）全部通过（147 passed）。

---

## 15. 运行记录

```text
python -m pytest -q tests/test_conversation_persistence_contract.py
    → 通过（纯离线契约；本机 conftest 会话守卫 setup ~260s，见 §11.1）

python -m pytest -q tests/test_conversation_persistence_db.py
    → 17 passed, 19 skipped（DB-gated 部分需 RUN_DB_TESTS=1）

$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_conversation_persistence_db.py
    → 环境不可达：PostgreSQL 连接超时（见 §11.1），未产生任何 DB 写入

python -m compileall -q backend tests scripts
    → 通过
```

---

## 16. 参考资料

* `docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md`
* `docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md`
* `docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md`
* `docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md`
* `backend/app/db/assistant_outcome_repository.py`（Repository 风格参考）
* `backend/app/db/init_db.py`（建表与幂等 DDL）
