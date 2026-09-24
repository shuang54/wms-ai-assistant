你现在开始执行项目 **Phase 3.7.7：Read-only SQL Executor**。

## 一、严格执行范围

本阶段只实现：

> **Validated SQL → Read-only SQL Executor → Query Result**

目标是让已经通过 Phase 3.7.5 `SQLValidator` 的 SQL 能够安全地在 PostgreSQL 中执行，并返回结构化查询结果。

### 本阶段禁止实现

不要实现以下任何内容：

* AI Router
* Agent
* LangGraph
* API Endpoint
* Chat 改造
* RAG + Text-to-SQL Hybrid
* Tool + Text-to-SQL Hybrid
* SQL 自动修复
* INSERT / UPDATE / DELETE
* DDL
* 数据写入
* 自动建表
* 自动修改数据库
* 自然语言结果生成
* 图表生成
* Phase 3.7.8 及之后的内容

**完成本阶段后必须停止并输出报告，不得自行进入下一阶段。**

---

# 二、开始前必须阅读

先阅读：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`
4. `docs/decisions/Phase 3.7.1 — ADR.md`
5. `docs/decisions/Phase 3.7.1.1 — ADR.md`
6. `docs/decisions/Phase 3.7.2 — ADR.md`
7. `docs/decisions/Phase 3.7.3 — ADR.md`
8. `docs/decisions/Phase 3.7.4 — ADR.md`
9. `docs/decisions/Phase 3.7.5 — ADR.md`
10. `docs/decisions/Phase 3.7.6 — ADR.md`

然后阅读当前实现：

* `backend/app/services/sql_validator_service.py`
* `backend/app/services/text_to_sql_service.py`
* `backend/app/projects/context.py`
* `backend/app/config.py`
* 当前数据库 session / engine 实现
* 现有 DB 测试 fixtures
* 现有异常处理模式
* 现有 service / DTO 编码风格

先理解现有架构，再修改代码。

---

# 三、核心架构

本阶段形成：

```text
Validated SQL
      │
      ↓
SQLExecutor
      │
      ├── Re-validate
      │
      ├── Read-only transaction
      │
      ├── Statement timeout
      │
      ├── Row limit protection
      │
      ↓
PostgreSQL
      │
      ↓
SQLExecutionResult
```

注意：

> SQLValidator 是第一道安全边界，SQLExecutor 是第二道运行时安全边界。

不能因为 SQL 已经经过 Validator，就完全信任执行环境。

---

# 四、建议新增文件

优先按照项目现有结构新增：

```text
backend/app/services/sql_executor_service.py

tests/test_sql_executor_service.py

docs/decisions/Phase 3.7.7 — ADR.md
```

如果项目已有更合适的 DB execution abstraction，可以复用，不要重复造轮子。

如果现有目录结构不同，以项目实际结构为准。

---

# 五、SQLExecutor Protocol

定义 Protocol，例如：

```python
class SQLExecutor(Protocol):
    async def execute(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> SQLExecutionResult:
        ...
```

具体签名可以根据现有项目风格调整。

核心要求：

* 上层依赖 Protocol
* 不暴露 SQLAlchemy Engine / Session
* 不暴露 PostgreSQL connection
* Executor 内部负责数据库执行
* 支持 Dependency Injection
* 测试可以注入 Fake DB / Fake executor

---

# 六、SQLExecutionResult

定义 frozen DTO。

建议至少包含：

```text
columns
rows
row_count
truncated
execution_time_ms
```

例如：

```python
@dataclass(frozen=True)
class SQLExecutionResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    row_count: int
    truncated: bool
    execution_time_ms: float
```

可以根据项目现有 DTO 风格调整。

要求：

* immutable
* 不返回 SQLAlchemy Row 对象
* 不返回 Cursor
* 不返回 Connection
* 不返回 Session
* 结果必须是普通 Python 数据结构

---

# 七、输入校验

Executor 必须拒绝明显非法输入。

至少处理：

* 空 SQL
* 非字符串 SQL
* 非法 `max_rows`
* 非法 timeout
* 空 allowed_tables 等异常参数

不要复制 SQLValidator 的 AST 逻辑。

---

# 八、执行前必须再次调用 SQLValidator

这是本阶段非常重要的一点。

即使调用方声称：

```text
这是已经 validated 的 SQL
```

Executor 也不能盲目信任。

执行前必须：

```text
SQL
 ↓
SQLValidator.validate(...)
 ↓
valid?
 ├─ no → 拒绝执行
 └─ yes
       ↓
     execute
```

必须使用：

```python
validator.validate(
    sql,
    schema=schema,
    allowed_tables=allowed_tables,
    max_rows=max_rows,
)
```

如果 Validator 返回 invalid：

* 不得执行数据库
* 返回/抛出明确的执行异常
* 携带 Validator error codes
* 不暴露底层数据库连接信息

---

# 九、不要修改 Phase 3.7.5 Validator

除非发现明确 bug，否则：

```text
sql_validator_service.py
```

本阶段不得修改。

Validator 已经是 Phase 3.7.5 的稳定安全组件。

---

# 十、Read-only Database Execution

数据库执行必须采用只读方式。

优先使用 PostgreSQL transaction：

```sql
BEGIN READ ONLY;
```

然后执行查询。

最后：

```sql
ROLLBACK;
```

或者使用等价的 SQLAlchemy read-only transaction 机制。

核心目标：

> 即使未来 Validator 出现漏洞，Executor 的数据库权限仍然不能写入。

---

# 十一、数据库账号权限

如果当前 Docker PostgreSQL 测试环境可以安全地创建专用只读账号，可以增加测试 fixture。

例如：

```text
wms_ai_readonly
```

原则：

```text
SELECT
  ✓

INSERT
  ✗

UPDATE
  ✗

DELETE
  ✗

TRUNCATE
  ✗

CREATE
  ✗

ALTER
  ✗

DROP
  ✗
```

但不要为了这个阶段大幅修改项目数据库初始化结构。

如果当前环境不适合创建独立 read-only role：

* 不要强行改生产配置
* Executor 至少使用 PostgreSQL `READ ONLY` transaction
* 在 ADR 中记录“专用只读 DB 用户作为部署层 defense-in-depth”

---

# 十二、Statement Timeout

必须考虑 SQL 执行时间。

建议默认：

```text
timeout_seconds = 10
```

具体默认值可以结合现有项目配置调整。

要求：

* timeout 必须可配置
* 必须有合理上下限
* 禁止无限等待
* 超时后取消/终止当前查询
* 不得影响后续连接池连接

PostgreSQL 可以考虑：

```sql
SET LOCAL statement_timeout = ...
```

优先使用 transaction-local 设置，不要污染连接池中的后续请求。

---

# 十三、Row Limit

Executor 必须有结果数量保护。

即使 Validator 要求：

```sql
LIMIT <= 1000
```

Executor 仍然应该保护：

```text
max_rows
```

执行后：

```text
最多向上层返回 max_rows 条
```

建议：

```text
SQL LIMIT
      ↓
Validator 限制
      ↓
Executor result cap
```

两层都存在。

不要简单依赖：

```sql
LIMIT 1000
```

作为唯一保护。

如果实际返回：

```text
max_rows + 1
```

可以用来判断：

```text
truncated = true
```

但不要为了实现这个机制而破坏 SQL Validator 的语义。

---

# 十四、结果大小保护

除了 row count，还需要考虑单行/整体结果过大。

至少避免：

* 单个字段无限大
* 超大 TEXT
* 超大 JSON
* 大型 BYTEA
* 返回几十 MB / 几百 MB 数据

本阶段不需要实现复杂的完整流式系统。

可以采用一个简单、明确、可配置的结果大小保护。

例如：

```text
MAX_RESULT_BYTES
```

默认值可以根据项目实际情况合理设置。

如果实现复杂度过高：

* 保留 row limit
* 不引入复杂 streaming
* 在 ADR 中明确记录后续优化点

不要过度设计。

---

# 十五、数据库异常处理

不要把原始数据库异常直接暴露给上层。

建立 Executor 异常体系，例如：

```text
SQLExecutorError
├── SQLExecutorInputError
├── SQLExecutorValidationError
├── SQLExecutorTimeoutError
├── SQLExecutorDatabaseError
└── SQLExecutorResultError
```

具体命名可遵循现有项目异常风格。

要求：

* Validator 错误 → 明确区分
* timeout → 明确区分
* DB connection error → 明确区分
* SQL execution error → 明确区分
* 不泄露密码
* 不泄露 DATABASE_URL
* 不泄露连接字符串
* 不泄露内部 credentials

---

# 十六、SQLAlchemy / PostgreSQL 执行方式

复用项目现有：

```text
DatabaseSettings
db.session
engine
```

不要重新创建：

```text
DATABASE_URL
create_engine()
```

如果现有项目已经提供统一 DB engine/session：

> 必须复用。

不要重复建立数据库基础设施。

---

# 十七、不要把 DB 对象泄露给 AI 层

最终：

```text
TextToSQLService
        ↓
SQLExecutor
        ↓
SQLExecutionResult
```

而不是：

```text
TextToSQLService
        ↓
Engine
Session
Connection
Cursor
```

AI/Application 层只能拿到：

```text
columns
rows
row_count
truncated
execution_time_ms
```

---

# 十八、不要执行任意 SQL

即使有人直接调用：

```python
executor.execute(
    "DELETE FROM knowledge_document ..."
)
```

也必须：

```text
Validator
 ↓
reject
 ↓
NO DATABASE EXECUTION
```

测试必须覆盖：

```sql
DELETE
UPDATE
INSERT
DROP
ALTER
TRUNCATE
CREATE
GRANT
COPY
multi-statement
SELECT INTO
dangerous functions
```

以及：

```sql
SELECT * FROM unauthorized_table LIMIT 10;
```

---

# 十九、TOCTOU 防护

执行前再次 Validator 校验不仅是形式要求。

考虑：

```text
第一次验证
   ↓
SQL 被修改
   ↓
执行
```

因此：

> Executor 必须验证“最终实际执行的 SQL 字符串”。

不要验证一个 SQL，然后执行另一个 SQL。

建议：

```python
validation = validator.validate(sql, ...)
if not validation.valid:
    raise ...

execute(sql)
```

最终执行字符串必须就是经过验证的 `sql`。

---

# 二十、不要自动修改 SQL

Executor 不负责：

```text
自动加 LIMIT
自动修改 SQL
自动删除危险语句
自动修复 SQL
自动拼接用户条件
```

这些属于其他层。

Executor：

> validate → execute → return result

---

# 二十一、测试要求

新增：

```text
tests/test_sql_executor_service.py
```

至少覆盖以下场景。

### A. 基础成功

```sql
SELECT 1 AS value LIMIT 1;
```

验证：

```text
columns
rows
row_count
execution_time_ms
```

---

### B. 多行查询

查询测试表。

验证：

```text
row_count
rows
columns
```

---

### C. Validator 拒绝

例如：

```sql
DELETE FROM knowledge_document WHERE id = 1;
```

要求：

```text
Validator rejected
Database execute count = 0
```

---

### D. 越权表

```sql
SELECT * FROM unauthorized_table LIMIT 10;
```

要求：

```text
不执行
```

---

### E. 超时

构造一个可控的 PostgreSQL 慢查询。

如果当前环境适合：

```sql
SELECT pg_sleep(...)
```

Validator 本身会拒绝危险函数，因此如果需要测试 Executor timeout：

* 可以通过 Fake Validator 放行受控 SQL
* 或使用安全、确定性的慢查询测试

不要为了测试 timeout 引入危险生产 SQL。

---

### F. max_rows

验证：

```text
返回结果不会超过 max_rows
```

并正确设置：

```text
truncated
```

---

### G. Read-only

尝试：

```sql
CREATE TABLE ...
INSERT ...
UPDATE ...
DELETE ...
DROP TABLE ...
```

验证：

```text
数据库数据没有变化
```

---

### H. 数据库异常

模拟：

```text
connection error
query error
timeout
```

确保转换为项目自己的异常。

---

### I. SQLAlchemy 对象泄漏

断言：

```text
result.rows
result.columns
```

里面没有：

```text
Row
Cursor
Connection
Session
```

---

### J. Prompt / AI 层隔离

确认 Executor：

* 不依赖 LLM
* 不调用 DeepSeek
* 不调用 Embedding
* 不调用 RAG
* 不调用 Tool
* 不调用 Router

---

# 二十二、Fake / DI

单元测试优先使用 Fake。

例如：

```text
FakeValidator
FakeEngine
FakeConnection
```

但不要为了测试而把生产代码设计得非常复杂。

真实 PostgreSQL 集成测试负责验证：

```text
真实 Executor
      ↓
真实 PostgreSQL
```

---

# 二十三、数据库测试

执行：

```powershell
$env:RUN_DB_TESTS="1"
pytest -q
```

必须确认：

```text
DB writes = 0
```

如果测试确实创建临时对象，测试结束后必须清理。

优先使用：

```text
transaction rollback
```

不要污染已有知识库数据。

---

# 二十四、配置

如果需要配置，优先挂到现有 Settings。

例如：

```text
SQL_EXECUTOR_TIMEOUT_SECONDS
SQL_EXECUTOR_MAX_ROWS
SQL_EXECUTOR_MAX_RESULT_BYTES
```

默认值要合理。

不要创建新的 Config 类。

不要把密码写入代码。

不要输出：

```text
DATABASE_URL
DB_PASSWORD
API_KEY
```

---

# 二十五、ADR

新增：

```text
docs/decisions/Phase 3.7.7 — ADR.md
```

至少说明：

### Context

为什么需要 Executor。

### Decision

为什么：

```text
Validator
+
Read-only transaction
+
Statement timeout
+
Result limits
```

### Security

明确：

```text
Prompt = soft constraint
Validator = SQL safety boundary
Executor = runtime defense-in-depth
Database read-only account = deployment defense-in-depth
```

### Responsibilities

```text
TextToSQLGenerator
    负责生成 SQL

SQLValidator
    负责静态验证

SQLExecutor
    负责安全执行

AI Router
    未来负责决定是否走 Text-to-SQL
```

---

# 二十六、静态安全检查

继续保持之前的安全风格。

可以检查：

* Executor 没有 LLM client
* Executor 没有 API key
* Executor 没有密码
* Executor 没有任意 HTTP 请求
* Executor 没有 write SQL API
* Executor 没有暴露 Engine/Session 给结果 DTO
* Executor 没有 `eval`
* Executor 没有 `exec`
* 没有 `subprocess`
* 没有把用户输入直接拼进数据库连接配置

---

# 二十七、回归测试

完成后必须运行：

```powershell
pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
pytest -q
```

再运行：

```powershell
python -m compileall backend
```

如果项目已有 lint / type check，也运行现有检查。

---

# 二十八、必须确认

最终报告必须明确回答：

1. 新增了哪些文件？
2. 修改了哪些文件？
3. SQLExecutor Protocol 是什么？
4. SQLExecutionResult 包含哪些字段？
5. 是否执行前重新调用 SQLValidator？
6. 是否允许任何写操作？
7. 是否使用 READ ONLY transaction？
8. Statement timeout 如何实现？
9. max_rows 如何保护？
10. 是否有 result size protection？
11. DB 异常如何处理？
12. 是否泄露 DB credentials？
13. 是否修改了 Phase 3.7.5 Validator？
14. 是否执行了真实 PostgreSQL 查询？
15. DB writes 是否为 0？
16. 单元测试数量？
17. DB 测试数量？
18. `pytest -q` 结果？
19. `RUN_DB_TESTS=1 pytest -q` 结果？
20. compile / lint 是否通过？
21. LLM 调用次数？
22. 是否修改 API / Router / Agent？
23. 当前 Text-to-SQL → Executor 链路是否闭环？

最终输出：

```text
Phase 3.7.7 COMPLETE
```

或者：

```text
Phase 3.7.7 BLOCKED
```

如果 BLOCKED，必须说明具体阻塞原因。

**完成报告后立即停止。不要进入 Phase 3.7.8。**
