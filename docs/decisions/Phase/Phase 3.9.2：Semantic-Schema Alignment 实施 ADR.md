# Phase 3.9.2 实施 ADR：Semantic-Schema Alignment

> 本文件记录 Phase 3.9.2 的实施结论（侦察发现、设计取舍、边界决策与回归结果）。
> 阶段任务书见同目录 `Phase 3.9.2：Semantic-Schema Alignment.md`。

---

## 一、侦察发现

| 项 | 结论 |
| --- | --- |
| Semantic DTO | `ProjectSemantic(tables/columns/relationships)`，全部 frozen；引用可为 `schema.table` 或裸表名 |
| Schema DTO | `DatabaseSchema(schema_name, tables)` → `SchemaTable(schema_name, name, description, columns, foreign_keys)` → `SchemaColumn.name` |
| allowed_tables | `RelevantTableSelector` 产出的 `"schema.table"` 元组；零匹配 → `()` |
| Composer | `tables=allowed_tables or None` → 零选中时**降级输出全库 schema** |
| 3.9.1 现状 | `business_context = BusinessSemanticSerializer().serialize(semantic)`，**未做任何对齐过滤** |
| 已有组件 | `SemanticSchemaValidator`（Phase 3.7.3）只"报告"失配，不改对象、不改 Schema |

→ 本阶段新增的是它的**过滤对应物**，不是替代品。

---

## 二、设计决策

### 2.1 新服务：`SemanticSchemaFilter`

```text
ProjectSemantic + DatabaseSchema + allowed_tables
        ↓ SemanticSchemaFilter.filter()
Filtered ProjectSemantic（新的 frozen 对象）
```

- 文件：`backend/app/services/semantic_schema_filter.py`
  （命名对齐既有 `semantic_schema_validator.py`；任务书允许按项目习惯调整，
  测试文件同名 `tests/test_semantic_schema_filter.py`）。
- 只做**减法**：`Schema → 限制 Semantic`；
  禁止 `Semantic → 推断 Schema`（不做同义词推断、不留"可能是 X"提示）。
- 纯内存：不查库、不调 LLM / Embedding / Reranker、不引入 tokenizer。

### 2.2 `allowed_tables` 空序列边界（关键取舍）

```text
None  / ()  → 不按选中范围限制，只按 Schema 存在性过滤
非空        → 只保留"Schema 中存在 且 被选中"的表语义
```

理由：`DatabaseContextComposer` 在零选中时降级为**全库 schema**
（`tables=allowed_tables or None`）。若此时把语义全部过滤，会出现
"Prompt 里是全库 schema，却没有对应业务语义"的不一致；反之若按
"空 = 全部过滤"处理，会破坏既有 3.8.3 DB 隔离测试
（`test_same_question_different_semantics`：同一问题在 project-b
零命中时仍需拿到 B 语义）。选择与 Composer 降级行为**同构**的处理。

### 2.3 引用解析与既有组件同构

`_resolve_table` / `_find_column` 规则与 `SemanticSchemaValidator`、
`SchemaSerializer` 完全一致（精确 `schema.table` 优先，裸表名唯一匹配）。
刻意保持同构实现而非跨模块 import 私有函数；并用交叉测试锁定：
**过滤后的语义必通过 `SemanticSchemaValidator.validate()`**。

---

## 三、修改 / 新增文件

```text
新增 backend/app/services/semantic_schema_filter.py
    SemanticSchemaFilterError / SemanticSchemaFilterInputError
    SemanticSchemaFilter.filter(semantic, schema, *, allowed_tables=None)
    → 新 ProjectSemantic（输入零修改）；稳定排序输出；纯内存；日志计数

修改 backend/app/services/ai_orchestrator_service.py（最小）
    新增可选构造参数 semantic_filter（缺省 SemanticSchemaFilter()）
    _run_text_to_sql：
        RelevantTableSelector
          → DatabaseContextComposer（只出 Schema 事实）
          → SemanticSchemaFilter      ← 本阶段新增
          → BusinessSemanticSerializer
          → TextToSQLContext → generate()（签名未变）

新增 tests/test_semantic_schema_filter.py（33 项）
    Test 1-8 / 交叉一致性 / 确定性 / 纯内存静态检查 /
    Prompt 长度 / Text-to-SQL E2E / DB 对齐

新增 docs/decisions/Phase/Phase 3.9.2：Semantic-Schema Alignment 实施 ADR.md
```

未修改：SQLValidator / SQLExecutor / Router / ProjectRegistry /
ProjectConfiguration / KnowledgeProvider / KnowledgeIngestion / RAG /
Embedding / Reranker / Tool / RelevantTableSelector / SchemaSerializer /
BusinessSemanticSerializer / DatabaseContextComposer / TextToSQLContext /
SemanticSchemaValidator / Semantic YAML / API / Generator 签名。

---

## 四、完成标准核对

| 项 | 状态 |
| --- | --- |
| Semantic 只保留当前 selected tables | ✅ `allowed_tables` 非空时按选中范围裁剪 |
| 不存在的 table 被过滤 | ✅ Test 1 |
| 不存在的 column 被过滤 | ✅ Test 2（含"不推断 name"验证） |
| 非 selected 的合法 table 被过滤 | ✅ Test 3（sales_order 消失） |
| 非法 relationship 被过滤 | ✅ Test 5（表/列不存在、端未选中） |
| 合法 relationship 保留 | ✅ Test 4 |
| 原始 Semantic 不被修改 | ✅ Test 6（含 Schema 不变、保留项对象同一性） |
| 空 Semantic 安全 | ✅ Test 7（A/B/C 三种空场景 + 输入校验） |
| Project A/B 不串数据 | ✅ Test 8（含交叉 schema → 全过滤） |
| Text-to-SQL 使用过滤后 business_context | ✅ E2E（含未选中/不存在表绝不出现） |
| Generator 签名没有变化 | ✅ E2E Fake 与 Phase 3.7.6 完全一致 |
| SQL Validator / Executor 没有变化 | ✅ 零修改 |
| 无新增 LLM/Embedding/Reranker 调用 | ✅ 纯内存；静态检查锁定 |
| 全量 / DB 测试通过 | ✅ 见下 |
| compile/lint 无错误 | ✅ 见下 |

---

## 五、回归结果

```text
python -m pytest -q                        → 1365 passed / 241 skipped / 0 failed
$env:RUN_DB_TESTS="1"; python -m pytest -q → 1568 passed /  38 skipped / 0 failed
python -m compileall backend               → OK
lint（修改文件）                            → 0 errors
DB residue                                 → knowledge_document/chunk = (0, 0)，
                                             project_a/project_b schema = 0
```

对比基线：非 DB 1334（3.9.1）→ 1365（+31）；DB 1535 → 1568（+33）。
既有 56 项 Text-to-SQL 测试、SQLValidator / SQLExecutor / Router /
Orchestrator / Project Configuration / Semantic 相关测试**全部保持**，
无既有用例被修改或删除。

---

## 六、已知限制

1. 空 `allowed_tables`（零选中）不做选中范围过滤，只按 Schema 存在性过滤
   ——与 Composer 降级为全库 schema 的行为保持一致（见 2.2）。
2. 不做字段同义词推断：`warehouse_name` 只会被删除，不会映射到 `name`。
3. 语义"配置过宽"本身仍由 Phase 3.7.3 `SemanticSchemaValidator` 在加载/
   校验环节报告；本模块只做运行时裁剪，不写日志告警以外的提示。
4. 过滤结果未回写 `ProjectSemanticProvider`；每次 Text-to-SQL 请求在内存中
   重新过滤（O(语义条目数)，无 IO）。
5. `business_context` 未设置独立字符预算，仍由 `DatabaseContextComposer`
   的整体 `max_chars` 与渲染后文本长度决定（未引入 tokenizer）。
