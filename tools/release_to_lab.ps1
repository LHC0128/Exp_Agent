# 发布脚本：把开发目录的已测试提交快进发布到运行目录。
# 正式模式从 master 发布到运行目录 lab 分支；迭代模式（-Iterate）从 exp/* 实验分支发布到
# 运行目录的分离 HEAD，lab 分支不动，标签为 lab-wip-*，供协同实验会话反复上机。
# 只在运行目录空闲时发布；只允许快进；依赖变化时停下等用户授权手动安装。
# 顺序原则：能提前做的检查都在停 GUI 之前完成；停 GUI 前再查一次空闲；
# 运行目录代码只在 GUI 停止后才改动，构建失败自动退回原版本并按原状态启动。
# 参数回存只在开发目录产生一条提交（正式落 master，迭代落当前实验分支），运行目录不提交、不合并。
#
# 用法（在开发目录执行）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\release_to_lab.ps1
#   可选参数：
#     -RunDir <路径>        运行目录，默认 D:\Code\exp_agent_run
#     -DevDir <路径>        开发目录，默认脚本所在仓库
#     -Iterate              迭代发布：开发目录在 exp/* 分支，运行目录更新到分离 HEAD，打 lab-wip-* 标签；
#                           相对运行目录当前版本只改了 scripts/ 时不停/启 GUI、不重建前端
#     -BackfillOnly         与 -Iterate 同用：只把运行目录 params/ 回存到实验分支，不动代码与 GUI
#     -Confirm              迭代发布时仍确认「已通过测试」（正式模式总是确认）
#     -SkipRestart          只更新代码和前端，不停止/启动 GUI；仅在 GUI 未运行时可用
#     -Rollback <标签>      回滚到某个 lab-* 标签（回到 lab 分支）或 lab-wip-* 标签（分离 HEAD）
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
    [switch]$Iterate,
    [switch]$BackfillOnly,
    [switch]$Confirm,
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
$ModeName = if ($BackfillOnly) { '仅回存' } elseif ($Iterate) { '迭代' } else { '正式' }
$LogPrefix = if ($Iterate) { '[迭代] ' } else { '' }

function Resolve-FullPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
}

function Write-ReleaseLog([string]$Message) {
    $line = '[{0}] {1}{2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LogPrefix, $Message
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
# -Wip 只看 lab-wip-YYYYMMDD-N：迭代与正式两套序号各自递增，互不影响。
function Get-LabTagsSorted([string]$RepoDir, [switch]$Wip) {
    $pattern = if ($Wip) { '^lab-wip-(\d{8})-(\d+)$' } else { '^lab-(\d{8})-(\d+)$' }
    $result = Invoke-Git $RepoDir @('tag', '--list', 'lab-*')
    $tags = @()
    foreach ($line in ($result.Text -split "`n")) {
        $name = $line.Trim()
        if ($name -match $pattern) {
            $tags += [pscustomobject]@{ Name = $name; Day = $Matches[1]; Seq = [int]$Matches[2] }
        }
    }
    # 逗号包裹：单元素数组经 return 会被解包成 PSCustomObject（无 .Count），调用方 .Count 判断会失效。
    return ,@($tags | Sort-Object Day, Seq)
}

# 某个提交上的发布标签（正式或 wip）；分离 HEAD 靠它判断运行目录停在哪个已发布版本。
function Get-ReleaseTagsAt([string]$RepoDir, [string]$Rev, [switch]$Wip) {
    $pattern = if ($Wip) { '^lab-wip-\d{8}-\d+$' } else { '^lab-\d{8}-\d+$' }
    $result = Invoke-Git $RepoDir @('tag', '--points-at', $Rev)
    return @($result.Text -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match $pattern })
}

# 本次发布要用的标签：目标提交已有同类标签则复用（幂等），否则按当日序号新建。
function Resolve-ReleaseTag([string]$RepoDir, [string]$TargetHead, [switch]$Wip) {
    $tags = Get-LabTagsSorted $RepoDir -Wip:$Wip
    $targetTags = @(Get-ReleaseTagsAt $RepoDir $TargetHead -Wip:$Wip)
    if ($targetTags.Count -gt 0) {
        $name = $targetTags[0]
        $index = -1
        for ($i = 0; $i -lt $tags.Count; $i++) { if ($tags[$i].Name -eq $name) { $index = $i } }
        Write-ReleaseLog "目标提交已有标签 $name，复用（不新建）。"
        return [pscustomobject]@{
            Name = $name
            PrevTag = if ($index -gt 0) { $tags[$index - 1].Name } else { $null }
            Create = $false
        }
    }
    $today = Get-Date -Format 'yyyyMMdd'
    $seq = 1
    foreach ($t in $tags) { if ($t.Day -eq $today -and $t.Seq -ge $seq) { $seq = $t.Seq + 1 } }
    $prefix = if ($Wip) { 'lab-wip-' } else { 'lab-' }
    return [pscustomobject]@{
        Name = "$prefix$today-$seq"
        PrevTag = if ($tags.Count -gt 0) { $tags[-1].Name } else { $null }
        Create = $true
    }
}

function Get-RepoStatusLines([string]$RepoDir) {
    $result = Invoke-Git $RepoDir @('-c', 'core.quotepath=false', 'status', '--porcelain')
    if (-not $result.Text.Trim()) { return @() }
    return @($result.Text -split "`n" | Where-Object { $_ })
}

# porcelain 行分类：params/ 内（含未跟踪）与其他改动分开。本机覆盖层既不是基线改动、
# 也不是「其他改动」，单独排除，避免被回存或触发「非 params 改动」停止。
function Split-StatusByParams([string[]]$Lines) {
    $params = @()
    $other = @()
    foreach ($line in $Lines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ')[-1] }
        $path = $path.Trim('"')
        if ($path -match '\.local\.yaml$') { continue }
        if ($path -like 'params/*' -or $path -eq 'params') { $params += $line } else { $other += $line }
    }
    return @{ Params = $params; Other = $other }
}

# 本机覆盖层 <id>.local.yaml 只在本机生效：不能进 git（跟踪后快进会冲突、回存会把它当基线改动搬走），
# 也不能被收尾清理删掉。
function Get-TrackedOverrides([string]$RepoDir) {
    $listing = Invoke-Git $RepoDir @('-c', 'core.quotepath=false', 'ls-files', '--', 'params/')
    return @($listing.Text -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '\.local\.yaml$' })
}

function Assert-NoTrackedOverrides([string]$RepoDir, [string]$Label) {
    $tracked = @(Get-TrackedOverrides $RepoDir)
    if ($tracked.Count -gt 0) {
        Stop-Release ("{0}有被 git 跟踪的本机覆盖层，已停止；覆盖层只在本机生效，请先 git rm --cached 再发布：`n{1}" -f $Label, ($tracked -join "`n"))
    }
}

# 覆盖层内容指纹：回存前后必须完全一致。
function Get-OverrideFingerprint([string]$RepoDir) {
    $root = Join-Path $RepoDir 'params'
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    return @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.local.yaml' | Sort-Object FullName | ForEach-Object {
        "$($_.FullName)|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
    })
}

# 参数回存：把运行目录 params/ 的改动逐文件带回开发目录，在当前分支（正式 master / 迭代 exp/*）上提交一条。
# 运行目录不提交、不合并，HEAD 保持不变。按文件搬运，二进制内容原样保留。
function Restore-RunParamsIntoDev([string]$DevDirPath, [string]$RunDirPath, [int]$ChangeCount) {
    $today = Get-Date -Format 'yyyy-MM-dd'
    $devBranch = Get-GitBranch $DevDirPath

    # 覆盖层不属于基线改动：既不回存，也不得在收尾清理时被删掉。
    Assert-NoTrackedOverrides $RunDirPath '运行目录'
    Assert-NoTrackedOverrides $DevDirPath '开发目录'
    $overridesBefore = @(Get-OverrideFingerprint $RunDirPath)
    # 开发目录 params/ 必须先干净，否则无法区分哪些改动来自运行目录。
    $devParams = Invoke-Git $DevDirPath @('-c', 'core.quotepath=false', 'status', '--porcelain', '--', 'params/')
    if ($devParams.Text.Trim()) {
        Stop-Release ("开发目录 params/ 有未提交改动，无法回存；请先提交或还原：`n" + $devParams.Text.Trim())
    }

    # 冲突判定需要共同基线：运行目录当前版本必须已在开发目录当前分支里（分离 HEAD 同样按提交判定）。
    $runHead = Get-GitHead $RunDirPath
    $devHead = Get-GitHead $DevDirPath
    if ((Invoke-Git $DevDirPath @('merge-base', '--is-ancestor', $runHead, $devHead) -AllowFail).Code -ne 0) {
        Stop-Release "开发目录 $devBranch 不包含运行目录当前版本，无法判断参数回存冲突；请先把运行目录版本同步回 $devBranch。"
    }

    # 以运行目录为准暂存 params/，取相对 HEAD 的逐文件改动；重命名拆成删除 + 新增，便于逐个搬运。
    # 本机覆盖层永远不回存：显式排除，避免它被当成基线改动带进开发目录。
    Invoke-Git $RunDirPath @('add', '-A', '--', 'params/', ':(exclude)**/*.local.yaml') | Out-Null
    $nameStatus = Invoke-Git $RunDirPath @('-c', 'core.quotepath=false', 'diff', '--cached', '--name-status', '--no-renames', '--', 'params/')
    $changed = @()
    foreach ($line in ($nameStatus.Text -split "`n")) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split "`t"
        if ($parts.Count -lt 2 -or -not $parts[1]) { throw "无法解析 git diff --name-status 输出：$line" }
        $changed += [pscustomobject]@{ Status = $parts[0].Substring(0, 1); Path = $parts[1] }
    }

    # 开发目录自上一版本以来也改过同一参数文件时直接覆盖会静默丢改动，停下交人工比对。
    if ($changed.Count -gt 0) {
        $devChanged = Invoke-Git $DevDirPath @('-c', 'core.quotepath=false', 'diff', '--name-only', '--no-renames', $runHead, $devHead, '--', 'params/')
        $devChangedSet = @($devChanged.Text -split "`n" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
        $overlap = @($changed | Where-Object { $devChangedSet -contains $_.Path } | ForEach-Object { $_.Path })
        if ($overlap.Count -gt 0) {
            Invoke-Git $RunDirPath @('reset', '-q', 'HEAD', '--', 'params/') -AllowFail | Out-Null
            Stop-Release ("运行目录与开发目录都改过同一参数文件，已停止（运行目录改动已放回未暂存状态）；请人工比对后再发布：`n" + ($overlap -join "`n"))
        }
    }

    if ($changed.Count -eq 0) {
        # 只有本机覆盖层变化时没有可回存的基线改动：清掉暂存状态，直接结束。
        Invoke-Git $RunDirPath @('reset', '-q', 'HEAD', '--', 'params/') -AllowFail | Out-Null
        Write-ReleaseLog '运行目录只有本机覆盖层变化，无需回存。'
        return
    }

    foreach ($item in $changed) {
        $src = Join-Path $RunDirPath $item.Path
        $dst = Join-Path $DevDirPath $item.Path
        if ($item.Status -eq 'D') {
            if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
            continue
        }
        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    Invoke-Git $DevDirPath @('add', '-A', '--', 'params/') | Out-Null

    # 逐文件搬运可能漏删除或空目录，提交前用 params 子树哈希确认两侧完全一致。
    $runTree = (Invoke-Git $RunDirPath @('write-tree')).Text.Trim()
    $devTree = (Invoke-Git $DevDirPath @('write-tree')).Text.Trim()
    $runParamsTree = (Invoke-Git $RunDirPath @('rev-parse', ($runTree + ':params'))).Text.Trim()
    $devParamsTree = (Invoke-Git $DevDirPath @('rev-parse', ($devTree + ':params'))).Text.Trim()
    if ($runParamsTree -ne $devParamsTree) {
        Invoke-Git $DevDirPath @('reset', '-q', 'HEAD', '--', 'params/') -AllowFail | Out-Null
        Invoke-Git $DevDirPath @('checkout', '--', 'params/') -AllowFail | Out-Null
        Stop-Release '参数回存校验失败：开发目录与运行目录的 params/ 内容不一致，已还原开发目录；请人工比对。'
    }

    Invoke-Git $DevDirPath @('commit', '-m', "运行参数回存 $today") | Out-Null
    Write-ReleaseLog "参数回存已提交到开发目录 $devBranch（$ChangeCount 项改动，无合并提交）。"

    # 改动已转移：丢弃运行目录的 params/ 改动，HEAD 不变，后续发布仍是纯快进。
    # -e '*.local.yaml' 显式保留覆盖层：即使它没被 .gitignore 忽略，也不能在这里被删。
    Invoke-Git $RunDirPath @('reset', '-q', 'HEAD', '--', 'params/') | Out-Null
    Invoke-Git $RunDirPath @('checkout', '--', 'params/') | Out-Null
    Invoke-Git $RunDirPath @('clean', '-fdq', '-e', '*.local.yaml', '--', 'params/') | Out-Null
    $overridesAfter = @(Get-OverrideFingerprint $RunDirPath)
    if (($overridesAfter -join "`n") -ne ($overridesBefore -join "`n")) {
        Stop-Release ('参数回存收尾改动了运行目录的本机覆盖层（回存前 {0} 个，回存后 {1} 个）已停止；' +
            '开发目录的回存提交已保留，请先核对 params/experiments/*.local.yaml 再发布。' -f $overridesBefore.Count, $overridesAfter.Count)
    }
    Write-ReleaseLog '运行目录 params/ 改动已丢弃（内容已在开发目录提交）。'
}

# 运行目录标记 .lab_runtime（内容为运行目录绝对路径）是驱动层放行真实仪器的唯一依据；
# 缺失或不符时发布后 GUI 连不上仪器，所以发布前拦截。返回问题描述，正常返回 $null。
function Get-RuntimeMarkerProblem([string]$RunDirPath) {
    $marker = Join-Path $RunDirPath '.lab_runtime'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        return "运行目录缺少标记 .lab_runtime（发布后将无法连接仪器）"
    }
    $recorded = (Get-Content -LiteralPath $marker -Raw -Encoding UTF8).Trim()
    if ((Resolve-FullPath $recorded) -ne (Resolve-FullPath $RunDirPath)) {
        return "运行目录标记 .lab_runtime 记录的路径 $recorded 与运行目录不符"
    }
    return $null
}

# 运行目录版本状态：在 lab 分支，或分离 HEAD 且 HEAD 恰好是某个 lab-* / lab-wip-* 标签。
function Get-RunDirVersion([string]$RunDirPath) {
    $branch = Get-GitBranch $RunDirPath
    $head = Get-GitHead $RunDirPath
    $detached = ($branch -eq 'HEAD')
    $tags = @(Get-ReleaseTagsAt $RunDirPath $head) + @(Get-ReleaseTagsAt $RunDirPath $head -Wip)
    return [pscustomobject]@{ Branch = $branch; Head = $head; Detached = $detached; Tags = $tags }
}

function Get-RunDirVersionText($State) {
    if ($State.Detached) {
        return "分离 HEAD $(Get-ShortRev $State.Head)（标签 $($State.Tags -join ', ')）"
    }
    return "分支 $($State.Branch) $(Get-ShortRev $State.Head)"
}

function Assert-RunDirVersion([string]$RunDirPath) {
    $state = Get-RunDirVersion $RunDirPath
    if ($state.Detached) {
        if ($state.Tags.Count -eq 0) {
            Stop-Release ("运行目录处于分离 HEAD $(Get-ShortRev $state.Head)，不在任何 lab-* / lab-wip-* 标签上；" +
                '请先人工切回 lab 分支或某个发布标签。')
        }
    } elseif ($state.Branch -ne 'lab') {
        Stop-Release "运行目录当前分支是 $($state.Branch)，应为 lab 或分离于发布标签；请先人工切回。"
    }
    $markerProblem = Get-RuntimeMarkerProblem $RunDirPath
    if ($markerProblem) { Stop-Release "$markerProblem；请先按 T9 设计创建标记。" }
    Assert-NoTrackedOverrides $RunDirPath '运行目录'
    return $state
}

# 两个提交之间依赖清单的变化（文件名列表文本）；无变化返回空串。
function Get-DependencyChanges([string]$RepoDir, [string]$From, [string]$To) {
    $diff = Invoke-Git $RepoDir (@('diff', '--name-only', $From, $To, '--') + $DependencyPaths)
    return $diff.Text.Trim()
}

function Get-DependencyInstallHint {
    return ('需用户授权后在运行目录手动执行：' +
        "agent_exp_env\Scripts\python.exe -m pip install -c constraints/win-py312.lock.txt -e `".[runtime,instruments,analysis,gui]`"；" +
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

# 构建失败：运行目录退回到发布前的状态（旧 dist 未动，与旧代码一致），删除本次新建的标签，
# 未指定 -SkipRestart 时按原版本启动 GUI，最后以失败结束。
# -Detached 表示发布前是分离 HEAD：直接 checkout --detach 回去，不动 lab 分支；
# 否则发布前在 lab 分支，checkout lab 后 reset --hard 回原提交。
function Restore-AfterBuildFailure([string]$RunDirPath, [string]$Reason, [string]$RestoreRef, [string]$CreatedTag, [string]$PreInstanceId, [switch]$Detached) {
    Write-ReleaseLog $Reason
    if ($RestoreRef) {
        if ($Detached) {
            Invoke-Git $RunDirPath @('checkout', '--detach', $RestoreRef) | Out-Null
        } else {
            Invoke-Git $RunDirPath @('checkout', 'lab') | Out-Null
            Invoke-Git $RunDirPath @('reset', '--hard', $RestoreRef) | Out-Null
        }
        Write-ReleaseLog "运行目录已退回 $(Get-ShortRev $RestoreRef)$(if ($Detached) { '（分离 HEAD）' } else { '（lab 分支）' })。"
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
    Write-ReleaseLog "===== DryRun：发布计划（模式：$ModeName；只读预检，不做任何改动） ====="
    $blockers = @()

    $devBranch = Get-GitBranch $DevDirPath
    $devHead = Get-GitHead $DevDirPath
    Write-ReleaseLog "开发目录：$DevDirPath（分支 $devBranch，HEAD $(Get-ShortRev $devHead)）"
    if ($Iterate) {
        if ($devBranch -notmatch '^exp/') { $blockers += "开发目录分支是 $devBranch，迭代发布要求 exp/*" }
    } elseif ($devBranch -ne 'master') {
        $blockers += "开发目录分支是 $devBranch，应为 master"
    }
    $staged = Invoke-Git $DevDirPath @('diff', '--cached', '--quiet') -AllowFail
    if ($staged.Code -ne 0) { $blockers += '开发目录有已暂存改动' }
    if (@(Get-TrackedOverrides $DevDirPath).Count -gt 0) { $blockers += '开发目录有被跟踪的本机覆盖层' }

    if (-not (Test-Path -LiteralPath $RunDirPath)) {
        Write-ReleaseLog "运行目录不存在：$RunDirPath（需先完成 T4 建运行目录）"
        $blockers += '运行目录不存在'
    } else {
        $runState = Get-RunDirVersion $RunDirPath
        $tags = Get-LabTagsSorted $RunDirPath
        $wipTags = Get-LabTagsSorted $RunDirPath -Wip
        $latestTag = if ($tags.Count -gt 0) { $tags[-1].Name } else { '（无）' }
        $latestWip = if ($wipTags.Count -gt 0) { $wipTags[-1].Name } else { '（无）' }
        Write-ReleaseLog "运行目录：$RunDirPath（$(Get-RunDirVersionText $runState)，最新正式标签 $latestTag，最新迭代标签 $latestWip）"
        if ($runState.Detached) {
            if ($runState.Tags.Count -eq 0) { $blockers += '运行目录分离 HEAD 不在任何 lab-* / lab-wip-* 标签上' }
        } elseif ($runState.Branch -ne 'lab') {
            $blockers += "运行目录分支是 $($runState.Branch)，应为 lab 或分离于发布标签"
        }
        $markerProblem = Get-RuntimeMarkerProblem $RunDirPath
        if ($markerProblem) { $blockers += $markerProblem }

        $status = Split-StatusByParams (Get-RepoStatusLines $RunDirPath)
        if ($status.Other.Count -gt 0) { $blockers += '运行目录有 params/ 以外的未提交改动' }
        if (@(Get-TrackedOverrides $RunDirPath).Count -gt 0) { $blockers += '运行目录有被跟踪的本机覆盖层' }
        $wipTagHere = @($runState.Tags | Where-Object { $_ -match '^lab-wip-' })
        if (-not $Iterate -and $runState.Detached -and $wipTagHere.Count -gt 0) {
            Write-ReleaseLog "运行目录在 $($wipTagHere -join ', ')；最后一次回存之后的 params/ 改动将阻止发布。"
        }
        if ($status.Params.Count -gt 0) {
            if (-not $Iterate -and $runState.Detached) {
                $blockers += "运行目录分离于迭代版本且 params/ 有 $($status.Params.Count) 项改动：先跑 -Iterate -BackfillOnly 回存并把回存提交整理进 master"
            } elseif ($BackfillOnly) {
                Write-ReleaseLog "将把运行目录 params/ 的 $($status.Params.Count) 项改动回存为开发目录 $devBranch 上的一条提交；不动代码与 GUI。"
            } elseif ($Iterate) {
                Write-ReleaseLog "将把运行目录 params/ 的 $($status.Params.Count) 项改动回存到开发目录 $devBranch，然后继续迭代发布。"
            } else {
                Write-ReleaseLog "将把运行目录 params/ 的 $($status.Params.Count) 项改动回存为开发目录 master 上的一条提交，然后停止；无合并提交，推送后重跑即可。"
            }
        } elseif ($BackfillOnly) {
            Write-ReleaseLog '运行目录 params/ 无改动：无需回存。'
        }

        if ($BackfillOnly) {
            Write-ReleaseLog '仅回存模式：不检查空闲、不停/启 GUI、不更新代码、不打标签。'
        } else {
            $health = Get-HealthState
            if ($null -eq $health) {
                Write-ReleaseLog 'GUI 未运行，视为空闲。'
            } elseif ($health.hardware_busy -or [int]$health.queued_hardware_jobs -gt 0) {
                $blockers += 'GUI 硬件任务忙'
            } else {
                Write-ReleaseLog "GUI 空闲（instance_id: $($health.instance_id)）。"
                if ($SkipRestart) { $blockers += '-SkipRestart 只能在 GUI 未运行时使用' }
            }

            $targetRef = if ($Iterate) { "origin/$devBranch" } else { 'origin/master' }
            $targetCheck = Invoke-Git $RunDirPath @('rev-parse', '--verify', $targetRef) -AllowFail
            if ($targetCheck.Code -ne 0) {
                Write-ReleaseLog "本地还没有 $targetRef（DryRun 不 fetch）：无法预检目标提交与标签。"
            } else {
                $targetHead = $targetCheck.Text.Trim()
                $planTag = Resolve-ReleaseTag $RunDirPath $targetHead -Wip:$Iterate
                Write-ReleaseLog "目标：$targetRef $(Get-ShortRev $targetHead)，标签 $($planTag.Name)$(if ($planTag.Create) { '（新建）' } else { '（复用）' })。"
                if ($devHead -ne $targetHead) {
                    Write-ReleaseLog "开发目录 HEAD 与本地 $targetRef 不一致：真正发布前需先推送（DryRun 不 fetch，本地 ref 可能落后）。"
                }
                $ffFrom = if ($Iterate -or -not $runState.Detached) { 'HEAD' } else { 'lab' }
                $ff = Invoke-Git $RunDirPath @('merge-base', '--is-ancestor', $ffFrom, $targetRef) -AllowFail
                if ($ff.Code -ne 0) {
                    if ($Iterate) {
                        $blockers += '实验分支未包含运行目录当前版本：可能改写了已发布提交，或分支不是从当前运行版本拉出'
                    } else {
                        $blockers += "运行目录 $ffFrom 不能快进到 $targetRef（需先处理独立提交）"
                    }
                } else {
                    $deps = Get-DependencyChanges $RunDirPath 'HEAD' $targetRef
                    if ($deps -and -not $Resume) {
                        Write-ReleaseLog "依赖清单有变化：发布会停 GUI、更新代码后暂停，等授权手动安装依赖再 -Resume：`n$deps"
                    }
                }
            }
            Write-ReleaseLog "（DryRun 只比对本地已有的 $targetRef，不执行 fetch。）"
        }
    }

    if ($BackfillOnly) {
        Write-ReleaseLog '计划步骤：1 开发目录检查（exp/* 分支）→ 2 运行目录状态与改动检查 → 3 参数回存到实验分支（一条提交）→ 结束。'
    } elseif ($Iterate) {
        Write-ReleaseLog '计划步骤：1 开发目录检查（exp/* 分支）→ 2 空闲检查 → 3 参数回存到实验分支后继续 → 4 预检（已推送、后代、依赖、wip 标签）→ 5 再查空闲并停 GUI → 6 checkout --detach 目标并打 lab-wip 标签（lab 不动）→ 7 构建前端（失败则退回原状态）→ 8 启动 GUI 并验证 instance_id。'
    } else {
        Write-ReleaseLog '计划步骤：1 开发目录检查与测试确认 → 2 空闲检查 → 3 参数回存（开发目录一条提交，无合并）→ 4 预检（已推送、快进、依赖、标签）→ 5 再查空闲并停 GUI → 6 回到 lab 并快进到 origin/master、打标签 → 7 构建前端（失败则退回原版本）→ 8 启动 GUI 并验证 instance_id。'
    }
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
if ($BackfillOnly -and -not $Iterate) { Stop-Release '-BackfillOnly 必须与 -Iterate 同用（回存到实验分支）。' }

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
    if ($Rollback) {
        $how = if ($Rollback -match '^lab-wip-') { "checkout --detach $Rollback（lab 不动）" } else { "回到 lab 并 reset --hard $Rollback" }
        Write-ReleaseLog "回滚计划：停 GUI → $how → 重建前端 → 启动 GUI。"
    }
    Show-ReleasePlan $DevDir $RunDir
}

if ($Rollback) {
    # ===== 回滚流程：检查 → 确认 → 再查空闲并停 GUI → 回退 → 重建前端 → 启动 =====
    Write-ReleaseLog "===== 回滚到标签 $Rollback ====="
    $tagCheck = Invoke-Git $RunDir @('rev-parse', '--verify', "refs/tags/$Rollback") -AllowFail
    if ($tagCheck.Code -ne 0) { Stop-Release "运行目录不存在标签：$Rollback" }
    # wip 标签回滚到分离 HEAD，正式标签回滚到 lab 分支。
    $rollbackWip = $Rollback -match '^lab-wip-\d{8}-\d+$'
    $runState = Assert-RunDirVersion $RunDir
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

    if ($rollbackWip) {
        Invoke-Git $RunDir @('checkout', '--detach', $Rollback) | Out-Null
        Write-ReleaseLog "已 checkout --detach 到 $Rollback（lab 分支不动）。"
    } else {
        if ($runState.Detached) { Invoke-Git $RunDir @('checkout', 'lab') | Out-Null }
        Invoke-Git $RunDir @('reset', '--hard', $Rollback) | Out-Null
        Write-ReleaseLog "已回到 lab 分支并 reset --hard 到 $Rollback。"
    }
    if ($rollbackDeps) {
        Stop-Release ("代码已回退到 $Rollback，GUI 保持停止。依赖清单与回滚前不同；" + (Get-DependencyInstallHint) +
            "完成后重跑同一回滚命令（-Rollback $Rollback）完成前端构建与启动。")
    }
    $buildError = Update-FrontendDist $RunDir
    if ($buildError) {
        Restore-AfterBuildFailure $RunDir $buildError $headBefore $null $preId -Detached:$runState.Detached
    }
    if ($SkipRestart) {
        Write-ReleaseLog '已指定 -SkipRestart：不启动 GUI。'
    } else {
        Start-LabGui $RunDir $preId $HealthTimeoutSec
    }
    Write-ReleaseLog '===== 回滚完成 ====='
    exit 0
}

# ===== 发布流程：正式（master → lab）/ 迭代（exp/* → 分离 HEAD）/ 仅回存 =====
# 步骤 1：开发目录检查 + 测试确认。
Write-ReleaseLog "步骤 1：检查开发目录（模式：$ModeName）..."
$devBranch = Get-GitBranch $DevDir
if ($Iterate) {
    if ($devBranch -notmatch '^exp/') {
        Stop-Release "迭代发布的开发目录当前分支是 $devBranch，必须是 exp/<YYYYMMDD>-<主题> 实验分支。"
    }
} elseif ($devBranch -ne 'master') {
    Stop-Release "开发目录当前分支是 $devBranch，发布只允许从 master 进行。"
}
$staged = Invoke-Git $DevDir @('diff', '--cached', '--quiet') -AllowFail
if ($staged.Code -ne 0) { Stop-Release '开发目录有已暂存改动，请先提交或取消暂存后再发布。' }
$devHeadBefore = Get-GitHead $DevDir
Write-ReleaseLog "开发目录 $devBranch HEAD：$(Get-ShortRev $devHeadBefore)"
# 迭代发布的测试门槛由协同实验会话规则约束，默认不再逐次确认；-Confirm 可保留确认。
if (-not $Iterate -or $Confirm) {
    $answer = Read-Host '确认本提交已通过测试（y/N）'
    if ("$answer".Trim().ToLower() -ne 'y') { Stop-Release '未确认测试通过，已取消发布。' }
}

# 步骤 2：首次空闲检查（提前拦截，不改动任何东西）。仅回存不动 GUI，跳过。
if ($BackfillOnly) {
    Write-ReleaseLog '步骤 2：仅回存模式，跳过空闲检查（GUI 可继续运行）。'
} else {
    Write-ReleaseLog '步骤 2：检查 GUI 是否空闲...'
    Assert-GuiIdle | Out-Null
    Assert-SkipRestartAllowed '发布开始时'
}

# 步骤 3：参数回存（只改开发目录当前分支的一条提交，运行目录 HEAD 不动，GUI 可继续运行）。
Write-ReleaseLog '步骤 3：检查运行目录改动与参数回存...'
$runState = Assert-RunDirVersion $RunDir
Write-ReleaseLog "运行目录当前版本：$(Get-RunDirVersionText $runState)"
$status = Split-StatusByParams (Get-RepoStatusLines $RunDir)
if ($status.Other.Count -gt 0) {
    Stop-Release ("运行目录有 params/ 以外的未提交改动，已停止；请人工处理：`n" + ($status.Other -join "`n"))
}
if ($status.Params.Count -gt 0) {
    if (-not $Iterate -and $runState.Detached) {
        Stop-Release ("运行目录分离于迭代版本（$($runState.Tags -join ', ')）且 params/ 有 $($status.Params.Count) 项改动，" +
            '正式发布不把迭代参数回存到 master，已停止（GUI 未停）；' +
            '请先跑 -Iterate -BackfillOnly 回存到实验分支，并把回存提交整理进 master 后再正式发布。')
    }
    Write-ReleaseLog "运行目录 params/ 有 $($status.Params.Count) 项改动，回存到开发目录 $devBranch..."
    Restore-RunParamsIntoDev $DevDir $RunDir $status.Params.Count
    if ($BackfillOnly) {
        Write-ReleaseLog '===== 仅回存完成：代码与 GUI 均未改动 ====='
        exit 0
    }
    if (-not $Iterate) {
        Stop-Release ('运行参数回存已提交到开发目录 master（只含 params/，无需重新测试）；' +
            '请推送到 origin 后再运行发布。运行目录与 GUI 均未改动。')
    }
} else {
    Write-ReleaseLog '运行目录无未提交改动，跳过参数回存。'
    if ($BackfillOnly) {
        Write-ReleaseLog '无需回存。'
        exit 0
    }
}

# 步骤 4：停 GUI 前的预检——开发目录是否已推送、目标是否包含当前版本、标签、依赖变化，全部只读。
Write-ReleaseLog '步骤 4：预检目标版本与依赖...'
Invoke-Git $RunDir @('fetch', 'origin') | Out-Null
# 目标固定为 origin 上的同名分支：开发目录未推送（或有未同步提交）时发布会悄悄发出旧版本，
# 参数回存后误重跑还会把运行目录参数退回去，所以先比对两侧 HEAD。
$targetRef = if ($Iterate) { "origin/$devBranch" } else { 'origin/master' }
$targetCheck = Invoke-Git $RunDir @('rev-parse', '--verify', $targetRef) -AllowFail
if ($targetCheck.Code -ne 0) {
    Stop-Release "运行目录的 origin 上没有分支 $devBranch；请先把 $devBranch 推送到 origin 再运行发布。运行目录与 GUI 均未改动。"
}
$targetHead = $targetCheck.Text.Trim()
$devHeadNow = Get-GitHead $DevDir
if ($devHeadNow -ne $targetHead) {
    Stop-Release ("开发目录 $devBranch（$(Get-ShortRev $devHeadNow)）与 $targetRef（$(Get-ShortRev $targetHead)）不一致；" +
        "请先把 $devBranch 推送到 origin 再运行发布。运行目录与 GUI 均未改动。")
}
if ($Iterate) {
    $descend = Invoke-Git $RunDir @('merge-base', '--is-ancestor', 'HEAD', $targetRef) -AllowFail
    if ($descend.Code -ne 0) {
        Stop-Release ('实验分支未包含运行目录当前版本：可能改写了已发布提交，或分支不是从当前运行版本拉出。' +
            '已停止；运行目录与 GUI 均未改动。')
    }
} else {
    # 分离 HEAD 时 lab 分支才是正式发布的起点：lab 必须是 origin/master 的祖先。
    $ffFrom = if ($runState.Detached) { 'lab' } else { 'HEAD' }
    $ffCheck = Invoke-Git $RunDir @('merge-base', '--is-ancestor', $ffFrom, 'origin/master') -AllowFail
    if ($ffCheck.Code -ne 0) {
        Stop-Release "运行目录 $ffFrom 不能快进到 origin/master（有未合并进 master 的提交），已停止；请先把这些提交合并回 master 并重新测试。"
    }
}
$runHeadBefore = Get-GitHead $RunDir
$tagPlan = Resolve-ReleaseTag $RunDir $targetHead -Wip:$Iterate
$newTag = $tagPlan.Name
$prevTag = $tagPlan.PrevTag
$createTag = $tagPlan.Create

$depsChanged = $false
if ($Resume) {
    Write-ReleaseLog '已指定 -Resume：跳过依赖检查（视为依赖已手动安装完成）。'
} else {
    $deps = Get-DependencyChanges $RunDir 'HEAD' $targetRef
    if ($deps) {
        $depsChanged = $true
        Write-ReleaseLog "依赖清单有变化：`n$deps"
        $answer = Read-Host '依赖有变化：将停止 GUI、更新代码后暂停，等你授权安装依赖再用 -Resume 完成。继续（y/N）'
        if ("$answer".Trim().ToLower() -ne 'y') { Stop-Release '未确认依赖变化的发布，已取消；运行目录与 GUI 均未改动。' }
    } else {
        Write-ReleaseLog '依赖清单无变化。'
    }
}

# 迭代发布只改了 scripts/ 时不停/启 GUI、不重建前端：后端与 lab_workflows 从不导入 scripts/，
# 代跑脚本每次由子进程从磁盘读取，热更新不影响在用后端。scripts/ 以外的任何改动（含参数回存）照旧走完整流程。
if ($Iterate -and -not $depsChanged) {
    $changedText = (Invoke-Git $RunDir @('-c', 'core.quotepath=false', 'diff', '--name-only', 'HEAD', $targetHead)).Text
    $changedPaths = @($changedText -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $outsideScripts = @($changedPaths | Where-Object { -not $_.StartsWith('scripts/') })
    if ($changedPaths.Count -gt 0 -and $outsideScripts.Count -eq 0) {
        Write-ReleaseLog "本次只改 scripts/（$($changedPaths.Count) 个文件）：不停 GUI、不重建前端。"
        Write-ReleaseLog '步骤 5：更新代码前再次检查空闲（不停 GUI）...'
        Assert-GuiIdle | Out-Null
        Write-ReleaseLog "步骤 6：运行目录 checkout --detach 到 $targetRef..."
        Invoke-Git $RunDir @('checkout', '--detach', $targetHead) | Out-Null
        if ($createTag) { Invoke-Git $RunDir @('tag', $newTag) | Out-Null }
        Write-ReleaseLog "运行目录已分离到 $(Get-ShortRev $targetHead)，标签 $newTag（lab 分支未动）。"
        Write-ReleaseLog "===== 迭代发布完成：$newTag（只改 scripts/，GUI 未重启） ====="
        exit 0
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

# 步骤 6：GUI 已停止，更新代码并打标签。迭代只移动 HEAD，lab 分支保持不动。
$createdTag = $null
if ($Iterate) {
    Write-ReleaseLog "步骤 6：运行目录 checkout --detach 到 $targetRef..."
    Invoke-Git $RunDir @('checkout', '--detach', $targetHead) | Out-Null
    if ($createTag) {
        Invoke-Git $RunDir @('tag', $newTag) | Out-Null
        $createdTag = $newTag
    }
    Write-ReleaseLog "运行目录已分离到 $(Get-ShortRev $targetHead)，标签 $newTag（lab 分支未动）。"
} else {
    Write-ReleaseLog '步骤 6：回到 lab 分支并快进到 origin/master...'
    if ($runState.Detached) { Invoke-Git $RunDir @('checkout', 'lab') | Out-Null }
    if ((Get-GitHead $RunDir) -ne $targetHead) {
        Invoke-Git $RunDir @('merge', '--ff-only', 'origin/master') | Out-Null
    }
    if ($createTag) {
        Invoke-Git $RunDir @('tag', $newTag) | Out-Null
        $createdTag = $newTag
        Write-ReleaseLog "已快进到 origin/master 并打标签 $newTag。"
    }
}
if ($depsChanged) {
    Stop-Release ("代码已更新到 $newTag，GUI 保持停止。依赖清单相对上一版本有变化；" + (Get-DependencyInstallHint) +
        '完成后加 -Resume 重跑本脚本，完成前端构建与启动。')
}

# 步骤 7：构建前端；失败则退回发布前的状态（正式发布本次没改动代码时退回上一个标签）。
Write-ReleaseLog '步骤 7：构建前端...'
$buildError = Update-FrontendDist $RunDir
if ($buildError) {
    $restoreRef = if ($Iterate -or $runState.Detached) {
        $runHeadBefore
    } elseif ($runHeadBefore -ne $targetHead) { $runHeadBefore } elseif ($prevTag) { $prevTag } else { $null }
    Restore-AfterBuildFailure $RunDir $buildError $restoreRef $createdTag $preId -Detached:$runState.Detached
}

# 步骤 8：启动 GUI。
if ($SkipRestart) {
    Write-ReleaseLog '已指定 -SkipRestart：不启动 GUI；代码与前端已更新，下次用 start_lab.vbs 启动。'
} else {
    Write-ReleaseLog '步骤 8：启动 GUI...'
    Start-LabGui $RunDir $preId $HealthTimeoutSec
}
if ($Iterate) {
    Write-ReleaseLog "===== 迭代发布完成：$newTag（分离 HEAD，lab 未动） ====="
} else {
    Write-ReleaseLog "===== 发布完成：$newTag ====="
}
exit 0
