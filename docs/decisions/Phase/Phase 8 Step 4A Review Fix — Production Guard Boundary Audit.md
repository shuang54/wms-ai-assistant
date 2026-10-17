# Phase 8 Step 4A Review Fix — Production Guard Boundary Audit

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

只复核 Phase 8 Step 4A 的环境识别与生产监听安全边界，避免将 `unspecified` 状态误认为生产部署已验证。

## 开始前

执行：

```powershell
git status --short
git diff --check
git diff --stat
```

保留所有未提交、未跟踪文件。不得 reset、clean、stash、覆盖或提交。

## 检查内容

阅读当前 `backend/app/config.py` 和全部 `Settings` 使用位置，确认：

1. `production_binding_verified` 仅在明确生产环境且监听地址满足要求时为 `True`。
2. `unspecified` 不会被报告或部署检查误判为生产配置通过。
3. 所有现有正式部署示例显式设置 `APP_ENV=production`。
4. 不存在绕过 `Settings` 构造校验的正式启动入口。
5. `APP_ENV` 非法值始终失败，不会静默降级为 development。
6. 开发与测试环境现有行为保持兼容。

## 修复边界

如果发现缺口，只允许在配置层和相关测试中做最小修改。

不得修改业务逻辑、API、认证逻辑、数据库 Schema、Compose 端口或运行中的容器。

如果仓库不存在统一的生产启动入口，不要临时发明新的启动框架；应明确记录该限制及所需的最小后续措施。

## 验证

```powershell
python -m pytest -q tests/test_deployment_hardening.py
python -m pytest -q tests/test_production_readiness.py
python -m pytest -q tests/test_deployment_config.py
python -m compileall -q backend tests scripts
git diff --check
```

按项目既有规则运行架构治理门。若 `test_11_backend_working_tree_is_unmodified` 仍因未提交的 `backend/app/config.py` 修改而失败，准确报告，不得弱化治理门。

## 最终报告

```text
Phase 8 Step 4A Review Fix Report

1. 检查的启动路径
2. production_binding_verified 语义
3. APP_ENV 环境契约
4. 是否存在绕过校验的入口
5. 修改文件
6. 测试结果
7. 治理门结果
8. 未解决限制
9. Git 状态与 HEAD

Production Deployment = BLOCKED
Review Fix 完成后 STOP，不进入 Phase 8 Step 5。
```

