param([switch]$OpenBrowser, [switch]$LocalOnly, [switch]$Hidden)

$ErrorActionPreference = "Stop"

# GUI 启动器：拉起 FastAPI 后端（8000） + Vite dev server（5173）。
# 普通模式在当前窗口运行 Vite；-Hidden 模式由 start_gui.vbs 调用，前端和后端都隐藏运行。
# Vite dev 模式提供 HMR，agent 改前端代码后浏览器秒级刷新，无需每次 npm run build。
# FastAPI 后端只负责 /api/，前端由 Vite 在 5173 端口 serve，Vite 自动把 /api 转发到 8000。
#
# 使用：双击 start_gui.vbs（完全无终端），或 start_gui.bat（会短暂闪一下控制台）。
#       命令行调试可用 powershell -File GUI\start.ps1；附加 -OpenBrowser 自动打开浏览器。
#       默认同时配置 Tailscale 私人 HTTPS 入口；-LocalOnly 只启动本机服务。
# 停止：双击 stop_gui.bat；它会停止 Vite，并请求后端优雅收尾后等待退出。
# 日志：后端 tmp\gui-backend.log，前端 tmp\gui-frontend.log / gui-frontend.err.log，
#       启动器 tmp\gui-launcher.log。

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "agent_exp_env\Scripts\python.exe"
$FrontendDir = Join-Path $Root "GUI\frontend"
$BackendCwd = Join-Path $Root "GUI"
$TmpDir = Join-Path $Root "tmp"
$BackendLog = Join-Path $TmpDir "gui-backend.log"
$FrontendLog = Join-Path $TmpDir "gui-frontend.log"
$FrontendErrorLog = Join-Path $TmpDir "gui-frontend.err.log"
$LauncherLog = Join-Path $TmpDir "gui-launcher.log"
$BackendStopFile = Join-Path $TmpDir "gui-backend.stop"
$BackendPidFile = Join-Path $TmpDir "gui-backend.pid"
$FrontendPidFile = Join-Path $TmpDir "gui-frontend.pid"
$LauncherPidFile = Join-Path $TmpDir "gui-launcher.pid"
$BackendProcess = $null
$ViteProcess = $null

function Write-LauncherLog([string]$Message) {
    Write-Host $Message
    try {
        Add-Content -LiteralPath $LauncherLog -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
    } catch {
    }
}

if ($Hidden) {
    New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
    trap {
        $message = $_.Exception.Message
        try {
            Add-Content -LiteralPath $LauncherLog -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message) -Encoding UTF8
        } catch {
        }
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show($message, "实验 UI 启动失败") | Out-Null
        } catch {
        }
        exit 1
    }
}

function Stop-FrontendProcess {
    if ($null -eq $ViteProcess) { return }
    if (-not $ViteProcess.HasExited) {
        Stop-Process -Id $ViteProcess.Id -Force -ErrorAction SilentlyContinue
        $ViteProcess.WaitForExit()
    }
    Remove-Item -LiteralPath $FrontendPidFile -Force -ErrorAction SilentlyContinue
}

function Stop-HiddenBackend {
    if ($null -eq $BackendProcess) { return }
    if ($BackendProcess.HasExited) {
        Remove-Item -LiteralPath $BackendStopFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BackendPidFile -Force -ErrorAction SilentlyContinue
        return
    }
    Write-LauncherLog "正在请求后端优雅停止并等待安全收尾；等待期间请勿强制结束进程..."
    New-Item -ItemType File -Path $BackendStopFile -Force | Out-Null
    $BackendProcess.WaitForExit()
    Write-LauncherLog "后端已退出；日志： $BackendLog"
    # 只有确认进程退出后才移除停止文件；中途中断时保留，后端下次轮询仍能看到。
    Remove-Item -LiteralPath $BackendStopFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $BackendPidFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "未找到 $FrontendDir\node_modules，请先在该目录运行 npm ci"
}
if ($Hidden) {
    $Node = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Node) {
        throw "未找到 node.exe，请先安装 Node.js / npm，并重新启动。"
    }
    $ViteCli = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $ViteCli)) {
        throw "未找到 $ViteCli，请在该目录运行 npm ci。"
    }
} else {
    $Npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Npm) {
        throw "未找到 npm.cmd，请先安装 Node.js / npm，并重新打开启动窗口。"
    }
}
foreach ($Port in 8000, 5173) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "端口 $Port 已被占用，请先运行 stop_gui.bat 或手动停止对应旧服务后重试；启动器不会结束已有进程。"
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
    Write-LauncherLog "正在配置 Tailscale Serve；首次如出现 HTTPS 设置链接，请在浏览器完成一次设置。"
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

New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
foreach ($Path in @($BackendStopFile, $BackendPidFile, $FrontendPidFile, $LauncherPidFile)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}
Set-Content -LiteralPath $LauncherPidFile -Value $PID

$ExitCode = 0
try {
    # FastAPI 后端：隐藏窗口运行，输出写入日志文件；启动器退出时通过停止文件请求优雅收尾。
    $BackendArguments = '-m backend --log-file "{0}" --stop-file "{1}" --parent-pid {2}' -f $BackendLog, $BackendStopFile, $PID
    $BackendProcess = Start-Process -FilePath $Python -ArgumentList $BackendArguments -WorkingDirectory $BackendCwd -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $BackendPidFile -Value $BackendProcess.Id
    Write-LauncherLog "FastAPI 后端已隐藏启动  http://127.0.0.1:8000（日志： $BackendLog）"

    # 等 8000 端口 LISTENING 后再启 Vite；避免浏览器打开 5173 时 /api 转发到还没绑定的 8000。
    $Deadline = (Get-Date).AddSeconds(20)
    $Ready = $false
    while ((Get-Date) -lt $Deadline) {
        if ($BackendProcess.HasExited) {
            throw "后端启动后立即退出（退出码 $($BackendProcess.ExitCode)），请检查日志： $BackendLog"
        }
        $Conn = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
        if ($Conn) { $Ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $Ready) {
        throw "20 秒内后端未监听 8000 端口，已取消前端启动。请检查日志： $BackendLog"
    }

    if ($Hidden) {
        $ViteArguments = '"{0}" --host 127.0.0.1 --strictPort' -f $ViteCli
        $ViteProcess = Start-Process -FilePath $Node.Source -ArgumentList $ViteArguments -WorkingDirectory $FrontendDir -WindowStyle Hidden -RedirectStandardOutput $FrontendLog -RedirectStandardError $FrontendErrorLog -PassThru
        Set-Content -LiteralPath $FrontendPidFile -Value $ViteProcess.Id

        $Deadline = (Get-Date).AddSeconds(30)
        $Ready = $false
        while ((Get-Date) -lt $Deadline) {
            if ($ViteProcess.HasExited) {
                throw "前端启动后立即退出（退出码 $($ViteProcess.ExitCode)），请检查日志： $FrontendErrorLog"
            }
            $Conn = Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue
            if ($Conn) { $Ready = $true; break }
            Start-Sleep -Milliseconds 300
        }
        if (-not $Ready) {
            throw "30 秒内前端未监听 5173 端口，已停止后端。请检查日志： $FrontendErrorLog"
        }

        Write-LauncherLog "GUI 已在后台运行  http://127.0.0.1:5173"
        if (-not $LocalOnly) {
            Write-LauncherLog "笔记本访问   https://$RemoteHost（笔记本需连接同一 Tailscale 网络）"
        }
        Write-LauncherLog "停止服务请双击 stop_gui.bat；关闭浏览器不会停止后台实验"
        if ($OpenBrowser) {
            try {
                Start-Process -FilePath "http://127.0.0.1:5173" | Out-Null
            } catch {
                Write-LauncherLog "无法自动打开浏览器：$($_.Exception.Message)"
            }
        }
        $ViteProcess.WaitForExit()
        $ExitCode = $ViteProcess.ExitCode
    } else {
        Set-Location -LiteralPath $FrontendDir
        Write-LauncherLog "Vite dev     http://127.0.0.1:5173"
        if (-not $LocalOnly) {
            Write-LauncherLog "笔记本访问   https://$RemoteHost（前端就绪后可用；笔记本需连接同一 Tailscale 网络）"
        }
        Write-LauncherLog "关闭窗口或 Ctrl+C 会同时结束后端并等待安全收尾"

        $ViteArgs = @("run", "dev", "--", "--strictPort")
        if ($OpenBrowser) { $ViteArgs += "--open" }
        & $Npm.Source @ViteArgs
        $ExitCode = $LASTEXITCODE
    }
} finally {
    Stop-FrontendProcess
    Stop-HiddenBackend
    Remove-Item -LiteralPath $LauncherPidFile -Force -ErrorAction SilentlyContinue
}
exit $ExitCode
