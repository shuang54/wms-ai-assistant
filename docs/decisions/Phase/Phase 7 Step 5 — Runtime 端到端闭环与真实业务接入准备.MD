# Phase 7 Step 5 — Runtime 端到端闭环与真实业务接入准备

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

基于现有 Agent Command API、Workflow Command API 和 Runtime Read/List API，继续原路线，完成 Agent 与 Workflow 从 HTTP 入口到 Runtime 执行、持久化和查询的端到端闭环。

不要再做 Git 提交任务、治理门专项任务或重复审计。

## 执行

1. 快速检查原始 Roadmap 中 Phase 7 的剩余步骤，确认下一项实际未完成的核心功能。
2. 检查 Agent Command API 的四个端点与 Workflow Command API 的交互契约，优先补齐真实集成缺口。
3. 重点验证并修复以下闭环：
   - 启动运行后，可以通过 Read/List API 查询到运行记录。
   - 等待审批时，运行状态与审批记录一致。
   - 批准或拒绝后，状态转换符合现有 Runtime 规则。
   - Resume 能恢复已有运行，不重复执行已经持久化的步骤或工具。
   - Cancel 保持协作式取消语义，不强杀 Tool。
   - 跨项目请求不能读取或操作其他项目的运行。
4. 复用现有 Runtime、Repository、Authorization 和 Command Service；不新建第二套执行引擎。
5. 只在原路线确实要求时才增加接口或能力。不要自行添加前端、生产数据接入或新一阶段架构。

## 测试

先运行受改动影响的针对性测试。仅当修改触及共享 Runtime 核心逻辑时，再运行相关回归测试。

本轮重点覆盖：
- Agent Command API + Agent Read/List API
- Workflow Command API + Workflow Read/List API
- Approval、Resume、Cancel 的状态一致性
- Project Scope 与幂等行为

不重复执行未受影响的治理门禁，不为追求覆盖率增加重复用例。

## B1/B2 注意事项

- `tests/test_rag_runtime_observability_e2e.py` 的 no-DB fixture 已补齐 Outcome Recorder 边界，保持其不写数据库的契约。
- `workflow_runtime/runtime.py` 的并发取消终态分类已修复，不要回退。
- 不要删除或清空其他测试数据。
- 如果再次发现 DB 残留，先确认归属，再按主键定向处理。

## 交付

完成一个端到端功能块后，简要报告：
1. 实际实现的功能
2. 关键修改文件
3. 针对性测试结果
4. 仍存在的真实阻塞

不提交 Git、不推送、不创建 PR、不部署。完成后停止，等待下一步。