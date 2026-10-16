# Phase 7 — Workflow UI Step 1：Run 详情与执行时间线

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在现有 Workflow Read API 基础上，交付一个真正可运行的 Workflow 运行详情页面。

用户输入已有的 `workflow_run_id` 后，可以查看：

1. Workflow Run 基本状态
2. Node 执行记录及历史 Attempts
3. Run Timeline
4. Approval 详情
5. 加载状态、空状态、错误状态和重新加载操作

本阶段只实现 **Workflow 可观测性 UI**。

不开发 Workflow 编辑器，不新增工作流执行能力，不修改 Runtime 行为。

最终用户应该能通过页面回答：

- 这个 Run 当前是什么状态？
- 哪些节点已经执行？
- 同一节点是否发生过多次 Attempt？
- 执行顺序和持久化事件是什么？
- 哪个节点关联了审批？审批状态是什么？

## 二、开始前必须检查工作区

先执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

当前 Workflow Read API 改动可能仍处于未提交状态。

**保护所有现有改动。**

禁止：

- `git reset`
- `git clean`
- `git stash`
- 覆盖或删除未跟踪文件
- 为了方便开发而撤销现有 API 改动
- 擅自 commit、push 或 amend

阅读当前工作区后，再判断如何在现有代码上继续开发。

## 三、先检查真实前端与 API

### 1. 前端结构

检查：

```text
package.json
frontend/
web/
src/
vite.config.*
tsconfig.json
```

以上只是候选位置，必须以仓库实际结构为准。

确认：

- 当前是否已经存在前端项目
- 使用什么框架及版本
- 当前页面、路由、布局和组件约定
- 当前 API 请求封装
- 当前样式系统
- 当前测试工具
- 现有页面如何处理 Loading、Error 和 Empty State

**如果已有前端，优先复用。不要另起一个平行前端项目。**

如果没有前端，先检查项目是否已经确定 UI 技术栈。不要未经检查直接引入新的框架、UI 库或大量依赖。若没有明确的既有方案，先报告实际情况和最小实现方案。

### 2. 阅读现有 Workflow Read API

重点检查：

```text
backend/app/api/workflow_read.py
backend/app/workflow_runtime/read_service.py
backend/app/workflow_runtime/read_factory.py
backend/app/workflow_runtime/ports.py
tests/test_workflow_read_api.py
tests/test_workflow_read_api_db.py
docs/api.md
docs/security.md
```

以真实代码为准，确认最终路由、响应字段、状态枚举和错误响应。

目前应重点检查的读取能力包括：

```http
GET /api/workflow-runs/{run_id}
GET /api/workflow-runs/{run_id}/nodes
GET /api/workflow-runs/{run_id}/timeline
GET /api/workflow-approvals/{approval_id}
GET /api/workflow-runs/{run_id}/approvals/{node_id}
```

这些路径仅作为预期接口的检查清单。**如果实际代码有差异，使用实际路由，不要为了符合任务书而修改 API 契约。**

## 四、页面设计

页面名称：

`Workflow Run Detail`

建议采用清晰、偏企业内部运维系统的布局。

### A. Run ID 查询区

页面顶部提供：

- Workflow Run ID 输入框
- 查询按钮
- 当前查询的 Run ID
- 重新加载按钮

要求：

- 支持手动输入已有 Run ID。
- 输入为空时禁止提交。
- 加载期间避免重复提交。
- 查询失败时保留用户输入。
- 不得虚构 Run 数据。
- 不开发全局 Run 列表。

当前 Read API 没有全局 Run 列表能力，因此本阶段通过 Run ID 查询，不得自行实现全表扫描或新增列表 Repository。

### B. Run Overview

展示真实响应中已有的字段，例如：

- Run ID
- Project ID
- Workflow ID
- Workflow Version
- Status
- Current Node
- Next Action
- State Version
- 创建时间、更新时间（仅当实际响应提供）

字段不存在时，不得伪造。

状态显示应当直观，但不能仅凭颜色传递含义。使用文字标签配合适当的视觉区分。

### C. Node Attempts

展示 Run 下所有 Node 执行记录和历史 Attempts。

建议使用表格或清晰的分组列表，展示实际响应支持的字段，例如：

- Node ID
- Node Type
- Node Status
- Attempt 序号
- Attempt Status
- Error Code
- 持久化输出摘要
- 时间字段

要求：

1. 保留所有历史 Attempts。
2. 不得仅显示最新 Attempt。
3. 不得自行重建未持久化的执行状态。
4. 不得在前端自动修复或截断损坏数据。
5. 不得直接展示敏感 metadata、凭证或内部异常堆栈。
6. 对较长的合法输出，可以使用折叠面板或详情展开，但不得修改原始数据。

如果 API 已提供安全投影，直接使用该投影，不得改成返回 ORM 对象或数据库原始记录。

### D. Timeline

以时间线展示真实持久化事件。

支持的事件类型取决于实际 API 响应，例如：

- Run 事件
- Node Attempt 事件
- Approval 事件

要求：

- 使用 API 返回的真实时间字段。
- 按后端已确定的顺序展示。
- 相同时间的事件保持稳定顺序。
- 时间缺失时明确显示“时间不可用”或等价状态。
- 不得根据当前状态推断历史事件。
- 不得伪造执行开始、执行完成或审批时间。

时间线必须与实际响应一致，而不是前端自行模拟执行过程。

### E. Approval Detail

提供查看审批详情的能力。

可以通过 Node Attempts 中的节点操作进入审批详情，使用当前已存在的按 Run/Node 查询接口。

只有在真实响应存在相关标识时才展示对应操作。

要求：

- 展示真实的 Approval ID、Run ID、Node ID 和 Status。
- 展示已有的决策信息和时间字段。
- 没有审批时显示明确的空状态。
- 不得把“没有审批”显示成“审批通过”。
- 不提供批准、拒绝、重试、取消等写操作。

本阶段只读。

## 五、前端与 API 的安全边界

这是本阶段的硬性要求。

当前 Workflow Read API 使用 `X-API-Key` 服务间凭证，并通过服务端配置确定项目读取范围。

### 禁止在浏览器中暴露凭证

不得：

- 将 `X-API-Key` 写入前端源代码。
- 将密钥放进 `VITE_*` 等会被打包到浏览器的环境变量。
- 将密钥写入 Local Storage、Session Storage 或浏览器 Cookie。
- 在浏览器 Network 请求中发送服务端密钥。
- 为了让 UI 可用而关闭现有 API 鉴权。
- 允许浏览器通过请求参数覆盖服务端的 `project_id`。

### 先检查现有的安全访问方式

如果项目已经有受信任的服务端代理或用户认证机制，优先复用。

如果没有，不能直接建立一个未受保护的代理，把固定项目范围内的 Workflow 数据开放给任意浏览器请求。

此时应当：

1. 完成不依赖真实数据的页面结构和状态组件。
2. 明确标记尚未接通安全数据访问。
3. 报告现有 API 与浏览器访问之间的认证缺口。
4. 提出最小的安全接入方案。

如果项目已有明确的本地开发代理约定，可以按该约定实施开发环境方案，但必须保证密钥仅在服务端进程中读取，代理只绑定本机，并且不得被误用为生产访问方案。

**不得通过降低安全边界来换取页面演示成功。**

## 六、交互与错误处理

至少实现以下状态：

### Loading

- 正在读取 Run 时显示加载状态。
- 正在读取详情时避免展示容易被误认为最新的旧数据。
- 防止重复提交。

### Empty

- 尚未查询时显示引导文字。
- Node Attempts 为空时显示明确提示。
- 不存在审批时显示审批空状态。

### Error

针对真实 HTTP 状态和现有错误契约处理：

- `401/403`：显示无权访问或认证失败提示。
- `404`：显示资源不可用或不存在的提示。
- `502`：显示持久化数据无法安全投影的提示，不尝试修复数据。
- `5xx` 或网络错误：显示读取失败和重试操作。

具体映射以真实 API 行为为准。

不得把完整异常堆栈、数据库错误、密钥或内部异常正文直接展示给用户。

### Refresh

允许重新读取当前 Run 的详情、Node Attempts、Timeline 和 Approval Detail。

刷新只允许触发 GET 查询。

不得触发：

- Runtime `advance()`
- Runtime `start()`
- Runtime `request_cancel()`
- Tool 执行
- LLM 调用
- Approval 状态更新

## 七、实现范围

允许修改：

- 现有前端页面与组件
- 现有前端路由
- 现有 API client
- 必要的前端测试
- 必要的 UI 文档

只有在完成安全方案检查后，才允许增加最小的服务端代理代码。

禁止：

- 修改 Workflow Runtime 执行逻辑。
- 修改 Read Service 和 Store Ports 来迁就 UI。
- 新增数据库表、Migration 或 Checkpoint。
- 新增全局 Run 列表 API。
- 新增审批写接口。
- 新增重试、恢复、取消或启动操作。
- 引入新的认证系统。
- 修改已有 API 的安全策略。
- 暴露 `input`、凭证、Session、Connection 或未筛选的内部 metadata。
- 为了 UI 测试而修改现有治理门或冻结基线。

## 八、测试要求

按项目现有测试技术栈实施。

至少覆盖：

1. Run ID 查询正常。
2. Run Overview 显示真实字段。
3. Node Attempts 保留全部历史记录。
4. Timeline 使用真实事件及时间。
5. Approval 详情正常显示。
6. 没有审批时显示空状态。
7. Loading 状态。
8. 404 状态。
9. 401/403 状态。
10. 502 或非法响应状态。
11. 刷新行为。
12. 重复提交防护。
13. 前端 bundle 不包含 API Key 或服务端凭证。
14. 不存在启动、推进、取消或审批写操作。
15. 不依赖真实 DeepSeek。
16. 不使用生产数据库。
17. 如果有服务端代理，验证未授权请求不能读取 Workflow 数据。

如果当前项目没有前端测试工具，不要为了凑测试数量引入大型测试框架；先复用已有设施，并说明剩余覆盖。

## 九、验证命令

先运行针对性前端测试及构建命令。

然后运行：

```powershell
python -m pytest -q tests/test_workflow_read_api.py tests/test_workflow_read_api_security_audit.py
python -m pytest -q tests/test_workflow_runtime_read_service.py
python -m compileall -q backend tests scripts
git diff --check
```

如果相关测试文件在当前分支有变化，应按实际文件路径调整。

如果需要运行 DB 测试：

```powershell
$env:RUN_DB_TESTS="1"
```

必须确认使用专用测试数据库，禁止连接生产数据库。

不得将未执行的测试写为 PASS。

完整离线测试若存在既有失败，记录真实数量并对比已知基线，不得为了通过而修改无关测试。

## 十、交付标准

页面可运行，并且：

- 可以输入已有 Run ID 并查询。
- 可以查看 Run Overview。
- 可以查看完整 Node Attempt 历史。
- 可以查看 Timeline。
- 可以查看 Approval Detail。
- 有完整 Loading / Empty / Error 状态。
- 不泄露 API Key。
- 不增加任何 Workflow 写操作。
- 现有 API、安全边界和 Runtime 行为保持不变。

如果因缺少安全的浏览器访问认证而无法接通真实 API，页面 UI 仍可交付，但必须清楚报告阻塞项，不得假装端到端完成。

## 十一、最终报告

完成后按以下格式汇报：

```text
【Phase 7 — Workflow UI Step 1 Report】

1. 开始时 Git 状态
2. 现有前端技术栈
3. 页面功能
4. Run / Node Attempts / Timeline / Approval
5. API 访问与凭证安全
6. 新增 / 修改文件
7. 前端测试与构建
8. 后端回归
9. DB 测试与 residue
10. compileall / diff check
11. 已知限制
12. Git 状态

不要 commit / push。
不要进入下一阶段。
完成后停止等待审阅。
```

## 十二、开发顺序

```text
检查工作区
→ 阅读现有前端与 API
→ 确认安全数据访问方案
→ Run Detail UI
→ Node Attempts
→ Timeline
→ Approval Detail
→ Loading / Empty / Error
→ Tests
→ Regression
→ 报告
→ STOP
```

**现在开始执行本阶段，不要再做与本功能无关的架构审计。**

