# Phase 7 Step 8：真实库存 Agent E2E 闭环验收

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

验证以下完整链路，不新增业务功能：

```text
用户库存问题
    ↓
Agent Command API
    ↓
Agent Runtime
    ↓
真实 DeepSeek Tool Call
    ↓
get_inventory
    ↓
独立 WMS/ERP 测试数据库
    ↓
真实库存现存量结果
    ↓
AgentRun / AgentStep / ToolResult 持久化
    ↓
Runtime Read API
```

本次重点是验证刚完成的报表等价查询是否真正进入 Agent 执行路径，而不只是独立 Tool 测试通过。

## 二、先检查现有实现

先阅读：
- `backend/app/tools/get_inventory.py`
- `backend/app/db/business_session.py`
- Agent Command API 与 Agent Runtime
- 现有 Agent Real LLM Smoke 测试或脚本
- Tool Result 持久化与 Runtime Read API
- `tests/test_wms_business_datasource.py`
- `tests/test_get_inventory_report_sql.py`

复用现有真实 LLM smoke 机制，不要重复建立一套执行框架。

## 三、真实端到端测试

优先使用已有的真实 LLM smoke 入口，选择一个已经与原始库存现存量报表核对过的物料，例如：

`EEBMCMBK01`

请求内容应明确要求查询该物料的库存现存量。

验证以下内容：

1. Agent Command API 正常接收请求。
2. Agent Runtime 实际执行。
3. 实际产生 `get_inventory` Tool Call。
4. Tool 参数中的物料编码正确。
5. Tool 使用独立业务数据库，不调用应用库 Engine 查询库存。
6. Tool 返回值与原始库存现存量报表对照结果一致。
7. AgentRun、AgentStep、ToolResult 等现有持久化记录符合当前实现。
8. 通过现有 Runtime Read API 能读取本次运行记录。
9. 运行结束后，应用库与业务库均无非预期写入。

必须检查实际执行记录，不能仅凭 Agent 最终回复判断成功。

## 四、结果对照

至少核对：

- Tool 是否真实执行
- 物料编码是否正确
- 库存总量是否与原始报表一致
- 分组结果及截断标记是否符合 Tool Contract
- Tool Result 持久化内容是否与实际返回一致
- Runtime Read API 是否能够读取运行状态和步骤

如果 Agent 最终回复中的数字与 Tool Result 不一致，应如实报告，不要为了通过测试而修改 Prompt 或伪造结果。

## 五、失败处理

如果失败，定位具体层级：

- Agent Command API
- Router / Runtime
- DeepSeek Tool Call
- Tool 参数校验
- get_inventory
- 业务数据库查询
- Agent 持久化
- Runtime Read API

优先复用现有日志和记录。

除非发现能够明确定位的缺陷，否则不要修改生产代码。不要通过 Mock Tool 来宣称真实端到端测试通过。

如果真实 LLM 调用失败或产生非预期 Tool Call，保留失败证据并停止，不要无限重试。

## 六、范围约束

- 使用现有真实 LLM smoke 机制；默认 pytest 不得因此调用真实 LLM。
- 不修改 `.env` 中的 `DATABASE_URL`。
- 不连接生产业务库。
- 不执行任何业务库写操作。
- 不修改 Prompt、Validator、Executor、Router 策略。
- 不新增数据库表或 migration。
- 不运行无关的全量回归测试。
- 不创建 Git commit，不推送，不创建 PR，不部署。
- 不进入下一 Phase。

## 七、测试执行

先运行本次相关的定向测试，再执行一次真实 Agent E2E smoke。

如果 smoke 入口支持只运行指定用例，优先使用该方式。

完成后检查：
- 测试结果
- 实际 LLM 调用次数
- Tool 调用次数
- 运行记录与 Tool Result
- 应用库及业务库写入情况
- `git status --short` 和 `git diff --stat`

## 八、最终报告

严格报告：

1. Agent E2E 是否通过
2. 实际路由与 Tool Call
3. 使用的物料编码
4. 原始报表与 Tool 结果对照
5. AgentRun / AgentStep / ToolResult 持久化结果
6. Runtime Read API 验证结果
7. 定向测试结果
8. 真实 LLM 调用次数
9. 数据库写入情况
10. 发现的问题及根因
11. 修改文件
12. 当前限制

只有当真实 Agent 调用、业务库查询、持久化和读取全部得到验证时，才报告 E2E PASS。

完成后立即 STOP，等待下一步指令。