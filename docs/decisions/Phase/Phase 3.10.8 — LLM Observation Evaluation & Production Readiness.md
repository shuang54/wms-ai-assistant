# Phase 3.10.8 — LLM Observation Evaluation & Production Readiness

## 一、阶段目标

验证 Phase 3.10.7 已接入的 `LLMObservation`：

```text
LLM Client
    ↓
Observation Sink
    ↓
Business Service
```

在真实 AI Core 业务链路中是否满足：

1. Observation 能正确产生
2. Observation 不影响业务结果
3. RAG / Tool / Text-to-SQL / Router / Orchestrator 链路不会出现重复或遗漏
4. 多次 LLM 调用能够正确对应多个 Observation
5. T2S semantic retry 能正确产生 request-level Observation
6. Refusal 仍然只产生一次 LLM Observation
7. Observation 不泄漏敏感信息
8. 当前设计具备进入后续生产可观测性阶段的基础

**本阶段不增加新的生产基础设施。**

---

# 二、必须先阅读

先阅读实际代码：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/observability.py

backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator.py

backend/app/api/
tests/

docs/architecture.md
```

搜索：

```text
create_llm_client
LLMObservationSink
observation_sink
LLMObservation
TextToSQL
RAG
Tool
Router
Orchestrator
```

必须先确认实际调用关系。

**不要根据任务书猜测调用链。**

---

# 三、核心验证模型

本阶段把 Observation 分成：

```text
Request-level Observation
```

而不是：

```text
Business-operation Observation
```

例如：

```text
一次 T2S 请求
    ↓
LLM request #1
    ↓
Validator rejection
    ↓
LLM request #2
    ↓
valid SQL
    ↓
Executor
```

应该得到：

```text
Observation #1
Observation #2
```

而不是：

```text
Observation #1 = 整个 T2S operation
```

因此：

> 一个实际 LLM Provider request 对应一个 Observation。

---

# 四、验证 AI Core 业务链路

至少覆盖以下四类：

## 1. RAG

```text
User
 ↓
RAG
 ↓
LLM
 ↓
Answer
```

验证：

```text
1 LLM request
1 Observation
success=True
```

如果实际调用链存在多个 LLM request，则按实际 request 数量验证。

---

## 2. Tool Calling

例如：

```text
User
 ↓
LLM
 ↓
Tool Call
 ↓
Tool result
 ↓
LLM
 ↓
Final Answer
```

如果实际架构是两次 LLM request：

```text
Observation #1
Observation #2
```

必须确认：

```text
Observation #1 ≠ Observation #2
```

并且：

```text
tool_calls
finish_reason
model
usage
request_id
```

均来自各自 request。

---

## 3. Text-to-SQL

至少验证：

```text
User
 ↓
T2S
 ↓
LLM
 ↓
SQL
 ↓
Validator
 ↓
Executor
```

正常一次生成：

```text
1 LLM request
1 Observation
```

如果发生 semantic retry：

```text
LLM request #1
 ↓
Validator rejection
 ↓
LLM request #2
 ↓
valid SQL
```

应该：

```text
2 Observations
```

不能只有最后一次。

---

## 4. Refusal

例如 destructive request：

```text
删除库存
```

验证：

```text
LLM calls = 1
Observation count = 1
Validator = 0
Executor = 0
```

并且：

```text
observation.success = True
```

因为 LLM 成功产生 refusal 是正常业务结果。

---

# 五、Router 验证

重点验证：

```text
AI Router
```

不会因为 Observation 引入额外 LLM request。

分别验证：

```text
RAG question
Tool question
Text-to-SQL question
Refusal question
```

确认：

```text
Router classification
```

不会因为 Observation Sink 改变。

尤其不要修改：

```text
routing prompt
routing rules
confidence threshold
```

---

# 六、Orchestrator 验证

检查：

```text
AIOrchestrator
```

是否可能产生：

```text
LLM call
    ↓
Observation
    ↓
Business logic
    ↓
LLM call
```

建立 request count 与 observation count 的关系：

```text
observation_count == actual_llm_request_count
```

在没有异常/取消/特殊生命周期情况下必须成立。

---

# 七、建立 Test Observation Sink

如果当前测试中已经存在类似：

```text
CollectingObservationSink
RecordingObservationSink
```

优先复用。

如果没有，新增一个**仅测试使用**的简单 sink：

```python
class CollectingObservationSink:
    observations: list[LLMObservation]
```

要求：

* 不进入生产代码
* 不进入业务服务
* 不持久化
* 不发送网络
* 只用于断言

---

# 八、建立统一 Observation Assertions

避免每个测试重复大量断言。

可以增加测试 helper，例如：

```text
assert_observation_valid(...)
assert_observation_matches_response(...)
```

但必须保持测试代码简单。

至少检查：

```text
success
latency_ms
model
provider
usage
finish_reason
request_id
error_type
```

不要创建大型测试框架。

---

# 九、业务链路计数测试

新增测试：

```text
tests/test_llm_observation_integration.py
```

如果项目已有合适测试文件，也可以扩展现有测试。

至少覆盖：

### Test 1 — RAG

```text
RAG → LLM
```

断言：

```text
answer == expected
observation_count == actual_request_count
```

---

### Test 2 — Tool Calling

```text
LLM → Tool → LLM
```

断言：

```text
tool result 正常
final answer 正常
observation_count == 2
```

如果实际架构只有一次 LLM request，则按实际实现断言。

---

### Test 3 — T2S normal

```text
T2S → LLM → Validator → Executor
```

断言：

```text
observation_count == 1
```

---

### Test 4 — T2S semantic retry

构造：

```text
first SQL invalid
second SQL valid
```

断言：

```text
observation_count == 2
```

并确认：

```text
observation[0].request_id
!=
observation[1].request_id
```

如果测试 fake provider 不提供 request_id，则不要强行伪造，使用其它 request-level distinction。

---

### Test 5 — Refusal

断言：

```text
observation_count == 1
validator_count == 0
executor_count == 0
```

---

### Test 6 — Router

验证四类请求的 Observation 数量与实际 LLM request 一致。

---

### Test 7 — Orchestrator

验证：

```text
actual_llm_requests
==
observations
```

---

# 十、失败链路

至少验证：

```text
LLMRequestError
```

发生在真实 AI Core service 调用过程中。

要求：

```text
business exception preserved
observation.success == False
observation.error_type == "LLMRequestError"
```

并且：

```text
Observation sink failure
```

不会覆盖原始业务异常。

---

# 十一、业务 Retry 验证

特别检查：

```text
Text-to-SQL semantic retry
```

Observation 是：

```text
request-level
```

而不是：

```text
retry-level
```

例如：

```text
attempt 1 → Observation 1
attempt 2 → Observation 2
```

不要新增：

```text
retry_count
```

到 `LLMObservation`。

不要修改 3.10.6 DTO。

---

# 十二、敏感信息检查

必须确认 Observation 中不存在：

```text
user prompt
system prompt
messages
SQL
RAG chunks
tool arguments
tool results
API key
Authorization
headers
database URL
password
raw provider response
exception message
exception stack
```

特别注意：

```text
error_type
```

只能是：

```text
LLMRequestError
TimeoutError
...
```

不能包含：

```text
error message
URL
API key
request payload
```

---

# 十三、No-op Sink 验证

生产默认：

```text
observation_sink=None
```

必须继续：

```text
NoopObservationSink
```

验证：

```text
business result unchanged
```

即：

```text
有 sink
```

与：

```text
无 sink
```

业务结果一致。

---

# 十四、Sink 行为一致性

验证：

```text
same request
```

使用：

```text
NoopObservationSink
```

和：

```text
CollectingObservationSink
```

业务结果：

```text
== 
```

不能因为 sink 存在而：

* 改变返回值
* 改变异常
* 改变 retry
* 改变 prompt
* 改变 routing
* 改变 SQL
* 改变 RAG retrieval

---

# 十五、Concurrency Regression

Phase 3.10.7 已经验证 Client 层并发安全。

本阶段只需要增加一个更接近业务层的验证：

```text
parallel business requests
```

例如：

```text
5 concurrent LLM requests
```

确认：

```text
5 observations
```

且没有：

```text
request_id 交叉
model 交叉
usage 交叉
success 状态交叉
```

不要新增并发框架。

---

# 十六、不要修改生产业务逻辑

本阶段原则：

```text
tests first
```

尽量只修改：

```text
tests/
docs/
```

如果为了测试必须修改生产代码：

1. 先说明为什么必须修改
2. 保持最小修改
3. 不修改业务语义

禁止修改：

```text
Prompt
Validator
Executor
Router strategy
Project Context
T2S semantics
RAG retrieval
Tool definitions
```

---

# 十七、不要引入新基础设施

严格禁止：

```text
OpenTelemetry
Prometheus
Grafana
Langfuse
Sentry
Redis
Kafka
database persistence
metrics exporter
trace exporter
cost tracking
billing
dashboard
```

本阶段只做：

```text
evaluation
regression
production readiness
```

---

# 十八、文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.8 LLM Observation Evaluation
```

内容说明：

```text
Observation 是 request-level telemetry DTO。

1 actual LLM request
    →
1 Observation
```

并记录：

```text
RAG
Tool Calling
Text-to-SQL
Refusal
Router
Orchestrator
```

的验证原则。

明确：

> 当前 Observation 仍然不持久化、不外发、不做 cost calculation。

---

# 十九、测试命令

先运行：

```powershell
python -m pytest tests/test_llm_observation_integration.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_observability.py -q
```

然后完整：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

最后 DB regression：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 二十、网络要求

新增测试：

```text
LLM network = 0
```

使用：

```text
Fake Provider
MockTransport
in-memory sink
```

不要调用真实 DeepSeek。

不要调用真实 SiliconFlow。

---

# 二十一、回归基线

Phase 3.10.7 基线：

```text
pytest:
2036 passed
267 skipped
0 failed

DB:
2262 passed
41 skipped
0 failed
```

本阶段完成后：

```text
默认 pytest 不得出现已有 regression failure。
```

新增测试数量可以增加，但旧测试不得减少。

如果出现失败：

```text
NOT_READY
```

不要通过修改旧测试来解决。

---

# 二十二、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

```text
□ 无 Prompt 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 Router 策略修改
□ 无 Project Context 修改
□ 无 Ground Truth 修改
□ 无新依赖
□ 无 migration
□ 无 secrets
□ 无业务逻辑重构
```

---

# 二十三、最终报告格式

完成后只输出：

```text
Phase 3.10.8 完成报告

1. 修改文件

2. RAG Observation 验证

3. Tool Calling Observation 验证

4. Text-to-SQL Observation 验证

5. T2S Semantic Retry 验证

6. Refusal Observation 验证

7. Router 验证

8. Orchestrator 验证

9. Failure Observation 验证

10. Sink Isolation 验证

11. Concurrency 验证

12. Security / Leakage 验证

13. Tests

14. Regression

15. Network / DB

16. Git Diff

17. 未完成项

Phase 3.10.8 READY

Phase 3.10.8 STOP
```

如果任何核心验证失败：

```text
Phase 3.10.8 NOT READY
```

---

# 二十四、最终验收标准

必须全部满足：

```text
□ RAG Observation 正常
□ Tool Calling Observation 正常
□ T2S Observation 正常
□ T2S semantic retry 每个实际 LLM request 都有 Observation
□ Refusal 只有一个 Observation
□ Router 不产生额外 Observation
□ Orchestrator request/observation 数量一致
□ Failure Observation 正常
□ 原始异常保持
□ Sink failure 不影响业务
□ No-op sink 与 collecting sink 业务结果一致
□ 并发无 Observation 交叉
□ 无 prompt 泄漏
□ 无 SQL 泄漏
□ 无 RAG chunk 泄漏
□ 无 tool argument 泄漏
□ 无 secrets 泄漏
□ 无持久化
□ 无外部 telemetry
□ 无 cost tracking
□ 默认 pytest 通过
□ DB regression 通过
□ compileall 通过
□ Git diff 干净
```

**完成后立即 STOP。**

不要自动进入 Phase 3.10.9。
