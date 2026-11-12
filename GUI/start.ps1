param([switch]$OpenBrowser, [switch]$Hidden, [switch]$Production)

$ErrorActionPreference = "Stop"

# GUI 启动器：拉起 FastAPI 后端（8000） + Vite dev server（5173）。
# 普通模式在当前窗口运行 Vite；-Hidden 模式由 start_gui.vbs 调用，前端和后端都隐藏运行。
# Vite dev 模式提供 HMR，agent 改前端代码后浏览器秒级刷新，无需每次 npm run build。
# FastAPI 后端只负责 /api/，前端由 Vite 在 5173 端口 serve，Vite 自动把 /api 转发到 8000。
# -Production 生产模式（运行目录 start_lab.vbs 使用）：不启动 Vite，后端 8000 直接提供
#       GUI\frontend\dist 构建产物，需先运行发布脚本或 npm run build。
#
# 使用：双击 start_gui.vbs（开发模式，完全无终端），或 start_gui.bat（会短暂闪一下控制台）；
#       运行目录双击 start_lab.vbs（生产模式，等价于 -Production -OpenBrowser -Hidden）。
#       命令行调试可用 powershell -File GUI\start.ps1；附加 -OpenBrowser 自动打开浏览器。
# 停止：双击 stop_gui.bat；它会停止 Vite（如有），并请求后端优雅收尾后等待退出。
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
# 后端导入全部实验模块后才监听 8000；平时约 10 秒，机器负载高或冷缓存时实测 19–38 秒。
# 超时会让启动器停掉后端，所以留足余量；后端提前退出仍立即报错。
$BackendStartTimeoutSec = 120

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

# 后端经 /api/restart 自重启时调用 os.execv；Windows 上这会换成新进程号、旧进程退出。
# 旧进程退出且未收到停止文件时，等待 8000 端口由新进程重新监听，返回新进程；超时返回 $null。
function Get-BackendSuccessor([int]$PreviousId) {
    $Deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $Deadline) {
        $Conn = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Conn -and [int]$Conn.OwningProcess -ne $PreviousId) {
            $Successor = Get-Process -Id ([int]$Conn.OwningProcess) -ErrorAction SilentlyContinue
            if ($Successor -and $Successor.ProcessName -in @("python", "pythonw")) { return $Successor }
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
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
if ($Production) {
    $DistIndex = Join-Path $FrontendDir "dist\index.html"
    if (-not (Test-Path $DistIndex)) {
        throw "未找到前端构建，请先运行发布脚本或 npm run build（缺少 $DistIndex）"
    }
} else {
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
}
$Ports = if ($Production) { @(8000) } else { @(8000, 5173) }
foreach ($Port in $Ports) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "端口 $Port 已被占用，请先运行 stop_gui.bat 或手动停止对应旧服务后重试；启动器不会结束已有进程。"
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

    # 等 8000 端口 LISTENING 后再继续；避免浏览器打开页面时 /api 请求到还没绑定的后端。
    $Deadline = (Get-Date).AddSeconds($BackendStartTimeoutSec)
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
        throw "$BackendStartTimeoutSec 秒内后端未监听 8000 端口，已取消 GUI 启动。请检查日志： $BackendLog"
    }

    if ($Production) {
        Write-LauncherLog "GUI 生产模式已运行  http://127.0.0.1:8000（前端由后端直接提供 dist 构建）"
        Write-LauncherLog "停止服务请双击 stop_gui.bat；关闭浏览器不会停止后台实验"
        if ($OpenBrowser) {
            try {
                Start-Process -FilePath "http://127.0.0.1:8000" | Out-Null
            } catch {
                Write-LauncherLog "无法自动打开浏览器：$($_.Exception.Message)"
            }
        }
        # 启动器必须保持存活等待后端退出：后端带 --parent-pid，启动器退出会触发其优雅收尾。
        # 界面「重启后端」会换新进程号，此时改为跟随新进程继续等待。
        while ($true) {
            $BackendProcess.WaitForExit()
            if (Test-Path -LiteralPath $BackendStopFile) { break }
            $Successor = Get-BackendSuccessor $BackendProcess.Id
            if ($null -eq $Successor) { break }
            $BackendProcess = $Successor
            Set-Content -LiteralPath $BackendPidFile -Value $BackendProcess.Id
            Write-LauncherLog "后端已自重启，继续跟随新进程（PID $($BackendProcess.Id)）"
        }
        $ExitCode = $BackendProcess.ExitCode
    } elseif ($Hidden) {
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
