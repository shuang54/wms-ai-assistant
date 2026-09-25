# Text-to-SQL Dangerous SQL Validator Security E2E — Phase 3.9.7

> 本阶段补齐 Phase 3.9.6 报告第 6 节 Q7 明确指出的安全验证缺口：
> **验证已知危险 SQL 进入真实链路后，是否被真实 SQL Validator 拒绝，
> 并确认它不会进入 SQL Executor。**
>
> 本阶段是**安全集成测试阶段**，不是生产功能开发。

---

## 1. Scope

```text
- Offline            : Yes（无网络）
- LLM                : NO（未调用 DeepSeek / 任何真实模型）
- Database           : NO（不连业务库，不执行任何 SQL）
- Production logic   : 未修改（Validator / Executor / Generator / Runner 全部原样）
- Validator          : REAL —— SQLValidatorService
- Generator          : STUB —— 强制返回危险 SQL
- Executor           : SPY —— 只记录调用，不执行
- Dataset / Snapshot : 未修改（3.9.3 Dataset、3.9.5 Snapshot、3.9.6 Analysis 均未改动）
- New dependencies   : 无
```

被测链路：

```text
StubDangerousGenerator（强制返回危险 SQL，并伪造 validated=True）
        ↓
TextToSQLEvaluationRunner（生产逻辑，未修改）
        ↓
Real SQLValidatorService（真实实现）
        ↓
REJECT
        ↓
SpyExecutor.call_count == 0
```

被测 case 通过 `with_execution_required()` 显式打开 `must_execute=True`
——即「**即使业务要求执行**，危险 SQL 也不得进入 Executor」。

---

## 2. Test Matrix

| SQL Type | Generator 产出 | Validator | Executor calls | 错误码 |
| --- | ---: | --- | ---: | --- |
| `DELETE FROM knowledge_document` | Yes | **Reject** | **0** | `NON_READ_ONLY` |
| `UPDATE knowledge_document SET title = 'x'` | Yes | **Reject** | **0** | `NON_READ_ONLY` |
| `INSERT INTO knowledge_document (title) VALUES ('x')` | Yes | **Reject** | **0** | `NON_READ_ONLY` |
| `DROP TABLE knowledge_document`（DDL） | Yes | **Reject** | **0** | `DANGEROUS_OPERATION` |
| `SELECT ...; DELETE ...`（多语句） | Yes | **Reject** | **0** | `MULTI_STATEMENT` + `NON_READ_ONLY` |
| `WITH x AS (DELETE ... RETURNING id) SELECT * FROM x`（CTE 内嵌写） | Yes | **Reject** | **0** | `NON_READ_ONLY` |
| `SELECT id FROM knowledge_document LIMIT 1`（只读） | Yes | **Accept** | **1** | （无错误） |

全部危险 SQL 使用当前项目真实存在的表（`public.knowledge_document`），
避免「因表/字段不存在」导致的失败原因混杂。

补充验证：

- `TextToSQLEvaluationRunner` 默认校验器确为真实 `SQLValidatorService`
  （防止误用 Fake Validator）；
- 真实 `TextToSQLService` + Fake LLM（返回 `DELETE`）场景下，
  Generator 内部校验连续拒绝 → 预算耗尽抛 `TextToSQLRetryExceededError`
  → 无 SQL 交付 → Executor 0 次调用；
- 同一个 Spy 顺序跑完全部危险类型，累计 `call_count` 仍为 0。

---

## 3. Security Boundary

```text
Dangerous SQL（由 Stub Generator 强制产出）
        ↓
Real SQLValidatorService
        ↓
REJECTED（NON_READ_ONLY / DANGEROUS_OPERATION / MULTI_STATEMENT）
        ↓
Executor NOT called（call_count = 0）
```

关键断言形式（§九 / §十八）：

```python
assert result.validation_passed is False          # 真实 Validator 拒绝
assert expected_code in result.validation_error_codes
assert executor.call_count == 0                   # 硬断言
assert executor.executed_sqls == []               # 硬断言
```

**不是** `assert dangerous_sql not in executed_sql` 这类弱断言
——后者在 Executor 根本没被调用时恒真，无法证明阻断。

同时验证「危险 SQL 确实到达了 Validator」：
断言 `result.generated_sql == <危险 SQL>` 且 `validation_error_codes` 非空，
确保拒绝来自真实校验，而不是「没生成 SQL」造成的假象。

---

## 4. What this proves

在本测试覆盖的危险 SQL 类型中：

> **真实 SQL Validator 能够拒绝危险 SQL，并阻止其进入 Executor。**

具体：

1. DML（DELETE / UPDATE / INSERT）、DDL（DROP）、多语句、
   CTE 内嵌写操作，均被真实 Validator 拒绝；
2. 拒绝发生在 Executor 之前——即使 case 显式要求执行（`must_execute=True`），
   Executor 调用数为 0；
3. 只读 SELECT 仍被接受并可到达 Executor → Validator 不是「拒绝一切」，
   因此上述拒绝断言是有意义的；
4. 真实 `TextToSQLService` 在 LLM 产出危险 SQL 时，
   其内部校验 + 重试预算也会耗尽并明确失败，不交付可执行的危险 SQL。

测试验证到的层次（§十二，如实标注）：

| 层次 | 是否本测试真实覆盖 |
| --- | --- |
| Stub Generator → Runner | Yes |
| Runner → Real `SQLValidatorService` | **Yes（真实实现）** |
| Validator Reject → 阻断 Executor | **Yes（Spy 断言 0 次）** |
| 真实 `SQLExecutorService` 自身行为 | No（Executor 为 Spy，未使用真实实现） |

---

## 5. What this does NOT prove

本阶段**不**声称：

- 所有 SQL 注入变体都已覆盖（仅覆盖上表 6 类 + CTE 形态）；
- 所有 sqlglot 解析边界都已覆盖（未做穷举/模糊测试）；
- `SQLExecutorService` 自身安全性已完整验证
  （本测试用 Spy 替代 Executor，只证明「没被调用」，未验证 Executor 内部防护）；
- 生产环境安全性 100%；
- LLM 安全性 100%（本阶段未调用任何真实模型，LLM 层的拒绝行为不在本测试范围）；
- 数据库层权限 / 行级安全 / 审计等防护措施已验证。

此外：

- 本测试**不修改** 3.9.5 安全基线。`safety_delete_all_documents` 仍保持
  `EXPECTATION_MISMATCH` + `SECURITY_LLM_REFUSAL` + `SECURITY_EXPECTATION_MISMATCH`；
  本阶段只是新增一层 **Controlled Validator Security E2E**，不是重跑 3.9.5。
- 3.9.5 Snapshot、3.9.6 Analysis Snapshot、3.9.3 Dataset 的 SHA256 均未变化。

---

## 6. Test Inventory（13 项）

| # | 测试 | 覆盖点 |
| --- | --- | --- |
| 1 | `test_delete_rejected_before_executor` | DELETE → Reject → Executor 0 |
| 2 | `test_update_rejected_before_executor` | UPDATE → Reject → Executor 0 |
| 3 | `test_insert_rejected_before_executor` | INSERT → Reject → Executor 0 |
| 4 | `test_ddl_rejected_before_executor` | DDL → Reject → Executor 0 |
| 5 | `test_multi_statement_rejected_before_executor` | 多语句 → Reject → Executor 0 |
| 6 | `test_cte_wrapped_delete_rejected_before_executor` | CTE 内嵌写 → Reject → Executor 0 |
| 7 | `test_read_only_select_passes_and_reaches_executor` | 正向：SELECT Accept 且 Executor 1 |
| 8 | `test_real_validator_accepts_read_only_directly` | 真实 Validator API 直接入口（正向） |
| 9 | `test_runner_uses_real_validator_implementation` | 禁止 Fake Validator 的结构性保证 |
| 10 | `test_real_validator_rejects_dangerous_sql_directly` | 真实 Validator API 直接入口（全类型） |
| 11 | `test_generator_output_is_what_validator_inspected` | 危险 SQL 确实到达 Validator |
| 12 | `test_real_service_blocks_dangerous_llm_output` | 真实 TextToSQLService + Fake LLM 边界 |
| 13 | `test_single_spy_stays_untouched_across_all_dangerous_types` | 单 Spy 累计 0 次调用 |

文件：`tests/test_text_to_sql_validator_security_e2e.py`（唯一新增代码文件）。
