# Phase 4.1 Step 32：Real Annotation Execution Contract（Decision Record）

- 类型：Contract / Decision Record（**不执行真实人工标注**）
- 对应评估文档：`docs/evaluation/Phase 4.1 Step 32 — Real Annotation Execution Contract.md`
- 实现：`tests/test_real_annotation_execution_contract.py`（test-local）
- Fixture：`tests/fixtures/conversation_context/real_annotation_execution_cases.yaml`

---

## 1. Sampling is deterministic

决定：case 抽样采用**确定性**规则 —— 显式 `case_ids` 保持传入顺序；
未提供时使用 `sorted(case_id)` 的 `[start : start + limit]` 窗口。

```text
禁止：random / random.seed / time / UUID / LLM 参与抽样。
Sampling 只 select，不修改 evidence（immutable）。
```

理由：抽样必须可复现、可审计；任何随机或时间依赖都会破坏契约测试与后续比较。

## 2. Annotator identity defines independence

决定：independence 的**唯一依据**是 annotator identity。

```text
annotator_a != annotator_b  → independent（允许）
annotator_a == annotator_b  → ANNOTATOR_NOT_INDEPENDENT
```

理由：不同 session / 时间 / 机器不构成独立性；只有身份不同才算独立标注者。
annotation 级检查复用 Step 29 `AnnotationDraft.annotator`，不新增身份结构。

## 3. Annotator input is isolated

决定：Annotator 输入只允许 `dataset_version / case_id / project_id / turns`
（turn 仅 `role / content`）；双向隔离（A 不见 B、B 不见 A）。

```text
发现禁止字段 → ANNOTATOR_INPUT_LEAK；
不静默删除字段、不自动 sanitize 后继续执行（拒绝而非清洗）。
```

理由：独立性要求两位标注者互不可见；任何"清洗后继续"都会污染独立性语义。

## 4. Dataset version != annotation version

决定：`dataset_version`（evidence 身份）与 `annotation_version`（标注产品身份）
严格分离，必须能够独立存在。

```text
requested_dataset_version != evidence.dataset_version
    → DATASET_VERSION_MISMATCH（不自动转换）
```

理由：同一数据集可产生多轮标注版本；合并版本号会使修订与追溯不可分辨。

## 5. Revision is immutable

决定：修订复用 Step 28 `AnnotatedEvidence.revise(...)`，**旧对象保持不变**；
修订必须给出 `new annotation_version`（同版本修订被拒绝）。

```text
old annotation → revise → new annotation
（old annotation 不得被原地修改）
```

理由：标注是可追溯证据；可变修订会破坏比较与 finalization 的完整性前提。

## 6. Existing compare/resolve/finalize logic is reused

决定：Step 32 **不重新实现** 比较 / 裁决 / 最终化逻辑。

```text
Comparison    → Step 29 compare_annotations
Resolution    → Step 29 review_phase / finalize_domain_review / review_status_for
Revision      → Step 28 AnnotatedEvidence.revise
Finalization  → Step 30 finalize_annotated_evidence
```

理由：本 Step 只冻结**调用边界**（execution → comparison → revision → finalization），
避免出现第二套语义。

## 7. Synthetic fixture is not real evidence

决定：`real_annotation_execution_cases.yaml` 明确为 `SYNTHETIC_ONLY`。

```text
不包含真实客户 / 供应商 / 订单 / 库存 / 员工 / 个人信息；
不使用真实 request_id / conversation_id / 数据库 ID；
不伪装成真实数据；不得被误认为 REAL_PRODUCTION_EVIDENCE。
```

理由：G1 仍 BLOCKED；synthetic 数据只能验证结构契约，不能充当生产证据。

## 8. G3/G4 remain blocked

决定：本阶段结束后：

```text
G3 = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

理由：无真实脱敏数据、无真实人工标注、无 reference/impact 评测执行；
Execution Contract 就绪不改变任何 Gate 状态。
