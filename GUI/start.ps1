param([switch]$OpenBrowser, [switch]$LocalOnly)

$ErrorActionPreference = "Stop"

# GUI 启动器：拉起 FastAPI 后端（8000，新窗口） + Vite dev server（5173，前台）。
# Vite dev 模式提供 HMR，agent 改前端代码后浏览器秒级刷新，无需每次 npm run build。
# FastAPI 后端只负责 /api/，前端由 Vite 在 5173 端口 serve，Vite 自动把 /api 转发到 8000。
#
# 使用：powershell -File GUI\start.ps1
#       或在仓库根目录直接 .\GUI\start.ps1
#       双击根目录 start_gui.bat 会附加 -OpenBrowser，自动打开浏览器。
#       默认同时配置 Tailscale 私人 HTTPS 入口；-LocalOnly 只启动本机服务。
# 退出：在当前窗口按 Ctrl+C 结束 Vite；FastAPI 后端在独立窗口，关掉那个窗口即可。

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "agent_exp_env\Scripts\python.exe"
$FrontendDir = Join-Path $Root "GUI\frontend"
$BackendCwd = Join-Path $Root "GUI"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "未找到 $FrontendDir\node_modules，请先在该目录运行 npm ci"
}
$Npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
if (-not $Npm) {
    throw "未找到 npm.cmd，请先安装 Node.js / npm，并重新打开启动窗口。"
}
foreach ($Port in 8000, 5173) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "端口 $Port 已被占用，请确认并手动停止对应旧服务后重试；启动器不会结束已有进程。"
    }
}

if (-not $LocalOnly) {
    $Tailscale = Get-Command tailscale.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Tailscale) {
        throw "未找到 Tailscale，请先安装并登录；仅本机使用可附加 -LocalOnly。"
    }
    $StateJson = & $Tailscale.Source status --json
    if ($LASTEXITCODE -ne 0) { throw "无法读取 Tailscale 状态，请检查 Tailscale 服务。" }
    $TailscaleState = ($StateJson | Out-String) | ConvertFrom-Json
    if ($TailscaleState.BackendState -ne "Running") {
        throw "Tailscale 尚未连接，请先打开 Tailscale 并登录；仅本机使用可附加 -LocalOnly。"
    }
    $RemoteHost = ([string]$TailscaleState.Self.DNSName).TrimEnd(".")
    if ($RemoteHost -notmatch '^[a-z0-9-]+(?:\.[a-z0-9-]+)+\.ts\.net$') {
        throw "Tailscale 未提供有效的本机域名，请检查 MagicDNS 设置。"
    }
    $ServeAddress = "${RemoteHost}:443"
    $ServeTarget = "http://127.0.0.1:5173"
    $ServeJson = & $Tailscale.Source serve status --json
    if ($LASTEXITCODE -ne 0) { throw "无法读取 Tailscale Serve 配置。" }
    $ServeConfig = ($ServeJson | Out-String) | ConvertFrom-Json
    $ExistingHandlers = $ServeConfig.Web.$ServeAddress.Handlers
    $ExistingHandler = $ExistingHandlers."/"
    if ($ServeConfig.AllowFunnel.$ServeAddress -or
        ($ServeConfig.TCP."443" -and -not $ServeConfig.TCP."443".HTTPS) -or
        ($ExistingHandlers -and @($ExistingHandlers.PSObject.Properties | Where-Object { $_.Name -ne "/" }).Count -gt 0) -or
        ($ExistingHandler -and $ExistingHandler.Proxy -ne $ServeTarget)) {
        throw "Tailscale 443 端口已有其他服务或公网 Funnel 配置，请先确认并手动处理；启动器不会覆盖。"
    }

    # 保留调用者已有的域名白名单，只补充当前设备的 Tailscale 域名。
    $AllowedHosts = @($env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS -split ',') + $RemoteHost
    $env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = ($AllowedHosts | Where-Object { $_ } | Select-Object -Unique) -join ','
    Write-Host "正在配置 Tailscale Serve；首次如出现 HTTPS 设置链接，请在浏览器完成一次设置。"
    & $Tailscale.Source serve --bg --yes --https=443 --set-path=/ $ServeTarget
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale Serve 配置失败，请检查上方错误、HTTPS 设置及账户权限；必要时在管理员终端运行。"
    }
    $ServeJson = & $Tailscale.Source serve status --json
    if ($LASTEXITCODE -ne 0) { throw "无法回读 Tailscale Serve 配置。" }
    $ServeConfig = ($ServeJson | Out-String) | ConvertFrom-Json
    if (-not $ServeConfig.TCP."443".HTTPS -or
        $ServeConfig.Web.$ServeAddress.Handlers."/".Proxy -ne $ServeTarget -or
        @($ServeConfig.Web.$ServeAddress.Handlers.PSObject.Properties).Count -ne 1 -or
        $ServeConfig.AllowFunnel.$ServeAddress) {
        throw "Tailscale 私人 HTTPS 入口回读不匹配，已取消 GUI 启动。"
    }
}

# FastAPI 后端：开新窗口，日志在该窗口显示。
Start-Process -FilePath $Python -ArgumentList "-m","backend" -WorkingDirectory $BackendCwd
Write-Host "FastAPI 后端已启动到独立窗口  http://127.0.0.1:8000"

# 等 8000 端口 LISTENING 后再启 Vite；避免浏览器打开 5173 时 /api 转发到还没绑定的 8000。
$Deadline = (Get-Date).AddSeconds(20)
$Ready = $false
while ((Get-Date) -lt $Deadline) {
    $Conn = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
    if ($Conn) { $Ready = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $Ready) {
    throw "20 秒内后端未监听 8000 端口，已取消前端启动。请检查后端窗口中的错误日志；后端若仍在运行，请在该窗口手动停止。"
}

Set-Location -LiteralPath $FrontendDir
Write-Host "Vite dev     http://127.0.0.1:5173"
if (-not $LocalOnly) {
    Write-Host "笔记本访问   https://$RemoteHost（前端就绪后可用；笔记本需连接同一 Tailscale 网络）"
}
Write-Host "Ctrl+C 结束当前 Vite 进程；后端需在独立窗口停止"

$ViteArgs = @("run", "dev", "--", "--strictPort")
if ($OpenBrowser) { $ViteArgs += "--open" }
& $Npm.Source @ViteArgs
exit $LASTEXITCODE
