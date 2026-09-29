你现在开始执行：

# Phase 3.12 Step 53 — LLM Usage Index Production Migration Safety Audit

## 一、阶段目标

本阶段只做：

> **审计 Phase 3.12 Step 52 新增的 `llm_usage_record.assistant_request_id` 索引，在生产环境中的创建方式是否安全。**

重点不是继续开发功能。

重点确认：

```text
ix_llm_usage_record_assistant_request_id
        ↓
当前 init_db 自动创建
        ↓
生产环境是否安全？
```

本阶段：

**只 Audit / Design，不执行生产迁移，不修改生产数据库。**

---

# 二、背景

Step 52 已经新增：

```text
ix_llm_usage_record_assistant_request_id
```

索引：

```text
table:
ai_ops.llm_usage_record

column:
assistant_request_id

type:
B-tree

unique:
false

nullable:
true
```

当前 `init_db.py` 使用：

```sql
CREATE INDEX IF NOT EXISTS ix_llm_usage_record_assistant_request_id
ON ai_ops.llm_usage_record (assistant_request_id);
```

Step 52 已经验证：

```text
offline tests PASS
DB tests PASS
idempotency PASS
query behavior unchanged
```

但是：

**尚未验证生产环境执行这个 DDL 的安全性。**

---

# 三、必须先阅读

先阅读真实代码：

```text
backend/app/db/init_db.py
backend/app/db/models/llm_usage_record.py
backend/app/db/session.py
```

如果实际路径不同，以真实代码为准。

继续检查：

```text
backend/app/main.py
backend/app/api/
```

确认：

```text
init_db()
```

到底在哪里被调用。

重点回答：

```text
1. 应用启动时是否自动执行 init_db？
2. 每次启动是否都会执行？
3. 生产环境是否可能执行？
4. init_db 使用 SQLAlchemy transaction / connection 的方式是什么？
5. CREATE INDEX 当前是否在事务块中？
6. 是否存在 migration framework？
7. 部署脚本是否会执行 init_db？
```

继续检查：

```text
Dockerfile
docker-compose*
scripts/
部署脚本
README
docs/
```

寻找：

```text
数据库初始化
应用启动
生产部署
```

不要根据文件名猜。

---

# 四、核心 PostgreSQL 风险审计

必须确认当前：

```sql
CREATE INDEX
```

与：

```sql
CREATE INDEX CONCURRENTLY
```

的区别。

当前普通：

```sql
CREATE INDEX
```

在构建索引期间可能阻塞目标表的写操作。

PostgreSQL 官方文档明确说明：

```text
normal CREATE INDEX
→ blocks INSERT / UPDATE / DELETE
```

而：

```text
CREATE INDEX CONCURRENTLY
```

可以避免阻塞普通写操作，但需要更多工作和时间。

同时必须确认：

```text
CREATE INDEX CONCURRENTLY
```

**不能在 transaction block 中执行。**

因此：

### 禁止直接做这种修改：

```python
CREATE INDEX
↓
CREATE INDEX CONCURRENTLY
```

除非已经确认当前连接/transaction 生命周期完全兼容。

---

# 五、审计 init_db Transaction Boundary

重点检查：

```text
init_db()
```

内部是否类似：

```python
with engine.begin() as conn:
    ...
```

或者：

```python
with engine.connect() as conn:
    ...
```

或者：

```python
conn.execute(...)
conn.commit()
```

或者其他方式。

最终必须明确记录：

```text
init_db transaction model:
...
```

并判断：

```text
CREATE INDEX CONCURRENTLY
是否可以直接放进当前 init_db？
```

如果答案：

```text
NO
```

不要修改代码。

---

# 六、检查生产执行路径

必须确认：

### 情况 A

```text
init_db
    ↓
只用于开发/测试
```

那么：

```text
生产风险 = LOW
```

但必须有代码/部署证据。

---

### 情况 B

```text
生产启动
    ↓
init_db
    ↓
自动 CREATE INDEX
```

那么必须重点评估：

```text
生产启动期间
    ↓
CREATE INDEX
    ↓
是否阻塞业务写入
```

---

### 情况 C

```text
init_db
```

既用于开发：

```text
pytest / local
```

又用于生产：

```text
application startup
```

必须认为：

> 当前自动 DDL 机制需要生产安全审查。

---

# 七、检查当前表规模

如果当前环境允许安全地读取：

```text
ai_ops.llm_usage_record
```

只允许执行：

```sql
SELECT COUNT(*)
FROM ai_ops.llm_usage_record;
```

以及：

```sql
SELECT pg_size_pretty(
    pg_total_relation_size('ai_ops.llm_usage_record')
);
```

只读。

禁止：

```text
INSERT
UPDATE
DELETE
TRUNCATE
ALTER
DROP
CREATE
```

如果没有可用的生产/类生产数据库：

必须明确：

```text
production table size: unavailable
```

不要猜。

---

# 八、检查 Index Definition

确认当前索引实际定义。

使用只读查询，例如：

```sql
SELECT
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'ai_ops'
  AND tablename = 'llm_usage_record'
  AND indexname = 'ix_llm_usage_record_assistant_request_id';
```

确认：

```text
index exists
column = assistant_request_id
method = btree
unique = false
```

特别注意：

```text
CREATE INDEX IF NOT EXISTS
```

只会根据同名 relation 判断是否存在。

它不会保证：

```text
existing index definition
==
expected index definition
```

因此 Audit 必须明确这个风险。

---

# 九、检查 Index Validity

如果当前 PostgreSQL 环境允许，只读检查：

```text
pg_index
```

确认：

```text
indisvalid = true
```

以及：

```text
indisready = true
```

目标：

```text
index exists
AND
valid
AND
ready
```

如果发现：

```text
INVALID
```

必须报告：

```text
Index exists but is invalid.
```

不要自动 DROP。

不要自动 REINDEX。

不要自动修复。

PostgreSQL 官方文档说明，并发索引构建失败可能留下 INVALID index，因此生产 runbook 必须考虑失败后的处理方式。

---

# 十、评估三种方案

本阶段只分析，不实施。

比较：

## Option A

继续：

```text
init_db
    ↓
CREATE INDEX IF NOT EXISTS
```

优点：

```text
简单
当前实现无需变化
```

风险：

```text
生产大表创建索引时可能阻塞写入
```

---

## Option B

改成：

```text
CREATE INDEX CONCURRENTLY IF NOT EXISTS
```

优点：

```text
生产写操作不会被普通 index build 长时间阻塞
```

风险：

```text
不能在 transaction block
```

并且 concurrent build：

```text
需要更多扫描
CPU / IO 成本更高
执行时间更长
可能留下 INVALID index
```

这些都需要记录。

---

## Option C

生产环境使用：

```text
显式一次性 migration / deployment operation
```

例如：

```text
deployment
    ↓
pre-migration
    ↓
CREATE INDEX CONCURRENTLY
    ↓
application startup
```

而不是：

```text
application startup
    ↓
自动 DDL
```

分析：

```text
优点：
生产 DDL 可控
可以单独观察耗时
可以选择低峰期
可以单独失败/回滚处理
```

缺点：

```text
当前项目没有 migration framework
```

---

# 十一、必须给出推荐方案

基于真实代码检查结果，最终给出：

```text
Recommendation:
Option A / B / C
```

但不能凭空推荐。

必须根据：

```text
init_db production usage
transaction boundary
table size
deployment model
```

综合判断。

如果：

```text
init_db 会自动在生产启动执行
AND
llm_usage_record 可能较大
```

则必须重点考虑：

```text
不要在应用启动事务里执行普通 CREATE INDEX
```

如果：

```text
init_db 只用于开发/测试
```

则可以保留当前设计，并记录：

```text
production migration remains an operational responsibility
```

---

# 十二、不要直接修改 Step 52

本阶段禁止为了完成 Audit 而直接修改：

```text
backend/app/db/init_db.py
backend/app/db/models/llm_usage_record.py
```

除非发现：

> 当前实现存在明确的生产安全 bug，而且不修改就无法形成正确的生产安全边界。

即使发现问题：

**先停止并报告。**

不要直接实现 Option B/C。

---

# 十三、生产 Runbook 设计

如果 Audit 认为生产不能直接依赖：

```text
init_db
```

那么只设计一个最小 Runbook。

例如：

```text
1. 检查 index 是否已经存在
2. 检查 index definition
3. 检查 index validity
4. 检查 table size
5. 在低峰期执行 CONCURRENTLY
6. 等待完成
7. 再次检查 index validity
8. 检查 Assistant Trace 查询
9. 确认无异常
```

注意：

本阶段只写 Runbook。

**不要真的执行生产 DDL。**

---

# 十四、失败恢复设计

只设计，不执行。

如果：

```text
CREATE INDEX CONCURRENTLY
```

失败：

必须明确：

```text
1. 如何发现 INVALID index
2. 谁负责处理
3. 是否需要 DROP INDEX
4. 是否重新执行 CREATE INDEX CONCURRENTLY
```

不要自动写：

```text
DROP INDEX
```

到应用启动流程。

PostgreSQL 官方文档说明 concurrent build 失败可能留下 invalid index，因此这一点必须进入 Runbook。

---

# 十五、安全要求

本阶段：

```text
只读 Audit
```

禁止：

```text
INSERT
UPDATE
DELETE
TRUNCATE
DROP
ALTER
CREATE INDEX
```

尤其：

**不要真的在数据库执行 CREATE INDEX。**

不要输出：

```text
DATABASE_URL
password
API key
connection string
```

如果日志中存在敏感配置，不要复制到报告。

---

# 十六、测试要求

本阶段不需要新增大量测试。

执行：

```powershell
python -m pytest -q tests/test_llm_usage_assistant_request_id_index.py
```

然后：

```powershell
python -m compileall backend tests scripts
```

如果现有完整测试成本可接受：

```powershell
python -m pytest -q
```

不要为了 Audit 修改测试。

---

# 十七、Audit 文档

新增：

```text
docs/evaluation/phase-3.12-step-53-index-production-safety.md
```

内容至少：

```text
1. Current implementation
2. init_db execution path
3. Transaction boundary
4. Production usage
5. Table size
6. Current index definition
7. CREATE INDEX risk
8. CONCURRENTLY compatibility
9. Option A
10. Option B
11. Option C
12. Recommendation
13. Production Runbook
14. Failure Recovery
15. Limitations
```

不要写成长篇数据库教程。

---

# 十八、最终报告

完成后严格报告：

```text
Phase 3.12 Step 53 完成汇报

1. Audit Scope
2. init_db 执行路径
3. Transaction Boundary
4. Production Usage
5. llm_usage_record 当前规模
6. Index Definition
7. CREATE INDEX 风险
8. CONCURRENTLY 兼容性
9. Option A
10. Option B
11. Option C
12. Recommendation
13. Production Runbook
14. Failure Recovery
15. Tests
16. compileall
17. DB writes
18. 修改文件
19. 当前限制
```

其中：

```text
DB writes = 0
```

必须明确。

---

# 十九、强制 STOP

完成 Step 53 后：

**立即停止。**

不要进入：

```text
Step 54
Pagination
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Dashboard
```

不要修改：

```text
LLM Usage API
Assistant Trace API
Tool Observability
RAG Observability
```

不要新增 index。

不要执行生产 migration。

只返回：

```text
Phase 3.12 Step 53 完成报告
```

等待下一步指令。
