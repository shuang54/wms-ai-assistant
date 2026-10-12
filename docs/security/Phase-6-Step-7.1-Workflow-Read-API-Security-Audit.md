# Phase 6 Step 7.1 — Workflow Read API Security Audit

> 审计对象：Phase 6 Step 7 的四个只读端点（GET /api/workflow-runs/{run_id}、
> /nodes、/timeline、GET /api/workflow-approvals/{approval_id}）
> 审计方式：**真实代码 + 运行时（TestClient）证据**——不依据注释 / README / OpenAPI 推断
> 审计日期：2026-10-09 · 基线：HEAD `3747a66`（工作区含未提交 S5/S6/S7）

---

## 1. Scope

**代码**
```text
backend/app/main.py                     应用入口 / Router 注册（含 S7 workflow_read）
backend/app/api/workflow_read.py        S7 四端点（审计主要对象）
backend/app/api/（全部 13 个模块）       既有 API 认证基线
backend/app/dto/workflow_read_api.py    响应 DTO（敏感字段面）
backend/app/workflow_runtime/read_factory.py / read_service.py  数据路径
backend/app/config.py                   APP_HOST / PROJECT_ID 等默认值
backend/app/db/（workflow_* 模型/仓储）  项目归属数据模型
docker-compose.yml                      仓库内唯一部署类配置
docs/security.md                        项目安全治理文档（关键规划证据）
tests/                                  既有测试 + S7.1 新增审计测试
```

**运行时验证手段**：FastAPI `TestClient`（进程内真实 ASGI 调用；两组——
fake 存储层全业务链 + 真实装配链）。**未启动监听端口的独立服务器**（非必要：
TestClient 与 uvicorn 同栈）。

**不覆盖**：部署环境（本机之外的网络拓扑、外部网关——仓库内无证据，见 §6）。

---

## 2. Authentication（逐个接口）

| 接口 | 要求身份认证？ | 未提供凭据的真实响应 | 依赖全局 dependency/middleware？ | OpenAPI security | 证据 |
| --- | --- | --- | --- | --- | --- |
| `GET /api/workflow-runs/{run_id}` | **否** | 200（数据存在）/ 404 / 502（DB 不可用）——**从不 401/403** | **无**（`main.py` 无 middleware、无全局 dependency） | 无（`securitySchemes` 为空） | E1–E5 |
| `GET /api/workflow-runs/{run_id}/nodes` | **否** | 同上 | 无 | 无 | E1–E5 |
| `GET /api/workflow-runs/{run_id}/timeline` | **否** | 同上 | 无 | 无 | E1–E5 |
| `GET /api/workflow-approvals/{approval_id}` | **否** | 同上 | 无 | 无 | E1–E5 |

**证据**
```text
E1  api/conversations.py:46 明文："不实现 Authentication / Authorization"（既有 API 基线）。
E2  全 backend/app 搜索 HTTPBearer | APIKeyHeader | OAuth2 | get_current_user |
    current_user | Security( | require_auth | verify_api_key ⇒ **0 匹配**。
E3  全 backend/app 搜索 add_middleware | TrustedHost | CORSMiddleware |
    BaseHTTPMiddleware | @app.middleware ⇒ **0 匹配**。
E4  workflow_read.py 源码：无 Depends( / Security( / Header( / 认证构造。
E5  运行时（TestClient，无任何凭据）：
    · fake 存储层：四端点全部 **200**（读取成功）；
    · 真实装配链：四端点响应 ∈ {200, 404, 502, 500}，**从不** 401/403；
    · OpenAPI：components.securitySchemes 缺失、四个操作无 "security" 字段
      ——文档与运行时一致（都无认证）。
```

**应用外部的认证保护**：**仓库内无任何证据**（无网关/反代配置、无 TLS——
见 §6）；部署环境是否有外部保护 ⇒ **NOT VERIFIED**。

---

## 3. Project Authorization

**project_id 的来源（真实数据流）**
```text
客户端 Query 参数 project_id（可选） → 省略时回退 settings.project.project_id
（默认 "vietnam-wai… vietnam-wms"，config.py:278-280）
→ WorkflowReadService 归属过滤：run.project_id == 请求值，否则与"不存在"
   返回**同一 404 同文案**（fail-closed 筛选）
→ 子记录（Node/Approval 均不携带 project_id）经父 Run 校验归属。
```

**结论：项目筛选 ≠ 项目授权（缺口确认）**
```text
· 请求方身份**从未被核验**（无认证层，§2），因此不存在"该身份对项目的
  访问权限"这一概念；
· 任意客户端（零凭据）只需在查询串中**主张**与该资源相符的 project_id，
  即可读取——运行时实测 **200**（tests/test_workflow_read_api_security_audit.py
  ::TestProjectScopeIsNotAuthorization）；
· 跨项目访问（主张不匹配的作用域）⇒ 404，与"资源不存在"**完全同构**
  （三入口 + approval 逐一实测 body 相同）——不泄露存在性 ✓（这是筛选层的
  正确行为，但**不能**升格为授权）；
· 默认项目逻辑：省略参数 ⇒ 默认作用域（默认配置 "vietnam-wms"）；
  客户端可用参数**覆盖**它——默认值不构成任何保护，也不额外扩大范围
  （作用域本身即可被任意主张）；
· 枚举风险的具体形态：安全性完全依赖"资源 ID（+ 其 project_id）未知"；
  无认证 + **无限流**（docs/security.md 规划项，未实现）⇒ 高熵 ID 减轻
  但不消除暴力枚举 / ID 泄漏（日志、消息、前端）后的直接读取。
· 数据模型层面：workflow_run.project_id 为 NOT NULL（服务器端解析的启动
  参数，F-1 后子表冗余携带）——**项目概念存在，但没有任何身份到项目的
  绑定**（无用户 / 无角色 / 无 tenant 映射）。
```

---

## 4. Threat Model

| 风险 | 代码证据 | 复现方法 / 验证方式 | 影响 | 状态 | 最小缓解方案 |
| --- | --- | --- | --- | --- | --- |
| **T1 未认证读取工作流数据** | E1–E5（无认证层；运行时无凭据 200） | `TestClient`：无凭据 GET 四端点 ⇒ 200 | 任何能访问服务的调用方读取 Run 详情 / 节点输出 / 时间线 / 审批状态——WMS 运营数据泄漏 | **FAIL（缺口确认）** | 引入认证边界（§10 最小方案 a/b）；落地前将端点限制在受信网络 / loopback |
| **T2 任意主张 project_id（筛选冒充授权）** | §3 数据流；审计测试 `test_any_client_can_claim_any_project_scope_*` | 零凭据 + `?project_id=<victim>` ⇒ 200 | 跨项目读取（只要知道资源 ID 与该项目标识） | **FAIL（缺口确认）** | project 作用域改由**已认证声明**注入（网关注入头 / token claim），停止信任 Query 参数 |
| **T3 默认作用域回退** | `workflow_read.py::_DEFAULT_PROJECT_ID`（settings 默认 "vietnam-wms"） | 省略参数 ⇒ 默认作用域 200；参数可覆盖 | 无（本身不越权）；但**造成"有默认安全边界"错觉** | **FAIL（弱化项：误导性）** | 缺省 ⇒ 400/403（要求显式作用域）；或由认证层注入且禁止覆盖 |
| **T4 全接口绑定 + 无网络隔离证据** | `config.py:543 APP_HOST 默认 "0.0.0.0"`；`main.py:85-90` 启动路径直接使用 | `python -m backend.app.main` ⇒ 绑定 0.0.0.0:8000 | 同网段可达 ⇒ T1/T2 暴露面扩大 | **FAIL（配置面）/ 部署拓扑 NOT VERIFIED** | 默认改 `127.0.0.1`；生产经反代暴露并加 mTLS / 网络策略 |
| **T5 跨项目与不存在的信息枚举差异** | 404 同码同文案（三入口 + approval 逐一实测 body 相同） | 对比响应 | 无（不可区分）✓ | **PASS** | 保持（回归测试锁定） |
| **T6 错误信息 / 敏感数据泄漏** | 404 固定文案（不含 ID 反射）；502 仅固定文案（注入含密码 URL 的异常 ⇒ 文本无泄漏）；无堆栈 / SQL / 路径 | 审计测试 `TestInformationLeakage` | 无 ✓ | **PASS** | 保持（回归测试锁定） |
| **T7 无限流 / 无速率控制** | docs/security.md 规划"限流 → Phase 6+"（未实现）；全层搜索无 limiter | — | 配合 T1 放大枚举 / 抓取能力 | **FAIL（缺口确认，低-中）** | 网关限流 + 审计日志（§10） |
| **T8 子记录归属绕过** | StoreAdapter → ReadService 显式校验 `workflow_run_id` 归属 + 经父 Run 项目校验 | 归属不一致 ⇒ fail-closed（S6/S7 测试） | 无 ✓（防御已实证） | **PASS** | 保持 |
| **T9 响应 DTO 泄漏 input / 内部对象** | 显式字段映射；input / request_id / ORM / Session 均不在 DTO（键 + 值扫描测试） | 审计与既有测试 | 无 ✓ | **PASS** | 保持 |

---

## 5. Sensitive Data

```text
响应 DTO：input（启动输入）与 request_id → **不返回**（键+值扫描，含注入标记）；
         ORM / Session / Connection / metadata → DTO 结构不含（逐项断言）；
         Node output 原样透传（既有安全边界；不解析/不截断/不修复）；
         Approval 无 created_at（不伪造）。
错误响应：404 固定文案（无 ID 反射 / 无内部路径）；502 固定文案（无异常正文 /
         SQL / 凭据——注入含密码 URL 的异常验证通过）；500 固定文案（无堆栈）。
日志：   workflow_read.py 仅记录异常**类型名**（extra={"error_type": …}）；
         未记录 Authorization / Token / Cookie / 请求体。
枚举差异：跨项目与不存在同构（§4 T5）——无差异 ✓。
```

---

## 6. Deployment Boundary

**已确认（仓库证据）**
```text
· docker-compose.yml = **仅本地开发 PostgreSQL**（"Local Development Database"；
  无 api/app 服务、无 nginx、无网络段声明）；
· 根目录**无** Dockerfile、无 nginx.conf、无 compose.yaml / 生产 compose；
· 全 backend 无 TrustedHost / forward 信任 / PROXY_ 配置（无"信任网关注入身份"
  的实现——因此也不能以"未来由网关认证"认定当前受保护）。
```

**无法确认（NOT VERIFIED）**
```text
· 实际部署环境是否存在反向代理 / 网关 / 网络隔离（仓库无证据）；
· 生产启动方式（README 推荐 uvicorn 默认 loopback；而 `python -m
  backend.app.main` 路径按配置默认绑定 0.0.0.0——两者行为不同）；
· 是否有外部限流 / WAF / mTLS；
· OpenAPI 与真实运行时一致（均无认证）——此项已确认，不属 NOT VERIFIED。
```

---

## 7. Tests（真实运行记录）

| 命令（实际执行） | 结果 |
| --- | --- |
| `python -m pytest -q tests/test_workflow_read_api_security_audit.py` | **14 passed / 0 failed / 0 skipped**（新增审计测试） |
| `python -m pytest -q tests/test_workflow_read_api.py tests/test_workflow_runtime_read_service.py` | **54 passed / 0 failed / 0 skipped**（离线基线） |
| `$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_read_api_db.py tests/test_workflow_runtime_read_service_db.py` | **14 passed / 0 failed / 0 skipped**（DB-gated 基线） |
| 组合回归（审计 + 两个离线文件） | **68 passed**（见 S7.1 完成报告最终数字） |
| `python -m compileall -q backend tests scripts` | rc 0 |
| `git diff --check` | PASS |

**状态口径**：审计测试的 **绿色 = "当前缺口行为被确认"**（非"安全通过"）；
上表所有 PASS 均为功能/回归层面的通过；**安全层面的评定见 §9**。
未被验证项：部署环境网络边界（§6）——**NOT VERIFIED**。

---

## 8. Git Changes

```text
新增（审计产物）：
  tests/test_workflow_read_api_security_audit.py
  docs/security/Phase-6-Step-7.1-Workflow-Read-API-Security-Audit.md（本文件）
修改：**无**（本阶段未修改任何生产逻辑；S5/S6/S7 未提交工作全部保留）
git diff --check → PASS
```

---

## 9. Deployment Gate

```text
判据核对（S7.1 §十一.9）：
  · 未认证请求能够读取敏感工作流数据？           **是**（E1–E5，运行时实证）
  · 是否有可验证的项目级授权？                   **否**（project_id = 可主张的筛选参数）

⇒ PRODUCTION_DEPLOYMENT = **BLOCKED**

分项：
  认证（Authentication）      : FAIL（不存在）
  项目授权（Authorization）   : FAIL（筛选 ≠ 授权；可任意主张）
  部署边界（Gateway/Network） : NOT VERIFIED（无证据）
  信息泄漏（T5/T6/T9）        : PASS（保持）
  （既有功能回归 68 passed 不改变上述结论。）
```

---

## 10. Minimum Next Step

**仅针对本审计确认的问题，最小独立实施阶段（待批准；本阶段不实施）**：

```text
Phase 6 Step 7.2 — Workflow Read API 认证与作用域注入（最小修复）
  前置：选择认证承载（推荐：**部署层网关**，零应用改动优先）：

  方案 A（推荐；先部署层、后应用最小改）
    1. 反代（Nginx/Traefik）终结 TLS + API Key 或 JWT 校验；
    2. 网关将**可信项目作用域**经固定头注入（如 X-Project-Scope），并剥离
       客户端自带的同名头；
    3. 应用最小改（独立任务书）：
       a) 新增 auth dependency（校验网关断言；未满足 ⇒ 401）；
       b) workflow_read.py 的 project_id 来源：Query → **已认证断言**；
       c) 缺省作用域：默认回退 → **拒绝（400/403）**；
       d) 网关/应用限流 + 访问审计日志。
  方案 B（无网关环境的最小应用内方案）
    a) 应用内 API Key（环境变量轮换）+ 同样的作用域改由 token 声明；
    b) 同 A.3.c/d。
  落地前的临时措施（如必须短暂暴露）：
    a) 启动改绑 127.0.0.1 / 受控网络；b) 或临时下线四个端点（路由摘除是可
       逆的一行改动，但属生产修改——需单独批准）。

  不改变：WorkflowReadService / Store Adapters / Runtime / Schema。
```

---

## 11. 五个问题的直接回答（S7.1 §一）

```text
1. 未认证用户能否读取 Workflow Run 和 Approval？
   → **能**（四端点；运行时实证，从不 401/403）。
2. 修改 project_id 能否访问其他项目的数据？
   → **能**（只要知道资源 ID 与该项目标识；零凭据 + 主张匹配作用域 ⇒ 200）。
3. 是否存在默认项目/默认作用域导致的越权？
   → 默认作用域（"vietnam-wms"）本身不越权，但**可被客户端任意覆盖**，
     且在无认证下不构成保护（与 1/2 同根因；属弱化/误导项）。
4. 是否存在可信网关、反向代理或网络边界保护？
   → 仓库内**无任何证据**（NOT VERIFIED）；默认 APP_HOST=0.0.0.0 的启动
     路径**存在**（风险面）。
5. 当前 API 是否可以部署到生产环境？
   → **不可以**（PRODUCTION_DEPLOYMENT = BLOCKED）。
```

---

## 12. S7.2 修复后状态（2026-10-09 更新 —— Phase 6 Step 7.2）

> 本节记录 S7.2"最小应用层 API Key 认证 + 服务端项目作用域授权"对
> 上文缺口的修复结果。**实现方式**：`backend/app/api/workflow_read_auth.py`
> （`X-API-Key` + `secrets.compare_digest`；授权作用域仅来自服务端配置
> `WORKFLOW_READ_API_PROJECT_ID`）+ 四个端点统一 `Security()` 依赖 +
> 移除客户端 `project_id` 查询参数。

| 上文风险 | S7.2 后状态 | 证据 |
| --- | --- | --- |
| T1 未认证读取 | **FIXED（应用层）**：四端点缺 / 错凭据 ⇒ 401；未配置 ⇒ 503（fail closed，绝不匿名放行） | `tests/test_workflow_read_api_security_audit.py::TestAuthenticationRequired`（离线）+ DB-gated 真链用例 |
| T2 任意主张 project_id | **FIXED（应用层）**：作用域**仅**来自服务端配置；客户端 `?project_id=` 被忽略、不能扩权；跨项目资源 ⇒ 404（与不存在同构） | `TestAuthorizedScope`（含 §7.1.5–10）+ OpenAPI security 断言 |
| T3 默认作用域回退 | **FIXED**：默认回退已移除——未配置作用域 ⇒ 503（含资源属 `vietnam-wms` 亦不读取） | `test_no_scope_configured_never_falls_back_to_default_project` |
| T7 无限流 | **未变**（仍缺；属部署层/后续阶段） | — |
| T4 网络边界 / HTTPS | **NOT VERIFIED（未变）**：仓库内仍无网关 / 反代 / TLS 配置；`APP_HOST` 默认 `0.0.0.0` 的启动路径仍存在 | `TestDeploymentBoundaryEvidence`（保留断言） |
| T5 / T6 / T8 / T9（泄漏 / 枚举 / 归属 / DTO） | **PASS（保持）** | 既有 + S7.2 回归 |

**部署状态（S7.2 后；§八 —— 如实记录）**
```text
Application authentication   = PASS（应用层已实现并验证）
Project authorization        = PASS（服务端作用域绑定；客户端不可主张）
HTTPS                        = NOT VERIFIED（无仓库证据）
Network boundary             = NOT VERIFIED（无仓库证据）
PRODUCTION_DEPLOYMENT        = BLOCKED（等待 HTTPS / 网络边界的独立部署验证：
                               外部不可绕过网关 / 只有可信网关传递身份 /
                               网关覆盖而非信任客户端身份头）
```

**S7.2 最小修复未覆盖（保持记录，不伪造）**
```text
· 多用户 / 多项目 / RBAC：单凭据绑定单项目模型（API Key ≠ 完整身份系统）；
· 凭据轮换 / 审计日志平台 / 限流：未实现（部署层或后续阶段）；
· 部署层验证（TLS 终结、网络隔离、网关覆盖身份头）：NOT VERIFIED。
```
