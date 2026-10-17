# Phase 7 — Workflow UI Step 3：运维工作台产品化

项目路径：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

基于已经完成的 Workflow Read API 和 Step 2 前端接入，继续开发真正可用的 Workflow 运维工作台。

本阶段重点是**新增用户可见的业务功能和交互**，不是重复验证已有 API，也不是重建已有架构。

## 二、开始前检查

执行：

```powershell
git status --short
git diff --check
git log -3 --oneline
```

阅读：

```text
frontend/index.html
frontend/styles.css
frontend/js/api-client.js
frontend/js/state.js
frontend/js/controller.js
frontend/js/render.js
frontend/js/app.js
frontend/README.md

backend/app/api/workflow_ui_read.py
backend/app/api/workflow_read.py
backend/app/dto/workflow_read_api.py
backend/app/workflow_runtime/read_service.py

tests/test_workflow_ui_security.py
tests/test_workflow_ui_read_api.py
```

必须保护 Step 2 尚未提交的全部变更，包括后端 API、文档、测试和未跟踪的 Phase 7 任务书。

不得 reset、clean、stash、覆盖或删除已有修改。

如果实际 Git 状态与此前报告不同，以当前真实状态为准。

## 三、严格范围

允许：

- 改进现有前端页面布局与样式。
- 增加节点状态筛选和错误定位。
- 增加 Timeline 与 Node Attempt 联动。
- 增加输出查看、复制和展开交互。
- 改善 Loading、Empty、Error、401、403、404、502、503 状态。
- 增加必要的前端单元测试和浏览器级测试。
- 更新前端 README 和必要的 UI 任务文档。

禁止：

- 重写现有前端框架。
- 修改 Workflow Runtime 执行逻辑。
- 修改 Store Ports、Repository、数据库 Schema。
- 修改 M1 Session 或 M2.4 授权模型。
- 在浏览器中存储服务端 API Key。
- 增加未经授权的后端代理。
- 为了让页面看起来正常而伪造成功数据。
- 实现全局 Workflow Run 列表。
- 增加启动、取消、重试、恢复、审批裁决等写操作。
- 修改冻结基线、治理门或无关业务数据源测试。

继续使用当前原生 HTML、CSS、ES Modules，不迁移 Vue/React，不引入大型 UI 框架。

## 四、具体开发内容

### 1. 运维工作台布局

将现有页面整理为清晰的运维工作台，至少包含：

- 页面标题和当前项目。
- Run ID 查询区域。
- Run Overview 摘要区域。
- Node Attempts 列表。
- Run Timeline。
- Approval Detail。
- 全局请求状态提示。

要求：

- 桌面端布局清晰，合理使用空间。
- 窄屏可以正常浏览。
- 加载时明确显示状态。
- 查询失败后保留当前输入。
- 不允许通过视觉设计掩盖数据缺失或请求失败。

### 2. Node Attempts 状态筛选

增加客户端筛选：

- 全部节点。
- 运行中。
- 已完成。
- 失败。
- 等待审批。

具体状态必须根据当前后端真实枚举映射；未知状态保留原始值并显示为未知状态，不得擅自映射为成功。

要求：

- 默认显示全部历史 Attempt。
- 同一节点的多次 Attempt 不得折叠或丢失。
- 筛选只影响前端展示，不修改后端数据。
- 显示当前结果数量。
- 无匹配项时显示明确空状态。
- 支持清除筛选。

### 3. 失败节点定位

对于失败节点：

- 明确显示节点 ID、Attempt 序号、错误码。
- 展示真实存在的开始时间、结束时间和耗时。
- 点击节点可定位 Timeline 中相关事件。
- 点击 Timeline 中的节点事件，可以定位对应 Node Attempt。
- 如果历史记录缺少对应事件，不得自行补造。

错误码按原始值展示。不得将内部异常正文、堆栈或凭证直接呈现给用户。

### 4. Timeline 交互

保持后端返回的事件顺序，不在前端重新排序。

支持：

- 按事件类型进行视觉区分。
- 点击节点事件定位对应 Attempt。
- 展示事件时间、类型、节点身份和 Attempt 身份。
- 时间缺失时显示“时间不可用”。
- 不存在关联节点的事件仍然可以正常展示。

不得根据当前状态推断未持久化的历史事件。

### 5. 输出查看

为节点输出增加：

- 展开与折叠。
- 完整原文查看。
- 一键复制。
- 空输出提示。
- 复制成功或失败提示。

安全要求：

- 使用 `textContent` 或安全 DOM API。
- 禁止使用 `innerHTML` 渲染后端输出。
- 不解析输出中的 HTML 并执行。
- 不自动修复或截断后端数据。
- 保留现有后端大小限制与 fail-closed 行为。

### 6. Approval 展示

继续使用真实审批只读接口。

要求：

- 展示真实审批状态和裁决时间。
- 无审批记录时明确显示“暂无审批记录”。
- 不得将无记录解释为已通过。
- 不显示后端未提供的创建时间。
- 不增加审批批准、拒绝等写操作。

### 7. 请求状态和错误体验

统一处理：

| 状态 | 页面行为 |
|---|---|
| Loading | 显示加载提示，避免重复提交 |
| 401 | 显示登录要求或现有会话失效提示 |
| 403 | 显示当前用户无项目访问权限 |
| 404 | 显示资源不存在或不可访问 |
| 502 | 显示数据完整性校验失败提示 |
| 503 | 显示服务暂不可用 |
| 网络错误 | 显示连接失败及重试入口 |

错误提示不得包含内部异常正文、SQL、连接串或凭证。

重新加载只能重新执行 GET 查询，不得触发 Workflow 执行。

## 五、前端状态设计

继续复用现有：

```text
api-client.js
state.js
controller.js
render.js
app.js
```

不要把所有逻辑堆进 `app.js`。

状态筛选、选中节点、Timeline 定位等交互状态应尽量放入纯逻辑模块，便于单元测试。

不要为筛选功能重复请求 API；筛选应使用已经加载的当前 Run 数据。

## 六、测试要求

新增测试覆盖：

1. 不同节点状态的筛选。
2. 多 Attempt 历史保留。
3. 清除筛选和无匹配项。
4. Timeline 与 Attempt 双向定位。
5. 缺失关联事件的处理。
6. 输出展开、折叠和复制。
7. 空输出处理。
8. 未知节点状态的安全展示。
9. 加载期间重复提交防护。
10. 请求失败后的状态恢复。
11. 401、403、404、502、503 错误提示。
12. 无审批记录不能显示为成功。
13. 所有写操作仍然不可调用。
14. 后端输出不能作为 HTML 执行。
15. 页面不存在伪造的 Workflow 成功数据。

继续执行：

```powershell
cd frontend
node --test tests/
```

以及：

```powershell
cd ..
python -m pytest -q tests/test_workflow_ui_security.py
python -m pytest -q tests/test_workflow_ui_read_api.py tests/test_workflow_read_api_security_audit.py
python -m compileall -q backend tests scripts
git diff --check
```

如果新增浏览器级测试，需要说明使用的工具、安装依赖和实际执行结果；不得把静态测试冒充真实浏览器测试。

数据库集成测试只有在运行前确认专用测试数据库身份后才执行，不连接生产库。

## 七、验收标准

满足以下条件才报告完成：

- 现有查询功能继续可用。
- 状态筛选真实生效。
- 多 Attempt 历史完整保留。
- Timeline 与节点详情可以联动。
- 输出可展开、可复制。
- 审批无记录时不会显示为成功。
- 401、403、404、502、503 均有明确反馈。
- 浏览器无服务端凭证。
- 没有新增任何 Workflow 写操作。
- 没有修改 Runtime、Schema、授权模型或冻结基线。
- 新增测试通过。
- 既有变更完整保留。

如果发现已有 API 契约无法支持某项功能，先报告真实缺口，不得伪造数据，也不得擅自修改后端契约。

## 八、最终报告

完成后报告：

```text
【Phase 7 — Workflow UI Step 3 Report】

1. 开始时 Git 状态
2. 新增功能
3. 修改文件
4. Node Attempts 筛选
5. Timeline 联动
6. 输出查看与复制
7. Approval 展示
8. 错误状态处理
9. 前端测试结果
10. 后端回归结果
11. 浏览器级测试结果
12. 安全边界
13. DB / Network 访问情况
14. compileall / diff check
15. 未解决问题
16. Git 状态

本阶段完成后立即停止。
不要自动进入 Step 4。
不要 commit / push，等待审阅。
```

执行顺序：

```text
保护工作区
→ 阅读真实代码
→ 开发交互
→ 新增测试
→ 定向回归
→ 汇报
→ STOP
```
