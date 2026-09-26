# Text-to-SQL Result Mismatch Diagnosis & Ground Truth Semantics - Phase 3.9.15

## 1. Objective

Phase 3.9.14 得到 `Result Accuracy = 9/12 (75%)`，其中 3 个 case mismatch。

本阶段**只做纯诊断**，回答：

> 这 3 个 mismatch 到底是模型 SQL 的问题，还是 Ground Truth 过于严格？

本阶段**不修改**任何生产代码、Prompt、Ground Truth、Dataset、baseline、
Result Checker、fixture data，也**不重新运行** DeepSeek。

---

## 2. Evidence

证据来源（全部为已 sealed 的只读产物）：

| 来源 | 用途 |
| --- | --- |
| `baselines/phase_3_9_14_result_llm_baseline.json` | 3 个 mismatch 的 generated SQL / actual columns / actual rows / expected |
| `text_to_sql_regression.yaml` | 3 个 case 的自然语言 question 与结构期望 |
| `result_ground_truth.yaml` | expected result 的定义方式 |
| `text_to_sql_result_evaluation_service.py` | checker 实际比较的内容 |
| `text_to_sql_evaluation_service.py` | structural expectation 的比较边界 |

3.9.14 运行指标（引用，不重新解释）：

```text
run_status                    = COMPLETE
model                         = deepseek / deepseek-chat
Generation Success            = 12/12
Validator Acceptance          = 12/12
Structural Expectation Match  = 12/12
Execution Success             = 12/12 (attempted 12)
Result Accuracy               = 9/12 = 0.75
```

---

## 3. Case-by-case diagnosis

### A. `group_by_chunk_count_per_document`

**自然语言 question**：`统计每个知识文档的知识分片数量`

**Question 实际要求的字段**：

- 每个知识文档的**标识**（否则"每个"无从对应）；
- 该文档的**知识分片数量**（"数量"是明确要求）。
- `title` **不是** question 明确要求的字段，但 question 也**没有禁止**它；
  且对"每个知识文档"而言，`title` 是合理的人类可读标识。

**生成的 SQL**：

```sql
SELECT d.id, d.title, COUNT(c.id) AS chunk_count
FROM t2s_eval.documents d
LEFT JOIN t2s_eval.chunks c ON c.document_id = d.id
GROUP BY d.id, d.title
LIMIT 1000;
```

**实际结果 vs Ground Truth**：

| document_id | actual count | GT count | 一致？ |
| ---: | ---: | ---: | --- |
| 1 | 2 | 2 | yes |
| 2 | 3 | 3 | yes |
| 3 | 1 | 1 | yes |
| 4 | 4 | 4 | yes |

结论：**id 全部正确、count 数值全部正确**；actual 仅额外返回了 `title` 列。

进一步验证（基于 checker 的 multiset 语义）：若把 actual 投影为
`(document_id, count)`，其 Counter 与 GT 的 Counter **完全相等**，case 即通过。
这说明失败原因**只有** `title` 这一额外列，不存在行集合错误、重复行错误或数值错误。
另外 GT 使用 `unordered_rows`，行顺序已被 checker 放宽，**顺序不是失败原因**。

**分类**：

- `primary_category = RESULT_EXTRA_COLUMN`
- `secondary_category = GROUND_TRUTH_SPECIFICATION_ISSUE`


### B. `having_chunk_count_greater_than`

**自然语言 question**：`查询知识分片数量超过2个的知识文档`

**Question 实际要求的字段**：

- question 的落点是「**查询……的知识文档**」，要求的是**文档本身**；
- 「知识分片数量超过 2 个」是 **HAVING 过滤条件**，不是要求输出 count 列；
- 因此 `count` **不是** question 要求的投影字段；
- `id` / `title` 用以标识"哪些文档"，足以回答该问题。

**生成的 SQL**：

```sql
SELECT d.id, d.title
FROM t2s_eval.documents d
JOIN t2s_eval.chunks c ON c.document_id = d.id
GROUP BY d.id, d.title
HAVING COUNT(c.id) > 2
LIMIT 1000;
```

**实际结果 vs Ground Truth**：

| document_id | 在 actual 中？ | 在 GT 中？ | GT count |
| ---: | --- | --- | ---: |
| 1 | no | no | 2 |
| 2 | yes | yes | 3 |
| 3 | no | no | 1 |
| 4 | yes | yes | 4 |

结论：**HAVING 筛选出的 document 集合完全正确**（{2, 4}）。
actual 相对 GT **缺少 `count` 列**（GT 规定了 `(document_id, count)` 投影），
而 question 本身并未要求输出 count。

**分类**：

- `primary_category = GROUND_TRUTH_SPECIFICATION_ISSUE`
- `secondary_category = RESULT_MISSING_REQUIRED_COLUMN`
  （"required" 是相对 GT 的投影定义而言，不是相对自然语言问题）

---

### C. `semantic_dependent_document_and_chunk`

**自然语言 question**：`查询每个文档包含多少个分片`

**分析**：

- 与 case A 语义等价（"每个文档……多少个分片" = 每文档分片数量）；
- question 要求：文档标识 + 分片数量；
- `title` 不是必要字段，但属于合理的人类可读附加标识；
- actual `(id, title, count)` **满足业务语义**：每文档的分片数量全部正确；
- GT `(id, count)` 是**其中一个**合法投影，**不是唯一正确投影**。

**分类**：

- `primary_category = RESULT_EXTRA_COLUMN`
- `secondary_category = GROUND_TRUTH_SPECIFICATION_ISSUE`


## 4. Column semantics analysis

「列选择语义」分类模型（**仅诊断用，未实现**）：

| 类别 | 含义 |
| --- | --- |
| `RESULT_VALUE_ERROR` | 返回的数据值错误（如 count 应为 4 实为 5） |
| `RESULT_MISSING_REQUIRED_COLUMN` | 缺少回答问题所必需的列 |
| `RESULT_EXTRA_COLUMN` | 返回了问题未要求、也未禁止的额外列 |
| `RESULT_COLUMN_ORDER_ERROR` | 列顺序与期望不同（值集合相同） |
| `RESULT_ROW_SET_ERROR` | 返回的行集合错误（多行 / 少行 / 错行） |
| `RESULT_DUPLICATE_ROW_ERROR` | 重复行处理错误 |
| `GROUND_TRUTH_SPECIFICATION_ISSUE` | GT 把某一种投影当成了唯一正确答案 |

逐项判定：

| 判定 | 是否成立 | 证据 |
| --- | --- | --- |
| `RESULT_VALUE_ERROR` | **否** | A/C：per-document 计数与 GT 完全一致；B：命中文档集合一致 |
| `RESULT_EXTRA_COLUMN` | **是（A、C）** | actual 多返回 `title` |
| `RESULT_MISSING_REQUIRED_COLUMN` | **是（B，相对 GT）** | actual 缺 `count` 列 |
| `RESULT_ROW_SET_ERROR` | **否** | A/C 行数 4 = GT 4；B 文档集合 {2,4} = GT |
| `RESULT_DUPLICATE_ROW_ERROR` | **否** | 无重复行问题 |
| `RESULT_COLUMN_ORDER_ERROR` | **否** | GT 用 `unordered_rows`，列顺序不参与比较 |
| `GROUND_TRUTH_SPECIFICATION_ISSUE` | **是（A、B、C）** | GT 固定了具体投影，而 question 未禁止其它列 |

---

## 5. Ground Truth strictness analysis

判定标准：

- **Strict**：GT 与自然语言要求一一对应，偏差即真实错误；
- **Reasonable**：GT 是一种合理投影，但未覆盖等价投影；
- **Potentially Over-specified**：GT 要求了 question 未要求的列。

| Case | GT 判定 | 依据 |
| --- | --- | --- |
| `group_by_chunk_count_per_document` | **Potentially Over-specified** | 要求 count（question 有要求），但固定了「不含 title」的投影；question 未禁止 title |
| `having_chunk_count_greater_than` | **Potentially Over-specified** | question 只要求「哪些文档」，GT 却要求输出 count 列 |
| `semantic_dependent_document_and_chunk` | **Potentially Over-specified** | 同 group_by |

三种情况的区分：

- **情况 1（真实数据错误）**：`expected count = 4, actual count = 5`
  -> 本阶段 3 个 mismatch 中 **0 个**属于此类；
- **情况 2（正确数据 + 额外列）**：A、C 属于此类；
- **情况 3（GT 规定了 question 未要求的列）**：B 属于此类
  （A/C 也部分涉及，因为 GT 未声明 title 为可选）。


## 6. Structural vs Result-level evaluation boundary

两个指标验证的是**不同层次**，二者都合理：

| 指标 | 验证内容 | 数据来源 | 是否比较执行结果 |
| --- | --- | --- | --- |
| Structural Expectation Match | SQL 是否引用了正确的表 / 关键列、是否只读、是否带 LIMIT（3.9.3 checker，经 3.9.14 映射到 `t2s_eval`） | 生成的 SQL AST | no |
| Result Accuracy | **执行后**的 rows 是否与 GT 一致（3.9.10 checker） | Executor 实际返回的 rows | yes |

因此：

- `Structural Expectation = 12/12` 表示：12 条 SQL 都引用了正确的表
  （`t2s_eval.chunks` / `t2s_eval.documents`）、正确的关键列
  （`document_id`），并且全部只读、带 LIMIT，**这 3 条 SQL 在结构上无可挑剔**；
- `Result Accuracy = 9/12` 表示：其中 3 条 SQL 的**输出投影**与 GT 规定的
  投影不一致；
- 两者并不矛盾：**SQL 可以结构完全正确，但 projection 与严格 GT 不同**；
- 这也说明 evaluation 层次本身是合理的：结构层与结果层各自测量不同事物，
  恰恰是本次诊断得以区分「模型错误 / 投影差异 / GT 过严」的前提。

---

## 7. Classification

| Case | Actual | Expected | 主要问题 | Primary Category | 是否模型真实错误 | 是否 GT 过严 |
| --- | --- | --- | --- | --- | --- | --- |
| `group_by_chunk_count_per_document` | `(id,title,count)` 4 行 | `(document_id,count)` 4 行 | 多返回 `title` | `RESULT_EXTRA_COLUMN` | no | yes |
| `having_chunk_count_greater_than` | `(id,title)` 2 行 | `(document_id,count)` 2 行 | 缺 `count` 列 / GT 多要求 | `GROUND_TRUTH_SPECIFICATION_ISSUE` | no | yes |
| `semantic_dependent_document_and_chunk` | `(id,title,count)` 4 行 | `(id,count)` 4 行 | 多返回 `title` | `RESULT_EXTRA_COLUMN` | no | yes |

### Overall conclusion（数字来自实际分析，非猜测）

```text
3 个 mismatch 中：

- 0 个属于真实 Result-level 模型错误（RESULT_VALUE_ERROR = 0）
- 2 个属于 Projection / Column Shape 问题（RESULT_EXTRA_COLUMN，
  primary：group_by / semantic_dependent）
- 3 个涉及 Ground Truth Specification Issue（A / B / C 均涉及；
  其中 B 为 primary）
- 0 个属于 Value Error
- 1 个存在 missing count 列（相对 GT 投影，having）
```

补充事实：

- 3 个 case 的 **Structural Expectation 均为 MATCH**；
- 3 个 case 的 **聚合值 / 筛选集合全部正确**；
- 3.9.14 的 `Result Accuracy = 75%` **不能**解读为 DeepSeek 的
  Text-to-SQL 能力只有 75%，其中 3 个失败全部属于投影 / GT 语义层面。


## 8. Recommendation for Phase 3.9.16

以下**仅是建议**，本阶段未实现任何改动。

### 8.1 是否应该修改 Ground Truth？

建议**是**，但以「增强声明」而非「改写数值」的方式进行：

- 保留现有数值型期望（count / 筛选集合不变）；
- 把**列**拆分为显式声明，而不是隐含在 row 元组里。

### 8.2 是否应该增强 Result Checker？

建议增加**投影感知的比较层**，并把比较结果拆分报告：

| 比较维度 | 建议输出 |
| --- | --- |
| value correctness | 数值 / 集合是否正确（当前已隐含） |
| missing required columns | 缺哪些列 |
| extra columns | 多哪些列 |
| row set / duplicates | 行集合与重复行 |
| column order | 顺序差异 |

这样 `RESULT_VALUE_ERROR` 与 `RESULT_EXTRA_COLUMN` 不再混在同一个布尔值里。

### 8.3 是否应该区分两种正确性？

建议在 ground truth 中引入两种模式：

```text
strict_exact_projection   # 现有行为：行元组完全一致
semantic_result_correctness  # 只比较语义必需列的值
```

### 8.4 是否需要 required / optional / forbidden columns？

建议在 ground truth 中支持：

```yaml
columns:
  required: [document_id, count]   # 必须出现且值正确
  optional: [title]                # 允许出现，值不参与判定
  forbidden: []                    # 出现即失败
```

### 8.5 是否应该分开统计？

建议把以下项**分开统计**，而不是合并成一个布尔值：

```text
wrong values
wrong row set
missing required columns
extra columns
```

### 8.6 不建议做的事

- 不建议用 LLM-as-a-Judge 判断结果正确性（破坏确定性）；
- 不建议为了提高 Result Accuracy 而放宽/改写既有 GT 数值；
- 不建议在本阶段实现任何上述改动。
