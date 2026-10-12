执行 **Phase 6 Documentation Governance — Pre-Step-3 Audit**。

### 目标

在进入 Phase 6 Step 3 Runtime 前，处理提交报告中的 R10：确认并提交必要的架构决策文档，保证代码提交与正式架构依据处于同一版本控制历史。

### 一、只读审计

检查：

* `git status --short`
* `git log --oneline -5`
* `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Discovery.md`
* `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md`
* `docs/decisions/Phase/Phase 6.0 — Discovery + Boundary Freeze.md`
* 两份 Phase 5 尾期文档
* `72808d1` 与 `d9867ac` 的提交内容

将未跟踪文档分为：

1. Phase 6 正式架构决策文档
2. Phase 6 正式边界冻结文档
3. Phase 5 历史审计文档
4. 临时任务书或重复副本

检查正式文档是否与已提交实现、Discovery 和已批准 ADR 一致。发现冲突时先报告，不修改代码，也不擅自改写已批准决策。

### 二、提交规则

* 只提交确认属于正式交付的文档。
* 临时任务书副本不提交。
* Phase 5 文档只有在确认属于应纳入版本控制的正式交付时才提交。
* 不修改任何代码。
* 不 push，不 merge，不 rebase，不进入 Step 3。
* 提交前检查 staged 文件清单、`git diff --cached --check` 和完整 staged diff。

### 三、提交后验证

* 输出新增文档的 commit ID。
* 确认正式 ADR 与 Discovery 已进入版本控制。
* 确认 Step 1/2 的两个 commit 没有被改写。
* 确认没有代码改动。
* 明确列出仍未跟踪的文件及其原因。

### 四、最终报告

输出：

【Phase 6 Documentation Governance Report】

1. 文档分类
2. ADR 一致性检查
3. 实际提交文件
4. Commit ID
5. Git 工作区状态
6. 未提交文件及原因
7. Phase 6 Step 3 readiness
8. 未解决风险

完成后立即停止，不实现 Step 3 Runtime。
