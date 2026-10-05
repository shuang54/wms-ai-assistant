# Phase 4.1 Step 37 — Full Regression Failure Triage

## 当前状态

Phase 4.1 Step 37：

**Real Evidence Persistence Implementation**

当前已确认：

```text
Evidence ORM                    PASS
Annotation ORM                  PASS
Evidence Repository             PASS
Annotation Repository           PASS
Real PostgreSQL Persistence    PASS
DB Tests                        7 passed
compileall                      PASS
```

但是 Full Regression：

```text
12 failed
5904 passed
640 skipped
0 errors
```

因此 Step 37 当前状态：

```text
BLOCKED — Regression Triage Required
```

---

# 唯一目标

**定位这 12 个失败的根因。**

本任务：

```text
只诊断
不修复
不重构
不修改生产代码
```

---

# 一、首先获取完整失败列表

运行：

```powershell
python -m pytest -q
```

记录全部：

```text
12 failed
```

必须获得：

```text
test file
test name
failure type
traceback
```

---

# 二、分类

将 12 个失败逐个归类：

## Category A — Step 37 Regression

明确由以下 Step 37 变更导致：

```text
ORM
models/__init__.py
Repository
DB registration
schema creation
transaction
session
imports
```

---

## Category B — Pre-existing Failure

如果能够通过：

```text
git diff
git show
历史测试结果
```

确认 Step 37 之前已经存在：

```text
FAIL
```

归类：

```text
PRE_EXISTING
```

不要修改。

---

## Category C — Test / Fixture / Environment

例如：

```text
DB fixture
test ordering
transaction state
cleanup
environment
```

导致失败。

归类：

```text
TEST_INFRA
```

不要修改业务代码。

---

## Category D — Unknown

无法确认来源：

```text
UNKNOWN
```

保留证据。

不要猜测。

---

# 三、重点检查 Step 37 Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认实际修改范围。

重点关注：

```text
backend/app/db/models/
backend/app/db/repositories/
backend/app/db/
tests/
```

特别检查：

```text
models/__init__.py
```

是否因为新增：

```text
Evidence
Annotation
```

改变了已有 import / metadata / create_all 行为。

---

# 四、不要修改任何代码

本任务禁止：

```text
修复失败
修改测试
修改 fixture
修改 Repository
修改 ORM
修改数据库
修改 AI Runtime
```

即使发现明显 bug，也只记录：

```text
Problem
Impact
Root Cause
Evidence
Recommended Fix
```

---

# 五、检查历史基线

使用 Git 检查 Step 37 前的基线。

当前 Step 37 修改之前的 commit：

```text
Step 36 baseline
```

根据实际 git history 找到对应 commit。

可以使用：

```powershell
git log --oneline --all --decorate -20
```

以及：

```powershell
git diff <step36-commit>..HEAD --stat
```

如果环境允许，可以只针对失败测试与 Step 36 baseline 做比较。

不要为了验证而破坏当前工作树。

---

# 六、最终输出

严格输出：

```text
【Phase 4.1 Step 37 — Regression Triage】

1. Full Regression
   12 failed
   5904 passed
   640 skipped

2. Failure List

| # | Test | Category | Step 37 Related | Root Cause |
|---|------|----------|-----------------|------------|

3. Category Summary

Step 37 Regression:
N

Pre-existing:
N

Test Infrastructure:
N

Unknown:
N

4. Step 37 Blocking Assessment

BLOCKED / NOT_BLOCKED

5. Evidence

列出支持分类判断的具体 traceback / diff / historical evidence。

6. Recommended Fixes

只列建议。

禁止执行。

7. Production Code Modified

MUST BE:

0

8. DB Writes

MUST BE:

0
```

---

# STOP

完成 Failure Triage 后：

**立即停止。**

不要修复。

不要进入 Step 38。

不要修改任何文件。
