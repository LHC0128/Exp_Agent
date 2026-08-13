---
title: Mx Keithley 6221 主磁场频率标定
type: Mx_Keithley_6221_Main_Field_Calibration
scan_mode: nested_scan
defaults:
  KEITHLEY_CURRENT_START_MA: 20.0
  KEITHLEY_CURRENT_STOP_MA: 50.0
  KEITHLEY_CURRENT_STEP_MA: 10.0
  KEITHLEY_CURRENT_RANGE_MA: 100.0
  KEITHLEY_INITIAL_HZ_PER_MA: 650.0
  FREQUENCY_HALF_WIDTH_HZ: 10000.0
  FREQUENCY_STEP_HZ: 500.0
  KEITHLEY_COMPLIANCE_V: 1.0
  KEITHLEY_CURRENT_SETTLE_TIME_S: 0.5
  FIXED_PARAMS.main_magnetic_field: 0.0
mapping_keys:
  keithley_6221_main_field:
    role: scan
  main_magnetic_field:
    role: forced_off
  Z_magnetic_field:
    role: fixed_off
  X_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
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
    role: detection
required_devices:
  - Keithley 6221
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - Keithley 6221 与 GS200 控制同一只 Z 主线圈，但本实验只允许 6221 物理接入。
  - GS200 必须物理断开；仅关闭输出不能替代物理断开确认。
  - 最终线性拟合同时求解斜率与截距，不强制通过原点。
---

# Mx Keithley 6221 主磁场频率标定

## 接线与启动条件

Keithley 6221 通过 `keithley_6221_main_field` 独占 Z 主线圈。GS200 使用原有
`main_magnetic_field` mapping，但必须从该线圈物理断开。不得把两台电流源并联在线圈上。

启动前必须勾选 `CONFIRM_GS200_PHYSICALLY_DISCONNECTED`。初始化会将 GS200
设置为 `0 mA + 输出关闭`，并将 `Z_magnetic_field` 与 X 场归零关闭。6221 配置为
由 `KEITHLEY_CURRENT_RANGE_MA` 选择的固定量程、关闭自动量程、`SLOW` 响应、
关闭模拟滤波和 `1 V` Compliance。GUI 提供从 `2 nA` 到 `100 mA` 的九个
标准档位；所选档位必须覆盖扫描起点和终点的绝对值，否则预检失败。

## 扫描

仓库当前保存的 6221 电流轴为：

```text
20, 30, 40, 50 mA
```

预测关系仅用于选择局部扫频窗口：

```text
f_pred = 650 Hz/mA * I
```

对应预测中心为 `13、19.5、26、32.5 kHz`。每组在中心 `+/-10 kHz` 范围内以
`500 Hz` 步进扫描，共 `41` 个频点和 `164` 个 Y RF 采集点。电流从 0 mA
直接设置到首点 20 mA，不做阶梯爬升；每次改变电流后等待 `0.5 s`。

每个电流稳定后及每个 Y RF 采集点前后均查询 Compliance。`CALC3:LIM:FAIL?`
返回命中时，工作流立即尝试 `ABORT`、关闭 6221 输出、归零并终止实验。

## 分析与结果

每组频扫离线拟合带基线 Lorentzian，并使用共振中心不确定度执行加权线性拟合：

```text
f_Hz = K_f_Hz_per_mA * current_mA + f_0mA_Hz
```

结果包含 `K_f_Hz_per_mA`、`f_0mA_Hz`、协方差、参数不确定度、R2、残差和逐电流
拟合质量。默认要求线性 `R2 >= 0.99`。图中的标注均使用英文和 `paper` 样式。

```text
data/Mx_Keithley_6221_Main_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    keithley_main_field_scan_index.npz
    current_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    calibration_results.npz
    frequency_response_fits.png
    keithley_main_field_frequency_calibration.png
    keithley_main_field_frequency_residuals.png
```

采集入口为 `experiments/Mx_Keithley_6221_Main_Field_Calibration.py`，离线分析入口为
`experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py`。

## 安全结束状态

正常完成、取消、异常、连接后配置失败和 Compliance 命中时，均先关断并归零 6221，
再关断并归零 GS200，随后关闭 Y RF、Z/X 辅助场并恢复温控。不会恢复任一电流源
运行前的非零状态。
