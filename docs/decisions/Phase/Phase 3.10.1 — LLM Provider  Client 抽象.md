# Phase 3.10.1 — LLM Provider / Client 抽象

## 一、目标

在现有 `wms-ai-assistant` 项目中，为 LLM 调用增加最小化的 Provider 抽象。

本阶段只解决：

> AI Core 不应该直接依赖 DeepSeek；DeepSeek 应该只是一个具体的 LLM Provider 实现。

目标结构：

```text
AI Core
   ↓
LLMProvider
   ↓
DeepSeekProvider
   ↓
现有 DeepSeek OpenAI-compatible Client
```

未来可以替换为 OpenAI、Qwen、其他 OpenAI-compatible 模型，但本阶段不要实现这些 Provider。

---

## 二、必须先阅读

开始修改前，先阅读并理解：

1. 当前 LLM Client 实现
2. `TextToSQLService`
3. `AIOrchestratorService`
4. 当前 Chat/LLM 调用链
5. 现有 LLM 相关 tests
6. 当前项目配置/settings
7. `backend/app` 目录结构

先判断当前项目中 DeepSeek Client 的真实调用方式。

不要假设文件路径。

---

## 三、本阶段允许修改

只允许修改与 LLM Provider 抽象直接相关的代码和测试。

推荐结构：

```text
backend/app/llm/
├── __init__.py
├── provider.py
└── deepseek_provider.py
```

如果当前项目已经存在合理的 `llm` / `providers` 目录，可以复用，不要重复创建。

可以增加：

```text
tests/test_llm_provider.py
```

以及必要的已有 LLM 单元测试调整。

---

## 四、核心接口

设计一个最小的 `LLMProvider` 抽象。

接口至少能够表达：

```python
generate(...)
```

或者项目当前调用方式对应的等价最小接口。

要求：

* Provider 是 AI Core 面向的抽象
* Provider 不应该暴露 DeepSeek 专属概念
* Provider 返回稳定、简单的结果
* 不把 HTTP/OpenAI SDK 类型泄漏到 AI Core
* 不把 API Key 放进 Provider 接口
* 不把 Project Context 放进 LLM Provider
* 不把 RAG / Tool / Text-to-SQL 逻辑放进 Provider

---

## 五、DeepSeek Provider

增加具体：

```text
DeepSeekProvider
```

要求：

* 内部复用现有 DeepSeek Client
* 不重复实现一套 HTTP Client
* 不改变现有 DeepSeek API 配置方式
* 不改变现有 `.env` / settings 配置语义
* 不输出 API Key
* 不修改 DeepSeek endpoint
* 不修改当前默认 model
* 不改变现有生产行为

如果当前项目已有 DeepSeek Client：

```text
DeepSeekProvider
       ↓
Existing DeepSeek Client
```

而不是：

```text
DeepSeekProvider
       ↓
重新创建一个 DeepSeek HTTP Client
```

---

## 六、Dependency Injection

让需要 LLM 的核心服务可以依赖：

```text
LLMProvider
```

而不是直接依赖：

```text
DeepSeekClient
```

优先处理当前最核心的 LLM 使用点。

尤其检查：

* `TextToSQLService`
* RAG 中的 LLM 调用
* Chat 中的 LLM 调用
* Orchestrator 中是否存在直接 DeepSeek 依赖

但是：

### 不要在本阶段大规模重构整个项目。

如果一次性修改多个调用链风险较高，只完成最小、安全的 Provider 注入路径，并记录剩余直接依赖。

---

## 七、兼容性要求

这是本阶段最重要的约束。

必须保持：

### Text-to-SQL

原来的：

```text
Question
→ TextToSQLService
→ DeepSeek
→ SQL
→ Validator
```

行为不变。

### RAG

原有 RAG 行为不变。

### Tool

Tool Calling 行为不变。

### Router

Router 行为不变。

### Project Context

Project Context 行为不变。

### Validator / Executor

绝对不要修改。

---

## 八、测试要求

至少增加以下测试。

### 1. Provider interface

验证具体 Provider 可以满足抽象接口。

### 2. DeepSeek Provider delegation

使用 fake/mock client：

```text
DeepSeekProvider
      ↓
Fake DeepSeek Client
```

验证：

* 参数正确传递
* 返回结果正确转换
* 不发生真实网络请求

### 3. Provider injection

验证核心服务可以接收：

```text
LLMProvider
```

而不是强绑定：

```text
DeepSeekClient
```

### 4. Existing behavior regression

运行现有 LLM / Text-to-SQL / RAG 相关测试。

不能因为 Provider 抽象导致现有测试失败。

### 5. Fake Provider

增加一个极小的：

```text
FakeLLMProvider
```

只用于测试。

不要把 Fake Provider 放进 production runtime。

---

## 九、不要做的事情

本阶段禁止：

* ❌ 修改 Prompt 内容
* ❌ 修改 Prompt v1
* ❌ 修改 Prompt v2
* ❌ 修改 SQL Validator
* ❌ 修改 SQL Executor
* ❌ 修改 Text-to-SQL Dataset
* ❌ 修改 Ground Truth
* ❌ 修改 Evaluation Baseline
* ❌ 修改 RAG 检索算法
* ❌ 增加 Hybrid Search
* ❌ 增加 Streaming
* ❌ 增加 Token Cost Tracking
* ❌ 增加 Retry / Backoff Framework
* ❌ 增加 Fallback Model
* ❌ 增加 Model Router
* ❌ 增加 Agent Loop
* ❌ 修改 Project Context
* ❌ 修改 Tool Registry
* ❌ 修改 API response contract
* ❌ 修改 production prompt version
* ❌ 把 v2 Prompt 切换成 production default

特别注意：

> 本阶段是架构抽象，不是功能扩展。

---

## 十、配置要求

如果当前系统通过 settings 获取：

```text
LLM provider
LLM model
API key
base URL
```

可以保留现有配置。

但是不要在本阶段设计复杂的动态 Provider 配置系统。

可以暂时保持：

```text
provider = deepseek
```

由 application composition/root 层决定实例：

```text
DeepSeekProvider(...)
```

未来再增加 Provider Factory / Registry。

---

## 十一、验证

完成后执行：

```text
pytest
python -m compileall backend tests scripts
```

如果项目已有专门的 LLM smoke test，可以运行。

但是：

### 不要求真实 DeepSeek 网络调用

本阶段重点是：

```text
Abstraction
DI
Delegation
Regression
```

不是模型效果评测。

---

## 十二、输出要求

完成后只报告：

### 1. 实际修改文件

列出：

```text
Modified:
...

Added:
...
```

### 2. Provider 架构

用简短结构说明：

```text
AI Core
  ↓
LLMProvider
  ↓
DeepSeekProvider
  ↓
Existing DeepSeek Client
```

### 3. Dependency Injection

说明哪些核心服务已经从：

```text
DeepSeek Client
```

变成：

```text
LLMProvider
```

哪些暂时没有修改。

### 4. Tests

报告：

```text
pytest:
compileall:
```

### 5. Regression

确认：

```text
Text-to-SQL:
RAG:
Tool:
Router:
API:
```

哪些通过。

### 6. 未完成项

如果存在直接依赖 DeepSeek Client 的地方，明确列出。

---

# 十三、强制 STOP

完成 Phase 3.10.1 后：

**立即停止。**

不要自动进入：

* Phase 3.10.2
* Streaming
* Retry
* Fallback
* Cost Tracking
* Model Router
* 其他任何功能

不要自行继续设计下一阶段。

只返回本阶段结果，等待下一步指令。
