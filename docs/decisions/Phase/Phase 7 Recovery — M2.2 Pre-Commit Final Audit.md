# Phase 7 Recovery — M2.2 Pre-Commit Final Audit

## 一、目标

对当前 `phase7` 工作区进行 M2.2 提交前最终审查。

当前已完成：
- M1 用户身份与认证
- M2.1 Conversation Ownership Audit
- M2.2 Conversation Authentication & Ownership
- M2.2 D1 Project Scope Hardening

本任务仅审查、验证和补齐本阶段必要的文档。

**不执行 Git add、commit、push、PR 或部署。**

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 二、审查前先读取真实状态

先执行并记录：

```powershell
git status --short --branch
git log -5 --oneline
git diff --stat
git diff --name-status
git diff
git diff --cached --stat
git ls-files --others --exclude-standard
```

确认当前 HEAD、分支、已暂存变更、未暂存变更及未跟踪文件。

预期历史 HEAD 可能为：

`f919e7209045be3c9d12efe49c83086000c0641b`

但必须以实际输出为准，不得假设。

**禁止清理、覆盖或重置工作区。** 不执行 `reset`、`checkout`、`clean`、`stash`、`restore` 等破坏性操作。

## 三、检查变更范围

逐个审查所有已修改和未跟踪文件，重点确认：

1. M2.2 Conversation 五个 API：
   - 创建会话
   - 读取会话
   - 读取消息
   - 追加消息
   - 归档会话

2. D1 Project Scope：
   - 服务端配置是允许项目的唯一授权来源。
   - 已注册不等于已授权。
   - 配置不可解析时 fail-closed。
   - 不存在默认项目回退。
   - 请求中的 `project_id` 必须精确匹配允许项目。
   - ResourceOwner 使用已验证的服务端项目值。

3. Conversation 所有权：
   - 不存在自动认领历史会话。
   - 非所有者与不存在会话使用相同的 404 语义。
   - 所有权服务故障 fail-closed。
   - 创建会话和 ResourceOwner 绑定保持同一事务。

4. 安全边界：
   - 写操作 CSRF 检查仍然有效。
   - Session/API-Key 身份边界未被混淆。
   - 不新增未授权的匿名访问。
   - 不记录或输出凭据、Cookie、API Key、数据库连接字符串。

5. 架构边界：
   - Conversation / Turn / Evidence 冻结字段不变。
   - 不新增 migration。
   - 不修改 Prompt、Validator、Executor、Router 策略或历史 baseline。
   - 不引入 Agent/Workflow 的额外功能。

输出每个发现的严重级别、文件、具体代码位置、影响和建议。

## 四、补齐 M2.2 文档债务

检查并按现有文档风格更新：

```text
.env.example
docs/api.md
docs/security.md
```

要求：

### `.env.example`

- 说明 `PROJECT_ID` 是当前部署的服务端项目作用域配置。
- 修改它会改变该部署允许创建会话的项目。
- 配置必须与服务端 Project Registry 保持一致。
- 不加入真实凭据。
- 不改变默认配置行为。

### `docs/api.md`

准确记录五个 Conversation API 的：
- 登录态要求
- CSRF 要求
- 项目作用域校验
- 会话所有权校验
- 401 / 403 / 404 / 422 / 503 等状态语义

以真实代码为准，不虚构 API 行为。

### `docs/security.md`

明确：
- 当前是单部署项目作用域，不是用户级 RBAC。
- 任意已认证用户仍可能在该部署允许的项目中创建自己的会话。
- 其他已注册项目不会因为已注册就自动被授权。
- 未授权或不存在的会话不会被自动认领。
- 多项目授权需要未来建立真实用户—项目授权模型。

仅更新与 M2.2 直接相关的内容，不扩展为新安全项目。

## 五、测试与回归

先运行定向测试：

```powershell
python -m pytest tests/test_conversation_ownership.py -q
python -m pytest tests/test_conversation_message_api_architecture_audit.py -q
```

然后运行受影响的 Conversation API / DB 集成测试。

如果需要 DB 测试，使用项目现有的测试数据库和门控机制。PowerShell 示例：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_conversation_ownership.py -q
```

随后执行全量回归：

```powershell
$env:PYTHONUTF8="1"
python -m pytest -q
python -m compileall backend tests scripts
```

不要为了让测试通过而修改冻结测试、基线或历史结果。

### 失败归因

特别区分：

**A. 本次 M2.2 / D1 新引入的失败**

必须定位根因并报告。

**B. 提交前治理检查失败**

例如 `test_11_backend_working_tree_is_unmodified` 因当前存在未提交后端改动而失败。只记录事实，不尝试绕过测试。

**C. 已存在的 Step 8 遗留失败**

历史已知：
- `test_tool_argument_contract.py` 中 2 项
- `test_tool_argument_extractor.py` 中 1 项

必须核对当前实际失败结果，不得只依据历史记录认定。

如果出现新的失败，必须单独列出，不能直接归入历史问题。

### DB 安全

检查测试数据库关键表的残留计数，至少包括：

- `app_user`
- `user_session`
- `resource_owner`
- `conversation`
- `conversation_turn`
- 项目已有的 observability 相关表

必须确认 DB residue 为 0，或者逐项解释已有测试数据为何保留。

## 六、Git Diff 与安全复核

使用只读命令审查：

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-status
git diff
git diff --cached
```

检查：

- 是否有意外文件
- 是否包含 secrets
- 是否误改历史 baseline
- 是否修改冻结模型或数据库结构
- 是否存在与 M2.2 无关的代码变化
- 文档是否与实际实现一致

Git 官方文档说明，`git status` 能区分暂存、未暂存和未跟踪文件；`git diff` 用于检查工作区与索引或提交之间的差异。审查时必须覆盖这三类状态，不能只看 `git diff`。参考：https://git-scm.com/docs/git-status 和 https://git-scm.com/docs/git-diff

## 七、最终报告格式

```text
【Phase 7 Recovery — M2.2 Pre-Commit Final Audit】

1. HEAD / Branch
2. Git 工作区状态
3. M2.2 + D1 变更文件清单
4. Project Scope 安全审查
5. Conversation Ownership / CSRF / Transaction 审查
6. 文档更新
7. 定向测试
8. 全量测试
9. 历史失败与新增失败区分
10. compileall / diff --check
11. DB residue
12. 冻结架构与历史 baseline 检查
13. 剩余风险
14. 最终结论：READY / NOT READY

Git add: NOT EXECUTED
Git commit: NOT EXECUTED
Git push: NOT EXECUTED
Deployment: NOT EXECUTED
M2.3: NOT STARTED
STOP
```

## 八、停止条件

审查与必要文档补齐完成后立即停止。

不执行 Git add/commit/push，不创建 PR，不部署，不开始 M2.3，不修改 Step 8 历史基线，不扩大到其他安全模块。

如果发现本次实现存在授权漏洞，先报告根因和最小修复建议，不要擅自扩展修复范围。