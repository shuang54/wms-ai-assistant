# Phase 4.1 Step 31 — Real Evidence Import Contract

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（Import 边界；不导入真实数据、不执行真实 G1）
- 前置：Step 26（Input Boundary）+ Step 27（Provenance）+ Step 28/29/30（Annotation 链）

---

## 1. Purpose

```text
Future Real Evidence
        ↓
Import Contract（本 Step）
        ↓
Provenance / De-identification / Schema Validation
        ↓
G1 Candidate Input（未来）
```

Import 只负责：

```text
输入格式正确 · 来源声明正确 · 版本声明正确 · 脱敏声明存在 ·
结构符合 Evidence Artifact
```

Import 成功：

```text
Candidate accepted
```

不代表：

```text
G1 READY（本阶段不执行 G1 Evaluation）
G3 READY
```

结果契约（test-local）：

```text
RealEvidenceImportResult
    accepted
    dataset_version
    source_type
    cases_imported
    blocked_cases
    failure_codes
    provenance（复用 Step 27 EvidenceProvenance；accepted 时非空）
```

---

## 2. Required Fields

Artifact 必需字段（白名单；除此之外一律拒绝 —— **不 silently ignore unknown fields**）：

```yaml
dataset_version: "wms-v1"              # non-empty / stable / explicit
source_type: REAL_DEIDENTIFIED         # Real Import 只接受该值
de_identification_attestation:
  attested: true
  method: external_process
conversations:
  - case_id: "..."
    project_id: "..."
    turns:
      - role: USER
        content: "..."
      - role: ASSISTANT
        content: "..."
```

字段规则：

```text
dataset_version   禁止 auto-generated / timestamp-derived / random / inferred；
                  显式声明（如 wms-v1 / wms-2026-09）；本阶段不规定生产命名规则；
source_type       必须 REAL_DEIDENTIFIED（SYNTHETIC_ONLY 作为 Real Import → INVALID_SOURCE_TYPE）；
attestation       attested 必须 true；缺失或 false → MISSING_DEIDENTIFICATION_ATTESTATION；
conversations     非空；每个 conversation 需要 case_id / project_id / turns；
turns             非空；role ∈ {USER, ASSISTANT}（Step 26 承载形小写等价）；
                  content 必须 non-null 字符串（非空，按 Step 26 已冻结规则）；
case_id           同一 dataset_version 内唯一（重复 → DUPLICATE_CASE_ID；
                  不自动去重 / 不 first-wins / 不 last-wins）；
                  同一 case_id 跨 dataset_version 允许；
conversation      至少包含一个 USER（assistant-only → INVALID_CONVERSATION；
                  user-only 允许）。
```

Attestation 缺失 / 为 false / 结构不完整 → **IMPORT_BLOCKED**（禁止 auto-assume de-identified）。

---

## 3. Forbidden Fields

### 生产 correlation IDs（继续 Step 26；Evaluation Dataset 与生产 Trace identity 解耦）

```text
conversation_id · assistant_request_id · turn_id · provider_request_id
→ PRODUCTION_CORRELATION_ID
```

（未来如确需 correlation：另建受控 mapping layer —— 本阶段不实现。）

### 派生 / Evaluation 结论字段

```text
annotation · reference · impact · strategy · strategy_id · selection ·
selection_result · score · ranking · winner · token_estimate · tokens
→ FORBIDDEN_FIELD
```

### Secrets 与 raw 内部对象（复用 Step 28 scanner + import 附加字段）

```text
API key · Authorization · Password · DATABASE_URL · 数据库连接串 · 内部 Token ·
prompt · messages · SQL · stack trace ·
raw LLM response · tool raw payload · embedding · vector · ORM object
→ FORBIDDEN_FIELD
```

### 个人身份信息（禁止进入 Artifact）

```text
姓名 · 手机号 · 邮箱 · 身份证 · 银行卡 · 家庭地址 · 精确个人联系方式
```

### 不新增的授权概念

```text
tenant_id · user_id · owner_id · permission · ACL
（project_id 继续允许：evidence context/grouping；project_id != authorization。）
```

---

## 4. Provenance

Import Result 保留 Step 27 的 `EvidenceProvenance`（至少）：

```text
dataset_version
source_type
```

不得新增：

```text
machine_id · hostname · username · absolute_path · git_sha ·
DATABASE_URL · database_host · container_id
```

---

## 5. Import vs G1

```text
Import PASS  ≠  G1 READY
```

```text
* Import PASS 只表示 Real Evidence Candidate structurally valid；
* G1 仍由 Step 25 定义（需要真实数据 + 正式 attestation + G1 evaluation）；
* 本阶段 G1 Evaluation = 0（不执行、不修改 Step 25 G1）；
* Import 不因 PASS 改变：
    G1 = BLOCKED
    G2 = INSUFFICIENT
    G3 Evidence = BLOCKED
    G4 = BLOCKED
```

即使 synthetic import PASS，也不产生：

```text
Synthetic → G1 / G2 / G3 升级
```

---

## 6. Synthetic Limitation

```text
Synthetic only
No real WMS import
```

```text
* fixture（real_evidence_import_cases.yaml）为 12 场景 synthetic 定义
  （含"REAL_DEIDENTIFIED 结构模拟"，仅用于契约测试）；
* 不含真实客户 / 供应商 / 订单 / 库存 / 员工 / 个人信息；
* 测试只使用 synthetic toxic payload（不注入真实个人信息 / 凭据）；
* Import 为 read-only：输入 immutable，禁止自动修复 / 去重 / 脱敏 / 补字段 / 改 role；
  发现问题一律 BLOCKED。
```

---

## 7. Deferred

```text
Real data（真实脱敏数据导入）        = Deferred
G1 evaluation（真实 G1 执行）        = Deferred
G2（真实长度分布）                    = Deferred
G4（Full vs Selected Evaluation）     = Deferred
ContextSelector / SelectionPolicy / SelectionBudget = Deferred
Correlation mapping layer（生产 Trace ↔ Evaluation 受控映射）= Deferred
De-identification system（真实脱敏系统）= Deferred
```

当前 Gate 状态：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

## 附：证据锚点

```text
tests/test_conversation_context_real_evidence_import.py
    → valid import / source_type / dataset_version / attestation /
      duplicate case / role / turn / conversation / correlation ID /
      forbidden payload / mixed versions / user-only / assistant-only /
      unknown field / immutability / determinism / G1 不改变
tests/test_conversation_context_real_evidence_import_architecture.py
    → production 无 import 契约实现；模块命名边界；runtime 无耦合
tests/fixtures/conversation_context/real_evidence_import_cases.yaml
    → 12 场景 synthetic import fixture（16 条记录）
```
