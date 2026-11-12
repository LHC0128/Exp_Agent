# 发布脚本演练测试：在 .cache/release-test/ 下建临时「开发仓库」及其 clone 作「运行目录」，
# 用假的 health/npm/启动/停止/确认输入覆盖发布全流程。
# 不连真实 8000 端口、不启动真实 GUI、不碰 D:\Code\exp_agent_run。
# 运行：powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_release_to_lab.ps1
$ErrorActionPreference = 'Stop'
# git 输出含中文（提交信息），统一按 UTF-8 解码。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ReleaseScript = Join-Path $PSScriptRoot 'release_to_lab.ps1'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TestRoot = Join-Path $RepoRoot '.cache\release-test'
$HealthUrl = 'http://127.0.0.1:8000/api/health'
$TestState = @{}

function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

# ===== 外部命令替换（缝隙都是 cmdlet；发布脚本内的自定义函数不受影响） =====

function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    $TestState.HealthCalls += 1
    Assert ($Uri -eq $HealthUrl) "健康检查地址变化：$Uri"
    if ($TestState.HealthDown) {
        throw (New-Object System.Net.WebException('无法连接到远程服务器', [System.Net.WebExceptionStatus]::ConnectFailure))
    }
    # 模拟首次空闲检查之后用户开始了新实验：第 N 次之后的检查返回忙。
    if ($TestState.Case.BusyAfterCalls -and $TestState.HealthCalls -gt $TestState.Case.BusyAfterCalls) {
        $busy = New-HealthObject $TestState.Health.instance_id
        $busy.hardware_busy = $true
        return $busy
    }
    return $TestState.Health
}

function Read-Host {
    param($Prompt)
    $TestState.Prompts += "$Prompt"
    if ($TestState.Answers.Count -gt 0) {
        $answer = $TestState.Answers[0]
        $TestState.Answers = @($TestState.Answers | Select-Object -Skip 1)
        return $answer
    }
    return 'n'
}

function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -eq 'npm.cmd') { return [pscustomobject]@{ Source = 'Invoke-FakeNpm' } }
    return Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}

function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    if ($FilePath -eq 'cmd.exe') {
        $TestState.StopCalls += "$ArgumentList"
        Assert ("$ArgumentList" -match 'stop_gui\.bat') "停止命令不匹配：$ArgumentList"
        Assert ("$ArgumentList" -match '< NUL') '停止命令未重定向 stdin（pause 会挂起）'
        Assert ($Wait -and $PassThru) '停止命令未等待退出或未取退出码'
        # 记录停 GUI 那一刻运行目录的 HEAD 与 dist：用来断言代码只在 GUI 停止后才改动。
        $TestState.HeadAtStop = Invoke-FixtureGit $TestState.RunDir @('rev-parse', 'HEAD')
        $TestState.DistAtStop = Get-DistContent $TestState.RunDir
        $code = if ($TestState.Case.StopFailure) { 1 } else { 0 }
        if ($code -eq 0) { $TestState.HealthDown = $true }
        return [pscustomobject]@{ ExitCode = $code }
    }
    if ($FilePath -eq 'wscript.exe') {
        $TestState.StartCalls += "$ArgumentList"
        Assert ("$ArgumentList" -match 'start_lab\.vbs') "启动命令不匹配：$ArgumentList"
        if (-not $TestState.Case.RestartStuck) {
            # 模拟重启：新实例 instance_id 变化；GUI 原本未运行时变为可用。
            $TestState.HealthDown = $false
            $TestState.Health = New-HealthObject ('post-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        }
        return
    }
    throw "意外的 Start-Process：$FilePath"
}

function Start-Sleep {
    param($Seconds, $Milliseconds)
    # 演练中不真实等待；轮询次数由 -HealthTimeoutSec 控制。
}

function Invoke-FakeNpm {
    $joined = $args -join ' '
    $TestState.NpmCalls += $joined
    Assert ($args[0] -eq 'run' -and $args[1] -eq 'build') "npm 参数不匹配：$joined"
    # PowerShell 调用函数时会吞掉分隔符 "--"（原生命令 npm.cmd 不会），
    # 所以按 --outDir 定位输出目录，兼容两种形态。
    $idx = [array]::IndexOf($args, '--outDir')
    Assert ($idx -ge 0 -and $idx + 1 -lt $args.Count) "npm 未使用临时 outDir：$joined"
    $outDir = $args[$idx + 1]
    Assert ($outDir -like '*tmp\dist-build-*') "构建输出不在运行目录 tmp 下：$outDir"
    if ($TestState.Case.BuildFailure) {
        $global:LASTEXITCODE = 1
        return
    }
    # 真实 npm 会向标准输出打印构建日志；假 npm 同样输出，防止构建日志被误当成失败原因。
    Write-Output "> vite build --outDir $outDir"
    Write-Output '✓ built in 0.01s'
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Set-Content -LiteralPath (Join-Path $outDir 'index.html') -Value 'NEW BUILD' -Encoding UTF8
    $global:LASTEXITCODE = 0
}

function New-HealthObject([string]$InstanceId) {
    return [pscustomobject]@{
        instance_id = $InstanceId
        hardware_busy = $false
        queued_hardware_jobs = 0
    }
}

# ===== 真实 git 临时仓库夹具 =====

function Invoke-FixtureGit {
    param([string]$RepoDir, [string[]]$GitArgs)
    $ErrorActionPreference = 'Continue'
    $output = & git -C $RepoDir @GitArgs 2>&1
    $code = $LASTEXITCODE
    $text = (@($output) | ForEach-Object { "$_" }) -join "`n"
    if ($code -ne 0) { throw "夹具 git $($GitArgs -join ' ') 失败（$code）：$text" }
    return $text.Trim()
}

function Set-FixtureGitIdentity([string]$RepoDir) {
    Invoke-FixtureGit $RepoDir @('config', 'user.email', 'release-test@example.invalid') | Out-Null
    Invoke-FixtureGit $RepoDir @('config', 'user.name', '发布演练') | Out-Null
    Invoke-FixtureGit $RepoDir @('config', 'commit.gpgsign', 'false') | Out-Null
    Invoke-FixtureGit $RepoDir @('config', 'core.autocrlf', 'false') | Out-Null
}

function Write-FixtureFile([string]$Path, [string]$Content) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -LiteralPath $Path -Value $Content -Encoding UTF8
}

# 每个用例独立夹具：dev 初始提交 → clone 出 run（lab 分支 + 初始标签 + 旧 dist）→ dev 追加一个新提交。
function New-Fixture([string]$CaseName) {
    $root = Join-Path $TestRoot ($CaseName + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $dev = Join-Path $root 'dev'
    $run = Join-Path $root 'run'
    New-Item -ItemType Directory -Force -Path $dev | Out-Null
    Invoke-FixtureGit $dev @('init', '-b', 'master') | Out-Null
    Set-FixtureGitIdentity $dev
    Write-FixtureFile (Join-Path $dev '.gitignore') "agent_exp_env/`ndata/`n.cache/`ntmp/`nGUI/frontend/dist/`nGUI/frontend/node_modules/`n.lab_runtime`n"
    Write-FixtureFile (Join-Path $dev 'params\mapping.yaml') "base: 1`n"
    Write-FixtureFile (Join-Path $dev 'pyproject.toml') "[project]`nname = 'fixture'`nversion = '0.1.0'`n"
    Write-FixtureFile (Join-Path $dev 'constraints\development.txt') "# 约束占位`n"
    Write-FixtureFile (Join-Path $dev 'GUI\frontend\package.json') '{"name":"fixture"}'
    Write-FixtureFile (Join-Path $dev 'GUI\frontend\package-lock.json') '{"lockfileVersion":3}'
    Write-FixtureFile (Join-Path $dev 'GUI\frontend\src\app.txt') "v1`n"
    Write-FixtureFile (Join-Path $dev 'start_lab.vbs') "' 演练占位：不会真正执行`n"
    Write-FixtureFile (Join-Path $dev 'stop_gui.bat') "@echo off`r`nexit /b 0`r`n"
    Invoke-FixtureGit $dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $dev @('commit', '-m', '初始提交') | Out-Null

    Invoke-FixtureGit $root @('clone', $dev, $run) | Out-Null
    Set-FixtureGitIdentity $run
    Invoke-FixtureGit $run @('switch', '-c', 'lab', 'origin/master') | Out-Null
    Invoke-FixtureGit $run @('tag', 'lab-20260101-1') | Out-Null
    Write-FixtureFile (Join-Path $run '.lab_runtime') $run
    Write-FixtureFile (Join-Path $run 'GUI\frontend\dist\index.html') 'OLD BUILD'
    New-Item -ItemType Directory -Force -Path (Join-Path $run 'tmp') | Out-Null

    Write-FixtureFile (Join-Path $dev 'GUI\frontend\src\app.txt') "v2`n"
    Invoke-FixtureGit $dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $dev @('commit', '-m', '新功能') | Out-Null
    return @{ Root = $root; Dev = $dev; Run = $run }
}

function Get-FixtureHead([string]$RepoDir) { return Invoke-FixtureGit $RepoDir @('rev-parse', 'HEAD') }
function Get-FixtureTags([string]$RepoDir) {
    $text = Invoke-FixtureGit $RepoDir @('tag', '--list', 'lab-*')
    if (-not $text) { return @() }
    # 逗号包裹：单元素数组经 return 会被解包成字符串（无 .Count）。
    return ,@($text -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
function Get-DistContent([string]$RunDir) {
    $path = Join-Path $RunDir 'GUI\frontend\dist\index.html'
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return (Get-Content -LiteralPath $path -Raw).Trim()
}
function Test-NoMergeInProgress([string]$RepoDir) {
    $ErrorActionPreference = 'Continue'
    & git -C $RepoDir rev-parse -q --verify MERGE_HEAD 2>&1 | Out-Null
    return ($LASTEXITCODE -ne 0)
}

# ===== 用例执行 =====

function Invoke-ReleaseCase {
    param($Fixture, [hashtable]$Options = @{}, [string[]]$Answers = @('y'))
    $TestState.Answers = $Answers
    $TestState.Prompts = @()
    $TestState.StopCalls = @()
    $TestState.StartCalls = @()
    $TestState.NpmCalls = @()
    $TestState.HealthCalls = 0
    $TestState.HeadAtStop = $null
    $TestState.DistAtStop = $null
    $TestState.RunDir = $Fixture.Run
    $opts = @{ RunDir = $Fixture.Run; DevDir = $Fixture.Dev }
    foreach ($key in $Options.Keys) { $opts[$key] = $Options[$key] }
    $failure = $null
    $output = ''
    try {
        $output = @(& $ReleaseScript @opts 6>&1) | Out-String -Width 8192
        $exitCode = $LASTEXITCODE
    } catch {
        $failure = $_.Exception.Message
        $exitCode = -1
    }
    return @{ Output = $output; ExitCode = $exitCode; Failure = $failure }
}

function Assert-CaseOk($Result, [string]$Name) {
    Assert ($null -eq $Result.Failure) "${Name}：意外异常：$($Result.Failure)"
    Assert ($Result.ExitCode -eq 0) "${Name}：退出码 $($Result.ExitCode)，输出：$($Result.Output)"
}
function Assert-CaseFailed($Result, [string]$Name, [string]$Pattern) {
    Assert ($null -eq $Result.Failure) "${Name}：意外异常：$($Result.Failure)"
    Assert ($Result.ExitCode -ne 0) "${Name}：应失败但退出码为 0，输出：$($Result.Output)"
    Assert ($Result.Output -match $Pattern) "${Name}：错误信息不匹配「$Pattern」，输出：$($Result.Output)"
}

$Today = Get-Date -Format 'yyyyMMdd'
$Passed = 0

try {
    if (Test-Path -LiteralPath $TestRoot) { Remove-Item -LiteralPath $TestRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null

    # 1 正常发布
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-1'
    $f = New-Fixture 'normal'
    $runHeadBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseOk $r '正常发布'
    Assert ($TestState.HeadAtStop -eq $runHeadBefore) '正常发布：停 GUI 之前运行目录代码已被改动'
    Assert ($TestState.DistAtStop -eq 'OLD BUILD') '正常发布：停 GUI 之前 dist 已被替换'
    Assert ($TestState.HealthCalls -ge 2) '正常发布：停 GUI 前未再次检查空闲'
    Assert ((Get-FixtureHead $f.Run) -eq (Get-FixtureHead $f.Dev)) '正常发布：运行目录未快进到开发 HEAD'
    Assert ((Get-FixtureTags $f.Run) -contains "lab-$Today-1") '正常发布：未打今日标签'
    Assert ((Get-DistContent $f.Run) -eq 'NEW BUILD') '正常发布：dist 未更新'
    Assert (-not (Test-Path -LiteralPath (Join-Path $f.Run 'GUI\frontend\dist.prev'))) '正常发布：dist.prev 未清理'
    Assert ($TestState.StopCalls.Count -eq 1 -and $TestState.StartCalls.Count -eq 1) '正常发布：停止/启动次数不对'
    Assert ($TestState.Health.instance_id -ne 'pre-1') '正常发布：未验证新实例'
    $log = Get-Content -LiteralPath (Join-Path $f.Run 'tmp\release.log') -Raw
    Assert ($log -match '发布完成') '正常发布：日志缺少完成记录'
    $Passed += 1; Write-Host '通过：正常发布'

    # 2 忙时拒绝（hardware_busy）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-2'
    $TestState.Health.hardware_busy = $true
    $f = New-Fixture 'busy'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '忙时拒绝' '硬件任务忙'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '忙时拒绝：运行目录被改动'
    Assert ($TestState.NpmCalls.Count -eq 0 -and $TestState.StopCalls.Count -eq 0) '忙时拒绝：仍执行了构建/停止'
    $Passed += 1; Write-Host '通过：忙时拒绝（hardware_busy）'

    # 3 忙时拒绝（排队任务）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-3'
    $TestState.Health.queued_hardware_jobs = 2
    $f = New-Fixture 'queued'
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '排队拒绝' '硬件任务忙'
    Assert ($TestState.NpmCalls.Count -eq 0) '排队拒绝：仍执行了构建'
    $Passed += 1; Write-Host '通过：忙时拒绝（queued_hardware_jobs）'

    # 4 参数回存并合并：第一次停止提示重新测试，重跑完成发布
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-4'
    $f = New-Fixture 'params'
    Write-FixtureFile (Join-Path $f.Run 'params\mapping.yaml') "base: run`n"
    $devHeadBefore = Get-FixtureHead $f.Dev
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '参数回存' '重新测试'
    $labLog = Invoke-FixtureGit $f.Run @('log', '-1', '--format=%s')
    Assert ($labLog -match "^运行参数回存 $(Get-Date -Format 'yyyy-MM-dd')$") "参数回存：lab 提交信息不对：$labLog"
    Assert ((Get-FixtureHead $f.Dev) -ne $devHeadBefore) '参数回存：未合并进开发 master'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) '参数回存：不应打新标签'
    Assert ($TestState.NpmCalls.Count -eq 0 -and $TestState.StartCalls.Count -eq 0) '参数回存：不应构建/重启'
    $r2 = Invoke-ReleaseCase $f
    Assert-CaseOk $r2 '参数回存后重跑'
    Assert ((Get-FixtureHead $f.Run) -eq (Get-FixtureHead $f.Dev)) '参数回存后重跑：未快进'
    Assert ((Get-DistContent $f.Run) -eq 'NEW BUILD') '参数回存后重跑：dist 未更新'
    $Passed += 1; Write-Host '通过：参数回存并合并（含重跑完成）'

    # 5 合并冲突停止（merge --abort）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-5'
    $f = New-Fixture 'conflict'
    Write-FixtureFile (Join-Path $f.Dev 'params\mapping.yaml') "base: dev`n"
    Invoke-FixtureGit $f.Dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $f.Dev @('commit', '-m', '开发改参数') | Out-Null
    Write-FixtureFile (Join-Path $f.Run 'params\mapping.yaml') "base: run`n"
    $devHeadBefore = Get-FixtureHead $f.Dev
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '合并冲突' '冲突'
    Assert ((Get-FixtureHead $f.Dev) -eq $devHeadBefore) '合并冲突：开发 HEAD 被改动'
    Assert (Test-NoMergeInProgress $f.Dev) '合并冲突：未执行 merge --abort'
    Assert ($TestState.NpmCalls.Count -eq 0) '合并冲突：仍执行了构建'
    $Passed += 1; Write-Host '通过：合并冲突停止并 abort'

    # 6 非快进停止（lab 有未合并提交且工作区干净）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-6'
    $f = New-Fixture 'nonff'
    Write-FixtureFile (Join-Path $f.Run 'params\mapping.yaml') "base: lab-only`n"
    Invoke-FixtureGit $f.Run @('add', '-A') | Out-Null
    Invoke-FixtureGit $f.Run @('commit', '-m', 'lab 独立提交') | Out-Null
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '非快进' '不能快进'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '非快进：运行目录 HEAD 被改动'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) '非快进：不应打新标签'
    Assert ((Get-DistContent $f.Run) -eq 'OLD BUILD') '非快进：dist 被改动'
    $Passed += 1; Write-Host '通过：非快进停止'

    # 7 依赖变化停止，-Resume 续跑（标签复用，幂等）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-7'
    $f = New-Fixture 'deps'
    Write-FixtureFile (Join-Path $f.Dev 'pyproject.toml') "[project]`nname = 'fixture'`nversion = '0.2.0'`n"
    Invoke-FixtureGit $f.Dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $f.Dev @('commit', '-m', '升级依赖') | Out-Null
    $runHeadBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Answers @('y', 'y')
    Assert-CaseFailed $r '依赖变化' 'pip install'
    Assert ($r.Output -match 'npm ci') '依赖变化：缺少 npm ci 提示'
    Assert ($r.Output -match '-Resume') '依赖变化：缺少 -Resume 提示'
    Assert ($TestState.HeadAtStop -eq $runHeadBefore) '依赖变化：停 GUI 之前代码已被改动'
    Assert ((Get-FixtureHead $f.Run) -eq (Get-FixtureHead $f.Dev)) '依赖变化：GUI 停止后未更新代码（安装依赖需要新版本清单）'
    Assert ((Get-DistContent $f.Run) -eq 'OLD BUILD') '依赖变化：不应构建'
    Assert ($TestState.StopCalls.Count -eq 1 -and $TestState.StartCalls.Count -eq 0) '依赖变化：应停 GUI 且不启动'
    $tagsAfterStop = Get-FixtureTags $f.Run
    $r2 = Invoke-ReleaseCase $f -Options @{ Resume = $true }
    Assert-CaseOk $r2 '依赖 -Resume 续跑'
    Assert ($r2.Output -match '复用') '依赖 -Resume：未复用已有标签'
    Assert ((Get-FixtureTags $f.Run).Count -eq $tagsAfterStop.Count) '依赖 -Resume：重复打了标签'
    Assert ((Get-DistContent $f.Run) -eq 'NEW BUILD') '依赖 -Resume：dist 未更新'
    Assert ($TestState.StartCalls.Count -eq 1) '依赖 -Resume：未启动 GUI'
    $Passed += 1; Write-Host '通过：依赖变化停止 + -Resume 续跑'

    # 7b 依赖变化未确认 → 什么都不动
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-7b'
    $f = New-Fixture 'deps-no'
    Write-FixtureFile (Join-Path $f.Dev 'constraints\development.txt') "numpy==2`n"
    Invoke-FixtureGit $f.Dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $f.Dev @('commit', '-m', '改约束') | Out-Null
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Answers @('y', 'n')
    Assert-CaseFailed $r '依赖未确认' '未确认依赖'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '依赖未确认：运行目录被改动'
    Assert ($TestState.StopCalls.Count -eq 0) '依赖未确认：不应停 GUI'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) '依赖未确认：不应打标签'
    $Passed += 1; Write-Host '通过：依赖变化未确认不改动'

    # 8 构建失败保留旧 dist
    $TestState.Case = @{ BuildFailure = $true }
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-8'
    $f = New-Fixture 'buildfail'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '构建失败' '构建失败'
    Assert ($r.Output -match '已退回原版本') '构建失败：未说明已退回'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '构建失败：运行目录代码未退回原版本'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) '构建失败：本次新建的标签未删除'
    Assert ((Get-DistContent $f.Run) -eq 'OLD BUILD') '构建失败：旧 dist 未保留'
    Assert (-not (Test-Path -LiteralPath (Join-Path $f.Run 'GUI\frontend\dist.prev'))) '构建失败：残留 dist.prev'
    Assert ($TestState.StopCalls.Count -eq 1 -and $TestState.StartCalls.Count -eq 1) '构建失败：应按原版本重新启动 GUI'
    $Passed += 1; Write-Host '通过：构建失败保留旧 dist'

    # 9 回滚（y 确认）：先正常发布，再回滚到初始标签
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-9'
    $f = New-Fixture 'rollback'
    $r = Invoke-ReleaseCase $f
    Assert-CaseOk $r '回滚前置发布'
    $tagCommit = Invoke-FixtureGit $f.Run @('rev-parse', 'lab-20260101-1')
    $r2 = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-20260101-1' }
    Assert-CaseOk $r2 '回滚'
    Assert ($r2.Output -match '将丢弃以下提交') '回滚：未打印将丢弃的提交'
    Assert ($r2.Output -match '新功能') '回滚：丢弃清单缺少提交'
    Assert ((Get-FixtureHead $f.Run) -eq $tagCommit) '回滚：HEAD 未回到标签'
    Assert ((Get-DistContent $f.Run) -eq 'NEW BUILD') '回滚：未重建前端'
    Assert ($TestState.StopCalls.Count -eq 1 -and $TestState.StartCalls.Count -eq 1) '回滚：未重启 GUI'
    $Passed += 1; Write-Host '通过：回滚（确认 y）'

    # 10 回滚拒绝（N）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-10'
    $f = New-Fixture 'rollback-no'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-20260101-1' } -Answers @('n')
    Assert-CaseFailed $r '回滚拒绝' '未确认'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '回滚拒绝：HEAD 被改动'
    Assert ($TestState.NpmCalls.Count -eq 0) '回滚拒绝：仍执行了构建'
    $Passed += 1; Write-Host '通过：回滚拒绝（N）'

    # 11 回滚时运行目录有未提交改动 → 停止
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-11'
    $f = New-Fixture 'rollback-dirty'
    Write-FixtureFile (Join-Path $f.Run 'params\mapping.yaml') "base: dirty`n"
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-20260101-1' }
    Assert-CaseFailed $r '回滚脏目录' '未提交改动'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '回滚脏目录：HEAD 被改动'
    Assert ($TestState.Prompts.Count -eq 0) '回滚脏目录：不应进入确认'
    $Passed += 1; Write-Host '通过：回滚时脏目录停止'

    # 12 回滚标签不存在
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-12'
    $f = New-Fixture 'rollback-badtag'
    $r = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-19990101-9' }
    Assert-CaseFailed $r '回滚坏标签' '不存在标签'
    $Passed += 1; Write-Host '通过：回滚标签不存在'

    # 12b 回滚跨越依赖变化：回退代码后停下等安装依赖，重跑同一命令完成
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-12b'
    $f = New-Fixture 'rollback-deps'
    Write-FixtureFile (Join-Path $f.Dev 'GUI\frontend\package-lock.json') '{"lockfileVersion":3,"v":2}'
    Invoke-FixtureGit $f.Dev @('add', '-A') | Out-Null
    Invoke-FixtureGit $f.Dev @('commit', '-m', '前端依赖升级') | Out-Null
    $r = Invoke-ReleaseCase $f -Answers @('y', 'y')
    Assert-CaseFailed $r '回滚依赖前置发布' '-Resume'
    $r = Invoke-ReleaseCase $f -Options @{ Resume = $true }
    Assert-CaseOk $r '回滚依赖前置发布 -Resume'
    $tagCommit = Invoke-FixtureGit $f.Run @('rev-parse', 'lab-20260101-1')
    $r2 = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-20260101-1' }
    Assert-CaseFailed $r2 '回滚跨依赖' '重跑同一回滚命令'
    Assert ($r2.Output -match '回滚跨越依赖清单变化') '回滚跨依赖：确认前未提示依赖变化'
    Assert ((Get-FixtureHead $f.Run) -eq $tagCommit) '回滚跨依赖：代码未回退'
    Assert ($TestState.StopCalls.Count -eq 1 -and $TestState.StartCalls.Count -eq 0) '回滚跨依赖：应停 GUI 且不启动'
    $r3 = Invoke-ReleaseCase $f -Options @{ Rollback = 'lab-20260101-1' }
    Assert-CaseOk $r3 '回滚跨依赖重跑'
    Assert ($TestState.StartCalls.Count -eq 1) '回滚跨依赖重跑：未启动 GUI'
    $Passed += 1; Write-Host '通过：回滚跨越依赖变化分两步完成'

    # 13 DryRun：只读预检，不做任何改动
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-13'
    $f = New-Fixture 'dryrun'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Options @{ DryRun = $true }
    Assert-CaseOk $r 'DryRun'
    Assert ($r.Output -match '发布计划') 'DryRun：未打印计划'
    Assert ($r.Output -match '预检通过') 'DryRun：未报告预检结果'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) 'DryRun：运行目录被改动'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) 'DryRun：打了标签'
    Assert ((Get-DistContent $f.Run) -eq 'OLD BUILD') 'DryRun：dist 被改动'
    Assert ($TestState.NpmCalls.Count -eq 0 -and $TestState.StopCalls.Count -eq 0 -and $TestState.StartCalls.Count -eq 0) 'DryRun：执行了构建/重启'
    Assert ($TestState.Prompts.Count -eq 0) 'DryRun：不应要求确认'
    $Passed += 1; Write-Host '通过：DryRun 只读预检'

    # 14 DryRun 遇忙 → 预检阻塞
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-14'
    $TestState.Health.hardware_busy = $true
    $f = New-Fixture 'dryrun-busy'
    $r = Invoke-ReleaseCase $f -Options @{ DryRun = $true }
    Assert-CaseFailed $r 'DryRun 忙' '预检阻塞'
    $Passed += 1; Write-Host '通过：DryRun 忙时预检阻塞'

    # 15 SkipRestart：更新但不重启
    $TestState.Case = @{}
    $TestState.HealthDown = $true
    $TestState.Health = $null
    $f = New-Fixture 'skiprestart'
    $r = Invoke-ReleaseCase $f -Options @{ SkipRestart = $true }
    Assert-CaseOk $r 'SkipRestart'
    Assert ((Get-DistContent $f.Run) -eq 'NEW BUILD') 'SkipRestart：dist 未更新'
    Assert ($TestState.StopCalls.Count -eq 0 -and $TestState.StartCalls.Count -eq 0) 'SkipRestart：仍停止/启动了 GUI'
    $Passed += 1; Write-Host '通过：SkipRestart（GUI 未运行）'

    # 15b SkipRestart 而 GUI 在运行 → 拒绝，不改动
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-15b'
    $f = New-Fixture 'skiprestart-running'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Options @{ SkipRestart = $true }
    Assert-CaseFailed $r 'SkipRestart 运行中' 'SkipRestart 只能在 GUI 未运行时使用'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) 'SkipRestart 运行中：运行目录被改动'
    Assert ($TestState.NpmCalls.Count -eq 0 -and $TestState.StopCalls.Count -eq 0) 'SkipRestart 运行中：仍执行了构建/停止'
    $Passed += 1; Write-Host '通过：SkipRestart 遇 GUI 运行拒绝'

    # 15c 首次检查空闲，停 GUI 前变忙（用户刚开始实验）→ 拒绝，不停 GUI、不改代码
    $TestState.Case = @{ BusyAfterCalls = 1 }
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-15c'
    $f = New-Fixture 'busy-before-stop'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '停前变忙' '硬件任务忙'
    Assert ($TestState.StopCalls.Count -eq 0) '停前变忙：仍停止了 GUI（会取消用户实验）'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '停前变忙：运行目录被改动'
    Assert ((Get-FixtureTags $f.Run).Count -eq 1) '停前变忙：不应打标签'
    Assert ($TestState.NpmCalls.Count -eq 0) '停前变忙：仍执行了构建'
    $Passed += 1; Write-Host '通过：停 GUI 前再次检查发现忙'

    # 16 运行目录有 params/ 以外的改动 → 停止并列出
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-16'
    $f = New-Fixture 'other-dirty'
    Write-FixtureFile (Join-Path $f.Run 'notes.txt') '手改代码'
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '非 params 改动' 'notes\.txt'
    Assert ($TestState.NpmCalls.Count -eq 0) '非 params 改动：仍执行了构建'
    $Passed += 1; Write-Host '通过：非 params 改动停止'

    # 17 开发目录有已暂存改动 → 停止
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-17'
    $f = New-Fixture 'dev-staged'
    Write-FixtureFile (Join-Path $f.Dev 'GUI\frontend\src\app.txt') "staged`n"
    Invoke-FixtureGit $f.Dev @('add', 'GUI/frontend/src/app.txt') | Out-Null
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '开发暂存' '已暂存'
    $Passed += 1; Write-Host '通过：开发目录暂存改动停止'

    # 18 开发目录不在 master → 停止
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-18'
    $f = New-Fixture 'dev-branch'
    Invoke-FixtureGit $f.Dev @('switch', '-c', 'feature') | Out-Null
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '开发分支' 'master'
    $Passed += 1; Write-Host '通过：开发目录非 master 停止'

    # 19 GUI 未运行（连接被拒）视为空闲，发布后启动成功
    $TestState.Case = @{}
    $TestState.HealthDown = $true
    $TestState.Health = $null
    $f = New-Fixture 'gui-down'
    $r = Invoke-ReleaseCase $f
    Assert-CaseOk $r 'GUI 未运行'
    Assert ($r.Output -match '视为空闲') 'GUI 未运行：未识别为空闲'
    Assert ($TestState.StartCalls.Count -eq 1) 'GUI 未运行：未启动 GUI'
    Assert ($TestState.StopCalls.Count -eq 1) 'GUI 未运行：未先执行停止入口'
    $Passed += 1; Write-Host '通过：GUI 未运行视为空闲'

    # 20 重启后 instance_id 不变 → 超时失败
    $TestState.Case = @{ RestartStuck = $true }
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-stuck'
    $f = New-Fixture 'restart-stuck'
    $r = Invoke-ReleaseCase $f -Options @{ HealthTimeoutSec = 3 }
    Assert-CaseFailed $r '重启超时' '超时'
    Assert ($TestState.HealthCalls -ge 3) '重启超时：轮询次数不足'
    $Passed += 1; Write-Host '通过：重启超时失败'

    # 21 stop_gui.bat 失败 → 停止，不启动
    $TestState.Case = @{ StopFailure = $true }
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-21'
    $f = New-Fixture 'stop-fail'
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '停止失败' 'stop_gui\.bat 失败'
    Assert ($TestState.StartCalls.Count -eq 0) '停止失败：仍启动了 GUI'
    Assert ((Get-FixtureHead $f.Run) -ne (Get-FixtureHead $f.Dev)) '停止失败：运行目录代码已被改动'
    Assert ((Get-DistContent $f.Run) -eq 'OLD BUILD') '停止失败：dist 被改动'
    $Passed += 1; Write-Host '通过：stop_gui.bat 失败停止'

    # 22 标签当日序号按数值递增（N>=10 时字典序错误）
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-22'
    $f = New-Fixture 'tag-seq'
    Invoke-FixtureGit $f.Run @('tag', "lab-$Today-9") | Out-Null
    Invoke-FixtureGit $f.Run @('tag', "lab-$Today-10") | Out-Null
    $r = Invoke-ReleaseCase $f
    Assert-CaseOk $r '标签序号'
    $tags = Get-FixtureTags $f.Run
    Assert ($tags -contains "lab-$Today-11") "标签序号：未生成 lab-$Today-11，实际：$($tags -join ', ')"
    Assert ($tags -notcontains "lab-$Today-2") '标签序号：错误地按字典序生成了 -2'
    $Passed += 1; Write-Host '通过：标签序号数值递增'

    # 23 运行目录不存在 → 停止
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-23'
    $f = New-Fixture 'no-rundir'
    $r = Invoke-ReleaseCase $f -Options @{ RunDir = (Join-Path $f.Root 'missing') }
    Assert-CaseFailed $r '运行目录缺失' '运行目录不存在'
    $Passed += 1; Write-Host '通过：运行目录不存在停止'

    # 24 未确认测试通过（N）→ 取消
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-24'
    $f = New-Fixture 'no-confirm'
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f -Answers @('n')
    Assert-CaseFailed $r '未确认测试' '未确认'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '未确认测试：运行目录被改动'
    $Passed += 1; Write-Host '通过：未确认测试取消'

    # 25 运行目录缺少或带错标记 .lab_runtime → 发布与 DryRun 都停止，不改动
    $TestState.Case = @{}
    $TestState.HealthDown = $false
    $TestState.Health = New-HealthObject 'pre-25'
    $f = New-Fixture 'no-marker'
    Remove-Item -LiteralPath (Join-Path $f.Run '.lab_runtime') -Force
    $headBefore = Get-FixtureHead $f.Run
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '缺少运行标记' '缺少标记 \.lab_runtime'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '缺少运行标记：运行目录被改动'
    Assert ($TestState.NpmCalls.Count -eq 0 -and $TestState.StopCalls.Count -eq 0) '缺少运行标记：仍执行了构建/停止'
    $r = Invoke-ReleaseCase $f -Options @{ DryRun = $true }
    Assert-CaseFailed $r '缺少运行标记 DryRun' '预检阻塞：运行目录缺少标记'
    Write-FixtureFile (Join-Path $f.Run '.lab_runtime') $f.Dev
    $r = Invoke-ReleaseCase $f
    Assert-CaseFailed $r '标记路径不符' '与运行目录不符'
    Assert ((Get-FixtureHead $f.Run) -eq $headBefore) '标记路径不符：运行目录被改动'
    $Passed += 1; Write-Host '通过：运行目录标记缺失或不符停止'

    Write-Host "全部 $Passed 项发布演练通过；未连接真实 8000、未启动 GUI、未触碰真实运行目录。"
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
} catch {
    Write-Host "演练失败：$($_.Exception.Message)"
    Write-Host "临时仓库保留在：$TestRoot"
    exit 1
}
exit 0