执行 **Phase 6 Step 1 + Step 2 Commit Finalization**。

### 1. 先核对完整文件清单

检查 `git status --short`，识别并分类：

* Phase 6 Step 1：Workflow Domain 与 Domain 测试
* Phase 6 Step 2：三个 ORM Model、三个 Repository、数据库注册、`init_db.py`、Persistence 测试
* Phase 6 Discovery：已提交的 ADR / 架构文档
* 临时任务书副本或其他非交付文件

不得遗漏任何必要的 Domain、Persistence 或测试文件；不要将临时任务书副本、缓存、密钥、环境文件混入提交。

### 2. 提交前检查

* 阅读全部 staged diff。
* 确认没有 Agent Runtime、Router、Orchestrator、Tool、API 或其他无关模块的修改。
* 确认 F-1 的两张子表字段和幂等补列实现均已包含。
* 执行 `git diff --check` 和 staged diff 检查。
* 确认没有生产数据库操作。

### 3. 提交策略

优先保留清晰的提交边界：

1. 如果 Step 1 的 Domain 文件可以独立通过其测试并形成完整提交，先单独提交 Step 1。
2. 再将 Step 2 的 ORM、Repository、数据库注册、幂等补列 DDL 与 Persistence 测试作为一个独立提交。
3. 如果现有依赖关系或治理规则不允许分开提交，停止并报告具体原因，不要擅自改变提交边界。

不得修改代码来绕过 `test_11_backend_working_tree_is_unmodified`。该测试要求相关 tracked backend 文件与索引一致，必须通过正常提交实现这一状态。

### 4. 提交后验证

提交完成后：

* 检查 commit 内容、`git status --short` 和工作区是否干净。
* 复跑 Workflow Domain 离线测试。
* 复跑 Workflow Persistence 离线测试与 DB 集成测试。
* 复跑 `test_11_backend_working_tree_is_unmodified` 及其治理门下游测试。
* 按项目既有方式执行全量测试，单独分类历史基线问题和环境波动。
* 核对 Workflow 表残留为 0，Agent 六表无变化。

不得因为单个治理门恢复就宣称全量测试全部通过。不得隐瞒失败或将未验证的原因写成确定根因。

### 5. 最终报告

输出：

【Phase 6 Step 1 + Step 2 COMMIT REPORT】

1. Commit ID 与提交边界
2. 实际纳入的文件
3. 工作区状态
4. Workflow Domain 测试
5. Workflow Persistence 测试
6. DB 集成测试
7. 治理门复跑结果
8. 全量测试结果与遗留失败
9. DB 残留与 Agent 边界
10. 当前风险
11. Phase 6 Step 3 是否允许开始

**本次只完成提交和提交后验证，不进入 Step 3 Runtime，不 push，不开发新功能。**
