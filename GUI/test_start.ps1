# 一键启动回归：替换外部命令，不启动服务、浏览器或真实仪器。
$ErrorActionPreference = "Stop"
$Launcher = Join-Path $PSScriptRoot "start.ps1"
$OriginalLocation = Get-Location
$OriginalAllowedHosts = $env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS
$TestHostName = "experiment-lab.private-test.ts.net"
$TestState = @{}

function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Test-Path {
    param($Path)
    return -not ($TestState.Case.MissingPython -and $Path -like '*Scripts\python.exe')
}

function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -eq "npm.cmd") { return [pscustomobject]@{ Source = "Invoke-FakeNpm" } }
    if ($Name -eq "tailscale.exe") {
        if ($TestState.Case.MissingTailscale) { return $null }
        return [pscustomobject]@{ Source = "Invoke-FakeTailscale" }
    }
    throw "意外的命令查询：$Name"
}

function Get-NetTCPConnection {
    param($State, $LocalPort, $ErrorAction)
    if ($TestState.Case.BusyPort -eq $LocalPort -or ($TestState.BackendStarted -and $LocalPort -eq 8000)) {
        return [pscustomobject]@{ LocalPort = $LocalPort }
    }
}

function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory)
    Assert (($ArgumentList -join ' ') -eq '-m backend') "后端命令发生变化"
    $TestState.BackendStarted = $true
}

function Invoke-FakeTailscale {
    $command = $args -join ' '
    $TestState.TailscaleCalls += $command
    $global:LASTEXITCODE = 0
    if ($command -eq 'status --json') {
        if ($TestState.Case.StatusFailure) { $global:LASTEXITCODE = 1; return }
        $state = if ($TestState.Case.Offline) { 'NeedsLogin' } else { 'Running' }
        $dns = if ($TestState.Case.BadDns) { 'unexpected.example.com.' } else { "$TestHostName." }
        return (@{ BackendState = $state; Self = @{ DNSName = $dns } } | ConvertTo-Json -Depth 5)
    }
    if ($command -eq 'serve status --json') {
        return ($TestState.ServeConfig | ConvertTo-Json -Depth 8)
    }
    Assert ($command -eq 'serve --bg --yes --https=443 --set-path=/ http://127.0.0.1:5173') "Serve 参数不匹配：$command"
    if ($TestState.Case.ServeFailure) { $global:LASTEXITCODE = 1; return }
    $target = if ($TestState.Case.BadReadback) { 'http://127.0.0.1:9999' } else { 'http://127.0.0.1:5173' }
    $TestState.ServeConfig = @{
        TCP = @{ '443' = @{ HTTPS = $true } }
        Web = @{ "${TestHostName}:443" = @{ Handlers = @{ '/' = @{ Proxy = $target } } } }
    }
    if ($TestState.Case.ExtraReadbackPath) {
        $TestState.ServeConfig.Web."${TestHostName}:443".Handlers['/api'] = @{ Proxy = 'http://127.0.0.1:3000' }
    }
}

function Invoke-FakeNpm {
    $TestState.NpmStarted = $true
    Assert (($args -join ' ') -eq 'run dev -- --strictPort --open') "前端启动参数不匹配"
    $global:LASTEXITCODE = if ($TestState.Case.FrontendFailure) { 23 } else { 0 }
}

$Cases = @(
    @{ Name = '私人 HTTPS 启动' },
    @{ Name = '重复启动复用相同入口'; ExistingTarget = 'http://127.0.0.1:5173' },
    @{ Name = '仅本机运行'; LocalOnly = $true; MissingTailscale = $true },
    @{ Name = '缺少 Python'; MissingPython = $true; Error = '未找到虚拟环境 Python' },
    @{ Name = '端口冲突'; BusyPort = 5173; Error = '端口 5173 已被占用' },
    @{ Name = '缺少 Tailscale'; MissingTailscale = $true; Error = '未找到 Tailscale' },
    @{ Name = 'Tailscale 未登录'; Offline = $true; Error = 'Tailscale 尚未连接' },
    @{ Name = '状态查询失败'; StatusFailure = $true; Error = '无法读取 Tailscale 状态' },
    @{ Name = '无效域名'; BadDns = $true; Error = '有效的本机域名' },
    @{ Name = '拒绝覆盖其他入口'; ExistingTarget = 'http://127.0.0.1:3000'; Error = '不会覆盖' },
    @{ Name = '拒绝其他路径截获请求'; ExistingTarget = 'http://127.0.0.1:5173'; ExtraPath = $true; Error = '不会覆盖' },
    @{ Name = '拒绝公网 Funnel'; Funnel = $true; Error = '不会覆盖' },
    @{ Name = '拒绝 TCP 转发冲突'; TcpForward = $true; Error = '不会覆盖' },
    @{ Name = 'Serve 配置失败'; ServeFailure = $true; Error = 'Serve 配置失败' },
    @{ Name = 'Serve 回读不匹配'; BadReadback = $true; Error = '回读不匹配' },
    @{ Name = 'Serve 回读出现其他路径'; ExtraReadbackPath = $true; Error = '回读不匹配' },
    @{ Name = '前端失败退出码'; FrontendFailure = $true }
)

try {
    foreach ($Case in $Cases) {
        $TestState.Case = $Case
        $TestState.TailscaleCalls = @()
        $TestState.ServeConfig = @{}
        $TestState.BackendStarted = $false
        $TestState.NpmStarted = $false
        $initialHosts = if ($TestState.Case.ExistingTarget) { "existing.example.test,$TestHostName" } else { 'existing.example.test' }
        $env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = $initialHosts
        if ($TestState.Case.ExistingTarget) {
            $TestState.ServeConfig = @{
                TCP = @{ '443' = @{ HTTPS = $true } }
                Web = @{ "${TestHostName}:443" = @{ Handlers = @{ '/' = @{ Proxy = $TestState.Case.ExistingTarget } } } }
            }
        }
        if ($TestState.Case.ExtraPath) {
            $TestState.ServeConfig.Web."${TestHostName}:443".Handlers['/api'] = @{ Proxy = 'http://127.0.0.1:3000' }
        }
        if ($TestState.Case.Funnel) { $TestState.ServeConfig.AllowFunnel = @{ "${TestHostName}:443" = $true } }
        if ($TestState.Case.TcpForward) { $TestState.ServeConfig.TCP = @{ '443' = @{ TCPForward = 'localhost:22' } } }
        $options = @{ OpenBrowser = $true; LocalOnly = [bool]$TestState.Case.LocalOnly }
        $failure = $null
        try {
            $output = @(& $Launcher @options 6>&1) | Out-String
            $exitCode = $LASTEXITCODE
        } catch { $failure = $_.Exception.Message }

        if ($TestState.Case.Error) {
            Assert ($failure -match $TestState.Case.Error) "$($TestState.Case.Name)：错误不匹配：$failure"
            Assert (-not $TestState.BackendStarted -and -not $TestState.NpmStarted) "$($TestState.Case.Name)：失败后仍启动了服务"
            if ($TestState.Case.Error -eq '不会覆盖') {
                Assert (@($TestState.TailscaleCalls | Where-Object { $_ -like 'serve --bg *' }).Count -eq 0) "冲突配置仍被写入"
            }
        } else {
            Assert (-not $failure) "$($TestState.Case.Name)：$failure"
            Assert ($TestState.BackendStarted -and $TestState.NpmStarted) "未启动前后端"
            $expectedExitCode = if ($TestState.Case.FrontendFailure) { 23 } else { 0 }
            Assert ($exitCode -eq $expectedExitCode) "前端退出码没有透传"
            $expectedHosts = if ($TestState.Case.LocalOnly) { $initialHosts } else { "existing.example.test,$TestHostName" }
            Assert ($env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS -eq $expectedHosts) "域名白名单被覆盖或重复添加"
            if ($TestState.Case.LocalOnly) {
                Assert ($TestState.TailscaleCalls.Count -eq 0) "仅本机模式仍调用了 Tailscale"
            } else {
                Assert ($output.Contains("https://$TestHostName")) "未显示笔记本访问地址"
            }
        }
        Write-Host "通过：$($TestState.Case.Name)"
    }
    Write-Host "全部 $($Cases.Count) 项启动回归通过；未启动真实服务或连接仪器。"
} finally {
    $env:__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = $OriginalAllowedHosts
    Set-Location -LiteralPath $OriginalLocation
}
