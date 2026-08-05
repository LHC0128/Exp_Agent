---
title: XY 控制 Demod3 R 噪声谱
type: Noise_Spectrum_XY_Demod3_R
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  CONTROL_FREQ_START_HZ: 200.0
  CONTROL_FREQ_STOP_HZ: 20000.0
  CONTROL_FREQ_POINTS: 200
  DEMOD3_FREQ_START_HZ: 200.0
  DEMOD3_FREQ_STOP_HZ: 20000.0
  DEMOD3_FREQ_POINTS: 200
  DEMOD3_FREQ_SETTLE_TIME_S: 0.2
  DEMOD3_ACQUISITION_DURATION_S: 0.1
  TEMP_RECOVERY_BATCH_POINTS: 25
  TEMP_RECOVERY_TIME_S: 2.0
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  lockin_r:
    role: detection
  Temp_Switch:
    role: fixed
required_devices:
  - GS200
  - DG4000
  - DG900
  - HF2
learned_notes:
  - TEC103 为可选控制设备；COM3 被外部温控软件占用时跳过设温和稳定等待。
  - Demod0 Y 通过 AuxOut2 和物理线缆接入 Signal Input 2 DC，再由 Demod3 解调。
  - 每个 Demod3 频点连续采集 0.1 s 的 sample.r，只保存算术平均值，不保存时序数组。
  - 每扫描 25 个 Demod3 频点恢复温控 2 s；切频后的 0.2 s 稳定等待同时覆盖波形上传和温控关闭后的等待。
---

# XY 控制 Demod3 R 噪声谱

## 目的

本实验是 `noise-spectrum-xy` 的独立硬件变体。原实验采集 Demod0 `sample.y`
时域波形并计算 Welch PSD；本实验将 Demod0 Y 通过物理回环送入 Demod3，二维扫描
DirectAW 控制频率和 Demod3 解调频率，直接记录平均 R 矩阵。

平均 R 的单位为 V，不是 V²/Hz。离线结果不会输出 `S_beta` 或 `N_S1`，也不会把
R 幅值标记为 PSD。

## 信号链与校相

```text
Demod0 Y → AuxOut2 → physical cable → Signal Input 2 DC → Demod3 sample.r
```

实验启动时保留两步自动校相：

1. 使用共享 `calibrate_demod_phase()` 校准 Demod0。
2. 使用共享 `calibrate_direct_aw_phase()` 校准 DirectAW 整体相位。

校相完成后不再扫描相位。Demod3 振荡器只扫描 200–20,000 Hz 的解调频率。

## 二维扫描与温控

- 外层控制频率：200–20,000 Hz，200 点；通过 K/B 标定反算 DirectAW 包络。
- 内层 Demod3 解调频率：200–20,000 Hz，200 点。
- 每个频点切频后等待 0.2 s，再连续采集 0.1 s `sample.r`。
- 默认 Demod3 rate 为 4800 Sa/s，单点约包含 480 个 R 样本；只保存其平均值。
- 内层每 25 点为一批。批次开始时关闭温控且不额外等待，批次结束后恢复温控并等待 2 s。

默认理论扫描时间为：

```text
200 × 200 × (0.2 s + 0.1 s) + 200 × 8 × 2 s = 15,200 s
```

即约 4 小时 13 分；升温、校相和仪器通信不包含在内，实际建议预留 4.5–5.5 小时。

## 数据与分析

```text
data/Noise_Spectrum_XY_Demod3_R/MMDD_HHMMSS_demod3_r/
  experiment_config.yaml
  raw/
    demod3_r_mean_matrix.npz
  results/
    demod3_r_matrix.npz
    calibration.npz
    r_fit_params.npz
    r_spectra.npz
    demod3_r_matrix.png
    calibration.png
    r_spectra.png
    analysis.yaml
    analysis.json
```

`raw/demod3_r_mean_matrix.npz` 在每个温控批次后增量更新。它包含平均 R 矩阵、
控制频率轴、DirectAW 包络轴和 Demod3 频率轴，不包含 0.1 s 连续采样的原始数组。

离线分析从 R 矩阵脊线重新拟合包络电压到控制频率的线性标定，然后在每个 Demod3
频率列上拟合 R 随控制频率的洛伦兹响应。结果字段为 `fit_amplitude`、
`r_baseline_V`、`gamma_Hz` 和 `frequency_offset_Hz`。

## 入口与安全收尾

- 采集：`experiments/Noise_Spectrum_XY_Demod3_R.py`
- 分析：`experiments/Noise_Spectrum_XY_Demod3_R_plot.py`

取消、异常和正常结束均恢复温控，并通过共享 `run_safety_shutdown()` 归零关闭 X/Y
DirectAW、外触发、Z 场和 Pump 门控；主磁场、Pump/Probe 光功率、Pump 载波和 HF2
配置保持不变，正常结束只断开 TEC。
