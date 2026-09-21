# WMS AI Assistant — Architecture

## 1. 文档信息

| 项目           | 内容                    |
| ------------ | --------------------- |
| 项目名称         | WMS AI Assistant      |
| 文档名称         | 系统架构设计                |
| 当前版本         | 1.0                   |
| 当前阶段         | MVP                   |
| 后端语言         | Python 3.12+          |
| Web 框架       | FastAPI               |
| 数据库          | PostgreSQL            |
| 向量数据库        | PostgreSQL + pgvector |
| LLM          | 可配置的 LLM API          |
| Embedding    | 可配置的 Embedding API    |
| RAG          | MVP 支持                |
| Tool Calling | MVP 支持                |
| Agent        | 后续阶段                  |
| LangGraph    | 后续阶段                  |
| 部署           | MVP 优先单体部署            |

---

# 2. 架构目标

WMS AI Assistant 是一个面向企业 WMS/ERP 场景的 AI 应用系统。

系统通过自然语言理解用户需求，根据用户问题自动选择：

1. 普通 LLM 对话
2. RAG 知识库检索
3. WMS/ERP 数据查询 Tool
4. 后续扩展 Agent 和 LangGraph

核心目标：

> 让用户通过自然语言访问企业知识、业务数据和业务能力，同时保证 AI 不绕过企业原有业务规则和权限体系。

例如：

```text
用户：
越南仓 A001 现在还有多少库存？

↓

AI 判断：
这是库存查询问题

↓

调用 Tool：
get_inventory(
    material_code="A001",
    warehouse_code="VN01"
)

↓

Tool 调用：
WMS API

↓

WMS 返回：
库存 1250

↓

LLM：
根据结构化数据生成自然语言回答

↓

用户：
A001 在越南仓还有 1250 个。
```

---

# 3. 总体架构

## 3.1 MVP 总体架构

```text
┌──────────────────────────────┐
│            用户              │
└──────────────┬───────────────┘
               │
               │ Natural Language
               ▼
┌──────────────────────────────┐
│          Frontend            │
│      Web / Chat Interface    │
└──────────────┬───────────────┘
               │ HTTP
               ▼
┌──────────────────────────────┐
│          FastAPI             │
│       API / Auth / Session   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│        AI Service            │
│                              │
│  ┌────────────────────────┐  │
│  │      Intent / Router    │  │
│  └───────────┬────────────┘  │
│              │               │
│      ┌───────┴────────┐      │
│      ▼                ▼      │
│    RAG             Tool      │
│   Service          Calling   │
│      │                │      │
│      └───────┬────────┘      │
│              ▼               │
│             LLM              │
└──────────────┬───────────────┘
               │
        ┌──────┴───────────┐
        │                  │
        ▼                  ▼
┌───────────────┐   ┌────────────────┐
│  PostgreSQL   │   │   WMS / ERP    │
│               │   │      API       │
│ Business Data │   │                │
│ Vector Data   │   │ Existing Rules │
│ Conversation  │   │ Permission     │
└───────────────┘   └────────────────┘
```

---

# 4. 核心架构原则

## 4.1 AI 不直接操作企业数据库

这是整个项目最重要的架构原则。

禁止：

```text
LLM
 ↓
直接连接 WMS PostgreSQL
 ↓
SELECT inventory ...
```

正确方式：

```text
LLM
 ↓
Tool
 ↓
WMS API
 ↓
WMS Business Service
 ↓
Database
```

原因：

* 避免 AI 绕过业务规则
* 避免 SQL 注入
* 避免越权查询
* 复用现有 WMS 业务逻辑
* 方便权限控制
* 方便审计
* 后续支持写操作时更加安全

---

# 5. 系统分层

系统采用简单的分层架构。

```text
API Layer
    ↓
Application Layer
    ↓
AI Layer
    ↓
Tool Layer
    ↓
Integration Layer
    ↓
External WMS / ERP
```

---

## 5.1 API Layer

负责：

* HTTP API
* 请求参数
* 用户会话
* 用户身份
* API Response
* 异常处理

例如：

```text
POST /api/chat
GET  /api/health
GET  /api/conversations
```

不负责：

* LLM Prompt
* RAG
* WMS 业务逻辑
* 数据库业务查询

---

# 6. Application Layer

Application Layer 负责组织业务流程。

例如：

```python
chat_service.chat(...)
```

负责：

```text
接收用户问题
    ↓
获取用户上下文
    ↓
调用 AI Service
    ↓
处理 Tool / RAG
    ↓
生成最终响应
    ↓
保存会话
```

Application Layer 不应该包含具体的 WMS SQL。

---

# 7. AI Layer

AI Layer 是整个系统的核心。

主要包含：

```text
LLM Service
Prompt Service
RAG Service
Tool Calling Service
Context Service
```

未来增加：

```text
Agent
LangGraph
Memory
Planning
```

---

# 8. LLM Service

LLM Service 负责统一封装不同的大模型。

建议不要在业务代码中直接写：

```python
openai.chat.completions.create(...)
```

而应该封装成：

```python
llm_service.chat(...)
```

或者：

```python
llm_service.generate(...)
```

这样未来可以切换：

```text
OpenAI
Claude
Qwen
DeepSeek
Ollama
企业私有模型
```

而不需要修改整个业务系统。

---

## 8.1 LLM 配置

模型配置统一使用环境变量。

例如：

```text
LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=
```

禁止：

```python
API_KEY = "xxxxx"
```

禁止把 API Key 提交到 Git。

---

# 9. Prompt Architecture

Prompt 不应该散落在 Python 代码中。

建议：

```text
backend/app/prompts/
├── system.txt
├── chat.txt
├── rag.txt
└── tool.txt
```

后续可以进一步拆分：

```text
prompts/
├── system/
├── rag/
├── inventory/
├── purchase/
└── outbound/
```

Prompt 与业务代码分离。

---

# 10. RAG Architecture

RAG 用于解决企业知识问答问题。

例如用户：

```text
采购入库应该怎么操作？
```

系统不应该单纯依赖 LLM 自己回答。

应该：

```text
用户问题
   ↓
Embedding
   ↓
向量检索
   ↓
获取相关企业文档
   ↓
构建 Context
   ↓
LLM
   ↓
答案
```

---

# 11. RAG 数据流程

## 11.1 文档入库

```text
PDF / DOCX / TXT / MD
          ↓
      Document Parser
          ↓
        Chunking
          ↓
       Embedding
          ↓
 PostgreSQL + pgvector
```

每个 Chunk 至少保存：

```text
id
document_id
content
embedding
source
metadata
created_at
```

Metadata 可以包含：

```text
document_name
document_type
department
warehouse
version
page
section
```

---

# 12. RAG 查询流程

用户：

```text
越南仓怎么进行盘点？
```

执行：

```text
Question
   ↓
Embedding
   ↓
Vector Search
   ↓
Top K Documents
   ↓
Context
   ↓
LLM
   ↓
Answer
```

如果检索结果不足：

```text
不要编造企业制度。
```

应该明确告诉用户：

```text
知识库中没有找到足够的信息。
```

---

# 13. Tool Calling Architecture

Tool Calling 用于获取实时业务数据。

例如：

```text
get_inventory
get_purchase_orders
get_outbound_orders
```

---

## 13.1 Tool 结构

每个 Tool 应包含：

```text
Tool Name
Description
Input Schema
Validation
Execution
Result Schema
Error Handling
Permission
Logging
```

例如：

```text
Tool:
get_inventory

Description:
查询指定物料在指定仓库的库存。

Parameters:
material_code
warehouse_code

Return:
material_code
warehouse_code
qty
unit
```

---

# 14. Tool 调用流程

```text
User
 ↓
FastAPI
 ↓
AI Service
 ↓
LLM
 ↓
判断需要 Tool
 ↓
Tool Calling
 ↓
Tool Registry
 ↓
Permission Check
 ↓
WMS API
 ↓
WMS Business Logic
 ↓
Return Structured Data
 ↓
LLM
 ↓
Natural Language Answer
```

---

# 15. Tool Registry

所有 Tool 统一注册。

例如：

```text
tools/
├── inventory.py
├── purchase.py
├── outbound.py
└── registry.py
```

Registry：

```python
TOOLS = [
    get_inventory,
    get_purchase_orders,
    get_outbound_orders,
]
```

未来可以扩展：

```text
get_stocktake_task
get_material_info
get_transfer_orders
get_production_orders
get_quality_records
```

---

# 16. WMS / ERP Integration

AI 系统不直接实现 WMS 业务逻辑。

例如：

```text
AI Assistant
      ↓
Tool
      ↓
WMS API
      ↓
WMS Service
      ↓
Database
```

如果现有 WMS 已经存在 API：

```text
优先复用现有 API。
```

不要重新实现：

```text
库存计算
单据状态
业务校验
权限
库存扣减
库存锁定
```

---

# 17. WMS API Adapter

建议增加 Integration Layer：

```text
backend/app/integrations/
├── wms/
│   ├── client.py
│   ├── inventory.py
│   ├── purchase.py
│   └── outbound.py
└── erp/
```

例如：

```python
class WMSClient:

    async def get_inventory(
        self,
        material_code: str,
        warehouse_code: str
    ):
        ...
```

Tool 不直接处理 HTTP 细节。

正确：

```text
Tool
 ↓
WMS Client
 ↓
HTTP API
```

而不是：

```text
Tool
 ↓
requests.get(...)
```

到处散落。

---

# 18. PostgreSQL Architecture

PostgreSQL 主要负责：

```text
系统数据
会话数据
消息数据
RAG 文档
Vector
系统配置
日志
```

建议初期使用：

```text
PostgreSQL
+
pgvector
```

而不是一开始就引入：

```text
Qdrant
Milvus
Elasticsearch
Redis
Kafka
```

当系统规模确实需要时再拆分。

---

# 19. 数据库逻辑划分

建议：

```text
conversation
conversation_message

knowledge_document
knowledge_chunk

tool_call_log

system_config
```

后续：

```text
user
role
permission
audit_log
agent_execution
workflow_execution
```

---

# 20. Conversation Architecture

聊天系统需要保存上下文。

例如：

```text
User:
查询 A001 库存

AI:
A001 越南仓库存 1250。

User:
其中多少是待检？

AI:
...
```

第二个问题需要结合第一轮上下文。

因此保存：

```text
conversation
    ↓
messages
    ↓
role
content
timestamp
metadata
```

---

# 21. Context Management

不能无限制把历史聊天全部发送给 LLM。

采用：

```text
Recent Messages
+
Conversation Summary
+
Current Question
+
RAG Context
+
Tool Result
```

例如：

```text
System Prompt

Conversation Summary

最近 5 条消息

Retrieved Knowledge

Tool Result

Current Question
```

---

# 22. Agent Architecture

Agent 不属于 MVP 第一阶段。

MVP：

```text
User
 ↓
LLM
 ↓
RAG / Tool
 ↓
Answer
```

后续：

```text
User
 ↓
Agent
 ↓
Planning
 ↓
Tool 1
 ↓
Tool 2
 ↓
Tool 3
 ↓
Result
```

例如：

```text
用户：

帮我分析越南仓 A001 为什么库存不足。
```

Agent 可以：

```text
1. 查询库存
2. 查询最近采购订单
3. 查询生产领料
4. 查询销售出库
5. 综合分析
```

---

# 23. LangGraph Architecture

LangGraph 在 Agent 复杂后再引入。

例如：

```text
START
  ↓
Analyze Question
  ↓
Query Inventory
  ↓
Query Purchase
  ↓
Query Outbound
  ↓
Analyze
  ↓
Generate Answer
  ↓
END
```

LangGraph 不应该为了“使用 AI 技术”而强行加入 MVP。

只有当流程出现：

* 多步骤
* 状态管理
* 循环
* 条件分支
* 人工确认
* 多 Tool
* 长流程

时再引入。

---

# 24. Permission Architecture

权限必须位于 AI 与企业数据之间。

```text
User
 ↓
AI
 ↓
Tool
 ↓
Permission Check
 ↓
WMS API
```

例如：

```text
用户 A：

查询越南仓库存
```

系统检查：

```text
用户 A
    ↓
是否允许访问 VN01？
    ↓
Yes
    ↓
执行
```

如果：

```text
No
```

直接拒绝 Tool 调用。

不能让 LLM 自己决定用户是否有权限。

---

# 25. Read / Write Separation

系统严格区分：

```text
Read
Write
```

---

## 25.1 Read

例如：

```text
查询库存
查询采购订单
查询出库单
查询盘点任务
```

经过权限验证后可以自动执行。

---

## 25.2 Write

例如：

```text
创建出库单
修改库存
提交盘点
审核单据
删除数据
```

默认必须：

```text
用户确认
+
权限验证
+
业务校验
+
执行
+
审计日志
```

例如：

```text
用户：
帮我创建 A001 调拨单 100 个。

AI：
准备创建：

物料：A001
来源仓：VN01
目标仓：VN02
数量：100

请确认是否执行？

用户：
确认。

↓

Permission Check

↓

Business Validation

↓

WMS API

↓

Audit Log
```

---

# 26. Security Boundary

系统安全边界：

```text
┌─────────────────────┐
│        LLM          │
│                     │
│ 不可信决策组件       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│       Tool          │
│                     │
│ 参数验证             │
│ 权限验证             │
│ 数据验证             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      WMS API        │
│                     │
│ 企业业务规则         │
│ 企业权限             │
│ 数据校验             │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      Database       │
└─────────────────────┘
```

核心原则：

> LLM 可以决定“调用哪个能力”，但不能决定“用户有没有权限”和“业务是否允许执行”。

---

# 27. Error Handling

AI 系统必须区分：

```text
LLM Error
RAG Error
Tool Error
WMS API Error
Permission Error
Business Error
Database Error
```

例如 WMS API 返回：

```text
库存查询失败
```

AI 不得生成：

```text
库存大约是 1000。
```

应该返回真实错误：

```text
当前无法获取 A001 的库存信息，请稍后重试。
```

---

# 28. Logging

至少记录：

```text
request_id
user_id
conversation_id
question
selected_tool
tool_arguments
tool_result_status
latency
error
created_at
```

涉及敏感信息时：

```text
禁止完整记录密码
禁止记录 API Key
禁止无控制地记录敏感业务数据
```

---

# 29. API Design

MVP API：

```text
GET  /api/health

POST /api/chat

GET  /api/conversations

GET  /api/conversations/{conversation_id}

POST /api/knowledge/documents

POST /api/knowledge/search
```

未来：

```text
GET  /api/tools

POST /api/tools/{tool_name}

GET  /api/audit/logs
```

---

# 30. Chat API

请求：

```json
{
  "conversation_id": "xxx",
  "message": "越南仓 A001 还有多少库存？"
}
```

响应：

```json
{
  "conversation_id": "xxx",
  "answer": "A001 在越南仓当前库存为 1250 个。",
  "tool_calls": [
    {
      "tool": "get_inventory",
      "status": "success"
    }
  ]
}
```

---

# 31. 推荐项目结构

```text
wms-ai-assistant/
│
├── AGENTS.md
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
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
│   └── app/
│       ├── main.py
│       ├── config.py
│       │
│       ├── api/
│       │   ├── chat.py
│       │   ├── health.py
│       │   └── knowledge.py
│       │
│       ├── services/
│       │   ├── chat_service.py
│       │   ├── llm_service.py
│       │   ├── rag_service.py
│       │   └── tool_service.py
│       │
│       ├── llm/
│       │   ├── client.py
│       │   └── models.py
│       │
│       ├── rag/
│       │   ├── loader.py
│       │   ├── splitter.py
│       │   ├── embedding.py
│       │   └── retriever.py
│       │
│       ├── tools/
│       │   ├── registry.py
│       │   ├── inventory.py
│       │   ├── purchase.py
│       │   └── outbound.py
│       │
│       ├── integrations/
│       │   ├── wms/
│       │   │   ├── client.py
│       │   │   ├── inventory.py
│       │   │   ├── purchase.py
│       │   │   └── outbound.py
│       │   │
│       │   └── erp/
│       │
│       ├── db/
│       │   ├── database.py
│       │   ├── models.py
│       │   └── repositories/
│       │
│       ├── prompts/
│       │   ├── system.txt
│       │   ├── chat.txt
│       │   ├── rag.txt
│       │   └── tool.txt
│       │
│       ├── schemas/
│       │
│       └── utils/
│
├── knowledge/
│
├── tests/
│   ├── test_chat.py
│   ├── test_rag.py
│   └── test_tools.py
│
└── scripts/
```

---

# 32. 请求处理流程

## 32.1 普通问题

```text
User
 ↓
POST /api/chat
 ↓
Chat Service
 ↓
LLM
 ↓
Answer
 ↓
User
```

例如：

```text
什么是 WMS？
```

---

## 32.2 企业知识问题

```text
User
 ↓
Chat Service
 ↓
LLM / Router
 ↓
RAG
 ↓
Vector Search
 ↓
Knowledge Context
 ↓
LLM
 ↓
Answer
```

例如：

```text
越南仓盘点流程是什么？
```

---

## 32.3 实时业务数据问题

```text
User
 ↓
Chat Service
 ↓
LLM
 ↓
Tool Calling
 ↓
Permission
 ↓
WMS API
 ↓
Tool Result
 ↓
LLM
 ↓
Answer
```

例如：

```text
A001 越南仓还有多少？
```

---

# 33. RAG 与 Tool 的选择

系统需要区分：

### 知识类问题

```text
怎么操作？
什么规则？
流程是什么？
制度是什么？
```

使用：

```text
RAG
```

### 实时数据问题

```text
现在库存多少？
有哪些采购订单？
今天出了多少货？
```

使用：

```text
Tool
```

### 混合问题

例如：

```text
为什么越南仓 A001 库存不足？
```

未来可以：

```text
RAG
+
Tool
+
Agent
```

---

# 34. MVP 不采用的架构

当前阶段不采用：

```text
Microservices
Kubernetes
Kafka
Redis Cluster
Milvus Cluster
Qdrant Cluster
Multi-Agent
复杂 LangGraph
MCP 全面接入
复杂 Memory
自动决策系统
```

原因：

> 当前重点是验证 AI + WMS 的核心业务闭环，而不是搭建复杂基础设施。

---

# 35. 部署架构

MVP 建议单体部署。

```text
                    Internet / LAN
                          │
                          ▼
                    ┌──────────┐
                    │  Nginx   │
                    └────┬─────┘
                         │
                         ▼
                    ┌──────────┐
                    │ FastAPI  │
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
        PostgreSQL      LLM       WMS API
          + pgvector
```

后续规模增长后再考虑：

```text
API Service
AI Service
RAG Service
Tool Service
Worker
Vector DB
Redis
```

---

# 36. 可扩展性设计

虽然 MVP 保持简单，但需要预留扩展点。

## LLM

```text
LLMService
```

可以替换不同模型。

## RAG

```text
RAGService
```

可以替换：

```text
pgvector
Qdrant
Milvus
```

## WMS

```text
WMSClient
```

可以适配：

```text
REST API
GraphQL
内部 API
ERP API
```

## Agent

在：

```text
AI Service
```

层增加：

```text
AgentService
```

不需要重构整个系统。

---

# 37. 技术演进路线

## Phase 1

```text
Python
FastAPI
LLM API
```

实现：

```text
AI Chat
```

---

## Phase 2

加入：

```text
PostgreSQL
Embedding
pgvector
RAG
```

实现：

```text
企业知识问答
```

---

## Phase 3

加入：

```text
Tool Calling
WMS API
```

实现：

```text
库存查询
采购查询
出库查询
```

---

## Phase 4

加入：

```text
Agent
```

实现：

```text
多步骤任务
```

---

## Phase 5

加入：

```text
LangGraph
```

实现：

```text
复杂业务流程
状态管理
人工确认
多步骤工作流
```

---

## Phase 6

加入：

```text
Permission
Audit
Write Operations
```

实现：

```text
AI 执行业务操作
```

---

# 38. 一个完整示例

用户：

```text
帮我查一下越南仓 A001 的库存，并告诉我最近有没有采购订单。
```

系统：

```text
                    User
                      │
                      ▼
                  FastAPI
                      │
                      ▼
                 ChatService
                      │
                      ▼
                     LLM
                      │
             ┌────────┴────────┐
             ▼                 ▼
      get_inventory      get_purchase_orders
             │                 │
             ▼                 ▼
          WMS API           WMS API
             │                 │
             └────────┬────────┘
                      ▼
                 Tool Results
                      │
                      ▼
                     LLM
                      │
                      ▼
                  Final Answer
```

最终：

```text
A001 越南仓当前库存为 1250 个。

最近采购订单：
PO20260918001：500 个
PO20260919002：1000 个

其中 PO20260918001 当前状态为待入库。
```

这里的数据全部来自 Tool/WMS API。

LLM 只负责：

```text
理解问题
选择 Tool
组织结果
生成自然语言
```

不负责：

```text
自己猜库存
自己计算业务状态
自己修改数据库
自己绕过权限
```

---

# 39. 架构决策

## ADR-001：使用 Python

原因：

* AI 生态成熟
* LangChain/LangGraph 支持
* RAG 生态成熟
* LLM SDK 丰富
* 用户已有 Python 基础

---

## ADR-002：使用 FastAPI

原因：

* Python 原生异步支持
* API 开发简单
* Pydantic 数据验证
* 适合 AI API 服务
* 后续容易容器化

---

## ADR-003：MVP 使用 PostgreSQL + pgvector

原因：

* 已有 PostgreSQL
* 降低基础设施复杂度
* 业务数据和向量数据可以统一管理
* MVP 数据量通常不需要独立向量数据库

---

## ADR-004：LLM 不直接访问数据库

原因：

* 安全
* 权限
* 业务规则
* 审计
* 防止错误操作

统一：

```text
LLM
 ↓
Tool
 ↓
WMS API
```

---

## ADR-005：MVP 不使用 Agent

原因：

Agent 会增加：

```text
复杂度
调试成本
Token 消耗
不确定性
```

先验证：

```text
LLM
+
RAG
+
Tool Calling
```

成功后再增加 Agent。

---

## ADR-006：MVP 不使用微服务

原因：

当前项目规模较小。

优先：

```text
单体应用
```

而不是：

```text
多个微服务
```

---

# 40. 开发原则

遵循：

```text
Simple First
        ↓
Working MVP
        ↓
Real WMS Integration
        ↓
Collect Feedback
        ↓
Agent
        ↓
LangGraph
        ↓
Enterprise Deployment
```

不要：

```text
先搭建复杂架构
        ↓
开发几个月
        ↓
最后才验证业务价值
```

---

# 41. 当前 MVP 架构边界

当前只要求：

```text
                    ┌───────────────┐
                    │     User      │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │    FastAPI    │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │  AI Service   │
                    └───────┬───────┘
                            ↓
                    ┌───────────────┐
                    │      LLM      │
                    └───────┬───────┘
                       ┌────┴────┐
                       ↓         ↓
                     RAG       Tools
                       │         │
                       ↓         ↓
                  PostgreSQL   WMS API
```

暂时不加入：

```text
Agent
LangGraph
MCP
复杂权限
写操作
多智能体
微服务
Kubernetes
```

---

# 42. 当前开发优先级

严格按照以下顺序：

```text
1. 项目骨架
        ↓
2. FastAPI
        ↓
3. Health Check
        ↓
4. LLM API
        ↓
5. Chat API
        ↓
6. PostgreSQL
        ↓
7. Embedding
        ↓
8. RAG
        ↓
9. Tool Calling
        ↓
10. WMS API
        ↓
11. Inventory Tool
        ↓
12. Purchase Tool
        ↓
13. Outbound Tool
        ↓
14. Agent
        ↓
15. LangGraph
        ↓
16. Permission
        ↓
17. Write Operations
        ↓
18. Private Deployment
```

每一步完成后必须：

```text
运行
 ↓
测试
 ↓
确认
 ↓
再进入下一阶段
```

---

# 43. 最终架构目标

最终系统希望形成：

```text
                    WMS AI Assistant
                           │
              ┌────────────┼────────────┐
              │            │            │
             RAG         Tools        Agent
              │            │            │
              │            │        LangGraph
              │            │            │
              └────────────┼────────────┘
                           │
                          LLM
                           │
                     Permission
                           │
                       WMS / ERP
                           │
                       Enterprise
```

核心思想：

> LLM 是“大脑”，RAG 是“知识库”，Tool 是“手”，WMS/ERP API 是“业务执行系统”，权限和业务规则是“安全边界”。

系统不是重新做一个 WMS，而是在现有 WMS/ERP 之上增加一层 AI 能力。

---

# 44. CodeBuddy 开发规则

CodeBuddy 开始编写代码前必须：

1. 阅读 `AGENTS.md`
2. 阅读 `docs/requirements.md`
3. 阅读 `docs/architecture.md`
4. 理解当前阶段
5. 不提前实现未来阶段功能
6. 不擅自修改架构原则
7. 不直接连接 WMS 数据库
8. 不在代码中硬编码 API Key
9. 每次只实现一个明确功能
10. 实现后运行测试
11. 发现架构冲突时先说明，不要自行改变架构

当前阶段优先实现：

```text
Phase 1:
项目骨架
+
FastAPI
+
Health Check
+
LLM API
+
最简单 Chat API
```

完成 Phase 1 后，再进入 RAG。

---

# 45. Definition of Done

一个功能只有满足以下条件才算完成：

```text
代码完成
+
类型/参数正确
+
异常处理
+
测试通过
+
API 可运行
+
文档同步
+
没有破坏现有功能
```

对于 AI 功能，还需要验证：

```text
正常问题
异常问题
空参数
错误参数
LLM Error
Tool Error
WMS Error
```

---

# 46. 最终原则

本项目遵循以下原则：

```text
安全 > 正确性 > 业务规则 > 架构一致性
> 可维护性 > 简单性 > 性能 > 开发便利
```

最重要的原则：

> 不为了使用 AI 技术而使用 AI 技术。

> 不为了使用 Agent 而使用 Agent。

> 不为了使用 LangGraph 而使用 LangGraph。

> 先解决真实 WMS/ERP 问题，再逐步增加 AI 能力。

最终目标不是做一个“聊天机器人”，而是：

```text
自然语言
    ↓
AI 理解
    ↓
企业知识
    ↓
企业实时数据
    ↓
企业业务能力
    ↓
安全地辅助用户完成工作
```
