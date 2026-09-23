你现在只执行 **Phase 3.7.5：SQL Validator（只读 SQL 安全校验）**。

这是一个严格限定范围的开发阶段。

**完成本阶段后必须停止，不得自动进入 Phase 3.7.6。**

---

# 一、背景

当前已经完成：

```text
Phase 3.7.1
SchemaExplorerService
        ↓
DatabaseSchema

Phase 3.7.1.1
ProjectContext / DataSource / Provider

Phase 3.7.2
SchemaSerializer

Phase 3.7.3
ProjectSemantic
SemanticSchemaValidator
BusinessSemanticSerializer
DatabaseContextComposer

Phase 3.7.4
Question
    ↓
RelevantTableSelector
    ↓
Selected Tables
```

现在 Text-to-SQL 还缺少一个非常重要的安全边界：

```text
未来：
LLM
 ↓
SQL
 ↓
??? 
 ↓
Database
```

不能允许 LLM 生成任意 SQL 后直接执行。

因此本阶段建立：

```text
SQL
 ↓
SQL Validator
 ↓
安全 / 合法
 ↓
未来 SQL Executor
```

---

# 二、本阶段核心目标

实现一个**独立、纯内存、只读 SQL Validator**。

核心职责：

```text
验证 SQL 是否允许进入未来的数据库执行层。
```

本阶段只负责：

```text
SQL string
+
DatabaseSchema
+
允许的 tables
        ↓
ValidationResult
```

---

# 三、严格禁止

本阶段禁止：

* ❌ LLM 调用
* ❌ DeepSeek
* ❌ Embedding
* ❌ Reranker
* ❌ RAG
* ❌ Chat 修改
* ❌ Tool 修改
* ❌ SQL 执行
* ❌ 数据库连接
* ❌ SQLAlchemy Session
* ❌ PostgreSQL 查询
* ❌ Text-to-SQL
* ❌ SQL 自动生成
* ❌ 自动修复 SQL
* ❌ 自动改写 SQL
* ❌ 自动增加业务条件
* ❌ 自动选择表
* ❌ 修改 DatabaseSchema
* ❌ 修改 ProjectSemantic

本阶段：

```text
SQL execution = 0
DB writes = 0
LLM calls = 0
```

---

# 四、先阅读

开始编码前必须阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md

docs/decisions/Phase 3.7.1 — ADR.md
docs/decisions/Phase 3.7.1.1 — ADR.md
docs/decisions/Phase 3.7.2 — ADR.md
docs/decisions/Phase 3.7.3 — ADR.md
docs/decisions/Phase 3.7.4 — ADR.md

backend/app/projects/
backend/app/services/schema_explorer_service.py
backend/app/services/schema_serializer_service.py
backend/app/services/semantic_schema_validator.py
backend/app/services/relevant_table_selector.py
```

同时检查：

```text
requirements.txt
pyproject.toml
```

确认当前项目是否已经存在 SQL parser 依赖。

---

# 五、SQL Parser 选择

不要自己使用大量正则解析 SQL。

优先检查项目是否已经有 SQL parser。

如果没有：

> 可以引入一个成熟、纯 Python 的 SQL parser 依赖，例如 `sqlglot`。

如果决定新增依赖：

1. 修改 `requirements.txt`
2. 固定合理版本范围
3. 在 ADR 中说明为什么使用
4. 测试 parser 行为

不要为了本阶段自行实现完整 SQL grammar。

---

# 六、核心接口

建议新增：

```text
backend/app/services/sql_validator_service.py
```

核心接口可以设计为：

```python
class SQLValidator(Protocol):
    def validate(
        self,
        sql: str,
        *,
        schema: DatabaseSchema | None = None,
        allowed_tables: Sequence[str] | None = None,
        max_rows: int = 1000,
    ) -> SQLValidationResult:
        ...
```

具体命名可以根据当前项目风格调整。

重点：

> 上层依赖 Validator 接口，不依赖具体 parser。

---

# 七、Validation Result

建议使用 frozen dataclass。

例如：

```python
@dataclass(frozen=True)
class SQLValidationResult:
    valid: bool
    normalized_sql: str | None
    errors: tuple[SQLValidationError, ...]
    referenced_tables: tuple[str, ...]
```

具体字段可以根据项目风格调整。

注意：

### 不要执行 SQL。

### 不要修改原 SQL。

如果 parser 可以产生 normalized SQL，可以作为**展示/后续使用信息**返回。

但本阶段不要进行业务改写。

---

# 八、错误体系

建议建立清晰异常 / error code。

至少能够区分：

```text
INVALID_SQL
EMPTY_SQL
MULTI_STATEMENT
NON_READ_ONLY
DANGEROUS_OPERATION
TABLE_NOT_ALLOWED
UNKNOWN_TABLE
ROW_LIMIT_EXCEEDED
UNSUPPORTED_SQL
```

具体实现方式根据现有项目异常风格决定。

错误信息必须能够告诉调用方：

```text
发生了什么
```

例如：

```text
SQL contains forbidden statement: DELETE
```

或者：

```text
Table public.inventory is not in the allowed table set
```

不要返回：

```text
validation failed
```

这种无法定位问题的信息。

---

# 九、第一条安全规则：只允许 SELECT

本阶段默认只允许：

```sql
SELECT ...
```

允许常见：

```sql
SELECT
SELECT ... FROM ...
SELECT ... JOIN ...
SELECT ... WHERE ...
SELECT ... GROUP BY ...
SELECT ... ORDER BY ...
SELECT ... LIMIT ...
```

是否允许 CTE：

```sql
WITH ...
SELECT ...
```

建议：

**允许只读 CTE。**

但是必须确认最终语句仍然是只读查询。

---

# 十、明确禁止写操作

必须拒绝：

```sql
INSERT
UPDATE
DELETE
MERGE
UPSERT
```

以及：

```sql
CREATE
ALTER
DROP
TRUNCATE
RENAME
```

以及：

```sql
GRANT
REVOKE
```

以及数据库事务控制：

```sql
COMMIT
ROLLBACK
BEGIN
START TRANSACTION
```

以及 PostgreSQL 可能用于执行副作用的操作。

如果 parser 能识别 AST，优先根据 AST 判断。

不要只靠：

```python
sql.upper().startswith("SELECT")
```

因为：

```sql
SELECT ...; DELETE ...
```

不能通过。

---

# 十一、多语句必须禁止

以下全部拒绝：

```sql
SELECT * FROM a;
SELECT * FROM b;
```

以及：

```sql
SELECT ...;
DELETE ...
```

要求：

```text
只能存在一个 SQL statement
```

即使多个 statement 全部是 SELECT，也暂时拒绝。

原因：

> MVP 安全边界简单明确。

---

# 十二、SELECT INTO 等特殊语法

必须考虑：

```sql
SELECT ... INTO ...
```

以及可能产生写入 / 创建对象效果的语法。

原则：

> 只允许真正的只读查询。

如果 parser 无法可靠判断：

```text
宁可拒绝
```

不要放行。

---

# 十三、函数调用

需要考虑 SQL 函数：

```sql
SELECT now();
SELECT count(*);
SELECT upper(name);
```

普通纯查询函数可以允许。

但对于可能产生副作用的函数：

```sql
pg_sleep(...)
```

以及写文件、执行命令、修改系统状态等危险能力：

```text
默认拒绝。
```

不要尝试维护一个巨大的 PostgreSQL 函数白名单。

MVP 可以：

1. 明确阻止已知危险函数
2. 对未知/不可安全判断的函数采取保守策略

在 ADR 中说明。

至少测试：

```sql
SELECT pg_sleep(10);
```

不能被认为是普通安全查询。

---

# 十四、允许的表白名单

这是本阶段第二个核心能力。

Validator 应支持：

```python
allowed_tables
```

例如：

```text
allowed_tables = (
    "public.inventory",
    "public.material",
)
```

SQL：

```sql
SELECT * FROM public.inventory;
```

允许。

SQL：

```sql
SELECT * FROM public.customer;
```

拒绝。

---

# 十五、表名必须从 AST 获取

不要简单用：

```python
if "inventory" in sql:
```

判断。

必须使用 parser 的结构化 AST。

例如：

```text
SELECT ...
FROM public.inventory i
JOIN public.material m ...
```

应该提取：

```text
public.inventory
public.material
```

然后逐个验证。

---

# 十六、Schema.table 标准化

统一使用：

```text
schema.table
```

例如：

```text
public.inventory
```

不要允许通过大小写绕过：

```text
PUBLIC.INVENTORY
Public.Inventory
```

匹配时应标准化大小写。

但：

> 输出 referenced_tables 时保持稳定的 canonical 表名。

---

# 十七、裸表名

对于：

```sql
SELECT * FROM inventory;
```

需要根据：

```text
DatabaseSchema
```

解析。

如果当前默认 schema 是：

```text
public
```

可以解析：

```text
inventory
→ public.inventory
```

但如果存在多个 schema：

```text
a.inventory
b.inventory
```

而 SQL 使用：

```sql
FROM inventory
```

则：

```text
拒绝
```

不要猜。

---

# 十八、Schema 事实约束

如果传入：

```text
schema: DatabaseSchema
```

那么 SQL 引用的每张表必须存在于：

```text
DatabaseSchema.tables
```

否则：

```text
UNKNOWN_TABLE
```

例如：

```sql
SELECT * FROM public.not_exists;
```

必须拒绝。

---

# 十九、allowed_tables 与 DatabaseSchema

如果两者同时提供：

```text
DatabaseSchema
+
allowed_tables
```

必须同时满足：

```text
SQL table exists in DatabaseSchema
AND
SQL table is in allowed_tables
```

即：

```text
DatabaseSchema
    ∩
allowed_tables
```

才是最终可查询集合。

---

# 二十、没有 allowed_tables 的行为

需要明确：

```python
allowed_tables=None
```

表示：

> 不额外限制表白名单，但仍必须通过只读 SQL 校验。

如果同时：

```text
schema != None
```

仍然不能引用 Schema 中不存在的表。

即：

```text
schema=None
allowed_tables=None
```

只验证 SQL 安全性。

而：

```text
schema=DatabaseSchema
allowed_tables=None
```

验证：

```text
安全性 + 表存在性
```

---

# 二十一、LIMIT / 行数保护

本阶段需要增加基础结果规模保护。

默认：

```text
max_rows=1000
```

对于：

```sql
SELECT * FROM inventory;
```

未来执行时可能返回百万行。

因此 Validator 应确保查询存在合理限制。

但注意：

> Validator 不执行 SQL。

建议 MVP 规则：

### 如果 SELECT 没有 LIMIT

自动判断为：

```text
需要 LIMIT
```

但是：

**不要自动修改 SQL。**

建议返回：

```text
ROW_LIMIT_REQUIRED
```

让后续 SQL Generation / Application 层决定如何重新生成。

---

# 二十二、已有 LIMIT

例如：

```sql
SELECT * FROM inventory LIMIT 100;
```

允许。

如果：

```sql
LIMIT 10000
```

而：

```text
max_rows=1000
```

拒绝：

```text
ROW_LIMIT_EXCEEDED
```

---

# 二十三、OFFSET

允许：

```sql
SELECT ...
LIMIT 100 OFFSET 20
```

只要：

```text
LIMIT <= max_rows
```

即可。

如果没有 LIMIT：

```text
仍然拒绝。
```

---

# 二十四、聚合查询

不要错误地认为：

```sql
SELECT count(*) FROM inventory LIMIT 1000;
```

是不安全的。

允许常见聚合：

```sql
COUNT
SUM
AVG
MIN
MAX
GROUP BY
HAVING
```

但仍然必须：

* SELECT-only
* 单 statement
* 表存在
* 表在 allowed_tables 内（如果提供）
* LIMIT 符合要求

---

# 二十五、JOIN

允许：

```sql
SELECT ...
FROM inventory i
JOIN material m
    ON i.material_id = m.id
LIMIT 100;
```

但所有引用表都必须经过：

```text
DatabaseSchema
+
allowed_tables
```

验证。

---

# 二十六、子查询

允许只读子查询：

```sql
SELECT *
FROM (
    SELECT ...
    FROM inventory
    LIMIT 100
) t
LIMIT 100;
```

所有子查询引用的表都必须验证。

---

# 二十七、CTE

允许只读 CTE：

```sql
WITH inventory_data AS (
    SELECT *
    FROM public.inventory
    LIMIT 100
)
SELECT *
FROM inventory_data
LIMIT 100;
```

但：

```sql
WITH x AS (
    DELETE FROM inventory ...
)
SELECT ...
```

必须拒绝。

---

# 二十八、注释 / SQL Injection 风险

SQL Validator 的目标不是传统 Web SQL injection 防护，因为 SQL 本身就是待执行查询。

但必须正确处理：

```sql
SELECT * FROM inventory -- comment
```

以及：

```sql
SELECT * FROM inventory /* comment */
LIMIT 100;
```

parser 应该负责解析。

同时测试：

```sql
SELECT * FROM inventory; DROP TABLE inventory;
```

必须拒绝。

---

# 二十九、禁止字符串搜索式安全检查

不要把整个 Validator 建立在：

```python
"DELETE" in sql.upper()
```

这种方式上。

例如：

```sql
SELECT 'DELETE' AS message
FROM inventory
LIMIT 1;
```

不应该因为字符串里出现 DELETE 就被误判。

应该：

```text
SQL Parser
    ↓
AST
    ↓
Statement Type
    ↓
安全检查
```

---

# 三十、SQL Parser 异常

非法 SQL：

```sql
SELECT FROM
```

应该得到：

```text
INVALID_SQL
```

而不是：

```text
500 Internal Server Error
```

Validator 自己负责把 parser exception 转换成项目异常 / validation result。

---

# 三十一、不要自动修复

例如输入：

```sql
SELECT * FROM inventory
```

缺 LIMIT。

不要自动变成：

```sql
SELECT * FROM inventory LIMIT 1000
```

本阶段只：

```text
拒绝 + 原因
```

未来 Text-to-SQL 层负责重新生成。

这样职责更加清晰：

```text
Generator
    ↓
生成 SQL

Validator
    ↓
判断 SQL 是否允许

Executor
    ↓
执行 SQL
```

---

# 三十二、Security Boundary

最终希望得到：

```text
LLM generated SQL
        ↓
SQLValidator
        ↓
┌─────────────────────┐
│ SELECT-only         │
│ single statement    │
│ known tables        │
│ allowed tables      │
│ LIMIT <= max_rows   │
│ no dangerous funcs  │
└─────────────────────┘
        ↓
Future SQLExecutor
```

---

# 三十三、测试要求

新增：

```text
tests/test_sql_validator_service.py
```

至少覆盖以下类别。

## A. 基础

1. 合法 SELECT
2. 空 SQL
3. whitespace SQL
4. 非法 SQL
5. parser error

## B. 写操作

6. INSERT
7. UPDATE
8. DELETE
9. MERGE
10. CREATE
11. ALTER
12. DROP
13. TRUNCATE
14. GRANT
15. REVOKE

## C. 多语句

16. 两个 SELECT
17. SELECT + DELETE
18. SELECT + DROP

## D. SELECT 特殊情况

19. SELECT INTO
20. 只读 CTE
21. 只读子查询
22. 聚合
23. GROUP BY
24. HAVING
25. JOIN

## E. 表验证

26. 已存在表
27. UNKNOWN_TABLE
28. allowed_tables 允许
29. allowed_tables 拒绝
30. schema + allowed_tables 双重限制
31. 裸表名
32. schema.table
33. schema 大小写
34. 裸表名歧义

## F. LIMIT

35. 没有 LIMIT → 拒绝
36. LIMIT=100
37. LIMIT=max_rows
38. LIMIT=max_rows+1
39. OFFSET + LIMIT
40. 非法 LIMIT

## G. Dangerous Functions

41. `pg_sleep`
42. 至少一种已知危险函数
43. 普通 `count`
44. 普通字符串包含 `DELETE` 不误判

## H. SQL comments

45. line comment
46. block comment

## I. Immutability

47. DatabaseSchema 不变
48. allowed_tables 输入不变
49. SQL 原文不变

## J. Determinism

50. 相同 SQL 多次验证结果一致
51. referenced_tables 稳定排序

---

# 三十四、重要安全测试

必须包含这些恶意 / 边界案例：

```sql
SELECT * FROM public.inventory; DROP TABLE public.inventory;
```

```sql
SELECT * FROM public.inventory;
DELETE FROM public.inventory;
```

```sql
SELECT 'DELETE' AS message
FROM public.inventory
LIMIT 1;
```

```sql
SELECT *
FROM public.inventory
LIMIT 10000;
```

```sql
SELECT *
FROM public.not_exists
LIMIT 100;
```

```sql
SELECT *
FROM public.inventory
LIMIT 100;
```

确保只有最后一个在正确 Schema / allowlist 条件下通过。

---

# 三十五、真实 DB Integration

本阶段可以使用真实 PostgreSQL **只读取 metadata**，但不是执行用户 SQL。

验证：

```text
SchemaExplorerService
        ↓
DatabaseSchema
        ↓
SQLValidator
```

例如：

```sql
SELECT id, title
FROM public.knowledge_document
LIMIT 10;
```

应该能够通过。

例如：

```sql
SELECT *
FROM public.not_exists
LIMIT 10;
```

应该拒绝。

**禁止真正执行被验证 SQL。**

---

# 三十六、不要修改数据库

本阶段：

```text
DB writes = 0
```

不能：

```text
INSERT
UPDATE
DELETE
CREATE
ALTER
DROP
TRUNCATE
```

包括测试代码也不能执行这些语句。

---

# 三十七、不要接 API

本阶段不要新增：

```text
/api/sql
/api/query
/api/text-to-sql
```

Validator 先作为内部 Service。

下一阶段再决定 Application API。

---

# 三十八、ADR

新增：

```text
docs/decisions/Phase 3.7.5 — ADR.md
```

至少记录：

### Context

LLM 未来会生成 SQL，但不能直接执行。

### Decision

引入独立：

```text
SQLValidator
```

作为：

```text
LLM → Validator → Executor
```

之间的安全边界。

### Rules

```text
SELECT only
single statement
known tables
allowed tables
LIMIT required
LIMIT <= max_rows
dangerous functions rejected
```

### Important

Validator：

```text
不执行 SQL
不修改 SQL
不生成 SQL
```

### Conservative Principle

无法安全判断：

```text
拒绝
```

而不是：

```text
放行
```

### Future

下一阶段才实现：

```text
Text-to-SQL Generator
```

再下一层：

```text
Read-only SQL Executor
```

---

# 三十九、代码质量

要求：

* 类型完整
* frozen DTO
* Protocol 优先
* 小函数
* AST-based validation
* 无正则 SQL parser
* 无 ORM
* 无 DB Session
* 无全局可变状态
* 清晰异常
* 清晰测试

尤其不要写成一个：

```text
validate()
```

超过几百行的巨型函数。

建议拆成：

```text
_validate_statement
_validate_read_only
_extract_tables
_validate_tables
_validate_limit
_validate_functions
```

具体拆分根据实际代码决定。

---

# 四十、依赖控制

如果新增 `sqlglot`：

检查：

```text
requirements.txt
```

并确保：

```text
Validator
```

不依赖：

```text
SQLAlchemy
PostgreSQL connection
FastAPI
LLM
```

Parser 只负责解析字符串。

---

# 四十一、回归测试

执行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

然后：

```bash
python -m compileall backend
```

要求：

```text
新测试全部 PASS
旧测试全部 PASS
```

不得破坏：

```text
RAG
Chat
Tool
Multi-Step Tool
Schema Explorer
Schema Serializer
Business Semantic
Context Composer
Relevant Table Selector
```

---

# 四十二、最终报告

完成后只汇报：

1. 新增/修改文件
2. 是否新增 SQL parser 依赖
3. Validator Protocol
4. DTO / Error Model
5. SELECT-only 实现
6. Multi-statement 防护
7. Table allowlist
8. DatabaseSchema 校验
9. LIMIT / max_rows
10. Dangerous Function 防护
11. 是否自动修改 SQL
12. 测试数量
13. 全量测试结果
14. DB 是否修改
15. 是否执行任何用户 SQL
16. LLM / Embedding / Reranker 是否调用
17. 当前阶段结论

**报告完成后立即停止。**

不要自动进入 Phase 3.7.6。

尤其不要自行实现：

```text
Text-to-SQL
SQL Generator
SQL Executor
/api/sql
LLM Router
```
