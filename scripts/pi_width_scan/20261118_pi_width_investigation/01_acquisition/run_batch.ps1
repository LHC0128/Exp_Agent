param(
    [double]$XBias = 0.0,
    [string]$Phases = "10,100,190,280,10",
    [string]$Tag = "bias0"
)
# 顺序运行 pi-width-scan：固定 X 偏置，逐个射频相位扫脉宽；任一次退出码非零即停止。
$ErrorActionPreference = "Stop"
Set-Location D:\Code\exp_agent
$log = "D:\Code\exp_agent\.cache\pi_width_investigation\runs"
New-Item -ItemType Directory -Force $log | Out-Null
$i = 0
foreach ($ph in $Phases.Split(",")) {
    $i++
    $name = "{0}_{1:D2}_phase{2}" -f $Tag, $i, $ph
    Write-Output ("[{0}] start {1}" -f (Get-Date -Format "HH:mm:ss"), $name)
    $out = & .\agent_exp_env\Scripts\python.exe -m tools.lab_client run pi-width-scan --allow pi-width-scan `
        --allow-param Y_RF_VPP=0.01:0.2 --allow-param N_REPEAT=1:5 --allow-param X_BIAS_V=-0.05:0.05 `
        --allow-param "PHASE_CHECK_ENABLED=false" --allow-param Y_RF_PHASE_DEG=0:360 `
        --allow-param Z_PULSE_WIDTH_START_US=2:14 --allow-param Z_PULSE_WIDTH_STOP_US=2:14 `
        --allow-param Z_PULSE_WIDTH_STEP_US=0.1:1 `
        --param Y_RF_VPP=0.1 --param N_REPEAT=2 --param "X_BIAS_V=$XBias" --param PHASE_CHECK_ENABLED=false `
        --param "Y_RF_PHASE_DEG=$ph" --param Z_PULSE_WIDTH_START_US=2.0 --param Z_PULSE_WIDTH_STOP_US=14.0 `
        --param Z_PULSE_WIDTH_STEP_US=0.5 --steps A01,A02,A04 --timeout 1500 2>&1
    $code = $LASTEXITCODE
    $out | Out-File -Encoding utf8 (Join-Path $log "$name.json")
    Write-Output ("[{0}] end {1} exit={2}" -f (Get-Date -Format "HH:mm:ss"), $name, $code)
    if ($code -ne 0) { Write-Output "STOP: nonzero exit"; exit $code }
}
Write-Output "BATCH DONE"
