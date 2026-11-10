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

function Get-ListenerPid([int]$Port) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

$FrontendPid = Read-PidFile $FrontendPidFile
$BackendPid = Read-PidFile $BackendPidFile
$LauncherPid = Read-PidFile $LauncherPidFile

if ($null -eq (Get-OwnProcess $FrontendPid @("node") "vite")) {
    $FrontendPid = Get-ListenerPid 5173
}
if ($null -eq (Get-OwnProcess $BackendPid @("python", "pythonw") "backend")) {
    $BackendPid = Get-ListenerPid 8000
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
