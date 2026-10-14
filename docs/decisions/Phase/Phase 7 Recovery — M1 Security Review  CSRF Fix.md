# Phase 7 Recovery — M1 Security Review / CSRF Fix

你现在继续执行项目：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

对刚完成的 M1 用户身份与认证基础做一次定向安全复核，重点验证：

1. CSRF 双提交 Cookie 是否存在可绕过路径。
2. Session Cookie 与服务端 Session 的绑定是否正确。
3. 登录、登出、会话过期、用户停用和账户锁定是否具有一致的安全语义。
4. 认证安全复核完成后，M1 是否具备进入 M2 的条件。

**本阶段只做 Security Review 和必要的最小修复，不重写认证系统。**

---

## 二、先阅读真实代码

先检查当前工作区，不要假设上一阶段的实现与报告完全一致。

重点阅读：

```text
backend/app/auth/
backend/app/api/auth.py
backend/app/api/auth_dependency.py
backend/app/config.py

backend/app/db/models/app_user.py
backend/app/db/models/user_session.py
backend/app/db/models/resource_owner.py

backend/app/db/user_repository.py
backend/app/db/user_session_repository.py
backend/app/db/resource_owner_repository.py

backend/app/services/user_service.py
backend/app/services/session_service.py
backend/app/services/resource_ownership_service.py

tests/test_auth_password.py
tests/test_user_service.py
tests/test_session_service.py
tests/test_resource_ownership_service.py
tests/test_auth_api.py
tests/test_auth_db.py

.env.example
docs/architecture.md
docs/security.md
```

同时检查：

```text
AGENTS.md
git status --short
git diff --stat
git diff --check
```

必须保留当前所有未提交修改，不得覆盖、重置或清理工作区。

---

## 三、第一优先级：CSRF 安全复核

当前实现据报告采用：

```text
wms_ai_session = HttpOnly Session Cookie
wms_ai_csrf = 可被前端读取的 CSRF Cookie

非安全方法：
X-CSRF-Token == CSRF Cookie
→ 通过
```

请核对真实代码是否确实如此。

**不能因为 Cookie 与请求头中的值相等，就直接认定 CSRF 防护安全。**

OWASP 指出，未与当前会话绑定的朴素 Double-Submit Cookie 可能遭受 Cookie 注入攻击；对于有服务端 Session 的系统，可以采用 Synchronizer Token Pattern，或者使用与会话绑定的签名 Double-Submit Cookie。

参考：

- https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html

### 3.1 先评估现有实现

检查：

- CSRF Token 是否由密码学安全随机源生成。
- Token 是否与当前认证 Session 绑定。
- 服务端是否验证 Token 的完整性，而不只是比较两个客户端可控的值。
- 其他用户的 CSRF Token 能否用于当前 Session。
- Session 被撤销或过期后，旧 CSRF Token 是否失效。
- Cookie 注入或重复 Cookie 是否可能造成验证歧义。
- 缺失、空值、格式错误和错误 Token 是否统一拒绝。
- 比较操作是否使用恒定时间比较。
- CSRF 校验是否发生在任何有副作用的业务操作之前。

### 3.2 选择最小、安全的修复

优先评估以下方案：

**方案 A：Synchronizer Token Pattern**

如果现有服务端 Session 可以安全保存 CSRF Token，则生成与 Session 绑定的随机 Token，由服务端验证请求头中的 Token。

**方案 B：Signed Double-Submit Cookie**

如果现有架构更适合无额外数据库字段的方案，可以采用带服务端 HMAC 签名、且明确绑定当前 Session 的 Double-Submit Cookie。

具体选择必须根据现有代码、配置和数据模型决定。

要求：

- 不新增第三方依赖。
- 不修改现有 Session 的过期与撤销语义。
- 不把 Session ID、密码、API Key 或服务端签名密钥暴露给浏览器。
- 不将 CSRF Token 写入 URL 或日志。
- 不因 CSRF 修复而增加自动重试。
- 不改变原有 API Key 调用路径。
- 不为满足测试而关闭安全校验。

如果现有方案已经安全，提供代码依据和测试证据，不要为了修改而修改。

---

## 四、第二优先级：Session Cookie 与认证生命周期

逐项检查：

### 4.1 Session Token

确认：

- Token 使用安全随机源生成，具有足够的随机熵。
- 数据库仅保存 Token 的哈希，而不是原始 Session Token。
- Session 查询使用哈希进行匹配。
- 不会因固定 Token、UUID 结构或可预测信息而削弱认证安全。
- 不存在通过 Session ID 查询绕过 Token 校验的路径。

### 4.2 Cookie 属性

检查当前：

```text
HttpOnly
Secure
SameSite=Lax
Path=/
```

以及是否设置了不必要的 `Domain` 属性。

确认生产默认配置不会意外允许不安全的 HTTP 会话。

如果开发环境需要 HTTP，请明确这是本地开发限制，不能将其误认为生产部署已满足安全要求。

### 4.3 生命周期

验证：

```text
Login
  ↓
Session Created
  ↓
Authenticated Request
  ↓
Logout
  ↓
Session Revoked
  ↓
旧 Session 被拒绝
```

并检查：

- 绝对过期时间是否由服务端强制执行。
- 登出是否清除客户端 Session Cookie。
- 用户停用后，已有 Session 是否立即失效。
- 账户锁定是否不会意外撤销其他用户的 Session。
- 数据库异常是否 fail-closed。
- 并发登出与并发请求是否产生意外的认证绕过。

不要擅自引入滑动过期、设备管理或密码重置功能。

---

## 五、第三优先级：登录与密码安全

检查：

- 用户名不存在、密码错误、用户停用、账户锁定是否返回统一的认证失败响应。
- 不存在的用户名是否执行合理的密码 KDF 耗时均衡。
- 登录失败计数是否在并发请求下可能被覆盖或绕过。
- 锁定时间是否由服务端判断。
- 成功登录后失败计数是否正确清零。
- 密码哈希格式、参数和盐是否经过严格校验。
- 畸形密码哈希是否导致无限资源消耗或 500 错误。
- 是否存在明文密码进入日志、异常消息、DTO 或数据库的路径。
- 密码长度边界是否在登录和创建用户时保持一致。

保留当前 scrypt 方案和现有依赖纪律。不要新增 bcrypt、JWT、passlib 或其他依赖来重写已有实现。

---

## 六、第四优先级：资源归属与 API Key 兼容性

仅检查现有 M1 能力，不在本阶段扩展所有端点。

重点验证：

```text
用户身份
  ↓
AgentRun
  ↓
Resource Ownership
  ↓
Agent Read API
```

必须确认：

- 客户端不能通过提交 `user_id` 冒充其他用户。
- 无归属记录、其他用户归属和归属存储故障时，用户上下文均 fail-closed。
- 跨用户读取不会泄露资源是否存在。
- 用户停用或 Session 过期后不能继续使用旧身份。
- 现有 Service-to-Service API Key 路径保持原有行为。
- API Key 调用不能被误认为 Human User Authentication。
- 客户端不能通过 API 参数自行声明项目作用域。

**不要修改 Conversation API 或 WorkflowRun API 的认证覆盖范围。**

这些工作属于后续 M2，不能在本轮顺手扩展。

---

## 七、测试要求

优先复用 M1 已有测试基础设施。

新增或补充最小必要测试，至少覆盖：

### CSRF

1. 合法 Session + 合法 CSRF Token → 通过。
2. 缺少 CSRF Cookie → 拒绝。
3. 缺少 CSRF Header → 拒绝。
4. Cookie 与 Header 不一致 → 拒绝。
5. 签名或 Session 绑定错误 → 拒绝。
6. 其他 Session 的 Token → 拒绝。
7. Session 撤销后，旧 Token 不再有效。
8. Session 过期后，旧 Token 不再有效。
9. Cookie 注入或重复 Cookie 不会绕过校验。
10. CSRF 失败时，业务副作用尚未发生。
11. 不安全方法均受保护，安全方法不应隐式执行写操作。

### Session

12. 正常登录后可以访问 `/api/auth/me`。
13. 登出后旧 Session 立即失效。
14. 用户停用后旧 Session 立即失效。
15. Session 过期后返回未认证结果。
16. Session 存储故障时 fail-closed。

### 登录与密码

17. 不存在用户与错误密码具有一致的外部错误语义。
18. 锁定账户即使密码正确也不能登录。
19. 并发登录失败不能轻易绕过锁定限制。
20. 密码与 Session 原文不会进入持久化字段或日志。

### 兼容性

21. 原 API Key 调用路径继续工作。
22. API Key 不会被转换成用户身份。
23. 跨用户 AgentRun 读取继续被拒绝。

测试应以现有接口、实际异常和现有测试风格为准，不要为凑数量制造重复测试。

---

## 八、治理门与回归

上一阶段报告中，治理门出现：

```text
DRIFT = OFFLINE/MATRIX_STATUS
```

报告称唯一差异来自 `test_11` 对未提交工作区的检测。

本阶段不要通过提交、跳过测试或关闭治理门来消除失败。

应当：

1. 确认 `test_11` 的真实失败条件。
2. 确认其是否仅反映当前工作区未提交修改。
3. 保留现有治理规则。
4. 运行相关认证测试和必要回归。
5. 重新运行当前治理门。
6. 如仍因未提交修改而失败，如实记录，不得声称全部通过。

建议执行：

```powershell
python -m pytest tests/test_auth_password.py tests/test_user_service.py tests/test_session_service.py tests/test_resource_ownership_service.py tests/test_auth_api.py -q
```

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_auth_db.py -q
```

```powershell
python -m compileall backend tests scripts
```

最后按项目现有方式运行相关认证安全审计与治理门。

不要为了验证而连接业务 WMS 数据库，也不要执行真实 LLM 调用。

---

## 九、严格禁止

本阶段禁止：

- 修改 Text-to-SQL Prompt、Validator、Executor。
- 修改 Agent Runtime 或 Workflow Runtime 核心行为。
- 修改冻结表结构。
- 新增 Conversation / WorkflowRun 的用户接线。
- 实现 RBAC、组织租户、用户管理 UI。
- 开发 Web UI。
- 部署、提交、推送、创建 PR。
- 修改历史 Ground Truth 或 Evaluation Baseline。
- 新增数据库迁移或生产表，除非安全修复确实不可避免；如确实需要，先说明原因并停止等待确认。
- 将 `test_11` 标记为跳过或修改门禁以强行通过。
- 在报告中宣称未执行的测试已经通过。

---

## 十、完成报告

只输出：

```text
Phase 7 Recovery — M1 Security Review

1. 审查文件
2. CSRF 方案与安全结论
3. Session Cookie / Token 安全结论
4. 登录与密码安全结论
5. 资源归属与 API Key 兼容性
6. 修改文件
7. 新增/修改测试
8. 相关回归结果
9. 治理门结果与 test_11 原因
10. 业务数据库 / LLM / 网络调用情况
11. 剩余风险
12. M1 READY / NOT READY

STOP — 不进入 M2，不开发 Web UI，不提交或推送。
```

如果发现 CSRF 绕过、Session Token 可预测、会话撤销失效等实质安全缺陷，先修复并用测试证明；如果需要修改冻结表或引入新的数据库结构，停止并报告原因，等待下一步指令。

**完成后立即停止。**

