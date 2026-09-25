# Phase 3.9.1 实施 ADR：Text-to-SQL Context Enhancement

> 本文件记录 Phase 3.9.1 的实施结论（侦察发现、设计取舍与回归结果）。
> 阶段任务书见同目录 `Phase 3.9.1：Text-to-SQL Context Enhancement.md`。

---

## 一、侦察发现

| 项 | 结论 |
| --- | --- |
| Generator 契约 | `generate(question, *, database_context, allowed_tables=None, schema=None, max_rows)` → `TextToSQLResult(question, sql, attempts, validated, referenced_tables)` |
| 既有 Fake Generator | 两处**严格 keyword**签名（`test_ai_orchestrator.py:114`、`test_project_semantic_isolation.py:211`）→ 新增任何 keyword 都会 `TypeError`，直接破坏既有测试 |
| database_context | `DatabaseContextComposer` 单段字符串 = 项目头 + `## Database Schema` + `Business Semantics:`（Schema 与 Semantic 混在一段里，无结构化载体） |
| Semantic | `ProjectSemantic(tables/columns/relationships)`，`BusinessSemanticSerializer` 输出稳定文本；**无 project_id 字段、无自动推断** |
| 选表信息 | `TableSelectionResult(selections=[(table="schema.table", score, matched_terms)])`；Orchestrator 取 `allowed_tables`，top_k=10，零匹配 → `()` |
| Prompt 约束 | system 已含 SELECT only / 禁 DML+DDL+GRANT / 必带 LIMIT / 不编造表列 / 注入防御；缺 "Semantic 与 Schema 优先级" 的显式规则 |
| Retry | 最多 3 次，回灌 previous SQL + 错误码；预算耗尽 → `TextToSQLRetryExceededError`（不携带失败 SQL）；缺 "只改失败部分 / 不许换成非 allowed 表" |

---

## 二、设计决策（关键取舍）

### 2.1 新增 DTO，但**不改** `generate()` 契约

```text
Project Layer → Orchestrator（Application）→ TextToSQLContext.render() → TextToSQLService
```

- `TextToSQLContext`（frozen）在 **Application 层**构造：
  `database_context`（Schema 事实）+ `business_context`（业务语义文本）+
  `allowed_tables` + `max_rows` + `project_id`（仅 tracing）；
- 渲染为结构化单段文本后，用**既有参数**传给 `generate()`。

理由：侦察发现既有 Fake Generator 为严格 keyword 签名，新增 keyword 会直接
破坏 AI Orchestrator / Project Semantic 既有测试（任务书 §十五要求全部保持）。
因此选择"DTO 结构化 + 渲染后走既有契约"，而不是"改契约 + 改测试"。

### 2.2 三层上下文真正分层

| 层 | 载体 | 来源 |
| --- | --- | --- |
| Database Schema | `database_context` | `DatabaseContextComposer`（**改传 `semantic=None`**，只出事实） |
| Business Semantic | `business_context` | `BusinessSemanticSerializer`（独立段，不再混进 Schema 段） |
| SQL Constraints | system / retry Prompt | 模板指令（**不是**安全边界） |

`render()` 纯内存合并；上游已含语义段时不重复追加 → 语义文本不会翻倍。

### 2.3 Service 不依赖 Project

`text_to_sql_context.py` 与 `text_to_sql_service.py` **均不 import**
`ProjectRegistry` / `ProjectConfigurationProvider` / `ProjectSemanticProvider` /
`ProjectKnowledgeProvider`；业务语义以**已序列化文本**进入，
`project_id` 只做 tracing（任务书 §五 / §十一）。

### 2.4 安全边界不变

SQL Constraints 只是 Prompt 指令；`SQLValidator` 仍是硬边界，
`SQLExecutor` 仍是执行边界，两者代码**零修改**、测试零削弱。

---

## 三、修改 / 新增文件

```text
新增 backend/app/services/text_to_sql_context.py
    TextToSQLContext（frozen DTO）+ TextToSQLContextError
    render()：确定性纯函数（零推断、零 fallback、不重复语义段）

修改 backend/app/prompts/text_to_sql_system.txt
    ROLE / HARD SQL CONSTRAINTS（11 条）/ HOW TO USE THE CONTEXT
    （Semantic > raw name，但 Schema 覆盖 Semantic、不猜字段）/ SECURITY
修改 backend/app/prompts/text_to_sql_user.txt
    保留全部既有 marker，新增"只存在 context 内表列 / LIMIT <= max_rows"提醒
修改 backend/app/prompts/text_to_sql_retry.txt
    新增"只修失败部分 / 不许换成非 allowed 表 / 不编造表列"

修改 backend/app/services/ai_orchestrator_service.py（最小）
    新增可选构造参数 semantic_serializer
    _run_text_to_sql：composer 只出 Schema 事实 → 语义单独序列化 →
    TextToSQLContext → render() → generate()（参数不变）

新增 tests/test_text_to_sql_context.py（26 项）
    Case 1-8 / A-B 隔离（非 DB + DB E2E）/ Validator 边界 / 无额外调用

新增 docs/decisions/Phase/Phase 3.9.1：Text-to-SQL Context Enhancement 实施 ADR.md
```

未修改：SQLValidator / SQLExecutor / ProjectRegistry / ProjectConfiguration /
KnowledgeProvider / KnowledgeIngestion / RAG / Embedding / Reranker / Tool /
RelevantTableSelector / SchemaSerializer / BusinessSemanticSerializer /
DatabaseContextComposer / TextToSQLResult / Router。

---

## 四、兼容性要点

1. system prompt 仍以 `You are a PostgreSQL Text-to-SQL generator` 开头，
   含 `SELECT` / `LIMIT` / `Do not invent tables or columns`；
2. user / retry 保留 `【DATABASE CONTEXT】/【ALLOWED TABLES】/【MAX ROWS】/
   【QUESTION】/【PREVIOUS SQL】/【VALIDATION ERRORS】` 全部既有 marker；
3. composer 改传 `semantic=None` 后，生成侧拿到的最终字符串**仍含**语义
   （经 `business_context` 合并）→ 既有"语义必须在 context 中"的断言全部继续成立；
4. `max_attempts=3` 与重试语义完全不变，无新增 LLM / DB / Embedding / Reranker 调用。

---

## 五、回归结果

```text
python -m pytest -q                        → 1334 passed / 239 skipped / 0 failed
$env:RUN_DB_TESTS="1"; python -m pytest -q → 1535 passed /  38 skipped / 0 failed
python -m compileall backend               → OK
lint（修改文件）                            → 0 errors
数据库残留                                  → knowledge_document/chunk = (0, 0)
```

与 Phase 3.8.6 基线对比：非 DB 1309 → 1334（+25）、DB 1509 → 1535（+26），
无既有用例被修改或删除，56 项 Text-to-SQL 测试与 SQLValidator / SQLExecutor /
Router / Orchestrator / Project Configuration 测试全部保持。

---

## 六、已知限制

1. `TextToSQLContext` 由 Application 层构造并渲染为字符串后传入 Generator；
   **Generator 契约未结构化**（受既有 Fake 严格签名约束），
   未来若统一升级 Fake 签名，可再把 DTO 作为 `generate(context=...)` 入参。
2. 业务语义过滤仍依赖上游：`BusinessSemanticSerializer` 会序列化全部配置
   （含 Schema 中不存在的表/列语义），本阶段**不做**语义-Schema 对齐过滤；
   Prompt 已明确"Schema 覆盖 Semantic、不猜字段"。
3. `project_id` 仅进入 DTO 与日志，未写入 `TextToSQLResult`
   （任务书 §十二：现有 result 字段已足够，不堆字段）。
4. Prompt 长度略有增加（约束与提醒更明确），无新增模型调用；
   上下文预算仍由 `DatabaseContextComposer` 的 `max_chars` 控制。
