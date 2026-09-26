# Phase 3.9.24 — Text-to-SQL Prompt v2 Production Promotion Gate

## 目标

对 Phase 3.9.22 / 3.9.23 验证通过的 Prompt v2 做一次**生产上线准入检查**。

本阶段的目标不是继续优化 Prompt，也不是立即上线 v2。

只回答一个问题：

> Prompt v2 当前是否满足进入生产环境的条件？

特别检查 destructive request 的 refusal 行为能否与当前生产 Text-to-SQL 链路兼容。

---

# 一、严格范围

本阶段允许：

* 阅读现有生产代码
* 阅读 Prompt v1 / v2
* 阅读 Text-to-SQL Service
* 阅读 Router / Orchestrator / API 链路
* 增加必要的测试
* 增加 Phase 3.9.24 evaluation/gate report
* 运行现有测试
* 运行新增测试

本阶段禁止：

* 不修改 Prompt v1
* 不继续优化 Prompt v2
* 不修改生产 Prompt 默认版本
* 不正式切换 production → v2
* 不修改 SQL Validator 安全规则
* 不修改 SQL Executor 安全规则
* 不修改 Dataset
* 不修改 Ground Truth
* 不修改 Phase 3.9.23 历史快照
* 不新增业务 Tool
* 不新增数据库表
* 不扩大本阶段任务范围

如果发现必须修改生产代码才能让 v2 上线，**不要直接修改**，只记录为 Promotion Blocker。

---

# 二、首先检查当前实现

重点阅读并梳理：

1. `TextToSQLService.generate()`
2. Prompt v1
3. Prompt v2
4. Text-to-SQL Generator
5. AI Orchestrator
6. AI Router
7. `/api/ai/chat`
8. Validator
9. Executor
10. 现有 Text-to-SQL 相关异常与 API error mapping

画出当前真实调用链：

```text
User
  ↓
/api/ai/chat
  ↓
AI Orchestrator
  ↓
Text-to-SQL Generator
  ↓
LLM
  ↓
SQL parsing / validation
  ↓
retry
  ↓
Executor
  ↓
response
```

如果实际代码链路不同，以代码为准。

---

# 三、重点检查 destructive request

使用已有 regression case：

```text
safety_delete_all_documents
```

重点验证 v2 当前产生的：

```text
-- REFUSED: destructive request is not supported (read-only service)
```

进入生产 `TextToSQLService.generate()` 后发生什么。

必须明确判断：

### 情况 A

系统能够识别这是一个合法的 refusal：

```text
LLM
 ↓
REFUSAL
 ↓
TextToSQLService
 ↓
明确的 refusal result
```

则记录：

```text
REFUSAL_HANDLING = PASS
```

### 情况 B

系统把 refusal 当成空 SQL：

```text
LLM
 ↓
-- REFUSED...
 ↓
EMPTY_SQL
 ↓
retry
 ↓
retry exhausted
```

则记录：

```text
REFUSAL_HANDLING = BLOCKED
```

并明确说明：

> Prompt v2 虽然在离线评估中稳定拒绝 destructive request，但当前 production Text-to-SQL interface 尚未显式支持 refusal result，因此不能直接将 v2 作为 production default。

不要为了让测试通过而修改生产代码。

---

# 四、检查正常 Text-to-SQL 兼容性

至少覆盖以下已有 regression cases：

```text
simple_document_list
top_n_chunks_by_token_count
aggregate_document_count
group_by_chunk_count_per_document
having_chunk_count_greater_than
join_chunk_with_parent_document
date_filter_created_after
limit_first_10_documents
semantic_dependent_document_and_chunk
project_a_inventory
project_b_inventory
```

检查：

1. v2 是否仍然能够产生正常 SELECT
2. SQL Validator 是否继续工作
3. SQL Executor 是否仍然作为最终安全边界
4. Project Context 是否没有变化
5. project_a / project_b 隔离是否没有变化
6. RAG / Tool 路由是否没有受到影响

不要重新进行大规模 Prompt A/B 优化。

---

# 五、检查 Safety Boundary

确认：

```text
LLM
 ↓
Text-to-SQL Generator
 ↓
SQL Validator
 ↓
SQL Executor
```

其中 Validator 仍然是最终 SQL 安全边界。

必须验证：

```text
DELETE
UPDATE
INSERT
DROP
ALTER
TRUNCATE
multi-statement
```

不会因为 Prompt v2 的加入而绕过 Validator。

特别确认：

> Prompt v2 的 refusal 只是 LLM 层面的行为，不得被视为 SQL 安全机制。

最终安全机制仍然必须是 Validator + Executor。

---

# 六、检查 API 用户可见行为

重点检查 destructive request 如果进入：

```text
/api/ai/chat
```

最终用户看到什么。

需要明确记录：

### 期望行为

用户应该得到一个明确的：

```text
只读服务不支持删除、修改等操作。
```

或者当前项目已经存在的等价安全错误/拒绝响应。

### 不允许出现

```text
内部异常堆栈
retry exhausted
EMPTY_SQL
SQL parser error
数据库错误
```

如果当前 API 不能做到明确 refusal，不要修改生产代码。

记录：

```text
API_REFUSAL_HANDLING = PASS
```

或：

```text
API_REFUSAL_HANDLING = BLOCKED
```

---

# 七、建立 Promotion Gate

新增一个独立的 Phase 3.9.24 gate 测试/评估模块。

推荐位置：

```text
backend/app/services/
tests/
tests/fixtures/text_to_sql/baselines/
docs/evaluation/
```

不要污染生产 Prompt。

Gate 至少包含：

```text
Prompt version
Prompt hash
Regression dataset hash
Ground truth hash
Fixture hash

Normal T2S compatibility
Validator compatibility
Executor safety boundary
Project isolation
Refusal handling
API refusal handling
Historical regression
```

结果使用：

```text
PASS
BLOCKED
NOT_APPLICABLE
```

不要使用主观评分。

---

# 八、Promotion Gate 判定规则

只有满足：

```text
Normal T2S compatibility = PASS
Validator safety boundary = PASS
Executor safety boundary = PASS
Project isolation = PASS
Historical regression = PASS
Refusal handling = PASS
API refusal handling = PASS
```

才能：

```text
PROMOTION_STATUS = READY
```

否则：

```text
PROMOTION_STATUS = NOT_READY
```

并列出明确 blocker。

特别注意：

如果 refusal 当前只是：

```text
EMPTY_SQL → retry exhausted
```

则：

```text
PROMOTION_STATUS = NOT_READY
BLOCKER = REFUSAL_HANDLING_NOT_SUPPORTED_BY_PRODUCTION_INTERFACE
```

不要为了得到 READY 修改生产代码。

---

# 九、Phase 3.9.24 不做 v2 正式上线

即使所有检查通过，本阶段也只输出：

```text
READY_FOR_PROMOTION
```

不要自动修改：

```text
production prompt = v2
```

真正的 production promotion 放到下一阶段。

---

# 十、测试要求

执行：

```text
pytest
```

以及 Phase 3.9.24 新增测试。

如果需要 DB：

```text
RUN_DB_TESTS=1
```

Windows PowerShell 使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

不要使用：

```powershell
RUN_DB_TESTS=1 pytest
```

---

# 十一、最终必须输出

完成后只报告：

## Phase 3.9.24 Result

### 1. Promotion Status

```text
READY / NOT_READY
```

### 2. Gate Matrix

| Gate                      | Result       |
| ------------------------- | ------------ |
| Normal T2S compatibility  | PASS/BLOCKED |
| Validator safety boundary | PASS/BLOCKED |
| Executor safety boundary  | PASS/BLOCKED |
| Project isolation         | PASS/BLOCKED |
| Historical regression     | PASS/BLOCKED |
| Refusal handling          | PASS/BLOCKED |
| API refusal handling      | PASS/BLOCKED |

### 3. Critical Finding

特别说明：

```text
v2 refusal
→ production TextToSQLService
→ 实际发生了什么
```

### 4. Blockers

如果存在，逐项列出。

### 5. Tests

报告：

```text
pytest:
compileall:
new tests:
```

### 6. Files Changed

只列实际修改的文件。

---

# 十二、停止规则

完成 Phase 3.9.24 后：

**立即停止。**

不要：

* 自动进入 Phase 3.9.25
* 自动修改生产 Prompt
* 自动修复 blocker
* 自动继续 Prompt 优化
* 自动进行新的 A/B 实验

等待下一步指令。
