# Phase 7 Recovery — M2.3 A2 实施任务书（待最终授权）

项目：`D:\coding\ai\wms-ai-assistant`

基线参考：`5444b610defa224c30e557b5ee4d474d9788c001`

当前 HEAD（只读审计报告）：`8be2840870c3d53eaebf7f815122ce644297347e`

阶段状态：`TASK SPEC DRAFT — IMPLEMENTATION NOT AUTHORIZED`

## 一、目标与范围

为现有 7 个匿名只读使用量／可观测性接口建立统一的 Session 认证、服务端授权和资源范围校验。

目标接口：

| 编号 | Method | Path |
|---|---|---|
| A2-01 | GET | `/api/usage/analytics` |
| A2-02 | GET | `/api/observability/tools` |
| A2-03 | GET | `/api/observability/tools/metrics` |
| A2-04 | GET | `/api/observability/tools/history` |
| A2-05 | GET | `/api/observability/tools/metrics/persistent` |
| A2-06 | GET | `/api/observability/assistant-trace/{assistant_request_id}` |
| A2-07 | GET | `/api/observability/assistant-timeline/{assistant_request_id}` |

以上路径来自 A2 只读审计报告，正式实施前仍须以实际源码为准。

本阶段只收口认证与授权、必要的安全投影和测试。不新增业务能力，不修改 Runtime 执行行为。

## 二、已确定的项目方决策

1. **认证：统一使用 M1 Session。**
   - 无 Session、无效 Session、过期 Session、已撤销 Session：拒绝访问。
   - 认证服务不可用：fail closed。
   - 不新增 API-Key 旁路。
   - 七个接口均为 GET，不机械添加 CSRF 校验；保留当前 M1 对安全方法的既定语义。

2. **项目与资源范围：使用服务端配置授权。**
   - 客户端提交的 `project_id` 不得作为授权依据。
   - 对历史记录和聚合结果执行服务端范围约束。
   - Trace / Timeline 必须校验资源实际所属项目。
   - 配置缺失、资源归属不明确或授权无法完成时，不返回受保护数据。

3. **Usage Analytics：限制为受控运维访问。**
   - 全局用量数据不得对所有普通 Session 用户开放。
   - 必须存在可验证的运维访问授权机制。
   - 不得仅因用户已登录就默认授予运维权限。

## 三、实施前必须解决的授权细节

当前 M1 `CurrentPrincipal` 据只读审计报告仅包含用户标识信息，不携带角色或项目授权。因此以下内容必须在最终任务书中明确。

### 3.1 运维访问授权来源

需要核实项目是否已有可复用的管理员／运维授权机制。

若没有，建议采用服务端配置的运维用户白名单，并使用 Session Principal 中的可信用户标识进行匹配。

要求：

- 不接受客户端传入的 `is_admin`、角色或类似字段。
- 不得把“有效 Session”等同于“运维权限”。
- 授权配置缺失或不可用时拒绝访问。
- 新增运行时配置前，必须在最终文件清单中明确列出并取得授权。

### 3.2 服务端项目作用域

建议复用项目已有的服务端只读授权模式，但必须核实真实配置及其语义。

要求：

- 服务端配置是项目范围的唯一可信来源。
- 历史查询的 `project_id` 参数不能扩大服务端授权范围。
- Persistent Metrics 的所有过滤条件均须受服务端范围约束。
- Trace / Timeline 先确定记录所属项目，再进行范围比对。
- 不允许通过猜测 `assistant_request_id` 访问授权范围以外的资源。

若现有配置只能表达单一项目，不得未经批准扩展为多项目权限系统。

## 四、各接口的预期授权模型

以下为实施建议，需在最终授权前确认。

| 接口 | 预期访问范围 | 关键要求 |
|---|---|---|
| Usage Analytics | 受控运维人员 | 保护全局用量与模型统计 |
| Tool Snapshot | 受控运维人员，或未来具备可信项目维度后按项目过滤 | 当前为进程级全局快照，不能伪装成项目级数据 |
| Tool Metrics | 受控运维人员，或未来具备可信项目维度后按项目过滤 | 全局聚合不得泄露给普通用户 |
| Tool History | Session + 服务端项目范围 | 客户端 `project_id` 不得扩大访问范围 |
| Persistent Tool Metrics | Session + 服务端项目范围 | SQL 聚合必须在可信范围内执行 |
| Assistant Trace | Session + 运维或项目授权 + 资源归属校验 | 不得仅凭 ID 可知即可读取 |
| Assistant Timeline | Session + 运维或项目授权 + 资源归属校验 | 与 Trace 使用一致的资源授权规则 |

特别注意：当前 Tool Snapshot / Metrics 没有可靠的项目过滤能力。最终方案必须明确将其作为运维专用全局视图，还是为其提供现有数据模型已经支持的项目范围过滤。不得在没有可信数据维度的情况下虚构过滤能力。

## 五、只读与敏感数据边界

必须保持：

- 不调用 LLM、Embedding、Tool、Agent 或 Runtime 执行入口。
- 不新增数据库写入、表或迁移。
- 继续使用现有 Query Service、Facade、Store Ports 和 Repository 边界。
- 不直接向响应暴露 ORM、Session、Connection 或内部对象。
- 保持显式响应 DTO 和字段白名单。
- 不返回 Prompt、Messages、SQL、Tool 参数、原始 ToolResult 或凭证。
- Trace / Timeline 的 `project_id`、模型信息、错误分类、Token 计数及知识库标识须按授权范围返回。
- 将 Usage Analytics 中异常正文回显改为固定安全文案，避免将底层异常信息暴露给客户端。
- 不将数据库不可用降级成空成功响应。

## 六、预计变更范围

以源码核验后的最小变更为准。

候选生产文件：

```text
backend/app/api/usage.py
backend/app/api/tool_observability.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
```

可能需要的授权配置：

```text
backend/app/api/observability_read_auth.py
backend/app/config.py
```

仅在确认现有机制不足且取得明确授权后新增。

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
docs/decisions/Phase/Phase 7 Recovery — M2.3 A2 ...
```

不得预先认定以上文件都需要修改。实施前必须给出实际变更清单，并特别标识冻结 28 文件矩阵中的文件。

## 七、测试与冻结基线保护

实施阶段需要覆盖：

- 七个接口的匿名访问拒绝。
- 无效、过期、已撤销 Session。
- 认证服务故障时 fail closed。
- 运维权限不足时拒绝访问。
- 跨项目历史查询拒绝。
- 跨项目 Trace / Timeline 资源访问拒绝。
- 有效授权下响应字段与原契约保持一致。
- GET 不因 CSRF 头缺失而产生人为回归。
- 错误响应不泄漏敏感信息。
- 只读接口不调用执行型服务。
- DB-gated 查询使用真实 PostgreSQL 和现有 Repository。
- 查询前后状态不变，最终 `db_residue == 0`。

冻结治理文件和常量保持不变，包括：

```text
EXPECTED_MATRIX_SCALE
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT
MATRIX_EXECUTION_BASELINE
EXPECTED_OBSERVABILITY_ROUTES
observability_residue_contract
```

冻结测试计数必须由真实 Gate 输出确认，不得根据静态推断写成 PASS。

## 八、实施授权边界

本任务书本身不授予实施权限。

只有在以下事项全部确认后，才能进入编码：

1. 运维授权机制及其配置来源已确定。
2. 服务端项目范围配置及其适用接口已确定。
3. 七个接口的访问模型已确认。
4. 最终新增／修改文件清单已确认。
5. 冻结矩阵影响已列明。
6. 项目方明确授权实施。

即使获得实施授权，也不自动授权 Git add、commit、push、PR、部署或进入后续阶段；这些操作需要分别遵守项目方的既有授权边界。

## 九、完成标准

实施完成后必须提供：

- 实际修改与新增文件清单。
- 七个接口逐项认证和授权结果。
- 运维授权与项目作用域的实际实现证据。
- 定向测试、全量离线测试和 DB-gated 测试结果。
- 冻结 Gate 原始结果。
- DB residue 证据。
- Git diff 与范围审查。
- 未解决问题与残余风险。

本阶段完成后立即停止，不自动进入后续阶段。
~~~

## 当前仍需明确的一点

你已确定了认证方式、项目范围原则和 Usage Analytics 的访问限制。还需要在任务书最终定稿时，确认**运维权限具体使用现有机制，还是新增服务端用户白名单配置**。这是因为 Session 本身目前并不携带角色信息，不能仅靠 `require_session` 实现运维授权。

在最终授权前，继续保持：

- 代码修改：0
- Git 写操作：0
- 测试运行：0
- 数据库访问：0
- A2 实际实施：`NOT STARTED`

这份任务书也符合服务端授权必须独立验证对象归属、不能只依赖客户端 ID 的安全原则。