# Phase 8 Step 4 — P0 Deployment Configuration Hardening

项目：`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Phase 8 Step 1～3 的基础上，修复可以在代码、配置和自动化测试层面解决的 P0 部署安全问题。

本阶段只处理：

1. 安全关键配置的严格校验。
2. 生产服务监听地址的明确约束。
3. PostgreSQL 开发/测试端口的本机访问隔离。
4. 部署回滚文档的破坏性操作防护。

**本阶段不执行生产部署，不连接生产数据库，不开放公网，不申请或部署真实 TLS 证书。**

完成后仍然需要将 Production Deployment 状态保持为 `BLOCKED`，直到生产环境的 TLS、网络隔离和进程管理等条件得到真实验证。

---

## 二、开始前：保护现有工作区

先在 PowerShell 中执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

确认当前 HEAD 和分支。

根据上一阶段报告，预期 HEAD 为：

```text
b004eff Phase 8 Step 2 — Production Gateway Validation Harness
```

但必须以实际 Git 状态为准。

注意：

- Phase 8 Step 3 的文档与测试可能仍为未跟踪文件。
- 不得执行 `git reset`、`git clean`、`git stash`。
- 不得覆盖、删除或重建既有文件。
- 不得把此前未提交的 Phase 8 工作当成当前阶段新产生的修改。
- 修改前先阅读目标文件及其现有测试。
- 本阶段不执行 commit 或 push。

---

## 三、先阅读真实实现

重点阅读：

```text
backend/app/config.py

docker-compose.yml
docker-compose.test.yml

deploy/README.md
deploy/production.env.example
deploy/nginx/wms-workflow-ui.conf.template
deploy/nginx/proxy-headers.conf

docs/decisions/Phase/Phase 8 — Step 1：Production Deployment Foundation.md
docs/decisions/Phase/Phase 8 Step 2 — Production Gateway Validation Harness.md
docs/decisions/Phase/Phase 8 Step 3 — Production Deployment Readiness.md

tests/test_deployment_config.py
tests/test_gateway_validation.py
tests/test_production_readiness.py
```

如果实际路径或文件名不同，先检查真实目录，不要假设。

确认：

1. 当前 settings 的加载方式。
2. `_get_bool`、`_get_str`、`_get_int` 等配置读取函数的真实行为。
3. `APP_HOST` 如何传递给实际应用启动命令。
4. 两份 Compose 文件的 PostgreSQL 端口映射。
5. 部署与回滚 Runbook 的真实命令。
6. 现有测试是否依赖当前配置默认值。

**如果发现需要修改业务逻辑才能完成本阶段，先报告，不要扩大范围。**

---

## 四、任务 A：安全关键配置严格校验

### 当前已知风险

Phase 8 Step 3 已记录：

```text
AUTH_COOKIE_SECURE=flase
```

可能被静默解析为 `False`。

这类错误不能在安全关键配置中悄悄退化为不安全值。

### 实施要求

1. 先检查现有配置读取方式。
2. 明确哪些安全关键变量必须采用严格解析。
3. 对已识别的安全关键布尔配置，至少覆盖：
   - `true`
   - `false`
   - `1`
   - `0`
   - 空值
   - 未设置
   - 非法值，例如 `flase`、`enabled`、`abc`
4. 非法值必须明确失败，不得静默变成 `False`。
5. 明确区分：
   - 未设置时的默认值。
   - 用户显式设置的值。
   - 用户提供的非法值。
6. 不得在异常信息中输出 API Key、密码或完整连接字符串。
7. 尽可能只加固安全关键配置，避免无理由改变所有历史环境变量的解析行为。

### 兼容性要求

不要简单地将所有 `_get_bool` 都改为严格模式，而不检查调用方。

先找出所有调用位置，确认是否会影响现有开发、测试和部署环境。

如果需要统一严格解析，必须补齐相应回归测试。

**不得为了测试通过而把默认安全值改成不安全值。**

---

## 五、任务 B：生产监听地址边界

### 当前已知风险

`backend/app/config.py` 的 `APP_HOST` 默认值为：

```text
0.0.0.0
```

这通常意味着服务监听所有 IPv4 网络接口。

虽然生产环境示例已经显式设置为 `127.0.0.1`，但不能仅依赖文档约定来保证生产启动安全。

### 实施要求

先确认实际启动方式，包括：

- 是否存在 `uvicorn.run()`。
- 是否由命令行参数传递 host。
- 是否通过环境变量传递 host。
- 测试环境如何启动应用。

然后采用**最小、明确且可验证**的约束方式。

优先考虑：

1. 为生产启动配置建立明确的 loopback 监听要求。
2. 增加自动化检查，确保生产部署示例不会使用 `0.0.0.0`。
3. 如果当前架构没有生产启动入口，不得为了满足测试而虚构一个新的生产启动框架。
4. 不要未经兼容性分析直接修改所有环境的 `APP_HOST` 默认值。
5. 如果无法在当前阶段建立可靠的运行时约束，明确记录这一限制，不能声称生产监听边界已经完成。

### 验证要求

至少验证：

- 生产配置示例明确使用 `127.0.0.1`。
- 生产启动配置不能静默退化到 `0.0.0.0`。
- 非法监听配置会被明确拒绝，或被现有启动检查识别为阻断项。
- 不改变现有 API 路由和响应契约。

---

## 六、任务 C：PostgreSQL 端口本机隔离

检查：

```text
docker-compose.yml
docker-compose.test.yml
```

Phase 8 Step 3 已记录，现有 PostgreSQL 端口发布可能没有明确指定 loopback 地址。

如果实际配置仍是：

```yaml
ports:
  - "5432:5432"
```

或：

```yaml
ports:
  - "5433:5432"
```

则需要结合项目实际使用方式，确认是否可以改成仅绑定本机。

预期形式：

```yaml
ports:
  - "127.0.0.1:5432:5432"
```

测试数据库则根据其实际宿主机端口配置相应修改。

### 实施前必须确认

1. 当前开发流程是否需要宿主机访问 PostgreSQL。
2. Docker 内部服务之间是否通过服务名通信。
3. 是否存在依赖宿主机其他网卡访问数据库的现有测试。
4. 是否有其他 Compose override 文件覆盖端口设置。

### 要求

- 只调整必要的端口绑定。
- 不修改 PostgreSQL 版本。
- 不修改数据库 Schema。
- 不修改数据卷。
- 不删除现有数据库容器或数据卷。
- 不运行 `docker compose down -v`。
- 不清理或重建用户现有数据库。
- 不修改业务数据库连接字符串，除非测试证明确实需要且属于本阶段范围。

### 测试

增加或更新配置测试，验证 PostgreSQL 的宿主机端口绑定为明确的 loopback 地址。

如果 Docker 可用，再执行只读配置检查：

```powershell
docker compose config
docker compose -f docker-compose.test.yml config
```

注意：若项目实际 Compose 文件还包含其他覆盖文件，应按真实使用方式检查。

**Compose 静态配置通过，不等于实际宿主机端口已经完成网络隔离验证。**

不得将静态检查报告成生产网络隔离已验证。

---

## 七、任务 D：回滚 Runbook 安全加固

先阅读 `deploy/README.md` 中的真实部署和回滚步骤。

重点检查：

```text
rsync --delete
git checkout
目录覆盖
版本切换
服务重启
配置文件恢复
```

这些命令如果路径、工作区或版本标识错误，可能覆盖已有文件或删除不属于当前部署的内容。

### 要求

1. 明确区分：
   - 源代码工作区。
   - 已构建的发布目录。
   - 生产配置目录。
   - 持久化数据目录。
2. 回滚优先针对明确版本的发布产物，而不是任意工作区。
3. 对涉及 `--delete` 的同步操作，增加明确的目标路径检查。
4. 在实际破坏性同步前，先提供可审查的 dry-run 步骤。
5. 明确禁止将数据库数据目录纳入源码同步或删除范围。
6. 避免通过 `git checkout` 覆盖操作者的未提交修改。
7. 不得假设项目已经具备蓝绿部署或 release-directory 机制；如果不存在，只能将其作为未来建议，不能声称已经实现。
8. 不引入完整的部署自动化平台。

### 文档要求

Runbook 应清楚说明：

- 哪些操作会修改文件。
- 哪些操作会删除文件。
- 执行前如何验证路径和版本。
- 失败时如何停止。
- 回滚后如何检查服务。
- 哪些步骤必须由有权限的部署人员确认。

本阶段只加固文档和必要的静态检查，不执行真实部署或回滚。

---

## 八、严格禁止

本阶段禁止：

```text
修改 Workflow Runtime
修改 AI Orchestrator
修改 Tool Framework
修改 RAG
修改 Text-to-SQL
修改 SQL Validator / Executor
修改 API contract
修改认证业务逻辑
新增数据库表或 migration
修改数据库数据
修改历史 Evaluation Baseline
增加公网监听
配置真实 DNS / TLS 证书
连接生产数据库
执行真实生产部署
执行真实生产回滚
引入新的部署平台或依赖
commit / push
进入 Phase 8 Step 5
```

如果发现需要修改生产业务代码才能解决的问题，先记录具体问题、影响和最小修复方案。

---

## 九、测试要求

新增或更新针对性测试，优先使用现有测试结构。

至少覆盖：

### 配置校验

- 合法布尔值。
- 非法布尔值明确失败。
- 未设置时使用明确默认值。
- 安全关键配置不会因拼写错误变为不安全值。
- 异常消息不泄露 secrets。

### 监听地址

- 生产示例使用 loopback。
- 非法生产监听配置被拒绝或被明确识别为阻断项。
- 不误伤已有测试环境。

### Compose 端口

- PostgreSQL 开发端口仅发布到 loopback。
- PostgreSQL 测试端口仅发布到 loopback。
- 不误判 Docker 内部服务端口。

### Runbook

- 回滚步骤有路径检查。
- 破坏性同步具有 dry-run。
- 不将数据库持久化目录纳入删除范围。
- 不要求覆盖任意工作区。

### Regression

保留现有所有测试和治理门，不得通过跳过测试、弱化断言或修改 baseline 来获得绿色结果。

---

## 十、验证命令

先执行新测试：

```powershell
python -m pytest -q tests/test_production_readiness.py
python -m pytest -q tests/test_deployment_config.py
```

网关回归：

```powershell
$env:RUN_GATEWAY_VALIDATION="1"
python -m pytest -q tests/test_gateway_validation.py
```

Workflow UI 安全回归：

```powershell
python -m pytest -q tests/test_workflow_ui_security.py
```

如 Docker 可用，检查 Compose：

```powershell
docker compose config
docker compose -f docker-compose.test.yml config
```

编译和差异检查：

```powershell
python -m compileall -q backend tests scripts
git diff --check
git diff --stat
git status --short
```

如果有失败，准确报告退出码与失败类别。

不得把 skipped、未执行或静态检查结果写成真实生产验证通过。

---

## 十一、最终报告

完成后只汇报：

```text
【Phase 8 Step 4 — P0 Deployment Configuration Hardening Report】

1. 开始时的 Git 状态
2. 配置校验修改
3. 生产监听地址约束
4. PostgreSQL 端口隔离
5. Runbook 回滚安全
6. 修改文件
7. 新增测试与测试结果
8. Gateway / Workflow UI Regression
9. Compose 配置验证
10. compileall / diff check
11. 是否修改业务运行逻辑
12. 是否修改数据库 Schema / 数据
13. 生产 TLS 状态
14. 生产网络隔离状态
15. 未解决问题
16. Git 状态与 HEAD
17. commit / push 状态

Phase 8 Step 4 完成后立即 STOP。
不得进入 Phase 8 Step 5。
```

最重要的执行顺序：

```text
保护工作区
→ 阅读真实配置
→ 安全配置校验
→ 监听地址约束
→ Compose 端口隔离
→ Runbook 加固
→ 测试
→ 回归
→ 报告
→ STOP
```

如果发现任何需要修改生产业务逻辑或数据库结构才能完成的内容，立即停止并报告，不要擅自扩大范围。

