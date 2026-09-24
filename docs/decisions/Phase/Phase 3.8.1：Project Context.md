你现在只执行 **Phase 3.8.1：Project Context 真正的数据源切换**。

## 一、目标

解决当前项目的一个架构限制：

> `project_id` 当前可以进入 ProjectContext，但实际 Schema / Text-to-SQL / SQL Executor / Tool 使用的数据库并没有真正随 project_id 切换。

本阶段要实现：

```text
project_id
    ↓
ProjectContext
    ↓
DataSource
    ↓
对应 Database Engine
    ↓
Schema Explorer / Text-to-SQL / SQL Executor
```

最终达到：

```text
项目 A
  ↓
数据库 A

项目 B
  ↓
数据库 B
```

同一套：

* AI Router
* AI Orchestrator
* RAG
* Text-to-SQL
* SQL Validator
* SQL Executor
* Tool Framework

继续复用。

---

# 二、严格范围

本阶段只处理：

**ProjectContext → DataSource → Database Engine 的实际切换。**

禁止：

1. 不实现 Agent
2. 不引入 LangGraph
3. 不做 Multi-Agent
4. 不做 Memory
5. 不做权限系统
6. 不做 RBAC
7. 不做租户系统
8. 不做 Streaming
9. 不做前端
10. 不修改 RAG 核心逻辑
11. 不修改 Reranker
12. 不修改 AI Router 路由规则
13. 不修改 SQL Validator 安全规则
14. 不修改 Tool Framework 的业务能力
15. 不增加数据库迁移
16. 不复制 AI Core
17. 不修改现有 API 协议，除非为了传递 project_id 必须做最小修改

如果发现需要扩大范围：

**立即停止并报告，不要自行继续。**

---

# 三、先阅读现有代码

先不要修改代码。

重点阅读：

```text
backend/app/projects/models.py
backend/app/projects/context.py
backend/app/config.py

backend/app/db/
backend/app/services/schema_explorer_service.py
backend/app/services/schema_serializer_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/sql_executor_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py

tests/
docs/decisions/
```

重点确认：

1. `ProjectContext`
2. `DataSource`
3. `DatabaseSettings`
4. `db.session.get_engine()`
5. 当前默认 project context 如何创建
6. `/api/ai/chat` 如何接收 project_id
7. Orchestrator 如何获得 ProjectContext
8. Text-to-SQL 使用哪个 engine
9. SQL Executor 使用哪个 engine
10. Tool 如何获得数据库
11. 是否存在全局 singleton engine
12. engine 是否可以安全缓存
13. 当前测试如何构造数据库

**阅读完成后先汇报现状，不要直接开始大改。**

---

# 四、设计目标

建议最终形成：

```text
ProjectRegistry
      ↓
project_id
      ↓
ProjectContext
      ↓
DataSource
      ↓
DatabaseSettings
      ↓
DatabaseEngineProvider
      ↓
Engine
```

其中：

```text
ProjectContext
```

只描述项目。

```text
DataSource
```

描述数据源。

```text
DatabaseEngineProvider
```

负责根据 DataSource 获取对应 Engine。

---

# 五、禁止把数据库密码放进 ProjectContext

ProjectContext 不能包含：

```text
password
api_key
token
secret
connection_string
```

仍然遵守现有安全设计。

建议：

```python
ProjectContext(
    project_id="vietnam-wms",
    project_name="Vietnam WMS",
    data_source=DataSource(...)
)
```

DataSource 也不要直接保存明文密码。

---

# 六、Project Registry

增加一个最小的项目注册机制。

例如：

```python
class ProjectRegistry(Protocol):
    def get(self, project_id: str) -> ProjectContext:
        ...
```

可以先实现：

```python
InMemoryProjectRegistry
```

例如：

```text
vietnam-wms
another-warehouse
```

但是不要创建真实第二套业务数据库。

测试可以使用：

```text
project-a → test database
project-b → another test database/schema
```

如果当前测试环境只有一个 PostgreSQL 实例，可以使用不同 schema 验证数据源切换。

---

# 七、DataSource → Engine

增加一个最小的：

```python
DatabaseEngineProvider
```

职责：

```text
DataSource
    ↓
Engine
```

要求：

* 根据数据源标识获取对应数据库连接
* 不把密码放到业务 DTO
* 不允许用户通过 HTTP 参数直接传 connection string
* 不允许用户直接指定 host / port / password
* project_id 只能选择服务器端已注册的数据源

---

# 八、安全要求

绝对禁止：

```text
POST /api/ai/chat
{
    "project_id": "...",
    "database_url": "..."
}
```

用户只能提交：

```json
{
  "project_id": "vietnam-wms",
  "question": "查询物料 10001 当前库存"
}
```

然后服务器内部：

```text
project_id
 ↓
ProjectRegistry
 ↓
DataSource
 ↓
受控 DatabaseEngine
```

---

# 九、Orchestrator

尽量保持：

```python
execute(question, ...)
```

现有核心 API 不要大改。

如果 API 层已经获得：

```text
project_id
```

可以通过：

```text
ProjectContextProvider
```

解析：

```text
project_id → ProjectContext
```

然后让需要数据库的组件使用该 ProjectContext 对应的数据源。

不要让 Orchestrator 自己创建数据库连接。

---

# 十、Text-to-SQL

必须支持：

```text
project A
 ↓
Schema A
 ↓
SQL A
 ↓
Executor A
```

以及：

```text
project B
 ↓
Schema B
 ↓
SQL B
 ↓
Executor B
```

验证：

```text
project_id
```

切换以后：

* Schema Explorer 查询的是对应数据库
* RelevantTableSelector 使用对应 Schema
* DatabaseContextComposer 使用对应 Schema
* TextToSQL 使用对应上下文
* SQLValidator 使用对应 Schema
* SQLExecutor 使用对应 Engine

---

# 十一、SQL Executor

这是本阶段最重要的验证点之一。

不能出现：

```text
Project B
 ↓
Schema B
 ↓
SQL B
 ↓
Executor A
```

必须保证：

```text
ProjectContext
 ↓
DataSource
 ├── Schema
 └── Executor
```

使用的是同一个项目数据源。

继续保留现有：

* SQL Validator
* READ ONLY
* statement timeout
* max rows
* result size protection
* re-validation

本阶段不要修改这些安全机制。

---

# 十二、Tool

暂时只验证现有 `get_inventory`。

要求：

```text
project_id
 ↓
ProjectContext
 ↓
get_inventory
 ↓
对应 DataSource
```

不要把 Tool 改造成复杂的动态工具系统。

只需要证明：

```text
Project A → 查询 A 数据
Project B → 查询 B 数据
```

---

# 十三、RAG

本阶段：

**不要把 RAG 数据库强制拆成多个项目库。**

保持现有 RAG 正常工作即可。

如果当前 RAG 使用独立知识库：

```text
RAG
 ↓
Knowledge Store
```

保持现状。

不要为了 Project Context 强行重构 RAG。

---

# 十四、测试设计

至少增加以下测试。

## 14.1 Project Registry

验证：

```text
known project → ProjectContext
unknown project → clear error
empty project_id → clear error
```

---

## 14.2 DataSource isolation

创建两个测试数据源：

```text
project-a
project-b
```

数据不同：

```text
project-a:
material 10001 → qty 100

project-b:
material 10001 → qty 999
```

然后分别执行：

```text
project-a → 100

project-b → 999
```

证明真正发生了数据源切换。

---

# 十五、Text-to-SQL E2E

必须测试：

```text
project-a
 ↓
Schema A
 ↓
Text-to-SQL
 ↓
SQL Validator
 ↓
SQL Executor A
```

以及：

```text
project-b
 ↓
Schema B
 ↓
Text-to-SQL
 ↓
SQL Validator
 ↓
SQL Executor B
```

必须确认结果不同。

---

# 十六、跨项目访问安全

验证：

```text
project-a
```

不能通过：

```text
allowed_tables
SQL
database_url
```

访问 project-b。

尤其测试：

```sql
SELECT * FROM project_b_table LIMIT 10
```

不能绕过 Project A 的 Schema / allowlist。

---

# 十七、API E2E

测试：

```http
POST /api/ai/chat
```

分别：

```json
{
  "project_id": "project-a",
  "question": "查询物料 10001 当前库存"
}
```

和：

```json
{
  "project_id": "project-b",
  "question": "查询物料 10001 当前库存"
}
```

得到不同数据。

同时：

```text
route = tool
```

或者根据测试设计进入：

```text
TEXT_TO_SQL
```

---

# 十八、配置

如果需要新增环境变量：

只增加最小必要配置。

例如项目注册配置可以先采用代码级测试注册，不要为了 MVP 引入复杂 YAML/数据库配置中心。

生产环境的真实多数据库连接配置：

**本阶段只设计接口，不要求接入真实第二个生产数据库。**

---

# 十九、兼容性

必须保证旧行为：

```text
project_id 未提供
```

仍然使用：

```text
vietnam-wms
```

默认 ProjectContext。

现有测试不能因为增加 ProjectRegistry 而大面积修改。

运行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

如果相关环境支持，再执行真实 API E2E。

最后：

```bash
python -m compileall backend
```

并检查 LSP / lint。

---

# 二十、文档

新增：

```text
docs/decisions/Phase/Phase 3.8.1：Project Context 数据源切换.md
```

记录：

1. 当前问题
2. ProjectRegistry
3. DataSource
4. DatabaseEngineProvider
5. project_id → DataSource → Engine
6. 安全边界
7. Tool 数据源切换
8. Text-to-SQL 数据源切换
9. 测试结果
10. 已知限制

---

# 二十一、验收标准

最终必须证明：

```text
project-a
    ↓
database-a
```

和：

```text
project-b
    ↓
database-b
```

确实使用不同数据源。

至少通过：

```text
Project Registry Test
DataSource Isolation Test
Tool E2E
Text-to-SQL E2E
Cross-project Security Test
API E2E
```

并且：

```text
pytest -q
```

全部通过。

如果 DB 测试可用：

```text
RUN_DB_TESTS=1 pytest -q
```

全部通过。

---

# 二十二、完成后停止

完成 Phase 3.8.1 后：

**立即停止。**

不要继续：

* 3.8.2
* Agent
* LangGraph
* Memory
* 权限
* Multi-Agent
* MCP
* Streaming

最终只报告：

```text
1. 修改文件
2. 新增文件
3. ProjectRegistry 如何实现
4. DataSource → Engine 如何实现
5. project-a / project-b 是否真正切换数据源
6. Tool 是否支持项目切换
7. Text-to-SQL 是否支持项目切换
8. 跨项目访问测试结果
9. pytest 结果
10. DB pytest 结果
11. API E2E 结果
12. 已知限制
```

**报告完成后停止，等待下一步指令。**
