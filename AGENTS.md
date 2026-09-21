# AGENTS.md

# WMS AI Assistant — AI 开发规范

> 本文件是本项目的核心开发规范，供 CodeBuddy / AI Coding Agent 使用。
> 所有 AI 生成、修改、重构代码时，必须优先遵守本文件。
>
> 详细业务需求、架构设计、API、数据库设计和技术决策，请按需阅读 `docs/` 下对应文档。
>
> 当前项目处于从 0 到 1 的开发阶段，优先保证简单、可理解、可测试和可演进。

---

# 1. Project Overview

## 1.1 项目名称

WMS AI Assistant

## 1.2 项目目标

开发一个面向企业 WMS / ERP 场景的 AI 助手。

用户可以通过自然语言：

* 查询 WMS / ERP 数据
* 查询库存
* 查询采购订单
* 查询销售订单
* 查询入库 / 出库
* 查询生产相关数据
* 查询盘点任务
* 查询 WMS 操作规范
* 根据企业知识库回答业务问题

后续逐步支持：

* Tool Calling
* Agent
* LangGraph
* 多步骤业务流程
* 权限控制
* 审计
* AI 辅助业务操作
* 私有化部署
* 本地 / 私有 LLM

---

# 2. Current Development Stage

当前采用 MVP → V1 → V2 的渐进式开发方式。

## MVP

当前只实现：

1. Python 后端
2. FastAPI
3. LLM API
4. 基础聊天接口
5. 基础 RAG
6. 基础 Tool Calling
7. WMS API 集成
8. 基础测试

## V1

计划增加：

* Agent
* LangGraph
* 多 Tool 协作
* 用户认证
* RBAC
* 操作审计
* 更完善的 RAG

## V2

计划增加：

* 多 Agent
* 工作流
* AI 执行业务操作
* 用户确认机制
* MCP
* 私有化 LLM
* Docker / Kubernetes
* 企业内网部署

### 重要原则

不要为了未来功能提前实现复杂架构。

当前阶段没有明确需求时：

* 不主动实现 Agent
* 不主动引入 LangGraph
* 不主动引入 MCP
* 不主动实现 Multi-Agent
* 不主动增加复杂基础设施

---

# 3. Technology Stack

## Backend

* Python 3.12+
* FastAPI
* Pydantic
* Uvicorn

## AI

* LLM API
* Embedding
* RAG
* Tool Calling
* LangChain（仅在确实需要时使用）
* LangGraph（Agent 阶段使用）

## Database

* PostgreSQL
* pgvector

## Integration

* WMS API
* ERP API
* REST API

## Development

* Git
* Docker（部署阶段使用）
* pytest

---

# 4. Core Architecture

整体架构：

```text
User
  ↓
Frontend
  ↓
FastAPI
  ↓
AI Service
  ├── LLM
  ├── RAG
  └── Agent
        ↓
      Tools
        ↓
   WMS / ERP API
        ↓
   Business Service
        ↓
   PostgreSQL
```

## 4.1 AI 与业务系统之间的原则

AI 不允许直接操作企业数据库。

禁止：

```text
User
 ↓
LLM
 ↓
SQL
 ↓
PostgreSQL
```

推荐：

```text
User
 ↓
LLM
 ↓
Tool
 ↓
Permission
 ↓
Business Service / WMS API
 ↓
PostgreSQL
```

AI 应通过受控 Tool / API 获取企业业务数据。

---

# 5. Repository Structure

推荐基础结构：

```text
wms-ai-assistant/
│
├── AGENTS.md
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
│
├── docs/
│   ├── architecture.md
│   ├── requirements.md
│   ├── api.md
│   ├── database.md
│   ├── security.md
│   └── decisions/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   │
│   │   ├── api/
│   │   │
│   │   ├── core/
│   │   │
│   │   ├── llm/
│   │   │
│   │   ├── rag/
│   │   │
│   │   ├── tools/
│   │   │
│   │   ├── agents/
│   │   │
│   │   ├── services/
│   │   │
│   │   ├── models/
│   │   │
│   │   └── schemas/
│   │
│   └── tests/
│
├── frontend/
│
├── knowledge/
│
└── scripts/
```

### 重要

不要为了“看起来专业”创建大量空目录。

只有在功能真正出现时才增加对应模块。

---

# 6. AI Coding Workflow

AI Agent 修改代码前必须遵循：

```text
Understand
    ↓
Inspect
    ↓
Plan
    ↓
Implement
    ↓
Test
    ↓
Review
    ↓
Document
```

具体要求：

## Step 1 — Understand

先理解用户需求。

如果需求存在明显歧义：

* 不要自行猜测关键业务规则
* 先提出必要的问题

如果可以合理推断：

* 明确记录假设
* 再继续开发

## Step 2 — Inspect

修改代码前检查：

* 相关代码
* 项目结构
* 相关文档
* 已有 API
* 已有测试
* 相关技术决策

不要直接假设项目中不存在已有实现。

## Step 3 — Plan

中等以上复杂度任务，先输出：

```text
修改目标
修改文件
实现方式
风险
测试方式
```

确认方案后再实施。

## Step 4 — Implement

遵循最小修改原则。

只修改完成任务所需要的文件。

不要无关重构。

## Step 5 — Test

修改完成后：

* 运行相关测试
* 检查类型错误
* 检查 lint
* 检查 API 行为

## Step 6 — Review

检查：

* 是否破坏已有功能
* 是否存在安全问题
* 是否引入不必要依赖
* 是否违反项目架构
* 是否存在明显异常处理缺失

## Step 7 — Document

如果修改了：

* API
* 数据库
* 架构
* AI Tool
* 权限
* 重要技术决策

需要同步更新 `docs/`。

---

# 7. Code Style

## Python

使用：

* Python type hints
* async / await
* Pydantic
* 清晰的函数和类职责
* 小函数
* 明确的异常处理

示例：

```python
async def get_inventory(
    material_code: str,
    warehouse_code: str,
) -> InventoryResult:
    ...
```

不要使用大量无类型的：

```python
def xxx(data):
    ...
```

---

# 8. Layer Responsibilities

不同层必须保持职责清晰。

## API Layer

负责：

* HTTP 请求
* 参数验证
* Authentication
* 调用 Service
* 返回 Response

不要在 API 路由中写大量业务逻辑。

---

## Service Layer

负责：

* 业务逻辑
* 数据处理
* 调用 Repository / 外部 API

---

## LLM Layer

负责：

* LLM Client
* Prompt
* Model configuration
* LLM response parsing

不要让 LLM Layer 直接操作数据库。

---

## RAG Layer

负责：

* 文档加载
* 文档切分
* Embedding
* Vector Search
* Retrieval
* Context 构建

---

## Tool Layer

负责：

* AI 可调用的工具
* Tool 参数定义
* Tool 输入验证
* 调用业务 Service / 外部 API
* 返回结构化结果

Tool 不应该直接承担复杂业务逻辑。

---

## Agent Layer

负责：

* Agent
* Tool selection
* Agent state
* Agent workflow

只有在真正开始 Agent 开发后才创建复杂 Agent 结构。

---

# 9. LLM Rules

## 9.1 API Key

禁止：

```python
API_KEY = "sk-xxxx"
```

必须使用环境变量：

```text
.env
```

并通过配置模块读取。

`.env` 禁止提交 Git。

---

## 9.2 Model Configuration

模型名称、API 地址、Temperature 等配置不要硬编码。

应该统一通过配置管理。

例如：

```text
LLM_PROVIDER
LLM_MODEL
LLM_API_KEY
LLM_BASE_URL
```

---

# 10. Prompt Rules

Prompt 应与 Python 业务代码分离。

不要把超长 Prompt 直接散落在业务代码中。

Prompt 应该：

* 清晰
* 可维护
* 可测试
* 尽量模块化

不要在没有必要时创建大量 Prompt 文件。

---

# 11. RAG Rules

RAG 的基本流程：

```text
Document
 ↓
Parse
 ↓
Chunk
 ↓
Embedding
 ↓
Vector Database
 ↓
Retrieve
 ↓
Context
 ↓
LLM
```

要求：

1. 不要把整个文档一次性发送给 LLM
2. 优先检索相关内容
3. 保留文档来源信息
4. 回答尽量能够追溯来源
5. 检索不到可靠资料时，不要让 AI 编造答案

RAG 第一阶段优先使用：

```text
PostgreSQL + pgvector
```

除非存在明确需求，否则不要过早引入新的向量数据库。

---

# 12. Tool Calling Rules

AI Tool 必须：

* 有明确名称
* 有明确描述
* 有明确参数
* 有输入验证
* 返回结构化数据
* 处理异常
* 可以记录调用日志

例如：

```text
get_inventory
get_purchase_order
get_sales_order
get_inbound_order
get_outbound_order
```

Tool 描述必须让 LLM 能够理解：

```text
用途
参数
参数含义
返回内容
限制
```

---

# 13. Enterprise API Rules

AI 不应该绕过企业业务 API。

优先：

```text
AI
 ↓
Tool
 ↓
WMS API
 ↓
WMS Business Logic
 ↓
Database
```

而不是：

```text
AI
 ↓
Database
```

如果现有 WMS 已经存在 API：

**优先复用现有 API。**

不要为了 AI 重新实现已有业务逻辑。

---

# 14. Read vs Write Operations

业务操作分为：

## Read

例如：

* 查询库存
* 查询订单
* 查询库位
* 查询盘点任务

可以在权限允许的情况下自动执行。

## Write

例如：

* 创建调拨
* 创建盘点任务
* 创建入库单
* 修改业务数据
* 提交审批

必须更加严格。

默认采用：

```text
AI
 ↓
生成操作计划
 ↓
展示操作内容
 ↓
用户确认
 ↓
权限校验
 ↓
执行
```

未经明确授权，不允许 AI 自动执行高风险写操作。

---

# 15. Security

必须遵循：

## 15.1 Least Privilege

AI 只拥有完成任务所需要的权限。

## 15.2 No Direct Database Access

LLM 不允许直接访问数据库。

## 15.3 Sensitive Data

敏感数据需要：

* 权限控制
* 日志
* 必要时脱敏

## 15.4 Secrets

禁止提交：

* API Key
* Password
* Token
* Database Password
* 私钥

---

# 16. Error Handling

不要吞掉异常。

禁止：

```python
try:
    ...
except Exception:
    pass
```

应该：

* 记录错误
* 返回可理解的错误
* 不向用户暴露敏感内部信息

AI Tool 发生异常时，应返回结构化错误信息。

例如：

```json
{
  "success": false,
  "error_code": "INVENTORY_QUERY_FAILED",
  "message": "库存查询失败"
}
```

---

# 17. Logging

关键操作需要日志。

至少记录：

* request_id
* user_id
* tool_name
* execution_time
* success / failure
* error_code

不要记录：

* API Key
* Password
* Token
* 不必要的敏感业务数据

---

# 18. Testing

测试至少分为：

```text
Unit Test
Integration Test
API Test
AI Tool Test
```

AI 功能必须尽量测试：

* 正常输入
* 空输入
* 错误参数
* Tool 调用失败
* API 超时
* 权限不足
* LLM 返回异常
* RAG 无结果

---

# 19. Database Rules

数据库相关修改必须谨慎。

修改数据库结构前：

1. 检查现有结构
2. 检查是否有依赖
3. 记录迁移方案
4. 更新 `docs/database.md`

不要未经确认删除生产数据或修改关键业务字段。

---

# 20. Dependencies

新增依赖前先判断：

1. 是否真的需要
2. 项目中是否已经存在类似能力
3. 是否可以使用标准库解决
4. 是否会增加维护成本

不要为了一个简单功能引入大型框架。

---

# 21. Git Rules

Commit 应该：

* 小
* 清晰
* 单一目的

推荐：

```text
feat: add inventory query tool
feat: add rag document loader
fix: handle inventory api timeout
refactor: simplify llm client
docs: update architecture
test: add inventory tool tests
```

不要提交：

```text
.env
*.log
__pycache__/
node_modules/
个人配置
密钥
临时文件
```

---

# 22. Documentation Rules

以下变化必须更新文档：

### Architecture Change

更新：

```text
docs/architecture.md
```

### API Change

更新：

```text
docs/api.md
```

### Database Change

更新：

```text
docs/database.md
```

### Security Change

更新：

```text
docs/security.md
```

### Important Technical Decision

新增：

```text
docs/decisions/
```

---

# 23. AI Agent Behavior

CodeBuddy 在执行任务时必须遵循：

### DO

* 先理解
* 先检查现有代码
* 优先复用
* 小步修改
* 保持架构一致
* 编写测试
* 更新必要文档
* 发现风险时主动说明

### DON'T

* 不要擅自重构整个项目
* 不要修改无关代码
* 不要删除已有功能
* 不要创建大量没有实际用途的抽象
* 不要为了“未来可能用到”提前实现复杂架构
* 不要假设不存在的 API
* 不要编造数据库字段
* 不要编造业务规则
* 不要直接操作生产数据库
* 不要泄露敏感信息

---

# 24. Before Coding Checklist

在开始编码前：

```text
[ ] 是否理解需求？
[ ] 是否检查现有代码？
[ ] 是否检查相关 docs？
[ ] 是否存在已有实现？
[ ] 是否需要修改架构？
[ ] 是否需要数据库修改？
[ ] 是否涉及权限？
[ ] 是否涉及敏感数据？
[ ] 是否需要新增依赖？
[ ] 是否需要测试？
```

---

# 25. After Coding Checklist

完成后：

```text
[ ] 代码是否可以运行？
[ ] 测试是否通过？
[ ] 是否存在明显异常？
[ ] 是否引入不必要依赖？
[ ] 是否修改了无关文件？
[ ] 是否符合项目架构？
[ ] 是否存在安全问题？
[ ] API 是否需要更新？
[ ] 文档是否需要更新？
```

---

# 26. Decision Priority

当多个方案发生冲突时，优先级：

```text
1. Security
2. Correctness
3. Existing Business Rules
4. Existing Architecture
5. Maintainability
6. Simplicity
7. Performance
8. Convenience
```

---

# 27. Development Philosophy

本项目遵循：

> Simple First, Evolve Later.

优先：

```text
能运行
→ 能测试
→ 能理解
→ 能维护
→ 再扩展
```

不要：

```text
一开始
→ Multi-Agent
→ MCP
→ LangGraph
→ Kubernetes
→ 微服务
→ 十几个数据库
```

项目应该随着真实需求逐步演进。

---

# 28. Current MVP

当前 MVP 目标：

```text
                    WMS AI Assistant

                         User
                           ↓
                      FastAPI API
                           ↓
                      LLM Service
                       ↙       ↘
                    RAG        Tools
                     ↓           ↓
                Knowledge     WMS API
                   Base           ↓
                                  ERP
                                  ↓
                              PostgreSQL
```

第一批功能：

### AI Chat

自然语言聊天。

### RAG

回答 WMS / ERP 操作文档问题。

### Inventory Tool

查询库存。

### Purchase Order Tool

查询采购订单。

### Outbound Tool

查询销售出库。

---

# 29. Future Direction

后续根据真实需求逐步增加：

```text
MVP
 ↓
RAG
 ↓
Tool Calling
 ↓
Agent
 ↓
LangGraph
 ↓
Permission
 ↓
Audit
 ↓
Workflow
 ↓
AI Write Operations
 ↓
MCP
 ↓
Private LLM
 ↓
Enterprise Deployment
```

任何未来能力都必须建立在真实需求基础上，不允许为了技术炫技提前复杂化。

---

# 30. Final Rule

当你不确定应该怎么做时：

1. 不要猜测关键业务规则
2. 不要擅自修改架构
3. 不要直接编写大量代码
4. 先检查现有实现
5. 先提出方案
6. 保持修改最小化
7. 优先保证安全、正确和可维护

本项目的目标不是展示 AI 技术，而是：

> **构建一个真正能够连接企业 WMS / ERP、可靠回答业务问题，并在权限控制下辅助企业业务工作的 AI Assistant。**
