---
title: Mx Y 最优控制 RF 频率响应（观察版）
type: Mx_Y_Optimal_Control_RF_Frequency_Response
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  FREQUENCY_START_HZ: 100.0
  FREQUENCY_STOP_HZ: 10000.0
  FREQUENCY_POINTS: 100
  FREQUENCY_RF_AMPLITUDE_VPP: 0.01
  PHASE_SCAN_START_DEG: 0.0
  PHASE_SCAN_STOP_DEG: 350.0
  PHASE_SCAN_STEP_DEG: 10.0
  CORRECTED_CONTROL_SOURCE_RUN: obbv6
mapping_keys:
  main_magnetic_field: fixed
  Z_magnetic_field: optimal_control
  Time_sequence_2: common_trigger
  X_magnetic_field: fixed
  rf_coil: scan
  Pump_laser_power: fixed
  Probe_laser_power: fixed
  Pump_modulation: fixed
  Time_sequence: fixed
  Temp_Switch: point_gate
  lockin_r: detection
required_devices: [GS200, DG900, DG4000, HF2]
learned_notes:
  - 每个频率点完整采集 Y RF Burst 相位 0–350° 的 Demod0 R。
  - 当前观察版不做相位拟合、峰值、带宽或 Bloch 拟合。
---

# Mx Y 最优控制 RF 频率响应（观察版）

实验在 Z 向闭环最优控制波形保持运行时，扫描 Y RF 频率并只记录 Demod0 R 的完整 Burst 相位响应。
