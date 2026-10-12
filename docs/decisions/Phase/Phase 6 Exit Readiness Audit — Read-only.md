# Phase 6 Exit Readiness Audit — Read-only

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

对当前 Phase 6 做一次只读退出审计，回答：

> Phase 6 是否具备进入最终验收的条件？哪些问题已经解决，哪些仍然阻塞？

本次只审计和汇总，不进行代码修复。

## 二、严格禁止

* 不修改任何 tracked 或 untracked 文件。
* 不执行 INSERT、UPDATE、DELETE、DDL 或 migration。
* 不执行 git reset、clean、stash。
* 不提交、不推送。
* 不修改或跳过任何测试。
* 不进入 Phase 7。
* 不因为本轮测试通过，就宣告历史间歇性问题已彻底解决。

## 三、第一步：保存 Git 基线

执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

记录：

* 当前分支和 HEAD。
* 工作区 tracked 修改数量。
* untracked 文件数量。
* 是否存在 diff whitespace 错误。
* 与上一次审计相比，工作区是否发生变化。

不要把用户提供的任务书副本误判成生产代码变更。

## 四、第二步：审计 Phase 6 能力矩阵

从仓库实际文档、测试和代码中寻找 Phase 6 Capability Matrix。

逐项记录：

| 项目                       | 审计要求               |
| ------------------------ | ------------------ |
| Agent Decision           | 实现位置、测试证据、状态       |
| Tool Authorization       | 授权检查、拒绝路径、测试证据     |
| Tool Execution           | 执行边界和错误处理          |
| Tool Result Persistence  | 持久化及清理证据           |
| AgentRun                 | 生命周期与读取边界          |
| AgentStep                | 生命周期与读取边界          |
| AgentDecision            | 持久化和读取边界           |
| AgentCheckpoint          | checkpoint 读写与恢复边界 |
| Tool Replay              | 幂等性和授权边界           |
| Resume                   | 恢复条件与测试证据          |
| HITL Approval            | 审批与执行授权边界          |
| Cooperative Cancellation | 取消信号和资源清理          |
| Agent Read API           | 认证、项目隔离、敏感信息过滤     |
| Workflow Read API        | 认证、项目隔离、敏感信息过滤     |

每项只能标记：

* `READY`：有代码和可复核测试证据。
* `PARTIAL`：存在实现，但证据不完整。
* `BLOCKED`：存在已知问题或关键证据缺失。
* `NOT VERIFIED`：本次无法验证。

不能只依据旧报告中的 READY 标记。

## 五、第三步：核对 8B-1 至 8B-3

核对以下事项：

1. 8B-1 是否将 residue 检查移动到正确的会话收尾位置。
2. 8B-2A 是否定位到真实写入者。
3. 8B-2B 是否为两个测试文件增加了水位清理。
4. 8B-2C 是否遵守目标行数不符时停止删除的规则。
5. 8B-3 是否完整记录本轮未复现的结果。

对四张 observability 表及相关 agent/workflow 表，记录最近一次可核实的计数和来源。

如果当前数据库环境不适合连接，记录 `NOT VERIFIED`，不得猜测。

对于历史 14 行已经消失的情况，只记录：

```text
历史记录在后续核验时已不存在；
具体由哪个过程清理，尚未确认。
```

不要推断其清理者。

## 六、第四步：核对测试证据

读取已有测试报告，不要无必要地重复运行全量测试。

分别列出：

* 全量离线测试最近结果。
* DB 测试最近结果。
* 各 DB shard 最近结果。
* 5 个治理门失败的准确断言。
* 分片 6 历史 residue error 的证据。
* 8B-3 本轮未复现的证据。

对治理门失败，确认失败是否来自“要求 tracked backend 文件处于干净状态”的断言，以及未提交修改是否是触发条件。

不能把治理门失败直接标成产品功能缺陷，也不能直接忽略它。

如确需补充验证，只运行最小范围测试；本次默认不重跑全量测试。

## 七、第五步：审计安全边界

只读检查：

* Agent / Workflow API 是否要求认证。
* 项目级授权是否由服务端执行，而非仅由请求参数决定。
* Tool 执行前是否进行授权校验。
* Replay / Resume 是否重新校验授权与状态。
* 取消任务后是否可能遗留资源或持久化记录。
* API 响应是否泄漏凭证、数据库连接信息或内部对象。
* 是否有已证实的跨项目访问风险。

区分：

```text
代码中存在安全检查
```

与：

```text
安全检查已通过可复现的测试验证
```

两者不能等同。

## 八、第六步：形成退出判定

使用以下规则：

### READY FOR FINAL ACCEPTANCE

仅当所有要求的能力均有证据、阻塞测试问题已解决，并且没有未关闭的关键安全问题时，才可标记。

### NOT READY

存在已知关键失败、权限边界问题或必要测试证据缺失。

### INCONCLUSIVE

关键证据因环境或测试限制无法取得。

不得为了得到 READY 而修改测试门、隐藏失败或改变判定标准。

## 九、最终报告

输出：

```text
【Phase 6 Exit Readiness Audit】

1. Branch / HEAD / Git 状态

2. Capability Matrix
- READY:
- PARTIAL:
- BLOCKED:
- NOT VERIFIED:

3. Step 8B-1 至 8B-3 审计
- 已解决的问题：
- 未解决的问题：
- 未复现的问题：

4. 最新测试证据
- Offline:
- DB:
- Shards:
- Governance gate:

5. Observability / Agent / Workflow residue
- 表名：
- 最新计数：
- 证据来源：
- 是否只读核验：

6. Security Boundary
- 已验证：
- 未验证：
- 风险：

7. Phase 6 Exit 判定
- READY FOR FINAL ACCEPTANCE / NOT READY / INCONCLUSIVE

8. 阻塞项
- 每项包含证据、影响、最小后续动作。

9. 本轮变更
- 文件修改：必须为无
- 数据库修改：必须为无
- Commit / Push：NOT EXECUTED

10. STOP
- 不进入 Phase 7
```

完成后立即停止，等待审阅。
