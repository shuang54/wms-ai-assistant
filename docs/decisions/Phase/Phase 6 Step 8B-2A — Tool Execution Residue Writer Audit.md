# Phase 6 Step 8B-2A — Tool Execution Residue Writer Audit

项目：`D:\coding\ai\wms-ai-assistant`

## 目标

只读定位 `tool_execution_record` 残留的所有测试写入者，重点解释：

1. `tests/test_project_capability_isolation.py` 中已确认的 DB 写入。
2. 离线全量测试结束时新增的 26 行。
3. DB 分片 6、7 的收尾检查为何仍报 residue。

本任务是审计阶段，不进行任何数据库清理。

## 严格限制

禁止：

* 修改代码文件
* 执行 DELETE、TRUNCATE、UPDATE 或其他数据库写操作
* 清理现存 26 行
* 修改或跳过测试断言
* 修改 pytest 顺序来规避问题
* commit / push

## 步骤一：枚举所有写入者

搜索整个 `tests/`，定位所有可能写入 `tool_execution_record` 的路径，包括：

* ORM add / flush / commit
* Repository create / save
* Tool Execution Service
* FastAPI endpoint / TestClient
* fixture 间接调用
* 真实 app 与 fake app 的差异

对每个写入者记录：

```text
文件与测试名称
写入入口
使用真实 DB 还是 Fake
是否有清理逻辑
清理条件
清理是否在异常时执行
是否依赖测试顺序
```

不能只搜索显式 SQL DELETE；必须追踪调用链。

## 步骤二：定位离线 26 行

先确认离线模式为什么仍可能写入数据库。

重点检查：

* `RUN_DB_TESTS=0` 是否真正阻止所有 DB-backed service 初始化。
* 测试是否使用真实 `DATABASE_URL`。
* `tool_execution_record` 写入是否由应用初始化、Tool 路由或 observability persistence 间接触发。
* 是否有测试创建了真实 app，却仅把部分依赖替换为 Fake。
* 26 行是否来自一个测试，还是多个测试累计。

优先使用只读查询、已有日志、测试级分组及逐文件运行进行定位。

不得为了定位而修改应用代码或测试文件。

若必须逐文件运行，请使用只读查询对比运行前后行数；不要通过删除记录恢复基线。

如果不能唯一确定写入者，明确列出候选和证据，不要猜测。

## 步骤三：分析 DB 分片 6、7

核对每个分片：

* 实际收尾检查的表与行数。
* 写入者对应的测试文件。
* 清理 fixture 是否覆盖该写入者。
* 是否存在前序测试产生的跨测试残留。
* 是否存在父 pytest 与嵌套子 pytest 共享数据库的问题。

明确区分：

```text
已证明的根因
有证据支持但尚未证明的假设
尚未检查的范围
```

## 步骤四：提出最小修复方案，不实施

为每个确认的写入者提出独立清理方案。

优先采用项目已有的 request_id、测试前水位、唯一测试标识或 fixture 生命周期模式。

要求：

* 清理范围仅覆盖当前测试创建的记录。
* 不删除测试开始前已有的数据。
* 失败路径也有明确清理策略。
* 不关闭全局 residue assertion。
* 不通过修改测试顺序规避问题。
* 离线测试应在隔离的 Fake 环境中运行，或者明确识别需要真实 DB 的测试。
* 不得将 `RUN_DB_TESTS=0` 当作自动证明没有 DB 访问。

对于当前 26 行，只提供只读证据与处置建议，不执行清理。

## 步骤五：最终报告

```text
【8B-2A Tool Residue Writer Audit】

1. 搜索范围
2. 全部 tool_execution_record 写入者
3. 已确认的未清理写入者
4. 离线 26 行的根因与证据
5. DB 分片 6 / 7 的根因与证据
6. 每个写入者的最小修复方案
7. 现存 26 行的只读核验结果
8. 是否修改文件：必须为否
9. 是否执行 DB 写操作：必须为否
10. 是否 commit / push：必须为否

完成后停止，等待授权。
```
