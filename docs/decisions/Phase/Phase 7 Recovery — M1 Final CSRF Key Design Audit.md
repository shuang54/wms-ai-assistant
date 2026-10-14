# Phase 7 Recovery — M1 Final CSRF Key Design Audit

项目路径：

`D:\coding\ai\wms-ai-assistant`

## 一、目标

对 M1 已修复的 Signed Double-Submit Cookie 方案进行最后一次密钥设计审计。

当前实现据报告为：

```python
csrf = HMAC-SHA256(
    key=token_hash,
    msg=session_id,
)
```

其中：

- `token_hash` 是用户 Session Token 的 SHA-256 哈希。
- `token_hash` 存储在 `ai_ops.user_session`。
- `session_id` 是服务端生成的会话标识。
- CSRF Token 由服务端签发，并通过非 HttpOnly Cookie 提供给前端。
- 服务端验证当前 Session、Cookie/Header 一致性及 HMAC 签名。

本任务只判断：**当前密钥选择是否符合预期安全边界，是否需要最小调整。**

本轮先做审计和决策，不修改代码，不提交、不推送、不部署。

---

## 二、先检查真实实现

阅读：

```text
backend/app/auth/tokens.py
backend/app/services/session_service.py
backend/app/api/auth_dependency.py
backend/app/api/auth.py
backend/app/db/models/user_session.py
backend/app/config.py
.env.example
tests/test_auth_api.py
tests/test_session_service.py
tests/test_auth_db.py
```

保留当前工作区所有未提交修改。

不得执行：

```text
git reset
git checkout -- .
git clean
git stash
```

不得覆盖其他阶段的工作。

---

## 三、重点回答五个问题

### 1. 当前 HMAC 密钥的真实安全属性

分析：

```text
key = token_hash
message = session_id
```

分别讨论：

- 仅能写入任意 Cookie 的攻击者。
- 仅能读取应用数据库的攻击者。
- 同时获得数据库读取权限及 Cookie 注入能力的攻击者。
- 获得应用服务器配置或进程权限的攻击者。

明确区分：

```text
CSRF 防护
Session 身份认证
数据库凭据泄露
应用服务器密钥泄露
```

不能把这些威胁混为一谈。

尤其说明：CSRF Token 被伪造是否意味着攻击者同时能够通过 Session 身份认证。

### 2. 是否满足 Signed Double-Submit 的密钥要求

对照 OWASP 的 Signed Double-Submit Cookie 指南：

https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html

重点确认：

- HMAC 是否使用服务端独占的秘密。
- HMAC 是否绑定当前 Session。
- Token 是否包含不泄露 Session 凭据的随机信息。
- 签名验证是否使用恒定时间比较。
- Cookie 注入、重复 Cookie、跨 Session 重放是否被拒绝。

不能只因为现有攻击测试通过，就认定密钥设计已经满足所有威胁模型。

### 3. 是否需要独立 CSRF Secret

比较以下方案：

**方案 A：保留当前 per-session key**

```text
key = token_hash
message = session_id
```

优点是无需新增环境变量或数据库字段。

分析它的实际安全边界和局限，不要直接判定不安全。

**方案 B：使用独立服务端 CSRF Secret**

```text
key = 独立服务端秘密
message = session-specific data + random nonce
```

评估：

- 是否需要新增配置项。
- 如何安全生成和管理密钥。
- 多进程、重启和多实例部署是否保持一致。
- 密钥轮换会如何影响现有 Session。
- 缺少密钥时是否 fail-closed。
- 测试和开发环境如何配置。

**方案 C：服务端 Synchronizer Token**

评估是否需要存储与 Session 关联的独立 CSRF Token，以及这是否会引入新的数据库结构或额外的状态管理。

不要为了理论上的完整性而直接新增数据库字段或表。

### 4. 当前实现是否需要修改

必须给出明确结论之一：

```text
KEEP_CURRENT_DESIGN
```

或：

```text
RECOMMEND_MINIMAL_CHANGE
```

如果建议修改，列出：

- 精确修改文件。
- 新增配置或数据库结构需求。
- 对现有 Session 的影响。
- 对现有测试的影响。
- 密钥轮换和部署风险。
- 是否会破坏 API Key 兼容性。

不要以“安全起见”作为唯一理由；必须说明具体威胁和收益。

### 5. M1 是否可以进入提交准备

根据实际证据回答：

```text
CSRF key design: ACCEPTED / NEEDS CHANGE
M1 security: READY / NOT READY
Git commit: NOT EXECUTED
Git push: NOT EXECUTED
```

如果需要调整密钥方案，先停止并报告，等待确认后再修改。

---

## 四、范围限制

本轮禁止：

- 修改任何代码或配置。
- 修改数据库结构。
- 新增环境变量。
- 连接业务 WMS/ERP 数据库。
- 调用真实 LLM。
- 运行数据库迁移。
- 提交、推送、创建 PR 或部署。
- 进入 M2。
- 开发 Web UI。

只允许读取文件、检查已有测试证据和进行安全设计分析。

---

## 五、最终报告

严格按以下格式输出：

```text
Phase 7 Recovery — M1 Final CSRF Key Design Audit

1. 当前密钥实现
2. Cookie 注入攻击分析
3. 数据库只读泄露威胁分析
4. Session 认证与 CSRF 的区别
5. OWASP Signed Double-Submit 对照
6. 方案 A / B / C 对比
7. 最终建议
8. 是否需要修改代码
9. M1 READY / NOT READY
10. Git 工作区状态

STOP — 本轮不修改代码、不提交、不推送、不进入 M2。
```

