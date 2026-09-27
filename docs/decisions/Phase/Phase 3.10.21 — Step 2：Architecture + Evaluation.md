# Phase 3.10.21 — Step 2：Architecture + Evaluation

## 一、当前状态

Phase 3.10.21 Step 1 已完成：

```text
Facade = implemented
Facade Tests = 19 passed
Analytics = unchanged
Aggregation = unchanged
Query Runtime = unchanged
DB Access = 0
Network = 0
```

本 Step 不重新实现 Facade。

---

# 二、本 Step 只做两件事

### 1. 修改

```text
docs/architecture.md
```

新增：

```text
§8.21 Usage Analytics Application Read Facade
```

### 2. 新增

```text
docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md
```

---

# 三、禁止修改 Python

本 Step：

```text
Python code = unchanged
```

禁止修改：

```text
backend/app/services/llm_usage_analytics_facade.py
tests/test_llm_usage_analytics_facade.py
```

除非发现文档与真实代码存在明显不一致。

如果发现不一致：

**不要自行修改 Python。**

先报告。

---

# 四、Architecture §8.21

必须根据真实代码记录。

不要照抄任务书假设。

真实 Facade：

```text
LLMUsageAnalyticsReadFacade
```

真实核心入口：

```python
async query_snapshot(query_filter=None)
```

真实组合：

```text
LLMUsageQueryRuntime
        ↓
list_records_async(...)
        ↓
records
        ↓
LLMUsageAnalyticsService.snapshot(...)
        ↓
LLMUsageAnalyticsSnapshot
```

---

# 五、明确四层职责

Architecture 中明确：

```text
Query Repository
    ↓
负责数据库读取

Query Service
    ↓
负责 Query Contract / Filter / Pagination

Query Runtime
    ↓
负责 async → sync DB boundary

Analytics Service
    ↓
负责 Analytics / Aggregation composition

Analytics Read Facade
    ↓
负责 Application Read Boundary
```

Facade：

**不负责数据库访问。**

---

# 六、明确 Facade 依赖边界

允许：

```text
Facade
 ↓
LLMUsageQueryRuntime
 ↓
LLMUsageAnalyticsService
```

禁止：

```text
Facade
 ↓
Repository
 ↓
SQLAlchemy Session
```

Facade 不直接使用：

```text
SQL
Session
Engine
Connection
PostgreSQL
```

---

# 七、Snapshot 语义

必须记录：

```text
query_snapshot()
```

只执行：

```text
Query Runtime = 1 次
```

然后：

```text
records
 ↓
Analytics.snapshot()
 ↓
LLMUsageAnalyticsSnapshot
```

Snapshot 包含：

```text
total
by_provider
by_model
by_provider_model
```

不要写成四次数据库查询。

---

# 八、Pagination 语义

必须明确：

Facade 的 Analytics 是：

```text
当前 Query Filter 对应记录集合
```

的 Analytics。

例如：

```text
limit = 100
```

表示：

```text
Analytics = 当前查询得到的 100 条记录
```

不是：

```text
整个数据库全局 Analytics
```

本阶段没有：

```text
COUNT(*)
global_total
total_count
```

---

# 九、便捷方法

当前真实实现还存在：

```text
query_summary()
query_by_provider()
query_by_model()
query_by_provider_model()
```

Architecture 可以记录这些方法。

但明确：

它们属于：

```text
convenience read methods
```

完整 Analytics 推荐：

```text
query_snapshot()
```

因为 Snapshot 可以一次 Query 得到完整四视图。

---

# 十、错误传播

Architecture 记录：

```text
Runtime error
    ↓
Facade
    ↓
caller
```

以及：

```text
Analytics error
    ↓
Facade
    ↓
caller
```

Facade 不吞异常。

不转换成 None。

---

# 十一、Immutability

记录：

```text
LLMUsageAnalyticsSnapshot = frozen
```

Facade 不重新构造：

```text
dict
list
```

保持已有 DTO：

```text
LLMUsageAnalyticsAggregate
ProviderUsageAggregate
ModelUsageAggregate
ProviderModelUsageAggregate
LLMUsageAnalyticsSnapshot
```

具体名称必须以真实代码为准。

---

# 十二、Security Boundary

Architecture 必须明确 Facade 返回的数据仍然不能包含：

```text
API Key
Password
Database URL
Connection String
Authorization Header
SQLAlchemy Session
Connection
Engine
Raw SQL
```

Facade 只是 application read boundary。

---

# 十三、Evaluation 文档

创建：

```text
docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md
```

建议结构：

```text
# Phase 3.10.21 — Usage Analytics Read Facade

## 1. Scope

## 2. Existing Implementation

## 3. Application Read Boundary

## 4. Query → Analytics Composition

## 5. Snapshot

## 6. Pagination Semantics

## 7. Convenience Methods

## 8. Error Propagation

## 9. Immutability

## 10. Security Boundary

## 11. Tests

## 12. DB / Network

## 13. Files

## 14. Limitations
```

---

# 十四、Tests 部分记录真实结果

记录：

```text
Facade tests:
19 passed, 0 failed

Facade + Analytics + Aggregation:
73 passed, 0 failed
```

不要写不存在的测试结果。

---

# 十五、文件来源说明

Evaluation 中可以客观记录：

```text
本阶段开始时发现 Facade implementation 与其测试文件已经存在于工作区。
本 Step 未重新生成或修改 Python 文件，仅基于真实代码进行验证与文档化。
```

不要推测这些文件是谁生成的。

---

# 十六、DB / Network

记录：

```text
DB Access = 0
DB writes = 0
Network = 0
```

并明确：

Step 1 使用 Fake / Stub Runtime 进行 Application Composition 验证。

没有：

```text
PostgreSQL
SQLAlchemy Session
Repository
LLM API
HTTP
```

---

# 十七、Current Limitations

必须写：

```text
1. 尚无 HTTP API
2. 尚无 Dashboard
3. 尚无 Billing / Cost
4. 尚无 Cache
5. 尚无 Queue / Worker
6. Facade 仍然是内部 Application Boundary
7. Pagination Analytics 代表当前查询页，而非全局 Analytics
```

---

# 十八、Architecture 链

§8.21 最后增加：

```text
LLM Request
  ↓
Observation
  ↓
Accounting
  ↓
Persistence
  ↓
Idempotency
  ↓
PostgreSQL
  ↓
Query Repository
  ↓
Query Service
  ↓
Query Runtime
  ↓
LLMUsageRecordView
  ↓
Usage Aggregation
  ↓
Usage Analytics
  ↓
Usage Analytics Read Facade
  ↓
Future API / Dashboard / Admin / AI Ops
```

注意：

最后的：

```text
Future API / Dashboard / Admin / AI Ops
```

只是未来消费者。

**本阶段没有实现它们。**

---

# 十九、完成检查

执行：

```powershell
cd D:\coding\ai\wms-ai-assistant

Select-String `
  -Path docs/architecture.md `
  -Pattern '^## 8.21'
```

然后：

```powershell
Test-Path "docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md"
```

最后：

```powershell
git status --short
```

确认：

```text
architecture.md
evaluation doc
```

存在。

Python 文件没有新的修改。

---

# 二十、本 Step 不执行

不要执行：

```text
pytest -q
RUN_DB_TESTS=1
compileall
LSP
完整 Git diff
```

这些留到 Step 3。

---

# 二十一、最终报告

完成后只报告：

```text
Step 2 COMPLETE

Architecture §8.21 = created
Evaluation = created
Python code = unchanged

Facade Tests = 19 passed
Composition Tests = 73 passed

DB Access = 0
Network = 0
```

然后：

**立即停止。**

不要进入 Step 3。
不要进入 Phase 3.10.22。
