# Phase 7 Step 6 — Agent 真实 LLM 与 WMS 只读业务闭环

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

让 Agent 不只是通过 FakeLLM 测试，而是能够调用现有 LLM Provider，使用现有 WMS 只读业务能力完成真实查询。

### 1. 先快速确认现状

检查：
- 原始 Roadmap 中 Phase 7 后续安排
- 当前 `AgentCommandService` 与 Agent Runtime 装配
- `LLMProvider` / `DeepSeekProvider`
- 当前 Tool Registry 及已注册的 WMS 只读工具
- Project Context、Tool Authorization 和现有配置

只阅读与本功能有关的代码，不重新审计整个项目。

### 2. 打通真实执行链

优先复用已有能力：

```text
HTTP Agent Command API
        ↓
Agent Runtime
        ↓
现有 LLM Provider
        ↓
Agent Decision
        ↓
现有 WMS 只读 Tool
        ↓
Tool Authorization
        ↓
业务查询结果
        ↓
Agent Run / Step 持久化
        ↓
Runtime Read/List API
```

要求：

- 使用项目现有 DeepSeek 配置，不硬编码 API Key。
- 使用当前已注册的真实只读业务 Tool，不重复造轮子。
- 如果 Tool 尚未接入 Agent Runtime，完成最小必要集成。
- 保持项目作用域与工具授权约束。
- 不允许 Agent 通过任意 SQL 或未经授权的 Tool 访问数据。
- 不修改现有 Runtime 状态机来迁就测试。

### 3. 真实调用策略

增加一个**手动触发、默认不执行**的真实 LLM Smoke Test 或脚本。

执行时使用项目现有配置，发送一个简单的 WMS 只读查询，并验证：
- LLM 能返回有效决策。
- Tool 能被正确选择和授权。
- 查询结果能进入 Agent 执行结果。
- Run 和 Step 可以通过现有 Read/List API 查到。
- 没有重复执行已完成的 Tool。

默认 `pytest` 不调用真实 LLM。

如果当前没有适合 Agent 使用的真实 WMS 只读 Tool，先实现最小必要的只读业务接入；必须复用真实 Schema 和数据库访问层，不得虚构表、字段或业务结果。

### 4. 安全边界

- 只读查询，不执行 INSERT、UPDATE、DELETE、DDL。
- 不向生产数据库写入测试数据。
- 不输出 API Key、数据库连接串或敏感配置。
- 不关闭认证、项目隔离或 Tool Authorization。
- 不新增无实际用途的框架、配置系统或治理流程。

### 5. 验证

先运行本次改动的针对性测试，再运行必要的相关回归测试。

只有修改共享 Runtime 核心逻辑时，才扩大测试范围。不重复执行无关的全量测试和治理门禁。

### 6. 完成后

简要汇报：
1. 实际接通的真实业务链路。
2. 真实 LLM Smoke 的执行结果；如果没有执行，明确说明。
3. 修改的关键文件。
4. 针对性测试结果。
5. 剩余的实际业务接入阻塞。

不要提交 Git，不推送，不创建 PR，不部署。完成后停止。

**现在直接开始实现，不要停在分析报告。**