# Phase 6 Step 8B-2C — Targeted Residue Cleanup

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

清理此前审计已确认归属于本轮测试实验的 `ai_ops.tool_execution_record` 记录：

```text
ID 70–83
预期数量：14 行
```

已知归属：

* ID 70–76：片 7 复现实验。
* ID 77–83：`test_project_datasource_isolation.py` 单测实验。

本轮仅进行定向清理和只读核验，不继续修复代码、不运行全量测试。

## 二、清理前必须重新核验

先连接当前测试数据库，执行只读查询，检查：

1. ID 70–83 是否仍然存在。
2. 存在的记录数量是否为 14。
3. 每行的 `request_id`、Tool 名称和其他可用字段是否仍与之前的实验归属证据一致。
4. 这些记录是否存在与已知归属冲突的情况。

不要仅凭历史报告直接删除。

如果记录数量不是 14、ID 不完整，或记录归属存在任何疑点：

**立即停止，不执行 DELETE，报告实际查询结果。**

## 三、定向清理

只有清理前核验全部通过，才允许在事务中删除明确确认的目标记录。

要求：

* 仅针对经过核验的 ID 集合。
* 使用参数化 SQL。
* 使用 `DELETE ... WHERE id IN (...) RETURNING id` 或项目等价方式。
* 删除前后均记录实际行数。
* 预期删除数量必须为 14。
* 删除数量不符时立即回滚并报告，不得扩大删除范围。
* 不允许按水位、时间范围、request_id 模糊匹配或执行全表清理。
* 不修改任何其他表。
* 不修改代码、Schema、测试断言或 Git 状态。
* 不执行 commit / push。

PostgreSQL 的 `DELETE` 只应作用于 `WHERE` 条件明确匹配的记录；无条件 `DELETE` 会删除表中全部行。必须遵守本任务限定的精确 ID 范围。参考：[PostgreSQL DELETE 文档](https://www.postgresql.org/docs/17/sql-delete.html)。

## 四、清理后只读核验

确认：

1. ID 70–83 均已不存在。
2. 本次实际删除数量符合预期。
3. `llm_usage_record` 行数。
4. `tool_execution_record` 行数。
5. `rag_execution_record` 行数。
6. `assistant_outcome_record` 行数。
7. 没有执行任何其他数据库写操作。

注意：其他表不一定必须为空；必须报告真实计数，不要为追求全零而删除其他记录。

## 五、禁止事项

本轮禁止：

* 清理 ID 70–83 以外的记录。
* 清理其他表。
* 运行全量离线或 DB 测试。
* 修改任何文件。
* 修改数据库 Schema。
* commit / push。
* 宣称 Phase 6 Exit 已通过。

## 六、最终报告

```text
【Phase 6 Step 8B-2C — Targeted Cleanup】

1. 清理前 ID 核验
2. 归属证据核验
3. 实际删除数量
4. 删除后目标 ID 核验
5. 四张表的只读行数
6. 是否发生范围外删除
7. 是否修改文件
8. 是否修改 Schema
9. Git 状态

全量测试 = NOT RUN
Git commit/push = NOT EXECUTED
Phase 6 Exit = 暂不判定
```

完成后停止，等待审阅。
