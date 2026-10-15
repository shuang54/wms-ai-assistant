# Phase 7 Recovery — M2.3 A2 任务书与只读核验

项目：`D:\coding\ai\wms-ai-assistant`

阶段：`M2.3 A2 — Anonymous Read-only Usage / Observability Endpoints`

当前基线参考：`5444b610defa224c30e557b5ee4d474d9788c001`

## 一、任务目标

本阶段首先完成 A2 的任务定义和现状审查。

需要回答四个问题：

1. 当前系统中，7 个匿名只读使用量／可观测性接口具体是什么？
2. 这些接口当前的认证、授权、项目隔离和数据读取边界是什么？
3. M2.3 A1 已建立的 Session、CSRF 与 fail-closed 规则是否会影响这些接口？
4. 冻结测试基线、工作区状态和历史治理证据是否足以支持后续实施？

**本阶段不实现 A2，不修改任何文件。**

## 二、绝对禁止

本次只读核验期间禁止：

- 修改、创建、删除或格式化任何文件。
- 执行 `git add`、`commit`、`push`、`reset`、`clean`、`stash`、`checkout`、`switch`、`restore`、`rebase`、`cherry-pick` 或其他 Git 写操作。
- 运行 pytest、测试脚本、迁移脚本、应用启动命令或会修改缓存／快照的评测工具。
- 访问 PostgreSQL 或其他数据库。
- 调用 LLM、Embedding、外部 API 或网络服务。
- 修改冻结测试矩阵、历史 baseline、治理注册表或 A1 提交。
- 自动修复发现的问题。
- 进入 A2 实际编码。

允许执行只读 Git 命令、源码搜索、文件阅读、静态分析和已有治理文件核对。

## 三、Step 1：核验工作区

先执行：

```powershell
Set-Location 'D:\coding\ai\wms-ai-assistant'

git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/phase7
git log -1 --format=fuller
git diff --check
git diff --stat
git diff --cached --stat
git ls-files --others --exclude-standard
```

检查提交来源：

```powershell
git show -s --format=fuller 5444b610defa224c30e557b5ee4d474d9788c001
git show --stat --oneline 5444b610defa224c30e557b5ee4d474d9788c001
git diff --name-status bbb5d7508b64d1b12e5215c25f8e9cf61b108726..5444b610defa224c30e557b5ee4d474d9788c001
```

要求：

1. 不假设当前 HEAD 仍是 `5444b61`。
2. 不假设工作区仍只有历史报告中的未跟踪审计文档。
3. 不覆盖、删除或忽略当前存在的未跟踪文件。
4. 如果分支、HEAD、远端引用或工作区状态与历史报告不同，记录差异并停止任何有风险的操作。
5. A1 的提交和推送授权已经由项目方明确确认，不要重复要求授权。
6. 不要求本次重新提交或推送 A1。

## 四、Step 2：发现真实的 7 个接口

不要根据历史描述猜测路径。

先搜索：

```powershell
rg -n -i "usage|observability|metrics|statistics|token|cost|latency|request_id" backend/app/api backend/app/services tests docs
```

搜索 API 路由定义和认证依赖：

```powershell
rg -n "@router\.(get|post|put|patch|delete)|@app\.(get|post|put|patch|delete)" backend/app/api
rg -n "require_session|require_csrf|CurrentPrincipal|APIKey|api_key|Depends\(" backend/app/api
```

阅读搜索命中的实际文件及相关依赖定义。

建立真实接口清单，逐个记录：

| 字段 | 核验要求 |
|---|---|
| HTTP Method | 以实际路由为准 |
| Path | 以实际路由为准 |
| Handler | 实际函数名 |
| 功能 | 根据实现确认 |
| 当前认证 | Session、API Key、无认证或其他实际机制 |
| CSRF | 是否适用以及当前是否检查 |
| Project Scope | 是否要求和验证项目范围 |
| 数据来源 | 内存、Store、Repository 或其他实际来源 |
| 访问副作用 | 是否可能触发执行、写入或外部调用 |
| 敏感字段 | 是否可能暴露凭证、Prompt、SQL、原始异常或内部数据 |
| 当前测试 | 已有测试文件及测试名称 |

**必须以真实源码为证据。**

如果找不到恰好 7 个接口，不得为了凑数把无关接口算进去。列出发现的接口、候选项和数量差异，并解释需要进一步确认的地方。

## 五、Step 3：检查 A1 认证边界

阅读以下实际实现：

```text
backend/app/api/auth_dependency.py
backend/app/api/chat.py
backend/app/api/orchestrator_chat.py
backend/app/api/rag.py
backend/app/api/tool_chat.py
tests/api_session_auth.py
tests/test_ai_execution_api_auth.py
docs/security.md
docs/api.md
```

检查 A2 的候选接口是否涉及以下边界：

1. 是否存在匿名访问入口。
2. 是否错误复用 A1 的 AI 执行接口认证策略。
3. 是否允许通过任意 `project_id`、`run_id` 或其他资源 ID 跨项目读取。
4. 是否暴露内部调用次数、Token 用量、模型信息、错误信息或其他可能敏感的数据。
5. 是否将 Session 身份、API-Key 身份和项目授权混为一谈。
6. 是否存在不必要的数据库查询、全表扫描或高成本统计。
7. 是否会触发 LLM、Tool、Runtime 执行或数据库写操作。

不要直接把 A1 的 Session + CSRF 方案复制到 A2。

A2 的认证策略必须由接口性质、现有身份模型和实际调用方需求决定。CSRF 是否适用也必须逐接口分析。

本次只记录建议，不修改认证策略。

## 六、Step 4：核验冻结测试基线

只读取现有治理配置、测试注册表、baseline 文件和历史报告。

搜索：

```powershell
rg -n -i "frozen|baseline|matrix gate|registry|M2.3|A1|A2|375|356|19|180|555" docs tests scripts
```

需要确认：

1. 冻结测试矩阵的权威定义在哪里。
2. 当前冻结文件数量和测试数量从哪个文件读取。
3. A1 是否修改过冻结测试文件、测试注册表或历史 baseline。
4. A2 候选接口有哪些现有测试可复用。
5. 后续需要运行哪些既有测试，但本次不得运行它们。
6. 哪些历史失败已知，哪些结果尚未验证。

历史报告中的 `375 / 356 / 19 / 0`、DB `180`、总计 `555` 仅作为待核验参考，不得直接宣称当前仍然成立。

不得通过修改测试数量、注册表或 baseline 使治理检查通过。

## 七、Step 5：核验 A1 治理证据

只读检查：

```text
docs/decisions/Phase/
docs/security.md
docs/api.md
```

以及工作区中是否存在相关的未跟踪审计文档。

分别记录：

- A1 任务书是否已提交。
- A1 实施与测试报告是否存在。
- Pre-Commit Audit 是否已跟踪并进入提交历史。
- A1 显式提交授权是否有仓库内证据。
- 提交签名状态。
- `git diff --check` 的历史问题是否仍存在。
- A1 的测试输出是否有可复核的原始证据。

授权已在项目方对话中明确确认。这里只核验仓库证据的可追溯性，不得据此宣称授权不存在。

不得自动把未跟踪文档加入 Git，也不得修改 A1 历史提交。

## 八、Step 6：形成 A2 最小任务设计

基于前面实际发现的接口，提出 A2 实施阶段的最小设计。

设计至少覆盖：

1. 接口范围与明确排除项。
2. 每个接口的认证和授权规则。
3. 项目隔离规则。
4. 只读数据访问边界。
5. 响应 DTO 和敏感字段过滤原则。
6. 错误处理和 fail-closed 行为。
7. 单元测试与边界测试。
8. 后续 DB-gated 测试要求。
9. 冻结矩阵和历史 baseline 的保护措施。
10. 预计新增／修改文件清单。

设计原则：

- 不假设接口必须全部改成 Session 认证。
- 不新增 API-Key 旁路。
- 不绕过现有 Store Ports。
- 不暴露 ORM、Session、Connection、密钥、Prompt 或原始异常。
- 不为了统计而触发额外业务执行。
- 不新增数据库表或迁移，除非未来任务书单独授权。
- 不扩大 A1 的范围。
- 不在本阶段开始实现。

## 九、最终报告格式

完成后仅汇报：

```text
【Phase 7 Recovery — M2.3 A2 Read-only Audit Report】

1. 工作区与分支状态
2. HEAD / 远端一致性
3. A1 基线与治理证据
4. 发现的实际接口数量
5. 7 个候选接口明细
6. 当前认证与授权边界
7. 项目隔离风险
8. 数据敏感性与副作用风险
9. 冻结测试基线
10. 可复用测试与待执行测试
11. A2 最小实施设计
12. 预计变更文件
13. 未确认事项
14. 风险与阻塞
15. Git / DB / Network / Tests 操作确认

状态：A2 TASK DEFINED / READY FOR IMPLEMENTATION 或 BLOCKED
A2 Implementation：NOT STARTED
```

对所有没有原始输出支撑的结果标记 `UNVERIFIED`。

如果发现阻塞，列出具体文件、接口、缺失能力和最小处理建议，不要擅自修改。

## 十、最终停止条件

本任务完成后立即停止。

不得：

- 实施 A2。
- 修改任何文件。
- 执行 Git 写操作。
- 运行测试。
- 访问数据库。
- 调用外部服务。
- 自动进入下一阶段。

等待项目方明确指令。
