你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.7.10：真实业务端到端 Demo / Evaluation

## 一、阶段目标

本阶段不新增 Agent、MCP、LangGraph、Memory、Planning 等高级能力。

唯一目标：

**验证 Phase 3.7.9 AI Orchestrator 是否能够把真实业务问题完整地路由并执行到底。**

需要形成三个可重复运行的端到端场景：

```text
场景 A：业务知识问题
Question
  ↓
AIOrchestrator
  ↓
Router
  ↓
RAG
  ↓
Answer
```

```text
场景 B：固定业务能力
Question
  ↓
AIOrchestrator
  ↓
Router
  ↓
Tool
  ↓
Tool Result
```

```text
场景 C：数据分析问题
Question
  ↓
AIOrchestrator
  ↓
Router
  ↓
RelevantTableSelector
  ↓
DatabaseContextComposer
  ↓
TextToSQL
  ↓
SQLValidator
  ↓
ReadOnlySQLExecutor
  ↓
Database Result
```

最终证明：

**同一个 AI Orchestrator 可以根据问题选择不同能力，并且三条路径互不越权。**

---

# 二、开始编码前必须先阅读

不要假设现有接口。

先阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/rag_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/sql_executor_service.py
backend/app/services/relevant_table_selector.py
backend/app/projects/
backend/app/api/
tests/
```

同时检查：

```text
docs/requirements.md
docs/architecture.md
docs/decisions/
```

尤其确认：

1. 当前 RAG Service 如何调用。
2. 当前 Tool Registry 如何注册 Tool。
3. 当前 Tool 的真实参数 schema。
4. 当前数据库测试如何创建/清理测试数据。
5. 当前默认 ProjectContext 是什么。
6. 当前 Business Semantic YAML 有哪些真实业务表。
7. 当前 Knowledge Base 中有哪些真实 WMS 知识。
8. 当前测试是否已经存在可复用的 Fake LLM / Fake Tool / Fake RAG。

**优先复用已有测试基础设施。**

不要重新造 Fake。

---

# 三、严格范围

本阶段：

## 允许

* 新增 evaluation / demo 测试
* 新增少量测试辅助代码
* 新增 evaluation 文档
* 必要时增加少量测试 fixture
* 使用真实 PostgreSQL 做集成验证
* 使用 Fake LLM 验证确定性链路
* 如果现有项目已有安全的真实 LLM smoke 机制，可以增加可选 smoke test，但默认不得调用真实 LLM

## 禁止

不要修改：

```text
AI Router 核心行为
AI Orchestrator 核心行为
RAG 核心算法
Tool Framework 核心实现
Text-to-SQL Generator
SQL Validator
SQL Executor
RelevantTableSelector
Business Semantic
Database Schema Explorer
```

除非测试发现明确的真实 bug。

如果发现 bug：

**先停止并报告，不要为了通过测试而随意修改核心代码。**

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
Loop
自主重规划
多步 Tool Calling
```

禁止修改数据库结构。

禁止增加 SQL 写操作。

禁止把真实 WMS 数据导入测试数据库。

---

# 四、测试目标

创建：

```text
tests/test_ai_e2e_evaluation.py
```

如果项目已有更合适的 evaluation 目录，则遵循现有结构。

测试至少覆盖：

```text
RAG
Tool
Text-to-SQL
```

并且全部通过：

```text
AIOrchestratorService
```

不能直接调用底层 Service 作为最终测试入口。

---

# 五、场景 A：真实知识库 RAG

使用当前项目已经存在的 WMS Knowledge Base。

测试问题优先选择已经存在知识文档明确覆盖的问题。

例如：

```text
采购入库怎么操作？
```

但不要假设该问题一定存在。

先检查当前 Knowledge Base。

选择一个：

**能够通过当前知识库稳定回答的问题。**

测试流程：

```text
Question
 ↓
AIOrchestrator.execute()
 ↓
RouteDecision = RAG
 ↓
RagService
 ↓
Knowledge Base
 ↓
Answer
```

验证：

1. route == `RAG`
2. content 非空
3. 没有调用 Tool
4. 没有调用 Text-to-SQL
5. 没有调用 SQL Executor
6. 返回结果可以被稳定断言

不要对完整自然语言答案做脆弱的全文字符串断言。

优先断言：

* route
* content 非空
* 必要关键词存在
* metadata 中必要字段存在

---

# 六、场景 B：真实 Tool Framework

检查当前 Tool Registry。

选择一个：

**当前已经存在且不产生写操作的 Tool。**

例如查询类 Tool。

不要为了测试强行新增一个复杂业务 Tool。

测试：

```text
Question
 ↓
AIOrchestrator
 ↓
Router
 ↓
TOOL
 ↓
ToolRegistry
 ↓
Existing Tool
 ↓
ToolResult
```

验证：

1. route == `TOOL`
2. Tool 被执行一次
3. RAG = 0
4. Text-to-SQL = 0
5. SQL Executor = 0
6. Tool 参数经过现有 schema 校验
7. Tool 结果正确进入 `AIOrchestrationResult`

如果当前 Tool Registry 没有适合的安全只读 Tool：

**不要创建复杂 Tool。**

可以使用现有测试 Tool / Fake Tool 完成 Orchestrator 集成验证，并在 evaluation 文档中记录：

```text
当前项目缺少可用于真实业务 E2E 的只读 Tool。
```

---

# 七、场景 C：真实 PostgreSQL Text-to-SQL

这是本阶段最重要的测试。

必须使用：

```text
AIOrchestrator
```

作为入口。

完整链路：

```text
Question
 ↓
AIOrchestrator
 ↓
Router
 ↓
TEXT_TO_SQL
 ↓
ProjectContext
 ↓
Schema
 ↓
Business Semantic
 ↓
RelevantTableSelector
 ↓
DatabaseContextComposer
 ↓
TextToSQLService
 ↓
SQLValidator
 ↓
SQLExecutor
 ↓
PostgreSQL
 ↓
AIOrchestrationResult
```

---

# 八、Text-to-SQL 测试数据

**不要修改生产数据。**

如果当前测试数据库已有：

```text
knowledge_document
knowledge_chunk
```

可以优先选择这些表进行只读查询。

例如：

```text
当前知识库有多少篇文档？
```

或者：

```text
当前知识库中有哪些文档？
```

但必须根据当前真实 schema 和 semantic 选择能够稳定生成 SQL 的问题。

如果现有测试数据库没有足够的数据：

可以在测试 fixture 中：

```text
BEGIN
 ↓
INSERT 测试数据
 ↓
测试
 ↓
ROLLBACK
```

但：

**不得向开发环境/生产环境数据库写入测试数据。**

如果当前项目测试基础设施已经有 seed fixture，优先复用。

---

# 九、Text-to-SQL 测试 LLM 策略

默认：

**不得调用真实 DeepSeek。**

使用 Fake LLM。

Fake LLM 必须返回符合当前真实 schema 的 SQL。

例如：

```sql
SELECT COUNT(*) AS document_count
FROM public.knowledge_document
LIMIT 1000
```

注意：

Fake SQL 必须经过真实：

```text
SQLValidator
```

以及真实：

```text
SQLExecutor
```

不能 Fake Validator。

不能 Fake Executor。

最终必须是真实 PostgreSQL 执行。

---

# 十、验证 SQL 安全链路

Text-to-SQL E2E 测试至少增加：

## 1. 正常 SELECT

确认：

```text
Generator
 ↓
Validator
 ↓
Executor
 ↓
真实 DB
```

成功。

---

## 2. DELETE

Fake LLM 返回：

```sql
DELETE FROM public.knowledge_document;
```

验证：

```text
Validator reject
```

并且：

**数据库不会发生 DELETE。**

---

## 3. DROP

Fake LLM 返回：

```sql
DROP TABLE public.knowledge_document;
```

验证：

```text
Validator reject
```

数据库不会发生任何写操作。

---

## 4. 多语句

Fake LLM 返回：

```sql
SELECT * FROM public.knowledge_document LIMIT 10;
DROP TABLE public.knowledge_document;
```

验证：

```text
MULTI_STATEMENT
```

并且数据库不会执行。

---

# 十一、三条路线互斥测试

新增一个测试：

```text
test_routes_are_mutually_exclusive
```

验证：

### RAG

```text
RAG = 1
Tool = 0
TextToSQL = 0
Executor = 0
```

### Tool

```text
RAG = 0
Tool = 1
TextToSQL = 0
Executor = 0
```

### Text-to-SQL

```text
RAG = 0
Tool = 0
TextToSQL = 1
Executor = 1
```

确保不存在：

```text
RAG → Tool
Tool → Text-to-SQL
Text-to-SQL → Tool
```

等隐式链路。

---

# 十二、Project Context 可迁移性验证

增加一个轻量测试：

创建第二个 Fake ProjectContext：

```text
project_id = "test-project"
project_name = "Test Project"
```

验证：

Orchestrator 本身不硬编码：

```text
vietnam-wms
Vietnam WMS
```

核心执行逻辑仍然可以接受不同 ProjectContext。

不要真的创建第二个完整数据库。

这里只验证：

**Orchestrator 没有把 WMS 项目写死。**

---

# 十三、结果结构验证

验证：

```python
AIOrchestrationResult
```

满足：

1. frozen / immutable
2. route 正确
3. content/data 类型符合现有设计
4. metadata 不包含：

   * API key
   * password
   * database URL
   * connection string
   * authorization header
5. 不暴露 SQLAlchemy Connection
6. 不暴露内部 DB Session

如果当前 DTO 已经满足，不要修改。

---

# 十四、真实 DB 写入零容忍

测试完成前后：

检查关键表行数。

例如：

```text
knowledge_document
knowledge_chunk
```

测试前：

```text
count_before
```

测试后：

```text
count_after
```

必须：

```text
count_before == count_after
```

如果测试数据库有专门的事务 rollback 机制，优先使用该机制。

最终报告必须写：

```text
DB writes = 0
```

---

# 十五、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.7.10 — E2E Evaluation.md
```

记录：

## 1. 测试环境

例如：

```text
Python
PostgreSQL
pgvector
Fake LLM
```

## 2. RAG Case

记录：

```text
Question
Expected Route
Actual Route
Result
```

## 3. Tool Case

同上。

## 4. Text-to-SQL Case

记录：

```text
Question
Selected Tables
Generated SQL
Validation Result
Execution Result
```

SQL 可以记录测试 SQL。

不要记录：

```text
API Key
password
database URL
credentials
```

## 5. Security Cases

记录：

```text
DELETE → rejected
DROP → rejected
Multi-statement → rejected
```

## 6. Final Result

记录：

```text
RAG = PASS
Tool = PASS
Text-to-SQL = PASS
Security = PASS
DB writes = 0
```

---

# 十六、不要把 Evaluation 做成 Benchmark 平台

本阶段只需要：

**3～5 个高质量 E2E Case。**

不要实现：

```text
复杂 benchmark framework
自动评分平台
Web UI
数据标注系统
Prompt optimization platform
```

不要过度工程化。

---

# 十七、真实 LLM Smoke

如果项目已有真实 LLM smoke test：

可以保留为：

```text
@pytest.mark.real_llm
```

或者当前项目使用的等价机制。

要求：

默认：

```text
SKIP
```

不要让：

```bash
pytest -q
```

调用 DeepSeek。

如果当前项目没有可靠的 real LLM test infrastructure：

**不要新增。**

---

# 十八、性能记录

不需要复杂性能优化。

只记录：

```text
RAG total latency
Tool total latency
Text-to-SQL total latency
```

如果当前服务已经返回 execution time，则复用。

否则不要修改核心 Service 只为了增加计时。

---

# 十九、测试命令

先执行：

```bash
pytest -q tests/test_ai_e2e_evaluation.py
```

然后：

```bash
pytest -q
```

如果项目存在 DB Integration：

```bash
RUN_DB_TESTS=1 pytest -q
```

按照项目当前实际测试启动方式执行。

同时检查：

```text
compileall
LSP
lint
```

要求：

```text
0 failed
0 diagnostics
```

---

# 二十、失败处理规则

如果发现：

```text
RAG E2E failure
Tool E2E failure
Text-to-SQL E2E failure
```

不要立即修改核心架构。

先定位：

```text
Router
Orchestrator
RAG
Tool
Schema
Semantic
Text-to-SQL
Validator
Executor
```

具体是哪一层出现问题。

如果是现有实现缺陷：

先在最终报告中明确：

```text
发现问题：
影响：
根因：
是否修改：
```

只有能够确认属于本阶段必要修复的问题，才允许最小修改。

---

# 二十一、最终报告格式

完成后严格按照以下格式报告：

```text
【Phase 3.7.10 COMPLETE】

1. 新增文件
2. 修改文件
3. RAG E2E
4. Tool E2E
5. Text-to-SQL E2E
6. Security E2E
7. Project Context 可迁移性
8. 测试结果
9. DB writes
10. Real LLM
11. API 是否修改
12. 发现并修复的问题
13. 当前限制
```

最后给出：

```text
Architecture E2E:

Question
  ↓
AIOrchestrator
  ↓
AI Router
  ├── RAG
  ├── Tool
  └── Text-to-SQL
        ↓
      Validator
        ↓
      Read-only Executor
        ↓
      PostgreSQL
```

然后：

**立即停止。**

不要进入 Phase 3.7.11。

不要开发 Agent。

不要开发 MCP。

不要接入 Chat API。

不要继续扩展功能。
