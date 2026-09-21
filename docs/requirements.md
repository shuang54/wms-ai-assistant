# WMS AI Assistant — Requirements

## 1. 文档信息

| 项目   | 内容                                       |
| ---- | ---------------------------------------- |
| 项目名称 | WMS AI Assistant                         |
| 文档版本 | 1.0                                      |
| 当前阶段 | MVP                                      |
| 目标   | 构建面向企业 WMS / ERP 场景的 AI 助手               |
| 后端   | Python + FastAPI                         |
| 数据库  | PostgreSQL                               |
| AI   | LLM API + Embedding + RAG + Tool Calling |
| 后续   | Agent + LangGraph + 权限 + 私有化部署           |

---

# 2. Product Vision

## 2.1 产品定位

WMS AI Assistant 是一个连接企业 WMS / ERP 系统的 AI 助手。

用户通过自然语言提出问题，系统根据：

* 企业知识库
* WMS / ERP 实时业务数据
* 预定义业务 Tool

理解用户意图并返回结果。

最终目标不是简单的聊天机器人，而是：

> **让用户通过自然语言访问企业业务知识和业务数据，并逐步实现 AI 辅助业务操作。**

---

# 3. Problem

目前企业 WMS / ERP 系统存在以下问题：

### 3.1 数据查询复杂

用户需要：

```text
进入系统
 ↓
找到模块
 ↓
设置查询条件
 ↓
查询
 ↓
查看结果
```

对于不熟悉系统的用户，使用成本较高。

---

### 3.2 企业知识分散

业务知识可能存在于：

* WMS 操作手册
* ERP 操作手册
* Excel
* Word
* PDF
* SOP
* 企业制度
* IT 文档

用户需要人工寻找资料。

---

### 3.3 数据与知识没有结合

例如用户问：

> A001 为什么库存不足？

单纯知识库无法回答，因为需要实时业务数据。

需要结合：

```text
库存
+
采购订单
+
销售订单
+
生产数据
+
企业业务规则
```

---

# 4. Product Goals

## 4.1 MVP Goals

MVP 必须实现以下能力：

### G1 — AI Chat

用户可以通过自然语言与 AI 对话。

例如：

> 什么是采购入库？

---

### G2 — Knowledge Q&A

AI 可以根据企业知识库回答业务问题。

例如：

> WMS 采购入库怎么操作？

AI 应该：

```text
用户问题
 ↓
检索知识库
 ↓
获取相关文档
 ↓
LLM 生成回答
```

---

### G3 — Inventory Query

用户可以通过自然语言查询库存。

例如：

> A001 在 VN01 仓库还有多少？

AI 自动识别：

```text
物料 = A001
仓库 = VN01
操作 = 查询库存
```

然后调用：

```text
get_inventory()
```

---

### G4 — Purchase Order Query

用户可以查询采购订单。

例如：

> 查询 A001 最近的采购订单。

AI 调用：

```text
get_purchase_orders()
```

---

### G5 — Outbound Query

用户可以查询销售出库。

例如：

> 今天有哪些销售出库单？

AI 调用：

```text
get_outbound_orders()
```

---

# 5. Non-Goals

MVP 阶段明确不实现：

* Multi-Agent
* 复杂 Agent
* LangGraph 工作流
* MCP
* AI 自动审核
* AI 自动删除数据
* AI 自动修改业务数据
* AI 自动创建业务单据
* OCR
* 语音交互
* 推荐系统
* 库存预测
* 自动补货
* 复杂 BI
* Kubernetes
* 微服务拆分
* 大规模分布式架构

这些功能属于未来版本。

---

# 6. Target Users

## 6.1 Warehouse User

典型需求：

* 查询库存
* 查询库位
* 查询入库
* 查询出库
* 查询盘点任务
* 查询 WMS 操作方法

---

## 6.2 PMC

典型需求：

* 查询生产相关数据
* 查询采购订单
* 查询物料库存
* 查询生产工单
* 分析物料供应情况

---

## 6.3 Purchasing

典型需求：

* 查询采购订单
* 查询采购入库
* 查询供应商相关数据
* 查询物料采购情况

---

## 6.4 IT / System Administrator

典型需求：

* 查询系统数据
* 查询系统使用方法
* 分析异常
* 调试 AI Tool
* 查看调用日志

---

# 7. Core User Experience

用户不需要学习 AI 指令。

用户可以直接使用自然语言：

```text
查询 A001 库存
```

或者：

```text
帮我看看 A001 为什么库存不足
```

系统负责：

```text
理解用户意图
 ↓
决定是否需要知识库
 ↓
决定是否需要 Tool
 ↓
调用 Tool
 ↓
分析结果
 ↓
生成自然语言回答
```

---

# 8. Functional Requirements

## FR-001 AI Chat

### Description

提供统一 AI 对话接口。

### Input

```json
{
  "message": "查询A001库存"
}
```

### Output

```json
{
  "answer": "A001 当前库存为 1200 个。"
}
```

### Requirements

* 支持自然语言
* 支持上下文对话
* 支持异常处理
* 不暴露内部错误
* 支持记录 request_id

---

# 9. FR-002 LLM Integration

系统需要支持 LLM API。

### Requirements

LLM 配置必须支持：

```text
LLM_PROVIDER
LLM_MODEL
LLM_API_KEY
LLM_BASE_URL
```

不得将 API Key 写入代码。

---

# 10. FR-003 Knowledge Base

系统需要支持企业知识库。

第一阶段支持：

```text
PDF
DOCX
TXT
MD
```

---

## 10.1 Knowledge Processing

文档处理流程：

```text
Document
 ↓
Parse
 ↓
Clean
 ↓
Chunk
 ↓
Embedding
 ↓
Vector Storage
```

---

## 10.2 Retrieval

用户提出问题：

```text
WMS采购入库怎么操作？
```

系统：

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
```

---

## 10.3 Source

RAG 返回的内容应该保留来源信息。

例如：

```text
来源：
WMS操作手册.pdf
第 23 页
```

如果无法找到可靠资料：

> 系统应明确说明没有找到相关资料，而不是编造答案。

---

# 11. FR-004 Embedding

Embedding 用于：

* 文档向量化
* 用户问题向量化
* 相似度检索

第一阶段优先使用：

```text
PostgreSQL
+
pgvector
```

暂不引入独立向量数据库。

---

# 12. FR-005 Inventory Tool

## Tool Name

```text
get_inventory
```

## Purpose

查询物料库存。

## Input

```text
material_code
warehouse_code
```

## Example

用户：

> 查询 A001 在 VN01 的库存。

AI：

```text
get_inventory(
    material_code="A001",
    warehouse_code="VN01"
)
```

## Output

应该返回结构化数据，例如：

```json
{
  "success": true,
  "material_code": "A001",
  "warehouse_code": "VN01",
  "quantity": 1200,
  "unit": "PCS"
}
```

---

# 13. FR-006 Purchase Order Tool

## Tool Name

```text
get_purchase_orders
```

## Purpose

查询采购订单。

## Example

用户：

> 查询 A001 最近的采购订单。

系统应该能够根据：

* 物料
* 采购订单号
* 供应商
* 日期
* 状态

等条件进行查询。

具体字段以后根据实际 WMS / ERP API 确定。

---

# 14. FR-007 Outbound Tool

## Tool Name

```text
get_outbound_orders
```

## Purpose

查询销售出库数据。

支持：

* 出库单号
* 销售订单
* 客户
* 日期
* 物料
* 状态

等条件。

---

# 15. FR-008 Tool Calling

LLM 可以根据用户问题选择合适的 Tool。

例如：

```text
用户
 ↓
"查询A001库存"
 ↓
LLM
 ↓
识别为库存查询
 ↓
get_inventory
 ↓
WMS API
 ↓
返回数据
 ↓
LLM
 ↓
自然语言回答
```

系统不应该让 LLM 自己生成 SQL 并直接访问数据库。

---

# 16. FR-009 WMS Integration

AI 系统需要连接现有 WMS。

原则：

> 优先调用现有 WMS API，不重复实现 WMS 已经存在的业务逻辑。

架构：

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

---

# 17. FR-010 Error Handling

Tool 调用失败时：

系统不能让 LLM 编造结果。

例如 WMS API 超时：

```json
{
  "success": false,
  "error_code": "WMS_API_TIMEOUT",
  "message": "WMS库存查询暂时不可用"
}
```

AI 应向用户说明：

> 当前无法获取实时库存，请稍后重试。

---

# 18. FR-011 Conversation Context

系统应该支持基本上下文。

例如：

用户：

> 查询 A001 库存。

AI：

> A001 当前库存为 1200 个。

用户：

> 那 B 仓呢？

AI 应理解：

```text
B仓
+
A001
```

而不是要求用户重新输入：

> 查询 A001 在 B 仓的库存。

---

# 19. FR-012 Logging

系统需要记录 AI 请求和 Tool 调用。

至少包含：

```text
request_id
user_id
timestamp
question
tool_name
execution_time
success
error_code
```

不得记录：

```text
API Key
Password
Token
```

---

# 20. Security Requirements

## SR-001

AI 不允许直接访问数据库。

## SR-002

所有 WMS / ERP 操作必须经过 API / Service。

## SR-003

API Key 必须使用环境变量。

## SR-004

用户只能访问其权限范围内的数据。

## SR-005

敏感数据需要权限控制。

## SR-006

未来所有写操作必须支持用户确认。

---

# 21. Read / Write Permission Model

MVP 主要支持 Read。

```text
查询库存       READ
查询采购订单   READ
查询销售出库   READ
查询知识库     READ
```

暂不支持：

```text
创建单据
修改单据
删除单据
审核单据
提交审批
```

未来增加写操作时：

```text
AI
 ↓
生成操作计划
 ↓
用户确认
 ↓
权限检查
 ↓
执行
```

---

# 22. Non-Functional Requirements

## NFR-001 Performance

普通 AI 请求应尽量在合理时间内完成。

Tool API 请求必须设置：

* timeout
* retry
* error handling

具体数值根据实际环境测试确定。

---

## NFR-002 Reliability

AI 不得因为单个 Tool 失败而产生虚假业务数据。

---

## NFR-003 Maintainability

代码需要：

* 模块化
* 类型明确
* 易测试
* 易扩展

---

## NFR-004 Security

敏感配置必须使用环境变量。

禁止提交：

```text
.env
credentials
private keys
API keys
```

---

# 23. MVP Architecture

```text
                    User
                      │
                      ↓
                Frontend / API
                      │
                      ↓
                   FastAPI
                      │
                AI Service
                 ↙         ↘
               RAG         LLM
                │            │
                ↓            │
          Knowledge Base     │
                             │
                             ↓
                         Tool Calling
                             │
                ┌────────────┼────────────┐
                ↓            ↓            ↓
           Inventory      Purchase      Outbound
              Tool          Tool          Tool
                │            │            │
                └────────────┼────────────┘
                             ↓
                         WMS API
                             ↓
                        ERP / DB
```

---

# 24. MVP Development Phases

## Phase 1 — Project Foundation

目标：

```text
Python
FastAPI
Project Structure
Configuration
Logging
Health Check
```

完成标准：

```text
GET /health
```

能够正常返回。

---

## Phase 2 — LLM

实现：

```text
FastAPI
 ↓
LLM API
 ↓
Response
```

完成：

```text
POST /api/chat
```

---

## Phase 3 — RAG

实现：

```text
Document
 ↓
Chunk
 ↓
Embedding
 ↓
pgvector
 ↓
Retrieval
 ↓
LLM
```

完成：

> WMS 文档问答。

---

## Phase 4 — Tool Calling

实现：

```text
get_inventory
get_purchase_orders
get_outbound_orders
```

---

## Phase 5 — WMS Integration

将 Tool 连接真实 WMS API。

---

## Phase 6 — Agent

当多个 Tool 的业务流程变复杂后，再引入 Agent。

---

## Phase 7 — LangGraph

当 Agent 出现：

* 多步骤
* 条件分支
* 状态管理
* 重试
* 人工确认

等需求时，引入 LangGraph。

---

# 25. Future Features

未来可能增加：

### Agent

```text
用户问题
 ↓
Agent
 ↓
Tool 1
 ↓
Tool 2
 ↓
Tool 3
 ↓
分析
 ↓
回答
```

### LangGraph

支持复杂业务流程。

### Permission

支持：

* 用户
* 角色
* 仓库
* 数据范围
* Tool 权限

### Audit

记录：

```text
谁
什么时候
问了什么
调用了什么 Tool
返回什么结果
是否执行了写操作
```

### Write Operations

未来支持：

```text
创建调拨
创建盘点
创建入库
```

但必须经过：

```text
AI
 ↓
用户确认
 ↓
权限
 ↓
API
```

---

# 26. Example User Scenarios

## Scenario 1 — Knowledge

用户：

> WMS 采购入库怎么操作？

系统：

```text
用户问题
 ↓
RAG
 ↓
WMS操作手册
 ↓
LLM
 ↓
回答
```

---

## Scenario 2 — Inventory

用户：

> A001 在 VN01 仓库还有多少？

系统：

```text
LLM
 ↓
get_inventory
 ↓
WMS
 ↓
1200 PCS
 ↓
LLM
 ↓
回答
```

---

## Scenario 3 — Follow-up

用户：

> A001 在 VN01 有多少？

AI：

> 1200 PCS。

用户：

> 那 VN02 呢？

AI 应理解：

```text
material_code = A001
warehouse_code = VN02
```

---

## Scenario 4 — Business Analysis

未来：

> 为什么 A001 库存不足？

Agent 可以：

```text
查询当前库存
 ↓
查询安全库存
 ↓
查询采购订单
 ↓
查询销售订单
 ↓
查询生产需求
 ↓
综合分析
```

该功能不属于 MVP。

---

# 27. Acceptance Criteria

MVP 完成必须满足：

### AC-001

用户可以正常发送自然语言问题。

### AC-002

系统可以调用 LLM API。

### AC-003

系统可以读取企业知识库。

### AC-004

RAG 能够根据文档内容回答问题。

### AC-005

系统可以调用库存 Tool。

### AC-006

系统可以调用采购订单 Tool。

### AC-007

系统可以调用销售出库 Tool。

### AC-008

Tool 可以通过 WMS API 获取真实数据。

### AC-009

AI 不允许直接访问数据库。

### AC-010

敏感配置不会出现在 Git。

### AC-011

Tool 调用失败时不会生成虚假业务结果。

### AC-012

核心功能具备自动化测试。

---

# 28. Development Principles

项目开发遵循：

> **先跑通，再完善；先简单，再复杂。**

优先级：

```text
正确性
 ↓
安全性
 ↓
可维护性
 ↓
可测试性
 ↓
性能
 ↓
复杂功能
```

不要为了使用 AI 技术而使用 AI 技术。

所有 Agent、LangGraph、MCP 等技术必须由实际业务需求驱动。

---

# 29. MVP Definition

当以下流程能够稳定运行：

```text
用户
 ↓
自然语言
 ↓
FastAPI
 ↓
LLM
 ↓
RAG / Tool
 ↓
WMS API
 ↓
真实业务数据
 ↓
LLM
 ↓
自然语言回答
```

即可认为 MVP 达成。

---

# 30. Current Priority

当前只关注：

```text
1. 项目初始化
2. FastAPI
3. LLM API
4. 基础 Chat
5. RAG
6. Tool Calling
7. WMS API
```

暂时不要实现：

```text
Agent
LangGraph
MCP
Multi-Agent
AI自动写操作
私有化部署
```

等 MVP 稳定后再进入下一阶段。
