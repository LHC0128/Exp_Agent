param([switch]$SkipFrontend)

$ErrorActionPreference = "Stop"

# 一键安装：虚拟环境 + 本地包（pip install -e .）+ 仪器/分析依赖 + GUI 前后端依赖。
# 脚本幂等、可重复运行，与 README「初次安装」的手动步骤等价；人类与 AI 编码代理
# 均可直接执行本脚本完成部署。
#
# 使用：powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
#       -SkipFrontend 跳过 Node.js / npm 前端依赖（纯 Python 使用）。
# 失败处理：任一步骤非零退出即停止并报告，不跳过、不降级；修复后重新运行即可续装。

$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

function Test-PythonAtLeast310 {
    param([string[]]$Command)
    try {
        $Output = (& $Command --version 2>&1 | Out-String).Trim()
        if ($Output -match 'Python (\d+)\.(\d+)') {
            $Major = [int]$Matches[1]
            $Minor = [int]$Matches[2]
            return ($Major -gt 3 -or ($Major -eq 3 -and $Minor -ge 10))
        }
    } catch { }
    return $false
}

function Invoke-PipStep {
    param([string]$VenvPython, [string]$Label, [string[]]$Arguments)
    Write-Host "==> $Label"
    & $VenvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 失败（退出码 $LASTEXITCODE）。请检查上方 pip 输出；修复后重新运行本脚本即可续装。"
    }
}

# 1. 定位 Python >= 3.10（优先 python，其次 py -3）
$PythonCommand = $null
foreach ($Candidate in @(@("python"), @("py", "-3"))) {
    if (Test-PythonAtLeast310 $Candidate) { $PythonCommand = $Candidate; break }
}
if (-not $PythonCommand) {
    throw "未找到 Python >= 3.10（已尝试 python 与 py -3）。请先安装 Python 3.10+ 并加入 PATH。"
}
Write-Host ("==> 使用 Python 入口: " + ($PythonCommand -join " "))

# 2. 虚拟环境（已存在则复用）
$VenvPython = Join-Path $Root "agent_exp_env\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> 创建虚拟环境 agent_exp_env"
    & $PythonCommand @("-m", "venv", (Join-Path $Root "agent_exp_env"))
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败（退出码 $LASTEXITCODE）。" }
} else {
    Write-Host "==> 虚拟环境已存在，直接复用"
}

# 3. Python 侧依赖（分组安装，与 README「初次安装」一致）
Invoke-PipStep $VenvPython "安装本地包 exp-agent（pip install -e .）" @("-m", "pip", "install", "-e", ".")
Invoke-PipStep $VenvPython "安装仪器通信与分析依赖" @("-m", "pip", "install", "pyvisa", "pyvisa-py", "pyserial", "zhinst", "numpy", "scipy", "matplotlib", "pyyaml", "jupyter")
Invoke-PipStep $VenvPython "安装 GUI 后端依赖" @("-m", "pip", "install", "-r", "GUI\requirements.txt")
Invoke-PipStep $VenvPython "依赖一致性检查（pip check）" @("-m", "pip", "check")

# 4. 前端依赖
if ($SkipFrontend) {
    Write-Host "==> 已按参数跳过前端依赖（-SkipFrontend）"
} else {
    $Npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    if (-not $Npm) {
        Write-Warning "未找到 npm.cmd，已跳过前端依赖。请安装 Node.js ^20.19.0 || >=22.12.0 后重新运行本脚本。"
    } elseif (Test-Path (Join-Path $Root "GUI\frontend\node_modules")) {
        Write-Host "==> GUI/frontend/node_modules 已存在，跳过 npm ci；如需重装请先删除该目录再运行"
    } else {
        Write-Host "==> 安装前端依赖（npm ci）"
        Push-Location (Join-Path $Root "GUI\frontend")
        try {
            & $Npm.Source @("ci")
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci 失败（退出码 $LASTEXITCODE）。请检查上方 npm 输出；修复后重新运行本脚本。"
            }
        } finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "安装完成。启动 GUI：双击 start_gui.bat，或运行 .\GUI\start.ps1。"
