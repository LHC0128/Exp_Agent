# 停止脚本回归：替换进程/端口/文件命令，不停止任何真实服务。
# 重点：PID 文件失效时按端口回退，只认命令行属于本目录的后端，不把另一目录（如运行目录）的后端当成自己的。
$ErrorActionPreference = "Stop"
$StopScript = Join-Path $PSScriptRoot "stop.ps1"
$Root = Split-Path -Parent $PSScriptRoot
$TestState = @{}

function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Test-Path {
    param($Path, $LiteralPath)
    if (-not $Path) { $Path = $LiteralPath }
    if ($Path -like '*.lab_dev.yaml') { return $null -ne $TestState.Case.DevConfig }
    return $false   # 没有 PID 文件，强制走端口回退
}

function Get-Content {
    param($LiteralPath, [switch]$Raw, $Encoding, $ErrorAction)
    Assert ($LiteralPath -like '*.lab_dev.yaml') "意外的文件读取：$LiteralPath"
    return $TestState.Case.DevConfig
}

function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    $listener = $TestState.Case.Listeners | Where-Object { $_.Port -eq $LocalPort -and $TestState.Alive[$_.Pid] } | Select-Object -First 1
    if ($listener) { return [pscustomobject]@{ LocalPort = $LocalPort; OwningProcess = $listener.Pid } }
}

function Get-Process {
    param($Id, $ErrorAction)
    $listener = $TestState.Case.Listeners | Where-Object { $_.Pid -eq $Id } | Select-Object -First 1
    if (-not $listener -or -not $TestState.Alive[$Id]) { return $null }
    if ($TestState.StopFileWritten) {
        $TestState.WaitPolls += 1
        Assert ($TestState.WaitPolls -lt 20) "等待了不会退出的进程（PID $Id）"
        $TestState.Alive[$Id] = $false   # 模拟后端读到停止文件后完成收尾
    }
    return [pscustomobject]@{ Id = $Id; ProcessName = $listener.Name }
}

function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    $id = [int]($Filter -replace '\D', '')
    $listener = $TestState.Case.Listeners | Where-Object { $_.Pid -eq $id } | Select-Object -First 1
    return [pscustomobject]@{ CommandLine = $listener.CommandLine }
}

function New-Item {
    param($ItemType, $Path, [switch]$Force)
    $TestState.StopFileWritten = $true
    $TestState.StopFilePath = $Path
}

function Remove-Item { param($LiteralPath, [switch]$Force, $ErrorAction) }
function Start-Sleep { param($Milliseconds) }
function taskkill.exe { $TestState.Killed += ($args -join ' ') }

$RunRoot = "${Root}_run"
$Cases = @(
    @{
        Name = '只有运行目录后端在 8000：不认领、不等待'
        Listeners = @(@{ Port = 8000; Pid = 700; Name = 'python'; CommandLine = "python.exe -m backend --log-file `"$RunRoot\tmp\gui-backend.log`"" })
        ExpectStop = $false
    },
    @{
        Name = '本目录开发后端在 8100：发停止文件并等待退出'
        Listeners = @(
            @{ Port = 8000; Pid = 700; Name = 'python'; CommandLine = "python.exe -m backend --log-file `"$RunRoot\tmp\gui-backend.log`"" },
            @{ Port = 8100; Pid = 810; Name = 'python'; CommandLine = "python.exe -m backend --log-file `"$Root\tmp\gui-backend.log`"" })
        ExpectStop = $true
    },
    @{
        Name = '本目录后端在 .lab_dev.yaml 指定端口'
        DevConfig = @('backend_port: 8200')
        Listeners = @(@{ Port = 8200; Pid = 820; Name = 'python'; CommandLine = "python.exe -m backend --log-file `"$Root\tmp\gui-backend.log`"" })
        ExpectStop = $true
    },
    @{
        Name = '运行目录 Vite 占用 5273：不结束'
        Listeners = @(@{ Port = 5273; Pid = 527; Name = 'node'; CommandLine = "node.exe `"$RunRoot\GUI\frontend\node_modules\vite\bin\vite.js`"" })
        ExpectStop = $false
    }
)

foreach ($Case in $Cases) {
    $TestState.Case = $Case
    $TestState.Alive = @{}
    foreach ($listener in $Case.Listeners) { $TestState.Alive[$listener.Pid] = $true }
    $TestState.StopFileWritten = $false
    $TestState.StopFilePath = $null
    $TestState.WaitPolls = 0
    $TestState.Killed = @()
    $output = @(& $StopScript 6>&1) | Out-String
    Assert ($TestState.Killed.Count -eq 0) "$($Case.Name)：结束了其他目录的进程：$($TestState.Killed)"
    if ($Case.ExpectStop) {
        Assert ($TestState.StopFileWritten -and $TestState.StopFilePath -like "$Root\tmp\*") "$($Case.Name)：未向本目录写停止文件"
        Assert ($output -match 'GUI 已停止') "$($Case.Name)：未报告已停止"
    } else {
        Assert (-not $TestState.StopFileWritten) "$($Case.Name)：认领了其他目录的后端"
        Assert ($output -match '未发现运行中的 GUI 服务') "$($Case.Name)：输出不符：$output"
    }
    Write-Host "通过：$($Case.Name)"
}
Write-Host "全部 $($Cases.Count) 项停止回归通过；未停止任何真实服务。"
