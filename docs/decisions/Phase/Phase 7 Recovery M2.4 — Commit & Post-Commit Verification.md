# Phase 7 Recovery M2.4 — Commit & Post-Commit Verification

项目：`D:\coding\ai\wms-ai-assistant`

## 一、执行授权

用户已授权：

- 提交已完成安全审查的 M2.4 变更。
- 不执行 `git push`。
- 接受以下冻结矩阵文件仅增加 fixture 装配的治理例外：
  - `tests/test_assistant_trace_api.py`
  - `tests/test_assistant_trace_correlation_e2e.py`
- 不修改冻结矩阵基线、常量、Gate 逻辑或既有业务断言。
- 不进入 M2.5。

## 二、提交前再次核验

先执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-only
git diff --cached --stat
```

确认当前分支仍为 `phase7`，并检查 HEAD 是否仍为：

```text
c4c0caf82ae322d5fb05c72b837f44abd455906c
```

如果 HEAD 或分支已经变化，停止并报告，不要直接沿用旧审查结论。

逐一核对所有 tracked 与 untracked 文件。**不要使用 `git add .` 或 `git add -A`。**

## 三、确认提交范围

仅提交 M2.4 本身的实现、测试、文档及本阶段任务记录。

应包含已审查的：

- 授权模型、Repository、AuthorizationService、API 授权依赖和授权 CLI。
- M2.4 授权单元测试、端点强制测试和 DB 集成测试。
- M2.4 对现有授权入口的必要修改。
- `.env.example`、`docs/api.md`、`docs/security.md` 中与 M2.4 直接相关的变更。
- 上述两个冻结测试文件中已审查的 fixture-only 变更。
- 经核实确属 M2.4 的任务记录文档。

不要纳入其他阶段的任务文档、实验文件、数据库脚本或无关工作。

对每个将提交的文件，使用 `git diff` 或直接检查新文件内容。若出现审查报告未覆盖的变更，先停止并报告，不得顺手纳入提交。

## 四、暂存与提交

1. 使用明确的文件路径逐一执行 `git add <path>`。
2. 执行：

```powershell
git status --short
git diff --cached --check
git diff --cached --stat
git diff --cached
```

3. 确认暂存区没有凭证、密钥、数据库连接字符串或无关文件。
4. 确认冻结文件只有已批准的 fixture 变更。
5. 确认 M2.4 所有必要文件均已纳入，其他 Phase 7 工作没有混入。

提交信息建议：

```text
feat: Phase 7 Recovery M2.4 project authorization
```

仅在以上检查全部通过后执行 `git commit`。

**禁止 push、禁止 amend、禁止 reset、禁止 clean、禁止 stash，也不要为了让 Gate 变绿而修改基线或测试断言。**

## 五、提交后验证

记录新的 HEAD 与提交摘要，然后运行：

```powershell
git status --short
git show --stat --oneline HEAD
git show --check --oneline HEAD
git diff HEAD^ HEAD --name-only
```

确认提交内容仅包含 M2.4 的批准范围。

随后运行项目既有 Matrix Gate。准确记录：

- Exit code
- offline passed / skipped / failed / errors
- DB passed / skipped / failed / errors
- matrix 总数及 residue
- Gate 是否 PASS

如果 Gate 仍然失败，保留原始失败证据并定位原因。不得修改 Gate 或基线来掩盖失败。

再次运行专用测试数据库的只读 residue 审计，确认：

```text
ai_ops.llm_usage_record         = 0
ai_ops.tool_execution_record    = 0
ai_ops.rag_execution_record     = 0
ai_ops.assistant_outcome_record = 0
ai_ops.authorization_grant      = 0
```

数据库连接必须继续硬性限制为：

```text
localhost:5433/wms_ai_test
```

不得访问生产数据库，不执行任何数据清理。

最后确认：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/phase7
```

若工作区仍有其他阶段的既有未提交文件，必须保留并逐项报告，不能擅自处理。

## 六、最终报告

```text
【Phase 7 Recovery M2.4 Commit Report】

1. 提交前 HEAD / branch
2. 实际提交文件清单
3. 冻结测试文件治理例外
4. Commit hash / message
5. 提交后 HEAD / branch
6. Matrix Gate 结果
7. 离线与 DB 回归结果
8. 五张表的 residue
9. 工作区剩余变更
10. 是否 push（必须为 NO）
11. 是否修改生产数据或 Schema
12. 未解决问题

M2.4 提交完成后立即 STOP。
不进入 M2.5。
```

