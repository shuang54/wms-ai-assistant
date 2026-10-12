# Phase 6 Step 2 — Pre-Commit Gate Audit

当前 Phase 6 Step 2 已完成，但尚未 commit。

本轮只做只读审计与验证，不修改代码，不进入 Step 3。

## 必须检查

1. 阅读 Step 2 新增的 8 个文件及 `backend/app/db/models/__init__.py` 的实际 diff。
2. 检查 `test_11_backend_working_tree_is_unmodified` 的断言、触发条件与治理目的，确认其失败是否确实由本次受控文件修改引起。不得假定 commit 后必然通过。
3. 分别复跑并记录全部失败测试的真实结果，区分：

   * 本次变更导致
   * 测试环境导致
   * 已有基线问题
   * 尚不能确定
4. 只读验证以下契约：

   * `workflow_run` CAS 与终态状态保护
   * Node attempt 终态不可逆与唯一约束
   * Approval 单赢、终态不可逆及时间字段一致性
   * FK CASCADE 行为
   * `output` JSON-safe 校验是否在持久化入口强制执行
   * 三张表的 project_id / workflow_version / request_id 语义
   * `create_all()` 对已有表结构的限制是否已在项目文档或测试说明中明确
5. 检查 Agent 六表、既有 schema、Workflow Domain 与其他模块均无非预期变更。
6. 运行 `git diff --check`、`git diff --stat`、`git status --short`。

## 严格禁止

* 不修改任何文件
* 不 commit / push / PR / merge / rebase
* 不执行生产数据库操作
* 不进入 Step 3
* 不把未验证的失败归因写成确定结论

## 输出

按以下格式报告：

【Phase 6 Step 2 PRE-COMMIT AUDIT】

1. 文件与 diff
2. 治理门失败根因
3. 全量测试失败分类
4. Persistence 契约审计
5. Schema / Agent 边界审计
6. DB 测试数据清理
7. Git 状态
8. Commit 建议：READY / NOT READY
9. 未解决风险

STOP。
