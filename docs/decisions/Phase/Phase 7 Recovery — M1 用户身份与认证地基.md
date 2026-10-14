# Phase 7 Recovery — M1 用户身份与认证地基

项目：`D:\coding\ai\wms-ai-assistant`

## 一、目标

基于刚完成的 Roadmap Recovery Audit，开始补齐原计划中已明确推迟的用户身份与认证能力。

本阶段只做：

- 用户身份模型
- 密码安全存储
- 登录与登出
- 服务端会话或令牌管理
- 用户身份与 Conversation / AgentRun 的资源归属设计
- 现有 API 认证体系的兼容性分析

**不开发前端，不部署，不提交 Git，不进入 RBAC 完整实现。**

## 二、开始前必须读取

阅读真实代码：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/security.md
backend/app/config.py
backend/app/main.py
backend/app/api/
backend/app/db/models/
backend/app/db/
backend/app/services/
backend/app/agent/
backend/app/workflow/
tests/
```

同时核对现有：

- 5 个域的 X-API-Key 认证
- 16 个未认证端点
- Agent Run / Conversation / Message / ToolResult 数据模型
- 当前数据库 migration / schema 管理方式
- 项目作用域和资源归属校验
- Phase 5 架构冻结约束
- Phase 6 Service API 边界

先理解现有实现，不得直接创建重复的 User、Session 或 Auth 模块。

## 三、用户身份设计

先设计最小 User 模型，至少考虑：

```text
id
username
password_hash
display_name
status
created_at
updated_at
```

要求：

- 使用不可预测的用户 ID。
- 用户名具有唯一性约束。
- 密码只能保存经过安全哈希处理的结果，禁止明文存储。
- 不提供公开注册接口，第一版由受控管理员或初始化流程创建用户。
- 禁止把数据库账号、业务库账号、LLM API Key 当成用户登录凭据。
- 不在日志或异常中输出密码、密码哈希、令牌或 API Key。

如果项目已有用户模型设计，应先复用或报告冲突，不要盲目新增。

## 四、认证与会话

先比较当前项目适用的两种方式：

1. 短期 JWT Access Token。
2. 服务端管理的 Session Cookie。

以公司内网 Web 应用为目标，优先评估 **HttpOnly、Secure、SameSite Cookie + 服务端会话管理**，因为浏览器不需要直接接触后端 API Key。

如果最终选择 JWT，必须说明令牌撤销、过期、刷新和存储策略。

最小认证接口建议：

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

要求：

- 未认证请求默认拒绝。
- 登录失败使用统一错误响应，不泄露用户名是否存在。
- 限制登录尝试的设计必须有明确的实现与验收方式；不要仅在文档中声称已实现。
- Session/Token 不得写入 URL 或普通日志。
- Cookie 模式必须评估 CSRF 防护。
- 认证接口必须有单元测试。
- 不能把浏览器传入的 user_id 当成可信身份。

## 五、资源归属设计

审查并提出最小方案，让已认证用户能够成为以下资源的可信归属主体：

```text
Conversation
Message
AgentRun
AgentStep
AgentDecision
AgentToolResult
WorkflowRun
Audit Event
```

注意：

- 不要假设所有资源都需要直接增加 user_id；根据现有外键和数据关系选择最小方案。
- 异步 Agent / Workflow 执行必须可靠传递已验证的用户上下文。
- 读取资源前必须在服务端校验资源归属。
- 不允许通过修改 URL 中的 run_id / conversation_id 读取其他用户的数据。
- 现有项目作用域必须继续有效。
- 不得破坏 Phase 5 冻结能力或 Phase 6 Service API 契约。

如果资源归属需要新增数据库字段或 migration，先说明兼容性、历史数据回填策略和 rollback 风险，再实施最小变更。

## 六、兼容现有 API Key

现有 Agent / Workflow API Key 认证不能突然失效。

必须区分：

```text
Human User Authentication
Service-to-Service API Key
Project Scope
Resource Ownership
```

它们不是同一种凭据，也不能相互替代。

本阶段先制定并测试兼容方案：

- 现有服务调用方的 API Key 如何继续工作。
- 浏览器用户如何通过会话访问后端。
- 服务端如何确定当前 user_id 和 project_id。
- 客户端不能自行伪造项目或用户身份。
- 不能因为新增用户认证而让未认证端点继续匿名访问敏感数据。

不要在没有完整覆盖测试前一次性修改所有 35 个端点。

## 七、数据库与迁移

- 先确认当前 migration 机制。
- 禁止手动修改生产数据库。
- 不连接真实 WMS/ERP 业务数据库进行写入。
- 任何新表或字段必须通过项目正式的 schema 变更机制。
- 不允许通过自动建表掩盖 migration 缺失。
- 必须有测试数据库验证和迁移回滚说明。
- 不要修改真实库存数据和现有 Text-to-SQL Evaluation Baseline。

## 八、测试要求

优先复用已有测试设施，覆盖：

```text
用户创建与唯一性
密码哈希与验证
错误密码
停用用户
登录成功 / 失败
登出后会话失效
会话过期
未认证访问
伪造 user_id
跨用户 Conversation 访问
跨用户 AgentRun 访问
项目作用域不回归
旧 API Key 调用兼容性
敏感信息不进入日志
```

不得让默认 pytest 调用真实 LLM。

不重复运行无关全量测试；先跑新增测试和直接相关的认证、Agent、Conversation 回归，再按现有治理门禁决定是否运行更广范围测试。

## 九、范围外内容

本阶段暂不实现：

```text
完整 RBAC 管理界面
组织 / 租户管理
多应用注册与切换
前端 Web UI
模型管理页面
知识库管理页面
工具管理页面
HTTPS / Docker 部署
```

这些必须保留在后续路线中，不能因为 M1 完成就宣称整个企业级平台完成。

## 十、完成报告

严格汇报：

1. 现有认证架构
2. 用户模型与会话方案
3. 新增/修改文件
4. 数据库 schema 变化
5. 用户与资源归属策略
6. API Key 兼容方案
7. 测试结果
8. 安全风险与剩余缺口
9. Git 工作区状态
10. 当前是否具备进入 Web MVP 的条件

如果发现现有架构冲突，先停在设计审查并报告，不要为了推进而修改冻结接口。

**本阶段结束后停止，不自动进入 RBAC、Web UI 或部署阶段。**