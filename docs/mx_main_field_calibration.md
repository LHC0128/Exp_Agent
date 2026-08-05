---
title: Mx 主磁场频率标定
type: Mx_Main_Field_Calibration
scan_mode: nested_scan
defaults:
  MAIN_FIELD_START_MA: 7.0
  MAIN_FIELD_STOP_MA: 10.0
  MAIN_FIELD_STEP_MA: 0.5
  MAIN_FIELD_REFERENCE_CURRENT_MA: 9.333
  MAIN_FIELD_REFERENCE_FREQUENCY_HZ: 90000.0
  MAIN_FIELD_INITIAL_HZ_PER_MA: 9643.201542912248
  FREQUENCY_HALF_WIDTH_HZ: 10000.0
  FREQUENCY_STEP_HZ: 500.0
  Y_RF_AMPLITUDE_VPP: 0.05
  GYROMAGNETIC_RATIO_HZ_PER_NT: 7.0
  SETTLE_TIME_S: 0.1
mapping_keys:
  main_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
  X_magnetic_field:
    role: fixed_off
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
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - TEC103 为可选控制设备；COM3 被外部温控软件占用时跳过设温和稳定等待。
  - 7 至 10 mA 范围不跨越总场零点，使用普通线性模型。
  - 设置主场电流后不单独等待；首个频点的温控关闭统一等待同时覆盖主场稳定。
  - 频率到磁场使用可配置的 GYROMAGNETIC_RATIO_HZ_PER_NT，默认 7.0 Hz/nT。
---

# Mx 主磁场频率标定

## 目标和构型

本实验在 Mx 构型下标定 GS200 `main_magnetic_field` 电流与 Y RF 共振中心频率、主磁场大小之间的关系：

\[
f(I)=K_f I+f_0,
\qquad
B(I)=\frac{f(I)}{\gamma}=K_B I+B_0.
\]

主场与 Pump 光沿 Z，Probe 光沿 X，`rf_coil` 沿 Y。DG4000 `Z_magnetic_field` 在整个实验中固定为 0 V 且 Output OFF，避免辅助 Z 场混入 GS200 标定。

## 扫描和采集

GS200 默认按单向七点扫描：

```text
7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0 mA
```

每个电流点的局部扫频预测中心为：

\[
f_\mathrm{pred}(I)=90\,\mathrm{kHz}
+9643.201542912248\,\mathrm{Hz/mA}\,(I-9.333\,\mathrm{mA}).
\]

每个预测中心在 ±10 kHz 内以 500 Hz 步进扫描。`MAIN_FIELD_INITIAL_HZ_PER_MA` 只用于定位局部扫频窗口，不作为最终标定结果。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率和 0.05 Vpp 幅度，并同步 HF2 振荡器。
2. 将温控开关设置为 0 V DC、Output ON。
3. 只等待一次 `SETTLE_TIME_S=0.1 s`，同时覆盖温控关闭、RF 频点稳定以及每个电流点首频点的主场稳定。
4. 采集 0.1 s Demod0 R。
5. 在 `finally` 中恢复温控为 5 V DC、Output ON，并等待 1 s。

若单次记录 `std(R)>0.01 V`，完整保存该尝试并重采，最多三次。采集阶段不执行共振拟合。

## 离线分析

分析器从 `raw/main_field_scan_index.npz` 和各 `raw/current_*/frequency_scan.npz` 重建频率响应。每条曲线拟合带基线 Lorentzian，再对有效中心执行标准不确定度加权线性拟合。单曲线中心需位于扫描窗口内且距边缘不少于一个 HWHM，最终线性 R² 默认不低于 0.99。

频率换算磁场时使用 `GYROMAGNETIC_RATIO_HZ_PER_NT`；默认值为 7.0 Hz/nT，并作为配置常数处理，不额外传播其系统不确定度。

结果目录为：

```text
data/Mx_Main_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    main_field_scan_index.npz
    current_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    main_field_frequency_calibration.png
    main_field_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Main_Field_Calibration.py` 和 `experiments/Mx_Main_Field_Calibration_plot.py`。

## 安全结束状态

运行前记录 GS200 的源模式、电流设定、输出开关、量程和限流。正常、取消或异常结束后均恢复该状态；Z 辅助场、X 场与 Y RF 归零关闭，温控恢复为 5 V DC、Output ON。Pump/Probe 光功率、Pump 载波/门控和 HF2 配置按标准策略保留，正常结束只断开 TEC。
