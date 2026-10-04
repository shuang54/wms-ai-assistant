你现在继续执行：

# Phase 4.1 Step 8.1：修复 Tool ORM Architecture Guard 误报

## 一、背景

Phase 4.1 Step 8 已完成。

Conversation API 已经实现并通过：

```text
tests/test_conversation_api.py
28 passed
```

```text
tests/test_conversation_api_architecture_audit.py
15 passed
```

```text
tests/test_conversation_api_contract.py
27 passed
```

但是现有旧测试：

```text
tests/test_tool_observability_persistence_architecture.py
```

中的：

```text
test_tool_execution_orm_model_is_confined_to_ai_ops
```

失败。

### 根因

当前守卫使用文本启发式：

```text
backend/app/db/models/
    ↓
搜索 tool_execution 字样
    ↓
命中文件即 offender
```

而：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
```

的 docstring 中为了说明：

```text
assistant_request_id
```

没有 FK 到：

```text
tool_execution_record
```

因此被错误识别。

实际 Conversation ORM：

```text
Conversation
ConversationTurn
```

都没有定义：

```text
ToolExecution
ToolExecutionRecord
tool_execution_record
```

相关 ORM Model/Table。

---

# 二、本 Step 唯一目标

只修复：

```text
tests/test_tool_observability_persistence_architecture.py
```

使 Tool ORM 架构守卫从：

```text
文本字符串扫描
```

变成：

```text
AST / executable structure 检查
```

Python `ast` 可以将 Python 源码解析为 AST 节点，因此这里应该检查实际代码结构，而不是源码中任意字符串。

---

# 三、严格范围

## 允许修改

只允许：

```text
tests/test_tool_observability_persistence_architecture.py
```

必要时可以在：

```text
tests/
```

新增极少量测试辅助代码。

但优先只修改上述测试文件。

---

## 禁止修改

禁止修改：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/__init__.py

backend/app/api/conversations.py
backend/app/dto/conversation_api.py
backend/app/main.py

ConversationService
ConversationRepository
Conversation ORM
ConversationTurn ORM
ToolExecution ORM
ToolExecutionRepository
ToolExecutionService
```

禁止：

```text
白名单 conversation.py
白名单 conversation_turn.py
```

不要通过增加文件名例外来解决问题。

---

# 四、先阅读真实守卫

先阅读：

```text
tests/test_tool_observability_persistence_architecture.py
```

以及：

```text
backend/app/db/models/tool_execution_record.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
```

确认当前测试究竟通过什么方式判断：

```text
Tool Execution ORM confined to ai_ops
```

不要假设现有实现。

---

# 五、AST 判定目标

守卫真正应该检查的是：

> 是否存在实际定义 Tool Execution ORM Model / Table 的 executable Python structure。

而不是：

```text
文件是否包含字符串 tool_execution
```

---

# 六、推荐判定策略

使用标准库：

```python
import ast
```

读取 Python 文件：

```text
source
 ↓
ast.parse(source)
 ↓
AST
```

然后检查真实代码节点。

至少关注：

```text
ClassDef
Assign
AnnAssign
Call
```

以及当前项目 SQLAlchemy ORM 的实际声明方式。

---

# 七、必须根据当前 Tool ORM 实现判断

例如如果实际 Tool ORM 是：

```python
class ToolExecutionRecord(Base):
    __tablename__ = "tool_execution_record"
```

那么应该通过 AST 识别：

```text
ClassDef.name
```

以及：

```text
__tablename__
```

而不是简单：

```python
"tool_execution" in source
```

如果当前项目采用其他 ORM 声明方式：

按照实际代码适配。

---

# 八、必须保留原架构约束

最终测试仍然必须保证：

```text
Tool Execution ORM
        ↓
只能位于
        ↓
ai_ops schema
```

以及：

```text
Tool Execution ORM
不能出现在 public / business schema
```

不要降低原测试的安全约束。

---

# 九、增加两个关键回归测试

## Test 1：Conversation docstring 不应触发

构造一个临时 Python source：

```python
"""
This document mentions tool_execution_record
but does not define a model.
"""

class Conversation:
    pass
```

验证：

```text
not offender
```

即：

```text
docstring string
≠
ORM definition
```

---

## Test 2：真实违规 Model 必须仍然被发现

构造 synthetic source：

```python
class FakeToolExecutionRecord(Base):
    __tablename__ = "tool_execution_record"
```

如果该模型不符合：

```text
ai_ops
```

要求，则必须：

```text
offender
```

不能因为从文本扫描改成 AST 后把真正违规模型漏掉。

---

# 十、Comment 也不能触发

增加：

```python
# tool_execution_record
```

验证：

```text
not offender
```

即：

```text
comment
≠
executable ORM declaration
```

---

# 十一、Conversation ORM Regression

必须确认：

```text
conversation.py
conversation_turn.py
```

不会再被：

```text
test_tool_execution_orm_model_is_confined_to_ai_ops
```

误报。

不要修改 Conversation ORM。

---

# 十二、不要改变测试语义

当前守卫真正想表达的是：

```text
Tool Execution persistence ORM
必须 confined to ai_ops
```

修改后仍然必须覆盖：

```text
ToolExecutionRecord
ToolExecution
tool_execution_record
ai_ops
```

具体名称以当前真实 ORM 为准。

不要把测试改成：

```text
只检查文件名
```

也不要改成：

```text
只检查 schema 字符串
```

---

# 十三、测试范围

完成后只运行：

```powershell
python -m pytest -q tests/test_tool_observability_persistence_architecture.py
```

然后运行 Conversation API 相关回归：

```powershell
python -m pytest -q tests/test_conversation_api.py
python -m pytest -q tests/test_conversation_api_contract.py
python -m pytest -q tests/test_conversation_api_architecture_audit.py
```

再运行：

```powershell
python -m compileall -q backend tests
```

---

# 十四、验收标准

必须达到：

```text
Tool ORM Architecture Guard
    ↓
PASS
```

并且：

```text
Conversation API
    ↓
PASS
```

同时：

```text
Conversation ORM
    ↓
无需任何修改
```

---

# 十五、禁止借机扩展

本 Step 不要做：

```text
❌ Step 9
❌ PostgreSQL API E2E
❌ Conversation Message POST
❌ Chat Integration
❌ Context Builder
❌ Memory
❌ Auth
❌ Pagination
❌ Trace Integration
❌ Timeline Integration
```

也不要：

```text
修改 production code
```

---

# 十六、最终报告

完成后只返回：

```text
Phase 4.1 Step 8.1 COMPLETE

1. 修改文件
2. Guard 原因
3. AST 判定方式
4. Conversation false-positive
5. Synthetic violation test
6. Tool ORM Guard
7. Conversation API Regression
8. compileall
9. Production Code Changes
10. DB / Network / LLM

Phase 4.1 Step 8.1 READY
Phase 4.1 Step 8.1 STOP
```

如果 AST 守卫无法在不降低原安全约束的情况下正确实现：

```text
Phase 4.1 Step 8.1 BLOCKED
```

立即停止，不要修改 Conversation ORM，也不要进入 Step 9。
