---
title: Mx Z 最优控制 XY 平衡场 RF 相位响应
type: Mx_Z_Optimal_Control_XY_RF_Phase_Response
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  X_FIELD_START_V: -0.05
  X_FIELD_STOP_V: 0.05
  X_FIELD_POINTS: 11
  Y_FIELD_START_V: -0.05
  Y_FIELD_STOP_V: 0.05
  Y_FIELD_POINTS: 11
  Y_RF_FREQUENCY_HZ: 12000.0
  PHASE_CAL_RF_AMPLITUDE_VPP: 0.01
  PHASE_SCAN_START_DEG: 0.0
  PHASE_SCAN_STOP_DEG: 340.0
  PHASE_SCAN_STEP_DEG: 20.0
mapping_keys:
  main_magnetic_field:
    role: fixed_main_field
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger
  X_magnetic_field:
    role: x_dc_scan
  Y_magnetic_field:
    role: y_dc_scan_and_rf_offset
  rf_coil:
    role: rf_burst_on_y_channel
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_r_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - X 外层、Y 蛇形网格按规范轴保存，Y 扫描值直接作为 rf_coil DC offset。
  - 每个 XY 点扫描 0–340°、20° 步进的 18 个 RF Burst 相位点。
  - Z 最优控制只在实验开始时启动并持续运行，Y RF 每个相位点使用 OFF→ON 重新等待共同触发。
  - 拟合失败点保留原始曲线并在参数地图中标记为无效，不自动选择最佳平衡点。
---

# Mx Z 最优控制 XY 平衡场 RF 相位响应

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-rf-phase-response`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制、共同触发、温控和 HF2 工作点，
但将 X/Y DC 补偿值改为二维网格，并在每个网格点上扫描 Y RF Burst 的触发相位。

X 由 `X_magnetic_field` 通道输出 DC；Y 与 `rf_coil` 共用物理通道，Y 网格值作为
RF 正弦的 DC offset。Y RF 使用固定频率和固定幅度，只有 Burst phase 在相位扫描中改变。
`Time_sequence_2` 的下降沿同时启动 Z 控制和 Y RF Burst，实体分配线需要运行前人工确认。

## 扫描与采集

默认 X/Y 均为 `-0.05 V` 至 `+0.05 V`、11 点。采集顺序为 X 外层、Y 蛇形。
每个 XY 点执行 18 个相位点 `0°、20°、…、340°`，实际顺序为相差 180° 的相位成对排列。

每个相位点先关闭温控并重新 arm Y RF，等待稳定后采集 Demod0 R；R 标准差超过门槛时保存
该次尝试并重采，最多执行配置的次数。每个 XY 点完成首轮相位扫描后，使用 R 相位曲线的
一阶谐波稳健残差（MAD）识别偏离较大的跨相位点；异常点不超过
`PHASE_OUTLIER_MAX_REACQUIRE_POINTS` 时逐点重新 arm RF 并重采一次，重采结果替换进入拟合，
原始文件、重采文件、重采前后残差和掩码均写入 `xy_rf_phase_response_scan.npz`。
异常点超过上限时不自动重采，但会保留检测报告。每个 XY 点完成后用仓库现有
`fit_phase_scan()` 拟合：

```text
abs(C + A*sin(phi-phi0))
```

同时保存 `C + A*abs(sin(phi-phi0))` 诊断拟合。拟合不通过的 XY 点不会中止网格采集。
重采后仍被检测为异常的相位点（包括超过重采上限而未重采的情况）会写入拒绝原因，
该 XY 点在汇总地图中标记为无效，但其全部原始曲线和拟合参数仍会保留。

采集时的跨相位重测和该 XY 点的正式接受判定均使用同一套非线性色散模型；
重测判定使用模型残差的稳健 MAD，最终判定额外检查 R²、协方差和响应信噪比。
旧的绝对值正弦拟合只作为兼容诊断保存，不再决定是否重测或接受该点。

离线分析阶段对保存的 Demod0 R 曲线重新使用非线性色散模型：

```text
B_eff(phi) = sqrt(A_rf^2 + C_res^2
                  + 2 A_rf C_res cos(phi - phi_res))
R(phi) = |scale (B_eff - B0) / ((B_eff - B0)^2 + width^2)|
```

其中 `A_rf` 取运行配置中的实际 RF 相位扫描幅度，`scale`、`B0`、`width`、
`C_res` 和 `phi_res` 在每个 XY 点独立拟合。该拟合只使用 Demod0 R；不需要
`kx`、`ky` 或 RF 转换系数的预先标定，X/Y 电压只作为网格坐标。该结果是
响应空间中的等效参数，不能单独用来唯一反推出物理的 `kx`、`ky` 或 RF 标定。
分析器保留采集时的绝对值正弦结果作为兼容对照，同时输出
`xy_rf_phase_response_nonlinear_maps.png` 和非线性参数地图。R²、响应峰谷差与
中位点噪声门槛仍按运行配置执行；门槛失败的点保留拟合参数和拒绝原因。

## 数据与分析

```text
data/Mx_Z_Optimal_Control_XY_RF_Phase_Response/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    grid_X###_Y###_phase_###_attempt_##.npz
    xy_rf_phase_response_scan.npz
  results/
    xy_rf_phase_response_maps.png
    xy_rf_phase_response_curves.png
    xy_rf_phase_response_nonlinear_maps.png
    analysis.yaml
    analysis.json
```

分析器只读取运行目录，输出非线性模型的 selected phase、residual RF amplitude、resonance
amplitude、width、R²、RMSE 和拟合有效性二维地图，并列出全部拟合失败点；旧正弦模型结果
作为兼容对照保留。不自动选择最佳 XY 点，也不执行 RF 幅度灵敏度或噪声分析。

## 安全结束状态

正常、取消和异常路径均关闭归零 `Time_sequence_2`、X 场和 Y RF，恢复 GS200 运行前状态，
并将温控恢复为 `5 V DC + Output ON`。Z 最优控制波形和 Output 保持，Pump/Probe、Pump 载波/门控
和 HF2 设置保留。
