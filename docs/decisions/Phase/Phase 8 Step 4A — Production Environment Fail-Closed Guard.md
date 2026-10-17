# Phase 8 Step 4A — Production Environment Fail-Closed Guard

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

针对 Phase 8 Step 4 审阅发现的配置边界问题，实施最小补充修复：

> 当生产环境标识缺失或非法时，不得让生产监听地址校验被静默绕过。

本阶段只修复配置层及其测试，不进入 Phase 8 Step 5。

## 二、开始前保护工作区

先执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -5 --oneline
```

必须保留当前所有未提交和未跟踪文件，包括：

- `backend/app/config.py`
- `deploy/README.md`
- `docker-compose.yml`
- `docker-compose.test.yml`
- Phase 8 Step 3 文档与测试
- Phase 8 Step 4 文档与测试

不得执行 `reset`、`clean`、`stash`、`checkout` 覆盖工作区或 commit/push。

## 三、先阅读真实配置加载路径

阅读：

```text
backend/app/config.py
tests/test_deployment_hardening.py
tests/test_deployment_config.py
tests/test_production_readiness.py
deploy/production.env.example
```

确认：

1. `APP_ENV` 的真实默认值。
2. `Settings` 是如何构造的。
3. `__post_init__` 在什么情况下运行。
4. `.env` 是否由启动入口显式加载。
5. 测试环境如何设置 `APP_ENV`。
6. 现有 `APP_HOST`、`APP_PORT` 的调用方式。

不要假设 `.env` 一定会自动加载。

## 四、实施要求

### 4.1 区分缺失、空值和明确环境值

必须明确区分：

```text
APP_ENV 未设置
APP_ENV=""
APP_ENV="   "
APP_ENV="development"
APP_ENV="test"
APP_ENV="production"
APP_ENV="prod"
APP_ENV=非法值
```

不得仅通过读取一个默认字符串，就把“没有配置 APP_ENV”和“明确配置为开发环境”视为完全相同的情况。

### 4.2 保持兼容性

- 不得破坏现有开发、测试环境。
- 不得把 `APP_HOST` 全局默认值改成其他值。
- 不得修改认证业务逻辑。
- 不得修改 API、Workflow Runtime、Tool、RAG 或 Text-to-SQL。
- 不得改变 `AUTH_COOKIE_SECURE` 已完成的严格布尔解析。
- 不得修改其他 `_get_bool` 调用点的既有语义。

### 4.3 生产监听保护

继续要求生产环境：

```text
APP_ENV=production
APP_HOST=127.0.0.1
```

允许的回环地址应以现有实现为准。

当 `APP_ENV=production` 时，非回环监听地址必须导致配置校验失败。

当 `APP_ENV` 缺失、空白或非法时，必须采取明确、可测试的策略，确保它不能被误认为已经完成生产安全校验。

如果现有部署方式无法可靠地区分开发与生产，先报告最小修复方案，不得猜测。

### 4.4 安全错误信息

异常信息可以指出变量名和允许值，但不得回显不可信配置内容、密码、API Key 或连接字符串。

## 五、测试要求

在 `tests/test_deployment_hardening.py` 中增加或调整测试。

至少覆盖：

1. 显式 production + loopback：通过。
2. 显式 production + `0.0.0.0`：拒绝。
3. 显式 development：保持既有开发行为。
4. 显式 test：保持既有测试行为。
5. `APP_ENV` 缺失。
6. `APP_ENV` 为空串。
7. `APP_ENV` 纯空白。
8. 非法 `APP_ENV`。
9. 配置错误不会泄漏环境变量敏感值。
10. 使用真实子进程验证配置导入/构造的退出码，不只使用 mock。

如果某个情况根据项目现有契约必须被接受或拒绝，应在测试中明确写出，不要使用宽泛断言。

## 六、回归测试

先执行：

```powershell
python -m pytest -q tests/test_deployment_hardening.py
python -m pytest -q tests/test_production_readiness.py
python -m pytest -q tests/test_deployment_config.py
```

再执行：

```powershell
$env:RUN_GATEWAY_VALIDATION="1"
python -m pytest -q tests/test_gateway_validation.py
```

以及：

```powershell
python -m pytest -q tests/test_workflow_ui_security.py tests/test_auth_api.py
```

最后执行：

```powershell
python -m compileall -q backend tests scripts
git diff --check
git status --short
```

如果治理门仍然因未提交的 `backend/app/config.py` 变更失败，必须如实报告；不得修改治理门或伪造通过。

## 七、禁止事项

本阶段禁止：

- 修改 PostgreSQL 运行中容器。
- 修改数据库数据或 Schema。
- 修改 Compose 端口绑定。
- 修改 TLS、DNS、防火墙或生产网络。
- 修改业务逻辑或 API contract。
- 修改历史 baseline。
- 删除、跳过或弱化测试。
- commit/push。
- 进入 Phase 8 Step 5。

## 八、最终报告

```text
【Phase 8 Step 4A — Production Environment Guard Report】

1. 修改前 Git 状态
2. APP_ENV 配置契约
3. 缺失 / 空值 / 非法值的处理
4. 生产监听地址校验
5. 新增测试与结果
6. Gateway / Auth / Workflow UI 回归
7. compileall / diff check
8. 是否修改业务逻辑
9. 是否修改数据库或运行中容器
10. 未解决问题
11. Git 状态与 HEAD
12. commit / push 状态

Production Deployment = BLOCKED
Phase 8 Step 4A 完成后立即 STOP。
```

