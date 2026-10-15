# Phase 7 Recovery — 清理已确认的 15 行测试残留

## 目标

仅清理专用测试数据库 `wms_ai_test` 中已经核验的 15 条 `tool_execution_record` 残留。

### 1. 固定清理范围

数据库身份必须为：

- Host：`localhost`
- Port：`5433`
- Database：`wms_ai_test`

目标表：

`ai_ops.tool_execution_record`

目标 ID：

```python
TARGET_IDS = [
    94, 95, 96, 97, 98,
    147, 148, 149, 150, 151,
    200, 201, 202, 203, 204,
]
```

## 2. 执行前硬性校验

1. 使用非空 `DATABASE_URL` 显式连接测试库。
2. 检查 Engine 的实际 host、port、database；任一不匹配立即退出。
3. 查询目标 ID，确认恰好 15 行，ID 集合完全一致。
4. 对每行复核：
   - `tool_name = 'get_inventory'`
   - `project_id IS NULL`
   - `success = TRUE`
   - `error_type IS NULL`
5. 若任一条件不满足，不执行删除，报告实际差异。

不得输出数据库密码或完整连接凭证。

## 3. 事务内定向删除

使用单一事务执行，禁止自动提交。

```sql
DELETE FROM ai_ops.tool_execution_record
WHERE id IN (
    94, 95, 96, 97, 98,
    147, 148, 149, 150, 151,
    200, 201, 202, 203, 204
)
AND tool_name = 'get_inventory'
AND project_id IS NULL
AND success = TRUE
AND error_type IS NULL
RETURNING id;
```

必须检查 `RETURNING id`：

- 返回数量必须为 15。
- 返回 ID 集合必须与 `TARGET_IDS` 完全一致。
- 若数量或 ID 集合不一致，立即 `ROLLBACK`，不得提交部分清理结果。

只有全部断言通过，才允许 `COMMIT`。PostgreSQL 的 `RETURNING` 可以返回实际被删除的行，以便核验。<Cite refs={["turn219672search0","turn219672search2"]}/>

## 4. 提交后核验

只读检查以下四张表的绝对行数：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
ai_ops.assistant_outcome_record
```

预期结果：

```text
llm_usage_record         = 0
tool_execution_record    = 0
rag_execution_record     = 0
assistant_outcome_record = 0
```

另外确认目标 ID 已全部不存在。

如果任何表仍有非零记录，如实报告，不得继续扩大删除范围。

## 5. 严格禁止

- 不连接应用库 `wms_ai`。
- 不删除其他 ID。
- 不按 `project_id IS NULL` 批量删除。
- 不执行全表 `DELETE` 或 `TRUNCATE`。
- 不清理 Docker 卷。
- 不修改生产代码、冻结基线或 Gate。
- 不执行 Git add、commit、push。

## 6. 最终报告

输出：

1. 数据库身份断言结果
2. 删除前目标 ID 与属性核验
3. 实际删除行数及 `RETURNING id` 结果
4. 事务是否提交或回滚
5. 四张表的清理后绝对行数
6. 目标 ID 是否全部不存在
7. Git 状态是否变化

完成后立即 STOP。