# WMS AI Assistant — 数据库设计

> ⚠️ **文档状态校正（必读）**
>
> §1–§7 是 **Phase 1 时期的历史规划快照**，与当前实现**已不一致**，不得作为
> 现状依据。实际现状摘要：
>
> - 已接入 **PostgreSQL + pgvector**，ORM 表共 **24** 个：
>   `ai_ops`（22，AI 内部运维数据）+ `public`（2：`knowledge_document` /
>   `knowledge_chunk`）；
> - **未引入 Alembic**；schema 由 `python -m backend.app.db.init_db` 的
>   **幂等 DDL** 维护（详见 §5 与 §8.7）；
> - **应用启动不会自动建表**（`create_app()` 无 lifespan / startup 钩子），
>   部署必须先执行 `init_db`（详见 §8.6）。
>
> **§8 起**为按真实 ORM 模型 / Repository / 初始化代码记录的内容。

---

## 1. 文档信息

| 项目     | 内容                              |
| -------- | --------------------------------- |
| 当前阶段 | Phase 7 Recovery（已接入数据库）  |
| 数据库   | PostgreSQL + pgvector（已实现）   |

---

## 2. Phase 1 现状（历史快照；**已过时**）

> 本节内容仅记录 Phase 1 当时的状态，**与当前实现不符**（见文首状态校正）。

Phase 1 不引入任何持久化存储：

- Chat API 当前返回 Mock，不保存对话历史。
- Health API 无状态。
- 所有 API Key / 配置通过环境变量加载，不入数据库。

因此**不需要创建任何数据库 schema 或迁移文件**。

---

## 3. 数据库职责（规划）

按 `docs/architecture.md` §19，本服务**只存自己的数据**，不镜像 WMS 业务数据。

| 表                     | 阶段    | 说明                                       |
| ---------------------- | ------- | ------------------------------------------ |
| `conversation`         | Phase 2 | 会话主表                                   |
| `conversation_message` | Phase 2 | 会话消息（user / assistant / tool）        |
| `knowledge_document`   | Phase 3 | 知识库文档元数据（来源、版本、分类）       |
| `knowledge_chunk`      | Phase 3 | 文档切片 + embedding（pgvector）           |
| `tool_call_log`        | Phase 4 | Tool 调用审计（request_id、参数、结果状态）|
| `user`                 | Phase 6 | 用户表                                     |
| `role`                 | Phase 6 | 角色                                       |
| `permission`           | Phase 6 | 权限                                       |
| `audit_log`            | Phase 6 | 通用审计日志                               |

> 表结构、字段、索引、外键在对应 Phase 启动前**先在本文件更新，再创建迁移**。

---

## 4. 数据库技术约束

- 必须使用 **PostgreSQL + pgvector**（`docs/architecture.md` ADR-003）。
- 严禁使用 LLM 直接连接业务数据库（`AGENTS.md` §15.2）。
- Phase 1–5 单一数据库；规模增长后再拆分（`docs/architecture.md` §35）。
- 不引入 Qdrant / Milvus / Elasticsearch / Redis / Kafka 作为 MVP 一部分。

---

## 5. 迁移策略

> ⚠️ **本节原规划（Alembic）从未落地**，实际机制见下；不得据此执行 Alembic 操作。

**实际机制（无 Alembic）**：

- 工具：**无迁移框架**。schema 由 `backend/app/db/init_db.py` 的**幂等 DDL**
  维护：
  - `Base.metadata.create_all(bind=conn)` —— 创建**缺失**的表（已存在表整体跳过）；
  - `ensure_*` 系列 —— 为**已存在**表补列 / 补索引 / 补约束
    （`ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` 等）；
- 入口：`python -m backend.app.db.init_db`（应用启动**不**执行，见 §8.6）。
- 每次 schema 变更：
  1. 检查依赖（其它模块、文档、既有数据）。
  2. **更新本文件 `docs/database.md`**。
  3. 在 ORM 模型中声明（新表随 `create_all` 创建；既有表的新列/新索引
     必须补 `ensure_*`，否则老库不会获得该变更）。
  4. 专用测试库验证（`localhost:5433/wms_ai_test`）+ 测试。
  5. 提交评审后再应用到生产。
- **限制（如实记录）**：无版本号、无迁移历史、无回滚能力、无法表达破坏性
  变更、无法对既有表做列类型/约束的幂等演进（需人工 DDL）。新增表安全；
  修改既有表结构需人工评审与手工 DDL 方案。
- **禁止未经确认删除生产数据或修改关键业务字段**（`AGENTS.md` §19）。

---

## 6. 向量数据（Phase 3 规划）

- 扩展：`pgvector`。
- Chunk 表字段至少包含：`id`、`document_id`、`content`、`embedding`、`source`、`metadata`、`created_at`。
- Metadata 可记录：`document_name`、`document_type`、`department`、`warehouse`、`version`、`page`、`section`。
- 检索时必须保留来源信息，回答中可追溯到具体文档（`docs/requirements.md` FR-003 §10.3）。

---

## 7. 当前 TODO（历史快照；**已过时**）

> 本节为 Phase 1 当时的 TODO，**全部已完成或已被取代**：`db/session.py`、
> `db/models/` 均已存在；**未**引入 `alembic/`（见 §5）；表结构由 §8 起记录。

- ~~Phase 1：不创建任何数据库相关代码或迁移。~~
- ~~Phase 2 启动时再补充：~~
  - ~~`db/session.py`（SQLAlchemy async session）~~ —— 已实现（同步 psycopg 引擎）
  - ~~`db/models/`（SQLAlchemy ORM 模型）~~ —— 已实现（24 个模型）
  - ~~`alembic/` 目录与 `alembic.ini`~~ —— **有意不引入**（见 §5）
  - ~~本文件表结构详细字段~~ —— 自 §8 起按真实模型记录

---

## 8. `ai_ops.authorization_grant`（Phase 7 Recovery M2.4；**已实现**）

> 本节内容按真实 ORM 模型（`backend/app/db/models/authorization_grant.py`）、
> Repository（`backend/app/db/authorization_grant_repository.py`）与初始化代码
> （`backend/app/db/init_db.py`）记录。安全语义的权威说明见
> `docs/security.md` §11，接口行为见 `docs/api.md` §15。

### 8.1 用途与授权范围

存储**最小的用户—项目 / 用户—系统授权关系**（最小 RBAC）：

```text
(user_id, project_id) → role          项目级授权
(user_id, system_scope) → SYSTEM_OPS  系统级授权
```

- **一行 = 一个显式授权**；**无**隐式推导、**无**角色继承、**无**组织 / 租户树。
- 服务端是项目权限与角色的**唯一事实来源**；客户端传入的 role / membership
  **永不**作为判定依据。
- 本表位于 `ai_ops` schema（AI 内部运维数据），**不**进入业务 schema `public`。
- 授权**没有公开 HTTP 入口**；只能由管理员经
  `python -m backend.app.cli.grant_role` 显式写入。
- `user_id` **无**外键约束（沿用 `ai_ops` 既有风格）；不校验用户是否存在
  ⇒ 拼写错误会产生无效的孤儿授权行（无安全影响，但需人工清理）。

### 8.2 字段

| 列 | 类型 | 可空 | 默认 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | `BIGINT` | NOT NULL | 自增（identity / serial） | 代理主键（内部行号，**不是**业务 ID） |
| `user_id` | `VARCHAR(64)` | NOT NULL | — | 被授权用户（`ai_ops.app_user.user_id`；服务端签发 uuid4；**无 FK**） |
| `scope_type` | `VARCHAR(16)` | NOT NULL | — | 作用域类型：`PROJECT` / `SYSTEM`（**判定依据**；禁止从 `project_id` 是否为 NULL 推断） |
| `project_id` | `VARCHAR(128)` | **NULL** | — | 项目 ID；`PROJECT` ⇒ 必须非空（且 trim 后非空）；`SYSTEM` ⇒ 必须 `NULL`（由复合 CHECK 强制） |
| `role` | `VARCHAR(32)` | NOT NULL | — | `PROJECT` ⇒ `MEMBER` / `PROJECT_OPS`；`SYSTEM` ⇒ `SYSTEM_OPS`（组合由复合 CHECK 强制） |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | NOT NULL | `now()`（DB 端） | 授权登记时间 |

> 列宽与既有表对齐（`user_id` = `app_user.user_id`；`project_id` 与
> `tool_execution_record` / `resource_owner` 一致），避免跨表比较的隐式转换。

### 8.3 `scope_type` / `role` 的合法组合

**仅允许以下三种组合**，其余一律由数据库拒绝：

```text
PROJECT + MEMBER      + 非空 project_id
PROJECT + PROJECT_OPS + 非空 project_id
SYSTEM  + SYSTEM_OPS  + NULL project_id
```

⇒ `project_id IS NULL` 的**唯一**合法解释是 `SYSTEM` 作用域。因此"项目级权限
通过 NULL 或缺失记录隐式升级为系统级"在**写入侧即不可能**。

### 8.4 CHECK 约束

| 名称 | 规则 |
| --- | --- |
| `ck_authorization_grant_scope_role` | `(scope_type = 'PROJECT' AND project_id IS NOT NULL AND length(btrim(project_id)) > 0 AND role IN ('MEMBER', 'PROJECT_OPS')) OR (scope_type = 'SYSTEM' AND project_id IS NULL AND role = 'SYSTEM_OPS')` |

该谓词在模型中以常量 `AUTHORIZATION_SCOPE_ROLE_PREDICATE` 定义（模型与测试
共用同一字面量，避免两套真相），并由 `init_db()` 随建表创建。

**读侧防御（补充）**：即使约束被删除或绕过，Repository 在读取命中行时仍校验
`scope_type` ↔ `role` ↔ `project_id` 一致性；不一致 ⇒ 拒绝读取并抛出
`AuthorizationGrantRepositoryError`（调用方映射为 503 fail closed），
**不**猜测、**不**静默丢弃、**不**降级为"无授权"。

### 8.5 主键与索引

| 名称 | 类型 | 定义 |
| --- | --- | --- |
| `authorization_grant_pkey` | 主键 | `(id)` |
| `uq_authorization_grant_project` | **partial unique index** | `UNIQUE (user_id, project_id, role) WHERE scope_type = 'PROJECT'` |
| `uq_authorization_grant_system` | **partial unique index** | `UNIQUE (user_id, role) WHERE scope_type = 'SYSTEM'` |
| `ix_authorization_grant_user_id` | 普通索引 | `(user_id)` —— 授权判定入口，避免全表扫描 |

**为什么必须是两个 partial unique index**：PostgreSQL 的 `UNIQUE` 视 `NULL`
互不相等 ⇒ 单一 `UNIQUE(user_id, project_id, role)` **无法**阻止重复的
`SYSTEM` 行（其 `project_id` 全为 NULL）。因此系统级唯一性必须用 partial
index 才能成立。

### 8.6 表创建与部署要求

- **创建方式**：表由既有初始化入口创建 ——

  ```bash
  python -m backend.app.db.init_db
  ```

  其内部 `init_db.py` 显式 `from backend.app.db import models` 后调用
  `Base.metadata.create_all(bind=conn)`，因此**新表随 `create_all` 自动创建**，
  无需为该表编写 `ensure_*`；重复执行安全（已存在表整体跳过）。

- **★ 应用启动不会自动建表**：`create_app()` **没有** `lifespan` /
  `startup` 钩子，模块导入期也**不**建立数据库连接。因此**部署必须先完成
  数据库初始化**：

  ```text
  若跳过 init_db ⇒ 本表不存在 ⇒ 7 个用量/可观测性端点全部返回 503
  （fail closed；`ProgrammingError` ⊂ `SQLAlchemyError` ⇒
   AuthorizationGrantRepositoryError ⇒ AuthorizationStoreUnavailableError ⇒ 503）
  ⇒ 安全但**可观测面整体不可用**（含旧白名单用户）
  ```

- **权限要求**：执行初始化需要目标库上的 `CREATE TABLE` / `CREATE INDEX`
  （schema `ai_ops`）；`init_db()` 内的其它 `ensure_*` 步骤对**既有表**可能
  需要 `ALTER TABLE`。目标环境账号的**实际**权限为部署侧事项
  （生产部署就绪审查中标记为 `NOT VERIFIED`）。

- 部署与回滚步骤、检查清单见 M2.4 部署就绪审查记录。

### 8.7 无 Alembic 的现状与限制

- 本项目**没有** Alembic、**没有**版本化迁移机制、**没有**迁移历史表。
- 唯一机制是 §5 描述的 `init_db()` 幂等 DDL。
- **限制**：
  1. 无版本号 ⇒ 无法判定某个库当前处于哪一版 schema；
  2. 无回滚 ⇒ 变更不可自动撤销（回滚靠重新部署旧镜像 + 保留数据）；
  3. 无法表达破坏性变更（删列 / 改类型 / 改约束需人工 DDL 方案与评审）；
  4. `create_all(checkfirst=True)` 对**已存在**表整体跳过 ⇒ 既有表的新增
     列 / 索引**必须**显式补 `ensure_*`，否则老库静默缺少该变更；
  5. 该表为**全新表**，因此不受第 4 条影响（首次 `init_db()` 即建全）。
- 相关纪律：数据库结构修改前必须检查现有结构与依赖、记录迁移方案并更新本
  文件（`AGENTS.md` §19 / §22）。

### 8.8 写入的幂等性（重复授权）

- Repository 使用 `INSERT ... ON CONFLICT DO NOTHING RETURNING id`，
  冲突目标与上述 partial unique index 精确对应（含 `index_where`）。
- 重复授予**同一组合** ⇒ **幂等成功**：不报错、不覆盖、不新增第二行；
  Repository 返回"本次是否新增"，CLI 输出 `OK（已存在，未变更）` 且退出码 `0`。
- 同一用户在同一项目**可以**同时持有 `MEMBER` 与 `PROJECT_OPS`（两条不同行）。

### 8.9 相关文档与测试

| 主题 | 位置 |
| --- | --- |
| 安全语义 / 端点适用 / 残留风险 | `docs/security.md` §11 |
| 接口行为与状态码 | `docs/api.md` §15 |
| 配置（无新增环境变量）与 CLI 用法 | `.env.example`（M2.4 注释块） |
| 单元测试 | `tests/test_authorization_service.py`、`tests/test_authorization_api_enforcement.py` |
| 专用 PostgreSQL 集成测试（含约束 / 索引 / 幂等 / 读侧防御） | `tests/test_authorization_grant_db.py` |

> 本表**不**存储：密码 / 哈希 / 令牌 / 会话、项目业务数据、观测记录。
> 不得把真实连接字符串或用户信息写入本文件。
