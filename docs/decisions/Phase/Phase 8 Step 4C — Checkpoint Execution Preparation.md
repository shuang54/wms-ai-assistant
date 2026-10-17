# Phase 8 Step 4C — Checkpoint Execution Preparation

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

依据 Step 4B Review Report，准备两个独立 checkpoint。

**本阶段先完成提交前核对；未经用户明确确认，不执行 `git add`、`git commit` 或 `git push`。**

## 1. 再次核对当前工作区

执行：

```powershell
git status --short --branch
git diff --check
git diff --stat
git diff --cached --stat
```

确认分支仍为 `phase7`、HEAD 仍为 `b004eff`，并重新检查全部 4 个 tracked 修改与 7 个 untracked 文件。

如果工作区与 Step 4B 报告不同，停止并报告差异，不要沿用旧清单。

## 2. 核对 C1 文件

建议 C1 清单：

```text
backend/app/config.py
docker-compose.yml
docker-compose.test.yml
deploy/README.md
tests/test_deployment_hardening.py
tests/test_production_readiness.py
docs/decisions/Phase/Phase 8 Step 3 — Production Deployment Readiness.md
```

逐一检查完整文件内容及差异，特别确认：

- 不包含真实凭证、私钥或 `.env`；
- 没有无关业务逻辑修改；
- Readiness 文档与实际实现一致；
- 测试没有跳过或弱化治理门；
- 不把配置校验描述成实际 socket 或生产网络隔离证明。

运行：

```powershell
python -m pytest -q tests/test_deployment_hardening.py tests/test_production_readiness.py tests/test_deployment_config.py
python -m pytest -q tests/test_gateway_validation.py
python -m pytest -q tests/test_workflow_ui_security.py tests/test_auth_api.py
python -m compileall -q backend tests scripts
git diff --check
```

Gateway 测试必须按项目现有门控执行。准确记录实际运行的命令与结果。

## 3. 核对 C2 文件

建议 C2 清单：

```text
docs/decisions/Phase/Phase 8 Step 4 — P0 Deployment Configuration Hardening.md
docs/decisions/Phase/Phase 8 Step 4A — Production Environment Fail-Closed Guard.md
docs/decisions/Phase/Phase 8 Step 4A Review Fix — Production Guard Boundary Audit.md
docs/decisions/Phase/Phase 8 Step 4B — Deployment Hardening Checkpoint Review.md
```

检查阶段名称、测试数量、路径、Gate 状态和限制说明是否与实际报告一致。不得把未执行的验证写成 PASS。

## 4. 检查提交依赖关系

重点确认 C1 与 C2 是否各自自洽：

- C1 应包含实现、相关测试和与实现一致的 Readiness 文档。
- C2 仅包含阶段任务书与审查记录。
- 不得遗漏 Step 3 文档对 Step 4 实现的必要引用。
- 不得把与 Phase 8 无关的文件带入提交。

## 5. 处理 Governance Gate

运行：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_audit.py tests/test_tool_observability_architecture_audit.py tests/test_conversation_api_architecture_audit.py tests/test_conversation_architecture_contract.py
```

如果仍然只有 `test_11_backend_working_tree_is_unmodified` 因 `backend/app/config.py` 未提交而失败，准确记录。不得修改、跳过或弱化该门，也不要通过提交来伪装此前运行已通过。

## 6. 停止条件

完成上述核对后，只输出：

```text
【Phase 8 Step 4C — Checkpoint Preparation Report】

1. 当前 HEAD / branch
2. 工作区变化
3. C1 文件清单及审查结果
4. C2 文件清单及审查结果
5. 测试与 Governance Gate 结果
6. Secret / scope 检查
7. 建议提交顺序
8. 未解决问题
9. Production Deployment 状态
10. Git 操作状态

未得到用户确认前：
git add = NOT EXECUTED
git commit = NOT EXECUTED
git push = NOT EXECUTED

STOP，等待用户确认。
```

不得进入 Phase 8 Step 5，不得重建 PostgreSQL 容器，不得执行生产部署。