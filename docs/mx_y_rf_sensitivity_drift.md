---
title: Mx Y RF 灵敏度长飘
type: Mx_Y_RF_Sensitivity_Drift
scan_mode: repeated_measurement
defaults:
  DRIFT_INTERVAL_S: 1800.0
  DRIFT_DURATION_S: 86400.0
  DRIFT_START_IMMEDIATELY: true
mapping_keys:
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - 每轮子实验复用 Mx_Y_RF_Sensitivity 的幅度响应和零 RF 噪声分析。
  - 父目录只保存轮次清单、表格和趋势图；原始数据保存在标准子运行目录。
  - 单轮分析失败或 R 点质量门限失败记录为无效轮次并继续；硬件采集异常停止任务。
---

# Mx Y RF 灵敏度长飘

该实验启动后立即执行第一轮，随后按绝对时间间隔重复单轮 `Mx_Y_RF_Sensitivity` 测量。`DRIFT_DURATION_S` 和 `DRIFT_INTERVAL_S` 可在 GUI 中修改，轮数按半开时间区间自动计算；默认间隔为 1800 s、运行时长为 86400 s，因此计划 48 轮。

每轮完成采集后立即调用现有离线分析器，更新父目录中的：

- `results/drift_summary.csv`
- `results/drift_summary.json`
- `results/drift_trend.png`
- `results/drift_trend_clock_time.png`

`drift_trend.png` 使用实验开始后的实际经过时间作为横轴；`drift_trend_clock_time.png` 使用 `Asia/Shanghai` 时区下每轮实际开始测量的日期和时刻作为横轴。两张趋势图均包括平坦频段灵敏度、零点实测斜率方法灵敏度、幅度等效 HWHM、拟合 R² 和无效轮次。幅度等效 HWHM 不是扫频共振线宽。

数据布局：

```text
data/Mx_Y_RF_Sensitivity_Drift/<parent_run>/
  experiment_config.yaml
  raw/drift_manifest.yaml
  results/drift_summary.csv
  results/drift_summary.json
  results/drift_trend.png
  results/drift_trend_clock_time.png
  runs are referenced as data/Mx_Y_RF_Sensitivity/<child_run>/
```
