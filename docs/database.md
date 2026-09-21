# WMS AI Assistant — 数据库设计

> 当前文档对应 MVP 演进路径。Phase 1 **不实际接入数据库**，仅规划。

---

## 1. 文档信息

| 项目     | 内容                              |
| -------- | --------------------------------- |
| 当前阶段 | MVP — Phase 1（无数据库）         |
| 目标数据库 | PostgreSQL + pgvector（Phase 2+） |

---

## 2. Phase 1 现状

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

## 5. 迁移策略（Phase 2+ 启用）

- 工具：**Alembic**（SQLAlchemy 官方迁移工具）。
- 每次 schema 变更：
  1. 检查依赖（其它模块、文档、迁移历史）。
  2. 更新本文件 `docs/database.md`。
  3. 生成 Alembic 迁移文件。
  4. 本地验证 + 测试。
  5. 提交评审后再应用到生产。
- **禁止未经确认删除生产数据或修改关键业务字段**（`AGENTS.md` §19）。

---

## 6. 向量数据（Phase 3 规划）

- 扩展：`pgvector`。
- Chunk 表字段至少包含：`id`、`document_id`、`content`、`embedding`、`source`、`metadata`、`created_at`。
- Metadata 可记录：`document_name`、`document_type`、`department`、`warehouse`、`version`、`page`、`section`。
- 检索时必须保留来源信息，回答中可追溯到具体文档（`docs/requirements.md` FR-003 §10.3）。

---

## 7. 当前 TODO

- Phase 1：不创建任何数据库相关代码或迁移。
- Phase 2 启动时再补充：
  - `db/session.py`（SQLAlchemy async session）
  - `db/models/`（SQLAlchemy ORM 模型）
  - `alembic/` 目录与 `alembic.ini`
  - 本文件表结构详细字段
