# Phase 6 Step 8B-3 — Conditional Residue Error Forensics

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

只读定位 Phase 6 DB 测试中间歇性出现的 observability residue teardown error。

已知情况：

* DB 分片 7 已通过。
* DB 分片 6 曾出现一次条件性 residue error。
* 定向运行以下四个候选测试时，均报告 delta = 0：

  * `tests/test_llm_usage_trace_correlation_db.py`
  * `tests/test_llm_usage_trace_read_db.py`
  * `tests/test_phase_4_1_security_boundary_db.py`
  * `tests/test_llm_usage_production_wiring_e2e_db.py`
* 全量离线测试曾出现条件性 residue error，但复跑时未必出现。
* `tool_execution_record` 的历史 14 行在本轮核验时已不存在；不得推断具体清理者。

**本阶段只查明错误触发条件和证据，不修复、不清理。**

## 二、绝对禁止

* 不执行 `DELETE`、`UPDATE`、`INSERT`、DDL 或 migration。
* 不修改生产代码。
* 不修改测试、fixture、清理逻辑或断言。
* 不弱化、跳过 residue assertion。
* 不修改数据库 Schema。
* 不执行 `git reset`、`git clean`、`git stash`。
* 不执行 commit / push。
* 不为追求复现而连续无上限运行全量测试。
* 不把“本次没复现”报告成“问题已解决”。

## 三、Step 1：保存初始证据

先运行并保存输出：

```powershell
git status --short
git diff --check
git diff --stat
```

记录当前工作区状态，确认没有因为本次取证修改文件。

只读检查：

```text
tests/observability_residue_contract.py
tests/test_observability_residue_lifecycle.py
tests/conftest.py
```

以及：

```text
tests/test_phase_4_1_traceability.py
```

如果实际测试名称不同，先用 `pytest --collect-only` 确认，不要猜测。

## 四、Step 2：理解 residue 检查机制

明确回答：

1. 哪个 fixture 或 finalizer 负责最终检查？
2. 检查发生在 function、module 还是 session teardown？
3. 检查前是否还有其他 fixture 执行清理？
4. 检查查询是否复用缓存的 Session / Connection？
5. 失败时具体是哪张表、多少行、哪条断言？
6. 检查异常是否可能覆盖原始测试异常？
7. 测试期间是否启动后台线程、异步任务、TestClient lifespan 或其他可能延迟写入的组件？

只读查询代码与调用链，不修改实现。

## 五、Step 3：单个目标测试取证

首先只运行目标测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -vv -s tests/test_phase_4_1_traceability.py::test_db_schema_traceability --tb=long
```

如果测试节点不存在，使用实际收集到的节点 ID。

执行前后，只读查询四张表的行数：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
ai_ops.assistant_outcome_record
```

要求：

* 记录测试前、测试后四表行数。
* 如果 residue assertion 失败，保存完整 traceback。
* 如果出现新增记录，尽可能记录新增记录的主键、创建时间、request/correlation ID、调用来源。
* 仅查看必要字段；严禁输出 API key、密码、数据库连接串或授权头。
* 不删除测试产生的记录。

## 六、Step 4：检查清理顺序

依据实际 fixture 依赖关系绘制顺序：

```text
Fixture setup
    ↓
Test body / API / background task
    ↓
Function teardown
    ↓
Module teardown
    ↓
Session teardown
    ↓
Final residue assertion
```

不要假定上述每一步在当前项目都存在，必须用代码证据确认实际顺序。

重点确认：

* residue assertion 是否确实最后执行。
* 写入者的清理 fixture 是否早于或晚于全局断言。
* `yield` fixture、`request.addfinalizer` 和 session hook 的先后关系。
* 是否有 teardown 异常导致后续清理未执行。
* 是否有并发测试进程或其他测试会话共用同一数据库。

pytest 的 fixture teardown 顺序取决于作用域、依赖和 autouse 关系；不要仅凭 fixture 在文件中的排列顺序下结论。参考官方说明：https://docs.pytest.org/en/stable/how-to/fixtures.html

## 七、Step 5：有限范围复现

只有在单测运行和只读证据已保存后，才考虑扩大范围。

顺序：

1. 单个目标测试。
2. 该测试所属文件。
3. 最小相关 DB 测试集合。
4. 只有必要时才执行 DB 分片 6。

每一级最多执行两次。

出现首次失败后，优先分析证据，不要继续重跑掩盖问题。

如果问题只在分片 6 出现，保存：

* 分片实际测试列表与执行顺序。
* residue assertion 的完整异常。
* 失败前后四表计数。
* 首次出现新增记录的测试节点（若能定位）。
* 该节点的 fixture 依赖和 teardown 顺序。

## 八、Step 6：区分真实残留和检查机制问题

最终只能基于证据归类：

A. **真实残留**：检查时数据库确实存在未清理记录。

B. **清理时序问题**：写入发生在清理之后，或异步任务晚于清理完成。

C. **Fixture 生命周期问题**：teardown 依赖不完整、异常路径未清理，或最终检查顺序错误。

D. **检查状态问题**：检查使用了过期 Session/事务状态或错误的计数上下文。

E. **尚未定位**：证据不足，无法确认以上任何一类。

不得为了得到确定分类而猜测。

## 九、最终报告

严格输出：

```text
【Phase 6 Step 8B-3 — Forensics Report】

1. 初始 Git 状态
2. 相关 fixture / finalizer 调用链
3. 单测复现结果
4. 四张表测试前后计数
5. 完整错误与触发条件
6. 新增记录来源（如能确认）
7. Fixture teardown 顺序
8. 是否发现异步/并发写入
9. 分类 A/B/C/D/E
10. 根因证据与置信度
11. 是否修改文件：必须为否
12. 是否修改数据库：必须为否
13. 是否清理数据：必须为否
14. 全量测试是否运行及结果
15. Git commit/push：NOT EXECUTED
16. Phase 6 Exit：暂不判定
```

如果未复现，明确写“本次未复现”，并列出已执行的有限范围；不要声称问题已解决。

完成报告后立即停止，不进入 Step 8B-4 或 Phase 7。
