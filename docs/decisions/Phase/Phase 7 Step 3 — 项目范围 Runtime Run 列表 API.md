# Phase 7 Step 3 — 项目范围 Runtime Run 列表 API

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Phase 7 Step 2 的统一详情 API 基础上，增加 Agent 和 Workflow 的项目范围列表查询能力。

现有详情接口保持不变：

```http
GET /api/runtime-runs/agent/{run_id}
GET /api/runtime-runs/workflow/{run_id}
```

建议新增：

```http
GET /api/runtime-runs/agent
GET /api/runtime-runs/workflow
```

目标是提供统一的查询入口和响应结构，同时保留两个 Runtime 各自独立的认证、项目隔离及状态语义。

本阶段只增加**列表查询能力**，不修改执行流程。

## 二、先检查现有实现

阅读：

```text
backend/app/api/runtime_read.py
backend/app/api/runtime_read_auth.py
backend/app/dto/runtime_read_api.py
```

以及现有：

```text
AgentReadService
WorkflowReadService
Agent Repository
Workflow Repository
```

重点确认：

1. 两个 Repository 当前的数据查询方式。
2. 项目范围如何传递和验证。
3. 是否已有状态过滤、排序、分页实现。
4. 当前模型中实际存在的时间字段。
5. 统一详情 API 的响应 DTO 和认证方式。

**先阅读真实代码，再确定最小实现。不要假设 Repository 的接口。**

## 三、列表 API

按现有路由和项目约定，实现：

```http
GET /api/runtime-runs/agent?limit=20&offset=0
GET /api/runtime-runs/workflow?limit=20&offset=0
```

如果项目已有统一分页参数规范，优先沿用。

要求：

- `limit` 有合理默认值和最大值。
- `offset` 不允许为负数。
- 参数错误返回项目现有的标准错误格式。
- 支持列表为空的情况。
- 排序必须稳定；使用实际存在的时间字段，并以唯一 ID 作为必要的排序辅助键。
- 如果现有设计适合状态过滤，可复用已有能力；不要为本阶段引入复杂查询 DSL。

两种 Runtime 分别执行查询，不要求一次请求跨域合并两类记录。

## 四、项目隔离与认证

必须复用 Phase 7 Step 2 已有的认证边界：

- Agent 列表使用 Agent Read 域认证。
- Workflow 列表使用 Workflow Read 域认证。
- 项目范围只能来自服务端认证配置。
- 不允许客户端通过 `project_id` 查询参数、请求体或其他输入扩大查询范围。
- 查询必须在数据库层按授权项目过滤，不能先查询全部记录再在 Python 中过滤。
- 未配置认证时保持 fail-closed。
- 不得暴露其他项目的运行记录。

列表查询必须支持真实项目隔离测试，而不仅是 Mock 断言。

## 五、统一列表 DTO

复用 `backend/app/dto/runtime_read_api.py` 中的公共字段和类型定义。

列表项只返回必要的概要字段，例如：

```text
runtime_type
run_id
project_id
status
started_at
```

具体字段以两个 Runtime 的真实模型为准：

- 不伪造缺失字段。
- 不把 Agent 和 Workflow 的不同状态强行映射为同一状态。
- 列表响应不包含完整的步骤、节点、审批或启动输入。
- 不暴露数据库 Session、Connection、凭据或敏感配置。

建议响应结构遵循现有 API 风格，例如：

```json
{
  "items": [],
  "limit": 20,
  "offset": 0,
  "total": 0
}
```

只有在能够通过真实查询可靠计算总数时才返回 `total`。不要伪造总数。

## 六、Repository 与数据库范围

由于当前两域尚无项目范围列表能力，可以在必要时增加最小的只读 Repository 方法。

允许：

- 增加按授权项目查询 Run 的方法。
- 增加必要的只读总数查询。
- 增加排序和分页。
- 增加对应测试。

禁止：

- 修改 Agent / Workflow 状态机。
- 修改命令 API。
- 修改数据库结构或增加迁移。
- 引入新的 ORM 或查询框架。
- 为列表查询增加全局管理员权限。
- 实现跨项目的全局 Run 列表。

## 七、测试要求

### 离线测试

至少覆盖：

1. Agent 列表正常响应。
2. Workflow 列表正常响应。
3. 空列表。
4. 分页参数边界。
5. 稳定排序。
6. Agent 与 Workflow DTO 不混淆。
7. 未认证返回正确错误。
8. 未配置认证保持 fail-closed。
9. 客户端 `project_id` 不能改变授权范围。
10. 响应不包含敏感字段。

### DB 集成测试

使用现有 PostgreSQL 测试环境，至少验证：

1. 两个项目各自只能查到授权范围内的记录。
2. Agent 列表不会包含 Workflow 记录。
3. Workflow 列表不会包含 Agent 记录。
4. 分页结果正确且排序稳定。
5. 总数与查询条件一致（如果提供 `total`）。
6. 测试数据被正确清理，最终无残留。

优先复用现有测试 fixture，不创建新的数据库结构。

## 八、回归验证

先运行新增测试和相关历史测试：

```powershell
python -m pytest -q tests/test_runtime_read_api.py
python -m pytest -q tests/test_runtime_read_api_db.py
```

然后按项目现有方式执行全量离线测试和 DB 测试。

编译检查：

```powershell
python -m compileall -q backend tests
```

不要为本阶段重复实现全量测试分片框架。复用已验证的方式即可。

## 九、完成报告

只报告：

1. 新增和修改文件。
2. 实际新增的 API 路径。
3. 分页与排序规则。
4. Agent 列表结果。
5. Workflow 列表结果。
6. 认证和项目隔离测试结果。
7. 新增测试及回归测试结果。
8. compileall 结果。
9. 数据库结构是否变化。
10. 当前限制。

## 十、STOP

完成 Phase 7 Step 3 后立即停止。

不要推送、创建 PR 或部署。

不要进入 Phase 7 Step 4。

不要修改 Runtime 状态机或扩展其他功能。