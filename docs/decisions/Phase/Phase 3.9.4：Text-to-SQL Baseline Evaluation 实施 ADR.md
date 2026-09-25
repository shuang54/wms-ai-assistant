# Phase 3.9.4 实施 ADR：Text-to-SQL Baseline Evaluation

> 本文件记录 Phase 3.9.4 的实施结论（度量定义、边界决策与真实运行结果）。
> 阶段任务书见同目录 `Phase 3.9.4：Text-to-SQL Baseline Evaluation.md`。
> 产物：`docs/evaluation/text-to-sql-baseline-3.9.4.md`（Report）
> 与 `tests/fixtures/text_to_sql/baselines/phase_3_9_4_baseline.json`（Snapshot）。

---

## 一、目标与边界

本阶段是**测量，不是优化**：

```text
Phase 3.9.3 Dataset（只读，14 cases 未改）
    ↓
Phase 3.9.3 TextToSQLEvaluationRunner（不重复实现）
    ↓
纯函数统计 calculate_baseline_metrics()
    ↓
Snapshot JSON + Markdown Report
```

明确不做：改 Prompt / Generator / Validator / Executor / Selector / Composer /
Semantic / Router / Orchestrator；不加 LLM 调用、不加 Embedding、不改 SQL、
不自动修复、不动 Dataset case、不建表、不做 UI / 排行榜。

---

## 二、度量定义（§五 ~ §十）

| 指标 | 定义 | 当前值 |
| --- | --- | --- |
| `expectation_pass_rate` | passed_cases / total_cases（case 全部期望项满足才算 PASS） | 1.0 (14/14) |
| `validation_pass_rate` | **实际**通过 Validator 的 case 数 / total（仅作对照） | 0.9286 (13/14) |
| `validation_expectation_pass_rate` | validation 期望被满足的 case 数 / 声明了该期望的 case 数 | 1.0 (14/14) |
| `execution_pass_rate` | 仅统计 `must_execute` case；分母 0 → **None**（N/A） | None（Dataset 无执行 case） |
| `security_expectation_pass_rate` | `must_pass_validation=false` case 中被 Validator **正确拒绝**的比例 | 1.0 (1/1) |
| `project_isolation_pass_rate` | `eval-project-a` / `eval-project-b` case 的期望满足比例 | 1.0 (2/2) |

关键取舍：

1. **不重新定义评分体系**：全部复用 3.9.3 Result 中已有的
   `passed` / `validation_passed` / `matched_expectations` / `failed_expectations`，
   `calculate_baseline_metrics()` 里没有第二套 Checker。
2. **`validation_pass_rate` ≠ 正确率**：安全 case（DELETE）被正确拒绝时
   `validation_passed=False`，但它属于 PASS，所以又单列
   `validation_expectation_pass_rate`——这正是任务书 §六要求的区分。
3. **N/A 而不是 0%**：无执行 case 时 `execution_pass_rate` 为 `None`，
   Snapshot 里序列化为 `null`，Report 显示 `N/A`。
4. **比率 = property**：Metrics DTO 只存计数，比率由 property 计算
   （单一真值，避免"计数改了比率没改"），统一 `0.0 ~ 1.0`、4 位小数。
5. **纯函数统计**：不查 DB、不调 LLM / Embedding / 网络、不改文件。

**安全 case 的识别**：优先用 dataset case 的 `must_pass_validation=false` 真值；
未传 cases 时从 Result 精确反推——3.9.3 Checker 满足
`actual == expected ⟺ matched`，因此 `expected == (validation_passed == matched)`
是一一对应的双射，不需要猜。

---

## 三、确定性 Baseline 的含义（§十八 / §十九）

- 默认 Baseline **不调用真实 LLM**：`deterministic-fake-generator` 模式把 LLM 层
  替换为固定 SQL（`baselines/deterministic_generator_responses.json`），
  因此 `pytest` 不需要网络与 API 配额，结果可重复。
- 被替换的只有 LLM 一步：Resolver 仍是真实的
  `RelevantTableSelector → SemanticSchemaFilter → DatabaseContextComposer`，
  Validator 仍是真实 `SQLValidatorService`（Runner 会再校验一次）。
- 因此本 Baseline 度量的是 **Pipeline 完整性**（选表 / 上下文 / 校验 / 安全边界 /
  项目隔离），**不是** LLM 生成 SQL 的质量。后者需要后续"真实 LLM Baseline"，
  本阶段记录：`Real LLM Baseline: not executed`。
- 所有 table / column 都来自当前真实 Schema（`public.knowledge_document` /
  `public.knowledge_chunk`）或既有 project_a / project_b fixture；未虚构字段。

---

## 四、失败 Case 的处理（§十五 / §二十二）

当前 Baseline **无失败 case**（14/14）。这是真实运行结果，不是被优化出来的结果：

- 没有修改任何 Dataset case、期望、Checker 或生产逻辑；
- Report 的 `Failed Cases` 段落会如实列出失败原因（例如
  `expected validation=false, actual validation=true` 这类事实描述），
  不含主观评价；
- 以后若出现失败，**应重新生成新的 Baseline**，而不是改统计代码或 Dataset
  去迁就旧分数（§二十一：不要把具体分数写死成生产逻辑）。

---

## 五、产物与再生流程

```text
python scripts/generate_text_to_sql_baseline.py           # 生成 / 覆盖产物
python scripts/generate_text_to_sql_baseline.py --check   # 只比对，不写文件（CI 可用）
```

`--check` 比较 `phase / dataset / metrics / cases`，忽略 `generated_at` 与
`environment`（PostgreSQL 版本、LLM 标识会随环境变化）。

Snapshot 结构：`phase / dataset{path,version,total_cases} / execution_mode /
environment / metrics / cases[] / generated_at`；
Case 级快照**不保存 generated SQL**（SQL 会随模型变化、易含业务数据噪声）。

环境字段只记录 Python 版本 / LLM provider / model / PostgreSQL 版本 /
执行模式；`to_snapshot_dict()` 自带敏感键自检（命中
`api_key / password / database_url / secret / token / ...` 直接抛错，不落盘）。

---

## 六、测试与回归结果

新增 `tests/test_text_to_sql_baseline.py`（28 项 = 27 非 DB + 1 DB-gated）：

- Dataset Version 读取（含真实 `_schema_version` 与缺失文件异常）
- Case 数量锁定（14）与"Dataset 无 must_execute"约束
- Metrics：混合结果 / passed+failed=total / 空分母 N/A / 执行率只算执行 case /
  安全拒绝计 PASS / 项目隔离 A+B / 隔离项目可配置 / 确定性 / dict 键齐全
- Snapshot：文件存在 + schema 合法 / case 字段（无 generated_sql）/
  **与真实运行一致** / 无敏感信息
- Report：数值与 Snapshot 一致 / N/A 显示 / 渲染确定性 / CLI 摘要 / 失败段落
- DB：真实 Schema 上跑 Baseline（只读）

回归：

```text
python -m pytest -q                        → 1432 passed / 246 skipped / 0 failed
$env:RUN_DB_TESTS="1"; python -m pytest -q → 1640 passed /  38 skipped / 0 failed
python scripts/generate_text_to_sql_baseline.py --check → OK
python -m compileall backend scripts       → OK
lint（新增 3 个文件）                        → 0 errors
DB residue                                 → knowledge_document / knowledge_chunk = (0, 0)
                                             project_a / project_b schema = 0
```

基线对比：非 DB 1405（3.9.3）→ 1432（+27）；DB 1612 → 1640（+28）。
既有 Phase 3.7.x / 3.8.x / 3.9.1 ~ 3.9.3 全部测试保持通过，无既有用例被修改或删除。

---

## 七、最终检查（§二十七）

| 项 | 状态 |
| --- | --- |
| Dataset 14 Cases 未修改 | ✅ 仅读取（`git status` 中不出现该 YAML 变更） |
| Dataset version 未修改 | ✅ `_schema_version: "1.0"`，Baseline 绑定该值 |
| Evaluation Runner 核心行为未改变 | ✅ 复用未改动，仅新增调用方 |
| Generator / Validator / Executor contract 未变 | ✅ |
| Prompt / Semantic / Selector / Composer / Router / Orchestrator 未变 | ✅ |
| 无新增 LLM / Embedding 调用 | ✅ 默认使用 Fake Generator |
| 无新增 DB 持久化写入 | ✅ 仅读 `SELECT version()`；无新表 |
| Metrics 来自真实运行 | ✅ `--check` 校验一致 |
| Snapshot 与 Report 数值一致 | ✅ 有专项测试 |
| Security rejection 计 PASS | ✅ 有专项测试 |
| execution N/A 正确处理 | ✅ `null` / `N/A` |
| Project A/B 隔离结果正确 | ✅ 2/2 |

---

## 八、已知限制

1. 本 Baseline 不含 LLM 生成质量；真实 LLM Baseline 需后续阶段单独跑并记录。
2. Dataset 当前没有 `must_execute` case，故 Execution Pass Rate 恒为 N/A；
   只有在 DB 环境下才有意义。
3. `project_isolation` 统计默认针对 dataset 里现存的 `eval-project-a` /
   `eval-project-b`；若 dataset 增加新隔离项目，需要同步传入 isolation_project_ids。
4. 环境信息中的 PostgreSQL 版本取决于生成时能否连库（不可用 → `unavailable`），
   因此它被排除在一致性比对之外。
5. Case 级 Snapshot 不保存 SQL，定位具体 SQL 变化需重跑 `--check` 或看 Failed Cases。
6. 未实现 Benchmark Web UI / 排行榜（任务书明确不做）。
