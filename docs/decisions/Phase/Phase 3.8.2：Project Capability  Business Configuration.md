你现在只执行 **Phase 3.8.2：Project Capability / Business Configuration**。

## 一、目标

在 Phase 3.8.1 已经实现：

```text
project_id
  ↓
ProjectRegistry
  ↓
ProjectContext
  ↓
DataSource
  ↓
DatabaseEngine
```

的基础上，继续解决：

> 当前不同 Project 虽然可以切换数据源，但可用业务能力仍然基本固定。

本阶段要实现：

```text
Project
 ├── DataSource
 ├── Semantic
 ├── Tools
 └── Knowledge
```

最终让 Orchestrator 能够根据当前 `ProjectContext` 获取该项目允许使用的业务能力。

---

# 二、严格范围

本阶段只做：

**Project → Capability 配置与解析。**

必须保持现有：

* AI Router
* AI Orchestrator
* RAG
* Reranker
* Text-to-SQL
* SQL Validator
* SQL Executor
* Tool Framework
* ProjectRegistry
* DatabaseEngineProvider

正常工作。

禁止：

1. 不做 Agent
2. 不做 LangGraph
3. 不做 Multi-Agent
4. 不做 Memory
5. 不做 RBAC
6. 不做用户权限
7. 不做租户系统
8. 不做 MCP
9. 不做 Streaming
10. 不做前端
11. 不做新的数据库表
12. 不做数据库迁移
13. 不重写 Tool Framework
14. 不重写 Semantic Layer
15. 不把所有现有代码大规模重构
16. 不实现动态 Python 代码加载
17. 不允许 HTTP 请求直接传 Tool handler / SQL / connection / filesystem path

如果发现必须扩大范围：

**停止并报告，不要自行继续。**

---

# 三、先阅读现有实现

先不要修改代码。

重点阅读：

```text
backend/app/projects/
backend/app/tools/
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py

backend/app/projects/semantic.py
backend/app/projects/semantic_loader.py
backend/app/projects/semantic/
backend/app/config.py

tests/
docs/decisions/
```

重点确认：

1. 当前 ProjectRegistry
2. 当前 ProjectContext
3. 当前 ToolRegistry
4. ToolCapabilityRegistry
5. ToolRegistryCapabilityAdapter
6. 当前 Semantic 配置
7. 当前 RAG Knowledge 配置
8. Orchestrator 如何获取 ToolRegistry
9. Router 如何获得 Tool Capability
10. `get_inventory` 当前如何注册

**第一步只阅读并报告现状，不要直接改代码。**

---

# 四、核心设计

新增一个项目级能力模型。

建议：

```python
@dataclass(frozen=True)
class ProjectCapabilities:
    tool_names: tuple[str, ...]
    knowledge_enabled: bool
    text_to_sql_enabled: bool
```

如果现有项目模型结构更适合，可以采用等价设计。

然后：

```python
@dataclass(frozen=True)
class ProjectRegistration:
    context: ProjectContext
    schema_name: str
    capabilities: ProjectCapabilities
```

注意：

**不要破坏 Phase 3.8.1 已有的 ProjectRegistration API。**

如果修改字段导致大量旧代码变化，应采用兼容方式。

---

# 五、Capability 的职责

Capability 只描述：

> 当前 Project 可以使用什么能力。

例如：

```text
vietnam-wms

Tools:
- get_inventory
- get_work_order

Knowledge:
enabled

Text-to-SQL:
enabled
```

另一个项目：

```text
sales-demo

Tools:
- get_inventory

Knowledge:
enabled

Text-to-SQL:
disabled
```

Capability 不负责执行。

不要在 Capability 中出现：

```text
handler
engine
session
llm
password
api_key
connection_string
```

---

# 六、Tool Capability

复用当前 ToolRegistry。

不要创建第二套 Tool Registry。

例如：

```text
Global Tool Registry
        ↓
Project Capability
        ↓
allowed tool names
        ↓
ToolRegistry
```

项目只声明：

```text
tool_names = ("get_inventory",)
```

真正执行仍然：

```text
ToolRegistry.execute(...)
```

---

# 七、Tool 隔离

必须实现：

```text
Project A:
tools = ["get_inventory"]

Project B:
tools = []
```

那么：

```text
Project A
→ get_inventory
→ 允许

Project B
→ get_inventory
→ 不允许
```

这里必须区分：

### Router 能看到的 Tool

Router 的 Capability Registry 也必须根据 Project 过滤。

否则会出现：

```text
Project B
 ↓
Router
 ↓
发现 get_inventory
 ↓
Tool route
 ↓
Orchestrator
 ↓
才发现 Tool 不允许
```

这种设计不够干净。

正确方式：

```text
project_id
 ↓
ProjectCapabilities
 ↓
filtered ToolCapabilities
 ↓
Router
```

因此 Router 应该只看到当前 Project 可用的 Tools。

---

# 八、RAG Capability

增加：

```text
knowledge_enabled
```

默认：

```text
true
```

如果：

```text
knowledge_enabled=false
```

则当前 Project 不允许走 RAG。

但：

**不要在本阶段实现 Project 独立 Knowledge Database。**

现有全局 Knowledge Store 保持不变。

也就是说：

```text
Project Capability
       ↓
是否允许使用 RAG
```

而不是：

```text
Project Capability
       ↓
创建新的 Knowledge DB
```

---

# 九、Text-to-SQL Capability

增加：

```text
text_to_sql_enabled
```

默认：

```text
true
```

如果：

```text
false
```

那么：

```text
Project
 ↓
Router / Orchestrator
 ↓
TEXT_TO_SQL
 ↓
拒绝
```

必须返回清晰的业务错误。

不要执行：

* Schema Explorer
* TextToSQL
* SQL Executor

即：

```text
能力禁用
→ 在执行前拦截
→ 不访问数据库
```

---

# 十、Router 集成

Router 本身不应该知道：

```text
vietnam-wms
sales-demo
```

等具体项目名称。

Router 只接受：

```text
available_tools
knowledge_enabled
text_to_sql_enabled
```

或者等价的 Project Capability Context。

例如：

```python
router.route(
    question,
    context=ProjectCapabilityContext(...)
)
```

保持 Router 通用。

---

# 十一、Orchestrator 集成

Orchestrator：

```text
project_id
 ↓
ProjectContext
 ↓
ProjectCapabilities
 ↓
Router
 ↓
Capability-aware route
```

执行时再次做硬校验。

例如：

```text
Router → TEXT_TO_SQL
```

但：

```text
project.text_to_sql_enabled = false
```

则 Orchestrator 必须拒绝。

原因：

> Router 是分类器，不能作为安全边界。

最终安全边界仍然是 Orchestrator。

---

# 十二、配置方式

优先采用现有项目代码级 Registry。

例如：

```python
InMemoryProjectRegistry(
    registrations={
        "vietnam-wms": ProjectRegistration(...),
        "sales-demo": ProjectRegistration(...),
    }
)
```

本阶段不要引入：

* 配置中心
* Redis
* 数据库配置表
* 动态代码加载
* YAML 热加载

如果未来需要，可以在后续阶段做。

---

# 十三、默认项目

必须保持：

```text
vietnam-wms
```

的现有行为不变。

默认能力建议等价于当前系统：

```text
Tools:
get_inventory

Knowledge:
enabled

Text-to-SQL:
enabled
```

如果当前还有其他正式 Tool，则根据实际 Registry 自动处理，不要遗漏。

---

# 十四、测试

至少新增以下测试。

## 14.1 Project Capability

验证：

```text
known project → capabilities
unknown project → ProjectNotFoundError
```

---

## 14.2 Tool filtering

Project A：

```text
tools = ["get_inventory"]
```

Project B：

```text
tools = []
```

验证：

```text
A → Router 不为空
B → Router 看不到 get_inventory
```

---

## 14.3 Tool execution isolation

验证：

```text
A + get_inventory → 成功
B + get_inventory → capability denied
```

注意：

**B 不应该执行 SQL。**

---

## 14.4 RAG capability

```text
knowledge_enabled=true
```

验证正常 RAG。

然后：

```text
knowledge_enabled=false
```

验证：

```text
RAG route → capability denied
RagService → 0 calls
```

---

## 14.5 Text-to-SQL capability

```text
text_to_sql_enabled=true
```

验证正常。

然后：

```text
false
```

验证：

```text
TEXT_TO_SQL route
→ capability denied
→ TextToSQLService = 0
→ SQLExecutor = 0
```

---

# 十五、API E2E

使用：

```http
POST /api/ai/chat
```

测试至少两个项目。

例如：

### Project A

```json
{
  "project_id": "project-a",
  "question": "查询物料 10001 当前库存"
}
```

能力：

```text
get_inventory = enabled
```

应该：

```text
route = tool
```

---

### Project B

```json
{
  "project_id": "project-b",
  "question": "查询物料 10001 当前库存"
}
```

能力：

```text
get_inventory = disabled
```

应该：

```text
Tool capability denied
```

并且：

```text
DB query = 0
```

---

# 十六、Router 回归

重点验证：

```text
Project A
```

原来的：

```text
知识问题 → RAG
库存查询 → TOOL
数据分析 → TEXT_TO_SQL
```

继续成立。

Project B 禁用某能力后：

```text
该能力不应该继续被 Router 选择。
```

---

# 十七、安全边界

重点测试：

用户不能通过：

```json
{
  "project_id": "project-b",
  "tool_names": ["get_inventory"]
}
```

强行打开 Tool。

也不能：

```json
{
  "project_id": "project-b",
  "text_to_sql_enabled": true
}
```

强行打开 Text-to-SQL。

HTTP 请求中的这些字段如果不存在就不要新增。

项目能力只能由：

```text
服务器端 ProjectRegistry
```

决定。

---

# 十八、不要修改数据库

本阶段：

```text
DB migration = 0
```

不增加任何业务表。

测试可以继续使用：

* Fake Tool
* Fake RAG
* Fake Generator
* Test schema

---

# 十九、文档

新增：

```text
docs/decisions/Phase/Phase 3.8.2：Project Capability Business Configuration.md
```

记录：

1. 为什么需要 Project Capability
2. ProjectCapabilities 设计
3. Tool Capability
4. Knowledge Capability
5. Text-to-SQL Capability
6. Router 过滤
7. Orchestrator 二次校验
8. API 行为
9. 安全边界
10. 测试结果
11. 已知限制

---

# 二十、验收标准

最终架构必须能够表达：

```text
Project A
 ├── DataSource A
 ├── Tool: get_inventory
 ├── Knowledge: enabled
 └── Text-to-SQL: enabled
```

以及：

```text
Project B
 ├── DataSource B
 ├── Tool: none
 ├── Knowledge: enabled
 └── Text-to-SQL: disabled
```

并且：

```text
Project B
 ↓
不会因为 Router / API / 用户输入
强行获得 Project A 的 Tool 或 Text-to-SQL 能力。
```

运行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

如果相关环境支持，再执行：

```bash
POST /api/ai/chat
```

API E2E。

最后：

```bash
python -m compileall backend
```

并检查 LSP / lint。

---

# 二十一、完成后立即停止

Phase 3.8.2 完成后：

**立即停止。**

不要继续：

* Phase 3.8.3
* Agent
* LangGraph
* Memory
* RBAC
* Multi-Agent
* MCP
* Streaming
* 前端

最终只报告：

```text
1. 修改文件
2. 新增文件
3. ProjectCapabilities 如何实现
4. Tool 如何按 Project 过滤
5. RAG capability 如何控制
6. Text-to-SQL capability 如何控制
7. Router 如何感知 Project Capability
8. Orchestrator 如何进行二次校验
9. API E2E 结果
10. pytest 结果
11. DB pytest 结果
12. 安全测试结果
13. 已知限制
```

**报告完成后停止，等待下一步指令。**
