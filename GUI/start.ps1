$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "agent_exp_env\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}

Set-Location (Join-Path $Root "gui")
& $Python -m backend
