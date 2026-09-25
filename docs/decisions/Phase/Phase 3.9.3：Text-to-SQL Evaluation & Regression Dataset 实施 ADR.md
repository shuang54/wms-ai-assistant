# Phase 3.9.3 实施 ADR：Text-to-SQL Evaluation & Regression Dataset

> 本文件记录 Phase 3.9.3 的实施结论（侦察发现、设计取舍、边界决策与回归结果）。
> 阶段任务书见同目录 `Phase 3.9.3：Text-to-SQL Evaluation & Regression Dataset.md`。

---

## 一、侦察发现

| 项 | 结论 |
| --- | --- |
| Generator 契约 | `generate(question, *, database_context, allowed_tables, schema, max_rows) -> TextToSQLResult`，本阶段零改动 |
| Validator | `SQLValidatorService.validate()` 基于 sqlglot AST，只读 + LIMIT + 白名单三重约束 |
| Executor | `SQLExecutorService.execute()` 内部已 **re-validate**（TOCTOU 防护） |
| 上下文 | Phase 3.9.1 `TextToSQLContext`（文本三层，不持有 DatabaseSchema） |
| 数据集惯例 | `tests/fixtures/rag/evaluation_cases.json`：`_schema_doc` / `_schema_version` / `_fields` 自描述块 |
| 工程现状 | 无 `conftest.py`；fixture 逐文件内联；`asyncio_mode=auto`；YAML 加载参考 `ProjectSemanticLoader`（三层异常 + 未知键拒绝 + 重复检测 + tuple 输出） |
| 依赖 | PyYAML、sqlglot 已在 `requirements.txt`；**本阶段零新增依赖** |
| 真实 DB schema | 只有 `public.knowledge_document` / `public.knowledge_chunk`（含 FK `chunk.document_id → document.id` 与 `created_at/updated_at`）；WMS 业务表不存在，仅存在于测试 fixture |

---

## 二、核心设计取舍

### 2.1 组成形态

```text
Dataset YAML（tests/fixtures/text_to_sql/）
    ↓ loader（纯读取 + 结构校验）
TextToSQLEvaluationCase
    ↓ EvaluationContextResolver（注入）
TextToSQLEvaluationInput（既有 TextToSQLContext + DatabaseSchema）
    ↓ TextToSQLGenerator（契约不变）
Generated SQL
    ↓ Runner 自己再跑一次 SQLValidator   ← 绝不信任 Generator
TextToSQLEvaluationResult / Summary（含文本报告）
```

- **给既有 `TextToSQLContext` 增加 `schema` 字段会破坏 3.9.1 的"不重复保存重对象"约定**，
  因此新增 `TextToSQLEvaluationInput(context, schema=None)` 来并列承载两者。
- Resolver 有 `EvaluationContextResolver` Protocol（支持 sync / awaitable），
  并提供 `StaticEvaluationContextResolver`——内部调用既有
  `RelevantTableSelector → SemanticSchemaFilter → DatabaseContextComposer`，
  即"真实链路、静态绑定"，不查注册表、不调 LLM。

### 2.2 绝不绕过 Validator

即使 Fake Generator 返回 `validated=True` 的非法 SQL，Runner 也一定用注入的
`SQLValidator` 重新校验；被测对象是链路，安全边界始终在 Validator + Executor。

### 2.3 结构期望 ≠ 精确 SQL

期望项：`must_pass_validation` / `must_contain_tables` / `must_contain_columns` /
`must_not_contain` / `must_execute`（任务书 §六）。
table / column 判定走 **sqlglot AST**，并对合法写法做规范化以避免假回归：

| 合法写法 | 处理 |
| --- | --- |
| `FROM inventory i … i.qty` | alias → 物理表解析 |
| `FROM inventory`（无 schema 前缀） | 用 DatabaseSchema 唯一解析后补齐 `schema.table` |
| `SELECT qty FROM inventory`（裸列名） | 只关联"本次引用过 + Schema 确有该列"的表 |
| 多 schema 同名表 | 不猜，只保留裸名（保守原则） |
| `WHERE code = 'fake_column'` | AST 判定 → 字面量不算命中 |

### 2.4 默认数据集零数据库依赖

Dataset **不设置任何 `must_execute`**（有测试锁定），因此全量 pytest 不需要 DB。
DB 测试按需 `with_execution_required(case)`（返回副本，不改原 case）。

### 2.5 确定性

- 结果 tuple 顺序 = dataset 顺序；失败也 nore-Python-set 无序遍历；
- `duration_ms` 用 `field(compare=False)` 排除在 `__eq__` 之外（任务书 §十四）；
- Loader 输出 tuple，调试两次加载结果相同（有测试锁定）。

### 2.6 场景适配（不虚构字段）

任务书示例场景用 inventory 设计，但**当前真实库没有 WMS 业务表**。
第一版 dataset 因此这样落地（§五"如果当前 Schema 不支持某个场景，不要虚构字段"）：

- 12 个主场景 = 真实 `knowledge_document` / `knowledge_chunk`
  （含 JOIN 走现有 FK、日期条件走 `created_at`、Semantic-dependent 走自身业务名）；
- Project A/B 隔离 = 沿用既有 `project_a` / `project_b` fixture 的 `inventory` 表。

---

## 三、新增 / 修改文件

```text
新增 backend/app/services/text_to_sql_evaluation_service.py
    DTO：TextToSQLExpectations / TextToSQLEvaluationCase /
         TextToSQLEvaluationInput / TextToSQLExecutionOutcome /
         TextToSQLEvaluationResult（duration_ms 不参与相等性）/
         TextToSQLEvaluationSummary.render_text()
    Loader：load_text_to_sql_regression_dataset()
            + DatasetNotFound / DatasetConfigError 双层异常
    Checker：check_expectations()（AST 判定）
    Resolver：EvaluationContextResolver Protocol + StaticEvaluationContextResolver
    Runner：TextToSQLEvaluationRunner.run_case() / run()
    辅助：with_execution_required(case)

新增 tests/fixtures/text_to_sql/text_to_sql_regression.yaml（14 cases）
    _schema_doc / _schema_version / _fields 自描述块（对齐 rag 数据集惯例）

新增 tests/test_text_to_sql_evaluation_service.py（44 项）
    40 非 DB + 4 DB-gated

新增 docs/decisions/Phase/Phase 3.9.3：Text-to-SQL Evaluation & Regression Dataset 实施 ADR.md
```

**未修改任何既有文件**：Generator / Validator / Executor / Router / RAG /
Tool Framework / Semantic YAML / Project Configuration / Prompt /
TextToSQLContext / Selector / Composer / SemanticSchemaFilter 全部零改动；
未新增数据库表、未引入 tokenizer、未新增依赖。

---

## 四、完成标准核对

| 项 | 状态 |
| --- | --- |
| Evaluation Dataset 可长期维护（YAML，非硬编码） | ✅ 14 cases + 自描述元数据 |
| ≥ 12 个代表性场景（含安全边界） | ✅ 简单 / Top N / 过滤 / 排序 / 聚合 / GROUP BY / HAVING / JOIN / 日期 / LIMIT / Semantic-dependent / 安全边界 |
| Result DTO（case_id / question / generated_sql / validation / execution / matched / failed / error_code / duration_ms） | ✅ |
| Expectation Checker 独立纯函数 + AST | ✅ `check_expectations()` |
| 不绕过 Validator | ✅ Runner 自己重跑 Validator（有专项测试） |
| DB 执行可选 | ✅ 仅 `must_execute`；dataset 默认全关闭 |
| Project Isolation A/B + 污染检测 | ✅ 3 项非 DB + 1 项真实 DB |
| Fake Generator 确定性单测 | ✅ 覆盖正确 SQL / 错表 / 错列 / 非法 SQL |
| DB 集成（真实 Schema / Validator / Executor） | ✅ 4 项 |
| 确定性（两次运行一致） | ✅ 排除 duration_ms 的相等性测试 |
| 不加 Mock / 桩 polyfill 到生产代码 | ✅ 全部 Fake 在测试文件内 |

---

## 五、回归结果

```text
python -m pytest -q                        → 1405 passed / 245 skipped / 0 failed
$env:RUN_DB_TESTS="1"; python -m pytest -q → 1612 passed /  38 skipped / 0 failed
python -m compileall backend               → OK
lint（新增两个文件）                        → 0 errors
DB residue                                 → knowledge_document / knowledge_chunk = (0, 0)
                                             project_a / project_b schema = 0
```

对比基线：非 DB 1365（3.9.2）→ 1405（+40）；DB 1568 → 1612（+44）。
既有 Phase 3.7.x / 3.8.x / 3.9.1 / 3.9.2 全部测试保持通过，
无既有用例被修改或删除。

---

## 六、已知限制

1. 第一版 dataset 依赖当前真实库的知识库表；WMS 业务表接入后应追加 case
   （仍建议保持结构期望，不要引入精确 SQL 断言）。
2. 未内置调度 / CLI：回归由 pytest 触发；未来需要批量跑多个 Generator /
   多模型对比时再增加上层脚本。
3. 未做模型评分 / 排行榜（任务书明确禁止），只有 pass / fail + 文本报告。
4. `must_not_contain` 对无法解析的 SQL 判失败（保守），不做文本兜底搜索。
5. Resolver 目前只有静态绑定版本；真实项目 registry 接入留作后续扩展点。
6. `must_contain_columns` 的裸列名解析依赖 DatabaseSchema；注入
   `schema=None` 的 Resolver 只能匹配限定列名。
