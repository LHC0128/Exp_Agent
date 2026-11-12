$ErrorActionPreference = "Stop"

# 停止隐藏运行的 GUI：先停 Vite，再请求后端优雅收尾并等待；不会强杀后端。
$Root = Split-Path -Parent $PSScriptRoot
$TmpDir = Join-Path $Root "tmp"
$BackendStopFile = Join-Path $TmpDir "gui-backend.stop"
$BackendPidFile = Join-Path $TmpDir "gui-backend.pid"
$FrontendPidFile = Join-Path $TmpDir "gui-frontend.pid"
$LauncherPidFile = Join-Path $TmpDir "gui-launcher.pid"

function Read-PidFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $text = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $text) { return $null }
    $text = $text.Trim()
    if ($text -notmatch '^\d+$') { return $null }
    return [int]$text
}

function Get-OwnProcess([int]$ProcessId, [string[]]$AllowedNames, [string]$CommandLike) {
    if ($ProcessId -le 0) { return $null }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($AllowedNames -notcontains $process.ProcessName) { return $null }
    if ($CommandLike) {
        $info = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($null -eq $info -or $info.CommandLine -notlike "*$CommandLike*") { return $null }
    }
    return $process
}

# 开发模式端口与 start.ps1 一致：默认 8100/5273，.lab_dev.yaml 可覆盖；生产模式后端 8000。
function Read-DevPort([string]$Key, [int]$Default) {
    $config = Join-Path $Root ".lab_dev.yaml"
    if (-not (Test-Path -LiteralPath $config)) { return $Default }
    $line = Get-Content -LiteralPath $config -Encoding UTF8 | Where-Object { $_ -match "^\s*$Key\s*:\s*(\d+)" } | Select-Object -First 1
    if (-not $line) { return $Default }
    $null = $line -match "^\s*$Key\s*:\s*(\d+)"
    return [int]$Matches[1]
}

# PID 文件失效时按端口回退，但只认命令行属于本目录的进程：开发与运行目录可能同时运行，
# 不能把另一目录的后端当成自己的（否则会在本目录写停止文件后一直等待它退出）。
function Get-OwnListenerPid([int[]]$Ports, [string[]]$AllowedNames, [string]$CommandLike) {
    foreach ($port in $Ports) {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn -and (Get-OwnProcess ([int]$conn.OwningProcess) $AllowedNames $CommandLike)) { return [int]$conn.OwningProcess }
    }
    return $null
}

$FrontendPid = Read-PidFile $FrontendPidFile
$BackendPid = Read-PidFile $BackendPidFile
$LauncherPid = Read-PidFile $LauncherPidFile

if ($null -eq (Get-OwnProcess $FrontendPid @("node") "vite")) {
    $FrontendPid = Get-OwnListenerPid @(Read-DevPort "frontend_port" 5273) @("node") "$Root\GUI\frontend\"
}
if ($null -eq (Get-OwnProcess $BackendPid @("python", "pythonw") "backend")) {
    $BackendPid = Get-OwnListenerPid @(8000, (Read-DevPort "backend_port" 8100)) @("python", "pythonw") "$Root\tmp\"
}

$Stopped = $false

if ($FrontendPid -and (Get-OwnProcess $FrontendPid @("node") $null)) {
    Write-Host "正在停止前端（PID $FrontendPid）..."
    & taskkill.exe /PID $FrontendPid /T /F 2>&1 | Out-Null
    $Stopped = $true
}

if ($BackendPid -and (Get-OwnProcess $BackendPid @("python", "pythonw") $null)) {
    Write-Host "正在请求后端优雅停止并等待安全收尾；等待期间请勿强制结束进程..."
    New-Item -ItemType File -Path $BackendStopFile -Force | Out-Null
    while (Get-OwnProcess $BackendPid @("python", "pythonw") $null) {
        Start-Sleep -Milliseconds 500
    }
    $Stopped = $true
}

if ($LauncherPid -and (Get-OwnProcess $LauncherPid @("powershell") "start.ps1")) {
    while (Get-OwnProcess $LauncherPid @("powershell") $null) {
        Start-Sleep -Milliseconds 200
    }
}

foreach ($Path in @($BackendStopFile, $BackendPidFile, $FrontendPidFile, $LauncherPidFile)) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}
if ($Stopped) {
    Write-Host "GUI 已停止。"
} else {
    Write-Host "未发现运行中的 GUI 服务。"
}
exit 0
