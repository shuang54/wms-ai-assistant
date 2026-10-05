# Phase 4.1 Step 37：Real Evidence Persistence Evaluation

- 类型：**Implementation**（Step 36 Contract → 真实 ORM / Repository / PostgreSQL）
- 前置：Step 36（Persistence & Provenance Boundary Contract）
- 实现：`backend/app/db/models/evidence_record.py` ·
  `backend/app/db/models/evidence_annotation_record.py` ·
  `backend/app/db/evidence_repository.py`
- 测试：`tests/test_evidence_persistence_db.py`（DB-gated；7 passed）

---

## 1. DB Architecture（审计结论；非假设）

```text
SQLAlchemy 2.x（DeclarativeBase；Mapped[...] + mapped_column）
Base                backend.app.db.base.Base
Session 工厂        backend.app.db.session.get_session_factory()（sessionmaker）
事务归属            Repository 持有（with factory() as session, session.begin():）
schema              ai_ops（与 conversation / 观测表同轴；非 public）
建表                init_db() → Base.metadata.create_all()（**无 Alembic**）
ID 约定             String(128) 业务键 或 BigInteger 自增
FK 约定             ForeignKey(f"{schema}.{table}.{col}", ondelete="CASCADE")
时间戳约定          DateTime(timezone=True) + server_default=func.now()
状态约定            String(32)（无 PostgreSQL ENUM）
```

## 2. ORM Design

```text
ai_ops.evidence_record
ai_ops.evidence_annotation_record
```

沿用现有风格：每列带 `comment`；只保存 metadata，**不保存**会话正文 /
prompt / SQL / chunks / embedding；不新增 Step 36 禁止字段。

## 3. Evidence Table

```text
evidence_id                    String(128) PK（服务端签发）
dataset_version                String(128) NOT NULL
source_type                    String(32) NOT NULL（REAL_DEIDENTIFIED / SYNTHETIC_ONLY）
de_identification_attested     Boolean NOT NULL default false
de_identification_method       String(64) NULL
status                         String(32) NOT NULL default IMPORTED
created_at / updated_at        DateTime(timezone=True) server_default now()
```

状态机：`IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED`
（显式 string 常量 + `EVIDENCE_STATUS_TRANSITIONS`；禁止隐式 bool、散落字符串、跳级）。

## 4. Annotation Table

```text
annotation_id        String(128) PK
evidence_id          String(128) NOT NULL FK → ai_ops.evidence_record.evidence_id
                     （ON DELETE CASCADE；**唯一**外键）
case_id              String(128) NOT NULL
annotation_version   String(64) NOT NULL（≠ dataset_version）
annotator_id         String(128) NOT NULL
review_status        String(32) NOT NULL default DRAFT（DRAFT / REVIEWED）
created_at/updated_at DateTime(timezone=True) server_default now()
```

## 5. Constraints

| 约束 | 对象 | 作用 |
| --- | --- | --- |
| PK | evidence_id / annotation_id | 主键 |
| FK | annotation.evidence_id → evidence（CASCADE） | 稳定关联；禁止孤儿 |
| UQ `uq_evidence_source_dataset` | (source_type, dataset_version) | **数据库层**幂等 |
| UQ `uq_evidence_annotation_evidence_case_version` | (evidence_id, case_id, annotation_version) | 同版本不重复；事务回滚验证点 |
| Index `ix_evidence_record_status` | status | 状态检索 |
| Index `ix_evidence_annotation_evidence_id` | evidence_id | 按 Evidence 检索 Annotation |

## 6. Repository

```text
create_evidence(...)    幂等（同 (source_type, dataset_version) → 返回既有）
get_evidence(...)       按 evidence_id
find_by_dataset(...)    按 (dataset_version, source_type)
update_status(...)      状态迁移（跳级 → InvalidEvidenceStatusTransitionError）
create_annotation(...)  **原子**：插入 Annotation + 推进 Evidence 状态
list_annotations(...)   按 evidence_id（case_id, annotation_version 排序）
```

错误：`EvidenceRepositoryError`（基类）· `EvidenceNotFoundError` ·
`InvalidEvidenceStatusTransitionError`；`SQLAlchemyError` 统一转译（事务已回滚）。

## 7. Transaction

沿用 Step 4 §4 方案 A（Repository 持有事务），未引入新事务框架：

```text
create_annotation 事务单元 =
    读 Evidence（存在性 + 版本校验）
  + INSERT annotation
  + UPDATE evidence status（PERSISTED → ANNOTATED）

失败 → 整体回滚：Annotation = 0（不新增），Evidence 状态不推进，无孤儿。
```

## 8. Idempotency

```text
幂等键 = (source_type, dataset_version)
* 应用层：create_evidence 先查既有 → first-write-wins
* 数据库层：uq_evidence_source_dataset 唯一约束兜底（不依赖应用层 alone）
```

## 9. DB Tests

```text
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_evidence_persistence_db.py
→ 7 passed（0.63s；真实 PostgreSQL）

覆盖：
  T1 Evidence Create → PostgreSQL
  T2 Evidence Read（identity 一致）
  T3 Status Transition（PERSISTED；跳级 → FINALIZED 被拒绝，状态未推进）
  T4 Annotation Create（关联 evidence_id）
  T5 Annotation Read（按 evidence_id 检索）
  T6 Cross Evidence Isolation（A/B 不交叉）
  T7 Idempotency（同一幂等键 → 计数 +1，返回既有 evidence_id）
  T8 Transaction Rollback（唯一约束冲突 → 无孤儿；Evidence 不存在 → 拒绝）
  T9 Security（无敏感列 / 无 content / 无 source_ref / content_ref / locator）
```

## 10. Security

ORM 列集合不含：api_key / password / authorization / database_url /
connection_string / llm_secret / token（T9 断言）。
不保存会话正文与 LLM 内容；`project_id` 未进入持久化（保持非 authorization 语义）。

## 11. DB Cleanup

```text
* module 级残留校验：两张表 count 前后一致（fixture 断言）
* 每个用例只删除本测试创建的 evidence_id（先 Annotation 后 Evidence）
* 无 TRUNCATE / 无全表 DELETE / 不触碰已有业务表与 WMS 表
```

## 12. Full Regression

```text
python -m pytest -q（离线）→ 5923 passed / 640 skipped / 0 failed / 0 errors
compileall → OK
Matrix Gate → PASS（offline 375 / db 180 / total 555 / residue 0）
```
