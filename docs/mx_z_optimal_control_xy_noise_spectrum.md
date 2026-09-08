---
title: Mx Z 最优控制 XY 噪声谱
type: mx-z-optimal-control-xy-noise-spectrum
scan_mode: nested_scan
defaults:
  X_FIELD_START_V: 0.0
  X_FIELD_STOP_V: 0.02
  X_FIELD_POINTS: 11
  Y_FIELD_START_V: -0.02
  Y_FIELD_STOP_V: 0.0
  Y_FIELD_POINTS: 11
  NOISE_N_AVG: 5
  NOISE_DURATION_S: 1.0
  LOW_FREQ_SKIP_HZ: 3.0
  NOISE_BAND_MAX_HZ: 200.0
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - XY 坐标为信号发生器电压，当前没有 X/Y 电压到磁场的独立标定。
---

# 实验目标

在最新 `corrected_run` Z 最优控制波形保持输出的条件下，扫描 X/Y 直流补偿偏置，测量 HF2 Demod0 的 R 噪声谱。主排序指标为指定频段内的 R ASD 中位数，单位为 `V/√Hz`；本实验不输出未经标定的 `nT/√Hz`。

# 接线与状态

- Z 方向使用 `Z_magnetic_field` DG4000 CH1 输出闭环冻结的最优控制任意波，输出保持开启。
- X 方向使用 DG4000 `CH1` 输出 DC 偏置；Y 方向使用同一 DG4000 的 `CH2` 输出 DC 偏置。
- Y 通道与 RF 线圈共用物理通道。噪声采集前关闭 Y Burst 和 Modulation，只保留当前 Y DC；Y 偏置为 0 V 时关闭该通道输出。
- HF2 仅采集 Demod0 `R(t)`，不执行 RF 相位校准或 RF 幅值扫描。
- Pump/Probe 与 Pump 调制按 Mx Z 最优控制公共工作点配置。

# 采集时序

每个蛇形 XY 网格点设置 X/Y DC 后，重复 `NOISE_N_AVG` 次：确认 Y 纯 DC、将 `Temp_Switch` 设为 0 V、等待关闭延迟、采集 `NOISE_DURATION_S` 的 R(t)，然后恢复 `Temp_Switch=5 V` 并等待恢复延迟。每条记录保存实际采样率和 XY/RF/控制/温控状态元数据（`temperature_gate_state=off_during_acquire_then_on`）。取消、通信异常和异常退出均执行安全停机。

# 离线分析

对每个点的所有噪声记录使用 Hann 窗、`detrend="constant"`、`scaling="density"` 的 Welch 方法计算 PSD，先对重复记录的 PSD 求平均，再开方得到 ASD。统计掩码为

```text
LOW_FREQ_SKIP_HZ <= frequency <= NOISE_BAND_MAX_HZ
```

掩码内全部频点（包括 100 Hz、150 Hz 等窄带谱线）参与中位数。分析同时保存该频段的最大 ASD 及其频率，用于诊断确定性谱线。

# 输出

```text
data/Mx_Z_Optimal_Control_XY_Noise_Spectrum/<run>/
  experiment_config.yaml
  raw/source_corrected_control_waveform.npz
  raw/source_manifest.yaml
  raw/xy_bias_grid.npz
  raw/point_manifest.yaml
  raw/points/x_###_y_###/noise_###.npz
  results/noise_median_matrix.npz
  results/analysis.yaml
  results/analysis.json
  results/noise_median_heatmap.png
  results/noise_spectrum_best.png
```

分析器也可通过 `analyze_legacy_xy_rf_noise_run()` 读取旧 `Mx_Z_Optimal_Control_XY_RF_Sensitivity` 运行目录的噪声子目录，用于预实验趋势分析；旧数据仍只代表原始 Demod0 R 噪声，不能证明控制改善，也不能换算 X/Y 物理磁场。

# 预实验提示

旧 `[-0.01,+0.01] V` 网格在 `3–200 Hz` 的最低点位于 `X=+0.010 V, Y=-0.010 V` 边界，因此正式默认网格扩展为 X `0…+0.02 V`、Y `-0.02…0 V`。统计上限必须作为参数保存在运行配置中，因为不同截止频率可能给出不同最佳点。
