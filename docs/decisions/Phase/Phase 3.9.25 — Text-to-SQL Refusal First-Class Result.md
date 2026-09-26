# Phase 3.9.25 — Text-to-SQL Refusal First-Class Result

## 目标

解决 Phase 3.9.24 发现的两个 Production Blocker：

1. `TextToSQLService.generate()` 无法表达 LLM 的明确 refusal
2. refusal 最终被 API 转换成 HTTP 500 / retry exhausted

本阶段只建立一个正式的 **Refusal First-Class Result**。

目标链路：

```text
LLM
 ↓
TextToSQLService
 ↓
识别 REFUSAL
 ↓
返回明确的 Refusal Result
 ↓
Orchestrator / API
 ↓
用户得到清晰的只读拒绝信息
```

而不是：

```text
REFUSAL
 ↓
EMPTY_SQL
 ↓
retry
 ↓
TextToSQLRetryExceededError
 ↓
HTTP 500
```

---

# 一、严格范围

允许：

* 修改 Text-to-SQL Service
* 新增 Text-to-SQL Result DTO / Result Type
* 修改 Orchestrator 对 refusal 的处理
* 修改 API 对 refusal 的处理
* 新增单元测试 / E2E 测试
* 新增 Phase 3.9.25 文档与 snapshot

禁止：

* 不修改 Prompt v1
* 不修改 Prompt v2
* 不进行 Prompt A/B
* 不优化 Prompt
* 不修改 SQL Validator 安全规则
* 不修改 SQL Executor
* 不修改 Dataset
* 不修改 Ground Truth
* 不修改已有历史 snapshot
* 不新增 Tool
* 不新增数据库表
* 不改变正常 Text-to-SQL SQL 生成逻辑
* 不把 refusal 当成合法 SQL
* 不让 refusal 进入 Executor

核心原则：

> 本阶段只增加“拒绝结果类型”，不要重构整个 Text-to-SQL 系统。

---

# 二、先阅读现有代码

重点检查：

```text
backend/app/services/text_to_sql_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/api/
```

以及现有：

```text
TextToSQLRetryExceededError
TextToSQLGenerationError
TextToSQLValidationError
ChatResponse
```

确认当前返回类型和异常传播方式。

不要猜接口，以当前代码为准。

---

# 三、设计 First-Class Refusal Result

新增一个最小 DTO。

推荐概念：

```python
TextToSQLResult
```

包含至少：

```text
status
sql
refusal_reason
```

状态至少支持：

```text
SQL
REFUSAL
```

如果现有代码结构更适合，也可以使用：

```text
TextToSQLSuccess
TextToSQLRefusal
```

二选一，以当前项目风格为准。

要求：

### SQL Result

```text
status = SQL
sql = validated SELECT
```

### Refusal Result

```text
status = REFUSAL
sql = None
refusal_reason = destructive_request_not_supported
```

禁止：

```text
REFUSAL + SQL
```

也禁止：

```text
REFUSAL → Validator
REFUSAL → Executor
```

---

# 四、Refusal 识别规则

当前 Prompt v2 的 refusal marker 是：

```text
-- REFUSED: destructive request is not supported (read-only service)
```

本阶段只识别已经约定好的 refusal marker。

必须支持：

```text
-- REFUSED: destructive request is not supported (read-only service)
```

以及：

````text
```sql
-- REFUSED: destructive request is not supported (read-only service)
````

````

要求：

- 去除 Markdown fence 后识别
- 前后允许存在空白
- 必须是完整、明确的 refusal marker
- 不要使用模糊的“包含 REFUSED 就算 refusal”

例如：

```text
-- REFUSED: destructive request is not supported (read-only service)
````

→ REFUSAL

但是：

```text
SELECT '-- REFUSED: destructive request is not supported (read-only service)'
```

→ 正常 SQL，由 Validator 判断

不要误判。

---

# 五、Retry 行为

这是本阶段最重要的行为变化之一。

当前：

```text
REFUSAL
 ↓
EMPTY_SQL
 ↓
retry × 2
```

修改为：

```text
REFUSAL
 ↓
REFUSAL RESULT
 ↓
立即结束
```

也就是说：

> 明确 refusal 不是 generation failure，不应该消耗 retry budget。

因此：

```text
llm_calls = 1
retry_count = 0
```

对于明确 refusal。

---

# 六、正常 SQL 行为必须保持不变

例如：

```text
SELECT id, title
FROM public.knowledge_document
LIMIT 1000
```

仍然：

```text
LLM
 ↓
extract SQL
 ↓
Validator
 ↓
TextToSQLSuccess
```

Validator rejection 仍然应该：

```text
invalid SQL
 ↓
retry
```

不要把所有非-SQL输出都当成 refusal。

区分：

```text
REFUSAL
INVALID / EMPTY
VALID SQL
```

---

# 七、Orchestrator 行为

检查：

```text
AIOrchestratorService.execute()
```

当 Text-to-SQL 返回：

```text
status = REFUSAL
```

不得：

```text
SQLExecutorService.execute()
```

应该直接形成 AI Chat 层面的拒绝结果。

例如内部语义：

```text
TextToSQLRefusal
    ↓
ChatResponse
```

不要让它进入通用 Exception → 500 路径。

---

# 八、API 行为

对于：

```text
POST /api/ai/chat
```

用户提出：

```text
删除所有库存
删除所有单据
修改库存数量
```

如果 Router 正确进入 Text-to-SQL，并且 LLM 返回 refusal：

API 不应该返回：

```text
HTTP 500
AI 能力执行失败
TextToSQLRetryExceededError
```

应该返回项目现有 ChatResponse 体系中合理的**正常 AI 拒绝响应**。

优先复用现有 response envelope。

不要为了本阶段创建一个全新的 API response schema。

例如最终用户可看到：

```text
当前 AI 数据查询服务仅支持只读查询，不支持删除、修改等操作。
```

具体 wording 可以根据现有项目风格确定。

不要把内部：

```text
destructive_request_not_supported
```

暴露给用户。

---

# 九、安全边界

必须保持：

```text
Prompt
 ↓
Refusal Detection
 ↓
SQL Validator
 ↓
SQL Executor
```

对于 refusal：

```text
Refusal Detection
 ↓
STOP
```

对于正常 SQL：

```text
Validator
 ↓
Executor
```

特别验证：

```text
DELETE
UPDATE
INSERT
DROP
ALTER
TRUNCATE
multi-statement
```

仍然由 Validator 拒绝。

本阶段绝对不能因为新增 refusal result 而弱化 Validator。

---

# 十、测试要求

新增专门测试文件，例如：

```text
tests/test_text_to_sql_refusal_result.py
```

至少覆盖：

### 1. 裸 refusal

```text
LLM → refusal marker
```

期望：

```text
status = REFUSAL
sql = None
llm_calls = 1
validator_calls = 0
executor_calls = 0
```

### 2. fenced refusal

````text
```sql
-- REFUSED...
````

````

期望同上。

### 3. refusal 不 retry

验证：

```text
retry_count = 0
````

### 4. 正常 SELECT

验证：

```text
status = SQL
```

且正常经过 Validator。

### 5. Invalid SQL

验证：

```text
invalid SQL → retry
```

不能误判成 REFUSAL。

### 6. SQL 中包含 refusal 字符串

例如：

```sql
SELECT '-- REFUSED: destructive request is not supported (read-only service)' AS message
LIMIT 1
```

必须：

```text
NOT REFUSAL
```

### 7. Refusal 不进入 Executor

必须：

```text
executor_calls = 0
```

### 8. Orchestrator refusal

验证：

```text
TextToSQLRefusal
 ↓
ChatResponse
```

而不是 Exception。

### 9. API refusal

验证：

```text
POST /api/ai/chat
```

得到正常拒绝响应，而不是 HTTP 500。

### 10. Existing regression

运行已有 Text-to-SQL regression tests。

不能破坏已有正常行为。

---

# 十一、不要直接接入真实 DeepSeek

本阶段核心是接口语义，不需要重新跑 3.9.23。

测试使用 scripted fake LLM。

原因：

```text
目标 = 验证 deterministic behavior
```

不是验证模型。

如确实需要真实模型，只能作为额外 smoke test，不得改变测试结论。

---

# 十二、测试命令

Windows PowerShell：

```powershell
python -m pytest tests/test_text_to_sql_refusal_result.py -q
python -m pytest
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

最后：

```powershell
python -m compileall backend
```

---

# 十三、Phase 3.9.25 验收标准

必须达到：

```text
Refusal Result = PASS
Refusal no retry = PASS
Refusal no Validator = PASS
Refusal no Executor = PASS
Normal SQL = PASS
Invalid SQL retry = PASS
Orchestrator refusal = PASS
API refusal = PASS
Historical regression = PASS
Security boundary = PASS
```

核心行为：

```text
REFUSAL
 ↓
First-Class Result
 ↓
ChatResponse
```

而不是：

```text
REFUSAL
 ↓
EMPTY_SQL
 ↓
RETRY_EXHAUSTED
 ↓
HTTP 500
```

---

# 十四、Promotion Gate 不在本阶段关闭

本阶段只解决 blocker。

不要宣称：

```text
v2 已正式生产
```

也不要自动修改：

```text
production prompt = v2
```

完成后应该得到：

```text
Phase 3.9.25 = PASS
```

然后等待下一阶段重新执行 Promotion Gate。

---

# 十五、最终报告

完成后只报告：

## Phase 3.9.25 Result

### 1. Result

```text
PASS / FAIL
```

### 2. New Result Model

说明最终采用：

```text
TextToSQLResult
```

还是：

```text
TextToSQLSuccess / TextToSQLRefusal
```

以及字段。

### 3. Refusal Flow

用实际代码说明：

```text
LLM
 ↓
Refusal Detection
 ↓
Refusal Result
 ↓
Orchestrator
 ↓
ChatResponse
```

### 4. Normal SQL Flow

确认没有改变。

### 5. API Behavior

说明 refusal 是否已经不再返回 HTTP 500。

### 6. Tests

报告：

```text
new tests:
pytest:
compileall:
```

### 7. Files Changed

只列实际修改文件。

### 8. Security

确认：

```text
Refusal → Executor = 0
```

以及 Validator 安全边界没有修改。

---

# 十六、停止规则

完成 Phase 3.9.25 后立即停止。

不要：

* 自动进入 3.9.26
* 自动重新做 Prompt A/B
* 自动推广 v2
* 自动修改 Validator
* 自动修改 Executor
* 自动继续优化 Prompt

等待下一步指令。
