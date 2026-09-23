你现在开始执行项目 **Phase 3.7.1.1 — Project / Data Source Abstraction**。

这是一个严格受控的单阶段任务。

目标：

> 在不破坏现有 RAG、Chat、Tool Calling、Multi-Step Tool Calling、Schema Explorer 的前提下，建立最小的 Project / Data Source 抽象，为未来更换项目、数据库和业务系统做准备。

---

# 一、开始前必须阅读

先阅读：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`
4. `docs/decisions/Phase 3.7.1 — ADR.md`
5. 当前 `backend/app/services/schema_explorer_service.py`
6. 当前 `backend/app/db/`
7. 当前 `backend/app/config.py`
8. 当前 Tool / RAG / Chat 相关代码

先理解现有架构。

**不要先重构，先检查现有代码。**

---

# 二、本阶段核心目标

建立两个最小抽象：

```text
ProjectContext
DataSource
```

概念关系：

```text
ProjectContext
├── project_id
├── project_name
├── description
└── data_source

DataSource
├── name
├── type
└── connection/config identity
```

但是：

**不要保存数据库密码、API Key 等秘密信息到 ProjectContext。**

ProjectContext 只描述“当前项目使用什么数据源”，真正连接配置继续复用现有 `DatabaseSettings` / `.env`。

---

# 三、重要设计原则

## 1. 不要做多租户系统

本阶段禁止实现：

* 用户 → 项目权限
* 多租户
* 项目管理 CRUD
* Project API
* 前端项目选择器
* 数据库动态创建
* 数据库动态切换
* Redis
* 配置中心

这里只建立代码层面的抽象。

---

## 2. 不要把 WMS 写死

不要出现类似：

```python
class WMSProject:
    ...
```

也不要：

```python
project_type = "wms"
```

作为核心架构依赖。

当前项目可以使用：

```text
project_id = "wms"
project_name = "Vietnam WMS"
```

作为默认配置/示例，但 Core 层不得依赖 WMS。

---

# 四、DataSource 抽象

建议建立：

```text
backend/app/projects/
```

或者根据当前项目结构选择最合理的位置。

建议考虑：

```text
backend/app/projects/models.py
backend/app/projects/context.py
```

具体文件可以根据现有架构调整，但不要无意义增加目录。

---

定义一个稳定的 DataSource DTO。

例如：

```python
DataSource(
    name="primary",
    type="postgresql",
)
```

`type` 不应该让整个项目依赖 PostgreSQL。

例如未来可以存在：

```text
postgresql
mysql
sqlserver
api
```

但本阶段只需要让当前 PostgreSQL 正常工作。

**不要实现 MySQL / SQL Server Adapter。**

---

# 五、ProjectContext

建立：

```python
ProjectContext(
    project_id=...,
    project_name=...,
    description=...,
    data_source=...,
)
```

要求：

* frozen dataclass
* 不可变
* 不持有 SQLAlchemy Engine
* 不持有 Session
* 不持有数据库密码
* 不持有 API Key
* 不暴露 `.env` 内容
* 不直接负责数据库连接

例如：

```text
ProjectContext
      │
      └── DataSource
```

而不是：

```text
ProjectContext
      │
      └── PostgreSQL Engine
```

---

# 六、当前项目默认 Context

增加一个最小的默认 Project Context。

例如：

```python
get_default_project_context() -> ProjectContext
```

当前可以返回：

```text
project_id: vietnam-wms
project_name: Vietnam WMS
data_source:
    name: primary
    type: postgresql
```

具体名称根据当前项目已有命名习惯调整。

**不要硬编码数据库密码、URL、用户名。**

数据库实际连接仍然使用：

```text
DatabaseSettings
```

---

# 七、Schema Explorer 解耦

这是本阶段最重要的代码修改。

当前：

```text
SchemaExplorerService
        ↓
PostgreSQL
```

调整为：

```text
SchemaExplorerService
        ↓
Metadata Provider / Adapter
        ↓
PostgreSQL
```

但是只做**最小抽象**。

例如：

```python
class DatabaseMetadataProvider(Protocol):
    async def inspect(
        self,
        *,
        schema: str | None = None,
    ) -> DatabaseSchema:
        ...
```

然后当前 PostgreSQL 实现：

```text
PostgreSQLMetadataProvider
```

负责现有的：

* pg_catalog 查询
* table
* column
* primary key
* foreign key
* comment

---

# 八、非常重要：不要复制 Schema Explorer

现有：

```text
SchemaExplorerService
```

已经通过测试验证。

不要重新实现一套 PostgreSQL catalog 查询。

应该：

```text
SchemaExplorerService
        ↓
DatabaseMetadataProvider
        ↓
PostgreSQLMetadataProvider
```

尽量保留原有 SQL 和行为。

---

# 九、兼容现有 API

现有：

```python
SchemaExplorerService.inspect(...)
```

必须继续可用。

现有测试：

```text
tests/test_schema_explorer_service.py
```

不能因为抽象层而大量修改。

目标：

```text
旧调用方式
↓
继续正常工作
```

而内部变成：

```text
SchemaExplorerService
        ↓
Provider
```

---

# 十、DataSource 不负责读取数据库

不要做：

```python
data_source.connect()
```

也不要让 DataSource 自己创建：

```text
Engine
Session
Connection
```

职责必须分离：

```text
ProjectContext
    ↓
描述当前项目

DataSource
    ↓
描述数据源

DatabaseSettings
    ↓
连接配置

DatabaseMetadataProvider
    ↓
读取数据库 Metadata

SchemaExplorerService
    ↓
业务层使用 Metadata
```

---

# 十一、测试

新增：

```text
tests/test_project_context.py
```

至少测试：

### ProjectContext

* 创建成功
* frozen / immutable
* 缺少 project_id 拒绝
* 缺少 project_name 拒绝
* description 可以为空

### DataSource

* 创建成功
* type 正常
* 不保存 password
* 不保存完整 DATABASE_URL
* frozen / immutable

### Default Context

验证：

```text
get_default_project_context()
```

能够正常返回当前项目上下文。

---

# 十二、Schema Explorer 回归测试

原有：

```text
tests/test_schema_explorer_service.py
```

必须全部通过。

新增测试至少确认：

```text
SchemaExplorerService
        ↓
PostgreSQLMetadataProvider
        ↓
当前 PostgreSQL
```

仍然可以获取：

* 2 个当前业务表
* 19 个字段
* 主键
* 外键
* COMMENT

不要修改真实业务数据。

---

# 十三、禁止事项

本阶段绝对不要实现：

* Text-to-SQL
* SQL Generator
* SQL Executor
* SQL Validator
* NL2SQL
* AI Router
* Agent
* LangGraph
* MCP
* 多租户
* Project HTTP API
* Project CRUD
* 前端
* MySQL Provider
* SQL Server Provider
* WMS API
* RAG 修改
* Tool Calling 修改
* Tool Registry 修改
* Chat 修改
* Embedding 修改
* Reranker 修改

---

# 十四、配置原则

不要增加复杂配置系统。

可以增加非常少量的：

```text
PROJECT_ID
PROJECT_NAME
PROJECT_DESCRIPTION
```

如果当前配置体系适合。

但不要把：

```text
DATABASE_URL
DATABASE_PASSWORD
```

复制到 ProjectContext。

数据库配置继续由现有 DatabaseSettings 管理。

---

# 十五、安全要求

代码、DTO、日志、测试输出都不能泄露：

* DATABASE_URL
* password
* API Key
* Authorization
* connection string

ProjectContext 只保存非敏感 metadata。

---

# 十六、文档

新增：

```text
docs/decisions/Phase 3.7.1.1 — ADR.md
```

说明：

1. 为什么需要 ProjectContext
2. 为什么 DataSource 与 DatabaseSettings 分离
3. 为什么 AI Core 不绑定 WMS
4. 为什么当前只实现 PostgreSQL
5. 未来换项目时哪些部分需要替换
6. 当前阶段为什么不做多租户

用简短 ADR，不要过度设计。

---

# 十七、验收标准

完成后执行：

```text
pytest -q
```

以及：

```text
RUN_DB_TESTS=1
```

对应的数据库测试。

还要执行当前项目已有：

```text
py_compile
LSP / lint
```

确认：

```text
RAG        PASS
Chat       PASS
Tool       PASS
Multi-Step PASS
Schema     PASS
DB         PASS
```

并确认：

```text
knowledge_document
knowledge_chunk
```

数据没有被修改。

---

# 十八、最终汇报

完成后必须立即停止。

不要自动进入 Phase 3.7.2。

按照下面格式汇报：

```text
【Phase 3.7.1.1 完成】

1. 新增文件：
2. 修改文件：
3. ProjectContext：
4. DataSource：
5. DatabaseMetadataProvider：
6. PostgreSQL Provider：
7. SchemaExplorer 是否保持兼容：
8. 测试结果：
9. 全量回归结果：
10. 数据库是否被污染：
11. 是否修改 RAG：
12. 是否修改 Tool：
13. 是否调用 LLM：
14. 当前架构：
15. 下一阶段建议：
```

**做完就停，不要自行开始下一阶段。**
