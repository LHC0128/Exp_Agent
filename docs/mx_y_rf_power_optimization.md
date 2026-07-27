---
title: Mx Y RF 光功率灵敏度优化
type: Mx_Y_RF_Power_Optimization
scan_mode: nested_scan
defaults:
  PUMP_POWER_START_V: 0.2
  PUMP_POWER_STOP_V: 0.8
  PUMP_POWER_POINTS: 7
  PROBE_POWER_START_V: 0.1
  PROBE_POWER_STOP_V: 0.5
  PROBE_POWER_POINTS: 7
  Y_RF_FREQUENCY_HZ: 90000.0
  Y_RF_NT_PER_VPP: 1517.79147
mapping_keys:
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: scan
  Probe_laser_power:
    role: scan
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
  - TEC103
learned_notes:
  - 完整复用 Mx Y 向 RF 场灵敏度的幅度等效线宽、R 响应和零 RF 噪声算法。
  - Pump 为外层、Probe 为内层蛇形扫描，矩阵仍按两条升序功率轴保存。
  - 每个功率点开始前强制恢复 Y RF 正弦波和 HF2 响应配置，避免噪声采集状态泄漏。
  - 二维网格使用 tqdm 显示完成比例、处理速度和预计剩余时间。
  - 所有结束路径恢复 Pump 0.5 V、Probe 0.3 V 并保持输出开启。
---

# Mx Y RF 光功率灵敏度优化

## 实验目标

在主磁场和 Pump 光沿 Z、Probe 光沿 X、待测 RF 场沿 Y 的 Mx 构型下，二维扫描 DG900 的 Pump/Probe 光功率控制电压。每个功率组合执行一次完整的 Y RF 幅度响应和零 RF 噪声测量，以校准后的平坦频段灵敏度中位数寻找最优工作点。

## 扫描与采集

- Pump 扫描范围为 0.2–0.8 V，共 7 点；Probe 扫描范围为 0.1–0.5 V，共 7 点。
- 采集顺序固定为 Pump 外层、Probe 内层蛇形扫描，不随机化也不重复整个网格。
- 设置新光功率后不增加独立等待；首个 RF 幅度点仍执行参考实验已有的温控关闭前导和响应稳定时间。
- 每个功率点使用 90 kHz Y RF、21 个带符号幅度点和 5 段 1 s 的零 RF 噪声。
- 每个功率点开始幅度扫描前，工作流都会重新配置 Y RF 为 90 kHz SINE，并把 HF2 Demod0 恢复为响应采样率、1 ms 时间常数和响应滤波阶数；因此上一点的零 RF DC 和高采样率噪声配置不会泄漏到下一点。
- 进入 Pump/Probe 二维网格后，`tqdm` 按完整功率点更新完成比例、处理速度和 ETA；GUI 日志每完成一点会输出一行可持续刷新的进度快照。
- 单个 R 点连续三次超过 `std(R)` 阈值时，该功率组合标记为无效并继续；设备通信、配置或安全错误立即终止实验。

原始数据按以下结构保存：

```text
data/Mx_Y_RF_Power_Optimization/<run>/
  experiment_config.yaml
  raw/
    power_grid.npz
    point_manifest.yaml
    points/
      pump_000_probe_000/
        amplitude_scan.npz
        amplitude_*_attempt_*.npz
        noise_*.npz
  results/
    optimization.yaml
    optimization.json
    optimization.npz
    sensitivity_heatmap.png
    slope_heatmap.png
    hwhm_heatmap.png
    best_point_full_analysis.png
    points/
      pump_000_probe_000/
        analysis.yaml
        full_analysis.png
```

`point_manifest.yaml` 在每个功率点后更新，因此取消或异常后仍可对已经完整保存的功率点手动重新分析。

## 最优点判据

离线分析逐点复用 `mx-y-rf-sensitivity` 的绝对值色散拟合、PSD、幅度等效 HWHM 和自动平坦段识别。每个采集完整的功率点都会在 `results/points/<point>/full_analysis.png` 生成双面板完整分析图；上图标题同时标出 Pump/Probe 控制电压和质量状态。被严格质量门槛拒绝的点仍会继续计算仅供排障的诊断谱，标题标为 `DIAGNOSTIC ONLY - INVALID`，其数值不会写入有效矩阵、不会参与排序。

只有响应拟合成功且平坦段识别成功的点才参与排序，目标为最小化 `flat_median_ft_per_sqrt_hz`。分析只报告最优点，不会把仪器自动切换到该功率。

若没有有效点，分析器仍保存矩阵和逐点失败原因，并明确报告未找到可用最优点。

## 安全结束状态

正常、取消和异常路径均关闭并归零 X/Y 场、恢复温控开关为 5 V DC + ON，并恢复 DG900 Pump=0.5 V、Probe=0.3 V 且保持两路输出开启。Pump 100 MHz 载波、主场和 HF2 设置按参考实验策略保留，正常结束仅断开 TEC。
