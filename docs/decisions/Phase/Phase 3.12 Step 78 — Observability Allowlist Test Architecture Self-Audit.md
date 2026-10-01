# Phase 3.12 Step 78 — Observability Allowlist Test Architecture Self-Audit

继续 `Phase 3.12`。

当前状态：

```text
Phase 3.12 Step 77 READY / STOP
```

Step 77 已完成：

```text
Actual Routes
    ==
Frozen Contract
    ==
C25 Allowlist
```

当前目标：

> **审计 Step 76 / Step 77 的测试架构本身，防止未来重新出现第二套 route scanner、第二份 allowlist 或绕过 Frozen Contract 的测试。**

本阶段只做：

```text
Test Architecture Audit
+
Offline Tests
```

**不修改 Production Code。**

---

# 一、严格范围

允许修改：

```text
tests/
docs/evaluation/
```

优先只新增：

```text
tests/test_observability_http_allowlist_architecture_audit.py
```

如确有必要，可新增：

```text
docs/evaluation/phase-3.12-observability-allowlist-test-architecture.md
```

---

# 二、禁止

禁止修改：

```text
backend/
docs/architecture.md
DB schema
migration
API implementation
Trace
Timeline
Outcome
Tool Framework
RAG
T2SQL
LLM
```

禁止：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

禁止：

```text
DB
Network
DeepSeek
```

---

# 三、核心问题

本阶段只回答：

```text
未来开发者如果新增一个 Observability API：

是否一定会经过 Frozen Contract？
```

以及：

```text
是否存在第二套 route discovery？
```

以及：

```text
是否存在第二份 allowlist？
```

---

# 四、Frozen Contract 必须保持唯一

当前唯一事实来源：

```text
tests/test_observability_http_allowlist_regression.py
```

中的：

```python
EXPECTED_OBSERVABILITY_ROUTES
```

要求测试验证：

```text
Step 76 Audit
        ↓
引用 Frozen Contract
```

而不是：

```text
Step 76 Audit
        ↓
自己重新写 6 条 route
```

---

# 五、检测重复 Route Scanner

扫描：

```text
tests/
```

中的 Python AST。

寻找与 route discovery 相关的：

```text
discover_routes
observability_routes
route_discovery
frozen_allowlist
allowed_paths
EXPECTED_OBSERVABILITY_ROUTES
```

但：

### 不要简单字符串全文搜索。

使用 AST：

```text
FunctionDef
ClassDef
Assign
AnnAssign
Name
Attribute
Call
```

识别 executable code。

---

# 六、允许的例外

以下属于合法引用，不算重复 scanner：

```text
from tests.test_observability_http_allowlist_regression import (
    EXPECTED_OBSERVABILITY_ROUTES
)
```

以及：

```text
_EXPECTED_BASELINE = _expected_baseline()
```

这种：

```text
引用 Frozen Contract
```

是允许的。

---

# 七、禁止的重复模式

发现以下结构时标记：

```text
第二个 discover_routes()
```

或者：

```text
def discover_observability_routes(...)
```

或者：

```text
observability_routes = {
    ...
}
```

或者：

```text
allowlist = {
    "/observability/..."
}
```

如果它不是：

```text
引用 Frozen Contract
```

则：

```text
DUPLICATE_ROUTE_SOURCE
```

---

# 八、允许路径字符串出现的位置

注意：

测试代码中出现：

```text
/observability/assistant-trace/{assistant_request_id}
```

本身**不等于重复 allowlist**。

例如：

```python
assert route == (
    "GET",
    "/observability/assistant-trace/{assistant_request_id}",
)
```

这种属于：

```text
specific contract assertion
```

允许。

真正禁止的是：

```text
重新建立完整 route 集合
```

---

# 九、检测第二套 Allowlist

重点寻找：

```text
set[str]
frozenset[str]
set[tuple[str,str]]
frozenset[tuple[str,str]]
list[str]
```

中包含多个：

```text
/observability/...
```

路径字面量的情况。

不要使用全文 grep。

使用 AST：

```text
Set
Tuple
List
Constant
```

检查 executable AST。

---

# 十、判断规则

### 合法

```python
EXPECTED_OBSERVABILITY_ROUTES
```

作为 Frozen Contract 唯一声明。

---

### 合法

测试引用：

```python
EXPECTED_OBSERVABILITY_ROUTES
```

---

### 合法

单条路径断言：

```python
assert "/observability/assistant-timeline/{assistant_request_id}" in ...
```

---

### 可疑

另一个测试文件定义：

```python
OBSERVABILITY_ROUTES = {
    (...),
    (...),
    (...)
}
```

如果形成完整集合：

```text
DUPLICATE_ALLOWLIST
```

---

### 可疑

另一个测试文件实现：

```python
def discover_routes():
    ...
```

并扫描：

```text
backend/app/api
```

形成：

```text
DUPLICATE_ROUTE_SCANNER
```

---

# 十一、检测 backend/app/api 扫描器数量

当前预期：

```text
Route Discovery implementation = 1
```

即：

```text
tests/test_observability_http_allowlist_audit.py
```

其他测试不得自行：

```text
Path.glob("backend/app/api/*.py")
```

或等价 AST / filesystem route discovery。

但是：

```text
Frozen Contract regression
```

可以通过引用 Audit scanner。

---

# 十二、检测文件系统扫描

使用 AST 检查测试文件中是否存在：

```python
Path(...).glob(...)
Path(...).rglob(...)
os.walk(...)
glob.glob(...)
```

如果其目标明显是：

```text
backend/app/api
```

则记录：

```text
DUPLICATE_API_ROUTE_SCAN
```

不要把普通 fixture 文件扫描误判。

---

# 十三、检测 Import Graph

检查：

```text
Step 77 Regression
Step 76 Audit
Architecture Audit
```

之间的依赖方向。

预期：

```text
Frozen Contract
      ↑
Step 76 Audit
      ↑
Architecture Audit
```

即：

```text
Architecture Audit
        ↓
Audit
        ↓
Frozen Contract
```

不能出现：

```text
Frozen Contract
    ↓
Audit
    ↓
Frozen Contract
```

形成循环 import。

---

# 十四、单一事实来源测试

新增测试：

```text
test_frozen_route_contract_has_single_declaration
```

验证：

```text
EXPECTED_OBSERVABILITY_ROUTES
```

只有一个实际定义位置。

可以通过 AST：

```text
Assign / AnnAssign
```

统计定义次数。

要求：

```text
definition_count == 1
```

---

# 十五、Route Scanner 测试

新增：

```text
test_api_route_scanner_has_single_implementation
```

要求：

```text
backend/app/api
```

扫描实现只有：

```text
Step 76 Audit
```

一个。

如果未来出现第二个：

```text
FAIL
```

并报告：

```text
duplicate scanner locations
```

---

# 十六、Allowlist Duplication Test

新增：

```text
test_no_duplicate_observability_allowlist
```

检查：

```text
tests/
```

中除了：

```text
EXPECTED_OBSERVABILITY_ROUTES
```

之外，不存在第二个完整 Observability route set。

---

# 十七、Security

继续确认本阶段新增的 Architecture Audit：

不得引入：

```text
api_key
password
authorization
database_url
```

不得：

```text
HTTP client
requests
httpx
PostgreSQL
SQLAlchemy
psycopg
Redis
```

本测试必须：

```text
offline
static
AST-only
```

---

# 十八、不要修改已有测试语义

本阶段原则：

```text
Step 76 Audit
Step 77 Regression
```

都已经 READY。

不要修改它们的：

```text
route discovery semantics
allowlist semantics
security semantics
```

只允许新增 Architecture Self-Audit。

---

# 十九、新增测试文件

创建：

```text
tests/test_observability_http_allowlist_architecture_audit.py
```

建议测试：

```text
1. frozen contract single declaration
2. no duplicate full allowlist
3. single route scanner
4. no second backend API scanner
5. no duplicate observability route source
6. frozen contract import direction
7. no forbidden filesystem scanner
8. no DB/network dependency
9. no secrets
10. architecture audit itself is offline
```

目标：

```text
10～15 tests
```

---

# 二十、Documentation

如果新增文档：

```text
docs/evaluation/phase-3.12-observability-allowlist-test-architecture.md
```

只记录：

```text
Frozen Contract = single source of truth

Route Scanner = single implementation

C25 Allowlist = consumer

Regression = consumer

Architecture Audit = protects the above relationship
```

不要写成复杂测试框架设计。

---

# 二十一、执行

先运行：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_architecture_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_regression.py
```

然后：

```powershell
python -m pytest -q tests/test_observability_http_allowlist_audit.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_architecture_audit.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

最后：

```powershell
git diff -- backend
```

必须：

```text
empty
```

不要运行：

```text
RUN_DB_TESTS=1
```

不要运行全量 pytest。

---

# 二十二、成功条件

必须：

```text
Frozen Contract single source = PASS

Duplicate allowlist = 0

Duplicate route scanner = 0

Duplicate API scanner = 0

Import direction = PASS

Filesystem scan integrity = PASS

Security = PASS

Offline = PASS

Backend Diff = EMPTY

Compileall = PASS
```

---

# 二十三、失败处理

如果发现：

```text
DUPLICATE_ALLOWLIST
DUPLICATE_ROUTE_SCANNER
DUPLICATE_API_ROUTE_SCAN
CIRCULAR_IMPORT
SECRET
NETWORK_DEPENDENCY
DB_DEPENDENCY
```

不要自动修复。

只报告：

```text
问题：
位置：
影响：
是否修改：否
```

---

# 二十四、最终报告

完成后严格：

```text
Phase 3.12 Step 78 完成报告

1. Frozen Contract Single Source
2. Duplicate Allowlist
3. Duplicate Route Scanner
4. Duplicate API Scanner
5. Import Direction
6. Filesystem Scan
7. Security
8. Tests
9. Compileall
10. Backend Diff
11. DB / Network / DeepSeek
12. 新发现问题
13. 当前限制

Phase 3.12 Step 78 READY
Phase 3.12 Step 78 STOP
```

完成后立即停止。

不要进入：

```text
Unified Timeline
event_id
sequence
span_id
parent_event_id
pagination
Conversation
Memory
Agent
MCP
OpenTelemetry
```

等待下一步指令。
