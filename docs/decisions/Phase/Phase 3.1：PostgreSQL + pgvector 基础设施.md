现在开始 **Phase 3.1：PostgreSQL + pgvector 数据库基础设施**。

当前 Phase 1、Phase 2 和 Real LLM Smoke Test 已完成。

本阶段目标只有：

> 为后续 RAG 准备 PostgreSQL + pgvector 基础设施。

**不要实现 RAG、Embedding、知识库上传、文档切分、向量检索、Tool Calling、Agent、LangGraph、WMS API。**

---

# 一、先阅读项目规范

开始前必须阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

然后检查当前项目：

* `backend/app/config.py`
* `backend/app/main.py`
* `backend/app/api/health.py`
* `requirements.txt`
* `.env.example`
* `.gitignore`
* 当前 tests

理解现有 Phase 1 / Phase 2 的实现，不要破坏已有功能。

---

# 二、本阶段技术目标

增加：

```text
FastAPI
   ↓
Database Layer
   ↓
PostgreSQL
   ↓
pgvector extension
```

使用：

* PostgreSQL
* pgvector
* SQLAlchemy 2.x
* psycopg 3
* pytest

如果项目当前已经存在数据库相关依赖或实现，请优先复用，不要重复创建。

---

# 三、数据库连接配置

完善：

```text
backend/app/config.py
```

增加数据库配置，使用环境变量。

建议支持：

```env
DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/wms_ai
```

同时更新：

```text
.env.example
```

只写示例值，绝对不要写真实密码。

注意：

* `.env` 不得提交 Git
* 数据库密码不得写死在代码
* 不要输出 `.env` 的敏感内容
* 不要输出真实数据库密码

---

# 四、建立 Database Layer

建立清晰的数据库基础设施，例如：

```text
backend/app/db/
├── __init__.py
├── session.py
├── base.py
└── init_db.py
```

具体结构可以根据当前项目调整，但必须保持职责清晰。

要求：

### session.py

负责：

* Database Engine
* Session
* 数据库连接管理

使用 SQLAlchemy 2.x 风格。

### base.py

提供：

```python
Base
```

供后续 ORM Model 使用。

### init_db.py

负责数据库初始化相关逻辑。

不要在这里实现业务数据。

---

# 五、增加数据库健康检查

扩展现有：

```text
GET /api/health
```

但不要破坏原来的健康检查。

可以增加数据库状态，例如：

```json
{
  "status": "ok",
  "database": "ok"
}
```

如果数据库不可用：

```json
{
  "status": "ok",
  "database": "error"
}
```

具体返回结构以现有 API 设计为准。

重点：

**数据库连接失败不能导致应用启动阶段直接崩溃。**

如果当前架构有更合理的处理方式，可以采用，但必须保持 API 清晰。

---

# 六、启用 pgvector

准备 PostgreSQL：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

需要增加初始化方式。

可以选择：

1. SQL 初始化脚本
2. Python 初始化脚本

如果项目暂时没有 Alembic，请**不要为了这个阶段强行引入复杂 Migration 系统**。

但是请为以后引入 Alembic 留出清晰结构。

---

# 七、验证 pgvector

数据库初始化后执行：

```sql
SELECT extname
FROM pg_extension
WHERE extname = 'vector';
```

应该能够看到：

```text
vector
```

再验证：

```sql
SELECT '[1,2,3]'::vector;
```

确保 pgvector 真正可用。

---

# 八、暂时不要创建知识库表

本阶段：

**不要创建：**

```text
knowledge_document
knowledge_chunk
```

也不要创建 embedding 字段。

这些放到 **Phase 3.2**。

本阶段只负责：

```text
PostgreSQL
+
SQLAlchemy
+
psycopg
+
pgvector extension
+
Database Connection
+
Health Check
```

---

# 九、增加 Docker PostgreSQL 环境

如果项目当前没有 PostgreSQL 本地开发环境，请增加：

```text
docker-compose.yml
```

用于开发环境启动 PostgreSQL + pgvector。

优先使用官方/成熟的 pgvector PostgreSQL 镜像。

要求：

* PostgreSQL 数据持久化
* 暴露本地 PostgreSQL 端口
* 创建数据库 `wms_ai`
* 使用环境变量配置密码
* 不把真实密码写入 Git

例如开发环境可以使用：

```text
PostgreSQL
localhost:5432
database: wms_ai
```

如果当前机器已经有 PostgreSQL，可以不要强制迁移，只提供 Docker 开发方案。

---

# 十、增加数据库测试

新增数据库测试，例如：

```text
tests/test_db.py
```

至少覆盖：

1. Database 配置读取
2. Database URL 配置
3. 数据库连接
4. 简单 SQL 查询
5. pgvector extension 是否存在

注意：

如果测试环境没有 PostgreSQL：

* 不要让整个 pytest 因数据库环境缺失而失败
* 可以合理 skip
* 但是必须明确告诉我数据库测试是否真正执行

例如：

```text
PostgreSQL available → run tests
PostgreSQL unavailable → skip database integration tests
```

不要用 Mock 冒充 PostgreSQL 连接测试。

---

# 十一、依赖管理

根据当前 `requirements.txt` 添加必要依赖。

优先：

```text
SQLAlchemy
psycopg[binary]
```

如果 pgvector Python 集成在当前阶段确实需要，再添加：

```text
pgvector
```

不要提前安装：

* LangChain
* LangGraph
* Chroma
* FAISS
* Milvus
* Pinecone
* Redis
* Celery

本阶段只需要 PostgreSQL + pgvector。

---

# 十二、不要修改现有 LLM 逻辑

以下文件如果没有必要，禁止修改：

```text
backend/app/llm/
backend/app/services/llm_service.py
backend/app/services/chat_service.py
backend/app/prompts/
```

Phase 2 已经验证 DeepSeek 正常。

本阶段数据库建设不能影响：

```text
/api/chat
```

---

# 十三、回归测试

完成后执行：

```bash
pytest
```

然后启动：

```bash
uvicorn backend.app.main:app --reload
```

测试：

```text
GET /api/health
```

以及：

```text
POST /api/chat
```

`/api/chat` 必须仍然可以正常工作。

本阶段如果不需要真实 DeepSeek 调用，不要为了测试再次调用真实 LLM，避免消耗 API 额度。

---

# 十四、最终检查

请最终确认：

```text
[ ] PostgreSQL 可以启动
[ ] wms_ai 数据库存在
[ ] FastAPI 可以连接 PostgreSQL
[ ] pgvector extension 已启用
[ ] Database Health Check 正常
[ ] Database 测试通过
[ ] 原有 35 个测试没有被破坏
[ ] /api/chat 没有被破坏
[ ] .env 没有进入 Git
[ ] 没有输出任何真实密码/API Key
```

---

# 十五、完成后只输出报告，不继续 Phase 3.2

报告格式：

## Phase 3.1 Result

| 项目             | 状态       |
| -------------- | -------- |
| PostgreSQL     | ✅/❌      |
| pgvector       | ✅/❌      |
| SQLAlchemy     | ✅/❌      |
| psycopg        | ✅/❌      |
| Database Layer | ✅/❌      |
| Health Check   | ✅/❌      |
| Docker         | ✅/❌      |
| Database Tests | ✅/❌      |
| 原有测试           | X passed |
| `/api/chat`    | ✅/❌      |

## 修改文件

列出新增和修改的文件，以及每个文件的用途。

## 数据库验证

提供：

* PostgreSQL 版本
* pgvector 是否启用
* 数据库连接测试结果

不要输出：

* 数据库密码
* API Key
* `.env` 敏感内容

## 当前状态

明确回答：

> Phase 3.1 是否完成？

如果完成：

> 可以进入 Phase 3.2。

如果失败：

> 停留在 Phase 3.1，并说明具体问题。

**不要自行进入 Phase 3.2。**
