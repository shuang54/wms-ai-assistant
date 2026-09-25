# Phase 3.8.6：Project Configuration Aggregation

## 一、阶段目标

在 Phase 3.8.1 ～ 3.8.5 已完成：

* Project DataSource
* Project Capability
* Project Semantic
* Project Knowledge Scope
* Project Knowledge Ingestion

之后，本阶段只解决：

> **统一项目配置的装配关系，避免 ProjectRegistry、SemanticProvider、KnowledgeProvider 等多个 Provider 彼此独立配置后出现项目配置漂移。**

目标架构：

```text
                    ┌── DataSource
                    │
Project ID ─────────┼── Capability
                    │
                    ├── Semantic
                    │
                    └── Knowledge Scope
                              ↓
                       ProjectConfiguration
                              ↓
                       Project Orchestrator
```

最终希望形成：

```text
project_id
    ↓
统一 Project Configuration
    ├── ProjectContext / DataSource
    ├── Capabilities
    ├── Semantic
    └── Knowledge Scope
    ↓
Factory
    ↓
AIOrchestrator
```

---

# 二、重要原则

这是一个**架构收敛阶段**，不是功能扩张阶段。

严格遵守：

> 最小修改、保持现有行为。

禁止：

* LangGraph
* Agent
* Memory
* 多轮会话
* MCP
* 新 Tool
* 新 RAG 能力
* Text-to-SQL 改造
* Knowledge ORM 改造
* Database Migration
* 微服务
* 配置中心
* Redis
* MQ
* 权限系统
* RBAC
* 前端
* 新 HTTP API

本阶段只统一 Project 配置读取和装配。

---

# 三、开始前先侦察

先不要修改代码。

阅读：

```text
backend/app/projects/registry.py
backend/app/projects/context.py
backend/app/projects/capabilities.py
backend/app/projects/semantic_provider.py
backend/app/projects/knowledge_provider.py
backend/app/services/project_orchestrator_factory.py
backend/app/services/ai_orchestrator_service.py
```

同时检查：

```text
tests/test_project_*.py
```

重点回答：

1. 当前 ProjectRegistry 保存什么；
2. Capability 从哪里读取；
3. SemanticProvider 从哪里读取；
4. KnowledgeProvider 从哪里读取；
5. Factory 当前需要分别调用哪些 Provider；
6. 是否存在同一个 project_id 在不同 Provider 注册状态不一致的情况；
7. 当前哪些 Provider 是可选的；
8. 哪些 Provider 是项目运行必需的；
9. 当前默认 vietnam-wms 行为是什么；
10. 是否存在重复的 project_id 校验/不存在处理。

先输出：

```text
Project configuration reconnaissance complete.

Current ProjectRegistry:
Current Capability source:
Current Semantic source:
Current Knowledge source:
Current Factory assembly:
Default vietnam-wms behavior:
Potential configuration drift:
Recommended minimal aggregation:
```

**侦察结束后再实施。**

---

# 四、核心设计

新增一个统一的项目配置 DTO。

建议：

```python
@dataclass(frozen=True)
class ProjectConfiguration:
    context: ProjectContext
    capabilities: ProjectCapabilities
    semantic: ProjectSemantic
    knowledge_scope: ProjectKnowledgeScope
```

但必须根据现有 DTO 的真实字段和可选性调整。

要求：

* frozen；
* 不包含密码；
* 不包含 DB URL；
* 不包含 API Key；
* 不包含 LLM secret；
* 不包含 engine/session；
* 不包含 Service；
* 不包含 Provider；
* 只保存项目运行所需要的**配置结果**。

---

# 五、Provider 不是 Configuration

注意区分：

```text
Provider = 如何获取配置

Configuration = 已解析的项目配置
```

不要把：

```python
ProjectRegistry
ProjectSemanticProvider
ProjectKnowledgeProvider
```

全部塞进 DTO。

错误：

```python
ProjectConfiguration(
    registry=...,
    semantic_provider=...,
    knowledge_provider=...
)
```

正确：

```text
Provider
   ↓
Configuration
   ↓
Factory
```

---

# 六、统一 Configuration Provider

建议新增：

```text
backend/app/projects/configuration.py
```

或者根据现有项目结构选择更合理的文件名。

定义：

```python
class ProjectConfigurationProvider(Protocol):
    def get(self, project_id: str) -> ProjectConfiguration:
        ...
```

然后实现：

```python
class DefaultProjectConfigurationProvider:
    ...
```

它负责：

```text
project_id
    ↓
ProjectRegistry.get(project_id)
    ↓
Capabilities
    ↓
SemanticProvider.get(project_id)
    ↓
KnowledgeProvider.get_scope(project_id)
    ↓
ProjectConfiguration
```

---

# 七、关键：配置读取顺序

推荐：

```text
1. ProjectRegistry
2. Capability
3. Semantic
4. Knowledge Scope
5. assemble ProjectConfiguration
```

如果 ProjectRegistry 不存在：

```text
ProjectNotFoundError
```

立即失败。

不能继续向 SemanticProvider / KnowledgeProvider 查询。

这样可以避免：

```text
unknown project
    ↓
semantic provider accidentally has config
    ↓
knowledge provider accidentally has config
    ↓
partial project
```

---

# 八、ProjectRegistry 是项目存在性的权威来源

本阶段明确：

> ProjectRegistry 是判断 project_id 是否存在的唯一权威来源。

SemanticProvider / KnowledgeProvider：

```text
project-a 未注册
```

不能因为它们自己有：

```text
project-a
```

就让项目存在。

因此：

```text
ProjectRegistry
        ↓
project exists?
     /       \
   no         yes
   ↓           ↓
 404        load other config
```

---

# 九、默认 Provider 兼容行为

必须保持当前：

```text
vietnam-wms
```

行为完全不变。

也就是说：

### DataSource

继续使用当前 primary datasource。

### Capability

保持当前：

```text
get_inventory
knowledge_enabled=True
text_to_sql_enabled=True
```

### Semantic

继续读取当前默认 semantic。

### Knowledge

继续：

```text
namespace = vietnam-wms
includes_global = True
includes_legacy = True
```

不要因为 Aggregation 改造导致默认项目行为变化。

---

# 十、Factory 改造

当前：

```text
Factory
 ├── Registry
 ├── SemanticProvider
 ├── KnowledgeProvider
 └── Capability
```

如果代码确实如此，则收敛成：

```text
Factory
    ↓
ProjectConfigurationProvider
    ↓
ProjectConfiguration
    ↓
Orchestrator
```

Factory 不再分别负责：

```python
registry.get()
semantic_provider.get()
knowledge_provider.get_scope()
```

而是：

```python
configuration = configuration_provider.get(project_id)
```

然后：

```text
configuration.context
configuration.capabilities
configuration.semantic
configuration.knowledge_scope
```

用于组装 Orchestrator。

---

# 十一、不要过度改造 AIOrchestrator

AIOrchestrator 当前已经接受：

```text
capabilities
knowledge_scope
ProjectContextProvider
```

本阶段：

**不要把整个 ProjectConfiguration 强行注入 Orchestrator。**

保持 AIOrchestrator 目前的职责边界。

推荐：

```text
ProjectConfiguration
        ↓
Factory
        ↓
已有 Orchestrator 参数
```

而不是：

```text
ProjectConfiguration
        ↓
Orchestrator.project_configuration
```

避免 Core 层绑定 Project 配置聚合对象。

---

# 十二、Semantic 的处理

当前 SemanticProvider 已经存在。

Aggregation 只负责：

```python
semantic = semantic_provider.get(project_id)
```

然后放入：

```python
ProjectConfiguration.semantic
```

不要修改：

* SemanticLoader
* SemanticSerializer
* ContextComposer
* RelevantTableSelector

不要增加自动推断。

---

# 十三、Knowledge 的处理

当前：

```python
knowledge_scope = knowledge_provider.get_scope(project_id)
```

继续复用。

不要重新实现：

```text
project-a
project-b
vietnam-wms
__global__
legacy
```

的判断。

这些规则仍然属于：

```text
ProjectKnowledgeProvider
```

Configuration 只是保存结果。

---

# 十四、Capability 的处理

当前 ProjectRegistry 已经拥有：

```python
ProjectRegistration.capabilities
```

如果当前实现允许直接读取：

```python
registration.capabilities
```

则统一从 Registry 得到。

不要新增：

```text
CapabilityProvider
```

除非侦察发现现有架构确实需要。

本阶段不要为了“对称”而创造新的 Provider。

---

# 十五、配置一致性

增加测试：

```text
tests/test_project_configuration.py
```

至少覆盖：

### Case 1

project-a：

```text
context.project_id = project-a
capabilities = A
semantic.project_id = project-a
knowledge_scope.project_id = project-a
knowledge_scope.namespace = project-a
```

四者必须一致。

---

### Case 2

project-b：

同样验证：

```text
project-b
```

---

### Case 3

vietnam-wms：

验证：

```text
context.project_id = vietnam-wms
semantic.project_id = vietnam-wms
knowledge_scope.project_id = vietnam-wms
knowledge_scope.namespace = vietnam-wms
includes_legacy = True
```

---

### Case 4：Unknown Project

```text
project-x
```

必须：

```text
ProjectNotFoundError
```

并且：

```text
SemanticProvider = 0 calls
KnowledgeProvider = 0 calls
```

---

### Case 5：Semantic 缺失

ProjectRegistry 有：

```text
project-a
```

但 SemanticProvider 没有：

```text
project-a
```

应该明确失败。

不能 fallback：

```text
vietnam-wms
```

---

### Case 6：Knowledge 缺失

同理：

```text
project-a
```

KnowledgeProvider 没有配置：

明确失败。

不能 fallback。

---

# 十六、Configuration Provider 测试

必须使用 Fake Providers 验证调用顺序和调用次数。

例如：

```text
unknown project
    Registry = 1
    Semantic = 0
    Knowledge = 0
```

正常：

```text
Registry = 1
Semantic = 1
Knowledge = 1
```

不要出现：

```text
Semantic = 2
Knowledge = 2
```

---

# 十七、Factory E2E

验证：

```text
project-a
    ↓
Factory
    ↓
ProjectConfiguration
    ↓
Orchestrator
```

最终：

### RAG

使用：

```text
knowledge_scope = project-a
```

### Tool

只加载：

```text
project-a capabilities.tool_names
```

### Text-to-SQL

使用：

```text
project-a context
project-a semantic
project-a datasource
```

---

# 十八、最关键的跨项目 E2E

保持现有 Phase 3.8.1～3.8.5 测试全部通过。

至少验证：

```text
project-a
    DataSource = A
    Semantic = A
    Knowledge = A
    Capability = A

project-b
    DataSource = B
    Semantic = B
    Knowledge = B
    Capability = B
```

同一个问题：

```text
系统的操作流程是什么
```

分别进入：

```text
project-a
project-b
```

必须继续得到对应 Context。

---

# 十九、安全测试

验证 HTTP 层不能注入：

```text
project_configuration
capabilities
semantic
knowledge_scope
datasource
```

如果 API request model 收到未知字段：

```json
{
  "project_id": "project-a",
  "knowledge_scope": "project-b",
  "semantic": "project-b",
  "datasource": "project-b"
}
```

最终仍然：

```text
project-a configuration
```

由服务器端 Provider 生成。

---

# 二十、禁止配置 fallback

以下全部禁止：

```text
project-a → missing semantic → vietnam-wms semantic
project-a → missing knowledge → global
project-a → missing capability → default capability
project-x → missing registry → vietnam-wms
```

配置缺失应该：

```text
clear error
```

而不是静默 fallback。

---

# 二十一、兼容现有测试

当前基线：

```text
1281 passed / 237 skipped
DB: 1480 passed / 38 skipped
```

本阶段完成后必须运行：

```powershell
python -m pytest -q
```

以及：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

还要运行：

```powershell
python -m compileall backend
```

以及现有 lint。

---

# 二十二、文件修改控制

优先：

新增：

```text
backend/app/projects/configuration.py
tests/test_project_configuration.py
docs/decisions/Phase/Phase 3.8.6：Project Configuration Aggregation 实施 ADR.md
```

必要时修改：

```text
backend/app/services/project_orchestrator_factory.py
```

以及确实需要的少量项目配置代码。

不要为了统一架构大面积重构。

---

# 二十三、完成后最终汇报

严格使用以下结构：

```text
Phase 3.8.6 完成汇报

1. 修改文件

2. 新增文件

3. Project Configuration 设计

4. Provider → Configuration 装配链路

5. ProjectRegistry 权威性

6. DataSource / Capability / Semantic / Knowledge 一致性

7. Factory E2E

8. 跨项目隔离测试

9. pytest

10. DB pytest

11. 安全测试

12. 已知限制

13. 本阶段停止
```

最后必须明确：

```text
Phase 3.8.6 到此停止。
不进入 Phase 3.8.7。
等待下一条指令。
```

不要自行继续下一阶段。
