# Phase 4.1 Step 37：Real Evidence Persistence Implementation（Decision Record）

- 类型：Implementation Decision
- 前置：`docs/decisions/Phase/Phase 4.1 Step 36：Real Evidence Persistence & Provenance Boundary.md`
  与 `docs/evaluation/Phase 4.1 Step 36：...`（Contract 冻结）
- 对应评估：`docs/evaluation/Phase 4.1 Step 37：Real Evidence Persistence Evaluation.md`

---

## Context

Step 36 审计确认：production 无 Evidence / Annotation ORM、Repository、Service，
真实 DB 持久化不存在；但 Contract（状态机 / 字段 / Provenance / 关联 / 幂等 /
事务 / 安全）已冻结。Step 37 首次允许落地生产持久化。

## Decision

将 Step 36 Contract 落地为**真实 ORM + Repository + PostgreSQL**，不扩展业务能力：
只增加两张 `ai_ops` 表与一个最小 Repository；API / Service 均不新增。

## Schema

沿用既有 schema `ai_ops`（与 conversation / 观测表同轴；**不**进入业务 schema public）。
建表沿用项目现状：`init_db() → Base.metadata.create_all()` —— **不引入 Alembic**。
禁止修改已有业务表、删除表、触碰 WMS 表、执行真实业务数据迁移。

## ORM

```text
ai_ops.evidence_record               （Evidence identity + provenance + 状态）
ai_ops.evidence_annotation_record    （annotation + evidence_id 稳定关联）
```

沿用现有风格：Base / Mapped+mapped_column / comment / String(32) 状态（无 ENUM）/
DateTime(timezone=True)+server_default=now()。
不新增 Step 36 明确禁止的字段（source_ref / content_ref / locator / file_path /
raw_content / conversation_content）；不保存会话正文与 LLM 内容。

## Repository

`EvidenceRepository` 提供最小方法集（create/get/find_by_dataset/update_status/
create_annotation/list_annotations），遵循现有 repository 风格：
frozen Row DTO、显式读列、`SQLAlchemyError → EvidenceRepositoryError`。
不提前实现大量 CRUD；不新增 Service（本 Step 范围为持久化边界）。

## Transaction

沿用既有事务归属（**Repository 持有事务**，Step 4 §4 方案 A），不引入新事务框架。
`create_annotation` 为原子单元：读 Evidence → INSERT annotation → 推进 Evidence 状态；
失败整体回滚 → 无孤儿 Annotation、Evidence 状态不错误推进。

## Constraints

```text
幂等：uq_evidence_source_dataset (source_type, dataset_version)
      —— 应用层 first-write-wins + 数据库层唯一约束兜底（不依赖应用层 alone）
关联：annotation.evidence_id → evidence.evidence_id（FK，ON DELETE CASCADE）
去重：uq_evidence_annotation_evidence_case_version (evidence_id, case_id, annotation_version)
索引：ix_evidence_record_status · ix_evidence_annotation_evidence_id
```

## Security

持久化列不含 api_key / password / authorization / database_url /
connection_string / llm_secret / token；不保存 content / prompt / SQL / embedding。
`project_id` 不进入本层（保持"只是业务上下文，非 authorization / tenant boundary"）。

## Non-goals

```text
Agent / MCP / Memory / Workflow / Chat API / Streaming
Review API / Finalization API
Provenance 重新设计 · Multi-tenancy · RBAC
Alembic 引入 · 业务表改动 · 真实数据迁移
Evidence case 集合持久化（Step 31 conversations 的落库粒度）
Annotation dependency / reference / impact 明细落库
```

## Evidence

```text
tests/test_evidence_persistence_db.py → 7 passed（RUN_DB_TESTS=1；真实 PostgreSQL）
python -m pytest -q（离线）→ 5923 passed / 640 skipped / 0 failed / 0 errors
compileall → OK
Matrix Gate → PASS（offline 375 / db 180 / total 555 / residue 0）

DB writes：仅测试 DB 内本测试创建的记录（module 级残留校验通过）
Production DB Writes = 0
```

## Remaining Gaps

1. Evidence 的 case 集合（Step 31 conversations）尚无持久化粒度；
2. Annotation 的 dependency / reference / impact 明细尚未落库；
3. 尚无 Evidence / Annotation Service（业务编排层）；
4. G3 / G4 仍 BLOCKED（无真实数据 / 无真实标注）。
