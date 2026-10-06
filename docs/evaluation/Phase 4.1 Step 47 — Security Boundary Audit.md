# Phase 4.1 Step 47 — Security Boundary Audit

> 测试：`tests/test_phase_4_1_security_boundary_db.py`（10 passed，`RUN_DB_TESTS=1`，真实 PostgreSQL）。
> 本阶段是 **audit**，不是实现授权系统。

---

## 1. Security Scope

```text
Conversation · ConversationTurn · AI Runtime · Evidence · Annotation
ConversationEvidence · Repository · Read Model
```

验证当前架构**已经能够证明**的隔离：

```text
cross-conversation / cross-evidence leakage
identity boundary violation
ORM / session leakage
sensitive metadata leakage
unauthorized persistence
repository bypass
boundary whitelist drift
```

Inventory（审计所得）：

| 对象 | 持久化 owner | 写入方式 | 事务 |
| ---- | ------------ | -------- | ---- |
| Evidence | `EvidenceRepository` | ORM only（无裸 SQL） | Repository owns |
| Annotation | `EvidenceRepository` | ORM only | Repository owns |
| Conversation / Turn | `ConversationRepository` | ORM only | Repository owns |
| ConversationEvidence | `ConversationEvidenceRepository` | ORM only | Repository owns |
| AI Runtime | `AIOrchestratorService` | 不写 Evidence（无 Evidence 依赖） | — |
| API 层 | — | 不直接写 Evidence / ConversationEvidence | — |

---

## 2. Boundary Matrix

| Boundary | Expected | Actual | Result |
| -------- | -------- | ------ | ------ |
| Conversation → Evidence | REFERENCE | 关联表引用，非 ownership | **PASS** |
| Evidence → Conversation | REFERENCE | `list_conversation_ids` 反查 | **PASS** |
| Conversation → Annotation | indirect only（经 Evidence） | Annotation 无 conversation 字段 | **PASS** |
| Evidence → Annotation | FK-owned | `evidence_id` FK（Step 39） | **PASS** |
| Conversation A → Evidence B | reject / absent | `get_association` = None | **PASS** |
| Conversation B → Evidence A | reject / absent | `get_association` = None | **PASS** |
| Evidence ID → Conversation | only explicit association | 仅显式关联可见 | **PASS** |
| assistant_request_id → Evidence ID | forbidden | Evidence 无该字段 | **PASS** |
| assistant_request_id → provenance | forbidden | provenance 仅 2 字段 | **PASS** |
| Evidence → Conversation ownership | forbidden | 删除语义：双向保留 | **PASS** |
| Conversation delete → Evidence | preserve | Evidence + Annotation 保留 | **PASS** |
| Evidence delete → Conversation | preserve | Conversation 保留 | **PASS** |
| Read Model → ORM/session | forbidden | frozen dataclass，无泄漏属性 | **PASS** |
| Metadata → credentials | forbidden | 敏感词扫描 0 命中 | **PASS** |
| Repository → raw SQL bypass | forbidden | 三 Repository 无裸 SQL 写操作 | **PASS** |

---

## 3. Negative Cases

| Case | Expected | Actual | Result |
| ---- | -------- | ------ | ------ |
| 1 Conversation A → Evidence B（未关联） | 无隐式关联 | `get_association` = None | **PASS** |
| 2 Evidence A → Conversation B（未关联） | 无隐式关联 | `list_conversation_ids` 不含 B | **PASS** |
| 3 `create_annotation(fake_evidence_id)` | reject，count 不变 | `EvidenceRepositoryError`，count 不变 | **PASS** |
| 4 `create_association(fake_conversation_id, evidence_id)` | reject，无 orphan row | `ConversationNotFoundRepositoryError` | **PASS** |
| 5 `create_association(conversation_id, fake_evidence_id)` | reject，无 orphan row | `EvidenceNotFoundRepositoryError` | **PASS** |
| 6 重复 `create(A, E)` | 只保留一条 | first-write-wins（复合 PK） | **PASS** |

---

## 4. Identity Boundary

语义层（非数值比较）：系统不会把一个 ID 当作另一个 Domain Object 的 identity。

```text
conversation_id        ≠ evidence_id     （Conversation 独立 identity）
turn_id                ≠ evidence_id     （Turn 独立 identity）
annotation_id          ≠ evidence_id     （Annotation 独立 identity）
assistant_request_id   ≠ evidence_id     （仅 runtime correlation）
evidence_id            ≠ conversation_id
```

证据：`EvidenceRecord` / `EvidenceAnnotationRecord` 列集合**不含**
`conversation_id` / `turn_id` / `assistant_request_id` / `provider_request_id`；
`assistant_request_id` 仅存在于 `conversation_turn`，且**不是**外键。

---

## 5. Sensitive Data

```text
credential leakage      = 0   （api_key / password / secret / token /
                                authorization / database_url / connection_string / dsn
                                大小写不敏感扫描 → 0 命中）
ORM leakage             = 0   （Read Model 字段无 session / connection / engine /
                                _sa / registry / metadata / result）
connection leakage      = 0   （三 Repository 均 ORM 访问；无 Engine 外泄）
AI Result metadata      = 0   （仅 request_id + outcome；不含域 ID 与凭据）
```

---

## 6. Authorization Scope

```text
RBAC                    = OUT OF SCOPE
Multi-tenancy           = OUT OF SCOPE
ACL / ABAC / Policy Engine / JWT / SSO = OUT OF SCOPE
Object-level authorization = FUTURE GOVERNANCE
Cross-project authorization = NOT YET IMPLEMENTED
```

说明：`project_id` 仅作业务上下文，**不是** tenant / authorization boundary；
当前不存在任何"凭 ID 即可跨项目访问"的授权逻辑（关联只经显式 `conversation_evidence`），
但也不存在拒绝层 —— 完整授权体系属后续 Enterprise Governance，不在 Phase 4.1 实现。

---

## 7. Findings

```text
Security Findings = 0
```

审计未发现：cross-conversation leak · evidence leak · credential leak ·
ORM leak · repository bypass · unexpected cascade · identity confusion ·
guard whitelist drift。

Guard 边界复核（Step 45 Reconciliation 后）：

* 4 个 Guard 仍使用**显式白名单 + 精确集合比较**；
* `association_persistence_modules` 仅 2 项
  （`conversation_evidence.py` / `conversation_evidence_repository.py`）；
* 未声明文件仍被拒绝（negative probe 已验证）。

---

## 8. 边界确认

```text
Production code changes = 0（Step 47 未修改任何生产代码）
DB schema changes       = 0
DB writes               = 0（仅测试自建数据；精确清理）
DB residue              = 0（5 表计数前后一致）
Real LLM                = SKIP（未调用 DeepSeek / SiliconFlow）
```
