# Phase 3.9.7：Text-to-SQL Dangerous SQL Validator Security E2E

## 一、阶段目标

补齐 Phase 3.9.6 明确指出的安全验证缺口：

> 验证已知危险 SQL 进入真实 Text-to-SQL Pipeline 后，是否能够被真实 SQL Validator 拒绝，并确认被拒绝的 SQL 不会进入 SQL Executor。

本阶段是**安全集成测试阶段**，不是生产功能开发。

核心验证链路：

```text
Stub Generator
      ↓
危险 SQL
      ↓
Real TextToSQLService / Evaluation Runner
      ↓
Real SQL Validator
      ↓
REJECT
      ↓
Executor 不被调用
```

必须验证：

```text
Dangerous SQL
    ↓
SQL Validator
    ↓
Rejected
    ↓
NO Executor Call
```

---

# 二、严格范围

本阶段允许：

* 新增测试文件
* 新增测试 fixture / fake generator
* 如果确实需要，可以新增极少量测试辅助代码
* 使用现有 SQL Validator
* 使用现有 Text-to-SQL Evaluation Runner / Service
* 使用现有 SQL Executor 的 mock / spy

本阶段禁止：

* 修改 SQL Validator 生产逻辑
* 修改 SQL Executor 生产逻辑
* 修改 TextToSQLGenerator Protocol
* 修改 TextToSQLService 生产逻辑
* 修改 TextToSQLEvaluationRunner 生产逻辑
* 修改 Orchestrator
* 修改 Router
* 修改 Prompt
* 修改 Regression Dataset
* 修改 3.9.5 Snapshot
* 修改 3.9.6 Analysis Snapshot
* 调用 DeepSeek
* 调用任何真实 LLM
* 新增数据库表
* 新增第三方依赖
* 为测试通过而放宽 Validator
* 为测试通过而修改安全规则

如果现有测试基础设施已经足够，只新增测试，不修改任何 backend production file。

---

# 三、优先使用现有测试基础设施

开始之前先检查：

```text
backend/app/services/sql_validator_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/text_to_sql_evaluation_service.py
tests/
```

确认：

1. SQL Validator 的真实入口
2. Text-to-SQL Service 的真实入口
3. Evaluation Runner 的真实入口
4. 现有 Fake Generator / Stub Generator
5. 现有 Executor DI / mock / monkeypatch 方式

**不要重新实现已有组件。**

---

# 四、测试核心

新增：

```text
tests/test_text_to_sql_validator_security_e2e.py
```

使用真实 SQL Validator。

Generator 必须是 Stub/Fake，强制返回危险 SQL。

例如：

```sql
DELETE FROM knowledge_document;
```

或者：

```sql
UPDATE knowledge_document
SET title = 'x';
```

或者：

```sql
INSERT INTO knowledge_document (...)
VALUES (...);
```

但具体 SQL 必须使用当前项目真实存在的表/字段，避免因为不存在的 schema/column 导致测试失败原因混杂。

优先使用：

```sql
DELETE FROM knowledge_document;
```

因为它已经对应 3.9.5 的：

```text
safety_delete_all_documents
```

但是：

**不要修改 3.9.3 Dataset。**

这里只是测试 fixture。

---

# 五、第一组：DELETE 必须被 Validator 拒绝

建立测试：

```python
def test_real_validator_rejects_delete_before_executor():
    ...
```

要求：

* Stub Generator 返回：

```sql
DELETE FROM knowledge_document;
```

* 使用真实 SQL Validator
* 验证 Validator 返回 rejected / 抛出已有 validation exception
* 验证 Executor 没有被调用

最终必须证明：

```text
generated dangerous SQL
        ↓
real validator
        ↓
REJECT
        ↓
executor calls = 0
```

---

# 六、第二组：UPDATE

新增：

```python
def test_real_validator_rejects_update_before_executor():
    ...
```

Stub Generator 返回：

```sql
UPDATE knowledge_document
SET title = 'x';
```

要求：

```text
Validator = rejected
Executor = 0 calls
```

---

# 七、第三组：INSERT

新增：

```python
def test_real_validator_rejects_insert_before_executor():
    ...
```

Stub Generator 返回一个语法正确、但执行写操作的 INSERT。

要求：

```text
Validator = rejected
Executor = 0 calls
```

如果当前 `knowledge_document` 必填字段较多导致构造 INSERT 麻烦，可以使用一个最小、但语法正确的 INSERT，并根据现有 schema 调整。

不要因为 INSERT fixture 麻烦而修改生产代码。

---

# 八、第四组：DDL

如果当前 Validator 已明确禁止 DDL，再增加：

```python
def test_real_validator_rejects_ddl_before_executor():
    ...
```

例如：

```sql
DROP TABLE knowledge_document;
```

或者当前 Validator 已覆盖的其它 DDL。

要求：

```text
Validator = rejected
Executor = 0 calls
```

如果现有 Validator 对 DDL 的行为并不是明确约定，不要强行增加这一项。

---

# 九、Executor 零调用必须是硬断言

不能只测试：

```python
assert validator_rejected
```

必须同时：

```python
assert executor.call_count == 0
```

或者使用当前项目已有的 spy/mock 方式进行等价断言。

这是本阶段最重要的测试点。

我们不是只证明：

> Validator 不喜欢 DELETE。

而是证明：

> Validator 拒绝之后，危险 SQL 不会继续进入 Executor。

---

# 十、必须使用真实 Validator

禁止：

```python
fake_validator = RejectAllValidator()
```

禁止：

```python
mock_validator.return_value = False
```

这种测试没有意义。

必须使用：

```text
真实 SQLValidatorService
```

或者当前项目真实 Validator 的实际构造方式。

允许 Fake 的只有：

```text
Generator
Executor
LLM
```

其中：

* Generator：Stub，故意产生危险 SQL
* Executor：Spy/Mock，只用于证明没有调用
* LLM：完全不调用

---

# 十一、不要调用真实数据库

本阶段原则：

```text
Validator = real
Generator = fake
Executor = spy/mock
Database = NO
LLM = NO
Network = NO
```

特别注意：

DELETE / UPDATE / INSERT **绝对不能实际执行**。

因此即使现有 Executor 需要 DB，也必须在 Validator 之前就被阻断。

如果当前架构导致构造 Executor 会初始化 DB connection：

* 可以 mock Executor
* 但不能 mock Validator
* 不得连接真实业务 DB

---

# 十二、测试 Runner / Service 的真实边界

优先选择能够验证真实链路的入口。

理想：

```text
TextToSQLEvaluationRunner
        ↓
TextToSQLService
        ↓
Real SQLValidator
        ↓
Executor spy
```

如果 Evaluation Runner 当前设计本身不会进入 Executor，则测试可以针对实际 Service 边界验证。

但是报告必须明确：

```text
测试验证到了哪一层
```

不要声称测试覆盖了不存在的调用链。

---

# 十三、验证异常/结果，不修改生产接口

首先查看当前 Validator 的实际 API。

可能是：

```python
result = validator.validate(sql)
assert not result.valid
```

也可能是：

```python
with pytest.raises(SQLValidationError):
    ...
```

或者其它现有形式。

**严格复用当前接口。**

不要为了方便测试修改 Validator API。

---

# 十四、增加安全边界测试

除了 DML 本身，增加至少两个测试：

### 14.1 多语句

例如：

```sql
SELECT id FROM knowledge_document;
DELETE FROM knowledge_document;
```

要求：

```text
Validator = rejected
Executor = 0
```

### 14.2 写操作嵌套 / CTE

如果当前 Validator 已经明确覆盖，可以增加类似：

```sql
WITH x AS (
    DELETE FROM knowledge_document
    RETURNING id
)
SELECT * FROM x;
```

如果 sqlglot / 当前 Validator 无法解析该 SQL，不要为了这个 case 修改 Validator。

可以跳过该 case，并说明原因。

---

# 十五、验证只读 SQL仍然可以通过

为了防止测试把 Validator 错误地变成“全部拒绝”，增加一个正向测试：

```python
def test_read_only_select_still_passes_and_can_reach_executor():
    ...
```

例如：

```sql
SELECT id
FROM knowledge_document
LIMIT 1;
```

要求：

```text
Validator = accepted
```

Executor 是否真正调用：

* 如果当前 Service / Runner 架构允许，则可以验证 executor call = 1
* 如果测试边界无法自然验证执行，则至少验证 Validator accepted

**不要为了这个正向测试连接真实 DB。**

---

# 十六、不要修改 3.9.5 安全基线

特别注意：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_5_real_llm_baseline.json
```

不得修改。

因为 3.9.5 的：

```text
safety_delete_all_documents
```

仍然应该保持：

```text
Expectation Mismatch
SECURITY_LLM_REFUSAL
SECURITY_EXPECTATION_MISMATCH
```

本阶段只是新增：

```text
Controlled Validator Security E2E
```

它不是重新跑 3.9.5。

---

# 十七、测试数量

建议控制在：

```text
8 ~ 12 tests
```

不要为了数量堆测试。

最少覆盖：

```text
1. DELETE → reject → executor 0
2. UPDATE → reject → executor 0
3. INSERT → reject → executor 0
4. DDL → reject → executor 0（如果当前规则明确支持）
5. multi-statement → reject → executor 0
6. read-only SELECT → accepted
7. dangerous SQL rejection happens before executor
8. generator output is actually passed into real validator
```

---

# 十八、重要：验证测试不是“假安全”

测试中不要出现：

```python
assert dangerous_sql not in executed_sql
```

作为唯一断言。

因为如果 Executor 根本没有被调用，这个断言太弱。

必须：

```python
assert executor.call_count == 0
```

最好再加：

```python
assert executor.execute.call_count == 0
```

具体按照项目现有 Executor 接口调整。

---

# 十九、报告

新增：

```text
docs/evaluation/text-to-sql-validator-security-e2e-3.9.7.md
```

报告至少包含：

## 1. Scope

说明：

```text
- Offline
- No LLM
- No DeepSeek
- No production logic modification
- Real SQL Validator
- Stub Generator
- Spy/Mock Executor
```

## 2. Test Matrix

例如：

| SQL Type        | Generated | Validator |              Executor |
| --------------- | --------: | --------: | --------------------: |
| DELETE          |       Yes |    Reject |                     0 |
| UPDATE          |       Yes |    Reject |                     0 |
| INSERT          |       Yes |    Reject |                     0 |
| DDL             |       Yes |    Reject |                     0 |
| Multi-statement |       Yes |    Reject |                     0 |
| SELECT          |       Yes |    Accept | according to boundary |

## 3. Security Boundary

明确写：

```text
Dangerous SQL
      ↓
Real Validator
      ↓
Rejected
      ↓
Executor not called
```

## 4. What this proves

可以证明：

> 在本测试覆盖的危险 SQL 类型中，真实 SQL Validator 能够拒绝危险 SQL，并阻止其进入 Executor。

## 5. What this does NOT prove

不能声称：

* 所有 SQL 注入都已覆盖
* 所有 sqlglot 边界都已覆盖
* Executor 本身安全性已经完整验证
* 生产环境安全性 100%
* LLM 安全性 100%

---

# 二十、建议增加一个测试安全快照（可选）

如果实现简单，可以在报告中直接使用测试矩阵。

**不需要新增 JSON Snapshot。**

本阶段重点是测试，不要为了保存结果再造一套评价系统。

---

# 二十一、回归测试

完成后运行：

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

然后验证：

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
* 不访问真实业务 DB
* DB residue = 0
* 3.9.5 Snapshot SHA256 不变
* 3.9.6 Analysis Snapshot SHA256 不变
* Dataset SHA256 不变

---

# 二十二、最终汇报格式

完成后只汇报：

1. 新增/修改文件
2. 使用了哪个真实 Validator 入口
3. Dangerous SQL 测试矩阵
4. Executor 是否做到 0 calls
5. 正向 SELECT 测试结果
6. 测试总数
7. 全量 pytest
8. DB pytest
9. compileall
10. 三个 baseline/analysis `--check`
11. Snapshot/Dataset 是否保持不变
12. DB residue
13. 本阶段到底证明了什么
14. 本阶段没有证明什么

**完成 Phase 3.9.7 后立即停止，不进入 Phase 3.9.8。**
