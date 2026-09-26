# Text-to-SQL Refusal First-Class Result — Phase 3.9.25

> 解决 Phase 3.9.24 的两个 Production Blocker：生产 `TextToSQLService`
> 无法表达 LLM refusal、refusal 被 API 映射为 HTTP 500 / retry exhausted。
> 本阶段只新增"拒绝结果类型"，不优化 Prompt、不弱化任何安全边界、
> 不改变正常 SQL 生成逻辑。

## 1. Result Model

采用**扩展现有 DTO**（而非新增两个类型），与项目 frozen-dataclass 风格一致，
全部既有构造点向后兼容：

```python
TextToSQLResult(
    question: str,
    sql: str | None,
    attempts: int,
    validated: bool,
    referenced_tables: tuple[str, ...],
    status: str = "sql",                 # "sql" | "refusal"
    refusal_reason: str | None = None,   # 仅 refusal：destructive_request_not_supported
)
```

- `status="sql"`：`sql` 为通过 Validator 的只读 SELECT（旧行为，默认值）。
- `status="refusal"`：`sql=None`、`validated=False`、`refusal_reason` 非空。

## 2. Refusal 识别规则

只识别 Prompt v2 约定的完整 marker（`backend/app/prompts/v2/*`）：

```text
-- REFUSED: destructive request is not supported (read-only service)
```

- 去除前后空白；整个输出恰为一个 markdown fence 时取 fence 内文；
- 折叠空白 + 忽略大小写后必须**整体等于** marker；
- 不做"包含 REFUSED 就算"的模糊匹配：

| 输入 | 判定 |
|---|---|
| `-- REFUSED: ...`（裸 / 带空白 / fence 内） | REFUSAL |
| `SELECT '-- REFUSED: ...' AS message LIMIT 1` | 正常 SQL（Validator 判定） |
| fence 前后附带其它文字 | 非 refusal（走既有 extract_sql → Validator） |
| 空输出 / 其它注释 | 非 refusal（既有重试路径） |

## 3. Refusal Flow（实际代码）

```text
LLM
  ↓ raw output
TextToSQLService.generate()          # text_to_sql_service.py
  ↓ is_refusal_output(raw)           # Validator 之前
  ↓ 命中 → TextToSQLResult(status="refusal", sql=None,
  ↓                        refusal_reason="destructive_request_not_supported")
  ↓ 立即返回：不调用 Validator、不进入 Executor、不消耗 retry budget
AIOrchestratorService._run_text_to_sql()
  ↓ status == REFUSAL → AIOrchestrationResult(
  ↓     content="当前 AI 数据查询服务仅支持只读查询，不支持删除、修改等操作。",
  ↓     data=None, metadata={"refused": True})     # Executor 不被调用
  ↓ （不走 Exception → 500 路径）
/api/ai/chat → _to_chat_response()
  ↓ data is None → data=None（复用现有 ChatResponse envelope，无新 schema）
ChatResponse(route="text_to_sql", content=<只读拒绝信息>, data=None)
```

内部 `refusal_reason` 不暴露给用户（不进入 metadata / detail）。

## 4. Normal SQL Flow（未改变）

```text
LLM → extract_sql → SQLValidatorService.validate → TextToSQLResult(status="sql")
invalid SQL → 既有 retry prompt 重试路径（不变）
```

## 5. Probe 证据（scripted fake LLM，0 network / 0 DB）

| Probe | status | llm_calls | validator_calls | attempts |
|---|---|---:|---:|---:|
| bare refusal | refusal | 1 | **0** | 1 |
| fenced refusal | refusal | 1 | **0** | 1 |
| normal SELECT | sql | 1 | 1 | 1 |
| invalid SQL（缺 LIMIT） | sql（第 2 次成功） | 2 | 2 | 2 |
| SELECT 含 refusal 字面量 | sql | 1 | 1 | 1 |
| 纯说明文字 | 既有路径：TextToSQLRetryExceededError | 2 | 2 | 2 |

完整数据见 `tests/fixtures/text_to_sql/baselines/phase_3_9_25_refusal_result.json`。

## 6. Security Boundary（§九）

- Refusal → Executor 调用次数 = **0**（orchestrator 级断言）。
- SQLValidatorService / SQLExecutorService **零修改**；DELETE / UPDATE /
  INSERT / DROP / ALTER / TRUNCATE / 多语句仍全部被 Validator 拒绝。
- Refusal 只是 LLM 层行为 + 接口语义，**不是**安全机制；最终安全边界仍是
  Validator + Executor（`BEGIN READ ONLY`）。

## 7. Tests

- 新增 `tests/test_text_to_sql_refusal_result.py`：24 项（识别规则 8、
  service 行为 9、orchestrator 3、API 2、安全边界 2）。
- 更新 `tests/test_text_to_sql_prompt_v2_promotion_gate.py`：3.9.24 gate
  探针识别 first-class refusal 后的新语义（refusal/API 两个 blocker 已解除，
  live gate = READY_FOR_PROMOTION），并新增"冻结 3.9.24 snapshot 保持
  NOT_READY 原样"守护测试。
- 全量 `pytest`：1869 passed, 267 skipped, 0 failed。
- `python -m compileall backend tests scripts`：0 错误。

## 8. 状态声明（§十四）

- 本阶段**不关闭** Promotion Gate：不宣称 v2 已正式生产，未修改
  production prompt = v2，未改写 3.9.24 历史 snapshot（其 NOT_READY
  结论作为冻结历史保留）。
- Phase 3.9.25 = **PASS**；等待下一阶段重新执行 Promotion Gate。
