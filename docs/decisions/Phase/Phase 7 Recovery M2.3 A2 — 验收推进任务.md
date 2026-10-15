# Phase 7 Recovery M2.3 A2 — 验收推进任务

当前状态：`IMPLEMENTED — PENDING ACCEPTANCE`

## 目标

在不修改 A2 生产逻辑、不修改冻结基线、不触碰应用数据库的前提下，推进剩余验收。

### Step 1：只读检查测试数据库环境

检查：

- `docker-compose.yml` / `compose.yaml` 及相关 override 文件
- 测试数据库专用环境变量和 fixture
- DB-gated 测试的连接方式
- `.env` 中的 `DATABASE_URL` 只检查配置来源和数据库名，严禁输出凭证

判断是否存在可证明隔离的专用 PostgreSQL 测试数据库。

禁止直接使用应用库 `wms_ai` 作为测试库。

### Step 2：若不存在专用测试数据库

停止 DB 测试，报告最小安全方案。不得自行创建数据库、启动新容器、执行迁移或写入数据。

### Step 3：若已存在专用测试数据库

先核实数据库身份和隔离边界，再运行 A2 相关 DB-gated 测试。

重点验证：

- A2-04/05 服务端 `PROJECT_ID` 过滤
- NULL `project_id` 记录不可见
- 跨项目查询拒绝
- Trace / Timeline 的真实 DB 读取
- 查询只读性与 DB residue

### Step 4：冻结 Matrix Gate

先阅读 `scripts/run_matrix_gate.py` 和 `test_11_backend_working_tree_is_unmodified` 的实际执行逻辑。

不要因为工作区未提交就修改该测试，也不要修改冻结常量。

如果 Gate 必须在干净的独立工作区执行，先报告实现要求；未经授权不得创建或修改 Git worktree。

### Step 5：核验 47 个失败

复核已有 pristine HEAD 基线差分报告，确认失败集合、测试标识和差分脚本是否可复现。

不得把基线失败自动认定为环境问题，必须保留具体证据。

### Step 6：停止并汇报

报告：

1. 专用测试数据库是否存在
2. 是否执行 DB 测试及具体结果
3. DB residue
4. Matrix Gate 结果或阻塞原因
5. 47 个失败的差分核验结果
6. 是否修改代码
7. Git 状态

本任务不授权 Git add / commit / push、数据库迁移、应用库访问或部署。

完成后停止，不进入下一阶段。