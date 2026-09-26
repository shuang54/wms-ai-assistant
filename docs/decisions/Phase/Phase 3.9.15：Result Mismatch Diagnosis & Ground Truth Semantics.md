# Phase 3.9.15 — Result Mismatch Diagnosis & Ground Truth Semantics

## 目标

基于 Phase 3.9.14 已完成的真实 DeepSeek Result-level Evaluation，对 3 个结果不匹配案例进行**纯诊断**。

本阶段不进行任何优化或修复。

需要回答：

1. 这 3 个 mismatch 到底是模型 SQL 的问题，还是 Ground Truth 过于严格？
2. 用户自然语言问题实际要求哪些结果列？
3. 多返回列是否应该判定为错误？
4. 缺少列是否应该判定为错误？
5. 当前 Result Checker 是否把“列选择错误”和“数值错误”混在一起？
6. 为什么 Phase 3.9.14 的 Structural Expectation 仍然是 12/12？
7. 最终应该如何分类这 3 个 mismatch？

---

## 已知事实

Phase 3.9.14：

* Generation Success: 12/12
* Validator Acceptance: 12/12
* Structural Expectation Match: 12/12
* Execution Success: 12/12
* Result Accuracy: 9/12 = 75%

只有以下 3 个案例 mismatch：

### 1. group_by_chunk_count_per_document

实际结果：

```text
(id, title, chunk_count)

4 / Delta / 4
2 / Beta / 3
3 / Gamma / 1
1 / Alpha / 2
```

Ground Truth：

```text
[(1, 2), (2, 3), (3, 1), (4, 4)]
```

也就是说：

* count 数值全部正确
* id 全部正确
* 实际 SQL 额外返回了 `title`
* 不存在 aggregate 数值错误

---

### 2. having_chunk_count_greater_than

实际结果：

```text
(id, title)

4 / Delta
2 / Beta
```

Ground Truth：

```text
[(2, 3), (4, 4)]
```

也就是说：

* HAVING 筛选出的 document 正确
* id 正确
* 实际 SQL 没有返回 Ground Truth 中的 count 列
* mismatch 主要表现为 projection / column shape 不一致

---

### 3. semantic_dependent_document_and_chunk

实际结果与 group_by 类似：

```text
(id, title, chunk_count)
```

Ground Truth：

```text
(id, chunk_count)
```

count 数值正确，但实际多返回 `title`。

---

# 本阶段严格限制

## 禁止

不要：

* 修改生产代码
* 修改 Prompt
* 修改 Ground Truth
* 修改 regression dataset
* 修改 baseline snapshot
* 修改 Result Checker 行为
* 修改 Text-to-SQL Generator
* 修改 Executor
* 修改 Validator
* 修改 Schema
* 修改 fixture data
* 重新优化 LLM
* 重新跑 DeepSeek benchmark
* 修改历史 Phase 3.9.14 artifact

本阶段只是 diagnosis。

---

# 第一步：读取现有证据

先阅读：

```text
docs/evaluation/text-to-sql-result-llm-baseline-3.9.14.md
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
tests/fixtures/text_to_sql/result_ground_truth.yaml
backend/app/services/text_to_sql_result_llm_evaluation_service.py
backend/app/services/text_to_sql_evaluation_service.py
```

必要时查看：

```text
backend/app/services/text_to_sql_context.py
backend/app/services/text_to_sql_service.py
```

重点确认：

* 3 个 mismatch 对应的自然语言 question
* expected result 的定义方式
* actual result 的保存方式
* checker 当前到底比较了什么
* Structural Expectation 与 Result Accuracy 的判定边界

---

# 第二步：逐案例分析自然语言需求

对下面三个 case 分别回答：

### A. group_by_chunk_count_per_document

分析：

* question 明确要求哪些字段？
* `title` 是否是自然语言问题明确要求的？
* `title` 是否属于合理但非必要的附加字段？
* 如果 SQL 返回 `(id,title,count)`，而 GT 是 `(id,count)`，应该算：

  * 正确
  * 部分正确
  * Column Selection Error
  * Ground Truth Specification Issue
  * 其他

必须给出证据。

---

### B. having_chunk_count_greater_than

分析：

* question 是否明确要求 count？
* 还是只要求找出 chunk_count > 某阈值的 document？
* 实际 SQL 返回 `(id,title)` 是否足以回答问题？
* GT 要求 `(id,count)` 是否属于额外的投影要求？
* 缺少 count 到底是模型错误还是 GT 过于严格？

---

### C. semantic_dependent_document_and_chunk

分析：

* question 的自然语言要求是什么？
* `title` 是否是必要字段？
* actual `(id,title,count)` 是否满足业务语义？
* GT `(id,count)` 是否只是一个特定 projection，而不是唯一正确 projection？

---

# 第三步：建立“列选择语义”分类

不要修改 checker，只进行诊断。

提出一个推荐分类模型，例如：

```text
RESULT_VALUE_ERROR
RESULT_MISSING_REQUIRED_COLUMN
RESULT_EXTRA_COLUMN
RESULT_COLUMN_ORDER_ERROR
RESULT_ROW_SET_ERROR
RESULT_DUPLICATE_ROW_ERROR
GROUND_TRUTH_SPECIFICATION_ISSUE
```

然后判断：

### group_by_chunk_count_per_document

属于什么？

### having_chunk_count_greater_than

属于什么？

### semantic_dependent_document_and_chunk

属于什么？

如果一个 case 同时存在多个问题，允许给出：

```text
primary_category
secondary_category
```

但不要为了分类而强行制造错误。

---

# 第四步：解释为什么 Structural Expectation 是 12/12

重点分析：

Phase 3.9.14：

```text
Structural Expectation Match = 12/12
```

但：

```text
Result Accuracy = 9/12
```

解释两个指标分别验证什么。

尤其确认：

* Structural Expectation 是否只验证 SQL 结构/语义，例如 GROUP BY、JOIN、HAVING 等
* Result Accuracy 是否真正比较执行后的 rows
* 为什么 SQL 可以结构正确，但 projection 不符合严格 GT
* 这是否说明 evaluation 层次本身是合理的

不要修改任何指标。

---

# 第五步：判断 Ground Truth 是否过于严格

对于每一个 mismatch 给出：

```text
Ground Truth:
- Strict / Reasonable / Potentially Over-specified
```

重点区分：

### 情况 1

模型返回错误的数据。

例如：

```text
expected count = 4
actual count = 5
```

这是明确的：

```text
RESULT_VALUE_ERROR
```

### 情况 2

模型返回正确数据，同时多返回一个自然语言问题没有禁止的列。

例如：

```text
expected: id,count
actual: id,title,count
```

这不能直接等同于数值错误。

### 情况 3

Ground Truth 强制规定了某个列，但自然语言问题并没有明确要求该列。

这种情况需要考虑：

```text
GROUND_TRUTH_SPECIFICATION_ISSUE
```

---

# 第六步：给出最终诊断结论

最终必须给出一个表：

| Case | Actual | Expected | 主要问题 | Primary Category | 是否模型真实错误 | 是否GT过严 |
| ---- | ------ | -------- | ---- | ---------------- | -------- | ------ |

然后给出总体结论：

```text
3 个 mismatch 中：

- X 个属于真实 Result-level 模型错误
- X 个属于 Projection/Column Shape 问题
- X 个属于 Ground Truth Specification Issue
- X 个属于 Value Error
```

数字必须来自实际分析，不能猜。

---

# 第七步：提出下一阶段建议，但不要执行

最后只提出建议，不实施。

需要回答：

### 下一阶段是否应该修改 Ground Truth？

### 是否应该增强 Result Checker？

### 是否应该区分：

```text
strict_exact_projection
semantic_result_correctness
```

### 是否需要增加：

```text
required_columns
optional_columns
forbidden_columns
```

### 是否应该把：

```text
extra columns
missing required columns
wrong values
wrong rows
```

分开统计？

但注意：

**本阶段不能实现这些改动。**

---

# 输出要求

创建一份诊断文档：

```text
docs/evaluation/text-to-sql-result-mismatch-diagnosis-3.9.15.md
```

文档至少包含：

1. Objective
2. Evidence
3. Case-by-case diagnosis
4. Column semantics analysis
5. Ground Truth strictness analysis
6. Structural vs Result-level evaluation boundary
7. Classification
8. Overall conclusion
9. Recommendation for Phase 3.9.16

不要修改其他生产代码。

---

# 测试要求

因为本阶段只新增诊断文档：

至少执行：

```text
compileall / lint
```

并确认：

* 生产代码没有变化
* Prompt 没有变化
* Ground Truth 没有变化
* Dataset 没有变化
* Phase 3.9.14 baseline 没有变化

如果已有 git diff 工具，可以检查：

```text
git diff
```

确认本阶段没有产生非预期代码修改。

---

# 最重要的原则

不要因为 Result Accuracy = 75% 就直接认为 DeepSeek 的 Text-to-SQL 能力只有 75%。

这 3 个失败案例必须先区分：

```text
真正的数据错误
        VS
Projection/Column Shape 差异
        VS
Ground Truth 过度约束
```

在完成上述诊断之前：

**不要开始 Phase 3.9.16。**

完成后立即 STOP，并报告：

```text
Phase 3.9.15 完成

1. 修改文件
2. 未修改文件
3. 三个 mismatch 的分类
4. Ground Truth 是否存在过严问题
5. Structural 12/12 与 Result 9/12 的原因
6. 推荐的下一阶段
7. 测试结果
```

然后停止。
