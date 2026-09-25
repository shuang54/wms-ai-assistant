# Phase 3.9.5 收尾修复 — `--check` 零 API 调用

## 一、目标

修复：

```text
scripts/generate_text_to_sql_real_llm_baseline.py
```

使：

```text
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

成为真正的 **offline consistency check**。

### 必须达到

```text
--check
    ↓
读取已有 3.9.5 Snapshot
    ↓
读取 Dataset
    ↓
做结构 / 数值 / Report 一致性检查
    ↓
输出 OK / FAIL
```

**绝对不能调用 DeepSeek。**

---

# 二、当前问题

当前实现存在：

```text
--check
    ↓
_run()
    ↓
真实 DeepSeek Evaluation
    ↓
再次生成 Snapshot / 比对
```

因此：

* 会产生 LLM API 调用
* 会产生 API 成本
* 依赖网络
* 不能称为纯 offline check

这与 Phase 3.9.5 任务书 §十六 的要求不一致。

---

# 三、修改范围

原则：

> **最小修改。**

允许修改：

```text
scripts/generate_text_to_sql_real_llm_baseline.py
```

如果确实需要共享一个极小的纯检查函数，可以修改：

```text
backend/app/services/text_to_sql_real_llm_baseline_service.py
```

但优先只修改 Script。

---

# 四、正确的执行模式

## 默认模式

```text
python scripts/generate_text_to_sql_real_llm_baseline.py
```

仍然执行：

```text
真实 DeepSeek
    ↓
14 cases
    ↓
生成 Real LLM Snapshot
    ↓
生成 Report
```

不能改变。

---

## `--check` 模式

```text
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

必须：

```text
NO LLM
NO DB
NO NETWORK
NO FILE WRITE
```

只读取：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_5_real_llm_baseline.json
```

以及：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

必要时读取：

```text
docs/evaluation/text-to-sql-real-llm-baseline-3.9.5.md
```

---

# 五、--check 检查内容

至少检查：

## 1. Snapshot 存在

```text
phase_3_9_5_real_llm_baseline.json
```

不存在：

```text
FAIL
```

---

## 2. Phase

必须：

```text
phase == "3.9.5"
```

---

## 3. Baseline Type

必须：

```text
baseline_type == "real_llm"
```

---

## 4. Execution Mode

必须：

```text
execution_mode == "real-llm"
```

---

## 5. Dataset Version

从实际 Dataset 读取：

```text
_schema_version
```

必须与 Snapshot：

```text
dataset.version
```

一致。

当前应为：

```text
1.0
```

---

## 6. Dataset Case Count

Dataset：

```text
14
```

Snapshot：

```text
dataset.total_cases == 14
len(cases) == 14
```

必须一致。

---

## 7. Case ID 集合

比较：

```text
Dataset case_id
vs
Snapshot case_id
```

必须：

```text
完全一致
```

不允许：

* 缺 Case
* 多 Case
* 重复 Case

---

## 8. Metrics 一致性

不要信任 Snapshot 中已经保存的 metrics。

重新根据：

```text
cases[]
```

计算应该得到的 Metrics，然后比较：

```text
Snapshot metrics
vs
recalculated metrics
```

至少检查：

```text
total_cases
passed_cases
failed_cases
expectation_pass_rate
validation_expectation_pass_rate
security_pass_rate
project_isolation_pass_rate
llm_generation_success_rate
validator_acceptance_rate
```

`execution_pass_rate` 当前应保持：

```text
null
```

因为 Dataset 没有 `must_execute`。

---

# 六、不要重新生成 Real LLM Result

这是本次修复的关键。

禁止：

```python
run_real_llm_baseline(...)
```

出现在：

```text
--check
```

路径中。

建议结构：

```python
if args.check:
    return check_existing_baseline()

return generate_new_real_llm_baseline()
```

其中：

```text
check_existing_baseline()
```

只能：

```text
load
validate
recalculate
compare
```

---

# 七、Report 一致性

如果现有 3.9.5 Report 中已经存在 Metrics 表，则 `--check` 可以检查 Report 中的关键数字与 Snapshot 一致。

至少检查：

```text
Total Cases
Passed
Failed
Expectation Pass Rate
Validation Expectation Pass Rate
Security Pass Rate
Project Isolation Pass Rate
LLM Generation Success Rate
Validator Acceptance Rate
```

如果现有 Report 已经有 3.9.4 vs 3.9.5 对比，也可以检查关键 3.9.5 数字。

不要为了做 Report Parser 而增加复杂 Markdown AST。

**简单稳定即可。**

如果当前 Report 检查实现过于复杂，可以只检查 Snapshot + Dataset 一致性，并明确 Report consistency 已由现有测试覆盖。

---

# 八、Secret Safety

`--check` 继续执行：

```text
secret safety check
```

Snapshot 中如果出现：

```text
sk-
api_key
authorization
password
token
DATABASE_URL
```

等敏感内容：

```text
FAIL
```

不得输出敏感值。

---

# 九、关键：证明没有调用 LLM

新增测试：

```text
tests/test_text_to_sql_real_llm_baseline.py
```

增加一个测试：

```text
test_check_mode_does_not_invoke_llm
```

使用 monkeypatch/mock。

例如让：

```text
Real LLM generator
```

如果被调用就：

```python
raise AssertionError("LLM must not be called in --check mode")
```

然后执行：

```text
--check
```

必须成功。

这样以后即使代码被重构，也不会重新出现这个问题。

---

# 十、最好增加一个 subprocess 测试

如果当前测试结构允许，可以直接测试：

```text
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

并 monkeypatch / 环境隔离。

但如果 subprocess mocking 复杂，不必为了这个增加复杂测试。

核心是：

> `--check` 的代码路径必须与 Real LLM generation path 完全分离。

---

# 十一、测试命令

完成后：

```powershell
python -m pytest -q tests/test_text_to_sql_real_llm_baseline.py
```

然后：

```powershell
python -m compileall backend scripts -q
```

然后：

```powershell
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

预期：

```text
OK
```

且：

**不产生任何 DeepSeek 请求。**

---

# 十二、验证“真的没有 API 调用”

不要只看程序输出。

建议至少采用一种实际验证：

### 方法 A：临时错误 API Key

在独立测试环境中设置一个无效 Key，然后：

```text
--check
```

仍然应该：

```text
OK
```

因为它根本不应该读取/调用 LLM。

注意：

**不要修改 `.env` 中真实 Key。**

可以使用临时环境变量覆盖。

---

### 方法 B：代码路径测试

测试 monkeypatch：

```text
LLM invocation → raise AssertionError
```

`--check` 仍然通过。

优先采用方法 B。

---

# 十三、绝对不能发生

以下情况均视为本修复失败：

```text
--check 调用 DeepSeek
```

或者：

```text
--check 修改 Snapshot
```

或者：

```text
--check 修改 Report
```

或者：

```text
--check 修改 Dataset
```

或者：

```text
--check 修改 3.9.4 Snapshot
```

或者：

```text
--check 产生 DB 写入
```

---

# 十四、回归

修复完成后运行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
$env:RUN_DB_TESTS=""
```

然后：

```powershell
python -m compileall backend scripts -q
```

然后：

```powershell
python scripts/generate_text_to_sql_baseline.py --check
```

确认：

```text
3.9.4 unchanged
```

最后：

```powershell
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

确认：

```text
3.9.5 snapshot is consistent
```

---

# 十五、必须重新确认 Snapshot 未被覆盖

修复过程中：

**不要重新生成 3.9.5 Real LLM Snapshot。**

也就是说：

```text
phase_3_9_5_real_llm_baseline.json
```

内容应该保持原来的：

```text
13 passed
1 failed
14/14 generation
14/14 validator acceptance
```

不能因为重新运行而产生新的：

```text
generated_at
```

漂移。

---

# 十六、最终汇报

完成后告诉我：

```text
Phase 3.9.5 收尾修复完成

1. 修改内容
2. --check 是否完全零 LLM
3. --check 是否零 DB
4. Snapshot 是否未改变
5. 3.9.4 --check
6. Real LLM baseline --check
7. 新增/修改测试
8. 全量 pytest
9. DB pytest
10. compileall
11. DB residue
12. API / Generator contract
```

特别确认：

```text
--check → 0 LLM calls
```

---

# 十七、STOP

这个修复完成后：

**Phase 3.9.5 正式封版。**

不要进入 Phase 3.9.6。

不要修改 Prompt。

不要修改 Dataset。

不要重新跑 DeepSeek。

不要顺便优化其他代码。
