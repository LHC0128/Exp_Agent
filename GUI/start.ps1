$ErrorActionPreference = "Stop"

# GUI 启动器：同时拉起 FastAPI 后端（8000）和 Vite dev server（5173）。
# Vite dev 模式提供 HMR，agent 改前端代码后浏览器秒级刷新，无需每次 npm run build。
# FastAPI 后端只负责 /api/，前端由 Vite 在 5173 端口 serve，Vite 自动把 /api 转发到 8000。
#
# 使用：powershell -File GUI\start.ps1
# 退出：当前窗口按 Ctrl+C，脚本 finally 会一并关闭 FastAPI 后端。

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "agent_exp_env\Scripts\python.exe"
$FrontendDir = Join-Path $Root "gui\frontend"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "未找到 $FrontendDir\node_modules，请先在该目录运行 npm install"
}

# FastAPI 后端：新窗口启动，方便看其独立日志；前端退出时 finally 会清理。
$Backend = Start-Process -FilePath $Python `
    -ArgumentList "-m", "backend" `
    -WorkingDirectory (Join-Path $Root "gui") `
    -PassThru

Write-Host "FastAPI 后端  http://127.0.0.1:8000  (PID $($Backend.Id))"

try {
    Set-Location $FrontendDir
    Write-Host "Vite dev     http://127.0.0.1:5173"
    & npm run dev
} finally {
    if (Get-Process -Id $Backend.Id -ErrorAction SilentlyContinue) {
        Stop-Process -Id $Backend.Id -Force
        Write-Host "已停止 FastAPI 后端 (PID $($Backend.Id))"
    }
}
