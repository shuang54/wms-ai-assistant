# Project Recovery Audit — 全项目进度核实与后续技术路线规划

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

对整个 `wms-ai-assistant` 项目进行一次完整的 Roadmap Recovery Audit。

目标不是继续开发某个子模块，而是回答：

1. 项目从最初的 AI 应用基础设施到现在，究竟完成了哪些能力？
2. Phase 3.x、Phase 4.1、Phase 5、Phase 6 之间的关系是什么？
3. 哪些阶段已经有代码、测试和实际验收证据？
4. 哪些阶段只是文档声称完成，仍缺乏充分证据？
5. 当前有哪些安全风险、技术债务和未提交工作？
6. 距离可交付的企业级 AI 应用平台还缺少哪些必要能力？
7. 下一阶段应该做什么，为什么？

最终输出一份基于真实仓库证据的全项目状态报告，以及一份有依赖关系的后续 Roadmap。

**本阶段只审计，不开发新功能。**

## 二、工作区保护

开始前执行：

```powershell
git status --short
git branch --show-current
git log -10 --oneline --decorate
git diff --check
git diff --stat
```

必须完整保留当前工作区，包括 Phase 5、Phase 6 Step 5～7.2 的所有修改和未跟踪文件。

禁止：

* `git reset`
* `git clean`
* `git stash`
* 覆盖或删除现有文件
* 擅自 commit / push
* 为了让审计结果好看而修改测试或代码

不要把未提交代码当成已经发布的正式版本。

## 三、第一步：建立真实阶段清单

先读取：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/decisions/
docs/evaluation/
docs/security.md
docs/security/
```

以及仓库中所有实际存在的：

```text
ROADMAP
roadmap
phase
milestone
architecture
capability matrix
completion report
```

相关文档。

如果文件不存在，按实际目录搜索，不要虚构文件。

重点梳理以下已知开发线：

```text
AI 基础设施
    ↓
RAG / Tool / AI Orchestrator / Text-to-SQL
    ↓
Text-to-SQL Evaluation / Regression
    ↓
Phase 4.1 相关开发
    ↓
Phase 5 Agent Runtime
    ↓
Phase 6 Workflow Runtime
    ↓
后续集成、部署与业务验收
```

以上只是已知线索，不代表所有阶段一定严格串行。

必须依据真实代码、Git 历史、文档和测试，恢复正确的阶段依赖关系。

## 四、第二步：检查实际代码和模块边界

至少检查：

```text
backend/app/
tests/
scripts/
docs/
```

重点核对：

### AI 应用基础层

* LLM Provider / Embedding Provider
* Knowledge Ingestion
* RAG
* Tool Registry / Tool Execution
* AI Router / Orchestrator
* Project Context
* Text-to-SQL
* SQL Validator / Read-only Executor
* Evaluation / Regression Dataset

### Agent Runtime

核对实际存在的：

* AgentRun / AgentStep / AgentDecision
* Checkpoint / Resume
* Tool Authorization / Execution
* Tool Result Persistence / Replay
* HITL Approval
* Cancellation
* Agent Read API

### Workflow Runtime

核对实际存在的：

* Workflow Domain / Runtime
* Node Execution
* Persistence / Store Adapters
* Factory
* Workflow Read Service
* Workflow Read API
* API Key Authentication
* Project Scope Authorization

不要仅凭文件名判断能力已经实现。

需要追踪关键调用链，确认能力是否真的可执行。

## 五、第三步：统一完成状态

为每个能力或阶段分配以下状态之一：

```text
VERIFIED_COMPLETE
IMPLEMENTED_TESTED
IMPLEMENTED_UNVERIFIED
PARTIAL
BLOCKED
NOT_STARTED
NOT_VERIFIED
```

状态定义：

* `VERIFIED_COMPLETE`：实现、关键测试、验收条件均有充分证据。
* `IMPLEMENTED_TESTED`：代码和指定测试通过，但尚未证明完整交付条件。
* `IMPLEMENTED_UNVERIFIED`：存在实现，但缺少足够测试或运行证据。
* `PARTIAL`：仅部分能力完成。
* `BLOCKED`：存在明确阻塞条件。
* `NOT_STARTED`：有证据表明尚未开始。
* `NOT_VERIFIED`：现有证据不足以判断。

每一项必须给出：

```text
能力名称
当前状态
代码证据
测试证据
未满足的验收条件
依赖项
建议动作
```

必须区分：

```text
历史报告称已完成
```

与：

```text
当前仓库实际验证通过
```

不能把历史报告里的测试数字直接当成刚刚重新运行的结果。

## 六、第四步：核对测试与安全风险

读取现有测试配置、pytest markers、DB-gated 测试、CI 配置和运行说明。

记录：

* 最近可确认的测试结果
* 哪些测试实际执行过
* 哪些测试只在历史报告中出现
* 哪些测试需要 PostgreSQL / pgvector
* 哪些测试可能访问真实 LLM 或网络
* 是否存在全量回归尚未执行的情况
* 数据库测试是否有清理和 residue 验证

重点审查：

1. RAG 与知识库入库。
2. Text-to-SQL 的生成、验证、执行和结果正确性。
3. Agent Runtime 的权限、持久化、恢复和 Replay。
4. Workflow Runtime 的运行与读取边界。
5. Workflow Read API 的认证和项目授权。
6. API Key 的安全配置和泄露风险。
7. HTTPS、可信网关、网络边界是否有实际证据。
8. Agent 与 Workflow 是否存在未经授权的递归调用或越权路径。

对 Step 7.2 的当前已知状态应记录：

```text
Application authentication = PASS（按最近报告）
Project authorization = PASS（按最近报告）
HTTPS = NOT VERIFIED
Network boundary = NOT VERIFIED
PRODUCTION_DEPLOYMENT = BLOCKED
```

不要因为应用层测试通过就解除生产部署门禁。

本阶段不要求模拟真实部署环境；如果缺少环境证据，明确记录 `NOT VERIFIED`。

## 七、第五步：核对 Git 与交付状态

必须报告：

```text
Current branch
HEAD commit
Working tree status
Modified tracked files
Untracked files
Known uncommitted phase work
Latest verified test evidence
```

重点核对 Phase 5、Phase 6 的未提交文件及阶段产物。

不要自动整理、删除、移动或提交文件。

识别重复文档、过期报告和命名不一致问题，但本阶段只列出建议，不实施清理。

## 八、第六步：评估平台距离最终目标还有多远

把目标分成四层：

### A. 基础 AI 应用能力

RAG、LLM、Tool、Orchestrator、Text-to-SQL。

### B. Agent 与 Workflow Runtime

决策、工具权限、持久化、恢复、审批、取消和读取 API。

### C. 企业级治理与可靠性

身份认证、项目隔离、审计、可观测性、配置管理、密钥管理、失败恢复和回归验证。

### D. 真实业务交付

端到端业务场景、业务数据正确性、权限验收、部署安全、运维手册和可重复发布流程。

每一层分别列出：

```text
已具备
未具备
未验证
阻塞项
必要依赖
```

不要仅根据完成的 Phase 数量计算项目百分比。

如果没有统一且可验证的验收矩阵，就不要给出看似精确的总体完成率。

## 九、第七步：制定后续 Roadmap

基于审计结果，制定后续阶段建议。

每个建议阶段都必须包含：

```text
阶段名称
目标
解决的问题
前置依赖
允许修改的模块
禁止修改的模块
验收标准
测试要求
完成后的交付物
```

遵守以下原则：

1. 优先修复安全和正确性问题。
2. 优先完成已有架构的验证与集成。
3. 不重复开发已经具备的能力。
4. 不因为存在缺口就自动引入新框架。
5. 不自动启动 Agent、MCP、LangGraph 或新的 Workflow 功能。
6. 不为追求“企业级”而无限扩大范围。
7. 先完成当前阶段验收，再启动下一阶段。
8. 如果发现关键阻塞，明确指出应先解决什么。

最终给出一个**唯一推荐的下一阶段**，并说明选择依据，而不是给出多个无法决策的方向。

## 十、输出文档

新增：

```text
docs/roadmap/Project-Recovery-Audit.md
```

如果该目录或等价文档已存在，先评估是否应更新现有文档；本阶段原则上不覆盖旧的历史审计报告。

报告至少包含：

1. 审计范围与方法。
2. 当前 Git / 工作区状态。
3. 全项目阶段清单。
4. 统一能力矩阵。
5. 关键调用链和架构边界。
6. 测试证据与缺口。
7. 安全风险与部署门禁。
8. 未提交工作和技术债务。
9. 距离最终目标的差距。
10. 后续阶段依赖图。
11. 唯一推荐的下一阶段。
12. 不应继续开发的重复或过早功能。

文档中的每个关键结论都应标明证据来源。

## 十一、本阶段执行限制

本阶段：

```text
只读审计为主
允许新增一份审计报告
允许运行不会产生危险副作用的检查命令
```

禁止：

```text
修改业务逻辑
修改数据库 Schema
运行未经确认的生产写操作
调用真实 LLM 进行不必要的测试
修改已有 baseline
修改既有测试来消除失败
整理或删除工作区文件
commit / push
```

如果某项验证可能触发真实 LLM、网络请求或数据库写入，先检查测试机制和环境要求，不要擅自执行。

## 十二、最终汇报格式

```text
【Project Recovery Audit COMPLETE】

1. 当前分支 / HEAD / 工作区
2. 已确认完成的阶段
3. 部分完成的阶段
4. 未验证的阶段
5. 当前能力矩阵
6. 测试证据与缺口
7. 安全与部署状态
8. 未提交工作和技术债务
9. 距离最终目标的差距
10. 后续 Roadmap
11. 唯一推荐的下一阶段
12. 新增 / 修改文件
13. 本阶段是否修改生产逻辑
14. 未解决的问题
```

最后明确：

```text
PRODUCTION_DEPLOYMENT = BLOCKED
```

除非 HTTPS 和实际网络边界已有新的、可核实的部署证据。

审计完成后立即停止，等待确认；不要自动进入下一阶段开发。
