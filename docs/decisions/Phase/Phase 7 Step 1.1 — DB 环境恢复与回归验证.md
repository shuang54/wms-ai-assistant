# Phase 7 Step 1.1 — DB 环境恢复与回归验证

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

恢复现有 PostgreSQL 测试环境，验证 Phase 7 Step 1 的 Workflow HTTP Command API。

本步骤以恢复环境和验证为主。**不要立即修改业务代码来消除测试失败。**

## 1. 检查环境

检查：

- 当前分支与 HEAD。
- Docker Desktop 是否运行。
- 项目现有 PostgreSQL 容器及启动配置。
- 当前测试数据库连接配置是否正确。
- 是否存在尚未提交的代码修改。

复用项目已有的 Docker Compose 配置，不要新建数据库或修改数据库结构。

如果容器不存在，先报告，不要擅自重建或删除数据卷。

## 2. 恢复 PostgreSQL

如果已有测试数据库容器，只启动现有容器。

确认：

- PostgreSQL 已启动。
- `localhost:5432` 可以连接。
- 当前测试数据库名称和连接目标正确。
- 不连接生产数据库。
- 不执行 migration、DDL 或清库操作。

## 3. 验证新增 API

先执行：

```powershell
python -m pytest -q tests/test_workflow_command_api.py
```

再按项目现有机制执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_workflow_command_api_db.py
```

确认命令结束后，恢复或清理测试环境变量，避免影响后续测试。

## 4. 全量回归

运行：

```powershell
python -m pytest -q
```

如果项目已有 DB 分片执行脚本，复用该脚本执行完整 DB 测试套件，不要重新设计测试框架。

同时检查：

```powershell
python -m compileall backend tests
```

## 5. 失败处理

针对失败逐项分类：

- 环境问题
- 测试配置问题
- 新增 API 引入的回归
- 历史遗留问题

如果离线测试仍然出现 DB 依赖测试返回 `502 data source unavailable`，先确认这些测试为什么没有被正确跳过或隔离，再决定是否需要最小修复。

如果发现明确代码缺陷，先报告根因和最小修复方案，不要随意修改 Phase 6 核心状态机。

## 6. 完成报告

只汇报：

1. PostgreSQL 是否恢复。
2. 新增 API 测试结果。
3. DB 集成测试结果。
4. 全量离线测试结果。
5. 全量 DB 测试结果。
6. compileall 结果。
7. 是否修改代码或数据库结构。
8. 尚未解决的问题。

不创建 PR，不部署，不额外增加 GitHub 审计流程。

完成后停止，等待下一步指令。