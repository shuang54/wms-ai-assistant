
# Phase 7 Recovery — Workflow Read API 功能闭环

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

在现有 Workflow Read Layer 基础上，完成对外只读 API 的功能闭环。

当前已知：

- `backend/app/workflow_runtime/read_service.py` 已存在。
- `backend/app/workflow_runtime/read_factory.py` 已存在。
- `backend/app/api/workflow_read.py` 已存在。
- Read Service 已具备 Run Detail、Node Attempt History、Approval Detail 和 Timeline。
- 最近提交 `e76d99dcfc0b76ecfb1a8981b61137a8fa4f1ce0` 已增强 Node 安全投影及按 Node 查询 Approval。

**本轮复用现有 API，不重复创建 Router，不重新实现 Read Service。**

目标是让现有 API 正确使用安全 DTO，并通过项目现有认证、授权和项目隔离机制，提供稳定的只读查询能力。

## 二、开始后直接开发

先检查：

```powershell
git status --short
git diff --check
git log -1 --oneline
```

预期起点：

```text
HEAD = e76d99dcfc0b76ecfb1a8981b61137a8fa4f1ce0
branch = phase7
```

如果 HEAD 或工作区与预期不一致，保留现有状态，先识别差异，不得 reset、clean、stash 或覆盖改动。

快速阅读：

```text
backend/app/api/workflow_read.py
backend/app/workflow_runtime/read_service.py
backend/app/workflow_runtime/read_factory.py
backend/app/workflow_runtime/ports.py
backend/app/api/authorization_dependency.py
backend/app/api/observability_read_auth.py
tests/
docs/api.md
docs/security.md
```

重点确认：

1. 当前真实 API 路由、请求参数、响应结构和异常映射。
2. 当前 Run、Node Attempt、Approval、Timeline 的调用路径。
3. 新增的 `get_node_run_details()` 和 `get_approval_for_node()` 是否已被 API 使用。
4. 当前 Session 认证和项目授权依赖如何接入。
5. 哪些测试已覆盖现有接口，哪些真实缺口尚未覆盖。

检查后立即进入编码，不要先输出一份单独的长篇审计报告。

## 三、本轮功能范围

### 1. Run Detail API

保留现有路由和兼容的响应契约。

确保：

- 通过现有 Read Service 获取 Run Detail。
- 必须执行项目范围校验。
- 返回稳定的显式响应 DTO。
- 不返回启动输入 `input`、ORM 实例、Session、Connection 或敏感 metadata。

### 2. Node Attempt History API

优先复用新增的：

```python
get_node_run_details(...)
```

确保对外返回的是安全投影，而不是未经校验的原始 Store Record。

必须保留所有历史 Attempt，不得只返回最新 Attempt。

输出字段应与现有 DTO 一致，至少核对：

- `workflow_run_id`
- `node_id`
- `attempt`
- `node_type`
- `status`
- `output`
- `error_code`
- `elapsed_ms`
- `started_at`
- `completed_at`

以实际模型和现有 API 契约为准，不要凭空增加字段。

损坏的 JSON、非法状态或非法错误码必须 fail closed，不得截断、修复或原样泄漏损坏数据。

### 3. Approval API

检查现有 Approval 路由是否已经按 ID 查询。

如果存在按 Node 查询审批的接口，优先将其接入：

```python
get_approval_for_node(...)
```

如果不存在，但确实需要通过 Run 和 Node 获取审批，则在现有 Router 内增加最小只读端点，遵循项目现有 URL 和命名规范。

必须验证：

- 父 Run 存在且属于请求的项目。
- Approval 的 Run 和 Node 归属一致。
- 无审批记录时，遵循 Read Service 已定义的 `None` 语义及现有 API 的兼容约定。
- 不返回内部审批对象或未授权字段。
- 不伪造审批创建时间；当前 Port 未暴露的 `created_at` 不得自行补造。

### 4. Run Timeline API

复用现有 Timeline 能力。

确保：

- 只由真实持久化记录构建。
- 时间排序和稳定次级排序不变。
- 不推断未持久化的事件。
- 不修改 Schema 补造审批创建时间。

如果现有接口已经满足要求，不要为了增加代码而重写。

## 四、认证、授权与项目隔离

所有路由必须沿用项目当前的 Session 认证和项目授权机制。

要求：

1. 未认证或 Session 无效：沿用现有 401 行为。
2. 授权服务不可用：沿用现有 fail-closed 503 行为。
3. Run 不存在或项目不匹配：按现有安全异常契约返回，不能泄漏跨项目资源是否存在。
4. 不允许只凭任意 `run_id` 读取其他项目的数据。
5. 不得把 `project_id` 当作已认证身份的替代品。
6. 不得绕过 Read Service 直接访问 Repository 或 ORM。
7. 不得将新接口加入全局 SYSTEM_OPS 权限范围，除非现有业务定义明确要求。

本轮不新增身份系统，不修改 M2.4 授权模型。

## 五、显式响应 DTO

优先复用现有 DTO；缺少 API 输出模型时，才新增最小 Pydantic 响应模型。

FastAPI 的 `response_model` 可以校验和过滤对外输出字段，适合用来保护 API 边界。参考官方文档：

https://fastapi.tiangolo.com/tutorial/response-model/

禁止直接返回：

```python
return orm_object
return model.__dict__
return dict(orm_object)
```

响应 DTO 不得包含：

- Workflow 启动输入 `input`
- API Key、密码、连接字符串
- Session / Connection
- 未经过安全投影的内部 metadata
- 未筛选的内部异常详情

不要为了“更安全”而把原有合法业务字段全部删除；必须保持已公开契约兼容。

## 六、测试要求

扩展现有 Workflow Read API 测试；先复用现有文件和 fixture，不要重复建立一套测试框架。

至少覆盖：

### 功能

- 正常获取 Run Detail。
- 正常获取完整 Node Attempt 历史。
- 正常获取 Approval Detail。
- 正常获取 Timeline。
- 无审批记录时的正确行为。
- 空节点列表的正确行为。

### 安全

- 未认证请求。
- 项目不匹配。
- Run 不存在。
- Approval 与父 Run / Node 归属不一致。
- 响应中不存在 `input`。
- 响应不泄漏 ORM、Session、Connection。
- 非法 JSON 输出不会进入 HTTP 正常响应。
- 非法状态、节点类型、错误码不会被原样返回。
- Read API 不触发 Runtime 状态推进。
- Read API 不调用 LLM 或 Tool。

### 兼容性

- 现有 API 路由与响应结构保持兼容。
- 现有 Runtime / Store Port 契约不变。
- 不修改冻结基线及治理门。

## 七、验证

先执行与 Workflow Read API 直接相关的现有测试，以及新增测试。

再执行：

```powershell
python -m pytest -q tests/test_workflow_runtime_read_service.py tests/test_workflow_runtime_read_service_db.py
python -m compileall -q backend tests scripts
git diff --check
```

按项目现有机制补充 API 认证、项目隔离和 Workflow Runtime 回归。

DB 测试只能使用专用测试数据库；执行前必须确认连接目标，不能连接生产数据库。

准确报告每条命令的退出码、passed、failed、skipped 和 errors，不得将未执行测试标记为 PASS。

## 八、Git 规则

- 不执行 Push。
- 不执行 Amend。
- 不执行 Reset、Clean、Stash。
- 不清理数据库。
- 不修改 Workflow Runtime 执行逻辑。
- 不修改 Store Ports、Repository 或 Schema。
- 不修改 M2.4 已提交内容。
- 不进入 M2.5。
- 不提交本轮改动，等待审阅。

## 九、完成报告

只报告：

1. 实际新增和修改的文件。
2. 新增或补齐的 API 路由。
3. Run / Node Attempt / Approval / Timeline 完成情况。
4. 认证与项目隔离验证结果。
5. 显式响应 DTO 和敏感字段隔离。
6. 定向测试与回归结果。
7. DB 测试是否实际执行。
8. Compile、AST 边界与 `git diff --check`。
9. Git HEAD、工作区状态。
10. 仍然存在的具体功能缺口。

完成后立即 STOP，不进入后续阶段。
