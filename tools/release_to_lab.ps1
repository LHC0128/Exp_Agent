# 发布脚本：把开发目录 master 的已测试提交快进发布到运行目录 lab 分支。
# 只在运行目录空闲时发布；只允许快进；依赖变化时停下等用户授权手动安装。
# 顺序原则：能提前做的检查都在停 GUI 之前完成；停 GUI 前再查一次空闲；
# 运行目录代码只在 GUI 停止后才改动，构建失败自动退回原版本并按原状态启动。
#
# 用法（在开发目录执行）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\release_to_lab.ps1
#   可选参数：
#     -RunDir <路径>        运行目录，默认 D:\Code\exp_agent_run
#     -DevDir <路径>        开发目录，默认脚本所在仓库
#     -SkipRestart          只更新代码和前端，不停止/启动 GUI；仅在 GUI 未运行时可用
#     -Rollback <标签>      回滚到运行目录的某个 lab-* 标签（停 GUI → 回退 → 重建前端 → 启动）
#     -DryRun               只打印发布计划和只读预检，不做任何改动
#     -Resume               依赖已手动安装完成后，跳过依赖检查继续发布
#     -HealthTimeoutSec <n> 启动后等待健康检查的秒数，默认 180（启动器自身给后端 120 秒）
#
# 约定接口：运行目录 start_lab.vbs 启动、stop_gui.bat 停止（等安全收尾；停止会取消正在运行的任务，
# 所以停止前必须确认空闲）。
# 日志：<运行目录>\tmp\release.log。
# 演练测试：tools\test_release_to_lab.ps1（临时仓库 + 假启动/停止/npm/health，不碰真实环境）。

param(
    [string]$RunDir = 'D:\Code\exp_agent_run',
    [string]$DevDir,
    [switch]$SkipRestart,
    [string]$Rollback,
    [switch]$DryRun,
    [switch]$Resume,
    [int]$HealthTimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
# git 输出含中文（提交信息、路径），统一按 UTF-8 解码，避免乱码。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:GIT_MERGE_AUTOEDIT = 'no'

if (-not $DevDir) { $DevDir = Split-Path -Parent $PSScriptRoot }
$HealthUrl = 'http://127.0.0.1:8000/api/health'
$DependencyPaths = @('pyproject.toml', 'constraints/', 'GUI/frontend/package-lock.json')
$script:LogFile = $null

function Resolve-FullPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
}

function Write-ReleaseLog([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    if ($script:LogFile) {
        Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
    }
}

function Stop-Release([string]$Message) {
    Write-ReleaseLog "失败：$Message"
    exit 1
}

# git 封装：所有 git 调用都带 -C <仓库>；失败默认抛错，-AllowFail 时返回退出码。
# 不解析 git 的本地化文本，只用退出码、HEAD 对比和 porcelain 输出判断结果。
function Invoke-Git {
    param([string]$RepoDir, [string[]]$GitArgs, [switch]$AllowFail)
    $ErrorActionPreference = 'Continue'
    $output = & git -C $RepoDir @GitArgs 2>&1
    $code = $LASTEXITCODE
    $text = (@($output) | ForEach-Object { "$_" }) -join "`n"
    if ($code -ne 0 -and -not $AllowFail) {
        throw "git $($GitArgs -join ' ') 失败（退出码 $code）：$text"
    }
    # 不做 Trim：porcelain 状态行首列可能是空格（如 " M path"），Trim 会破坏列对齐。
    return [pscustomobject]@{ Code = $code; Text = $text }
}

function Get-GitHead([string]$RepoDir) {
    return (Invoke-Git $RepoDir @('rev-parse', 'HEAD')).Text.Trim()
}

function Get-GitBranch([string]$RepoDir) {
    return (Invoke-Git $RepoDir @('rev-parse', '--abbrev-ref', 'HEAD')).Text.Trim()
}

function Get-ShortRev([string]$Rev) {
    if ($Rev.Length -gt 8) { return $Rev.Substring(0, 8) }
    return $Rev
}

# 健康检查：连接被拒 = GUI 未运行 = 返回 $null；其他错误上抛。
function Get-HealthState {
    try {
        return Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 5
    } catch {
        $ex = $_.Exception
        if ($ex -is [System.Net.WebException] -and
            $ex.Status -eq [System.Net.WebExceptionStatus]::ConnectFailure) { return $null }
        if ($ex.Message -match '无法连接|拒绝|refused|No connection could be made') { return $null }
        throw
    }
}

function Assert-GuiIdle {
    $health = Get-HealthState
    if ($null -eq $health) {
        Write-ReleaseLog 'GUI 未运行（健康检查连接被拒），视为空闲。'
        return $null
    }
    if ($health.hardware_busy -or [int]$health.queued_hardware_jobs -gt 0) {
        Stop-Release ('GUI 硬件任务忙（hardware_busy={0}，queued_hardware_jobs={1}），拒绝发布；请等当前实验安全收尾后重试。' -f $health.hardware_busy, $health.queued_hardware_jobs)
    }
    Write-ReleaseLog "GUI 空闲（instance_id: $($health.instance_id)）。"
    return $health
}

# -SkipRestart 不停 GUI：GUI 运行中改代码会让后端在之后的按需导入、参数读取中读到新版本，必须拒绝。
function Assert-SkipRestartAllowed([string]$When) {
    if ($SkipRestart -and $null -ne (Get-HealthState)) {
        Stop-Release "-SkipRestart 只能在 GUI 未运行时使用（$When 检测到 GUI 在运行）；运行中的 GUI 不能在其下更新代码。去掉 -SkipRestart 让脚本先停 GUI，或先手动 stop_gui.bat。"
    }
}

# lab-YYYYMMDD-N 标签按日期 + 当日序号数值排序（字典序在 N>=10 时错误）。
function Get-LabTagsSorted([string]$RepoDir) {
    $result = Invoke-Git $RepoDir @('tag', '--list', 'lab-*')
    $tags = @()
    foreach ($line in ($result.Text -split "`n")) {
        $name = $line.Trim()
        if ($name -match '^lab-(\d{8})-(\d+)$') {
            $tags += [pscustomobject]@{ Name = $name; Day = $Matches[1]; Seq = [int]$Matches[2] }
        }
    }
    # 逗号包裹：单元素数组经 return 会被解包成 PSCustomObject（无 .Count），调用方 .Count 判断会失效。
    return ,@($tags | Sort-Object Day, Seq)
}

function Get-RepoStatusLines([string]$RepoDir) {
    $result = Invoke-Git $RepoDir @('-c', 'core.quotepath=false', 'status', '--porcelain')
    if (-not $result.Text.Trim()) { return @() }
    return @($result.Text -split "`n" | Where-Object { $_ })
}

# porcelain 行分类：params/ 内（含未跟踪）与其他改动分开。
function Split-StatusByParams([string[]]$Lines) {
    $params = @()
    $other = @()
    foreach ($line in $Lines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ')[-1] }
        $path = $path.Trim('"')
        if ($path -like 'params/*' -or $path -eq 'params') { $params += $line } else { $other += $line }
    }
    return @{ Params = $params; Other = $other }
}

function Assert-RunDirOnLab([string]$RunDirPath) {
    $branch = Get-GitBranch $RunDirPath
    if ($branch -ne 'lab') {
        Stop-Release "运行目录当前分支是 $branch，应为 lab；请先人工切回 lab 分支。"
    }
}

# 两个提交之间依赖清单的变化（文件名列表文本）；无变化返回空串。
function Get-DependencyChanges([string]$RepoDir, [string]$From, [string]$To) {
    $diff = Invoke-Git $RepoDir (@('diff', '--name-only', $From, $To, '--') + $DependencyPaths)
    return $diff.Text.Trim()
}

function Get-DependencyInstallHint {
    return ('需用户授权后在运行目录手动执行：' +
        "agent_exp_env\Scripts\python.exe -m pip install -c constraints/development.txt -e `".[runtime,instruments,analysis,gui]`"；" +
        'GUI\frontend 下 npm ci。')
}

# 临时目录构建前端，成功后替换 dist/（旧 dist 先改名 dist.prev）。成功返回 $null，失败返回原因，旧 dist 保持不变。
function Update-FrontendDist([string]$RunDirPath) {
    $frontendDir = Join-Path $RunDirPath 'GUI\frontend'
    if (-not (Test-Path -LiteralPath $frontendDir)) { return "未找到前端目录：$frontendDir" }
    $npm = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
    if (-not $npm) { return '未找到 npm.cmd，请先安装 Node.js / npm。' }

    $tmpOut = Join-Path $RunDirPath ('tmp\dist-build-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmpOut | Out-Null
    Write-ReleaseLog "构建前端：npm run build -- --outDir $tmpOut"
    Push-Location -LiteralPath $frontendDir
    try {
        # 构建输出直接显示，不能进入函数输出流：本函数用返回值（$null=成功）表示构建结果。
        & $npm.Source run build -- --outDir $tmpOut | Out-Host
        $buildCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($buildCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $tmpOut 'index.html'))) {
        Remove-Item -LiteralPath $tmpOut -Recurse -Force -ErrorAction SilentlyContinue
        return "前端构建失败（退出码 $buildCode），旧 dist/ 保持不变。"
    }

    $dist = Join-Path $frontendDir 'dist'
    $prev = Join-Path $frontendDir 'dist.prev'
    if (Test-Path -LiteralPath $prev) { Remove-Item -LiteralPath $prev -Recurse -Force }
    $hadDist = Test-Path -LiteralPath $dist
    if ($hadDist) { Rename-Item -LiteralPath $dist -NewName 'dist.prev' }
    try {
        Move-Item -LiteralPath $tmpOut -Destination $dist
    } catch {
        if ($hadDist) { Rename-Item -LiteralPath $prev -NewName 'dist' }
        Remove-Item -LiteralPath $tmpOut -Recurse -Force -ErrorAction SilentlyContinue
        return "替换 dist/ 失败，已恢复旧 dist：$($_.Exception.Message)"
    }
    if ($hadDist) { Remove-Item -LiteralPath $prev -Recurse -Force }
    Write-ReleaseLog '前端 dist/ 已更新。'
    return $null
}

function Stop-LabGui([string]$RunDirPath) {
    $stopBat = Join-Path $RunDirPath 'stop_gui.bat'
    if (-not (Test-Path -LiteralPath $stopBat)) { Stop-Release "未找到 $stopBat（运行目录缺少停止入口）。" }
    Write-ReleaseLog '停止 GUI（stop_gui.bat，等待安全收尾）...'
    # stdin 重定向 NUL：stop_gui.bat 失败时会 pause，防止无人值守时挂起。
    $stopArgs = '/c ""{0}" < NUL"' -f $stopBat
    $stopProc = Start-Process -FilePath 'cmd.exe' -ArgumentList $stopArgs -WindowStyle Hidden -Wait -PassThru
    if ($stopProc.ExitCode -ne 0) {
        Stop-Release "stop_gui.bat 失败（退出码 $($stopProc.ExitCode)）；GUI 可能仍在运行，运行目录未改动，请人工检查后再发布。"
    }
}

# 停 GUI 前的最后一次空闲检查：从首次检查到此刻，用户可能已开始新实验；stop_gui.bat 会取消运行中的任务。
# 返回停之前的健康信息（GUI 未运行时为 $null），供启动后比对 instance_id。
function Stop-LabGuiWhenIdle([string]$RunDirPath) {
    Write-ReleaseLog '停 GUI 前再次检查空闲...'
    $health = Assert-GuiIdle
    Stop-LabGui $RunDirPath
    return $health
}

# start_lab.vbs → 轮询健康检查至多 TimeoutSec 次（每次间隔 1 秒），instance_id 与停之前不同即成功。
function Start-LabGui([string]$RunDirPath, [string]$PreInstanceId, [int]$TimeoutSec) {
    $startVbs = Join-Path $RunDirPath 'start_lab.vbs'
    if (-not (Test-Path -LiteralPath $startVbs)) { Stop-Release "未找到 $startVbs（运行目录缺少启动入口）。" }
    Write-ReleaseLog '启动 GUI（start_lab.vbs）...'
    Start-Process -FilePath 'wscript.exe' -ArgumentList ('"{0}"' -f $startVbs) -WindowStyle Hidden | Out-Null

    for ($attempt = 0; $attempt -lt $TimeoutSec; $attempt++) {
        Start-Sleep -Seconds 1
        $health = Get-HealthState
        if ($null -ne $health -and
            ($null -eq $PreInstanceId -or $health.instance_id -ne $PreInstanceId)) {
            Write-ReleaseLog "GUI 已启动（instance_id: $($health.instance_id)）。"
            return
        }
    }
    Stop-Release "等待 GUI 启动超时（$TimeoutSec 秒）：instance_id 未变化或健康检查无响应；请检查运行目录 tmp\gui-launcher.log。"
}

# 构建失败：运行目录退回到 $RestoreRef（旧 dist 未动，与旧代码一致），删除本次新建的标签，
# 未指定 -SkipRestart 时按原版本启动 GUI，最后以失败结束。
function Restore-AfterBuildFailure([string]$RunDirPath, [string]$Reason, [string]$RestoreRef, [string]$CreatedTag, [string]$PreInstanceId) {
    Write-ReleaseLog $Reason
    if ($RestoreRef) {
        Invoke-Git $RunDirPath @('reset', '--hard', $RestoreRef) | Out-Null
        Write-ReleaseLog "运行目录已退回 $(Get-ShortRev $RestoreRef)。"
    } else {
        Write-ReleaseLog '没有可退回的上一版本，运行目录代码保持当前状态。'
    }
    if ($CreatedTag) {
        Invoke-Git $RunDirPath @('tag', '-d', $CreatedTag) | Out-Null
        Write-ReleaseLog "已删除本次新建的标签 $CreatedTag。"
    }
    if (-not $SkipRestart) {
        Write-ReleaseLog '按原版本启动 GUI...'
        Start-LabGui $RunDirPath $PreInstanceId $HealthTimeoutSec
    }
    Stop-Release "$Reason 已退回原版本$(if (-not $SkipRestart) { '并重新启动 GUI' })。"
}

# -DryRun：打印计划并做只读预检，不确认、不改动、不重启。
function Show-ReleasePlan([string]$DevDirPath, [string]$RunDirPath) {
    Write-ReleaseLog '===== DryRun：发布计划（只读预检，不做任何改动） ====='
    $blockers = @()

    $devBranch = Get-GitBranch $DevDirPath
    $devHead = Get-GitHead $DevDirPath
    Write-ReleaseLog "开发目录：$DevDirPath（分支 $devBranch，HEAD $(Get-ShortRev $devHead)）"
    if ($devBranch -ne 'master') { $blockers += "开发目录分支是 $devBranch，应为 master" }
    $staged = Invoke-Git $DevDirPath @('diff', '--cached', '--quiet') -AllowFail
    if ($staged.Code -ne 0) { $blockers += '开发目录有已暂存改动' }

    if (-not (Test-Path -LiteralPath $RunDirPath)) {
        Write-ReleaseLog "运行目录不存在：$RunDirPath（需先完成 T4 建运行目录）"
        $blockers += '运行目录不存在'
    } else {
        $runBranch = Get-GitBranch $RunDirPath
        $runHead = Get-GitHead $RunDirPath
        $tags = Get-LabTagsSorted $RunDirPath
        $latestTag = if ($tags.Count -gt 0) { $tags[-1].Name } else { '（无）' }
        Write-ReleaseLog "运行目录：$RunDirPath（分支 $runBranch，HEAD $(Get-ShortRev $runHead)，最新标签 $latestTag）"
        if ($runBranch -ne 'lab') { $blockers += "运行目录分支是 $runBranch，应为 lab" }

        $status = Split-StatusByParams (Get-RepoStatusLines $RunDirPath)
        if ($status.Other.Count -gt 0) { $blockers += '运行目录有 params/ 以外的未提交改动' }
        if ($status.Params.Count -gt 0) {
            Write-ReleaseLog "将先在 lab 提交参数回存（$($status.Params.Count) 项），再合并回开发目录并停止，提示重新测试。"
        }

        $health = Get-HealthState
        if ($null -eq $health) {
            Write-ReleaseLog 'GUI 未运行，视为空闲。'
        } elseif ($health.hardware_busy -or [int]$health.queued_hardware_jobs -gt 0) {
            $blockers += 'GUI 硬件任务忙'
        } else {
            Write-ReleaseLog "GUI 空闲（instance_id: $($health.instance_id)）。"
            if ($SkipRestart) { $blockers += '-SkipRestart 只能在 GUI 未运行时使用' }
        }

        $ff = Invoke-Git $RunDirPath @('merge-base', '--is-ancestor', 'HEAD', 'origin/master') -AllowFail
        if ($ff.Code -ne 0) {
            $blockers += '运行目录 lab 不能快进到 origin/master（需先处理 lab 上的独立提交）'
        } else {
            $deps = Get-DependencyChanges $RunDirPath 'HEAD' 'origin/master'
            if ($deps -and -not $Resume) {
                Write-ReleaseLog "依赖清单有变化：发布会停 GUI、更新代码后暂停，等授权手动安装依赖再 -Resume：`n$deps"
            }
        }
        Write-ReleaseLog '（DryRun 只比对本地已有的 origin/master，不执行 fetch。）'
    }

    Write-ReleaseLog '计划步骤：1 开发目录检查与测试确认 → 2 空闲检查 → 3 参数回存/合并 → 4 预检（快进、依赖、标签）→ 5 再查空闲并停 GUI → 6 快进并打标签 → 7 构建前端（失败则退回原版本）→ 8 启动 GUI 并验证 instance_id。'
    if ($SkipRestart) { Write-ReleaseLog '已指定 -SkipRestart：不停止/启动 GUI（要求 GUI 未运行）。' }
    if ($blockers.Count -gt 0) {
        foreach ($b in $blockers) { Write-ReleaseLog "预检阻塞：$b" }
        exit 1
    }
    Write-ReleaseLog 'DryRun 预检通过。'
    exit 0
}

# ===== 主流程 =====
$DevDir = Resolve-FullPath $DevDir
$RunDir = Resolve-FullPath $RunDir
if ($HealthTimeoutSec -lt 1) { Stop-Release '-HealthTimeoutSec 必须 >= 1。' }

if (-not (Test-Path -LiteralPath (Join-Path $DevDir '.git'))) {
    Stop-Release "开发目录不是 git 仓库：$DevDir"
}
if (Test-Path -LiteralPath $RunDir) {
    if (-not (Test-Path -LiteralPath (Join-Path $RunDir '.git'))) {
        Stop-Release "运行目录不是 git 仓库：$RunDir"
    }
    $tmpDir = Join-Path $RunDir 'tmp'
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    $script:LogFile = Join-Path $tmpDir 'release.log'
} elseif (-not $DryRun) {
    Stop-Release "运行目录不存在：$RunDir（请先完成 T4 建运行目录与环境）。"
}

Write-ReleaseLog "===== 发布开始：开发目录 $DevDir → 运行目录 $RunDir ====="

if ($DryRun) {
    if ($Rollback) { Write-ReleaseLog "回滚计划：停 GUI → reset --hard $Rollback → 重建前端 → 启动 GUI。" }
    Show-ReleasePlan $DevDir $RunDir
}

if ($Rollback) {
    # ===== 回滚流程：检查 → 确认 → 再查空闲并停 GUI → reset --hard → 重建前端 → 启动 =====
    Write-ReleaseLog "===== 回滚到标签 $Rollback ====="
    $tagCheck = Invoke-Git $RunDir @('rev-parse', '--verify', "refs/tags/$Rollback") -AllowFail
    if ($tagCheck.Code -ne 0) { Stop-Release "运行目录不存在标签：$Rollback" }
    Assert-RunDirOnLab $RunDir
    Assert-GuiIdle | Out-Null
    Assert-SkipRestartAllowed '回滚开始时'

    $dirty = Get-RepoStatusLines $RunDir
    if ($dirty.Count -gt 0) {
        Stop-Release ("运行目录有未提交改动，回滚会丢弃它们，已停止：`n" + ($dirty -join "`n"))
    }
    $headBefore = Get-GitHead $RunDir
    $discarded = Invoke-Git $RunDir @('log', '--oneline', "$Rollback..HEAD")
    if ($discarded.Text.Trim()) {
        Write-ReleaseLog "回滚将丢弃以下提交：`n$($discarded.Text)"
    } else {
        Write-ReleaseLog "HEAD 已在 $Rollback，无提交被丢弃（仍会重建前端并重启）。"
    }
    $rollbackDeps = Get-DependencyChanges $RunDir 'HEAD' $Rollback
    if ($rollbackDeps) {
        Write-ReleaseLog "注意：回滚跨越依赖清单变化，回退代码后会停下，需按旧版本重新安装依赖：`n$rollbackDeps"
    }
    $answer = Read-Host "确认回滚到 $Rollback（y/N）"
    if ("$answer".Trim().ToLower() -ne 'y') { Stop-Release '未确认，已取消回滚。' }

    $preId = $null
    if ($SkipRestart) {
        Assert-SkipRestartAllowed '回退代码前'
    } else {
        $health = Stop-LabGuiWhenIdle $RunDir
        if ($health) { $preId = [string]$health.instance_id }
    }

    Invoke-Git $RunDir @('reset', '--hard', $Rollback) | Out-Null
    Write-ReleaseLog "已 reset --hard 到 $Rollback。"
    if ($rollbackDeps) {
        Stop-Release ("代码已回退到 $Rollback，GUI 保持停止。依赖清单与回滚前不同；" + (Get-DependencyInstallHint) +
            "完成后重跑同一回滚命令（-Rollback $Rollback）完成前端构建与启动。")
    }
    $buildError = Update-FrontendDist $RunDir
    if ($buildError) {
        Restore-AfterBuildFailure $RunDir $buildError $headBefore $null $preId
    }
    if ($SkipRestart) {
        Write-ReleaseLog '已指定 -SkipRestart：不启动 GUI。'
    } else {
        Start-LabGui $RunDir $preId $HealthTimeoutSec
    }
    Write-ReleaseLog '===== 回滚完成 ====='
    exit 0
}

# ===== 正常发布流程 =====
# 步骤 1：开发目录检查 + 测试确认。
Write-ReleaseLog '步骤 1：检查开发目录...'
$devBranch = Get-GitBranch $DevDir
if ($devBranch -ne 'master') { Stop-Release "开发目录当前分支是 $devBranch，发布只允许从 master 进行。" }
$staged = Invoke-Git $DevDir @('diff', '--cached', '--quiet') -AllowFail
if ($staged.Code -ne 0) { Stop-Release '开发目录有已暂存改动，请先提交或取消暂存后再发布。' }
$devHeadBefore = Get-GitHead $DevDir
Write-ReleaseLog "开发目录 master HEAD：$(Get-ShortRev $devHeadBefore)"
$answer = Read-Host '确认本提交已通过测试（y/N）'
if ("$answer".Trim().ToLower() -ne 'y') { Stop-Release '未确认测试通过，已取消发布。' }

# 步骤 2：首次空闲检查（提前拦截，不改动任何东西）。
Write-ReleaseLog '步骤 2：检查 GUI 是否空闲...'
Assert-GuiIdle | Out-Null
Assert-SkipRestartAllowed '发布开始时'

# 步骤 3：参数回存与合并（只提交 params/，不改代码，GUI 可继续运行）。
Write-ReleaseLog '步骤 3：检查运行目录改动...'
Assert-RunDirOnLab $RunDir
$status = Split-StatusByParams (Get-RepoStatusLines $RunDir)
if ($status.Other.Count -gt 0) {
    Stop-Release ("运行目录有 params/ 以外的未提交改动，已停止；请人工处理：`n" + ($status.Other -join "`n"))
}
if ($status.Params.Count -gt 0) {
    $today = Get-Date -Format 'yyyy-MM-dd'
    Write-ReleaseLog "运行目录 params/ 有 $($status.Params.Count) 项改动，提交参数回存..."
    Invoke-Git $RunDir @('add', '--', 'params/') | Out-Null
    Invoke-Git $RunDir @('commit', '-m', "运行参数回存 $today") | Out-Null
    Write-ReleaseLog '把参数回存合并回开发目录 master...'
    $pull = Invoke-Git $DevDir @('pull', '--no-rebase', '--no-edit', $RunDir, 'lab') -AllowFail
    if ($pull.Code -ne 0) {
        Invoke-Git $DevDir @('merge', '--abort') -AllowFail | Out-Null
        Stop-Release "参数回存合并冲突，已执行 git merge --abort；请人工处理冲突后再发布。git 输出：`n$($pull.Text)"
    }
    $devHeadAfter = Get-GitHead $DevDir
    if ($devHeadAfter -ne $devHeadBefore) {
        Stop-Release '参数回存已合并进 master（开发目录 HEAD 变化）；请重新测试后再运行发布。'
    }
} else {
    Write-ReleaseLog '运行目录无未提交改动，跳过参数回存。'
}

# 步骤 4：停 GUI 前的预检——快进可行性、标签、依赖变化，全部只读。
Write-ReleaseLog '步骤 4：预检快进与依赖...'
Invoke-Git $RunDir @('fetch', 'origin') | Out-Null
$ffCheck = Invoke-Git $RunDir @('merge-base', '--is-ancestor', 'HEAD', 'origin/master') -AllowFail
if ($ffCheck.Code -ne 0) {
    Stop-Release '运行目录 lab 不能快进到 origin/master（lab 上有未合并进 master 的提交），已停止；请先把 lab 的提交合并回 master 并重新测试。'
}
$runHeadBefore = Get-GitHead $RunDir
$targetHead = (Invoke-Git $RunDir @('rev-parse', 'origin/master')).Text.Trim()
$tags = Get-LabTagsSorted $RunDir
$targetTags = @((Invoke-Git $RunDir @('tag', '--points-at', $targetHead)).Text -split "`n" |
    ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^lab-\d{8}-\d+$' })
if ($targetTags.Count -gt 0) {
    # 幂等：目标提交已打过标签（例如上次发布中途停下后重跑或 -Resume）则复用，不新建。
    $newTag = $targetTags[0]
    $newTagIndex = -1
    for ($i = 0; $i -lt $tags.Count; $i++) { if ($tags[$i].Name -eq $newTag) { $newTagIndex = $i } }
    $prevTag = if ($newTagIndex -gt 0) { $tags[$newTagIndex - 1].Name } else { $null }
    $createTag = $false
    Write-ReleaseLog "目标提交已有标签 $newTag，复用（不新建）。"
} else {
    $prevTag = if ($tags.Count -gt 0) { $tags[-1].Name } else { $null }
    $today = Get-Date -Format 'yyyyMMdd'
    $seq = 1
    foreach ($t in $tags) { if ($t.Day -eq $today -and $t.Seq -ge $seq) { $seq = $t.Seq + 1 } }
    $newTag = "lab-$today-$seq"
    $createTag = $true
}

$depsChanged = $false
if ($Resume) {
    Write-ReleaseLog '已指定 -Resume：跳过依赖检查（视为依赖已手动安装完成）。'
} else {
    $deps = Get-DependencyChanges $RunDir 'HEAD' 'origin/master'
    if ($deps) {
        $depsChanged = $true
        Write-ReleaseLog "依赖清单有变化：`n$deps"
        $answer = Read-Host '依赖有变化：将停止 GUI、更新代码后暂停，等你授权安装依赖再用 -Resume 完成。继续（y/N）'
        if ("$answer".Trim().ToLower() -ne 'y') { Stop-Release '未确认依赖变化的发布，已取消；运行目录与 GUI 均未改动。' }
    } else {
        Write-ReleaseLog '依赖清单无变化。'
    }
}

# 步骤 5：再查空闲并停 GUI（-SkipRestart 时改为确认 GUI 仍未运行）。
$preId = $null
if ($SkipRestart) {
    Assert-SkipRestartAllowed '更新代码前'
} else {
    Write-ReleaseLog '步骤 5：停止 GUI...'
    $health = Stop-LabGuiWhenIdle $RunDir
    if ($health) { $preId = [string]$health.instance_id }
}

# 步骤 6：GUI 已停止，快进并打标签。
Write-ReleaseLog '步骤 6：快进运行目录到 origin/master...'
if ($runHeadBefore -ne $targetHead) {
    Invoke-Git $RunDir @('merge', '--ff-only', 'origin/master') | Out-Null
}
$createdTag = $null
if ($createTag) {
    Invoke-Git $RunDir @('tag', $newTag) | Out-Null
    $createdTag = $newTag
    Write-ReleaseLog "已快进到 origin/master 并打标签 $newTag。"
}
if ($depsChanged) {
    Stop-Release ("代码已更新到 $newTag，GUI 保持停止。依赖清单相对上一版本有变化；" + (Get-DependencyInstallHint) +
        '完成后加 -Resume 重跑本脚本，完成前端构建与启动。')
}

# 步骤 7：构建前端；失败则退回发布前的版本（本次运行没有改动代码时退回上一个标签）。
Write-ReleaseLog '步骤 7：构建前端...'
$buildError = Update-FrontendDist $RunDir
if ($buildError) {
    $restoreRef = if ($runHeadBefore -ne $targetHead) { $runHeadBefore } elseif ($prevTag) { $prevTag } else { $null }
    Restore-AfterBuildFailure $RunDir $buildError $restoreRef $createdTag $preId
}

# 步骤 8：启动 GUI。
if ($SkipRestart) {
    Write-ReleaseLog '已指定 -SkipRestart：不启动 GUI；代码与前端已更新，下次用 start_lab.vbs 启动。'
} else {
    Write-ReleaseLog '步骤 8：启动 GUI...'
    Start-LabGui $RunDir $preId $HealthTimeoutSec
}
Write-ReleaseLog "===== 发布完成：$newTag ====="
exit 0
