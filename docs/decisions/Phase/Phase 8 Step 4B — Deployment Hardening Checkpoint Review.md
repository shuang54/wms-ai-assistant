# Phase 8 Step 4B — Deployment Hardening Checkpoint Review

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

对 Phase 8 Step 3、Step 4、Step 4A 和 Review Fix 的未提交变更进行一次完整审查，为后续 checkpoint 做准备。

本阶段只进行：

- Git 工作区审计
- 变更内容与范围核对
- 现有测试和治理门复核
- checkpoint 文件清单整理
- 提交前风险报告

**本阶段不自动 commit、push，不进入 Step 5，不部署生产环境。**

## 二、保护现有工作区

先执行并记录：

```powershell
git status --short --branch
git diff --check
git diff --stat
git diff --cached --stat
git log -5 --oneline
```

逐一检查 tracked 修改、staged 修改和 untracked 文件。

特别保护：

```text
backend/app/config.py
deploy/README.md
docker-compose.yml
docker-compose.test.yml
tests/test_deployment_hardening.py
tests/test_production_readiness.py
tests/test_deployment_config.py
tests/test_gateway_validation.py
docs/decisions/Phase/
```

不要假设上述文件全部属于本阶段；必须根据实际 diff 确认。

禁止：

```text
git reset
git clean
git stash
git checkout -- <file>
git restore
```

不得覆盖、删除或隐藏现有修改。

## 三、检查变更边界

重点审查：

1. `APP_ENV` 缺省、合法值和非法值的行为。
2. `AUTH_COOKIE_SECURE` 严格布尔解析。
3. 生产环境监听地址校验。
4. `production_binding_verified` 的真实语义。
5. Docker Compose PostgreSQL 端口是否只绑定到 `127.0.0.1`。
6. Nginx Gateway Validation 是否仍保留真实 HTTP 测试。
7. Runbook 是否清楚区分配置校验与真实 socket、TLS、防火墙验证。
8. 是否存在意外修改生产业务逻辑、认证、数据库 Schema 或 API contract 的情况。
9. 是否新增依赖、泄漏凭证或削弱安全检查。

不得把“应用配置校验通过”描述成“生产监听地址已被强制保证”。Uvicorn CLI 参数仍可能覆盖应用配置，必须保留这一限制。

## 四、复核治理门

运行项目现有治理门，不得修改、跳过或弱化测试。

如果：

```text
test_11_backend_working_tree_is_unmodified
```

仍因 `backend/app/config.py` 的未提交修改失败：

- 记录完整失败信息；
- 判断它是否是预期的工作区保护门；
- 不得删除该门；
- 不得把它标记为 PASS；
- 不得擅自提交来绕过失败。

同时复核相关测试：

```powershell
python -m pytest -q tests/test_deployment_hardening.py
python -m pytest -q tests/test_production_readiness.py
python -m pytest -q tests/test_deployment_config.py
python -m pytest -q tests/test_gateway_validation.py
python -m pytest -q tests/test_workflow_ui_security.py tests/test_auth_api.py
python -m compileall -q backend tests scripts
git diff --check
```

如果 Gateway 测试要求环境变量或 Docker，严格按项目现有机制执行，并准确报告 skipped、failed 和未执行项。不得将未执行的测试报告为 PASS。

## 五、checkpoint 清单

整理建议纳入 checkpoint 的文件，按以下类别列出：

- Step 3 readiness 文档与测试；
- Step 4 配置加固与 Compose 端口绑定；
- Step 4A 环境识别与 fail-closed guard；
- Review Fix 文档和测试。

同时识别：

- 不属于 Phase 8 的历史未提交变更；
- 与本阶段无关的文件；
- 不应纳入本次 checkpoint 的运行产物、日志、密钥或临时证书。

**只输出清单和建议，不执行 git add 或 git commit。**

## 六、最终报告

严格按以下格式汇报：

```text
【Phase 8 Step 4B — Checkpoint Review Report】

1. Git 分支与 HEAD
2. 工作区状态
3. tracked / staged / untracked 文件清单
4. Step 3 / Step 4 / Step 4A / Review Fix 变更边界
5. 安全配置审查
6. Compose 端口绑定审查
7. 测试结果
8. Governance Gate 结果
9. checkpoint 文件建议清单
10. 不应纳入 checkpoint 的文件
11. 未解决问题
12. Production Deployment 状态
13. 是否执行 commit / push
14. Git 最终状态

STOP：等待人工审阅。
```

本阶段完成后立即停止，不进入 Phase 8 Step 5。