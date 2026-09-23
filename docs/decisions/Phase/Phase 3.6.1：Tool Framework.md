你现在开始实现项目 **Phase 3.6.1：Tool Framework**。

项目目录：

`D:\coding\ai\wms-ai-assistant`

## 一、本阶段目标

为现有 WMS AI Assistant 增加一个最小、可扩展的 Tool Framework。

本阶段只解决：

```text
Tool 定义
Tool 注册
Tool 参数校验
Tool 查找
Tool 执行
Tool 执行结果标准化
```

最终形成：

```text
ToolRegistry
    ↓
ToolDefinition
    ↓
ToolHandler
    ↓
ToolResult
```

**暂时不要让 LLM 自动调用 Tool。**

下一阶段才做：

```text
用户问题
↓
LLM
↓
Tool Calling
↓
ToolRegistry
↓
Tool
```

---

# 二、严格禁止扩大范围

本阶段禁止：

* 不接真实 WMS API
* 不接真实 ERP API
* 不连接业务数据库执行查询
* 不写 SQL Tool
* 不修改现有 RAG Pipeline
* 不修改 `/api/chat`
* 不修改 `/api/rag/answer`
* 不做 Agent
* 不做 LangGraph
* 不做 MCP
* 不做自动 Tool Calling
* 不做前端
* 不做权限系统
* 不做 Tool 持久化
* 不做异步任务队列
* 不做生产级 Tool 沙箱

只实现一个**内存中的最小 Tool Framework**。

遵守项目 `AGENTS.md` 中已有架构规范。

---

# 三、先阅读现有代码

实现前必须先阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md

backend/app/config.py

backend/app/services/rag_service.py
backend/app/services/chat_service.py

backend/app/api/chat.py
backend/app/api/rag.py

现有 tests/
```

特别确认现有代码的：

* DTO 风格
* frozen dataclass 使用方式
* exception 层级
* service 结构
* test 风格
* async / sync 使用方式

不要重复创建已有的通用基础设施。

---

# 四、建议目录

新增：

```text
backend/app/tools/
├── __init__.py
├── base.py
├── registry.py
└── errors.py

tests/
└── test_tool_framework.py
```

如果现有项目结构有更合理的位置，可以保持项目现有架构，但不要无必要增加目录。

---

# 五、ToolDefinition

设计一个不可变的 ToolDefinition。

建议包含：

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
```

要求：

### name

工具唯一名称，例如：

```text
get_inventory
```

要求：

* 非空
* 建议只允许：

  * 字母
  * 数字
  * `_`
  * `-`
* 不允许重复注册

### description

工具描述。

不能为空。

### parameters

使用 JSON Schema 风格描述参数。

例如：

```python
{
    "type": "object",
    "properties": {
        "material_code": {
            "type": "string",
            "description": "物料编码"
        }
    },
    "required": ["material_code"]
}
```

注意：

本阶段只是保存 Tool Schema。

不要自己实现完整 JSON Schema 引擎。

---

# 六、ToolHandler

定义 Tool Handler 接口。

可以采用：

```python
Protocol
```

例如：

```python
class ToolHandler(Protocol):
    async def __call__(
        self,
        arguments: dict[str, Any],
    ) -> Any:
        ...
```

具体实现不需要复杂。

---

# 七、ToolResult

定义统一的 Tool 执行结果。

建议：

```python
@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    success: bool
    data: Any | None = None
    error: str | None = None
```

要求：

成功：

```python
ToolResult(
    tool_name="get_inventory",
    success=True,
    data={
        ...
    },
)
```

失败：

```python
ToolResult(
    tool_name="get_inventory",
    success=False,
    error="material_code is required",
)
```

不要把 traceback 放入 ToolResult。

不要返回：

* API Key
* 数据库连接字符串
* SQL
* embedding
* 内部异常 traceback

---

# 八、ToolRegistry

实现：

```python
class ToolRegistry:
    ...
```

至少提供：

```python
register(...)
unregister(...)
get(...)
list_tools(...)
execute(...)
```

行为要求：

### register

注册 Tool。

重复 name：

```text
ToolAlreadyRegisteredError
```

---

### get

根据：

```python
tool_name
```

查找 Tool。

不存在：

```text
ToolNotFoundError
```

---

### unregister

删除 Tool。

不存在：

```text
ToolNotFoundError
```

---

### list_tools

返回已经注册的 ToolDefinition。

要求：

* 不返回内部 handler
* 返回稳定顺序
* 不暴露敏感信息

---

### execute

调用：

```python
registry.execute(
    "get_inventory",
    {
        "material_code": "MAT001"
    }
)
```

执行对应 Handler。

需要处理：

```text
Tool 不存在
参数非法
Handler 执行异常
```

不要吞掉无法识别的系统错误。

---

# 九、参数校验

本阶段做**最小参数校验**。

至少支持：

```text
object
required
string
integer
number
boolean
array
```

例如：

```python
{
    "type": "object",
    "properties": {
        "material_code": {
            "type": "string"
        },
        "warehouse_code": {
            "type": "string"
        }
    },
    "required": ["material_code"]
}
```

下面应该失败：

```python
{}
```

下面应该成功：

```python
{
    "material_code": "MAT001"
}
```

不要实现完整 JSON Schema。

如果项目已有 Pydantic 可以合理复用，但不要为了这一阶段引入额外大型依赖。

---

# 十、异常设计

至少定义：

```python
ToolError
ToolAlreadyRegisteredError
ToolNotFoundError
ToolValidationError
ToolExecutionError
```

继承关系：

```text
ToolError
├── ToolAlreadyRegisteredError
├── ToolNotFoundError
├── ToolValidationError
└── ToolExecutionError
```

Tool Framework 内部异常不要直接暴露底层 traceback。

---

# 十一、实现两个 Mock Tool

为了验证 Framework，创建两个最简单的测试 Tool。

不要创建真实 WMS Tool。

### Tool 1：get_inventory

Schema：

```json
{
  "type": "object",
  "properties": {
    "material_code": {
      "type": "string"
    },
    "warehouse_code": {
      "type": "string"
    }
  },
  "required": ["material_code"]
}
```

Mock 返回：

```python
{
    "material_code": "...",
    "warehouse_code": "...",
    "quantity": 1000,
    "unit": "PCS"
}
```

---

### Tool 2：get_work_order

Schema：

```json
{
  "type": "object",
  "properties": {
    "work_order_no": {
      "type": "string"
    }
  },
  "required": ["work_order_no"]
}
```

Mock 返回：

```python
{
    "work_order_no": "...",
    "status": "RELEASED"
}
```

注意：

这两个 Mock Tool 只是测试 Tool Framework。

不要接 PostgreSQL。

不要调用 WMS。

不要调用 ERP。

---

# 十二、测试要求

新增：

```text
tests/test_tool_framework.py
```

至少覆盖：

### Registry

* 注册 Tool
* 获取 Tool
* 列出 Tool
* 删除 Tool
* 重复注册
* 获取不存在 Tool
* 删除不存在 Tool

### 参数

* required 缺失
* string 类型正确
* string 类型错误
* integer 类型正确
* boolean 类型错误
* 未知参数的行为明确

### Execute

* 正常执行
* Tool 参数错误
* Tool 不存在
* Handler 正常返回
* Handler 抛出异常
* ToolResult.success 正确

### 安全

确认 ToolResult / ToolDefinition 不暴露：

```text
API Key
Authorization
DATABASE_URL
SQL
traceback
embedding vector
```

---

# 十三、不要修改现有 RAG

本阶段必须保证：

```text
/api/chat
/api/rag/answer
RagService
VectorSearchService
EmbeddingClient
KnowledgeIngestionService
```

行为不发生变化。

执行完整测试：

```bash
pytest
```

如果项目已有 DB 测试，也按照当前项目已有方式执行。

---

# 十四、代码质量

要求：

* 类型注解完整
* 不使用 `Any` 逃避类型设计，只有 Tool data / handler 边界允许合理使用
* frozen dataclass 保持项目现有风格
* 不使用全局可变 Registry
* Registry 实例化后独立工作
* 不修改现有 RAG 代码
* 不引入不必要依赖
* 不添加 TODO 代替实现
* 不留下 debug print
* 不记录敏感参数

---

# 十五、最终验证

完成后依次执行：

```bash
pytest tests/test_tool_framework.py -q
```

然后：

```bash
pytest -q
```

最后进行 Python 编译 / 静态检查。

---

# 十六、最终汇报格式

完成后不要继续做 Phase 3.6.2。

只汇报：

```text
Phase 3.6.1 COMPLETE

1. 新增文件
2. Tool Framework 架构
3. ToolDefinition
4. ToolRegistry
5. ToolResult
6. 参数校验
7. Mock Tools
8. 测试结果
9. 全量回归结果
10. 是否修改 RAG
11. 是否连接真实 WMS/ERP
12. 当前 Git diff / 未提交文件
13. 下一阶段建议
```

**完成 Phase 3.6.1 后必须停止，等待我确认，不得自动进入下一阶段。**
