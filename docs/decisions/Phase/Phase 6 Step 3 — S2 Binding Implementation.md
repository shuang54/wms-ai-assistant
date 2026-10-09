# Phase 6 Step 3 — S2 Binding Implementation

项目：`D:\coding\ai\wms-ai-assistant`

基线：核对当前实际 HEAD，不假定仍为 `26bc707`。

## 一、目标

新增：

`backend/app/workflow_runtime/binding.py`

并新增对应离线测试：

`tests/test_workflow_runtime_binding.py`

本轮只实现：

* Input / Previous 数据解析与路径读取
* 模板占位符渲染
* Tool 参数映射
* CONDITION 节点条件求值

不得开始 S3 Execution 或 Runtime 实现。

## 二、编码前检查

先读取：

* `backend/app/workflow_runtime/ports.py`
* `backend/app/workflow_runtime/__init__.py`
* `backend/app/workflow/definition.py`
* `backend/app/workflow/policy.py`
* `backend/app/workflow/states.py`
* `tests/test_workflow_runtime_ports.py`
* 相关 Domain 测试

必须复用现有 DTO、类型约束、模板语法和 JSON-safe 边界，不得复制或重新定义第二套规则。

如果真实接口与以下要求存在冲突，先报告，不得擅自改 Domain 或 ports。

## 三、Input / Previous 读取

要求：

1. `input` 与 `previous` 是唯一允许的模板根。
2. `input` 从持久化 JSON 文本解码；`None` 表示没有输入。
3. `previous` 使用调用方传入的最近成功节点输出，不依赖节点 ID 排序。
4. 仅允许安全的字典键和列表索引访问。
5. 路径不存在、类型不匹配、非法路径、无法解析的 JSON 一律明确失败，不静默转成空字符串。
6. 不允许执行 Python 表达式、`eval`、动态导入或任意函数调用。
7. 读取出的值不得绕过已有 JSON-safe 校验。

如果当前 Domain 对路径语法已有严格定义，必须以 Domain 为准。

## 四、模板渲染

支持当前 Definition 已批准的模板语法，例如：

`{input.order_no}`

`{previous.total_qty}`

要求：

* 只支持已定义的占位符格式。
* 缺失变量必须 fail-closed。
* 不允许隐式类型转换以掩盖错误。
* 不允许递归执行模板内容。
* 不允许静默保留未解析占位符。
* 保留普通文本，不改变非占位符部分。
* 明确区分合法的空字符串与缺失值。

不得增加新的模板语法或修改 Domain contract。

## 五、Tool arguments_map

按现有 Definition 的真实 schema 实现：

* 逐项解析参数映射。
* 所有引用均经过同一个安全路径解析器。
* 输出必须是 JSON-safe 字典。
* 缺失路径、非法类型或不安全对象必须失败关闭。
* 不执行 Tool，不导入 Tool Registry，不调用 LLM。
* 不允许通过参数映射访问任意 Python 对象属性。

## 六、CONDITION 求值

严格遵循已冻结的 OQ-1：

* 缺失路径、`None` 或类型不匹配：比较结果为 `false`。
* `EXISTS`：路径存在且值不是 `None`。
* `CONTAINS`：仅支持字符串子串或列表成员判断。
* 不做隐式类型转换。
* 不使用 Python `eval`。
* 不允许未知 operator 静默返回 `false`；未知 operator 应明确报错。

注意：模板缺失变量与 CONDITION 缺失路径的语义不同，不能混用。

## 七、测试

至少覆盖：

### 路径读取

* input / previous 正常读取
* 多层字典
* 合法列表索引（若现有语法支持）
* 根路径非法
* 缺失键
* 类型不匹配
* None 输入
* 非法 JSON

### 模板

* 单个与多个占位符
* 普通文本保留
* 缺失变量失败
* 非法语法失败
* 不递归展开
* 不执行表达式

### 参数映射

* 正常映射
* 多字段映射
* 缺失字段引用
* 非 JSON-safe 输出
* 非法映射 schema

### CONDITION

* 比较成功与失败
* 缺失路径
* None
* 类型不匹配
* EXISTS
* 字符串 CONTAINS
* 列表 CONTAINS
* 未知 operator

所有测试必须离线、确定性，不访问真实数据库或 LLM。

## 八、边界与禁止事项

* `workflow_runtime/binding.py` 只依赖标准库和必要的 Domain 定义。
* 不得导入 DB、Repository、API、Project、Agent Runtime 或 Tool Framework。
* 不修改 ports、Domain、ORM、Repository、ADR。
* 不新增 schema、迁移或数据库写入。
* 不修改现有 S1 测试以规避失败。
* 不提交、不 push、不 stash、不 rebase。
* 不开始 S3。

## 九、验收命令

先执行：

`python -m pytest -q tests/test_workflow_runtime_binding.py`

然后：

`python -m pytest -q tests/test_workflow_runtime_ports.py tests/test_workflow_domain_boundary.py`

最后：

`python -m compileall -q backend tests scripts`

如果项目有针对 Workflow Runtime 的 AST import-boundary 检查，也必须执行。

报告实际执行结果，不推断未执行项目通过。

## 十、最终报告

输出：

`【Phase 6 Step 3 — S2 Binding Report】`

1. 新增文件
2. 对外函数与真实签名
3. Input / Previous 读取规则
4. 模板渲染规则
5. arguments_map 规则
6. CONDITION 求值规则
7. 测试结果
8. Import boundary
9. Git 状态
10. 未解决问题

完成 S2 后立即停止，等待下一步指令。不得自动开始 S3。
