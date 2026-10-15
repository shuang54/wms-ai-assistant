# Phase 7 Recovery — M2.3 A2 开始实施

项目路径：`D:\coding\ai\wms-ai-assistant`

**本次正式授权实施 Phase 7 Recovery M2.3 A2。不得再次询问已经确认的设计决策。**

## 一、实施前保护工作区

先执行：

```powershell
git status --short
git diff --check
git diff --stat
git rev-parse HEAD
```

要求：

- 当前 HEAD 应为已核验的 `8be2840870c3d53eaebf7f815122ce644297347e`；若已变化，记录实际 HEAD 并核对新增提交。
- 保留所有已有 tracked / untracked 修改，包括 S5、A1 和两份 A2 文档。
- 不得执行 `reset`、`clean`、`stash`、`checkout`、`restore`。
- 不得覆盖或删除已有任务书。
- 不执行 `git add`、`commit`、`push`。
- 本阶段只修改 A2 实施所需文件。

## 二、已确定的架构决策

### 1. Session 认证

七个接口统一使用现有 M1 Session 认证，不新增 API-Key 认证旁路。

### 2. 运维白名单

新增配置：

```text
OBSERVABILITY_READ_OPS_USER_IDS
```

以可信 `Principal.user_id` 与服务端白名单匹配。

- 配置缺失、空白、无法解析或不可用：`503`，fail closed。
- 已认证但不在白名单：`403`。
- 不新增 RBAC、`AppUser.role` 或数据库列。
- 不将配置内容泄漏到日志或响应。

### 3. 运维接口使用装饰器级依赖

必须采用：

```python
dependencies=[Depends(require_observability_ops)]
```

不要为了运维授权给 Handler 增加无必要的参数，以保护现有签名冻结断言。

认证模块不得声明任何 HTTP 路由。

### 4. 服务端项目范围

A2-04、A2-05 使用服务端 `PROJECT_ID`。

- 不使用客户端 `project_id` 作为授权范围。
- 移除客户端 `project_id` 查询参数。
- `PROJECT_ID` 缺失、空白或无法解析时返回 `503`。
- 查询必须应用服务端项目范围。
- `project_id IS NULL` 的记录必须排除，并在 `docs/security.md` 记录该 fail-closed 取舍。
- 跨项目资源采用现有安全约定，返回 `404`。

### 5. Trace / Timeline

A2-06、A2-07 使用 Session + 运维白名单。

不新增 Schema，不伪造项目归属，不尝试通过局部 Tool/RAG 记录推断 Trace 的项目所有权。

在 `docs/security.md` 明确记录无法验证资源级项目归属的残留风险。

### 6. 全局运维视图

以下接口均为运维专用全局视图：

- A2-01 Usage Analytics
- A2-02 Tool Snapshot
- A2-03 Tool Metrics
- A2-06 Assistant Trace
- A2-07 Assistant Timeline

不新增不存在的项目统计维度。

## 三、七个接口的实施范围

| 接口 | 必须实施的访问控制 |
|---|---|
| `GET /api/usage/analytics` | Session + 运维白名单 |
| `GET /api/observability/tools` | Session + 运维白名单 |
| `GET /api/observability/tools/metrics` | Session + 运维白名单 |
| `GET /api/observability/tools/history` | Session + 服务端 `PROJECT_ID` |
| `GET /api/observability/tools/metrics/persistent` | Session + 服务端 `PROJECT_ID` |
| `GET /api/observability/assistant-trace/{assistant_request_id}` | Session + 运维白名单 |
| `GET /api/observability/assistant-timeline/{assistant_request_id}` | Session + 运维白名单 |

优先复用现有 Session 认证基础设施、`tests/api_session_auth.py` 和既有 Query Service。

不要直接访问 ORM 绕过查询服务。

## 四、必须保护的冻结测试文件

下列七个文件在冻结矩阵中，实施前后必须重点检查：

```text
tests/test_assistant_trace_api.py
tests/test_assistant_trace_api_db.py
tests/test_assistant_timeline_api.py
tests/test_assistant_timeline_db_e2e.py
tests/test_assistant_timeline_concurrency_db_e2e.py
tests/test_tool_observability_architecture_audit.py
tests/test_assistant_trace_timeline_contract.py
```

另外必须检查：

```text
tests/test_tool_observability_api.py
tests/test_usage_analytics_api.py
tests/test_tool_observability_history_api_db.py
tests/test_tool_observability_history_filtering_db.py
tests/test_tool_observability_persistent_metrics_db.py
```

认证后可能需要调整测试客户端装配或参数契约，但不得修改冻结 baseline 数字、注册表或常量来掩盖失败。

若冻结断言确实要求修改，先记录具体断言、原因和最小修复方案；不得静默修改。

## 五、测试要求

新增认证测试，至少验证：

1. 七个接口匿名请求均拒绝。
2. 无效、过期、已撤销 Session 均拒绝。
3. 运维白名单缺失或无效时 fail closed。
4. 非白名单用户访问运维接口返回 `403`。
5. 有效运维用户可以读取原有全局视图。
6. A2-04/05 不接受客户端项目参数扩大访问范围。
7. NULL 项目记录不可见。
8. Trace / Timeline 受到运维授权保护。
9. GET 不因缺少 CSRF Token 产生人为回归。
10. 错误响应不泄漏敏感信息。
11. Read API 不触发 Runtime、Tool、LLM 或写操作。
12. 原有分页、响应 DTO 和字段白名单保持有效。

使用真实 M1 Session 测试装配，不用 `dependency_overrides` 绕过生产认证依赖。

## 六、文档

更新：

```text
docs/api.md
docs/security.md
```

明确七个匿名接口改为认证访问所产生的 breaking change，以及：

- 运维白名单是部署级授权，不是 RBAC。
- A2-01/02/03 为运维全局视图。
- A2-06/07 无法证明资源级项目归属。
- A2-04/05 排除 `project_id IS NULL` 记录。
- 运维授权配置缺失时 fail closed。

权威任务书为：

```text
docs/decisions/Phase/Phase 7 Recovery — M2.3 A2 实施任务书（待最终授权）.md
```

本次实施中不要自动重命名或删除另一份 A2 只读核验文档。

## 七、实施和验证顺序

按以下顺序完成：

```text
工作区保护
→ 核验实际源码
→ 新增运维授权依赖与配置
→ 七个接口接入认证/授权
→ 修复受影响的测试装配
→ 新增安全回归测试
→ 更新 API / Security 文档
→ 定向测试
→ 冻结矩阵 Gate
→ 全量离线测试
→ DB-gated 回归（仅使用专用测试数据库）
→ compileall
→ Git diff / status 审查
→ 最终报告
```

执行测试时使用 PowerShell 正确设置环境变量：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

运行 DB-gated 测试前必须确认 `DATABASE_URL` 指向专用测试数据库。若无法证明数据库隔离，跳过 DB 测试并如实报告。

不要修改冻结 baseline 来让 Gate 通过。

## 八、停止条件

完成 A2 后立即停止。

不执行 Git add / commit / push，不创建 PR，不部署，不迁移数据库，不进入下一阶段。

最终报告必须列出：

1. 修改和新增文件。
2. 七个接口的认证与授权行为。
3. 运维白名单配置语义。
4. `PROJECT_ID` 与 NULL 项目处理。
5. Trace / Timeline 残留风险。
6. 新增测试结果。
7. 冻结矩阵与全量测试结果。
8. DB-gated 测试结果与 DB residue。
9. compileall 结果。
10. `git diff --check`、`git diff --stat`、`git status --short`。
11. 未完成项和实际限制。

不得把未运行的测试写成 PASS。