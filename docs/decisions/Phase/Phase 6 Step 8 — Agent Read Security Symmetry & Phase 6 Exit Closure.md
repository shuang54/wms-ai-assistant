# Phase 6 Step 8 — Agent Read Security Symmetry & Phase 6 Exit Closure

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Project Recovery Audit 的基础上，完成 Phase 6 的安全修复、全量回归和退出审计。

本阶段分为：

```text
8A Agent Read API Security Audit & Minimal Fix
       ↓
8B Full Regression & Database Verification
       ↓
8C Phase 6 Exit Audit（EC1–EC10）
       ↓
8D Documentation & Delivery Readiness
       ↓
等待用户批准 Git Commit
```

**重要：本阶段允许执行必要的最小代码修复，但不得扩大功能范围。**

目标是形成有真实代码、测试和验收证据支撑的 Phase 6 结论，而不是单纯让所有测试显示绿色。

---

## 二、开始前必须读取的文件

先核实当前工作区和阶段证据。

```powershell
git status --short
git branch --show-current
git log -10 --oneline --decorate
git diff --check
git diff --stat
```

读取：

```text
docs/roadmap/Project-Recovery-Audit.md
docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md
docs/security.md
docs/architecture.md
docs/requirements.md
```

以及：

```text
backend/app/api/
backend/app/config.py
backend/app/main.py
backend/app/agent_runtime/
backend/app/workflow_runtime/
tests/
```

如果目录或文件名称与实际仓库不同，先定位真实文件。

重点读取 Agent Read API 的实际实现、DTO、工厂、Service、相关测试和 Phase 5 Exit Audit。

同时找到并阅读：

```text
Phase 6 EC1–EC10
Phase 6 Step 1–7.2 completion reports
test_11 governance gate
baseline-related assertions
```

不得只依赖历史报告推断当前代码状态。

---

## 三、工作区保护规则

当前工作区有 Phase 6 Step 5–7.2 等尚未提交的工作。

必须完整保留。

禁止：

```text
git reset
git clean
git stash
覆盖已有修改
删除未跟踪文件
擅自 commit
擅自 push
```

不要把所有未提交修改归类成当前 Step 8 的新增内容。

报告中必须区分：

* Step 8 新增修改。
* Step 8 开始前已存在的修改。
* 仍未解决的历史问题。

开始前先保存并记录 `git status --short` 和 `git diff --stat` 的结果。

---

# 四、Step 8A — Agent Read API 安全审计与最小修复

## 4.1 先识别真实端点

从实际代码和 FastAPI 路由注册中识别全部 Agent Read API 端点。

不要假定只有两个端点，也不要遗漏隐藏在 router 中的端点。

为每个端点记录：

```text
HTTP method
Path
认证依赖
授权作用域来源
Service / Factory 调用链
当前测试
```

确认用户审计报告中提到的 Agent Read API 缺口是否仍然存在。

## 4.2 安全要求

参考 Workflow Read API Step 7.2 的实现，但先确认两套 API 的真实配置和数据模型。

要求：

1. 所有 Agent Read API 均需要有效认证。
2. 缺少或错误凭据时拒绝访问。
3. 缺少必要安全配置时 fail closed。
4. 项目作用域只能来自服务端认证授权上下文。
5. 客户端不能通过 `project_id`、普通 Header、Body 或代理身份头扩大授权。
6. 不允许因缺少项目作用域而回退到默认项目或全局查询。
7. 跨项目访问不得返回其他项目数据。
8. 不能通过不同错误响应泄露不必要的资源存在性信息。
9. 不得泄露 API Key、内部配置、堆栈、DB Session 或 Connection。
10. 保持现有 Agent Runtime 业务语义和 DTO 契约。

优先复用已验证的安全依赖和配置模式，但不要为了复用而强行让 Agent API 依赖 Workflow Runtime。

如果 Agent 与 Workflow 的授权语义不同，应使用适合各自领域的薄适配层。

## 4.3 明确的修改授权

本阶段明确允许：

* Agent Read API 认证依赖。
* 必要的配置读取。
* 服务端项目授权。
* API 层最小修改。
* 必要的安全测试。

本阶段不允许：

* 修改 Agent Decision 算法。
* 修改 Agent 生命周期。
* 修改 Tool 执行与授权核心逻辑。
* 修改 Checkpoint / Resume / Replay。
* 新增 Agent 写接口。
* 新增 JWT 签发服务、登录系统或 RBAC 后台。
* 修改数据库 Schema。

如果发现必须修改 Agent Runtime 核心逻辑才能实现访问控制，先停止该部分，报告证据和原因。

## 4.4 测试要求

新增或更新测试，至少验证：

```text
缺少凭据 → 401
错误凭据 → 401
必要认证配置缺失 → 503 或项目既有的等价 fail-closed 契约
正确凭据 + 授权项目 → 允许读取
正确凭据 + 未授权项目 → 404 或既有的安全拒绝契约
修改客户端 project_id → 不能扩大授权
敏感信息 → 不出现在响应与日志中
```

具体 HTTP 状态码必须与项目现有 API 约定一致；如果采用不同状态码，必须明确记录理由并保持一致。

必须验证：

* 真实 FastAPI 路由依赖确实执行。
* 未认证请求不会进入敏感数据读取路径。
* OpenAPI 声明与实际运行时行为一致。
* Agent 原有响应字段和读取语义没有无意变化。

不能只用静态 AST 断言证明认证有效。

---

# 五、Step 8B — 全量回归与失败诊断

Project Recovery Audit 报告记录的离线测试结果：

```text
7257 passed
5 failed
915 skipped
```

这些是历史实测结果，不是本阶段执行结果。

必须重新验证。

## 5.1 先重现原始失败

运行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

保存真实退出码、失败测试名称和 traceback。

优先定位：

```text
test_11 governance gate
baseline-related assertions
4 个级联失败
```

逐一判断根因。

不要直接把所有失败归类成测试问题。

## 5.2 修复规则

如果失败来自 Step 8 新增的安全代码：

进行最小修复并重新运行相关测试。

如果失败来自治理门：

检查治理门所要求的工作区状态与本次任务的冲突。

不得通过删除治理门、跳过测试或无条件允许 dirty worktree 来消除失败。

如果失败来自 baseline 数字漂移：

1. 先确认 baseline 的用途和维护规则。
2. 追踪新增测试为什么影响计数。
3. 判断数字变化是预期变更还是治理缺陷。
4. 按项目现有流程处理。
5. 不得为了通过测试而直接覆盖历史 baseline。

如果多个失败存在共同根因，应修复根因，而不是逐个打补丁。

## 5.3 全量离线回归

完成最小修复后重新运行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

要求：

```text
0 failed
```

如果仍然失败，必须如实报告，不能宣布 Phase 6 COMPLETE。

不得把失败测试改成 skip 来满足验收条件。

---

# 六、数据库集成回归

先检查：

* 当前 DB 测试配置。
* PostgreSQL 连接来源。
* `RUN_DB_TESTS` 的真实门控方式。
* 现有测试是否包含清理与 residue 检查。
* 数据库是否明确属于专用测试环境。

不得把开发或生产数据库误当成测试数据库。

确认安全后，运行项目实际使用的 DB-gated 全量测试方式。

例如：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果项目实际门控方式不同，使用真实配置并记录原因。

必须报告：

```text
DB passed
DB failed
DB skipped
DB residue
```

重点验证：

* Agent Read API 安全回归。
* Agent Runtime 持久化与恢复。
* Workflow Runtime 持久化与读取。
* Store Adapter / Factory。
* Run、Node Run、Approval 的归属与隔离。
* 数据库结构没有未经批准的变化。

如果 DB 测试失败：

先定位真实根因，不得直接跳过失败测试。

如果没有安全可用的测试数据库，停止 DB 执行并报告阻塞，不得猜测或切换到其他数据库。

---

# 七、治理门与 CI 检查

检查：

```text
test_11 governance gate
CI workflow
pytest configuration
conftest.py
baseline snapshots
```

重点核实 Recovery Audit 提到的以下问题：

1. 治理门是否依赖 tracked worktree clean。
2. 当前未提交工作是否使治理门必然失败。
3. baseline 数字断言是否随测试数量变化而漂移。
4. CI 是否真正执行 pytest。
5. DB 测试是否被正确隔离。
6. 测试是否有合理的 timeout 和失败诊断。

只修复能够确认属于 Phase 6 Exit 必要范围的问题。

不要借机重构整个 CI 平台。

如果 CI 改动明显超出最小范围，记录为后续任务。

对于必须在提交后才能通过的治理门：

明确区分：

```text
工作区状态下的结果
提交后预期验证
实际提交后的验证
```

不得把“预计提交后会通过”写成“已通过”。

本阶段禁止擅自 commit。

---

# 八、Step 8C — Phase 6 Exit Audit

先找到项目中正式定义的 EC1–EC10。

必须使用真实定义，不得自行重写验收标准以迎合当前实现。

为每条 Exit Criterion 建立表格：

| EC | 原始验收要求 | 当前证据 | 测试结果 | 状态 | 剩余问题 |
| -- | ------ | ---- | ---- | -- | ---- |

状态只允许：

```text
PASS
FAIL
BLOCKED
NOT_VERIFIED
```

每个 `PASS` 必须有证据。

每个 `FAIL` 必须指出差距。

每个 `BLOCKED` 必须说明阻塞条件。

每个 `NOT_VERIFIED` 必须说明缺少哪类证据。

特别注意 Recovery Audit 指出的：

* EC1 及治理门失败。
* EC8 文档同步。
* Phase 6 是否允许解冻 Agent 后续工作。
* Workflow Runtime 只有进程内运行能力，是否符合正式 Exit Criteria。
* 是否存在 Step 6 / Step 7 编号或阶段定义不一致。

不得擅自修改 Exit Criteria 来让阶段通过。

如果某项 EC 与后续 Agent 解冻条件存在张力，必须引用原文并报告冲突，不能自行裁决。

## Exit Audit 文档

新增或更新符合项目惯例的正式文件，例如：

```text
docs/roadmap/Phase-6-Exit-Audit.md
```

如果已有等价文件，优先更新现有文件，避免重复创建多个互相矛盾的 Exit Audit。

---

# 九、Step 8C — 文档同步

在不扩大范围的前提下，检查：

```text
docs/architecture.md
docs/api.md
README.md
```

仅同步已经被代码和测试证明的事实。

要求：

* 更新 Workflow Read API 的认证要求。
* 更新 Agent Read API 的认证要求。
* 说明项目授权来源。
* 明确生产部署仍有 HTTPS 和网络边界门禁。
* 不把未验证的部署能力写成已实现。
* 不删除历史设计决策。
* 不把任务书副本和执行报告混成正式 API 文档。

如果 README 或 API 文档不存在，先按实际目录核查，再决定是否新增。

---

# 十、Phase 6 Exit 判定

只有满足以下条件，才能判定 Phase 6 Exit：

1. EC1–EC10 全部有明确结论。
2. 所有必须通过的 EC 均为 PASS。
3. Agent Read API 认证与项目授权缺口已修复并经过真实请求测试。
4. Workflow Read API 原有认证与授权回归通过。
5. 全量离线测试 0 failed。
6. 要求执行的全量 DB 回归通过；若因环境无法执行，必须标记 BLOCKED，不得宣布完整通过。
7. 治理门状态有真实证据。
8. 架构与 API 文档已同步。
9. 没有未经批准的数据库 Schema 变化。
10. 没有因为本阶段修改而引入已知安全回归。

**生产部署门禁与 Phase 6 代码退出是两个独立判断。**

即使 Phase 6 代码 Exit PASS，也必须继续记录：

```text
HTTPS = NOT VERIFIED
Network boundary = NOT VERIFIED
PRODUCTION_DEPLOYMENT = BLOCKED
```

除非有新的真实部署证据，否则不得解除生产部署阻断。

---

# 十一、Step 8D — Git 与交付收口

本阶段结束时：

* 保留全部已有工作。
* 列出本阶段修改与历史未提交修改。
* 检查 `git diff --check`。
* 检查是否存在不必要的重复文档。
* 不删除零字节文档或任务书副本；只报告并提出建议。
* 不自动整理目录或批量重命名文件。

必须先输出完整的阶段验收报告。

**未经用户明确批准，绝对不能执行 `git commit` 或 `git push`。**

如果所有 Exit 条件满足，报告“建议进入提交准备”，等待用户批准。

如果 Exit 条件不满足，列出剩余阻塞项，不得为了清理工作区而提前提交。

---

# 十二、测试与验证命令

至少执行：

```powershell
python -m pytest -q tests/test_workflow_read_api_security_audit.py
python -m pytest -q tests/test_workflow_read_api.py
python -m pytest -q tests/test_agent_read_api_security.py
```

第三条路径应替换为仓库中实际存在的 Agent Read API 安全测试文件。

然后执行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

确认测试环境安全后，再执行全量 DB-gated 回归。

编译：

```powershell
python -m compileall backend tests
```

并执行项目实际使用的 lint、治理门和：

```powershell
git diff --check
git status --short
git diff --stat
```

不要编造不存在的命令或测试文件。

---

# 十三、最终报告格式

```text
【Phase 6 Step 8 — Agent Read Security & Exit Closure】

1. Agent Read API 端点清单
2. 认证与项目授权实现
3. 安全测试结果
4. Workflow Read API 回归
5. 全量离线测试
6. 全量 DB 测试
7. 治理门与 baseline 结果
8. EC1–EC10 Exit Audit
9. 文档同步
10. 数据库 Schema / residue
11. 本阶段新增文件
12. 本阶段修改文件
13. 历史未提交工作区状态
14. Git diff / lint / compileall
15. 安全风险与部署门禁
16. Phase 6 Exit 判定
17. 是否建议进入提交准备
18. 尚未解决的问题
```

最后明确：

```text
Phase 6 Exit = PASS / FAIL / BLOCKED
PRODUCTION_DEPLOYMENT = BLOCKED
Git commit/push = NOT EXECUTED
```

Phase 6 Exit 只有在真实验收条件全部满足时才能为 PASS。

如果治理门、DB 回归或 EC 条件未通过，必须如实给出 FAIL 或 BLOCKED，并指出下一步的最小修复任务。

---

## 十四、完成后立即停止

不要进入 Phase 7。

不要开发 Workflow HTTP 命令面。

不要开发 Agent、MCP 或 LangGraph 新功能。

不要擅自 commit 或 push。

先完成安全修复、回归、Exit Audit 和报告，等待用户确认。
