# Phase 6 Step 8F-1 — Pre-Commit Code Review

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

审查 Step 8F 未提交的 residue fix，判断是否可以安全提交。

重点检查：

1. `_db_suite_execution` 的真实调用时机。
2. `_residue_delta(before, after)` 是否可能掩盖真实残留。
3. `lru_cache` 是否导致首次测量结果被错误复用。
4. 新增测试是否覆盖运行时行为。
5. 会话结束的绝对残留检查是否保持原有强度。

## 二、第一步：确认工作区

运行：

```powershell
git status --short
git rev-parse HEAD
git diff --check
git diff --stat
git diff -- tests/test_assistant_trace_timeline_regression.py
git show --check --oneline HEAD
```

预期 HEAD：

`efbc8cb58220550ca726ccb5e9241c15d0d59074`

如果 HEAD 已变化，停止并报告，不要覆盖其他人的提交。

确认 Step 8F 的 tracked 修改仍然只有预期文件。不要删除或修改 untracked 任务书副本。

## 三、审查真实调用链

阅读：

```text
tests/test_assistant_trace_timeline_regression.py
tests/conftest.py
```

找到以下函数和对象的真实定义与所有调用点：

```text
_db_suite_execution
_residue_delta
_matrix_db_residue_delta
_current_matrix_baseline
_db_residue_total
MATRIX_EXECUTION_BASELINE
```

明确回答：

* 哪个测试或函数最先触发 `_db_suite_execution`？
* `before` 是否在矩阵 DB 套件开始前读取？
* `after` 是否在套件执行完成后读取？
* `_current_matrix_baseline()` 是否存在绕过执行入口、提前读取的路径？
* `lru_cache` 的缓存键和生命周期是什么？
* 如果执行入口抛出异常，是否可能留下看似有效的旧结果？
* 计数失败、返回 `None` 或数据库不可用时，是否会 fail closed？

不要仅凭源码注释作结论。沿实际调用链核实。

## 四、审查净增量语义

当前计算：

```python
max(0, after - before)
```

至少分析：

| before | after | 预期解释                   |
| -----: | ----: | ---------------------- |
|      0 |     0 | 无净增加                   |
|      0 |     1 | 检测到净增加                 |
|      3 |     7 | 净增加 4                  |
|     15 |    15 | 无净增加，但绝对行数仍为 15        |
|     10 |     8 | 净增量为 0，但不能据此证明期间没有新增残留 |
|   None |     1 | 未知，不能当成 0              |

重点回答：

1. 执行期间如果新增 5 条、同时删除 5 条，净增量会是多少？
2. 是否有其他测试或进程可能同时写入这四张观测表？
3. 会话收尾检查是否独立检查绝对残留？
4. 若 before/after 读取失败，矩阵 gate 是否会失败关闭？
5. 当前净增量究竟能证明什么、不能证明什么？

不要为了保持测试全绿而弱化安全要求。

## 五、审查测试质量

检查新增的 `TestMatrixResidueDeltaSemantics`。

区分：

* 纯函数单元测试；
* 源码文本断言；
* 真实运行时行为测试。

确认是否真正覆盖：

* 套件入口在测量前后各读取一次；
* 首次调用时机正确；
* `None` 或读取失败不会被转成零；
* 真正新增残留仍触发 DRIFT；
* 历史 baseline 未被修改；
* 会话结束的绝对残留检查仍有效。

如果缺少运行时测试，只列出缺口，本步骤不修改测试。

## 六、审查绝对残留保护

检查 `tests/conftest.py` 中真实的 `_observability_residue_check` 及相关 guards。

以实际代码为准，列出它检查的表、检查时机、失败条件。

确认：

* 全库绝对残留检查仍然存在；
* 该检查不会被净增量替代；
* 没有新增 skip/xfail；
* 没有降低零残留断言强度；
* 没有修改历史 baseline；
* 未执行任何数据删除或数据库写操作。

特别区分：

**“矩阵执行的净新增残留为零”不等于“数据库绝对残留为零”。**

## 七、验证规则

本步骤只读。

允许运行安全的离线针对性测试，但不要为了审查重新运行完整 DB 套件。

禁止：

```text
INSERT
UPDATE
DELETE
DDL
真实 LLM
git commit
git push
```

任何需要数据库写入才能验证的场景，标记为 `NOT VERIFIED`，说明原因。

## 八、最终报告

严格输出：

```text
【Phase 6 Step 8F-1 — Pre-Commit Review】

1. Git 状态
- HEAD：
- 未提交文件：
- diff --check：
- 历史提交空白问题：

2. 实际调用链
- 首次触发位置：
- before 读取时机：
- after 读取时机：
- 缓存行为：
- 异常处理：

3. 净增量语义
- 正常新增：
- 新增/删除抵消：
- None / DB 读取失败：
- 并发风险：
- 可证明的内容：
- 无法证明的内容：

4. 测试质量
- 纯函数测试：
- 源码断言：
- 运行时测试：
- 缺失覆盖：

5. 绝对残留保护
- 检查时机：
- 实际覆盖表：
- 失败条件：
- 是否有安全降级：

6. 问题清单
- 问题：
- 代码证据：
- 风险：
- 建议：

7. 最终结论
- READY FOR COMMIT / NOT READY
- 理由：

本步骤只读，不修改文件、不提交、不推送。
STOP。
```

## 九、停止条件

审查完成后立即停止。不要自动修复、提交、推送或进入 Phase 7。
