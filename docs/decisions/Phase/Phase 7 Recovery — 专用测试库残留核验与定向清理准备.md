# Phase 7 Recovery — 专用测试库残留核验与定向清理准备

## 当前状态

- A2 DB ACCEPTANCE：PASSED
- Matrix Gate：PASS
- 已知残留：专用测试库 `wms_ai_test` 中存在 15 行 `ai_ops.tool_execution_record`
- 来源：报告称为被中断的 pytest 会话产生的 `project_id IS NULL` 的 `get_inventory` 记录

## Step 1：只读核验

1. 显式设置非空 `DATABASE_URL`，指向 `localhost:5433/wms_ai_test`。
2. 在执行任何数据库操作前，断言实际 Engine 的数据库名为 `wms_ai_test`、端口为 `5433`。
3. 只读查询这 15 行的：
   - 主键 ID
   - `project_id`
   - `request_id`
   - 工具名称
   - 创建时间及其他可用于归属确认的非敏感字段
4. 核对行数、ID 集合、时间范围与中断会话的证据。
5. 同时只读统计四张观测表的绝对行数。

不得输出连接凭证或敏感请求内容。

## Step 2：生成清理方案，不执行删除

生成一份清理预览，列出：

- 精确 ID 集合
- 每个 ID 的归属证据
- 预期删除行数
- 删除后的预期四表计数
- 无法确认归属的记录

清理范围必须限定为已确认的测试残留，不得按 `project_id IS NULL` 批量删除，也不得使用全表 DELETE。

**本轮禁止执行 DELETE、TRUNCATE、清空表、清理卷或其他数据修改操作。**

## Step 3：报告并停止

输出：

1. 实际数据库身份
2. 15 行是否全部复核成功
3. 精确 ID 集合
4. 四表绝对行数
5. 清理预览 SQL（必须有精确 ID 条件）
6. 未能确认的记录
7. Git 状态

完成后停止，等待单独的清理授权。