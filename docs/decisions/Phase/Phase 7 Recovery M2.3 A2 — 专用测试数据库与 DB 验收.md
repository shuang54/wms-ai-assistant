# Phase 7 Recovery M2.3 A2 — 专用测试数据库与 DB 验收

## 当前状态

`A2 IMPLEMENTED — ACCEPTANCE BLOCKED ON DB ENVIRONMENT`

已确认：
- 专用 PostgreSQL 测试数据库不存在。
- Docker Desktop 当前不可用。
- `DATABASE_URL=""` 不会禁用 `.env` 中的数据库配置。
- 离线测试差分报告显示 A2 新增失败为 0，但 DB 级断言尚未验证。

## 一、目标

建立独立、可重复使用的 PostgreSQL 测试环境，并验证 A2 的数据库相关授权与只读行为。

## 二、允许修改

仅允许新增：

- `docker-compose.test.yml`
- 测试专用环境配置说明或脚本（确有必要时）
- DB 测试运行说明文档

如必须修改现有测试代码才能安全注入测试数据库 URL，先报告具体原因，不得擅自修改共享 Engine 或生产配置。

## 三、隔离要求

测试数据库必须满足：

- 独立服务，不复用应用库 PostgreSQL 容器。
- 数据库名称为 `wms_ai_test`。
- 独立命名卷，不挂载应用库的数据卷。
- 独立端口，例如宿主机 `5433`。
- 使用独立测试凭证，不复用应用数据库凭证。
- 不自动连接 `wms_ai`。
- 不修改原有 `docker-compose.yml`。
- 不执行 `docker compose down -v` 等可能删除其他项目数据的命令。
- 不修改 `.env` 中的应用数据库配置。
- 测试连接必须显式指向 `wms_ai_test`。

启动前先检查 Docker 是否恢复可用，并运行 Compose 配置校验。若 Docker 不可用，停止并报告，不得改用应用库。

## 四、测试 URL 注入

先阅读 `backend/app/db/session.py`、`tests/conftest.py` 和现有 DB fixture。

确认 `DATABASE_URL` 在 Settings、`.env` 和 Engine 初始化之间的实际优先级。

必须证明测试进程使用的是专用数据库，而不是仅仅将环境变量置空。

如果当前全局 Engine 在导入时就被创建，应先提出最小、可审查的测试注入方案；不得顺带重构生产数据库连接架构。

## 五、DB 验收

在专用测试数据库创建所需的测试 Schema 后，执行现有 DB-gated 测试。

至少验证：

1. A2-04/05 使用服务端 `PROJECT_ID` 过滤。
2. `project_id IS NULL` 的记录不可见。
3. 跨项目查询被拒绝。
4. Trace / Timeline 能够完成真实数据库读取。
5. 查询前后 Run、Attempt、Approval 等相关记录状态不变。
6. `tests/conftest.py` 的残留守卫仅作用于专用测试数据库。
7. 测试完成后检查 DB residue，并记录具体结果。

不得在应用库上运行这些测试。

不得将 Fake Store 测试当作 DB 集成证据。

## 六、测试环境失败处理

如果 Docker 镜像无法获取、初始化失败、迁移失败或测试数据库连接失败：

- 记录实际错误。
- 不切换到 `wms_ai`。
- 不跳过失败后宣称 PASS。
- 不修改冻结基线以适配结果。

## 七、Matrix Gate

本任务不执行 `git add`、`commit`、`push`、PR 或部署。

由于 Matrix Gate 的工作区干净性测试要求 `backend` 无未提交修改，而 A2 当前尚未提交，先保持 Gate 状态为 `BLOCKED`。

完成 DB 验收后，报告 Gate 所需的最小后续步骤；不要修改 Gate 测试或冻结常量。

## 八、最终报告

报告：

1. 新增文件
2. Docker / Compose 可用性
3. 测试数据库隔离证据
4. 测试连接实际指向的数据库名
5. DB-gated 测试 passed / skipped / failed
6. DB residue
7. A2-04/05 验收结果
8. Trace / Timeline 真实 DB 验收结果
9. 是否修改现有生产代码
10. Git 状态
11. Matrix Gate 状态及阻塞原因

完成后停止，不提交 Git，不进入下一阶段。