# Phase 7 Recovery — M2.3 A2 最终实施任务书

项目：`D:\coding\ai\wms-ai-assistant`

阶段：`M2.3 A2 — Usage / Observability Read API Authentication and Authorization`

参考提交：`5444b610defa224c30e557b5ee4d474d9788c001`

当前审计 HEAD：`8be2840870c3d53eaebf7f815122ce644297347e`

任务书状态：`TASK SPEC FINALIZED — IMPLEMENTATION NOT AUTHORIZED`

## 一、阶段目标

为现有 7 个匿名只读使用量／可观测性接口建立明确的 Session 认证、服务端项目范围约束和运维授权边界。

本阶段只收口既有读取接口，不新增业务能力，不改变数据模型，不修改 Agent、Tool、Workflow 或 Runtime 执行行为。

## 二、项目方已确认的决策

### 2.1 认证

- 七个接口统一使用现有 M1 Session。
- 不新增 API-Key 旁路。
- Session 缺失、无效、过期或已撤销时拒绝访问。
- 认证服务不可用时 fail closed。
- GET 接口不机械添加 CSRF 校验，沿用现有 M1 安全方法语义。

### 2.2 运维授权

新增服务端配置：

```text
OBSERVABILITY_READ_OPS_USER_IDS
```

建议语义：

- 以可信的 `Principal.user_id` 与服务端配置的用户 ID 白名单匹配。
- 不接受客户端传入的角色、管理员标志或身份声明。
- 配置缺失、空白、无法解析或不可用时，不授予运维权限。
- 对需要运维权限的接口，授权配置不可用时 fail closed。
- 不新增 `AppUser.role` 列，不引入 RBAC 或组织模型。
- 配置值不得写入响应或普通业务日志。

未配置时的错误状态码必须与现有 API 错误契约保持一致；不得把授权配置错误伪装成空数据或成功响应。

### 2.3 项目与资源范围

- 项目授权以服务端配置为依据。
- 不允许客户端 `project_id` 扩大授权范围。
- A2-04、A2-05 使用 Session 与服务端项目范围。
- A2-06、A2-07 采用运维专用方案，不实施无法证明的项目级资源归属校验。
- Trace / Timeline 无法确认项目归属是已接受的残留风险，必须在 `docs/security.md` 中明确记录。
- 不修改数据库 Schema，不新增迁移，不伪造历史项目归属。

### 2.4 全局观测接口

- A2-01 Usage Analytics：运维专用全局视图。
- A2-02 Tool Snapshot：运维专用全局视图。
- A2-03 Tool Metrics：运维专用全局视图。
- A2-06 Assistant Trace：运维专用。
- A2-07 Assistant Timeline：运维专用。

A2-04 / A2-05 的客户端 `project_id` 不得作为授权依据；应按既有服务端项目作用域执行查询。

## 三、接口范围

| 编号 | Method | Path | 访问模型 |
|---|---|---|---|
| A2-01 | GET | `/api/usage/analytics` | Session + 运维白名单 |
| A2-02 | GET | `/api/observability/tools` | Session + 运维白名单 |
| A2-03 | GET | `/api/observability/tools/metrics` | Session + 运维白名单 |
| A2-04 | GET | `/api/observability/tools/history` | Session + 服务端项目作用域 |
| A2-05 | GET | `/api/observability/tools/metrics/persistent` | Session + 服务端项目作用域 |
| A2-06 | GET | `/api/observability/assistant-trace/{assistant_request_id}` | Session + 运维白名单 |
| A2-07 | GET | `/api/observability/assistant-timeline/{assistant_request_id}` | Session + 运维白名单 |

正式编码前仍须以实施时的真实源码确认路由、Handler 和依赖签名，不得根据本表猜测接口实现。

## 四、逐接口要求

### A2-01：Usage Analytics

- 限制为运维白名单访问。
- 保留现有响应结构和统计语义。
- 将底层异常正文回显改为固定安全文案。
- 不将数据库异常、连接信息或内部配置返回给客户端。
- 不改变已有数据库故障状态码契约，除非另有明确授权。

### A2-02：Tool Snapshot

- 限制为运维白名单访问。
- 保留现有进程内 Snapshot 读取语义。
- 不新增项目过滤，不虚构不存在的维度。
- 不触发 Tool、LLM 或 Runtime 执行。

### A2-03：Tool Metrics

- 限制为运维白名单访问。
- 保留现有全局 Metrics 语义。
- 不新增 `by_project` 或其他不存在的统计维度。
- 不为实现权限控制而修改 Metrics 数据模型。

### A2-04：Tool History

- 使用 Session 认证。
- 查询范围由服务端项目配置确定。
- 客户端 `project_id` 不得扩大访问范围；按既有服务端授权模式处理。
- 未授权资源采用安全、明确的拒绝语义，优先与既有只读 API 的 404 约定保持一致。
- 保留已有分页、过滤和排序语义。

### A2-05：Persistent Tool Metrics

- 使用 Session 认证。
- SQL 聚合必须限制在服务端授权项目范围内。
- 客户端 `project_id`、`tool_name`、`success` 等参数只能作为业务过滤条件，不能扩大授权范围。
- 不新增全库聚合，不绕过既有 Query Service 或 Repository。

### A2-06：Assistant Trace

- 使用 Session + 运维白名单。
- 不宣称已实现项目级资源归属校验。
- 记录中包含的 `project_id`、模型、错误分类、Token 计数和知识库标识仅在运维授权通过后返回。
- 不因请求 ID 可知就把该接口视为公开资源。
- 不新增 Schema 或历史数据回填逻辑。

### A2-07：Assistant Timeline

- 使用 Session + 运维白名单。
- 与 A2-06 保持一致的访问策略。
- 明确记录目前无法从持久化数据可靠确定每个 Trace / Timeline 的所属项目。
- 不推断、不伪造资源归属。
- 不新增 Schema 或历史数据回填逻辑。

## 五、通用安全要求

1. 所有接口先完成认证和授权，再读取受保护数据。
2. 不允许认证失败后继续执行查询并返回部分结果。
3. 不允许授权配置异常时降级为全局访问。
4. 不允许将 Session 有效直接等同于运维权限。
5. 不暴露 Prompt、Messages、SQL、Tool 参数、原始 ToolResult、凭证、连接字符串、Session、Connection 或 ORM 对象。
6. 保持显式响应 DTO 和字段白名单。
7. 不新增数据库写入、迁移或业务执行副作用。
8. 不使用 `except Exception: pass` 吞掉未知异常。
9. 不改变现有业务成功响应结构。
10. 不把匿名访问拒绝变成空结果 200。

这些规则遵循默认拒绝、最小权限和逐接口授权的原则。认证与授权必须分开处理；拥有有效身份并不自动获得运维权限。

## 六、冻结测试与治理边界

冻结对象保持原样，包括：

```text
EXPECTED_MATRIX_SCALE
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT
MATRIX_EXECUTION_BASELINE
EXPECTED_OBSERVABILITY_ROUTES
observability_residue_contract
```

禁止通过修改冻结数字、测试注册表或历史 baseline 消除 Drift。

已知影响：

- `tests/test_assistant_timeline_api.py` 位于冻结 28 文件矩阵内，若需修改，必须在实施报告中单独申报。
- A2 认证依赖的测试装配应复用现有 `tests/api_session_auth.py`，避免重复实现 Session 测试基础设施。
- 冻结路由契约只比较 Method / Path，但 Gate 的最终结果仍必须以实际执行输出为准。
- 当前 375 / 356 / 19、DB 180、总计 555 等历史值尚未在本次核验中重新运行验证，不得预先写成 PASS。

## 七、预计变更文件

候选生产文件：

```text
backend/app/api/usage.py
backend/app/api/tool_observability.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
backend/app/api/observability_read_auth.py
backend/app/config.py
.env.example
```

其中 `observability_read_auth.py`、`config.py` 和 `.env.example` 的修改属于新增运维授权配置的必要范围；实施前仍须确认现有配置风格及最小差异。

候选测试文件：

```text
tests/test_observability_read_api_auth.py
tests/test_usage_analytics_api.py
tests/test_assistant_timeline_api.py
tests/test_assistant_trace_api_db.py
tests/test_tool_observability_history_api_db.py
tests/test_tool_observability_history_filtering_db.py
tests/test_tool_observability_persistent_metrics_db.py
```

候选文档：

```text
docs/api.md
docs/security.md
docs/decisions/Phase/Phase 7 Recovery — M2.3 A2 实施任务书.md
```

该清单是预计范围，不是要求全部修改的清单。编码前必须再次核验真实接口，并给出最终变更文件清单。

## 八、测试要求

实施阶段至少验证：

- 七个接口匿名访问均拒绝。
- 无效、过期、已撤销 Session 拒绝。
- 运维白名单配置缺失、空白或不可用时 fail closed。
- 非白名单用户不能访问运维专用接口。
- 有效运维用户可读取原有全局视图。
- A2-04 / A2-05 不允许客户端 `project_id` 扩大授权范围。
- Trace / Timeline 的运维访问限制生效。
- GET 不因缺少 CSRF 头而发生人为回归。
- 错误响应不泄漏敏感信息。
- 只读接口不调用执行型服务。
- DB-gated 测试验证服务端项目过滤、跨项目拒绝和查询只读性。
- DB residue 最终为零。

必须运行：

```powershell
python -m pytest -q tests/test_observability_read_api_auth.py
python scripts/run_matrix_gate.py
python -m pytest -q
```

并按项目现有机制执行 DB-gated 测试。

实际命令可根据仓库现有测试结构调整，但必须如实报告 passed、skipped、failed、exit code。任何未执行的测试都不得写成 PASS。

## 九、残留风险

以下风险必须在安全文档中明确记录：

1. A2-01 / A2-02 / A2-03 是运维专用全局视图，不具备项目级数据过滤。
2. A2-06 / A2-07 只能通过运维白名单限制访问，无法证明每条 Trace / Timeline 的项目归属。
3. 运维白名单是部署级授权，不是用户级 RBAC。
4. 多项目、多角色授权模型仍未实现。
5. A1 相关提交未签名，是否引入提交签名仍是独立治理决策。
6. 冻结测试基线必须以实际 Gate 结果重新确认。

## 十、实施授权

**本任务书不授予实施权限。**

开始编码前必须再次取得项目方明确的实施授权。

即使获得实施授权，也不自动授权：

- Git add / commit / push
- PR
- 部署
- 数据库迁移
- A2 之后的阶段

这些事项继续遵循各自的独立授权边界。

## 十一、停止条件

完成 A2 后续实施和验证报告后立即停止。

不自动进入下一阶段。
~~~

## 当前状态

- A2 访问模型：已确认。
- 运维白名单配置：已确认。
- Trace / Timeline 残留风险：已确认接受并要求记录。
- 任务书：内容已在本回复中定稿，尚未写入仓库。
- 代码修改：0。
- Git 写操作：0。
- 测试运行：0。
- 数据库访问：0。
- A2 实际实施：`NOT STARTED`。
- 实施授权：`NOT AUTHORIZED`。

下一步应由你先审阅这份最终任务书，再单独授权实施。