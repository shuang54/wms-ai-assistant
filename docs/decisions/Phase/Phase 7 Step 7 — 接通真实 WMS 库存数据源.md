# Phase 7 Step 7 — 接通真实 WMS 库存数据源

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

让 `get_inventory` 能查询真实存在的 WMS 库存数据，而不是只证明 LLM 能选择 Tool。

### 1. 查清真实数据源

直接检查：

- 当前 `DATABASE_URL` 对应数据库的实际用途和连接目标（禁止输出连接串或凭证）。
- PostgreSQL 中现有业务 Schema、表和视图。
- `get_inventory` Handler 的 SQL、字段映射和只读事务。
- Business Semantic、Project Configuration、Schema Explorer 中的真实库存定义。
- 项目现有 ERP/WMS 数据源连接配置和已有只读查询实现。

通过 `information_schema.tables`、`information_schema.columns` 等元数据确认实际表和字段，不要根据 `inventory` 这个名字推断业务表必然存在。参考 PostgreSQL 官方文档：https://www.postgresql.org/docs/16/information-schema.html

### 2. 根据检查结果直接处理

**情况 A：存在真实库存表或视图**

- 确认主键、物料编码、库存数量、仓库等真实字段。
- 修改 `get_inventory` Handler，使其使用实际表结构。
- 保留参数校验、参数化查询、项目授权和只读事务。
- 使用真实只读查询验证 `MAT001`；没有该物料时如实返回空结果。

**情况 B：库存数据位于另一套已配置的数据源**

- 复用现有连接层和项目配置。
- 如果连接已具备且安全，直接接通。
- 不复制整套数据库访问框架，不把业务数据库凭证写入代码。

**情况 C：当前环境根本没有业务库存数据源**

- 明确指出缺少哪个数据源或连接条件。
- 不建 `public.inventory`，不造测试数据，不伪造查询成功。
- 不要反复修改 Handler 来掩盖环境缺失。
- 转而完成当前代码能够独立交付的下一项核心能力。

### 3. 验证真实业务闭环

数据源可用时验证：

```text
Agent Command API
  → Agent Runtime
  → DeepSeek
  → get_inventory
  → Tool Authorization
  → 真实只读查询
  → Run / Step 持久化
  → Runtime Read / List
```

再验证终态 Resume 不会重复执行 Tool。

默认 pytest 仍然不得调用真实 LLM。真实 Smoke 继续使用显式开关。

### 4. 严格限制

- 不新增或伪造业务表。
- 不修改数据库 Schema。
- 不执行任何业务写操作。
- 不关闭认证、项目隔离或工具授权。
- 不重复运行无关的全量测试和治理门禁。
- 不提交 Git、不推送、不创建 PR、不部署。

### 5. 完成后简要汇报

只报告真实数据源是否找到、实际修改内容、针对性测试结果，以及是否还需要外部环境支持。

**立即开始检查并解决，不要停在方案分析上。**