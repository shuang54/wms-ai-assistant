# Phase 4.1 Step 32 — Real Annotation Execution Contract

- 阶段：Phase 4.1（G3 准备链；在 Phase 3.12 Step 32 既有产物上补齐边界）
- 类型：**Contract only**（只冻结"真实人工标注如何执行"；**不执行真实人工标注**）
- 前置：Step 24（Annotation Schema）· Step 28（AnnotatedEvidence）·
  Step 29（Review Procedure）· Step 30（Finalization）· Step 31（Real Evidence Import）
- 实现：`tests/test_real_annotation_execution_contract.py`（test-local；**非 production**）
- Fixture：`tests/fixtures/conversation_context/real_annotation_execution_cases.yaml`

链路：

```text
Real Evidence Import（Step 31）
        ↓
Sampling（explicit / deterministic / auditable）
        ↓
Annotator Input（isolated；只含 dataset_version / case_id / project_id / turns）
        ↓
Independent Annotation（human；本阶段不产生）
        ↓
Comparison（复用 Step 29 compare_annotations）
        ↓
Revision / Resolution（复用 Step 28 revise / Step 29 domain review）
        ↓
Finalization（复用 Step 30 finalize_annotated_evidence）
```

但：

```text
G3 = BLOCKED
G4 = BLOCKED
```

---

## 1. Sampling Contract

```python
sample_cases(
    evidence,
    *,
    case_ids=None,
    start=0,
    limit=None,
    requested_dataset_version=None,
)
```

规则：

```text
* 显式 case_ids → 保持用户传入顺序（不排序；auditable）；
* 未提供 case_ids → sorted(case_id ascending) → [start : start + limit]；
* 结果 DTO：AnnotationSamplingResult（frozen）=
    dataset_version / case_ids / sample_count；
* Sampling 只 select，**不修改 evidence**：
    不 sanitize / 不 normalize / 不 merge / 不 split / 不改写 /
    不截断 / 不改 role / 不改 project_id / 不改 dataset_version；
* 禁止 random / random.seed / time / UUID / LLM 参与抽样。
```

## 2. Annotator Input Contract

Annotator 输入只允许：

```text
dataset_version
case_id
project_id
turns[]（每 turn 只允许 role / content）
```

禁止进入 Annotator 输入：

```text
其他 annotator annotation · comparison result · disagreement ·
resolved annotation · final annotation · reviewer hints · ground truth ·
selection result · evaluation result ·
annotator_id（execution metadata）· previous_annotation · review_status
```

实现：`AnnotatorCaseInput`（frozen）+ `check_input_isolation`（白名单校验）。

## 3. Independence

```text
annotator identity 是 independence 的唯一依据
annotator_a != annotator_b  → 允许 independent
annotator_a == annotator_b  → ANNOTATOR_NOT_INDEPENDENT
```

明确：

```text
* 不同 session / 不同时间 / 不同机器 **不构成** independence；
* annotation 级检查复用 Step 29 AnnotationDraft.annotator（不新增身份结构）：
    check_annotation_independence(annotation_a, annotation_b)；
* 不做 session / time / machine 维度比较（它们不进入契约）。
```

## 4. Input Isolation

```text
Annotator A 不看到 Annotator B 的标注
Annotator B 不看到 Annotator A 的标注
```

规则：

```text
* 双向隔离：A/B 输入结构上互不可见（测试覆盖双向）；
* 发现禁止字段：
    ANNOTATOR_INPUT_LEAK
  不得静默删除字段；不得自动 sanitize 后继续执行（拒绝而非清洗）；
* 隔离检查的 payload 不被改写（测试断言禁止键保持原位）。
```

## 5. Versioning

严格区分：

```text
dataset_version      = "wms-v1"            （evidence 身份）
annotation_version   = "annotation-v1"     （人工标注产品身份）
```

```text
* 两者必须独立存在；不得混为一个版本号；
* requested_dataset_version != evidence.dataset_version
    → DATASET_VERSION_MISMATCH（不自动转换）；
* Execution Result 只承载 dataset_version；
  annotation_version 属于 annotation 产物（不在 Execution Result 中）。
```

## 6. Comparison

```text
独立 annotation
        ↓
Comparison（复用 Step 29 compare_annotations；调用边界在此冻结）
```

```text
* 不重新实现比较算法；
* AGREEMENT / DISAGREEMENT 判定与 differing_fields 由 Step 29 负责；
* 本阶段不设计：LLM judge / automatic scoring / semantic similarity /
  embedding similarity。
```

## 7. Revision

```text
old annotation
        ↓
revise
        ↓
new annotation
```

```text
* 复用 Step 28 AnnotatedEvidence.revise(...)（不重实现）；
* Revision 必须 immutable：旧 annotation 对象保持不变；
* 修订必须给出 new annotation_version（同版本修订被拒绝）；
* 修订不改变 dataset_version。
```

## 8. Finalization

```text
独立 annotation
        ↓
comparison
        ↓
revision / resolution（domain review；复用 Step 29）
        ↓
final annotation（复用 Step 30 finalize_annotated_evidence）
```

```text
* 不修改 Step 30 的最终化语义；
* dispute 记录中同一 annotator → ANNOTATOR_NOT_INDEPENDENT（Step 30 复用校验）；
* 无自动合并：final draft 由人工提供。
```

## 9. Failure Codes

Step 32 最多冻结 6 个：

```text
DATASET_VERSION_MISMATCH
CASE_NOT_FOUND
DUPLICATE_CASE_ID
ANNOTATOR_NOT_INDEPENDENT
ANNOTATOR_INPUT_LEAK
INVALID_CASE
```

```text
* start < 0 / limit < 0 / case_id 空 / dataset_version 空 → INVALID_CASE；
* 不新增 ANNOTATION_ERROR / COMPARE_ERROR / REVISION_ERROR / FINALIZATION_ERROR。
```

## 10. Synthetic Fixture

`real_annotation_execution_cases.yaml`：

```text
* SYNTHETIC_ONLY —— synthetic execution contract fixture，非生产数据；
* 不包含真实客户 / 供应商 / 订单 / 库存 / 员工 / 个人信息；
* 不使用真实 request_id / conversation_id / 数据库 ID；
* 4 个 case 覆盖四类形状（各 1）：
    straightforward / disagreement / revision / multi-turn；
* 6 个执行场景：
    normal_independent / duplicate_case / missing_case /
    dataset_version_mismatch / same_annotator / input_leakage；
* 不伪装成真实数据（REAL_DEIDENTIFIED 结构模拟仅用于契约测试）。
```

## 11. Step 29 Compatibility

```text
AnnotatorCaseInput
        ↓
AnnotationDraft（Step 29 DTO）
        ↓
compare_annotations() → ComparisonOutcome（AGREEMENT / DISAGREEMENT）
        ↓
review_phase / finalize_domain_review / review_status_for
```

```text
* compare_annotations / resolve（domain review）/ finalize 均复用，不重实现；
* 测试覆盖 DISAGREEMENT → DOMAIN_REVIEW_REQUIRED → FINAL_REVIEW → REVIEWED。
```

## 12. Step 30 Compatibility

```text
EVIDENCE_SOURCE_TYPE == SYNTHETIC_ONLY（与 Step 30 一致）
        ↓
AnnotatedEvidence + CaseFinalizationMetadata
        ↓
finalize_annotated_evidence(...) → AnnotationEvidenceIntegrityResult
```

```text
* finalization 路径复用 Step 30（含其 independence / version 校验）；
* synthetic fixture 不得被误认为 REAL_PRODUCTION_EVIDENCE。
```

## 13. G3 Status

```text
G3 = BLOCKED
```

```text
* 本阶段不产生 G3 evidence（无真实数据、无真实人工标注）；
* Execution PASS（contract simulation）不改变 G1~G4 状态。
```

## 14. G4 Status

```text
G4 = BLOCKED
```

```text
* 不进入 Selection Strategy / ContextSelector / G4 evaluation；
* Execution Result 不承载 selection_strategy / G3 / G4 / model_score / LLM_score。
```

## 15. Security Boundary

```text
* 复用 Step 28 forbidden-field 检查（不实现第二套）；
* 禁止进入 Annotator Input：
    API_KEY · Authorization · password · DATABASE_URL · SQL ·
    stack trace · raw LLM response · prompt · messages ·
    other annotation · reviewer hints；
* project_id 只是业务上下文：
    **不是** authorization / tenant boundary（本阶段不新增权限体系）。
* DB = 0 · Network = 0 · LLM = 0 · 不导入真实 WMS 数据 · 不执行真实人工标注。
```

---

## 附：证据锚点

| 契约点 | test |
| --- | --- |
| 显式 case_ids 保序 | test_01 |
| sorted + start/limit 窗口 | test_02 |
| duplicate / missing / version mismatch | test_03 / test_04 / test_05 |
| 非法参数（start / limit / 空值） | test_06 / test_07 / test_34 |
| sampling 不改 evidence | test_08 / test_16 |
| 无 random / time / UUID | test_09 |
| 独立性（execution / annotation 级） | test_11 / test_35 |
| 输入隔离（白名单 / leak / 双向 / 不 sanitize） | test_12 / test_13 / test_14 / test_36 |
| 版本（dataset ≠ annotation） | test_17 |
| Revision immutable | test_18 |
| Comparison → Domain Review → Finalization | test_25 / test_37 / test_38 |
| G1~G4 保持 BLOCKED | test_27 / test_28 |
| fixture 四类形状 | test_39 |
| Production boundary / 文档 | test_31 / test_32 |
