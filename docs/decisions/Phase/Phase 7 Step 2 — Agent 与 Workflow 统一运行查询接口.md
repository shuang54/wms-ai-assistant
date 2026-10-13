# Phase 7 Step 2 — Agent 与 Workflow 统一运行查询接口

## 一、阶段目标

在现有 Agent Runtime 和 Workflow Runtime 基础上，提供统一的运行查询入口。

目标是让调用方能够通过一致的 API 查询 Agent Run 和 Workflow Run，而不必分别理解两套内部数据结构。

```text
Client
  ↓
Unified Runtime Read API
  ├── Agent Runtime ReadService
  └── Workflow Runtime ReadService
         ↓
      Existing Repository
         ↓
      PostgreSQL
```

本阶段只做 **Read API**，不新增执行能力。

## 二、开始前先阅读真实代码

检查以下内容，路径以仓库实际结构为准：

- 现有 Agent Run Read API、DTO、ReadService
- 现有 Workflow Run Read API、DTO、ReadService
- Agent / Workflow 的 Repository 和模型
- 现有认证、项目作用域和分页约定
- 对应的 API 测试与 DB 集成测试

先确认两套接口已经提供哪些查询能力，再决定如何复用。

**不要为了统一接口而重写现有 ReadService 或 Repository。**

## 三、建议的最小 API

完成代码检查后，尽可能复用现有路由风格，提供统一查询入口，例如：

```http
GET /api/runtime-runs
GET /api/runtime-runs/{runtime_type}/{run_id}
```

其中：

- `runtime_type` 用于区分 `agent` 和 `workflow`。
- 列表接口支持按运行类型过滤。
- 详情接口查询指定类型的指定运行记录。
- 分页参数遵循项目已有约定。
- 如果现有接口不支持某项能力，不要为了统一而大规模扩展。

最终路径和参数应以现有架构为准。如果统一详情接口没有必要，优先采用更小的实现。

## 四、统一 DTO

只定义必要的公共字段，例如：

```text
runtime_type
run_id
project_id
status
created_at
updated_at
```

其他字段按需保留在类型专属的详情数据中。

要求：

- Agent 与 Workflow 的状态值不能被错误地强行映射。
- 不伪造缺失字段。
- 不暴露内部数据库 Session、Connection、认证凭据或敏感配置。
- 时间、分页和错误响应遵循现有 API 约定。
- 不为统一 DTO 修改底层持久化模型。

## 五、权限与项目隔离

必须复用现有认证与项目作用域机制。

1. 项目范围由服务端认证配置确定，不接受客户端通过查询参数或请求体任意切换项目。
2. 跨项目和不存在的运行记录应遵循现有的资源隐藏策略。
3. 不允许因新增统一入口绕过 Agent 或 Workflow 原有的读取权限。
4. 认证未配置时，继续遵循对应 API 的 fail-closed 行为。
5. 不在响应中返回 API Key、数据库连接信息或内部凭据。

如果两套现有 Read API 的权限约定不同，先记录差异并复用安全边界，不要自行放宽权限。

## 六、严格范围

允许：

- 新增少量统一查询 API、DTO 和查询服务
- 复用现有 ReadService / Repository
- 增加离线 API 测试与 DB 集成测试
- 更新必要的 API 文档

禁止：

- 修改 Agent Runtime 状态机
- 修改 Workflow Runtime 状态机
- 修改现有命令 API 的执行逻辑
- 增加 Agent / Workflow 的执行、取消、审批或恢复能力
- 修改数据库结构或增加迁移
- 引入新的查询框架、事件总线或复杂抽象
- 新增前端 UI
- 部署、推送或创建 PR

## 七、测试要求

至少验证：

1. Agent 与 Workflow 的运行记录都能通过统一入口读取。
2. 运行类型过滤正确。
3. 详情查询不会混淆不同运行类型的记录。
4. 分页参数及结果稳定。
5. 不存在的记录返回符合现有约定的错误。
6. 跨项目读取被拒绝或按现有策略隐藏。
7. 未认证和未配置认证的行为符合现有约定。
8. 响应 DTO 不泄露内部对象或敏感信息。
9. 原有 Agent / Workflow Read API 不回归。
10. DB 测试验证真实 Repository 查询路径，且测试后无数据残留。

优先复用现有测试基础设施，不要重复造 Fake 或测试框架。

## 八、验证命令

先运行新增测试，再运行相关历史测试：

```powershell
python -m pytest -q tests/test_runtime_read_api.py
python -m pytest -q
python -m compileall -q backend tests
```

上面的新测试文件名仅为建议，按实际目录调整。

如果项目要求启用 DB 测试，沿用当前已验证的 PostgreSQL 环境和项目现有启动方式。不得清库、重建容器或修改数据卷。

## 九、完成报告

只汇报：

1. 新增和修改文件
2. 实际 API 路径
3. 统一 DTO 字段
4. Agent 查询结果
5. Workflow 查询结果
6. 认证与项目隔离结果
7. 新增测试及全量测试结果
8. compileall 结果
9. 数据库结构是否变化
10. 已知限制

## 十、STOP

完成并验证后立即停止。

不要自动进入 Phase 7 Step 3；不要部署、推送或创建 PR。 