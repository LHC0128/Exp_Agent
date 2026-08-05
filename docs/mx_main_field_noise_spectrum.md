---
title: Mx 主磁场控制噪声谱
type: Mx_Main_Field_Noise_Spectrum
execution_mode: typed_workflow
scan_mode: point_by_point
defaults:
  CONTROL_FREQUENCY_START_HZ: 0.0
  CONTROL_FREQUENCY_STOP_HZ: 50000.0
  CONTROL_FREQUENCY_POINTS: 500
  HF2_REFERENCE_FREQUENCY_HZ: 90000.0
  MAIN_FIELD_CALIBRATION_HZ_PER_MA: 9671.91741380711
  MAIN_FIELD_CALIBRATION_INTERCEPT_HZ: 196.65637261343872
  ACQUISITION_DURATION_S: 1.0
  REQUESTED_RATE_SA_S: 100000.0
  TEMP_SWITCH_OFF_SETTLE_S: 0.3
  TEMP_SWITCH_ON_SETTLE_S: 1.0
mapping_keys:
  main_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: fixed_off
  X_magnetic_field:
    role: fixed_off
  Y_magnetic_field:
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
  - 主场标定来自 0720_124208_mx_main_field_cal，线性拟合成功且 R² 为 0.9999942015。
  - 正控制频率用于噪声分离拟合；原始数据同时保存方向相反的有符号失谐。
  - Z 方向 DG4000 仍连接，但始终保持 0 V 且 Output OFF。
  - 设置 GS200 后不增加独立等待；温控关闭后的 0.3 s 是统一稳定时间。
---

# Mx 主磁场控制噪声谱

## 目标与构型

本实验保持主场和 Pump 光沿 Z、Probe 光沿 X，以 GS200 主场电流代替
`Z_magnetic_field` DG4000 的直流扫描。辅助 Z 场以及 X/Y 场在整个实验中归零并关闭。
Pump AOM 保持 100 MHz 连续载波，`Time_sequence` 保持 5 V DC、Output ON。

HF2 Demod0 使用 Input 1，振荡器固定为 90 kHz，只采集 `sample.r`。每个主场点
采集一条 1 s 时间序列，不做重复平均。

## 标定与扫描轴

使用 `Mx_Main_Field_Calibration/0720_124208_mx_main_field_cal` 的成功结果：

\[
f(I)=9671.91741380711\,I+196.65637261343872\ \mathrm{Hz},
\]

其中 `I` 的单位为 mA。该标定实际覆盖 3–10 mA，新实验除全局 -10–10 mA 安全限值
外，还禁止超出此标定范围。

GUI 使用非负且递增的控制频率：

\[
\Omega_\mathrm{Ctrl}=f_\mathrm{HF2}-f(I),
\qquad
I=\frac{f_\mathrm{HF2}-\Omega_\mathrm{Ctrl}-f_0}{K_f}.
\]

默认 `f_HF2=90 kHz`，`Omega_Ctrl` 从 0 扫至 50 kHz、共 500 点，因此 GS200
从 `9.2849576547 mA` 单向降至 `4.1153518919 mA`。原始数据同时保存
`signed_detuning=f(I)-f_HF2=-Omega_Ctrl`，其范围为 0 至 -50 kHz。

## 采集流程

1. 使用 `DeviceSession` 连接 GS200、场控制 DG4000、光功率 DG900、Pump DG4000、
   温控开关 DG900、TEC103 和 HF2。
2. 在任何输出设置前记录 GS200 的源模式、电流、输出、量程和限流，并按
   `params/clock_sources.yaml` 设置、回读所有已连接时钟。
3. 强制 Z/X/Y 场为 0 V、Output OFF，配置光功率、120 °C 温度、Pump 连续工作点和
   固定 90 kHz Demod0；保存 HF2 相位及硬件返回的实际采样率。
4. 每个控制频率点依次执行：
   - 按标定式反算 GS200 电流并再次执行安全与标定范围校验；
   - 设置电流并保持 GS200 Output ON；
   - 将温控开关设为 0 V DC、Output ON，统一等待 0.3 s；
   - 按 HF2 实际采样率采集 1 s Demod0 R；
   - 在 `finally` 中恢复温控为 5 V DC、Output ON，并等待 1 s。

扫描和等待均支持 GUI 取消。500 点的显式等待与采集时间约为 1150 s，不含连接与
初始温度稳定时间。

## 离线分析

分析器只读取明确的运行目录，不连接仪器。它先用每条记录保存的实际采样率计算
Welch PSD，再从主场电流重建共振频率、正控制频率和负失谐；三者任一不满足固化
标定式即停止分析。

严格递增的正控制频率轴输入共享四参数 Lorentzian 噪声分离器，输出：

- `S_beta`：可控噪声谱；
- `N_S1`：不可控噪声与电子噪声基线；
- `gamma`：线宽；
- `dw`：控制频率偏移。

## 输出

```text
data/Mx_Main_Field_Noise_Spectrum/MMDD_HHMMSS_mx_main_field_noise/
  experiment_config.yaml
  raw/
    main_field_noise_scan_axes.npz
    waveform_I0000.npz
    ...
    waveform_I0499.npz
  results/
    psd_matrix.npz
    popt_fit.npz
    noise_spectra.npz
    noise_spectra.csv
    main_field_control_axis.png
    noise_spectrum_2d.png
    noise_spectra_extracted.png
    analysis.yaml
    analysis.json
```

所有图中的标题、坐标轴、图例和注释均使用英文。

## 安全结束状态

正常完成、异常、取消和 Ctrl+C 均恢复 GS200 的完整运行前状态；Z/X/Y 场归零关闭，
温控恢复为 5 V DC、Output ON。主场之外的 Pump/Probe 光功率、Pump 载波/门控和
HF2 配置按标准策略保留，正常结束只断开 TEC 通信。
