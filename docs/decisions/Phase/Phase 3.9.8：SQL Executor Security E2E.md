# Phase 3.9.8：SQL Executor Security E2E

## 一、阶段目标

验证现有 `SQLExecutorService` 自身的安全边界。

Phase 3.9.7 已经证明：

```text
Dangerous SQL
    ↓
Real SQL Validator
    ↓
Reject
    ↓
Executor = 0 calls
```

本阶段进一步验证：

> 即使绕过 Validator，直接把 SQL 交给真实 `SQLExecutorService`，Executor 自身是否仍能阻止写操作，并保持测试数据库安全。

核心链路：

```text
Test SQL
   ↓
Real SQLExecutorService
   ↓
READ ONLY transaction
   ↓
Dangerous SQL
   ↓
Database rejects write
   ↓
Rollback / no persistent mutation
```

本阶段不是 Validator 测试。

**必须直接调用真实 SQLExecutorService。**

---

# 二、严格范围

允许：

* 新增测试文件
* 新增测试 fixture
* 必要时新增测试辅助函数
* 使用现有测试 PostgreSQL
* 使用真实 `SQLExecutorService`
* 使用真实测试数据库连接

禁止：

* 修改 `SQLExecutorService` 生产逻辑
* 修改 SQLValidator
* 修改 TextToSQLService
* 修改 Runner
* 修改 Prompt
* 修改 Dataset
* 修改 3.9.5 Snapshot
* 修改 3.9.6 Analysis Snapshot
* 修改 3.9.7 Report
* 调用 DeepSeek / 任何真实 LLM
* 使用真实业务数据库
* 新增数据库表到生产 schema
* 新增第三方依赖
* 修改数据库 migration
* 修改 Executor API

如果现有 Executor 已经具备测试能力，优先只新增测试。

---

# 三、先阅读现有 Executor

开始前必须阅读：

```text
backend/app/services/sql_executor_service.py
```

同时查看：

```text
tests/
backend/app/db/
```

确认现有实现：

1. transaction 如何创建
2. 是否使用：

```text
AUTOCOMMIT
BEGIN READ ONLY
ROLLBACK
```

3. statement timeout 如何设置
4. `fetchmany(max_rows + 1)` 如何工作
5. 1MB result protection 如何实现
6. search_path 如何注入
7. exception 如何转换
8. connection / session 如何释放

**不要先假设实现。**

测试必须基于当前真实代码。

---

# 四、数据库范围

本阶段可以使用当前测试 PostgreSQL。

但必须：

```text
Database = test PostgreSQL only
Schema = isolated test schema
Production tables = NO
Production database = NO
```

如果现有 DB fixture 已经有：

```text
project_a
project_b
```

可以复用。

但如果为了测试 Executor 写保护需要数据表：

* 优先使用已有测试 schema
* 不新增 migration
* 可以在测试生命周期内创建临时表
* 测试结束必须 DROP
* 或使用 transaction fixture 自动回滚

最终必须：

```text
DB residue = 0
```

---

# 五、第一组：直接执行 DELETE

新增测试：

```python
def test_executor_rejects_delete_in_read_only_transaction():
    ...
```

直接调用：

```text
Real SQLExecutorService
```

不要调用 Validator。

SQL 使用测试表，例如：

```sql
DELETE FROM <isolated_test_table>;
```

要求：

```text
Validator = NOT INVOKED
Executor = REAL
Database = TEST ONLY
```

预期：

```text
Execution fails
```

并且：

```text
persistent rows unchanged
```

---

# 六、第二组：UPDATE

测试：

```python
def test_executor_rejects_update_in_read_only_transaction():
    ...
```

SQL：

```sql
UPDATE <isolated_test_table>
SET ...
WHERE ...;
```

要求：

```text
Execution fails
Database row unchanged
```

不要依赖 Validator。

---

# 七、第三组：INSERT

测试：

```python
def test_executor_rejects_insert_in_read_only_transaction():
    ...
```

要求：

```text
Execution fails
No new row persisted
```

---

# 八、第四组：DDL

如果 PostgreSQL + 当前 Executor 的 READ ONLY transaction 对 DDL 有明确阻止行为，增加：

```python
def test_executor_rejects_ddl_in_read_only_transaction():
    ...
```

例如：

```sql
CREATE TABLE ...
```

或：

```sql
DROP TABLE ...
```

优先使用临时测试对象。

注意：

不要为了测试 DROP 去删除已有测试表。

---

# 九、最重要：验证 READ ONLY

如果当前 Executor 的实现确实使用：

```sql
BEGIN READ ONLY
```

必须增加一个明确测试，证明 transaction 本身处于 read-only 状态。

例如通过一个只读查询：

```sql
SHOW transaction_read_only;
```

如果 Executor API 不允许返回该内部状态，不要修改生产 API。

可以通过现有测试数据库 fixture / connection spy 验证：

```text
BEGIN READ ONLY
```

或者通过数据库实际行为验证。

**优先黑盒验证。**

---

# 十、验证 SELECT 仍然正常

新增：

```python
def test_executor_allows_read_only_select():
    ...
```

例如：

```sql
SELECT id
FROM <isolated_test_table>
LIMIT 1;
```

要求：

```text
success = True
```

并且：

```text
rows <= max_rows
```

证明 Executor 不是简单地“拒绝所有 SQL”。

---

# 十一、验证 MAX_ROWS

现有 Executor 已经有：

```text
max_rows
```

以及：

```text
fetchmany(max_rows + 1)
```

本阶段必须验证：

```python
def test_executor_enforces_max_rows():
    ...
```

例如：

```text
test table = 10 rows
max_rows = 3
```

要求结果不会返回超过：

```text
3 rows
```

如果当前 API 的预期行为是抛出“result too large”异常，则按照现有 API 断言。

**不要改变现有行为，只测试它。**

---

# 十二、验证 1MB Result Protection

如果当前实现已有：

```text
1MB result protection
```

增加：

```python
def test_executor_rejects_oversized_result():
    ...
```

使用测试数据生成超过限制的结果。

要求：

```text
Execution fails safely
No partial result is treated as successful
```

不要使用极端大的数据集导致测试变慢。

优先构造可控的：

```text
repeat(...)
generate_series(...)
```

如果 PostgreSQL 测试环境支持。

---

# 十三、验证 Statement Timeout

当前 Executor 已经设置 statement timeout。

增加：

```python
def test_executor_enforces_statement_timeout():
    ...
```

使用非常短的 timeout。

测试 SQL 可以使用：

```sql
SELECT pg_sleep(...);
```

但必须确保：

* sleep 时间明显大于 timeout
* 测试本身不会长时间阻塞
* 不产生数据写入

例如：

```text
timeout = 50~100 ms
sleep = 1 s
```

具体根据当前 Executor 配置单位调整。

预期：

```text
Execution fails with timeout
```

并且 connection / transaction 可以正常恢复。

---

# 十四、验证异常后的连接可继续使用

增加：

```python
def test_executor_connection_recovers_after_failed_query():
    ...
```

流程：

```text
1. 执行超时 SQL
2. 捕获 Executor exception
3. 再执行 SELECT 1
4. SELECT 1 成功
```

目的：

验证异常不会把测试连接永久留在 aborted transaction 状态。

如果 Executor 每次调用创建独立 connection，则按照真实实现调整：

```text
failed query
    ↓
resource cleanup
    ↓
next query succeeds
```

---

# 十五、验证 rollback / 无残留

对于：

```text
DELETE
UPDATE
INSERT
DDL
```

测试后必须检查：

```text
数据没有发生永久变化
```

例如：

```text
before_count == after_count
before_value == after_value
```

如果写操作在 PostgreSQL READ ONLY transaction 内直接被拒绝，则数据自然不变。

但仍然必须显式验证。

---

# 十六、不要修改 Validator

本阶段必须保证：

```text
SQLValidatorService = NOT USED
```

如果测试调用了 Validator：

这不是本阶段的 Executor 安全测试。

可以在测试中直接构造：

```text
SQLExecutorService
```

然后执行危险 SQL。

这样才能回答：

> Validator 被绕过后，Executor 自己是否还有第二道防线？

---

# 十七、Executor 的真实 API

严格按照现有：

```text
SQLExecutorService
```

API 写测试。

不要因为测试方便修改：

```text
execute()
```

签名。

不要添加：

```text
security_mode
allow_write
skip_validation
```

之类新参数。

本阶段不做生产设计。

---

# 十八、测试数量

建议：

```text
8 ~ 12 tests
```

最少覆盖：

```text
1. DELETE → Executor reject
2. UPDATE → Executor reject
3. INSERT → Executor reject
4. DDL → Executor reject（如果适用）
5. SELECT → Executor success
6. max_rows
7. oversized result
8. statement timeout
9. connection recovery
10. no persistent mutation
```

如果某项当前实现不存在，例如 Executor 没有独立的 1MB protection：

不要新增生产逻辑。

明确报告：

```text
Not applicable / existing implementation does not expose this boundary
```

---

# 十九、测试数据库隔离

测试中禁止：

```text
public.knowledge_document
```

等真实业务表作为写操作目标。

建立或使用：

```text
isolated test schema
```

例如：

```text
text_to_sql_executor_security_test
```

但如果创建 schema：

必须在测试结束清理。

推荐 fixture：

```python
@pytest.fixture
def executor_security_table(...):
    ...
    yield ...
    ...
```

最终：

```text
schema residue = 0
```

---

# 二十、报告

新增：

```text
docs/evaluation/text-to-sql-executor-security-e2e-3.9.8.md
```

报告至少包含：

## 1. Scope

```text
Real SQLExecutorService
No Validator
No LLM
Test PostgreSQL only
Isolated schema
```

## 2. Security Matrix

| Test             | Executor | DB Mutation | Result |
| ---------------- | -------- | ----------- | ------ |
| DELETE           | Reject   | No          | PASS   |
| UPDATE           | Reject   | No          | PASS   |
| INSERT           | Reject   | No          | PASS   |
| DDL              | Reject   | No          | PASS   |
| SELECT           | Accept   | Read only   | PASS   |
| max_rows         | Limited  | No          | PASS   |
| oversized result | Reject   | No          | PASS   |
| timeout          | Reject   | No          | PASS   |
| recovery         | Reusable | No          | PASS   |

按照实际测试结果填写。

---

# 二十一、安全边界结论

报告必须明确区分：

### Phase 3.9.7

证明：

```text
Validator
    ↓
reject
    ↓
Executor not called
```

### Phase 3.9.8

本阶段要证明：

```text
Validator bypassed
    ↓
Real Executor
    ↓
READ ONLY transaction
    ↓
write rejected
```

如果所有测试通过，可以形成：

```text
              ┌── Validator ── reject ── X
Dangerous SQL ┤
              └── Executor ── READ ONLY ── reject ── X
```

这意味着存在两层独立防护。

但报告仍然不能写：

> “系统已经绝对安全”。

---

# 二十二、必须记录当前 Executor 的实际安全机制

报告中根据源码真实情况记录：

```text
Transaction mode:
Statement timeout:
Max rows:
Result size limit:
Rollback behavior:
Connection cleanup:
```

不要根据任务书猜测。

---

# 二十三、回归测试

完成后：

```powershell
pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

然后：

```powershell
python -m compileall backend scripts -q
```

再执行：

```powershell
python scripts/generate_text_to_sql_baseline.py --check
```

```powershell
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

```powershell
python scripts/analyze_text_to_sql_real_llm_baseline.py --check
```

要求：

* 全部通过
* 不调用 DeepSeek
* 不访问真实业务数据库
* 3.9.5 Snapshot 不变
* 3.9.6 Snapshot 不变
* Dataset 不变
* DB residue = 0
* 无临时 schema / table 残留

---

# 二十四、如果发现 Executor 当前实现存在安全缺陷

这是非常重要的一点：

**不要直接修改生产代码来让测试通过。**

如果出现：

```text
DELETE unexpectedly succeeds
```

或者：

```text
UPDATE unexpectedly persists
```

或者：

```text
Executor 没有 READ ONLY
```

请：

1. 保留失败测试
2. 记录实际行为
3. 报告安全缺口
4. 停止

本阶段目标是**建立真实安全基线**，不是为了获得绿色测试而修改 Executor。

---

# 二十五、最终汇报

完成后只汇报：

1. 新增/修改文件
2. 当前真实 Executor 安全机制
3. 测试矩阵
4. DELETE / UPDATE / INSERT / DDL 结果
5. SELECT 结果
6. max_rows 结果
7. result size 结果
8. timeout 结果
9. connection recovery 结果
10. 数据是否发生永久变化
11. 全量 pytest
12. DB pytest
13. compileall
14. 三个历史 `--check`
15. Snapshot / Dataset SHA256
16. DB residue
17. 本阶段证明了什么
18. 本阶段没有证明什么

**完成 Phase 3.9.8 后立即停止，不进入 Phase 3.9.9。**
