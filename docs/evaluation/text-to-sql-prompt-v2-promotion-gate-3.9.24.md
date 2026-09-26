# Prompt v2 Production Promotion Gate — Phase 3.9.24

> Validation only：本阶段不修改任何 Prompt / 生产代码，
> 不执行正式上线。全部探针离线运行（fake LLM 注入 / 静态核验 /
> 历史快照证据），0 LLM / 0 DB / 0 network。

## Promotion Status

```text
NOT_READY
```

## Gate Matrix

| Gate | Result | Blocker |
|---|---|---|
| Normal T2S compatibility | PASS | — |
| Validator safety boundary | PASS | — |
| Executor safety boundary | PASS | — |
| Project isolation | PASS | — |
| RAG / Tool routing isolation | PASS | — |
| Refusal handling | BLOCKED | REFUSAL_HANDLING_NOT_SUPPORTED_BY_PRODUCTION_INTERFACE |
| API refusal handling | BLOCKED | API_REFUSAL_NOT_A_CLEAR_READ_ONLY_MESSAGE |
| Historical regression | PASS | — |

## Critical Finding

Prompt v2 refusal marker is treated as EMPTY_SQL by the production TextToSQLService: extract_sql passes the comment through, the Validator rejects it (EMPTY_SQL), the retry loop re-asks up to max_attempts times, and generate() ends in TextToSQLRetryExceededError. There is NO explicit refusal result type in the production interface.

生产链路（以代码为准）：

```text
User
  ↓
POST /api/ai/chat            (orchestrator_chat.py)
  ↓
AIOrchestratorService.execute()
  → Router → route=text_to_sql → capability check
  → ProjectContext resolve (project / schema / semantic)
  → RelevantTableSelector.select → allowed_tables
  → SchemaComposer / SemanticFilter / Serializer
  → TextToSQLService.generate()
      → LLM (system+user prompt; retry prompt ≤ max_attempts)
      → extract_sql → SQLValidatorService.validate → retry
      → 全部无效 → TextToSQLRetryExceededError（无 SQL 返回）
  → SQLExecutorService.execute（再校验 + BEGIN READ ONLY）
  → AIOrchestrationResult → ChatResponse
```

## Blockers

- `REFUSAL_HANDLING_NOT_SUPPORTED_BY_PRODUCTION_INTERFACE`
- `API_REFUSAL_NOT_A_CLEAR_READ_ONLY_MESSAGE`

## Probe evidence

### normal_t2s_compatibility — PASS

With v2 prompts wired in, the production TextToSQLService still (a) returns a validated SELECT on the first attempt and (b) recovers via the retry prompt when the Validator rejects the first attempt.

```json
{
  "normal_select": {
    "validated": true,
    "attempts": 1,
    "sql": "SELECT id, title FROM public.knowledge_document LIMIT 10",
    "llm_calls": 1
  },
  "retry_path": {
    "validated": true,
    "attempts": 2,
    "llm_calls": 2
  }
}
```

### validator_safety_boundary — PASS

SQLValidatorService (unchanged) rejects every write / DDL / multi-statement / no-LIMIT statement, regardless of which Prompt produced the SQL. The Validator remains the final SQL safety boundary; Prompt v2's refusal is ONLY an LLM behavior and is NOT a security mechanism.

```json
{
  "delete": {
    "valid": false,
    "errors": [
      "NON_READ_ONLY",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "update": {
    "valid": false,
    "errors": [
      "NON_READ_ONLY",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "insert": {
    "valid": false,
    "errors": [
      "NON_READ_ONLY",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "drop": {
    "valid": false,
    "errors": [
      "DANGEROUS_OPERATION",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "alter": {
    "valid": false,
    "errors": [
      "DANGEROUS_OPERATION",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "truncate": {
    "valid": false,
    "errors": [
      "DANGEROUS_OPERATION",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "multi_statement": {
    "valid": false,
    "errors": [
      "MULTI_STATEMENT",
      "NON_READ_ONLY",
      "ROW_LIMIT_REQUIRED"
    ]
  },
  "no_limit": {
    "valid": false,
    "errors": [
      "ROW_LIMIT_REQUIRED"
    ]
  }
}
```

### executor_safety_boundary — PASS

SQLExecutorService (unchanged) still opens every execution in a BEGIN READ ONLY transaction with statement_timeout — DB-level write protection independent of any Prompt. (Runtime DB probe is NOT_APPLICABLE in this offline phase; the existing RUN_DB_TESTS-gated tests cover it.)

```json
{
  "begin_read_only_present": true,
  "statement_timeout_present": true,
  "runtime_db_probe": "NOT_APPLICABLE"
}
```

### project_isolation — PASS

Project A / Project B schemas remain disjoint in the offline bindings, and Phase 3.9.23 shows both isolation cases STABLE PASS across all three Candidate v2 runs. Project context / isolation is unaffected by Prompt v2.

```json
{
  "project_a_tables": [
    "schematable(schema_name='project_a', name='inventory', description=none, columns=(schemacolumn(name='material_code', data_type='character varying', nullable=true, default=none, ordinal_position=1, is_primary_key=false, description=none), schemacolumn(name='qty', data_type='character varying', nullable=true, default=none, ordinal_position=2, is_primary_key=false, description=none)), foreign_keys=())"
  ],
  "project_b_tables": [
    "schematable(schema_name='project_b', name='inventory', description=none, columns=(schemacolumn(name='material_code', data_type='character varying', nullable=true, default=none, ordinal_position=1, is_primary_key=false, description=none), schemacolumn(name='qty', data_type='character varying', nullable=true, default=none, ordinal_position=2, is_primary_key=false, description=none)), foreign_keys=())"
  ],
  "schemas_disjoint": true,
  "historical_3_9_23_isolation_stable": true
}
```

### routing_isolation — PASS

The Text-to-SQL prompts are consumed exclusively by TextToSQLService; RAG and Tool routing use their own prompts/paths. Prompt v2 changes cannot affect RAG / Tool routing.

```json
{
  "t2s_prompts_wired_in_text_to_sql_service": true
}
```

### refusal_handling — BLOCKED

Prompt v2 refusal marker is treated as EMPTY_SQL by the production TextToSQLService: extract_sql passes the comment through, the Validator rejects it (EMPTY_SQL), the retry loop re-asks up to max_attempts times, and generate() ends in TextToSQLRetryExceededError. There is NO explicit refusal result type in the production interface.

```json
{
  "plain_comment": {
    "outcome": "RETRY_EXHAUSTED",
    "exception": "TextToSQLRetryExceededError",
    "attempts": 3,
    "llm_calls": 3,
    "validation_errors": [
      "EMPTY_SQL"
    ]
  },
  "sql_fence": {
    "outcome": "RETRY_EXHAUSTED",
    "exception": "TextToSQLRetryExceededError",
    "attempts": 3,
    "llm_calls": 3,
    "validation_errors": [
      "EMPTY_SQL"
    ]
  }
}
```

### api_refusal_handling — BLOCKED

Production chain: TextToSQLRetryExceededError → wrapped by AIOrchestratorService._run_text_to_sql into AIOrchestratorExecutionError('Text-to-SQL 生成失败: ...') → mapped by /api/ai/chat to HTTP 500 ('AI 能力执行失败: ...'). The user therefore receives an internal-error-shaped response containing the retry-exhausted exception type, NOT a clear read-only refusal message. No traceback / SQL / credentials leak, but the refusal is not user-visible as a refusal.

```json
{
  "generation_error_wrapped": true,
  "execution_error_maps_to_500": true,
  "user_visible_detail": "HTTP 500: AI 能力执行失败: Text-to-SQL 生成失败: TextToSQLRetryExceededError"
}
```

### historical_regression — PASS

Phase 3.9.23 (frozen): Candidate v2 achieved 42/42 structural passes across 3 runs with 14/14 STABLE cases and ZERO regression cases.

```json
{
  "snapshot": "phase_3_9_23_prompt_v2_stability.json",
  "candidate_structural_pass_count": 42,
  "candidate_stable_cases": 14,
  "candidate_regression_cases": []
}
```

## Hashes

- dataset_sha256: `a9328e2d63565d70…`
- ground_truth_sha256: `e5167b137b9dd99c…`
- fixture_schema_sha256: `cc32c9d1bb5ecd2f…`
- fixture_data_sha256: `5a912ef37d68187e…`
- case_order_hash: `2d432701e34f36fd…`
- baseline(v1) prompt hash: `ff9e65337d276d92…`
- candidate(v2) prompt hash: `e8863caddfc8a51a…`
