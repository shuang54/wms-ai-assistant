# Phase 6 Step 8B-2B — Targeted Tool Residue Repair

项目：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

修复已确认的 `tool_execution_record` 测试残留，并继续定位尚未证实的 DB 分片 6 和离线全量问题。

本轮只处理测试隔离，不修改生产逻辑。

## 二、第一阶段：修复已确认的写入者

目标文件：

`tests/test_project_capability_isolation.py`

目标测试：

`TestApiCapabilityE2E`

实施前先阅读该文件及 `tests/conftest.py`，确认真实数据库引擎、表名、现有 fixture 与测试生命周期。

要求：

1. 使用项目现有的水位清理模式。
2. 在测试开始前读取 `tool_execution_record` 的最大 ID 水位。
3. 在可靠的 teardown/finalizer 中，仅清理本测试水位之后产生的记录。
4. 清理必须覆盖测试断言失败等正常异常路径。
5. 不得删除测试开始前已有的数据。
6. 不得修改业务断言来规避写入。
7. 不得修改生产代码。
8. 不得使用全表 DELETE。

如果该测试类会与其他测试并行运行，先检查项目是否启用了并行执行；若可能并发写入同一张表，必须评估单纯 ID 水位是否会误删其他测试的记录，并选择更精确的隔离标识。

## 三、第二阶段：定位 DB 分片 6

逐个检查以下候选：

* `tests/test_llm_usage_trace_correlation_db.py`
* `tests/test_llm_usage_trace_read_db.py`
* `tests/test_phase_4_1_security_boundary_db.py`
* `tests/test_llm_usage_production_wiring_e2e_db.py`

使用 `RUN_DB_TESTS=1`，在每个候选文件运行前后执行只读计数及必要的行标识对比。

目标是确认：

* 是否新增 `tool_execution_record`；
* 新增记录由哪个测试及调用链产生；
* 现有清理逻辑是否遗漏；
* 是否由其他前序测试造成。

只有确认具体写入者后，才可实施与第一阶段相同原则的最小定向清理。

禁止修改 pytest 顺序、跳过测试或弱化全局 residue assertion。

## 四、第三阶段：调查离线全量的条件性残留

不要把 `RUN_DB_TESTS=0` 视为绝对不会访问数据库。

先确认真实 `DATABASE_URL` 配置下，哪些离线测试仍会创建真实 app 并执行 Tool 路由。

采用只读计数、逐步缩小测试范围、组合二分等方法定位固定 26 行的写入来源。

要求：

* 不修改生产代码；
* 不通过插桩改动来掩盖现象；
* 不执行数据库清理；
* 如果不能复现或无法唯一定位，报告证据与未解决范围，不猜测。

若定位到写入者，先提出最小修复方案。只有在确认修改属于测试隔离且不会扩大清理范围后，才实施。

## 五、现有残留的处置

审计报告显示当前四张观测表均为 0。

再次核验：

* `llm_usage_record`
* `tool_execution_record`
* `rag_execution_record`
* `assistant_outcome_record`

若仍为 0，不执行任何清理。

若出现非零数据，先报告只读查询结果、ID 范围、时间范围和归属证据。未经单独授权，不得删除。

## 六、验证顺序

先运行定向测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_project_capability_isolation.py
```

然后运行已确认相关的 DB 文件及候选文件。

再运行：

```powershell
$env:RUN_DB_TESTS="0"
python -m pytest -q
```

最后按项目既有分片规则执行完整 DB 回归。

每轮记录：

* 测试命令与退出码；
* passed / failed / skipped / errors；
* 收尾检查结果；
* 四张表的只读 residue；
* 修改前后文件状态。

## 七、禁止事项

本轮禁止：

* 全表清理；
* 删除现有 26 行以外的任何数据；
* 删除或跳过测试；
* 修改治理门或 baseline；
* 修改生产代码；
* 修改数据库 Schema；
* commit / push；
* 进入 Phase 7。

## 八、最终报告

```text
【Phase 6 Step 8B-2B Report】

1. 已修复的写入者
2. 修改文件
3. 清理范围与并发安全分析
4. DB 分片 6 调查结果
5. 离线 26 行调查结果
6. 单测结果
7. 全量离线测试结果
8. 全量 DB 分片结果
9. 四表 residue
10. compileall / diff check
11. 是否修改生产代码
12. 是否修改 Schema
13. 未解决问题
14. Git 状态

Git commit/push = NOT EXECUTED
Phase 6 Exit = 暂不判定
```

完成后停止，等待审阅。
