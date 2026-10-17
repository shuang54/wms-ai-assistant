# Phase 8 Step 3 — Production Deployment Readiness

> **本文件是上线前检查表与 Runbook，不是"已上线"的证明。**
>
> 本阶段**没有**连接生产服务器、**没有**修改 DNS、**没有**申请或替换正式证书、
> **没有**连接生产数据库、**没有**开放公网监听。
>
> 当前生产结论：**`Production Deployment = BLOCKED`**（依据见 §9）。

---

## 0. 证据基线（写作时的真实状态）

| 项 | 值 / 来源 |
| --- | --- |
| 分支 / HEAD | `phase7` / `b004eff`（Phase 8 Step 2 提交） |
| 部署模板 | `deploy/nginx/wms-workflow-ui.conf.template`、`deploy/nginx/proxy-headers.conf`、`deploy/production.env.example` |
| 本机网关验证 | `tests/test_gateway_validation.py`（门控 `RUN_GATEWAY_VALIDATION=1`）**48 passed** |
| 模板静态校验 | `tests/test_deployment_config.py` **42 passed / 1 skipped** |
| 浏览器只读面 | `tests/test_workflow_ui_security.py` **91 passed** + Phase 7 真实 E2E 6 passed |
| 认证 / 授权实现 | `backend/app/api/auth.py`、`backend/app/api/authorization_dependency.py`、`ai_ops.authorization_grant` |
| 进程管理 | **仓库不提供**（无 systemd unit / Procfile / supervisor / gunicorn conf） |
| CI 部署自动化 | **不存在**（`.github/workflows/observability-matrix-gate.yml` 仅测试门） |

> 上述均为**本地**证据。它们证明"配置与代码行为符合设计"，**不证明**任何生产主机、
> 生产证书或生产网络的真实状态。

---

## 1. 应用配置（4.1）

### 1.1 配置来源与优先级（真实代码）

`backend/app/config.py` 使用 `python-dotenv` + `os.getenv`：

```text
进程环境变量  >  仓库 .env（load_dotenv，override=False）  >  代码默认值
```

**读取函数分为两档（Phase 8 Step 4 / 4A 已加固其中安全关键项）：**

| 函数 | 缺失 / 非法时行为 | 说明 |
| --- | --- | --- |
| `_get_str(key, default)` | 返回 `default` | 仍 fail-open；`APP_ENV` / `APP_HOST` 的**语义**由构造期守卫补足 |
| `_get_int(key, default)` | 解析失败 ⇒ 返回 `default` | 仍 fail-open（残留风险，见 §10） |
| `_get_bool(key, default)` | 仅 `1/true/yes/on` 为真，**其它一律 False** | 仅保留给**非安全关键**开关 |
| `_get_bool_strict(key, default)` | **非法值 ⇒ 抛错** | ✅ Step 4：`AUTH_COOKIE_SECURE` 专用 |
| `_get_int_clamped` | 越界钳制到边界 | 仍 fail-open |

> 早期版本的本节曾写"所有读取函数都不会启动失败"——该表述在 Step 4/4A 后**已不成立**。

### 1.2 `APP_ENV` 环境契约（Step 4A）

```python
app_env: str = field(default_factory=lambda: _get_str("APP_ENV", ""))
```

**默认空串 = 未声明**，不再伪装成 `development`。解析为三态：

| `APP_ENV` 输入 | 解析结果 | 监听地址限制 |
| --- | --- | --- |
| `production` / `prod`（大小写、空白无关） | `production` | **必须**回环，否则启动失败 |
| `development` / `dev` / `test` / `testing` / `ci` / `local` | `non-production` | 不限制 |
| 缺失 / `""` / 纯空白 | `unspecified` | 回环或"沿用默认值"允许；**显式非回环 ⇒ 拒绝** |
| 其它非空值（`staging` / `prodction` / `prd` …） | — | **启动失败**（拼写保护，不降级为开发） |

两个只读属性（供部署检查使用，**不得**被当作"服务器已验证"）：

```python
Settings.environment_kind            # 'production' | 'non-production' | 'unspecified'
Settings.production_binding_verified # 仅当 environment_kind == 'production'
                                     # 且 APP_HOST 为回环时才为 True
```

**边界（重要）**：`production_binding_verified` 只证明**配置意图**符合要求，
**不证明**目标主机上真实的监听 socket、防火墙或安全组状态。
真实监听必须按 §1.5 实测；`unspecified` 环境下该值恒为 `False`，
**不得**被任何部署检查解读为"生产配置已通过"。

### 1.3 已知风险：`APP_HOST` 默认 `0.0.0.0`

```python
app_host: str = field(default_factory=lambda: _get_str("APP_HOST", "0.0.0.0"))
```

- **默认值未被修改**（修改默认值属行为变更，需单独评审）；
- 生产**必须**通过环境变量显式设 `APP_HOST=127.0.0.1`（模板已给出，见
  `deploy/production.env.example`）；
- 由于默认值仍是通配地址：**未声明 `APP_ENV` 且未显式设置 `APP_HOST`** 时仍允许
  启动（为兼容开发/测试）⇒ 生产必须以 §1.2 的三态契约显式声明环境；
- 因此**不能只看配置文件**，必须用 §1.5 的方法实测监听地址。

### 1.4 启动命令（生产必须显式绑定）

```bash
# 由部署层的进程管理器拉起（仓库不提供 unit 文件）
APP_HOST=127.0.0.1 APP_PORT=8000 \
  python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --log-level info
```

- 命令中 **`--host 127.0.0.1` 与 `APP_HOST` 双重限定**：任一失效仍不暴露；
- **不要**使用 `--reload`（开发特性）；
- uvicorn `--forwarded-allow-ips` 保持默认（仅 `127.0.0.1`），不要放宽。

### 1.5 验证实际监听地址（必须实测）

```bash
# 期望：只有 127.0.0.1:8000，没有 0.0.0.0:8000 / :::8000
ss -lntp | grep -E ':8000\b'
# 或
netstat -lntp | grep ':8000'
```

```text
[ ] 输出仅含 127.0.0.1:8000
[ ] 不存在 0.0.0.0:8000 或 [::]:8000
```

### 1.6 开发配置进入生产的风险

| 风险 | 检查点 |
| --- | --- |
| 仓库 `.env` 被复制到生产 | 生产使用平台 secret 注入，**不**拷贝 `.env` |
| `APP_ENV` 未设为 `production` | `deploy/production.env.example` 已固定 |
| `AUTH_COOKIE_SECURE=false` 残留 | §4 用真实响应头验收 |
| `DATABASE_ECHO=1` 导致 SQL 入日志 | 模板固定 `0` |
| 服务间 `*_API_KEY` 为空 | 未使用该能力时可不设；**不得**进入浏览器 |

---

## 2. TLS 与域名（4.2）

| 项 | 要求 | 当前状态 |
| --- | --- | --- |
| 生产域名 | `<PRODUCTION_HOST>`（由责任人确认） | **BLOCKED**（未提供） |
| DNS 解析责任人 | 由基础设施负责人执行 A/AAAA 记录 | **NOT VERIFIED** |
| 证书来源 | Let's Encrypt（ACME）或企业 CA | **BLOCKED**（未提供） |
| 证书链 | 中间证书必须随站点证书下发（fullchain） | **NOT VERIFIED** |
| 有效期 | ≥ 30 天，且监控到期 | **NOT VERIFIED** |
| 私钥存储 | 仅部署机 `/etc/nginx/tls/`，`600 root:root`；**不入库**（`.gitignore` 已忽略 `*.key` 等） | **NOT VERIFIED** |
| 续期 | ACME 客户端（如 certbot）自动续期 + `nginx -s reload` | **NOT IMPLEMENTED**（仓库无自动化） |
| 续期失败告警 | 到期前 14/7/1 天告警 | **NOT IMPLEMENTED** |
| HTTP→HTTPS | 80 端口仅 `301`（已在本机容器验证） | 本机 PASS / 生产 **NOT VERIFIED** |

**验收命令**（在目标机执行）：

```bash
openssl s_client -connect <PRODUCTION_HOST>:443 -servername <PRODUCTION_HOST> </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
curl -sI http://<PRODUCTION_HOST>/ | head -1        # 期望 301
curl -sI https://<PRODUCTION_HOST>/ | head -1       # 期望 200
```

```text
[ ] 证书 subject/SAN 与域名一致
[ ] 颁发者与预期 CA 一致
[ ] notAfter ≥ 30 天
[ ] 证书链完整（openssl verify 通过）
[ ] HTTP → HTTPS 301
```

> **本机自签名证书（`%TEMP%/wms-gw-*/tls/`）只用于容器验证，不构成生产 TLS 证据。**

---

## 3. 网络隔离（4.3）

### 3.1 目标拓扑

```text
External Client ──HTTPS(443)/HTTP(80)──▶ Nginx ──▶ 127.0.0.1:8000 ──▶ PostgreSQL
```

### 3.2 必须满足

```text
[ ] Nginx 仅对外开放 80 / 443
[ ] 后端 8000 只监听 127.0.0.1
[ ] 云安全组入站规则不含 8000（也不含 5432 / 5433）
[ ] 主机防火墙（ufw/firewalld/Windows Firewall）默认拒绝入站 8000
[ ] 无其它容器端口映射到公网
[ ] 无第二层反向代理 / 负载均衡绕过本网关
[ ] 后端不信任任意来源的 X-Forwarded-*（当前实现：**完全不读取**）
```

### 3.3 本阶段实测到的风险（证据）

| 风险 | 证据 | 状态 |
| --- | --- | --- |
| dev compose 端口发布在 `0.0.0.0` | 原为 `"${POSTGRES_PORT:-5432}:5432"` / `"${WMS_TEST_DB_PORT:-5433}:5432"` | **已在 Phase 8 Step 4 修复为 `127.0.0.1:` 前缀**（`docker compose config` 输出 `host_ip: 127.0.0.1`）。已运行容器需 recreate 才生效 |
| 后端默认 `APP_HOST=0.0.0.0` | `config.py` | **Step 4 已加约束**：`APP_ENV=production` 且 `APP_HOST` 非回环 ⇒ 启动失败。默认值本身未改（避免影响开发/测试） |
| 无防火墙 / 安全组配置入库 | 仓库无 IaC | 仍 **NOT VERIFIED**，完全依赖部署层 |

**缓解措施**：

```text
· 生产主机禁止运行 docker-compose.yml / docker-compose.test.yml
· dev compose 已固定 127.0.0.1 绑定；已运行容器需 `docker compose up -d` 重建后生效
· 生产启动必须 APP_ENV=production + APP_HOST=127.0.0.1（否则启动失败）
· 安全组默认拒绝入站，仅显式放行 80/443
```

### 3.4 三层验证必须分开记录

```text
本地端口绑定测试   ≠   生产防火墙验证   ≠   公网网络隔离验证
   （已完成）          （未执行）            （未执行）
```

本机 Docker 验证只能证明"容器把端口发布到 127.0.0.1"，**不能**推断生产主机、
安全组或公网路径的真实状态。

**必须单独标记 `NOT VERIFIED`**：

```text
· 真实服务器上的后端监听地址
· 云安全组 / 主机防火墙是否阻断外部直连 8000
· 生产 TLS 证书与域名
· 生产 HTTPS 从公网的可达性
· 是否存在其它监听地址或旁路入口
```

---

## 4. 认证与会话安全（4.4）

### 4.1 当前真实行为（代码证据）

| 项 | 实现 | 位置 |
| --- | --- | --- |
| 登录 | `POST /api/auth/login` | `backend/app/api/auth.py` |
| 登出 | `POST /api/auth/logout` | 同上 |
| 当前用户 | `GET /api/auth/me`（Web 网关**不暴露**） | 同上 |
| 会话 Cookie | `httponly=True`、`secure=AUTH_COOKIE_SECURE`（默认 true）、`samesite=AUTH_COOKIE_SAMESITE`（默认 lax）、`path=/` | 同上 |
| CSRF Cookie | 非 HttpOnly（双提交模式要求 JS 可读），值与会话绑定签名 | 同上 |
| 项目授权 | `enforce_project_access(user_id, project_id)` → `ai_ops.authorization_grant` | `api/authorization_dependency.py` |
| 错误语义 | 未登录 401 / 非成员 403 / 跨项目 404（同构，不泄漏存在性） | `api/workflow_ui_read.py` |
| CORS | **无中间件**（同源部署） | `main.py` |

### 4.2 生产验收步骤

```text
[ ] 未登录 GET /api/ui/projects/<p>/workflow-runs/<r> → 401
[ ] POST /api/auth/login 成功后 Set-Cookie 含 HttpOnly + Secure + SameSite=Lax
[ ] 响应头中 Cookie 无 Domain 泄漏、Path=/
[ ] 已授权项目 GET → 200
[ ] 未授权项目 GET → 403（且不提示重新登录）
[ ] 跨项目 Run → 404（不泄漏 Run ID / 项目名）
[ ] GET /api/auth/login → 403（网关方法限制）
[ ] GET /api/auth/logout → 403（网关方法限制）
[ ] GET /api/auth/me → 404（网关未暴露）
[ ] POST /api/auth/logout → 204/200 且会话失效
[ ] 会话过期后 GET → 401
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<PRODUCTION_HOST>/api/ui/projects/x/workflow-runs/y
curl -sI -X POST https://<PRODUCTION_HOST>/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"<u>","password":"<p>"}' | grep -i set-cookie
```

### 4.3 代理后的安全要点

```text
· Host / X-Forwarded-Host 由网关用 $server_name 覆盖（Step 2 已修复 Host 头注入）
· X-Forwarded-For 用 $remote_addr（客户端值被丢弃）
· 后端完全不读取 X-Forwarded-*，因此这些头无法影响认证 / 授权判定
· 若将来引入「按来源 IP 限流」等能力，必须先定义可信代理地址范围
```

---

## 5. 资源与运维（4.5）

| 项 | 现状 | 状态 |
| --- | --- | --- |
| CPU / 内存 / 磁盘检查 | 仓库无监控脚本 | **NOT IMPLEMENTED** |
| 日志位置 | 后端 stdout/stderr（由进程管理器收集）；Nginx `access.log` / `error.log` | 依赖部署层 |
| 日志轮转与保留 | 仓库无 logrotate 配置 | **NOT IMPLEMENTED** |
| 启动顺序 | Nginx 可在后端未就绪时启动（503），但**推荐**先后端后 Nginx | 见 Runbook |
| 异常退出恢复 | 依赖进程管理器 `Restart=always` | **仓库不提供** |
| 健康检查 | `GET /api/health`（返回 `status` / `database`）；**网关对 `/api/` 其余路径返回 404，故不得从公网探活** | 可用，需在主机本地探测 |
| 部署成功判定 | 见 §6 Step C 清单全绿 | — |
| 前后端版本一致 | 静态资源随同一次发布产物部署；`index.html` 引用固定文件名（无 hash）⇒ **必须整体替换，不可混部** | 手工保证 |
| 镜像 / 依赖固定 | 网关镜像固定 `nginx:1.27-alpine`；Python 依赖见 `requirements*.txt` | 部分 |
| 限流 | — | **NOT IMPLEMENTED** |
| 审计日志（访问 / 操作） | — | **NOT IMPLEMENTED**（应用侧已有业务审计表，但接入面未启用） |
| 告警 | — | **NOT IMPLEMENTED** |
| 多实例 / 会话粘性 | 单实例假设（会话存 DB，理论可多实例，但未验证） | **NOT VERIFIED** |

> 本阶段**不引入**限流 / 监控 / 告警等新基础设施。上表 `NOT IMPLEMENTED` 项是
> 上线的**已知缺口**，需由项目负责人决定是否作为上线前置条件。

---

## 6. 部署 Runbook

### Step A — 部署前（人工逐项确认）

```text
[ ] 分支 / 提交版本已确认（git rev-parse HEAD 记录到部署单）
[ ] 工作区干净（git status --short 为空）
[ ] 本地测试基线通过：
      · pytest tests/test_deployment_config.py
      · RUN_GATEWAY_VALIDATION=1 pytest tests/test_gateway_validation.py
      · pytest tests/test_workflow_ui_security.py
[ ] 生产环境变量已由责任人注入（secret 管理，非仓库文件）
[ ] 证书与域名已由责任人确认（§2 全部打勾）
[ ] 安全组 / 防火墙规则已由责任人确认（§3.2 全部打勾）
[ ] 备份材料就绪：当前 Nginx 配置、当前静态资源、当前后端版本标记
[ ] 回滚方案已明确（§7）并有人可执行
[ ] 本阶段允许的操作范围已确认（变更窗口 / 影响面）
```

> **"配置文件存在" ≠ "准备完成"**：§2 / §3 的证据必须来自目标环境。

### Step B — 部署（在目标服务器执行）

```bash
# 1) 发布后端版本（示例：按实际目录约定替换 <APP_DIR> / <RELEASE>）
cd <APP_DIR> && git fetch && git checkout <RELEASE_TAG_OR_SHA>

# 2) 安装依赖（示例；按项目实际依赖文件）
python -m pip install -r requirements.txt

# 3) 启动 / 重载后端（由进程管理器执行；仓库不提供 unit 文件）
#    首次：注册服务并 start；后续：restart
systemctl restart <BACKEND_SERVICE_NAME>     # ← 名称由部署层确定

# 4) 发布静态资源（**先 dry-run**，路径校验见 deploy/README.md §6.1）
rsync -a --delete --dry-run <APP_DIR>/frontend/index.html \
      <APP_DIR>/frontend/styles.css <APP_DIR>/frontend/js/ <STATIC_ROOT>/
# 人工审阅 dry-run 输出后执行
rsync -a --delete <APP_DIR>/frontend/index.html <APP_DIR>/frontend/styles.css \
      <APP_DIR>/frontend/js/ <STATIC_ROOT>/

# 5) 校验并重载 Nginx（配置由 deploy/nginx/* 渲染而来）
nginx -t && systemctl reload nginx
```

```text
占位符说明（尚未确定，必须由部署层填写）：
  <APP_DIR>                应用目录
  <RELEASE_TAG_OR_SHA>     本次发布版本
  <BACKEND_SERVICE_NAME>   后端进程管理器单元名
  <STATIC_ROOT>            静态资源目录（与 Nginx 模板中的 <STATIC_ROOT> 一致）
```

### Step C — 部署后验收（逐项留证）

```text
[ ] Nginx 配置检查通过            nginx -t                       → exit 0
[ ] Nginx reload 成功             systemctl is-active nginx       → active
[ ] 后端进程正常                  systemctl is-active <SVC>       → active
[ ] 后端监听地址正确              ss -lntp | grep :8000           → 仅 127.0.0.1
[ ] HTTP 正确跳转 HTTPS           curl -sI http://<HOST>/         → 301
[ ] 证书链与域名校验通过          openssl s_client …              → verify return:1
[ ] 前端页面可加载                curl -sI https://<HOST>/index.html → 200
[ ] 登录与退出正常                POST /api/auth/login → 200 + Set-Cookie
[ ] Project Grant 授权正常        已授权项目 GET → 200
[ ] 跨项目访问被拒绝              → 404（不泄漏 ID）
[ ] 未授权 API 路由被拒绝         /api/unknown → 404；/api/auth/me → 404
[ ] 后端端口不能外部直连          外部 curl http://<HOST>:8000/ → 连接失败
[ ] 日志无敏感信息泄漏            grep -iE 'password|token|authorization' <log>
[ ] 错误响应不暴露内部堆栈        500 响应体不含 traceback
```

### Step D — 回滚（见 §7）

---

## 7. 回滚 Runbook

> **本仓库没有自动回滚机制**（无 CI 部署、无版本管理器）。以下为人工步骤。

### 7.1 触发条件

```text
· Step C 任一项失败且 15 分钟内无法修复
· 出现认证 / 授权异常（401/403 误判、会话无法建立）
· 静态资源新旧混部导致页面报错
```

### 7.2 回滚前：路径与版本校验（Phase 8 Step 4 加固）

```bash
APP_DIR=<APP_DIR>            # 源码工作区
STATIC_ROOT=<STATIC_ROOT>    # 已发布静态产物
BACKUP_DIR=<BACKUP_DIR>      # 备份目录（含时间戳）
DB_DATA_DIR=<DB_DATA_DIR>    # 数据库数据目录（**禁止**进入同步 / 删除范围）

# 目录存在性 + 危险路径拦截
for d in "$APP_DIR" "$STATIC_ROOT" "$BACKUP_DIR"; do
  [ -d "$d" ] || { echo "目录不存在: $d"; exit 1; }
done
[ "$STATIC_ROOT" = "/" ] && { echo "拒绝: STATIC_ROOT 不能是 /"; exit 1; }
[ "$APP_DIR" = "$STATIC_ROOT" ] && { echo "拒绝: 源码与产物目录不得相同"; exit 1; }
case "$STATIC_ROOT" in
  "$DB_DATA_DIR"*) echo "拒绝: 产物目录不得位于数据库数据目录内"; exit 1;;
esac

# 版本确认：回滚目标必须与备份记录一致
cat "$BACKUP_DIR/prev_revision.txt"          # 部署前记录的上一版本
git -C "$APP_DIR" rev-parse --short HEAD     # 当前版本（应为本部署版本）
```

### 7.3 回滚步骤

```bash
# 1) 恢复 Nginx 配置（配置不合格 ⇒ 立即停止，保留旧版本）
cp "$BACKUP_DIR/nginx/conf.d/default.conf" /etc/nginx/conf.d/default.conf
cp "$BACKUP_DIR/nginx/proxy-headers.conf" /etc/nginx/proxy-headers.conf
nginx -t || { echo "配置校验失败：已停止，未 reload"; exit 1; }
systemctl reload nginx

# 2) 恢复静态资源 —— **先 dry-run**
rsync -a --delete --dry-run "$BACKUP_DIR/static/" "$STATIC_ROOT"/   # 人工审阅
# 审阅通过后执行
rsync -a --delete "$BACKUP_DIR/static/" "$STATIC_ROOT"/

# 3) 恢复后端版本（先确认工作区干净，避免覆盖操作者未提交修改）
git -C "$APP_DIR" status --porcelain | grep . && {
  echo "工作区有未提交修改：已停止，请人工处置"; exit 1; }
git -C "$APP_DIR" checkout "$(cat "$BACKUP_DIR/prev_revision.txt")"
python -m pip install -r requirements.txt
systemctl restart <BACKEND_SERVICE_NAME>
```

### 7.4 回滚约束

```text
[ ] 破坏性同步前必须先 dry-run，并有人工审阅记录
[ ] `rsync --delete` 只作用于 $STATIC_ROOT；绝不作用于源码 / DB 数据目录 / 家目录
[ ] 数据库数据目录（DB_DATA_DIR）**永不**纳入同步或删除范围
[ ] **不**执行任何数据库迁移回滚（本阶段无 migration；如未来有，需单独方案）
[ ] **不**覆盖业务数据 / 不删表 / 不 restore 数据库（除非独立审批）
[ ] `git checkout` 前工作区必须干净，避免覆盖操作者未提交修改
[ ] 回滚后必须重跑 Step C 的 1–10 项
[ ] 记录失败原因、回滚时间、恢复后的验证结果到部署单
```

### 7.5 回滚完成判定

```text
[ ] nginx -t 通过且服务 active
[ ] 后端 active 且监听 127.0.0.1:8000
[ ] 页面 200、登录可用、已授权项目可读、跨项目 404
[ ] 无残留新版本文件（rsync --delete 已覆盖）
[ ] 数据库数据目录未被触碰（部署单记录备份前后校验值）
```

> **不存在**蓝绿发布 / release 目录 / 自动回滚机制；以上全部为人工步骤。
> 若未来引入 release 目录或蓝绿切换，需单独设计与评审。

---

## 8. 判定规则（Readiness Gate）

| 状态 | 含义 |
| --- | --- |
| **PASS** | 有**实际证据**（命令输出 / 测试结果 / 目标环境响应）证明条件满足 |
| **BLOCKED** | 必要条件未满足，或缺少证据，不得上线 |
| **NOT APPLICABLE** | 明确不适用于当前部署方式，并已说明原因 |
| **NOT VERIFIED** | 尚未在**目标环境**验证 |

**规则：`NOT VERIFIED` 永远不得自动升级为 `PASS`；本地证据不得替代目标环境证据。**

---

## 9. 当前 Readiness 结论

### 9.1 分项状态

| # | Gate | 状态 | 依据 |
| --- | --- | --- | --- |
| 1 | **Local Gateway Validation** | **PASS** | Step 2：`RUN_GATEWAY_VALIDATION=1` → 48 passed（真实 nginx 1.27.5 容器 + 真实 HTTP） |
| 2 | 生产配置模板正确性 | **PASS** | `test_deployment_config.py` 42 passed（占位符、无凭据、方法/路径白名单、Host 覆盖） |
| 3 | 生产配置**实际注入** | **BLOCKED** | 真实环境变量未注入（无目标环境） |
| 4 | **Production TLS** | **BLOCKED / NOT VERIFIED** | 无真实证书、无域名、无 `openssl` 验收记录 |
| 5 | **Production Network Isolation** | **NOT VERIFIED** | 无安全组 / 防火墙 / 主机监听证据。dev compose 已在 Step 4 收紧为 `127.0.0.1`（运行中容器需 recreate 才生效），但本项仍需**目标主机**证据 |
| 6 | 认证与会话**实现** | **PASS** | 91 UI 安全测试 + Phase 7 真实 E2E 6 passed + DB-gated 54 passed |
| 7 | 认证与会话**生产验收** | **NOT VERIFIED** | 未在 HTTPS 生产环境执行 §4.2 |
| 8 | 进程管理 / 自愈 | **BLOCKED** | 仓库无 unit / supervisor；依赖部署层 |
| 9 | 日志轮转 / 监控 / 告警 / 限流 | **NOT IMPLEMENTED** | 仓库无相关配置 |
| 10 | 部署与回滚自动化 | **NOT APPLICABLE** | 项目无 CI 部署；本文件提供**人工** Runbook |
| 11 | 多实例 / 会话粘性 | **NOT VERIFIED** | 单实例假设，未验证横向扩展 |
| 12 | **Production Deployment** | **BLOCKED** | 依赖 3/4/5/7/8 全部转 PASS |

### 9.2 强制 Gate（全部打勾才可上线）

```text
[ ] TLS 有真实证据：证书 + 完整链 + 有效期 ≥30 天 + 续期与 reload 方案
[ ] 网络隔离有真实证据：安全组 / 防火墙 + 实测外部不可直连 8000
[ ] 认证与会话按 §4.2 全部通过（含 Secure Cookie 与跨项目 404）
[ ] 进程管理到位：自愈重启 + 日志轮转 + 主机本地健康检查
[ ] 回滚演练完成一次，并留存记录
```

> 这些是**上线前置条件**，不是当前状态。当前状态见 §9.1。

### 9.3 允许带缺口上线的项（需项目负责人书面确认）

```text
· 限流 / 告警 / 审计接入（当前 NOT IMPLEMENTED）
· 多实例扩展（当前单实例）
```

---

## 10. 未验证项汇总

```text
NOT VERIFIED（必须由目标环境提供证据）
1. 生产主机后端实际监听地址
2. 云安全组 / 主机防火墙是否阻断 8000
3. 生产 TLS 证书、链、有效期、续期
4. 生产 HTTPS 从公网可达性与跳转一致性
5. 是否存在其它监听端口 / 旁路入口
6. 生产环境下的登录 / 登出 / 会话失效 / 跨项目授权
7. 多实例与会话粘性

BLOCKED（必要条件缺失）
1. 生产域名与证书
2. 生产环境变量注入
3. 进程管理方案
4. 回滚演练

NOT IMPLEMENTED（已知能力缺口）
1. 限流
2. 访问审计接入
3. 告警
4. 日志轮转
```

---

## 11. 最小后续建议

| 优先级 | 建议 | 理由 | 状态 |
| --- | --- | --- | --- |
| P0 | 生产启动命令双重绑定 `--host 127.0.0.1` + `APP_HOST` | 抵消 `0.0.0.0` 默认值 | 文档要求（Step 4 保持） |
| P0 | 非回环监听必须启动失败 | 把"漏配即暴露"变为可发现 | ✅ **Step 4 已实现**（`APP_ENV=production` 校验） |
| P0 | `AUTH_COOKIE_SECURE` 非法值报错而非静默 False | 防 Cookie 静默降级 | ✅ **Step 4 已实现**（`_get_bool_strict`） |
| P1 | dev compose 端口加 `127.0.0.1:` 前缀 | 消除误跑即暴露 | ✅ **Step 4 已实现**（已运行容器需 recreate） |
| P1 | 证书续期 + reload 自动化 + 到期告警 | 可用性 | 未实施 |
| P2 | 限流与访问审计 | 运行期防护 | 未实施 |

> 已实现项由 `tests/test_deployment_hardening.py` 锁定；未实施项**未**声称完成。

---

## 12. 相关文件

```text
deploy/README.md                              部署模板说明与不变量
deploy/nginx/wms-workflow-ui.conf.template    网关模板（占位符）
deploy/nginx/proxy-headers.conf               可信代理转发头
deploy/production.env.example                 生产环境变量清单
tests/test_deployment_config.py               模板静态校验
tests/test_gateway_validation.py              真实 Nginx 行为验证（门控）
tests/test_production_readiness.py            本文件的治理校验
docs/security.md §7                           生产部署 BLOCKED 的既有记录
```
