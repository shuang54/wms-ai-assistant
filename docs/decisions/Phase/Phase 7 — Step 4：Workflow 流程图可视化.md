# Phase 7 — Step 4：Workflow 流程图可视化

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

在现有 Workflow 运维工作台中新增只读的 Workflow Graph View。

本阶段新增真实可见的产品功能，不重写前端框架，不重复实现 Step 3 已完成的交互。

目标布局：

```text
Workflow Run Detail
    ├── Overview
    ├── Graph View        ← 新增
    ├── Node Attempts
    ├── Timeline
    └── Approval
```

## 二、开始前检查

保护当前工作区：

```powershell
git status --short
git diff --check
git log -3 --oneline
```

阅读当前 Workflow Definition、NodeRunRecord、Workflow Read API 和前端数据结构。

确认：

- 工作流节点定义实际存储在哪里。
- 节点之间的依赖关系是否有真实、持久化或可安全读取的数据来源。
- 节点 ID、类型和状态的实际字段。
- 是否存在循环、分支、条件边或多条入边。
- 当前 Read API 是否能提供足够信息。

不得假设节点之间一定是简单的线性流程。

不得覆盖、清理或重置 Step 2、Step 3、Step 3B 的未提交变更。

## 三、实现范围

允许：

- 新增前端 Graph View。
- 复用现有 Workflow Definition 和 Read API 数据。
- 使用原生 SVG、CSS 和 JavaScript 实现图形。
- 增加节点状态图例、节点选择和详情联动。
- 增加必要的前端测试与 Chromium E2E。

禁止：

- 修改 Workflow Runtime 的执行逻辑。
- 修改数据库 Schema 或 Store Ports。
- 新增启动、取消、重试、恢复、审批裁决等写操作。
- 凭节点执行先后顺序猜测工作流拓扑。
- 将失败或未知状态渲染为成功。
- 使用虚构的工作流定义或节点数据。
- 引入大型图形框架。

## 四、Graph View 功能

### 1. 节点展示

每个节点尽可能展示真实的：

- Node ID
- 节点类型
- 当前执行状态
- Attempt 数量
- 错误标识

节点状态必须使用当前真实枚举。

未知状态保留原值并明确标注，不得自动映射成成功。

### 2. 节点关系

只有存在真实依赖关系时才绘制连接线。

如果当前数据只有 Node Attempts 和 Timeline，没有可靠的 Workflow Definition 或依赖边：

- 不得按时间顺序虚构连接线。
- 先报告具体缺失字段与最小安全方案。
- 不得为实现视觉效果擅自修改后端契约。

### 3. 节点与详情联动

点击图中的节点后：

- 定位现有 Node Attempts。
- 展示对应节点的历史 Attempt。
- 允许跳转至相关 Timeline 事件。
- 保留 Step 3 已实现的多 Attempt 历史和筛选语义。

没有相关事件时明确提示，不得补造。

### 4. 视图交互

支持：

- 节点状态图例。
- 选中节点高亮。
- 失败节点突出显示。
- 长工作流的容器内滚动。
- 窄屏下可用的图形浏览方式。
- 无节点、无拓扑、未知状态等空状态。

不要求实现复杂自动布局算法。先采用与真实工作流结构相匹配的最小实现。

### 5. 安全

- 继续使用 `/api/ui/projects/...` 会话认证接口。
- 浏览器不得持有服务端 API Key。
- 节点名称、ID 和错误码使用安全 DOM API 展示。
- 不新增写请求。
- 不展示未授权的 Workflow 数据。

## 五、测试要求

增加针对真实图形交互的测试：

1. 有真实拓扑时节点和边正确显示。
2. 缺少拓扑时不生成虚假边。
3. 节点状态映射正确。
4. 未知状态不会显示为成功。
5. 点击节点可以定位 Node Attempts。
6. 节点可定位时能关联 Timeline。
7. 多 Attempt 历史不丢失。
8. 空工作流与未知节点类型有明确状态。
9. 后端提供的文本不会作为 HTML 执行。
10. 页面不存在任何 Workflow 写操作。

运行：

```powershell
cd frontend
npm test
npm run test:e2e
```

再运行：

```powershell
cd ..
python -m pytest -q tests/test_workflow_ui_security.py
python -m pytest -q tests/test_workflow_ui_read_api.py tests/test_workflow_read_api_security_audit.py
python -m compileall -q backend tests scripts
git diff --check
```

DB-gated 测试只有在确有必要且确认专用测试数据库后才运行。

## 六、验收标准

- 运维工作台新增 Graph View。
- 图形使用真实 Workflow 拓扑，不虚构边。
- 节点状态正确。
- 节点可以联动已有 Attempt 和 Timeline。
- 浏览器交互测试通过。
- 既有 Step 2、Step 3、Step 3B 功能不回归。
- 不修改 Runtime、Schema、授权模型。
- 不连接生产数据库。
- 不 commit / push。

## 七、最终报告

```text
【Phase 7 — Step 4 Report】

1. 开始时 Git 状态
2. Workflow Definition 的真实来源
3. Graph View 新增功能
4. 节点与边的数据来源
5. 节点状态映射
6. Attempt / Timeline 联动
7. 响应式布局
8. 前端测试结果
9. Chromium E2E 结果
10. 后端回归结果
11. 安全边界
12. compileall / diff check
13. 未解决问题
14. Git 状态

完成后立即 STOP，不进入 Step 5。
```

