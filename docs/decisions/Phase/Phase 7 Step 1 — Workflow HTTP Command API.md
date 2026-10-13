# Phase 7 Step 1 — Workflow HTTP Command API

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

在现有 Phase 6 Workflow Runtime 基础上，增加 Workflow 的 HTTP 命令接口，让调用方可以通过 API 操作工作流。

本阶段只实现命令接口，不扩展 Agent 能力，不重新设计 Workflow Runtime。

目标流程：

```text
HTTP Request
    ↓
Authentication
    ↓
Project Scope Validation
    ↓
Request Validation
    ↓
Workflow Command Service
    ↓
Existing Workflow Runtime
    ↓
Persisted State
    ↓
HTTP Response
```

## 二、先检查现有代码

先阅读实际实现，不要假设接口：

```text
backend/app/api/
backend/app/services/
backend/app/db/models/
backend/app/db/
tests/
```

重点找到：

- Phase 6 Workflow Runtime
- Workflow Run / Node / Approval 持久化模型
- Workflow Read API
- Authentication 与 project_id 隔离机制
- 现有 Agent 命令接口
- Workflow 状态机、事务及错误处理
- 可复用的 API 测试 fixture

同时查看现有架构文档及 Phase 6 已接受的 Workflow Architecture Decision。

**优先复用现有 Service、Repository、DTO 和测试基础设施。不要重复造轮子。**

## 三、实现范围

根据现有架构，支持以下命令：

1. **Start Workflow**：启动工作流。
2. **Advance Workflow**：推进工作流到下一步。
3. **Cancel Workflow**：取消正在运行的工作流。
4. **Approve / Reject**：处理需要人工审批的节点。

以上命令必须遵守当前已有的 Workflow 状态机及业务规则。

如果现有 Runtime 不支持某项操作，不要通过直接修改数据库状态来模拟成功；先确认缺口，并采用符合现有架构的最小实现。

## 四、接口设计要求

遵循项目现有 API 路由、DTO、错误码和响应格式。

要求：

- 所有操作经过现有 Authentication。
- 所有资源操作验证 `project_id` 权限。
- 不允许跨项目操作 Workflow。
- 不允许重复审批、重复取消或非法状态转换。
- 参数错误返回明确的客户端错误。
- 未授权访问按现有安全策略处理。
- 并发操作不得导致工作流状态不一致。
- 不在响应中暴露凭据、数据库连接或内部 Session。
- 不绕过已有的持久化和事务边界。

不要为了本阶段引入新的认证机制或权限框架。

## 五、测试要求

优先复用现有测试 fixture。

至少覆盖：

- 正常启动工作流。
- 正常推进工作流。
- 正常取消工作流。
- 正常审批与拒绝。
- 非法状态转换。
- 重复命令。
- 缺失或无效参数。
- 未认证请求。
- 跨项目访问。
- 工作流状态正确持久化。
- 失败操作不会留下错误的部分状态。

单元测试使用现有 Fake/Stub；涉及持久化的场景按项目已有 DB 测试机制执行。

默认测试不得调用真实 LLM。

## 六、严格禁止

本阶段不要：

- 修改 Phase 6 已有的核心状态机，除非发现明确的必要缺陷。
- 重构 Agent Runtime。
- 引入 LangGraph 或 MCP。
- 增加新的数据库表，除非现有架构确实无法满足需求，并先报告原因。
- 修改无关的 Text-to-SQL、RAG 或 Tool Framework。
- 接入真实生产系统。
- 部署生产环境。
- 为了通过测试而关闭安全检查。

## 七、执行顺序

```text
阅读现有实现
    ↓
确认 API 设计与复用点
    ↓
实现最小命令接口
    ↓
补充针对性测试
    ↓
运行相关测试
    ↓
运行全量非 DB 测试
    ↓
按现有机制运行 DB 测试
    ↓
提交本阶段代码
```

使用项目现有的测试和 Git 工作流，不额外建立复杂的 PR 或审计流程。

## 八、完成后报告

只需汇报：

1. 新增了哪些 API。
2. 修改和新增的文件。
3. 状态机及项目隔离如何保证。
4. 新增测试及运行结果。
5. 全量测试结果。
6. 是否修改了数据库结构。
7. 当前尚未实现的功能。

完成后停止，等待下一步指令。

**现在开始 Phase 7 Step 1，不要继续扩展到其他阶段。**