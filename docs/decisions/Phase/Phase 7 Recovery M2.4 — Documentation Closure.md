# Phase 7 Recovery M2.4 — Documentation Closure

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

完成 M2.4 的数据库文档闭环，核实并处理已提交的空文档问题。

当前提交：

`5edcab345bc576747c6527c778d096ecfc10880b`

本阶段只做文档维护与审计，不进入 M2.5。

## 二、开始前检查

执行：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git diff --check
```

确认当前 HEAD、分支与现有工作区状态。

必须保留所有已有未提交文件，不得 reset、clean、stash 或覆盖。

## 三、更新数据库文档

先阅读：

```text
AGENTS.md
docs/database.md
backend/app/db/models/authorization_grant.py
backend/app/db/authorization_grant_repository.py
backend/app/db/init_db.py
```

根据真实模型，为 `docs/database.md` 增加 `ai_ops.authorization_grant` 的说明，至少包括：

- 表用途与授权范围。
- 字段名称、类型、可空性及含义。
- `scope_type` 与 `role` 的合法组合。
- CHECK 约束的实际名称和规则。
- 主键及两个 partial unique index、用户索引。
- 重复授权时的幂等行为。
- 表由 `python -m backend.app.db.init_db` 创建。
- `init_db()` 不会自动随应用启动执行，因此部署时必须先完成数据库初始化。
- 当前没有 Alembic 或版本化迁移机制，以及这一设计的限制。

所有描述必须与真实模型、Repository 和初始化代码一致，不要凭任务书猜字段。

不得把真实生产连接字符串、凭证或用户信息写入文档。

## 四、检查空审查记录

检查：

```text
docs/decisions/Phase/Phase 7 Recovery M2.4 — Pre-Commit Security Review.md
```

确认文件是否确实为 0 字节，并核对是否存在其他地方可恢复的正式审查内容。

不要伪造审查记录，也不要用推测内容填充。

本轮先报告处理建议；**未经进一步明确授权，不删除该文件**。

## 五、严格禁止

- 不修改生产代码或测试。
- 不修改 Schema、数据库数据或授权记录。
- 不执行 `init_db()`。
- 不运行生产数据库命令。
- 不修改 Matrix Gate、冻结基线或断言。
- 不修改 M2.4 提交。
- 不执行 git add、commit、push、amend。
- 不进入 M2.5。

## 六、验证

运行：

```powershell
git diff --check
git diff -- docs/database.md
git status --short
```

确认变更只涉及批准的文档范围。不要把已有的两个未跟踪任务文档误纳入修改。

## 七、最终报告

```text
【M2.4 Documentation Closure Report】

1. HEAD / branch
2. docs/database.md 修改内容
3. 与真实模型、约束和索引的一致性
4. init_db 部署要求是否清楚
5. 空审查记录文件状态
6. git diff --check
7. 修改文件清单
8. 是否修改生产代码、Schema 或数据库数据
9. 是否 commit / push2
10. 剩余问题

完成后立即 STOP，不进入 M2.5。
```

