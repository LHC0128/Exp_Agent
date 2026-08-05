---
title: Mx 高主场 Z 磁场频率标定
type: Mx_Z_Field_Calibration
scan_mode: nested_scan
defaults:
  FIXED_PARAMS.main_magnetic_field: 9.3
  ZERO_BIAS_CENTER_FREQUENCY_HZ: 90000.0
  Z_INITIAL_HZ_PER_V: 10621.690594509037
  Z_BIAS_START_V: -3.0
  Z_BIAS_STOP_V: 3.0
  Z_BIAS_STEP_V: 1.0
  FREQUENCY_HALF_WIDTH_HZ: 4000.0
  FREQUENCY_STEP_HZ: 100.0
  Y_RF_AMPLITUDE_VPP: 0.05
mapping_keys:
  main_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: scan
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
  - 9.3 mA 高主场下，Z 偏置 -3 至 +3 V 不跨过总场零点，使用普通线性模型。
  - 采集工作流不执行共振拟合；全部 Lorentzian 和线性标定均由离线分析器完成。
  - 每个频点独立关闭温控采集，随后恢复 5 V 并等待 1 s。
---

# Mx 高主场 Z 磁场频率标定

## 目标和构型

本实验在 Mx 构型下标定 `Z_magnetic_field`（DG4000 CH1）DC 电压与 Y RF 共振中心频率之间的关系：

\[
f_0(V_Z)=K_Z V_Z+f_{0V}.
\]

主场与 Pump 光沿 Z，Probe 光沿 X；`rf_coil` 沿 Y，HF2 Demod0 只采集 R。默认主场为 9.3 mA，零偏预测中心为 90 kHz。`Z_INITIAL_HZ_PER_V=10621.690594509037` 只用于确定每个 Z 电压的局部扫频窗口，不作为最终标定结论。

`FIXED_PARAMS.main_magnetic_field` 与 `ZERO_BIAS_CENTER_FREQUENCY_HZ` 均允许设置为 `0`。当零偏预测中心为 `0 Hz` 时，采集前使用首个 Z 扫描点的预测中心初始化 Y RF 与 HF2；所有实际扫频点仍必须大于 `0 Hz`，否则预检会拒绝运行。

## 扫描流程

Z 偏置按单向七点扫描：

```text
-3, -2, -1, 0, +1, +2, +3 V
```

GUI 必须明确选择正 Z 电压使预测中心升高或降低。每个 Z 点的预测中心为：

\[
f_\mathrm{pred}=f_\mathrm{GUI}+sK_\mathrm{initial}V_Z,
\]

其中 \(s=\pm1\)。每个 Z 点在预测中心 ±4 kHz 内以 100 Hz 步进扫描。设置新的 Z DC 偏置后不执行专用稳定等待。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率、0.05 Vpp 幅度，并同步 HF2 振荡器。
2. 将温控开关设置为 0 V DC 且保持 Output ON。
3. 等待 0.1 s，再等待 0.05 s 频点稳定时间。
4. 采集 0.1 s Demod0 R。
5. 在 `finally` 中恢复温控为 5 V DC + Output ON，并固定等待 1 s。

若单次记录 `std(R)>0.01 V`，完整保存该尝试并重采，最多 3 次。采集阶段不检查共振峰，不执行 Lorentzian 拟合，也不扩展扫频窗口。

## 离线分析

分析器从 `raw/z_scan_index.npz` 和各 `raw/z_*/frequency_scan.npz` 重建七条频率响应，对每条响应拟合带基线 Lorentzian。正式中心只执行拟合所需的基础有效性检查：

- 拟合正常收敛且输入中至少有 5 个有限频率点；
- HWHM 大于频率步进且小于扫描跨度的一半；
- 中心位于扫描窗口内，且距边缘不少于一个 HWHM。

单曲线 R²、中心标准不确定度和 HWHM 相对不确定度只作为诊断结果，不再参与中心取舍。离散最大值也只作为诊断。有效中心使用标准不确定度加权线性拟合；线性拟合在数学上至少需要 2 个有效中心，最终线性 R² 仍需不低于 0.99。不再计算或输出有效 Z 点数与有效 Z 跨度。

结果写入：

```text
data/Mx_Z_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    z_scan_index.npz
    z_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    z_frequency_calibration.png
    z_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Z_Field_Calibration.py` 和 `experiments/Mx_Z_Field_Calibration_plot.py`。

## 安全结束状态

正常、取消和异常路径均归零关闭 Z DC 与 Y RF，保持 X 场关闭，将温控恢复为 5 V DC + Output ON。主场、Pump/Probe 光功率、Pump 载波/门控和 HF2 配置按标准策略保留；正常结束只断开 TEC。
