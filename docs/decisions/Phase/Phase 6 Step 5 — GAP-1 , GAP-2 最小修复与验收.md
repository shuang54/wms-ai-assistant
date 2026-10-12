# Phase 6 Step 5 — GAP-1 / GAP-2 最小修复与验收

项目：`D:\coding\ai\wms-ai-assistant`

## 一、执行授权

已批准：

* GAP-1：采用选项 A，为 `workflow_run` 增加可空 `input` 持久化字段。
* GAP-2：新增三个 Store Adapter，适配真实 Workflow Repository 与既有 Ports。
* 在修复后重新执行 S5 离线、DB-gated 和并发集成测试。

本次仅授权完成 S5，不进入 S6。

## 二、GAP-1：补齐 input 持久化

按现有项目真实结构，最小修改以下位置：

1. `backend/app/db/models/workflow_run.py`

   * 增加 nullable TEXT 列 `input`。
   * 不设置数据库长度限制。
   * 历史记录保持 NULL，不回填。
   * 不增加不必要的默认值。

2. `backend/app/db/workflow_run_repository.py`

   * `create()` 增加 `input: str | None = None` 参数。
   * 写入模型列并保证读取可返回。
   * 严格检查现有调用方，不破坏原有调用兼容性。
   * Repository 负责校验输入 JSON 文本的安全边界：合法 JSON、深度不超过 8、序列化后不超过 32 KiB。
   * 复用 Domain 的规范化规则；不要建立重复且可能分叉的校验逻辑。
   * 不增加 `db → services` 依赖。

3. `backend/app/db/init_db.py`

   * 遵循项目现有 schema 演进约定，增加幂等的 nullable TEXT 列补齐逻辑。
   * 参考现有 `ensure_*_column` 实现。
   * 确认操作针对 `ai_ops.workflow_run`，不误改其他同名表。
   * 历史行应自然读取为 NULL。

4. `tests/test_workflow_persistence.py`

   * 更新列清单断言。
   * 增加 input 写入、读取、NULL 兼容、非法 JSON、超深度和超大小测试。
   * 不削弱现有断言。

重要：Repository 接收到 JSON 文本时，不能只检查字符串长度；必须解析并验证结构。不得将无效内容写入数据库。

## 三、GAP-2：新增 Store Adapters

新增：

`backend/app/workflow_runtime/store_adapters.py`

如现有命名约定明确要求其他文件名，可遵循既有约定。

实现三个显式 Adapter：

* WorkflowRunStore Adapter
* WorkflowNodeRunStore Adapter
* WorkflowApprovalStore Adapter

要求：

1. 严格实现 S1 Ports 的真实方法签名。
2. ORM 行必须显式映射为 `RunRecord`、`NodeRunRecord`、`ApprovalRecord`。
3. 禁止把 ORM 对象直接透传给 Runtime。
4. 禁止使用 `dict(orm_row)` 或未经筛选的 `__dict__` 透传。
5. 不暴露 Session、Connection 或其他 ORM 内部对象。
6. 保持所有 `project_id` 一致性规则。
7. 正确映射现有 Repository 的异常类型到 Ports 异常。
8. 已知异常按既有映射表映射；未知异常映射为安全的 `WorkflowPortsError`，不得丢失必要的异常链。
9. 不记录凭证、数据库 URL、敏感参数或完整异常正文。
10. 不修改 Ports 的既有公共签名。

Factory 应使用这些 Adapter 组装真实 Repository。注入 Fake Store 时仍应保持原有测试能力。

## 四、Factory 与 Runtime 回归

保留现有 `create_workflow_runtime(...)` 的职责和调用方式。

确认：

* 默认 Store 通过 Adapter 连接真实 Repository。
* 不在 Factory 中创建全局请求级 Session。
* 不重复创建 Engine。
* 不隐式调用真实 LLM。
* 不隐式执行 Tool。
* 不硬编码项目 ID 或 Workflow 名称。
* Registry 的 current / exact-version 解析规则不变。
* 不修改 Agent Runtime、Tool Framework、AI Orchestrator 核心逻辑。

## 五、测试要求

### A. 离线测试

新增或扩展测试，覆盖：

* 三种 Adapter 的字段映射。
* 每类 Repository 异常映射。
* 未知异常安全映射。
* input 校验与持久化边界。
* Factory 默认装配与 Fake 注入。
* project_id 错配时外部调用次数为零。
* 不泄漏 ORM、Session 或敏感字段。

运行现有 S5 Adapter / Factory 测试及 Workflow Runtime 全系回归。

### B. DB-gated 测试

确认连接的是专用测试数据库，才运行 DB 测试。

先验证 `workflow_run.input` 列确实存在且类型、可空属性正确。

然后重跑原先失败的五项：

* `test_start_persists_run_readable_from_repository`
* `test_approval_flow_with_real_repository`
* `test_cas_race_single_winner`
* `test_attempt_create_race_single_winner`
* `test_runtime_concurrent_advance_executes_exactly_once`

并执行完整的 `tests/test_workflow_runtime_factory_db.py`。

同时验证：

* CAS 竞争只有一个成功者。
* 相同 `(run,node,attempt)` 的并发创建只有一个成功者。
* 并发 advance 不重复调用外部执行器。
* Approval 恢复不会重复推进。
* 取消和终态不互相覆盖。
* project_id 一致。
* 测试数据清理后各 Workflow 表 residue 为零。

若数据库环境不可用，明确报告 skipped / blocked；不得把未执行报告成 PASS。

### C. 完整回归

运行：

```powershell
python -m pytest -q tests/test_workflow_persistence.py
python -m pytest -q tests/test_workflow_runtime_adapters.py tests/test_workflow_runtime_factory.py
python -m pytest -q tests/test_workflow_runtime_runtime.py tests/test_workflow_runtime_execution.py tests/test_workflow_runtime_binding.py tests/test_workflow_runtime_ports.py tests/test_workflow_domain_boundary.py
python -m compileall -q backend tests scripts
```

按项目现有方式运行 DB-gated 测试。记录准确的 passed / skipped / failed 数量。

## 六、停止条件

遇到以下任一情况，立即停止并报告，不要继续扩大范围：

* Domain 与 Repository 的 JSON-safe 契约无法统一。
* 现有 Repository 的事务或 CAS 行为出现新问题。
* Adapter 无法无损映射既有 Port。
* 并发 advance 仍可能重复执行外部能力。
* 取消、审批恢复或终态存在竞争漏洞。
* 需要修改 AI Orchestrator、Agent Runtime 或 Tool Framework 才能继续。

## 七、最终报告

严格报告：

1. GAP-1 修复文件
2. GAP-2 Adapter 文件
3. Factory 接线变化
4. input 校验与历史数据兼容
5. 离线测试结果
6. DB-gated 测试结果
7. CAS / Attempt / advance 并发验收
8. Approval / Cancel / terminal 验收
9. DB residue
10. compile / lint / boundary
11. API 是否修改
12. Git 状态
13. 未验证项和限制

本轮完成 S5 后立即停止，不进入 S6。
