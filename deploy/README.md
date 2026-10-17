# 生产部署模板（Phase 8 Step 1）

> ⚠️ **本目录是配置模板与责任说明，不是已验证的生产部署。**
> `Production Deployment = BLOCKED` —— 直到真实环境完成独立验证（HTTPS 证书、
> 网关可达性、后端不可绕过、Cookie 安全属性、审计与限流）。
>
> 本阶段**没有**连接任何生产服务器、**没有**申请真实证书、**没有**修改 DNS、
> **没有**开放公网访问。

---

## 1. 目标形态

```text
Browser
   │  HTTPS（TLS 在本层终结）
   ▼
Nginx（生产网关 / 反向代理）        ← deploy/nginx/wms-workflow-ui.conf.template
   ├── 静态 Workflow UI             → <STATIC_ROOT>
   └── 白名单 API
         ├── /api/ui/projects/*            仅 GET / HEAD（只读）
         └── /api/auth/{login,logout}      仅 POST
                    │
                    ▼  127.0.0.1:8000（回环，公网不可达）
            FastAPI（Uvicorn）
                    │
                    ▼
            现有认证（M1 会话 Cookie，HttpOnly + Secure + SameSite=lax）
                    │
                    ▼
            项目授权（M2.4，`ai_ops.authorization_grant`）
                    │
                    ▼
            Workflow Read Layer（只经 Store Ports）
                    │
                    ▼
                PostgreSQL
```

**关键不变量**：

| # | 不变量 |
|---|---|
| 1 | TLS 只由 Nginx 终结；HTTP(80) 只做 301 跳转 |
| 2 | 后端只监听 `127.0.0.1`（`APP_HOST` 默认 `0.0.0.0`，生产**必须**覆盖） |
| 3 | 只转发两份白名单；`/api/` 其余路径 **404**（不做全路径代理） |
| 4 | `/api/ui/` 只允许 `GET`/`HEAD` —— 部署不得把只读面变成写面 |
| 5 | `/api/auth/` 只暴露真实需要且存在的 `login` / `logout`（`POST`） |
| 6 | 不添加任何 `Access-Control-*` 头（同源部署） |
| 7 | 客户端 `X-Forwarded-*` 与 `Host` 由网关**覆盖**（`$remote_addr` / `$server_name`），不透传 |
| 8 | 浏览器不接触数据库；DB 只由后端进程按既有方式连接 |
| 9 | 不关闭、不绕过 Session / 项目授权 / CORS 相关安全检查 |
| 10 | **禁止**使用 `frontend/scripts/dev-gateway.mjs` 承载生产流量 |

---

## 2. 文件

| 文件 | 用途 |
| --- | --- |
| `nginx/wms-workflow-ui.conf.template` | Nginx `server` 全量配置（占位符） |
| `nginx/proxy-headers.conf` | 可信代理转发头片段（被上面 `include`） |
| `production.env.example` | 生产环境变量清单（占位符，无真实值） |
| `README.md` | 本文件：责任边界、验收清单、未决项 |

**本目录不含**：证书 / 私钥 / 真实域名 / 真实密码 / API Key / 数据库连接串 /
服务器 IP。所有值以 `<UPPERCASE_PLACEHOLDER>` 表示。

---

## 3. 部署步骤（人工执行，不在本阶段进行）

1. **准备静态资源**
   把 `frontend/` 下的 `index.html`、`styles.css`、`js/` 发布到 `<STATIC_ROOT>`。
   **不要**发布 `tests/`、`e2e/`、`scripts/`、`node_modules/`、`package*.json`。

2. **准备后端进程**
   ```bash
   # 由进程管理器（systemd / supervisor / 平台）拉起，**只绑回环**
   APP_HOST=127.0.0.1 APP_PORT=8000 \
     python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```
   - 环境变量经平台 secret 注入（见 `production.env.example`）；
   - **不**在本阶段执行 `init_db` 或任何迁移；建表属独立变更窗口；
   - uvicorn 的 `--forwarded-allow-ips`（默认 `127.0.0.1`）必须保持只含回环。

3. **替换 Nginx 模板占位符**
   `<PRODUCTION_HOST>`、`<TLS_CERT_PATH>`、`<TLS_KEY_PATH>`、`<STATIC_ROOT>`。
   两个文件放在同一目录（模板通过 `include proxy-headers.conf` 引用片段）。

4. **证书与续期**
   证书来源与续期责任由部署方确定（Let's Encrypt / 企业 CA）。
   续期失败必须告警；证书私钥**不得**入库（根 `.gitignore` 已忽略 `*.pem` /
   `*.key` / `*.crt` / `*.p12` / `*.pfx`）。

5. **验证（必须逐项留证）**
   ```bash
   nginx -t                       # 配置语法（本仓库未安装 nginx ⇒ 未执行）
   curl -I https://<PRODUCTION_HOST>/            # 200 + HSTS
   curl -I http://<PRODUCTION_HOST>/             # 301 → https
   curl -I https://<PRODUCTION_HOST>/api/health  # 404（健康检查不对外）
   curl -X POST https://<PRODUCTION_HOST>/api/ui/projects/x/workflow-runs/y  # 405/403
   curl -I http://127.0.0.1:8000/                # 仅本机可达（外部应失败）
   ```

6. **破坏性操作防护（部署与回滚通用，必须遵守）**

   > 本仓库**没有**蓝绿发布、release 目录或自动回滚机制；以下全部为**人工**步骤。
   > 不得假设项目已有上述能力。

   **6.1 先定义并校验路径（禁用相对路径）**

   ```bash
   APP_DIR=<APP_DIR>            # 源码工作区
   STATIC_ROOT=<STATIC_ROOT>    # 已发布的静态产物目录
   BACKUP_DIR=<BACKUP_DIR>      # 本次部署备份（含时间戳）
   DB_DATA_DIR=<DB_DATA_DIR>    # 数据库数据目录（**禁止**进入任何同步 / 删除范围）

   for d in "$APP_DIR" "$STATIC_ROOT" "$BACKUP_DIR"; do
     [ -d "$d" ] || { echo "目录不存在: $d"; exit 1; }
   done
   [ "$STATIC_ROOT" = "/" ] && { echo "拒绝: STATIC_ROOT 不能是 /"; exit 1; }
   [ "$APP_DIR" = "$STATIC_ROOT" ] && { echo "拒绝: 源码与产物目录不得相同"; exit 1; }
   case "$STATIC_ROOT" in
     "$DB_DATA_DIR"*) echo "拒绝: 产物目录不得位于数据库数据目录内"; exit 1;;
   esac
   ```

   **6.2 `rsync --delete` 必须先 dry-run**

   ```bash
   # 1) 先看会删什么
   rsync -a --delete --dry-run <SRC>/ "$STATIC_ROOT"/      # 人工审阅
   # 2) 确认无误后执行
   rsync -a --delete <SRC>/ "$STATIC_ROOT"/
   ```

   ```text
   · --delete 只允许作用于 $STATIC_ROOT（静态产物）
   · 绝不作用于 APP_DIR / DB_DATA_DIR / 家目录 / 挂载点
   · 源必须是 frontend/index.html + styles.css + js/，不得整目录同步 frontend/
     （会带上 tests/、e2e/、scripts/、node_modules/）
   · dry-run 出现任何非预期删除 ⇒ **立即停止**，不要执行第二步
   ```

   **6.3 禁止用 `git checkout` 覆盖操作者的未提交修改**

   ```bash
   git -C "$APP_DIR" status --porcelain    # 必须为空，否则停止
   git -C "$APP_DIR" rev-parse --short HEAD > "$BACKUP_DIR/prev_revision.txt"
   ```

   **6.4 必须由有权限的部署人员逐项确认**

   ```text
   [ ] 四个路径的校验结果（6.1 全部通过）
   [ ] rsync dry-run 输出（6.2）
   [ ] nginx -t 结果
   [ ] 后端服务重启 / reload 的执行与结果
   [ ] 回滚触发决定
   ```

7. **回滚（人工）**

   完整步骤、约束与完成判定见
   `docs/decisions/Phase/Phase 8 Step 3 — Production Deployment Readiness.md` §7。

   回滚同样受本节 6.1–6.4 约束：**路径校验 → dry-run → 才允许破坏性同步**，
   且数据库数据目录**永不**纳入同步或删除范围。

---

## 4. 与本仓库其它部分的关系

| 组件 | 定位 |
| --- | --- |
| `nginx/*`（本目录） | **生产**网关模板（本阶段新增，未验证） |
| `frontend/scripts/dev-gateway.mjs` | **仅本地开发**；`NODE_ENV=production` 时拒绝启动；不转发服务间面 |
| `scripts/start-workflow-ui.ps1` | **仅本地开发**；生产不得使用 |
| `frontend/scripts/serve-static.mjs` | 仅本地静态外壳；对 `/api/*` 返回 501（不是代理） |

**明确禁止**：把 `dev-gateway.mjs` 或 `serve-static.mjs` 部署为生产入口；
把 `AUTH_COOKIE_SECURE` 置为 `false`；用 CORS 放宽替代同源部署。

---

## 5. 认证与授权（部署不得改动）

```text
/api/ui/**  → require_session（M1 会话 Cookie）+ enforce_project_access（M2.4）
              未登录 401 · 非成员 403 · 跨项目 404（同构，不泄漏存在性）
```

- 会话 Cookie：`HttpOnly` + `Secure`（`AUTH_COOKIE_SECURE=true`）+ `SameSite=lax`；
- 路径中的 `project_id` 只是请求目标，**不构成授权结论**；
- 网关**不做**任何认证判定，也不注入 / 剥离认证头。

---

## 6. 未决项（阻挡 READY）

| # | 缺口 | 影响 |
| --- | --- | --- |
| 1 | 无真实 TLS 证书与域名 | HTTPS **NOT VERIFIED** |
| 2 | 未在任何真实网关执行 `nginx -t` / 真实请求 | 网关配置**未验证** |
| 3 | 未验证后端在宿主防火墙 / 安全组下确实不可从公网直连 | 绕过风险**未验证** |
| 4 | 未定义限流 / 审计 / 告警 | 运行期防护缺失 |
| 5 | 未定义证书续期自动化与失效告警 | 可用性风险 |
| 6 | 多实例 / 会话粘性未定义（单实例假设） | 横向扩展未验证 |
| 7 | `APP_HOST` 代码默认 `0.0.0.0`（依赖部署覆盖） | 误配即暴露 |

⇒ **`Production Deployment = BLOCKED`**，直到上述各项在真实环境独立验证。
