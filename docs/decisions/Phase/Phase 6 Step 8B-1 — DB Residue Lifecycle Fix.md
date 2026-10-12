# Phase 6 Step 8B-1 — DB Residue Lifecycle Fix

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、任务目标

修复：

`tests/test_assistant_trace_timeline_regression.py::TestMatrixExecutionBaseline::test_db_residue_is_zero`

已完成只读审计，确认：

* 测试在会话中途检查四张观测表的全库行数。
* `test_assistant_trace_multi_path_e2e.py` 会通过真实 `/api/ai/chat` 链路写入 `assistant_outcome_record`，但文件级清理不处理该表。
* `tests/conftest.py` 中 `_assistant_outcome_residue_guard` 在 session teardown 才清理 outcome 表。
* 因此全量 DB 测试中，中途的零残留断言会被前序测试写入的数据触发；单独运行则可能通过。
* `_db_residue_total()` 使用 `@lru_cache(maxsize=1)`，必须同时解决缓存可能导致的陈旧读取。

本任务只修改测试基础设施，不修改生产代码或数据库结构。

## 二、实施前保护

先记录：

```powershell
git status --short
git diff --check
git diff --stat
```

保留所有既有未提交工作。

禁止：

* `git reset`
* `git clean`
* `git stash`
* 删除或覆盖已有未提交修改
* 修改数据库 Schema
* 删除历史数据库数据
* 执行无关数据库清理
* `git commit`
* `git push`

本任务不得提交任何文件。

## 三、先核实 fixture 结构

阅读：

```text
tests/conftest.py
tests/test_assistant_trace_timeline_regression.py
```

确认三个 session 级守卫的实际名称、依赖关系和 teardown 行为。

必须保证：

```text
会话开始
    ↓
三个既有守卫记录各自水位
    ↓
全量 DB 测试
    ↓
三个守卫全部执行清理
    ↓
最终 residue assertion
```

不得依赖测试文件顺序、函数定义顺序或 `pytest-order`。

## 四、最小实现要求

优先采用方案 A：将全库零残留检查迁移至 session cleanup 完成后的检查点。

允许修改：

```text
tests/conftest.py
tests/test_assistant_trace_timeline_regression.py
```

如确实需要共享查询逻辑，可新增一个很小的测试辅助模块，但必须说明为什么无法通过现有结构避免新增文件。

### 4.1 显式依赖关系

不能只让最终检查依赖某一个清理守卫，然后假定另外两个守卫必然先执行。

必须显式构建依赖关系，保证最终检查发生在全部相关清理之后。

可使用一个 session 级协调 fixture，使最终检查明确依赖三个清理守卫。

fixture 的实际依赖必须在代码中可见，并通过测试验证。

pytest fixture 的执行和 teardown 顺序由 scope、依赖关系与 autouse 等规则决定；不得使用偶然的执行顺序作为正确性保障。

参考：

https://docs.pytest.org/en/7.1.x/reference/fixtures.html

### 4.2 保留严格断言

原有四张观测表必须保持不变：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

必须保留：

```text
Σ COUNT(*) == 0
residue == MATRIX_EXECUTION_BASELINE.db_residue
```

不得添加过滤条件，不得只检查当前测试创建的行，不得降低 baseline 要求。

### 4.3 解决缓存问题

`_db_residue_total()` 使用：

```python
@lru_cache(maxsize=1)
```

最终检查前必须确保使用最新数据库状态。

优先考虑：

```python
_db_residue_total.cache_clear()
residue = _db_residue_total()
```

但必须先确认函数真实定义、调用方式与缓存装饰器，不能机械复制示例。

验收必须证明最终检查确实重新查询数据库，而不是复用会话中途缓存的结果。

### 4.4 避免循环导入

不要让 `tests/conftest.py` 反向导入测试模块。

如果表名、baseline 或查询函数必须共享：

* 优先使用现有公共测试辅助模块；
* 否则只新增一个最小的纯测试辅助模块；
* 不得复制两份互相独立的表名或 baseline 定义。

## 五、异常清理边界

必须区分：

1. 普通测试失败后，pytest 仍执行 fixture teardown。
2. 进程被强制终止时，teardown 可能根本没有机会执行。

本次修复只保证正常测试会话中，在全部清理之后执行最终检查。

不得宣称该机制能保证进程被强制终止时也一定完成清理。

不得为了处理异常退出而引入额外数据库清理范围。

## 六、测试要求

新增或更新最少量测试，验证：

1. 三个清理守卫都完成后，最终检查才执行。
2. 四张表任何一张非零，最终检查都失败。
3. 四张表均为零时，最终检查通过。
4. 最终检查前缓存被清除，重新读取数据库。
5. baseline 断言仍然保留。
6. 测试不依赖文件或函数顺序。
7. 现有 DB fixture 与相关测试没有新增回归。

不要仅用静态 AST 断言证明执行顺序。可以通过轻量级 fixture 测试记录 setup/teardown 事件，验证真实生命周期。

## 七、验证命令

先运行相关测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q "tests/test_assistant_trace_timeline_regression.py::TestMatrixExecutionBaseline::test_db_residue_is_zero"
```

然后运行相关 DB 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_assistant_trace_timeline_regression.py tests/test_assistant_trace_multi_path_e2e.py
```

再运行全量离线测试：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

最后执行全量 DB 回归：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果项目全量 DB 测试需要按既有方式分片，必须保持既有分片规则，并报告每个分片结果。不得通过改变顺序规避失败。

运行：

```powershell
python -m compileall tests
git diff --check
git status --short
git diff --stat
```

## 八、验收标准

必须满足：

* 单独运行 residue 测试通过。
* 全量 DB 测试中的最终零残留检查通过。
* 四张表的严格零残留断言未被削弱。
* 最终查询使用最新数据库状态。
* 清理依赖是显式的。
* 没有新增生产代码修改。
* 没有数据库 Schema 变更。
* 没有新增数据库清理操作或历史数据删除。
* 没有提交或推送。

若测试发现其他既有失败，必须如实报告根因和影响，不得跳过、删除断言或调整顺序掩盖。

## 九、最终报告

```text
【DB Residue Lifecycle Fix】

1. 根因
2. 修改文件
3. fixture 依赖与 teardown 顺序
4. 缓存失效验证
5. 单测结果
6. 相关 DB 测试结果
7. 全量离线测试结果
8. 全量 DB 测试结果
9. DB residue
10. compileall / diff check
11. 是否修改生产代码
12. 是否修改 Schema
13. 未解决问题
14. Git 状态

Git commit/push = NOT EXECUTED
Phase 6 Exit = 尚未重新判定
```

完成后立即停止，等待用户审阅报告。不得进入 Phase 7。
