# Phase 7 Recovery — M2.2 Conversation Authentication & Ownership

## 一、目标

在当前 `phase7` 分支上，为 Conversation API 接入 M1 用户 Session 认证和 `resource_owner` 资源所有权控制。

当前基线：

- M1 commit：`28c54e00e46a6cb0728934382809c0a4070150c5`
- M2.1 审计提交：`f919e7209045be3c9d12efe49c83086000c0641b`
- 当前 M2.1 审计已完成。
- 开始前必须重新检查真实 HEAD 和工作区状态。

本阶段只保护 Conversation 资源，不扩展成完整 RBAC 或多租户平台。

## 二、实施前必须阅读

先阅读：

```text
backend/app/api/conversations.py
backend/app/api/auth_dependency.py
backend/app/services/resource_ownership_service.py
backend/app/services/session_service.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/models/conversation_evidence.py
backend/app/db/models/resource_owner.py
backend/app/projects/
tests/test_conversation_message_api_architecture_audit.py
tests/test_observability_http_allowlist_audit.py
```

同时搜索所有调用 `/api/conversations*` 的测试和服务。

必须以实际签名、事务边界、Repository 和依赖注入方式为准，不要凭文件名假设实现。

## 三、固定决策

### D1：项目作用域

- 保留现有请求 DTO 的 `project_id` 字段，尽量避免不必要的 API 契约变更。
- 创建时必须通过服务端 Project Registry / Project Context 验证项目是否已注册且允许当前调用方访问。
- 不得仅因客户端提交某个 `project_id` 就允许在该项目下创建 Conversation。
- 如果现有系统无法可靠判断用户是否有权访问某项目，必须 fail-closed，并明确报告限制；不得虚构项目授权能力。
- 所有权记录中的 `project_id` 必须来自已验证的服务端上下文。
- 现有 `vietnam-wms` 合法链路必须保持兼容。

### D2：所有权绑定失败

创建 Conversation 时：

```text
Authenticate
    ↓
Validate project
    ↓
Create Conversation
    ↓
Bind resource owner
    ↓
Commit
```

要求：

- Conversation 创建与所有权绑定尽可能处于同一事务。
- 任何步骤失败都不得留下无主的 Conversation。
- 如果当前 Repository / Service 架构无法提供原子事务，先报告真实限制并实施最小安全方案。
- 不允许返回成功状态后再静默绑定所有权。
- 不允许以 best-effort 方式忽略归属登记失败。

### D3：响应语义

- 未登录、无效 Session、过期 Session、已撤销 Session：`401`。
- 无资源或无权访问：统一 `404`。
- 所有权服务或数据库故障：返回受控 `503`，不得放行。
- CSRF 校验失败：`403`。
- 不向客户端暴露数据库异常、内部 SQL 或敏感实现细节。

保持现有项目的错误响应格式。

### D4：历史 Conversation

- 没有 `resource_owner` 记录的 Conversation 在用户 Session 上下文中一律拒绝访问。
- 禁止采用首个访问者自动认领。
- 禁止默认归属到默认用户或默认项目。
- 当前环境审计结果为 0 条历史 Conversation；不得因此省略对应测试。
- 管理员接管历史会话不属于本阶段。

## 四、实现范围

只修改完成目标所必需的文件，优先限定于：

```text
backend/app/api/conversations.py
tests/test_conversation_message_api_architecture_audit.py
tests/test_observability_http_allowlist_audit.py
```

必要时允许少量修改：

```text
backend/app/services/resource_ownership_service.py
backend/app/db/
```

但必须证明现有能力不足，且不得修改冻结表结构。

新增：

```text
tests/test_conversation_ownership.py
```

如果现有测试结构更适合其他位置，则沿用现有结构。

禁止修改：

- Conversation / Turn / Evidence 冻结模型字段。
- Agent / Workflow 的权限策略。
- Text-to-SQL、RAG 核心算法或 Prompt。
- M1 Session / CSRF 安全边界。
- 不相关的 Step 8 库存报表验证文件。

## 五、API 行为

覆盖全部五个端点：

```text
POST   /api/conversations
GET    /api/conversations/{id}
GET    /api/conversations/{id}/messages
POST   /api/conversations/{id}/messages
POST   /api/conversations/{id}/archive
```

要求：

1. 五个端点都必须经过 M1 用户 Session 认证。
2. 创建时验证项目作用域并登记所有权。
3. 读取 metadata 前检查所有权。
4. 读取消息正文及 evidence 关联内容前检查所有权。
5. 追加消息、触发 AI 前检查所有权。
6. 归档前检查所有权。
7. append/archive 使用现有 M1 CSRF 机制。
8. 任何越权请求都不能触发 LLM 调用、数据库写操作或其他业务副作用。
9. 不允许仅依靠 UUID 不可枚举性作为授权措施。
10. 不允许通过改变 Conversation ID 绕过所有权检查。

## 六、测试要求

### 认证

- 未登录访问全部五个端点。
- 无效、过期、已撤销 Session。
- Logout 后重放旧 Cookie。
- append/archive 缺少 CSRF Header。
- append/archive CSRF 值不匹配。

### 所有权

- 用户 A 创建并访问自己的 Conversation。
- 用户 B 访问 A 的 Conversation。
- 用户 B 尝试读取 A 的消息正文。
- 用户 B 尝试追加消息。
- 用户 B 尝试归档。
- 随机不存在的 Conversation ID。
- 无 `resource_owner` 记录的历史 Conversation。
- Conversation ID 篡改。
- evidence 关联内容越权读取。

### 项目作用域

- 合法注册项目。
- 未注册项目。
- 未授权项目。
- 客户端伪造 `project_id`。
- 现有 `vietnam-wms` 流程兼容。

### 故障处理

- 所有权绑定失败时不留下无主 Conversation。
- 所有权查询异常时不得放行。
- 数据库异常时返回受控错误。
- 无权访问与资源不存在的响应保持一致。
- 所有失败路径都不触发不必要的 LLM 调用。

### 兼容性

- 更新所有受影响的 Conversation 测试，统一使用现有登录测试夹具。
- 不通过跳过测试或放宽断言来解决回归。
- 保持 API-Key 相关路径不变。

## 七、冻结与数据库规则

```text
NO migration
NO frozen table column changes
NO production WMS/ERP database
NO production data writes
NO real LLM
```

复用 M1 的 `resource_owner` 表和服务。

## 八、验证

先执行新增测试，再执行：

```powershell
python -m pytest tests/test_conversation_ownership.py -q
python -m pytest tests/test_conversation_message_api_architecture_audit.py -q
python -m pytest tests/test_observability_http_allowlist_audit.py -q
python -m pytest -q
python -m compileall backend tests scripts
```

如果项目支持 DB 集成测试，再按项目现有方式执行：

```powershell
$env:PYTHONUTF8="1"
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

必须检查 Matrix Gate、DB residue 和完整 Git diff。

## 九、最终报告

```text
Phase 7 Recovery — M2.2 Conversation Ownership

1. 修改/新增文件
2. 五个端点认证结果
3. 所有权校验结果
4. project_id 作用域处理
5. 原子性与失败处理
6. CSRF 兼容性
7. 测试结果
8. Matrix Gate
9. compileall
10. DB residue
11. 冻结表检查
12. API 契约变更
13. 未解决风险

Git add: NOT EXECUTED
Git commit: NOT EXECUTED
Git push: NOT EXECUTED
PR: NOT CREATED
Deployment: NOT EXECUTED
M2.3: NOT STARTED

STOP
```

