# Phase 7 Recovery — M2.3 A1 Pre-Commit Audit

## 目标

对当前 A1 工作区进行只读提交前审查，确认变更边界、测试证据、冻结矩阵约束和 Gate 失败原因，为后续是否授权提交提供依据。

项目：

`D:\coding\ai\wms-ai-assistant`

已知基线：

```text
Branch: phase7
HEAD: bbb5d7508b64d1b12e5215c25f8e9cf61b108726
```

以上信息必须重新核验，不能假设工作区仍与报告一致。

## 一、严格只读

本次只允许读取文件、Git 状态、已有测试结果和测试注册表。

禁止：

```text
修改代码
修改文档
修改测试
修改 baseline / snapshot / registry
git add
git commit
git push
PR
部署
修改数据库
运行会写入数据库的测试
```

不要使用 `reset`、`clean`、`stash`、`checkout`、`restore` 或其他可能覆盖现有工作区的命令。

## 二、重新核验工作区

执行：

```powershell
git status --short
git diff --check
git diff --stat
git diff --name-status
git diff
git ls-files --others --exclude-standard
```

逐一检查全部变更，特别是：

```text
backend/app/api/orchestrator_chat.py
backend/app/api/chat.py
backend/app/api/rag.py
backend/app/api/tool_chat.py
backend/app/services/text_to_sql_prompt_v2_promotion_gate_rerun_service.py
tests/api_session_auth.py
tests/test_ai_execution_api_auth.py
docs/api.md
docs/security.md
```

注意：以上是预期清单，不是完整清单。实际变更必须以 Git 输出为准。

确认：

1. 没有遗漏或混入其他阶段的改动。
2. 没有 secrets、`.env`、数据库凭证或真实会话 Token。
3. 没有修改冻结模型、Schema、Prompt、Validator、Executor 或历史 baseline。
4. 没有修改 Matrix Gate 注册表或冻结计数。
5. 未跟踪的任务书和其他文件均得到明确识别。

## 三、认证实现审查

逐个检查四个入口：

```text
POST /api/ai/chat
POST /api/chat
POST /api/chat/with-tools
POST /api/rag/answer
```

确认：

- Session 认证先于业务执行。
- CSRF 校验正确绑定 Session。
- 认证服务故障 fail-closed。
- 失败请求不会调用 LLM、RAG、Tool 或 Text-to-SQL。
- 成功响应 DTO 未发生非预期变化。
- 服务间调用没有通过匿名放行维持兼容。
- OpenAPI 401/403/503 说明与真实行为一致。

额外检查：

`text_to_sql_prompt_v2_promotion_gate_rerun_service.py`

确认其改动仅为合法认证调用适配，没有改变 Prompt 晋升策略或 gate 判定逻辑。

## 四、测试证据审查

本步骤只核对已有执行记录，不重新运行会产生副作用的测试。

重点检查：

1. A1 定向认证矩阵：32 passed。
2. 四个端点直接契约测试：149 passed。
3. 调用方批次测试及跳过原因。
4. DB 定向测试：172 passed / 1 failed。
5. 全量测试：7650 passed / 17 failed / 997 skipped。
6. `compileall` 与 `git diff --check`。
7. Matrix Gate 的实际输出和退出码。

如果日志或输出文件无法证明某项结果，标记为 `UNVERIFIED`，不要根据完成报告之外的猜测补齐证据。

## 五、Matrix Gate 根因核验

重点回答：

1. `test_11_backend_working_tree_is_unmodified` 的实际断言是什么？
2. 是否确实仅因本阶段受控工作区改动导致失败？
3. 冻结离线子集的实际计数是否与冻结基线一致？
4. 是否存在 `MATRIX_TOTAL_DRIFT` 或数据库残留？
5. Gate 的 DRIFT 是治理状态问题，还是另有认证测试回归？

不要通过修改 Gate、跳过 test_11、放宽断言或更新 baseline 消除失败。

## 六、失败分类

按当前证据核对：

| 分类 | 已报告数量 | 审查要求 |
|---|---:|---|
| 工作区治理检查及级联失败 | 5 | 确认与未提交改动的因果关系 |
| Step 8 历史失败 | 3 | 确认测试与根因，不修复 |
| WMS 业务库连接超时 | 9 | 核对真实异常与独立复现证据 |
| A1 新引入失败 | 0 | 只有证据充分时才能确认 |

如果失败分类或数量发生变化，逐项列出差异。

## 七、输出报告

只输出：

```text
【Phase 7 Recovery — M2.3 A1 Pre-Commit Audit】

1. Branch / HEAD / 工作区
2. 实际变更文件清单
3. 认证实现审查
4. Promotion Gate 探针审查
5. 冻结模块与 baseline 审查
6. 定向测试证据
7. 全量测试证据
8. Matrix Gate 根因
9. 失败分类与新增失败判断
10. DB residue 证据
11. 未跟踪文件与 secrets 检查
12. 提交前遗留风险
13. 建议状态：READY_FOR_COMMIT_REVIEW / BLOCKED / NOT_READY

Git add: NOT AUTHORIZED
Git commit: NOT AUTHORIZED
Git push: NOT AUTHORIZED
Deployment: NOT AUTHORIZED
A2: NOT STARTED
STOP
```

**完成后立即停止，不修改任何文件。**