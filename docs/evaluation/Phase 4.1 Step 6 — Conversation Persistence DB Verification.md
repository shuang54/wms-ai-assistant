# Phase 4.1 Step 6 — Conversation Persistence DB Verification

> 目标：在**真实 PostgreSQL** 上验证 Phase 4.1 Step 5 的 Conversation 持久化层。
> 本阶段**不新增功能 / 不新增 API / 不改 AI Core / 不改 ORM / Repository / Service Contract**。
> 生产代码改动：**0**（仅新增测试与本文档）。

```text
Offline Contract : PASS
DB-gated         : PASS（40 passed，全部真实执行）
Database         : AVAILABLE
```

---

## 1. Database Availability

| 项 | 结果 |
| --- | --- |
| DB configured | yes（`DATABASE_URL` 已配置；**不输出凭据 / 连接串**） |
| host | masked |
| port | 5432 |
| database | masked |
| `127.0.0.1:5432` | REACHABLE |
| `::1:5432` | REACHABLE |
| `localhost:5432` | REACHABLE |

对比：Step 5 执行时 PostgreSQL 不可达（`psycopg ConnectionTimeout`，260s 超时）；
本阶段数据库已恢复可用，DB-gated 用例首次完整执行。

---

## 2. 真实表结构（`information_schema` 读取）

### `ai_ops.conversation`

| # | 列 | 类型 | nullable | default |
| --- | --- | --- | --- | --- |
| 1 | conversation_id | character varying | NO | - |
| 2 | project_id | character varying | NO | - |
| 3 | created_at | timestamp with time zone | NO | now() |
| 4 | updated_at | timestamp with time zone | NO | now() |
| 5 | status | character varying | NO | 'ACTIVE' |

### `ai_ops.conversation_turn`

| # | 列 | 类型 | nullable | default |
| --- | --- | --- | --- | --- |
| 1 | turn_id | bigint | NO | nextval(conversation_turn_turn_id_seq) |
| 2 | conversation_id | character varying | NO | - |
| 3 | role | character varying | NO | - |
| 4 | content | text | NO | - |
| 5 | assistant_request_id | character varying | **YES** | - |
| 6 | created_at | timestamp with time zone | NO | now() |

### 索引（真实）

```text
conversation_pkey                                  (conversation)
conversation_turn_pkey                             (conversation_turn)
ix_conversation_turn_conversation_id_created_at     (conversation_id, created_at)
```

### 外键（真实）

```text
conversation_turn.conversation_id
    → conversation.conversation_id
      ON DELETE CASCADE
```

* **只有这一个外键**；`assistant_request_id` **不是**外键（correlation only）。

---

## 3. DB 验证结果（逐项）

| # | 验证项 | 结果 | 依据 |
| --- | --- | --- | --- |
| 1 | Create（conversation_id 非空、project_id 正确、status=ACTIVE、created_at/updated_at 非空、updated_at=created_at） | PASS | `test_create_conversation` |
| 2 | conversation_id ≠ assistant_request_id（uuid4 会话 ID，非 request ID） | PASS | `test_create_conversation`（长度 36、服务端生成） |
| 3 | Get（存在 → Row；不存在 → `None`，不与空 turns 混淆） | PASS | `test_get_conversation` / `test_get_missing_conversation_returns_none` |
| 4 | Archive（ACTIVE → ARCHIVED；`updated_at` 变化） | PASS | `test_archive_conversation` |
| 5 | Archive 幂等（第二次无错误、无 DB 写入、`updated_at` 不变） | PASS | `test_archive_is_idempotent` |
| 6 | Archive 不存在 → `ConversationNotFoundError` | PASS | `test_archive_missing_conversation` |
| 7 | Append USER（role=USER、`assistant_request_id IS NULL`、content 正确） | PASS | `test_append_user_turn` |
| 8 | Append ASSISTANT（role=ASSISTANT、request id 非空、content 正确） | PASS | `test_append_assistant_turn` |
| 9 | Ordering（USER/ASSISTANT/USER 顺序；`created_at` 非递减 + `turn_id` 严格递增） | PASS | `test_list_turns_ordering` |
| 10 | Ordering tie（**相同 created_at** 由 `turn_id ASC` 稳定解决） | PASS | 真实插入两行同 timestamp → 返回顺序 = turn_id 顺序（见 §4） |
| 11 | Empty turns（存在但无消息 → `()`；不是 `None`） | PASS | `test_list_turns_empty` |
| 12 | list_turns 会话不存在 → `ConversationNotFoundError`（不与空混淆） | PASS | `test_list_turns_missing_conversation` |
| 13 | Archived append → `ConversationArchivedError` 且 **DB turn count 不变（write before reject = 0）** | PASS | `test_append_to_archived_is_rejected_with_zero_writes` |
| 14 | Validation：USER + request id → 拒绝 | PASS | `test_user_turn_with_request_id_is_rejected` |
| 15 | Validation：ASSISTANT + `None` → 拒绝 | PASS | `test_assistant_turn_without_request_id_is_rejected` |
| 16 | Validation：empty content → 拒绝；invalid role → 拒绝 | PASS | `test_empty_content_is_rejected` / `test_invalid_role_is_rejected` |
| 17 | Atomicity：INSERT 成功 + UPDATE 失败 → **整体回滚**（Turn 不存在、`updated_at` 未变） | PASS | `test_failed_update_rolls_back_inserted_turn` |
| 18 | Delete 语义：删会话 → Turns 随 CASCADE 删除；观测记录**保留** | PASS | `test_delete_conversation_cascades_turns_and_keeps_observability` |
| 19 | Project binding：project_id 创建后不可变；只按 conversation_id 定位 | PASS | `test_project_id_is_immutable_after_turns` / `test_conversation_created_for_project_b_is_independent` |
| 20 | Security：库中只有冻结字段（无 api_key/password/Authorization/database_url/prompt/messages/SQL/RAG chunk/embedding/raw response） | PASS | 真实列清单（§2）+ `test_no_sensitive_columns_exist` |
| 21 | 正文往返（普通 user/assistant 文本，不做改写） | PASS | `test_normal_content_roundtrip` |

---

## 4. Ordering tie（相同 timestamp）

真实插入两行**相同 `created_at`** 的消息（`USER` / `ASSISTANT`），再读取：

```text
created_at identical : True
order by turn_id ASC : True
rows                 : [(10, 'USER', 'tie-1', None), (11, 'ASSISTANT', 'tie-2', 'step6-tie-req')]
```

结论：`ORDER BY created_at ASC, turn_id ASC` 在时间戳相同时由 `turn_id` 稳定 tie-break。

---

## 5. Residue

测试结束（含上述 tie 验证脚本自建自治会话）后的真实行数：

```text
conversation               0
conversation_turn          0
llm_usage_record           0
tool_execution_record      0
rag_execution_record       0
assistant_outcome_record   0
```

* conversation / conversation_turn 残留 = **0**（`created` fixture 按 conversation_id
  精确 DELETE；FK CASCADE 连带；**未使用 TRUNCATE**、未清空 ai_ops）；
* 观测表未被错误清理（本次环境中这些表本身无数据；删除语义用例在其内部显式清理了
  自己写入的 outcome 行，`tests/conftest.py` 的既有水位守卫亦兜底）。

---

## 6. 回归验证

```text
python -m pytest -q tests/test_conversation_persistence_contract.py      → 62 passed
python -m pytest -q tests/test_conversation_service_boundary_contract.py → 46 passed
python -m pytest -q tests/test_conversation_model_contract.py            → 39 passed
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_outcome_persistence.py
                                                                         → 32 passed（既有观测层 DB 测试未受影响）
python -m compileall -q backend tests scripts                            → OK
```

---

## 7. 测试运行口径（不混淆 skip 与 pass）

| 命令 | 结果 |
| --- | --- |
| `RUN_DB_TESTS=1 pytest -q tests/test_conversation_persistence_db.py` | **40 passed**（17 离线契约 + 23 DB-gated，全部真实执行） |
| `pytest -q tests/test_conversation_persistence_db.py`（离线） | **19 passed, 21 skipped**（DB-gated 明确 SKIP，不记为 pass） |

---

## 8. Production Changes

本阶段生产代码改动 = **0**：

```text
Conversation ORM         = unchanged
ConversationTurn ORM     = unchanged
ConversationRepository   = unchanged
ConversationService      = unchanged
```

`git status` 仅新增：测试（新增 Project binding 用例）与本文档。

---

## 9. Problems Found

无。真实 DB 验证未发现持久化层缺陷；所有冻结契约（字段 / 类型 / FK / 索引 /
排序 / 原子性 / 幂等归档 / 删除语义 / project 绑定 / 安全字段）均与实现一致。

---

## 10. Deferred

Conversation API · Chat API 接入 · Context Builder · Memory · Regenerate ·
Authentication / Authorization · `client_message_id` · Delete / 归档业务 API ·
Alembic / 迁移文件 · conversation detail 的 Trace / Timeline 组合端点。

---

## 11. 参考资料

* `docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md`
* `docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md`
* `tests/test_conversation_persistence_db.py`
