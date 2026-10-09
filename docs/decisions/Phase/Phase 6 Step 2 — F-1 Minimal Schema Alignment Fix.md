你现在执行 **Phase 6 Step 2 — F-1 Minimal Schema Alignment Fix**。

## 一、目标

修复 PRE-COMMIT AUDIT 已确认的问题：

`workflow_node_run` 和 `workflow_approval` 缺少 `project_id`，违反已批准 ADR §16 I1/I4 与用户批准指令 §13。

只允许完成这项必要修复，不进入 Phase 6 Step 3。

## 二、开始前检查

先阅读并确认：

* `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Discovery.md`
* 三个 Workflow ORM Model
* 三个 Workflow Repository
* `tests/test_workflow_persistence.py`
* `tests/test_workflow_persistence_db.py`
* 当前数据库初始化方式及 `Base.metadata.create_all` 行为

保留现有工作区中的所有改动，不得覆盖、丢弃或重建 Step 1/Step 2 文件。

## 三、最小修改范围

1. 为 `workflow_node_run` 增加 `project_id`，并与 ADR 定义的类型、非空约束保持一致。
2. 为 `workflow_approval` 增加 `project_id`，并与 ADR 定义的类型、非空约束保持一致。
3. 创建子表记录时，必须从对应的 `workflow_run` 获得真实 `project_id`，不得信任调用方传入的任意项目标识。
4. 评估并确保子表的 `project_id` 与所属 `workflow_run.project_id` 不会不一致。优先复用现有结构；若仅靠 Repository 无法保障该不变量，先检查 ADR 是否允许增加数据库约束，不得擅自扩大设计。
5. 更新精确列清单、ORM/Repository 测试、数据库集成测试。
6. 现有本地数据库可能已经创建三张 Workflow 表。必须明确处理 `create_all()` 不会自动为已存在表补列的问题；按照项目现有 DDL 规范增加必要的幂等 schema DDL，不得删除或重建表，不得清空既有数据。
7. 保留原有主键、唯一约束、CAS、审批单赢家、FK CASCADE、只读边界和错误码语义。

## 四、禁止事项

* 不修改 Agent Runtime 或其六张表。
* 不修改 Workflow Domain，除非发现不可避免的接口冲突；如发现，先报告，不要顺手扩展。
* 不改变 Router、Orchestrator、Tool、API 等核心行为。
* 不增加 `workflow_checkpoint` 或其他表。
* 不连接生产数据库，不写入生产数据。
* 不 commit、push、stash、rebase。
* 不开始 Step 3 Runtime。

## 五、验证

依次执行：

1. Workflow 离线测试。
2. `RUN_DB_TESTS=1` 对应的 Workflow 数据库集成测试。
3. schema 初始化至少两次，验证幂等性及旧表补列。
4. 检查两张子表中每条记录的 `project_id` 均与父 `workflow_run` 一致。
5. 验证不同项目的数据读取不会因漏过滤而越界。
6. 复跑之前的治理门失败，明确区分 `test_11` 的工作树约束与下游级联。
7. 执行 `git diff --check`、`git diff --stat`、`git status --short`。
8. 确认 Workflow 表测试数据清理完成、无生产数据库操作。

不要因为旧的 Matrix Baseline 或 Knowledge Base 测试出现历史性、时序性失败，就直接归因于本次修改；需要给出复跑证据和分类。

## 六、最终报告

严格输出：

【Phase 6 Step 2 F-1 FIX REPORT】

1. 修改文件
2. 两张子表的 `project_id` 定义
3. 项目归属一致性如何保证
4. 旧数据库幂等补列方案
5. 离线测试结果
6. DB 集成测试结果
7. 治理门复跑结果
8. Agent / Domain / API 边界检查
9. DB 残留与生产数据库操作确认
10. Git 状态
11. 未解决风险
12. Commit readiness：READY / NOT READY

如果遇到 ADR 冲突、现有数据无法安全补列、迁移约束无法确认，立即停止并报告，不得自行改架构。

完成后立即停止，不进入 Phase 6 Step 3。
