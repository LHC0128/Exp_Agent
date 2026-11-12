# 一键启动回归：替换外部命令，不启动服务、浏览器或真实仪器。
# 任何预期之外的命令查询（包括远程隧道类命令）都会直接抛错，使对应用例失败。
$ErrorActionPreference = "Stop"
$Launcher = Join-Path $PSScriptRoot "start.ps1"
$OriginalLocation = Get-Location
$TestState = @{}

function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Test-Path {
    param($Path, $LiteralPath)
    if (-not $Path) { $Path = $LiteralPath }
    $TestState.PathQueries += $Path
    if ($Path -like '*.lab_dev.yaml') { return $null -ne $TestState.Case.DevConfig }
    if ($Path -like 'D:\Missing*') { return $false }
    if ($TestState.Case.MissingPython -and $Path -like '*Scripts\python.exe') { return $false }
    if ($TestState.Case.MissingDist -and $Path -like '*dist\index.html') { return $false }
    if ($TestState.Case.MissingNodeModules -and $Path -like '*node_modules') { return $false }
    # 自重启用例：第一次后端退出时没有停止文件，模拟界面「重启后端」。
    if ($TestState.Case.Restart -and $Path -like '*gui-backend.stop' -and -not $TestState.SuccessorAdopted) { return $false }
    return $true
}

function Get-Content {
    param($LiteralPath, $Encoding)
    Assert ($LiteralPath -like '*.lab_dev.yaml') "意外的文件读取：$LiteralPath"
    return $TestState.Case.DevConfig
}

function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    $TestState.CommandQueries += $Name
    if ($Name -eq "npm.cmd") { return [pscustomobject]@{ Source = "Invoke-FakeNpm" } }
    if ($Name -eq "node.exe") { return [pscustomobject]@{ Source = "D:\Fake\node.exe" } }
    throw "意外的命令查询：$Name"
}

function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    $TestState.PortQueries += $LocalPort
    if ($TestState.Case.BusyPort -eq $LocalPort -or
        ($TestState.BackendStarted -and $LocalPort -eq $TestState.ExpectedBackendPort) -or
        ($TestState.FrontendStarted -and $LocalPort -eq $TestState.ExpectedFrontendPort)) {
        $owner = if ($TestState.SuccessorStarted) { 4343 } else { 4242 }
        return [pscustomobject]@{ LocalPort = $LocalPort; OwningProcess = $owner }
    }
}

function Get-Process {
    param($Id, $ErrorAction)
    Assert ($Id -eq 4343) "意外的进程查询：$Id"
    $TestState.SuccessorAdopted = $true
    $successor = [pscustomobject]@{ HasExited = $false; ExitCode = 0; Id = 4343; ProcessName = 'python' }
    $successor | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
        $this.HasExited = $true
        $TestState.SuccessorWaited = $true
    }
    return $successor
}

function Start-Process {
    param(
        $FilePath,
        $ArgumentList,
        $WorkingDirectory,
        $WindowStyle,
        [switch]$PassThru,
        $RedirectStandardOutput,
        $RedirectStandardError
    )
    if ($FilePath -like 'http://*') {
        $TestState.BrowserOpened = $true
        $TestState.BrowserUrl = $FilePath
        return
    }
    if ($FilePath -like '*node.exe') {
        Assert ($ArgumentList -match 'vite\.js') "Vite 入口不匹配"
        Assert ($ArgumentList -match '--strictPort') "Vite 参数不匹配"
        Assert ($WindowStyle -eq 'Hidden') "Vite 未使用隐藏窗口"
        Assert ($PassThru) "未获取 Vite 进程句柄"
        Assert ($RedirectStandardOutput -and $RedirectStandardError) "Vite 日志未重定向"
        $TestState.FrontendStarted = $true
        $fake = [pscustomobject]@{ HasExited = $false; ExitCode = 0; Id = 5252 }
        $fake | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
            $this.HasExited = $true
        }
        return $fake
    }
    Assert ($ArgumentList -match '(^| )-m backend( |$)') "后端命令发生变化"
    Assert ($ArgumentList -match '--log-file "[^"]+gui-backend\.log"') "后端日志未重定向"
    Assert ($ArgumentList -match '--stop-file "[^"]+\.stop"') "未配置后端停止文件"
    Assert ($ArgumentList -match '--parent-pid \d+') "未传入启动器进程号"
    Assert ($WindowStyle -eq 'Hidden') "后端未使用隐藏窗口"
    Assert ($PassThru) "未获取后端进程句柄"
    $TestState.BackendStarted = $true
    $TestState.BackendPortEnv = $env:LAB_BACKEND_PORT
    $TestState.FrontendPortEnv = $env:LAB_FRONTEND_PORT
    $TestState.DataRootEnv = $env:LAB_DATA_ROOT
    $TestState.BackendStopped = $false
    $TestState.BackendWaited = $false
    $backendExitCode = if ($TestState.Case.BackendFailure) { 24 } else { 0 }
    $fake = [pscustomobject]@{ HasExited = $false; ExitCode = $backendExitCode; Id = 4242 }
    $fake | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
        $this.HasExited = $true
        $TestState.BackendStopped = $true
        $TestState.BackendWaited = $true
        if ($TestState.Case.Restart) { $TestState.SuccessorStarted = $true }
    }
    return $fake
}

function Start-Transcript {
    param($Path, [switch]$Append)
}

function Stop-Transcript {
}

function Set-Content {
    param($LiteralPath, $Value)
    $TestState.Files[$LiteralPath] = $Value
    $TestState.WrittenFiles[$LiteralPath] = $true
    $TestState.Writes += "$(Split-Path -Leaf $LiteralPath)=$Value"
}

function Add-Content {
    param($LiteralPath, $Value, $Encoding)
    $TestState.Files[$LiteralPath] = "$($TestState.Files[$LiteralPath])$Value"
}

function New-Item {
    param($ItemType, $Path, [switch]$Force)
}

function Remove-Item {
    param($LiteralPath, [switch]$Force, $ErrorAction)
    $TestState.Files.Remove($LiteralPath)
}

function Invoke-FakeNpm {
    $TestState.NpmStarted = $true
    Assert (($args -join ' ') -eq 'run dev -- --strictPort --open') "前端启动参数不匹配"
    $global:LASTEXITCODE = if ($TestState.Case.FrontendFailure) { 23 } else { 0 }
}

$Cases = @(
    @{ Name = '开发模式正常启动' },
    @{ Name = '缺少 Python'; MissingPython = $true; Error = '未找到虚拟环境 Python' },
    @{ Name = '开发模式 5273 端口冲突'; BusyPort = 5273; Error = '端口 5273 已被占用' },
    @{ Name = '开发模式 8100 端口冲突'; BusyPort = 8100; Error = '端口 8100 已被占用' },
    @{ Name = '开发模式与运行目录 8000 共存'; BusyPort = 8000 },
    @{ Name = '开发模式读取 .lab_dev.yaml 端口'; Hidden = $true; DevConfig = @('data_root: D:\x', 'backend_port: 8200  # 注释', 'frontend_port: 5300'); BackendPort = 8200; FrontendPort = 5300; DataRoot = 'D:\x' },
    @{ Name = '开发模式传递数据根'; DevConfig = @('data_root: "D:\Run\data"'); DataRoot = 'D:\Run\data' },
    @{ Name = '开发模式数据根不存在'; DevConfig = @('data_root: D:\Missing\data'); Error = 'data_root 不存在' },
    @{ Name = '开发模式拒绝 8000 端口'; DevConfig = @('backend_port: 8000'); Error = '不能使用运行目录专用的 8000' },
    @{ Name = '开发模式拒绝无效端口'; DevConfig = @('frontend_port: 80'); Error = '不是有效端口' },
    @{ Name = '前端失败退出码'; FrontendFailure = $true },
    @{ Name = '隐藏终端启动'; Hidden = $true },
    @{ Name = '生产模式正常启动'; Production = $true },
    @{ Name = '生产模式隐藏启动'; Production = $true; Hidden = $true },
    @{ Name = '生产模式不打开浏览器'; Production = $true; NoBrowser = $true },
    @{ Name = '生产模式缺少 dist'; Production = $true; MissingDist = $true; Error = '未找到前端构建' },
    @{ Name = '生产模式 8000 端口冲突'; Production = $true; BusyPort = 8000; Error = '端口 8000 已被占用' },
    @{ Name = '生产模式忽略开发端口占用'; Production = $true; BusyPort = 8100 },
    @{ Name = '生产模式忽略 .lab_dev.yaml'; Production = $true; DevConfig = @('backend_port: 8200', 'data_root: D:\Run\data'); StaleDataRoot = $true },
    @{ Name = '生产模式不依赖 node_modules'; Production = $true; MissingNodeModules = $true },
    @{ Name = '生产模式等待后端退出并透传退出码'; Production = $true; BackendFailure = $true },
    @{ Name = '生产模式跟随后端自重启'; Production = $true; Hidden = $true; Restart = $true }
)

try {
    foreach ($Case in $Cases) {
        $TestState.Case = $Case
        $TestState.BackendStarted = $false
        $TestState.BackendStopped = $false
        $TestState.BackendWaited = $false
        $TestState.NpmStarted = $false
        $TestState.FrontendStarted = $false
        $TestState.BrowserOpened = $false
        $TestState.BrowserUrl = $null
        $TestState.Files = @{}
        $TestState.WrittenFiles = @{}
        $TestState.Writes = @()
        $TestState.CommandQueries = @()
        $TestState.PathQueries = @()
        $TestState.PortQueries = @()
        $TestState.SuccessorStarted = $false
        $TestState.SuccessorAdopted = $false
        $TestState.SuccessorWaited = $false
        $TestState.BackendPortEnv = $null
        $TestState.FrontendPortEnv = $null
        $env:LAB_BACKEND_PORT = $null
        $env:LAB_FRONTEND_PORT = $null
        $env:LAB_DATA_ROOT = $null
        if ($TestState.Case.StaleDataRoot) { $env:LAB_DATA_ROOT = 'D:\Stale' }
        $TestState.DataRootEnv = $null
        if ($TestState.Case.Production) {
            $TestState.ExpectedBackendPort = 8000
            $TestState.ExpectedFrontendPort = $null
        } else {
            $TestState.ExpectedBackendPort = if ($TestState.Case.BackendPort) { $TestState.Case.BackendPort } else { 8100 }
            $TestState.ExpectedFrontendPort = if ($TestState.Case.FrontendPort) { $TestState.Case.FrontendPort } else { 5273 }
        }
        $options = @{
            OpenBrowser = -not [bool]$TestState.Case.NoBrowser
            Hidden = [bool]$TestState.Case.Hidden
            Production = [bool]$TestState.Case.Production
        }
        $failure = $null
        try {
            $output = @(& $Launcher @options 6>&1) | Out-String
            $exitCode = $LASTEXITCODE
        } catch { $failure = $_.Exception.Message }

        if ($TestState.Case.Error) {
            Assert ($failure -match $TestState.Case.Error) "$($TestState.Case.Name)：错误不匹配：$failure"
            Assert (-not $TestState.BackendStarted -and -not $TestState.NpmStarted -and -not $TestState.FrontendStarted) "$($TestState.Case.Name)：失败后仍启动了服务"
        } else {
            Assert (-not $failure) "$($TestState.Case.Name)：$failure"
            Assert ($TestState.BackendStarted) "未启动后端"
            Assert ($TestState.BackendStopped) "启动器退出时未等待后端停止"
            Assert ($TestState.BackendPortEnv -eq "$($TestState.ExpectedBackendPort)") "后端端口环境变量不匹配：$($TestState.BackendPortEnv)"
            Assert ($TestState.DataRootEnv -eq $TestState.Case.DataRoot) "数据根环境变量不匹配：$($TestState.DataRootEnv)"
            if ($TestState.Case.Production) {
                Assert (-not $TestState.NpmStarted -and -not $TestState.FrontendStarted) "生产模式仍启动了前端"
                Assert (@($TestState.CommandQueries | Where-Object { $_ -in @('npm.cmd', 'node.exe') }).Count -eq 0) "生产模式查询了 npm/node"
                Assert (@($TestState.PathQueries | Where-Object { $_ -like '*node_modules*' }).Count -eq 0) "生产模式检查了 node_modules"
                Assert ($TestState.PortQueries -notcontains 5273 -and $TestState.PortQueries -notcontains 8100) "生产模式检查了开发端口"
                Assert (@($TestState.PathQueries | Where-Object { $_ -like '*.lab_dev.yaml' }).Count -eq 0) "生产模式读取了 .lab_dev.yaml"
                Assert (@($TestState.WrittenFiles.Keys | Where-Object { $_ -like '*gui-frontend.pid' }).Count -eq 0) "生产模式写入了前端 PID 文件"
                Assert ($TestState.BackendWaited) "生产模式启动器未等待后端进程退出"
                if ($TestState.Case.Restart) {
                    Assert ($TestState.SuccessorAdopted -and $TestState.SuccessorWaited) "生产模式后端自重启后启动器未跟随新进程"
                    Assert ($TestState.Writes -contains 'gui-backend.pid=4343') "自重启后未更新后端 PID 文件"
                } else {
                    Assert (-not $TestState.SuccessorAdopted) "未自重启却查询了新后端进程"
                }
                if ($TestState.Case.NoBrowser) {
                    Assert (-not $TestState.BrowserOpened) "未要求打开浏览器却打开了"
                } else {
                    Assert ($TestState.BrowserOpened -and $TestState.BrowserUrl -eq 'http://127.0.0.1:8000') "生产模式浏览器未打开 8000 地址"
                    Assert ($output.Contains('http://127.0.0.1:8000')) "生产模式日志未提示 8000 地址"
                }
                $expectedExitCode = if ($TestState.Case.BackendFailure) { 24 } else { 0 }
            } elseif ($TestState.Case.Hidden) {
                Assert ($TestState.FrontendStarted -and $TestState.BrowserOpened) "隐藏模式未启动前端或未打开浏览器"
                Assert ($TestState.BrowserUrl -eq "http://127.0.0.1:$($TestState.ExpectedFrontendPort)") "隐藏模式浏览器地址不匹配：$($TestState.BrowserUrl)"
                Assert ($TestState.FrontendPortEnv -eq "$($TestState.ExpectedFrontendPort)") "前端端口环境变量不匹配"
                Assert (@($TestState.WrittenFiles.Keys | Where-Object { $_ -like '*gui-frontend.pid' }).Count -eq 1) "隐藏模式未写入前端 PID 文件"
                $expectedExitCode = 0
            } else {
                Assert ($TestState.NpmStarted) "未启动前端"
                $expectedExitCode = if ($TestState.Case.FrontendFailure) { 23 } else { 0 }
            }
            Assert ($exitCode -eq $expectedExitCode) "退出码没有透传：期望 $expectedExitCode，实际 $exitCode"
        }
        Write-Host "通过：$($TestState.Case.Name)"
    }
    Write-Host "全部 $($Cases.Count) 项启动回归通过；未启动真实服务或连接仪器。"
} finally {
    $env:LAB_BACKEND_PORT = $null
    $env:LAB_DATA_ROOT = $null
    $env:LAB_FRONTEND_PORT = $null
    Set-Location -LiteralPath $OriginalLocation
}
