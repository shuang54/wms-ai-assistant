<#
.SYNOPSIS
    Phase 7 Step 7 —— **本地开发**一键启动 Workflow 运维工作台。

.DESCRIPTION
    按顺序完成：数据库可达性探测 → 端口占用检查 → 启动 Uvicorn →
    等待 /api/health 通过 → 启动既有同源网关（frontend/scripts/dev-gateway.mjs）
    → 输出访问地址与登录说明。

    本脚本**只**做"启动编排"，不修改任何业务行为：

    * 不设置 `DATABASE_URL`（沿用进程环境变量 / 仓库 `.env`）；
    * **不**自动设置 `AUTH_COOKIE_SECURE=false`（需要时**提示**用户自行显式设置）；
    * 不创建用户、不授予项目、不创建 Workflow Run；
    * 不执行数据库迁移 / 初始化（不调用 `init_db`）；
    * 不打印连接串、口令、Cookie 或任何凭据；
    * 只绑定 `127.0.0.1`；`NODE_ENV=production` 时拒绝启动（不绕过网关安全门）。

.PARAMETER BackendPort
    Uvicorn 监听端口（默认 8000）。

.PARAMETER GatewayPort
    同源网关监听端口（默认 8080）。

.PARAMETER HealthTimeoutSeconds
    等待 `/api/health` 通过的秒数（默认 45）。

.PARAMETER AllowNonLocalDatabase
    显式允许 `DATABASE_URL` 指向**非**回环主机（默认拒绝，避免误连生产库）。

.PARAMETER Stop
    停止上一次由本脚本启动的子进程（仅按记录的 PID + 命令行校验，不误杀）。

.EXAMPLE
    .\scripts\start-workflow-ui.ps1

.EXAMPLE
    $env:AUTH_COOKIE_SECURE = "false"     # 仅本地 http；**必须由用户显式设置**
    .\scripts\start-workflow-ui.ps1 -BackendPort 8000 -GatewayPort 8080

.EXAMPLE
    .\scripts\start-workflow-ui.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$GatewayPort = 8080,
    [int]$HealthTimeoutSeconds = 45,
    [switch]$AllowNonLocalDatabase,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ============================================================
# 常量与路径
# ============================================================

$Script:RepoRoot = Split-Path -Parent $PSScriptRoot
$Script:FrontendDir = Join-Path $Script:RepoRoot 'frontend'
$Script:StateFile = Join-Path $Script:FrontendDir '.dev-launch.json'
$Script:BackendOut = Join-Path ([System.IO.Path]::GetTempPath()) 'wms-ui-backend.out.log'
$Script:BackendErr = Join-Path ([System.IO.Path]::GetTempPath()) 'wms-ui-backend.err.log'
$Script:GatewayOut = Join-Path ([System.IO.Path]::GetTempPath()) 'wms-ui-gateway.out.log'
$Script:GatewayErr = Join-Path ([System.IO.Path]::GetTempPath()) 'wms-ui-gateway.err.log'

$Script:BackendProc = $null
$Script:GatewayProc = $null

# ============================================================
# 输出辅助（**不打印任何凭据**）
# ============================================================

function Write-Step([string]$Message) {
    Write-Host "[..] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[!!] $Message" -ForegroundColor Yellow
}

function Write-Err([string]$Message) {
    Write-Host "[XX] $Message" -ForegroundColor Red
}

function Write-Hint([string]$Message) {
    Write-Host "     $Message" -ForegroundColor DarkGray
}

# ============================================================
# .env / 环境变量读取（只取值，不打印）
# ============================================================

function Get-DotEnvValue {
    <#
      读取键值：优先进程环境变量，其次仓库 .env（与 backend/app/config.py 的
      load_dotenv() 语义一致——进程环境变量优先）。
    #>
    param([Parameter(Mandatory = $true)][string]$Name)

    $fromEnv = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($fromEnv -and $fromEnv.Trim()) { return $fromEnv.Trim() }

    $envPath = Join-Path $Script:RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) { return '' }

    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) {
            $value = $Matches[1].Trim()
            $value = $value.Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }
    return ''
}

function ConvertFrom-DatabaseUrl {
    <#
      从 SQLAlchemy URL 中只解析 host / port / database。
      **不返回也不打印**用户名、口令等凭据片段。
    #>
    param([Parameter(Mandatory = $true)][string]$Url)

    $pattern = '^[a-zA-Z0-9+]+://(?:[^@/]*@)?(?<host>[^:/?]+)(?::(?<port>\d+))?/(?<db>[^?]+)'
    $match = [regex]::Match($Url, $pattern)
    if (-not $match.Success) { return $null }

    $port = 5432
    if ($match.Groups['port'].Success) {
        $port = [int]$match.Groups['port'].Value
    }
    return [pscustomobject]@{
        Host     = $match.Groups['host'].Value
        Port     = $port
        Database = $match.Groups['db'].Value
    }
}

function Test-TcpPort {
    <# 纯 TCP 可达性探测（不携带凭据、不执行 SQL）。 #>
    param(
        [Parameter(Mandatory = $true)][string]$TargetHost,
        [Parameter(Mandatory = $true)][int]$TargetPort,
        [int]$TimeoutMs = 2000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortListenerOwner {
    <# 返回占用端口的进程描述（PID + 进程名）；未占用 ⇒ $null。 #>
    param([Parameter(Mandatory = $true)][int]$PortNumber)

    $connections = @(Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) { return $null }

    $ownerPid = $connections[0].OwningProcess
    $name = 'unknown'
    try {
        $name = (Get-Process -Id $ownerPid -ErrorAction Stop).ProcessName
    } catch {
        $name = 'unknown'
    }
    return [pscustomobject]@{ Pid = $ownerPid; Name = $name }
}

function Show-LogTail {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Title = '日志',
        [int]$Lines = 20
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $content = @(Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($content.Count -eq 0) { return }
    Write-Host "--- $Title（最后 $($content.Count) 行）---" -ForegroundColor DarkYellow
    foreach ($line in $content) { Write-Host "    $line" -ForegroundColor DarkYellow }
    Write-Host '---' -ForegroundColor DarkYellow
}

# ============================================================
# -Stop：只停本次启动的子进程
# ============================================================

function Test-IsOurProcess {
    <# 校验 PID 仍属于本次启动的进程（进程名 + 命令行标记）。 #>
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Marker
    )

    try {
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    } catch {
        return $false
    }
    if ($null -eq $info) { return $false }
    if (-not $info.CommandLine) { return $false }
    return $info.CommandLine -like "*$Marker*"
}

function Invoke-Stop {
    if (-not (Test-Path -LiteralPath $Script:StateFile)) {
        Write-Warn '未找到上次启动记录（可能已正常退出）。'
        return
    }
    $state = Get-Content -LiteralPath $Script:StateFile -Raw -Encoding UTF8 | ConvertFrom-Json

    # 依赖逆序：先停同源网关，再停后端（缩短"后端先退导致启动器 finally 抢先清理"的竞态窗口）
    foreach ($entry in @(
            @{ Pid = $state.gatewayPid; Marker = 'dev-gateway.mjs'; Name = '同源网关' },
            @{ Pid = $state.backendPid; Marker = 'uvicorn'; Name = 'Uvicorn' }
        )) {
        if (-not $entry.Pid) { continue }
        $targetPid = [int]$entry.Pid
        if (Test-IsOurProcess -ProcessId $targetPid -Marker $entry.Marker) {
            try {
                Stop-Process -Id $targetPid -Force -ErrorAction Stop
                Write-Ok "已停止$($entry.Name)（pid=$targetPid）"
            } catch {
                Write-Warn "停止$($entry.Name)失败（pid=$targetPid）：$($_.Exception.Message)"
            }
        } else {
            Write-Hint "跳过 pid=$targetPid（已不是本次启动的进程，避免误杀）"
        }
    }

    Remove-Item -LiteralPath $Script:StateFile -Force -ErrorAction SilentlyContinue
}

function Save-LaunchState {
    $state = [pscustomobject]@{
        backendPid  = if ($Script:BackendProc) { $Script:BackendProc.Id } else { $null }
        gatewayPid  = if ($Script:GatewayProc) { $Script:GatewayProc.Id } else { $null }
        backendPort = $BackendPort
        gatewayPort = $GatewayPort
        startedAt   = (Get-Date).ToString('o')
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $Script:StateFile -Encoding UTF8
}

function Stop-Children {
    foreach ($entry in @(
            @{ Proc = $Script:GatewayProc; Name = '同源网关' },
            @{ Proc = $Script:BackendProc; Name = '后端' }
        )) {
        $proc = $entry.Proc
        if ($null -eq $proc) { continue }
        try {
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            }
        } catch {
            Write-Warn "清理$($entry.Name)失败（pid=$($proc.Id)）：$($_.Exception.Message)"
        }
    }
    Remove-Item -LiteralPath $Script:StateFile -Force -ErrorAction SilentlyContinue
}

# ============================================================
# 预检
# ============================================================

function Assert-NotProduction {
    if ($env:NODE_ENV -and $env:NODE_ENV.ToLower() -eq 'production') {
        Write-Err 'NODE_ENV=production：本脚本只用于**本地开发**，拒绝启动。'
        Write-Hint '生产部署必须使用 HTTPS + 受控网关（见 frontend/README.md §6.3）。'
        exit 1
    }
}

function Assert-Prerequisites {
    foreach ($cmd in @('python', 'node')) {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
            Write-Err "缺少命令：$cmd（请先安装并加入 PATH）"
            exit 1
        }
    }
    foreach ($path in @(
            (Join-Path $Script:RepoRoot 'backend\app\main.py'),
            (Join-Path $Script:FrontendDir 'scripts\dev-gateway.mjs')
        )) {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Err "缺少必需文件：$($path -replace [regex]::Escape($Script:RepoRoot), '.')"
            exit 1
        }
    }
}

function Assert-PortsFree {
    foreach ($port in @($BackendPort, $GatewayPort)) {
        $owner = Get-PortListenerOwner -PortNumber $port
        if ($null -ne $owner) {
            Write-Err "端口 $port 已被占用（pid=$($owner.Pid)，进程=$($owner.Name)）。"
            Write-Hint '请先停止该进程，或用 -BackendPort / -GatewayPort 指定其它端口。'
            Write-Hint '若是上次启动残留：.\scripts\start-workflow-ui.ps1 -Stop'
            exit 1
        }
    }
    Write-Ok "端口可用：后端 $BackendPort · 网关 $GatewayPort"
}

function Test-DatabaseReachability {
    $url = Get-DotEnvValue -Name 'DATABASE_URL'
    if (-not $url) {
        Write-Warn 'DATABASE_URL 未配置（进程环境变量与 .env 均为空）。'
        Write-Hint '后端仍会启动，但 /api/health 会报告 database=disabled，'
        Write-Hint 'Workflow 读接口将 fail-closed（503）。本脚本不会替你设置数据库。'
        return
    }

    $target = ConvertFrom-DatabaseUrl -Url $url
    if ($null -eq $target) {
        Write-Warn 'DATABASE_URL 格式无法解析，跳过数据库可达性探测。'
        return
    }

    $isLoopback = @('localhost', '127.0.0.1', '::1') -contains $target.Host
    if (-not $isLoopback -and -not $AllowNonLocalDatabase) {
        Write-Err "DATABASE_URL 指向非回环主机（$($target.Host)），已拒绝启动。"
        Write-Hint '为避免误连生产 / 共享数据库，本脚本默认只允许本机数据库。'
        Write-Hint '如确需连本地以外的开发库，请显式加 -AllowNonLocalDatabase。'
        exit 1
    }
    if (-not $isLoopback) {
        Write-Warn "DATABASE_URL 指向非回环主机（$($target.Host)）：请确认这不是生产库。"
    }

    if (Test-TcpPort -TargetHost $target.Host -TargetPort $target.Port) {
        Write-Ok "数据库可达：$($target.Host):$($target.Port)（库名/凭据不打印）"
        return
    }

    Write-Err "数据库不可达：$($target.Host):$($target.Port)"
    Write-Hint '请先启动本地 PostgreSQL（与测试库隔离的容器）：'
    Write-Hint '    docker compose -f docker-compose.test.yml up -d'
    Write-Hint '或在 .env / 环境变量中把 DATABASE_URL 指向你已启动的**本地开发库**。'
    Write-Hint '本脚本**不会**自动切换数据库、不会执行 init_db 或任何迁移。'
    exit 1
}

function Test-CookieSecureSetting {
    $raw = Get-DotEnvValue -Name 'AUTH_COOKIE_SECURE'
    $effective = 'true'      # backend/app/config.py 的默认值
    if ($raw) { $effective = $raw.ToLower() }

    if ($effective -eq 'true') {
        Write-Warn 'AUTH_COOKIE_SECURE 生效值为 true（默认）。'
        Write-Hint '以 http:// 访问时浏览器不会保存 HttpOnly 会话 Cookie，读取会返回 401。'
        Write-Hint '本地 http 调试请**自行显式**设置（本脚本不会替你改）：'
        Write-Hint '    $env:AUTH_COOKIE_SECURE = "false"'
        Write-Hint '生产环境必须保持 true，并由 HTTPS + 受控网关承载。'
        return
    }
    Write-Ok "AUTH_COOKIE_SECURE=$effective（仅本地 http 调试；生产必须 true）"
}

# ============================================================
# 启动子进程
# ============================================================

function Start-Backend {
    Write-Step "启动后端 Uvicorn（127.0.0.1:$BackendPort）…"
    Remove-Item -LiteralPath $Script:BackendOut, $Script:BackendErr -Force -ErrorAction SilentlyContinue

    $arguments = @(
        '-m', 'uvicorn', 'backend.app.main:app',
        '--host', '127.0.0.1',
        '--port', "$BackendPort",
        '--log-level', 'info'
    )
    $Script:BackendProc = Start-Process -FilePath 'python' `
        -ArgumentList $arguments `
        -WorkingDirectory $Script:RepoRoot `
        -RedirectStandardOutput $Script:BackendOut `
        -RedirectStandardError $Script:BackendErr `
        -WindowStyle Hidden -PassThru
}

function Wait-BackendHealthy {
    $url = "http://127.0.0.1:$BackendPort/api/health"
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    $lastError = ''

    while ((Get-Date) -lt $deadline) {
        if ($Script:BackendProc.HasExited) {
            Write-Err "后端进程已退出（exit code=$($Script:BackendProc.ExitCode)）。"
            Show-LogTail -Path $Script:BackendErr -Title '后端 stderr'
            Show-LogTail -Path $Script:BackendOut -Title '后端 stdout'
            exit 1
        }
        try {
            $health = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
            $dbState = "$($health.database)"
            Write-Ok "后端健康检查通过：status=$($health.status) · database=$dbState"

            if ($dbState -eq 'disabled') {
                Write-Warn 'DATABASE_URL 未配置：Workflow 读接口会 fail-closed（503）。'
            } elseif ($dbState -ne 'ok') {
                Write-Warn '数据库探测失败（database=error）：读接口会返回 503。'
                Write-Hint '检查本地 PostgreSQL 是否运行、DATABASE_URL 是否指向本地开发库。'
            }
            return
        } catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    }

    Write-Err "等待后端健康检查超时（${HealthTimeoutSeconds}s）：$lastError"
    Show-LogTail -Path $Script:BackendErr -Title '后端 stderr'
    Show-LogTail -Path $Script:BackendOut -Title '后端 stdout'
    exit 1
}

function Start-Gateway {
    Write-Step "启动同源网关（127.0.0.1:$GatewayPort → /api → 127.0.0.1:$BackendPort）…"
    Remove-Item -LiteralPath $Script:GatewayOut, $Script:GatewayErr -Force -ErrorAction SilentlyContinue

    $arguments = @(
        'scripts/dev-gateway.mjs',
        '--port', "$GatewayPort",
        '--api-port', "$BackendPort"
    )
    $Script:GatewayProc = Start-Process -FilePath 'node' `
        -ArgumentList $arguments `
        -WorkingDirectory $Script:FrontendDir `
        -RedirectStandardOutput $Script:GatewayOut `
        -RedirectStandardError $Script:GatewayErr `
        -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds(15)
    $probeUrl = "http://127.0.0.1:$GatewayPort/index.html"
    while ((Get-Date) -lt $deadline) {
        if ($Script:GatewayProc.HasExited) {
            Write-Err "同源网关已退出（exit code=$($Script:GatewayProc.ExitCode)）。"
            Show-LogTail -Path $Script:GatewayErr -Title '网关 stderr'
            Show-LogTail -Path $Script:GatewayOut -Title '网关 stdout'
            exit 1
        }
        try {
            $response = Invoke-WebRequest -Uri $probeUrl -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                Write-Ok "同源网关就绪：$probeUrl"
                return
            }
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }

    Write-Err '等待同源网关就绪超时（15s）。'
    Show-LogTail -Path $Script:GatewayErr -Title '网关 stderr'
    Show-LogTail -Path $Script:GatewayOut -Title '网关 stdout'
    exit 1
}

# ============================================================
# 主流程
# ============================================================

if ($Stop) {
    Write-Step '停止上一次启动的本地 Workflow UI 子进程…'
    Invoke-Stop
    Write-Ok '完成。'
    exit 0
}

Assert-NotProduction
Assert-Prerequisites

Write-Host ''
Write-Host 'Workflow 运维工作台 —— 本地开发启动（Phase 7 Step 7）' -ForegroundColor White
Write-Host '说明：本脚本只做启动编排；认证 / 授权 / 运行时行为完全由后端执行。' -ForegroundColor DarkGray
Write-Host ''

Assert-PortsFree
Test-DatabaseReachability
Test-CookieSecureSetting

try {
    Start-Backend
    Wait-BackendHealthy

    Start-Gateway
    Save-LaunchState

    Write-Host ''
    Write-Host '================ 已就绪 ================' -ForegroundColor Green
    Write-Host "  访问地址 : http://127.0.0.1:$GatewayPort/index.html" -ForegroundColor White
    Write-Host "  后端健康 : http://127.0.0.1:$BackendPort/api/health" -ForegroundColor Gray
    Write-Host "  网关端口 : $GatewayPort（仅转发 /api/ui/ 与 /api/auth/）" -ForegroundColor Gray
    Write-Host ''
    Write-Host '  登录方式（服务端签发 HttpOnly 会话 Cookie）：' -ForegroundColor White
    Write-Host '      POST /api/auth/login' -ForegroundColor Gray
    Write-Host '      {"username":"<你的账号>","password":"<你的口令>"}' -ForegroundColor Gray
    Write-Host '  需要先创建用户并授予项目成员权限（见 frontend/README.md §6.5）：' -ForegroundColor White
    Write-Host '      python -m backend.app.cli.create_user --username <name>' -ForegroundColor Gray
    Write-Host '      python -m backend.app.cli.grant_role --user-id <uuid> --project <pid> --role MEMBER' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  页面查询需要：Project ID（你已获授权的项目）+ 真实 Workflow Run ID。' -ForegroundColor White
    Write-Host '  本脚本不创建用户 / 不授权 / 不创建 Run。' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  按 Ctrl+C 停止（只结束本次启动的两个子进程）。' -ForegroundColor Yellow
    Write-Host '=======================================' -ForegroundColor Green
    Write-Host ''

    # 前台保持：任一子进程退出即退出
    while ($true) {
        Start-Sleep -Seconds 1
        if ($Script:BackendProc.HasExited) {
            Write-Err "后端进程意外退出（exit code=$($Script:BackendProc.ExitCode)）。"
            Show-LogTail -Path $Script:BackendErr -Title '后端 stderr'
            break
        }
        if ($Script:GatewayProc.HasExited) {
            Write-Err "同源网关意外退出（exit code=$($Script:GatewayProc.ExitCode)）。"
            Show-LogTail -Path $Script:GatewayErr -Title '网关 stderr'
            break
        }
    }
} finally {
    Write-Host ''
    Write-Step '正在停止本次启动的子进程…'
    Stop-Children
    Write-Ok '已退出（未触碰其它进程）。'
}
