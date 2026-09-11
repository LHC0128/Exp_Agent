$ErrorActionPreference = "Stop"

# GUI 启动器：拉起 FastAPI 后端（8000，新窗口） + Vite dev server（5173，前台）。
# Vite dev 模式提供 HMR，agent 改前端代码后浏览器秒级刷新，无需每次 npm run build。
# FastAPI 后端只负责 /api/，前端由 Vite 在 5173 端口 serve，Vite 自动把 /api 转发到 8000。
#
# 使用：powershell -File GUI\start.ps1
#       或在仓库根目录直接 .\GUI\start.ps1
# 退出：在当前窗口按 Ctrl+C 结束 Vite；FastAPI 后端在独立窗口，关掉那个窗口即可。
#       想命令行动手停后端：Get-Process -Name python | Where-Object { $_.CommandLine -like '*-m backend*' } | Stop-Process -Force

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "agent_exp_env\Scripts\python.exe"
$FrontendDir = Join-Path $Root "gui\frontend"
$BackendCwd = Join-Path $Root "gui"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "未找到 $FrontendDir\node_modules，请先在该目录运行 npm install"
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
    Write-Warning "20 秒内 8000 端口未 LISTENING。请检查后端窗口中的错误日志（可能 import 失败、端口被占等）。脚本继续启动 Vite，但首次 /api 请求会失败。"
}

Set-Location $FrontendDir
Write-Host "Vite dev     http://127.0.0.1:5173"
Write-Host "浏览器打开 5173；Ctrl+C 结束当前 Vite 进程"

& npm run dev
