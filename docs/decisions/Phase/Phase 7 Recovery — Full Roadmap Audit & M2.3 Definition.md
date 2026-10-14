# Phase 7 Recovery — Full Roadmap Audit & M2.3 Definition

## 一、任务目标

项目：

`D:\coding\ai\wms-ai-assistant`

当前已知基线：

- Branch：`phase7`
- 当前 HEAD 预期：`bbb5d7508b64d1b12e5215c25f8e9cf61b108726`
- 最新已完成阶段：Phase 7 Recovery — M2.2 Conversation Authentication & D1 Project Scope
- 最近提交：`bbb5d75`
- M2.2 + D1 已提交，工作区此前报告为 clean
- Matrix Gate：PASS
- 全量测试：7632 passed / 3 failed / 997 skipped
- 剩余 3 个失败属于 Step 8 历史遗留，根因是 `4a537f9` 库存工具 schema 变更后冻结测试未同步

以上是已知历史信息，执行时必须重新核验实际仓库状态。

### 本阶段唯一目标

**审查 Phase 7 完整技术路线图，明确 M2.3 的真实目标、前置依赖、范围、风险、验收标准与停止条件。**

本任务属于只读 Architecture / Roadmap Audit。

## 二、严格禁止

本阶段不允许：

- 修改任何生产代码
- 修改任何测试
- 修改任何文档
- 新建或删除文件
- 新增数据库 migration
- 修改测试基线或历史快照
- 修改现有 Git 提交
- `git add`
- `git commit`
- `git push`
- 创建 PR
- 部署
- 执行会改变数据库状态的操作
- 开始 M2.3 实际实现

**只允许读取文件、查看 Git 历史、运行只读审计命令，并输出分析报告。**

不要使用 `checkout`、`reset`、`clean`、`restore`、`stash` 等可能影响工作区状态的命令。

## 三、第一步：核验当前 Git 基线

执行：

```powershell
git status --short --branch
git branch --show-current
git rev-parse HEAD
git log -15 --oneline --decorate
git show --stat --oneline HEAD
git diff --stat
git diff --cached --stat
```

确认：

1. 当前分支是否为 `phase7`。
2. HEAD 是否为 `bbb5d7508b64d1b12e5215c25f8e9cf61b108726`。
3. 工作区是否干净。
4. M2.2 + D1 是否是当前 HEAD 的提交内容。
5. 是否存在尚未记录的新提交或其他工作区变更。

若 HEAD 或工作区状态与预期不同，只记录差异，不修改、不清理、不覆盖。

## 四、第二步：完整搜索 Phase 7 路线图

先检查仓库中的项目指导文件：

```text
AGENTS.md
README.md
docs/requirements.md
docs/architecture.md
docs/decisions/
docs/decisions/Phase/
docs/
```

同时检查实际存在的：

```text
Phase 7 Roadmap
Phase 7 Recovery
M1
M2.1
M2.2
M2.3
M3
Phase 7 Step 1 ... Step N
```

不要假设文件一定存在，也不要根据文件名自行推断任务内容。

使用 PowerShell 只读搜索：

```powershell
Get-ChildItem -Path docs -Recurse -File |
  Where-Object {
    $_.Name -match 'Phase.?7|Roadmap|Recovery|M2|M3|Architecture'
  } |
  Select-Object FullName
```

然后阅读实际存在的路线图、阶段决策、任务书及其引用的文档。

必须确认：

1. Phase 7 的最终目标是什么。
2. Phase 7 原始阶段划分与 Recovery 后的阶段划分是否一致。
3. 每个已完成阶段的目标、交付物和验收条件。
4. M1、M2.1、M2.2 之间的依赖关系。
5. M2.3 是否已经有明确任务书。
6. 如果存在 M2.3 任务书，它是否与当前架构及已完成阶段一致。
7. 后续阶段是否依赖 M2.3。
8. 是否存在阶段编号冲突、过期文档、重复目标或未解决的架构决策。

**路线图文档的优先级高于根据阶段编号进行的猜测。**

如果不同文档相互冲突，必须列出冲突来源，不能擅自选择一个版本作为事实。

## 五、第三步：复核已完成阶段

以实际代码和 Git 提交为证据，复核：

### Phase 5

重点检查路线图中对 Agent Runtime 的冻结声明、已交付能力以及后续依赖。

### Phase 6

重点检查 Workflow Runtime 的完成状态、架构冻结边界及其与 Phase 7 的关系。

### Phase 7 Step 8

重点检查真实库存报表接入、工具 schema、对应测试及当前 3 个历史失败。

确认这些失败是否确实属于已知历史问题。

不要修改相关测试或实现。

### Phase 7 M1

核验用户身份、登录态、Session、CSRF、ResourceOwner 等基础能力的实际实现及其边界。

### Phase 7 M2.1

核验 Conversation Ownership Audit 的结论是否已由 M2.2 落实。

### Phase 7 M2.2 + D1

以提交：

`bbb5d7508b64d1b12e5215c25f8e9cf61b108726`

为基准，核验：

- Conversation 五个 API 的身份验证和所有权检查。
- CSRF 写操作保护。
- Conversation 与 ResourceOwner 的事务原子性。
- D1 服务端项目作用域。
- 已注册项目不等于已授权。
- 配置不可解析时 fail-closed。
- 冻结模型及数据库 schema 未改变。
- 已知限制：当前是单部署项目作用域，不是用户级 RBAC。

只记录核验结果，不重新实现已完成能力。

## 六、第四步：分析 M2.3 的真实定位

先找出 M2.3 的原始任务定义。

如果找到了，逐条对照：

```text
原始目标
当前实现
前置依赖
验收条件
剩余差距
```

如果没有找到明确任务书，则根据完整路线图提出候选定义，但必须明确标注：

`PROPOSED — 尚未获得现有路线图直接确认`

不得直接把候选定义当作正式的 M2.3 目标。

对 M2.3 至少分析以下问题：

### 1. 目标与必要性

- M2.3 要解决的具体问题是什么？
- 这个问题是否已经被 M1 / M2.2 解决？
- 是否存在更小的实现范围？
- 为什么它应当位于 M2.2 之后？

### 2. 前置依赖

- 是否依赖 M1 的身份与 Session。
- 是否依赖 M2.2 的 Conversation 所有权。
- 是否依赖 D1 的部署项目作用域。
- 是否依赖 Phase 5 Agent Runtime 或 Phase 6 Workflow Runtime。
- 是否存在尚未解决的 Step 8 测试失败或部署约束。

### 3. 范围边界

分别列出：

- 本阶段必须实现的内容。
- 可以复用的现有能力。
- 明确不应该在本阶段实现的内容。
- 必须保持冻结的模块。

### 4. 风险

至少检查：

- 身份认证与对象授权边界。
- 多用户并发与数据隔离。
- 数据库事务与失败回滚。
- API 兼容性。
- Agent / Workflow 冻结边界。
- 历史测试与治理门禁。
- HTTPS、部署配置和生产环境约束。

只有与候选 M2.3 直接相关的风险才列为阻塞项，不要把所有未来技术债都塞入本阶段。

### 5. 验收标准

给出可执行、可验证的标准，包括：

- 需要新增或复用哪些测试。
- 关键成功路径。
- 关键拒绝路径。
- 故障时的 fail-closed 行为。
- 是否涉及数据库以及如何证明无残留。
- 需要运行哪些定向测试、全量测试及治理门禁。

不要虚构测试数量、性能指标或覆盖率目标。

## 七、第五步：检查现有治理约束

核对仓库中的实际治理机制：

```text
scripts/run_matrix_gate.py
tests/test_observability_http_allowlist_audit.py
```

以及相关的冻结测试、Architecture Audit 和回归测试。

确认：

1. Matrix Gate 的通过条件。
2. `test_11_backend_working_tree_is_unmodified` 的用途。
3. 三个 Step 8 历史失败对全量测试和矩阵结果的影响。
4. 新阶段是否需要在实施前解决这些历史失败，还是应独立处理。
5. 哪些测试或基线明确禁止修改。

原则：

**不得把“修复历史失败”默认混入 M2.3。**

如果它们确实阻塞 M2.3，必须提供证据并单独提出处理方案。

## 八、第六步：形成 Phase 7 完整阶段矩阵

请输出以下表格：

| 阶段 | 原始目标 | 当前状态 | 证据 | 前置依赖 | 未解决问题 |
|---|---|---|---|---|---|
| Phase 5 | 从路线图提取 | 核验后填写 | 文件/提交 | 实际依赖 | 实际问题 |
| Phase 6 | 从路线图提取 | 核验后填写 | 文件/提交 | 实际依赖 | 实际问题 |
| Phase 7 Step 8 | 从路线图提取 | 核验后填写 | 文件/测试 | 实际依赖 | 实际问题 |
| Phase 7 M1 | 从任务书提取 | 核验后填写 | 文件/提交 | 实际依赖 | 实际问题 |
| Phase 7 M2.1 | 从任务书提取 | 核验后填写 | 文件/提交 | 实际依赖 | 实际问题 |
| Phase 7 M2.2 + D1 | 从任务书提取 | 核验后填写 | `bbb5d75` | 实际依赖 | 实际问题 |
| Phase 7 M2.3 | 从正式任务书提取或标记 PROPOSED | 不得擅自标为完成 | 文件/路线图 | 实际依赖 | 实际问题 |
| 后续阶段 | 从路线图提取 | 未开始/实际状态 | 文件/路线图 | 实际依赖 | 实际问题 |

必须明确区分：

- 已实现。
- 已提交。
- 已通过测试。
- 已部署。
- 尚未验证。

这五种状态不能混为一谈。

## 九、第七步：最终结论

最终报告必须包含：

```text
【Phase 7 Full Roadmap Audit】

1. 当前 HEAD / Branch / 工作区状态
2. 路线图来源与文档冲突
3. Phase 5 / Phase 6 / Phase 7 完整阶段矩阵
4. M1 / M2.1 / M2.2 + D1 验收回顾
5. Step 8 历史失败影响
6. M2.3 原始定义是否存在
7. M2.3 的准确目标
8. M2.3 前置依赖
9. M2.3 实施范围
10. M2.3 明确不做的事项
11. M2.3 风险与阻塞项
12. M2.3 验收标准
13. 部署与生产环境限制
14. 推荐的下一步执行顺序
15. 最终状态：READY TO PLAN / BLOCKED / NEEDS ROADMAP DECISION
```

如果 M2.3 原始定义明确且依赖已满足，可以给出：

`READY TO PLAN`

这只代表可以制定实施计划，不代表可以开始编码。

如果路线图缺失、相互冲突或授权边界不清，给出：

`NEEDS ROADMAP DECISION`

如果存在无法绕过的前置依赖，给出：

`BLOCKED`

## 十、强制 STOP

完成审查后：

- 不修改任何文件。
- 不执行 Git 写操作。
- 不运行会写入数据库的测试。
- 不创建 M2.3 任务书。
- 不开始 M2.3 编码。
- 不推送、不部署。

只输出路线图审查报告，等待下一步明确指令。