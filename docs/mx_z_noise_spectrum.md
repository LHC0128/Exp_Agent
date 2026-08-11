---
title: Mx Z 直流控制噪声谱
type: Mx_Z_Noise_Spectrum
execution_mode: typed_workflow
scan_mode: point_by_point
defaults:
  TARGET_DETUNING_START_HZ: 0.0
  TARGET_DETUNING_STOP_HZ: 50000.0
  TARGET_DETUNING_POINTS: 500
  ZERO_BIAS_REFERENCE_FREQUENCY_HZ: 90000.0
  Z_CALIBRATION_HZ_PER_V: 24224.007001623544
  Z_CALIBRATION_INTERCEPT_HZ: 90302.74646653689
  ACQUISITION_DURATION_S: 1.0
  REQUESTED_RATE_SA_S: 100000.0
  TEMP_SWITCH_OFF_SETTLE_S: 0.3
  TEMP_SWITCH_ON_SETTLE_S: 1.0
mapping_keys:
  main_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: scan
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
  - Z 电压严格使用保存的 Mx Z 标定斜率和截距反算，不把 0 V 强制定义为零失谐。
  - HF2 振荡器在全部 Z 点固定为 90 kHz，只采集 Demod0 R。
  - 每点关闭温控后只等待 0.3 s；该等待同时承担 Z DC 与温控关闭后的稳定时间。
  - 每点恢复温控为 5 V DC + Output ON 后默认等待 1 s。
  - 每个 Z 点只采一条 1 s 记录，不做重复平均。
---

# Mx Z 直流控制噪声谱

## 目标与构型

本实验在 Mx 高主场构型下用恒定 Z 场代替 X/Y 交流控制载波：主磁场和
Pump 光沿 Z，Probe 光沿 X；`Z_magnetic_field` 输出逐点恒定的 DC 电压，X/Y
磁场通道始终归零关闭。Pump AOM 保持 100 MHz 连续载波，`Time_sequence`
保持 5 V DC 且 Output ON。

HF2 Demod0 使用 Input 1，振荡器在整个扫描中固定为零偏参考频率 90 kHz，DAQ
只订阅 `sample.r`。每个 Z 点采集一条 1 s 的 R 时间序列，不做重复平均。

## Z 控制轴

目标控制量是相对 HF2 固定参考频率的失谐：

\[
\Delta f = f_0(V_Z)-f_{\mathrm{HF2}}.
\]

默认使用 `Mx_Z_Field_Calibration/0717_154520_mx_z_cal` 的成功标定结果：

\[
f_0(V_Z)=24224.007001623544\,V_Z+90302.74646653689\ \mathrm{Hz}.
\]

扫描轴为 0–50 kHz、500 点。每个目标失谐严格反算为：

\[
V_Z=\frac{90000\ \mathrm{Hz}+\Delta f-90302.74646653689\ \mathrm{Hz}}
{24224.007001623544\ \mathrm{Hz/V}}.
\]

因此首点不是强制 0 V。模型预检会对反算的首末电压调用
`validate_safety_limit("Z_magnetic_field", value)`，每次实际输出前再次校验。

## 采集流程

1. 连接 GS200、Z/XY 场 DG4000、光功率 DG900、Pump DG4000、温控开关
   DG900、TEC103 和 HF2；同一物理设备通过 `DeviceSession` 复用。
2. 按 `params/devices.yaml` 的 `reference_clock` 设置并回读全部已连接设备的参考时钟；任一设备
   不一致即在输出配置前停止。
3. 配置 Mx 工作点：主场 9.333 mA、Pump/Probe 0.5/0.3 V、气室 120 °C、
   Pump 100 MHz/0.18 Vpp、Pump 门控 5 V DC ON。
4. 配置 HF2 Input 1 和 Demod0：振荡器固定 90 kHz，请求采样率 100 kSa/s、
   TC 1 µs、四阶滤波；配置文件保存硬件返回的实际采样率和相位快照。
5. 对 500 个目标失谐依次执行：
   - 严格反算并设置 Z DC；
   - 将温控开关设为 0 V DC 且保持 Output ON；
   - 统一等待 0.3 s，不再增加其他点间稳定等待；
   - 按硬件实际采样率采集 1 s Demod0 R；
   - 在 `finally` 中恢复温控为 5 V DC + Output ON，并等待 1 s；即使采集异常也执行恢复和等待。

500 点的显式等待与采集时间约为 1150 s，不含设备连接和首次温度稳定时间。

## 离线分析

分析器只读取指定运行目录，不连接仪器。每个 R 时间序列使用 Welch 方法计算
PSD，组成

\[
S_{S_1}(\omega,\Delta f).
\]

分析时从保存的 Z 电压重新计算控制轴：

\[
\Omega_{\mathrm{Ctrl}}
=K_ZV_Z+f_{0V}-f_{\mathrm{HF2}},
\]

并检查它与采集时的目标失谐在 1 µHz 内一致。随后复用 XY 控制噪声谱的共享
四参数 Lorentzian 模型：

\[
S_{S_1}(\Omega_{\mathrm{Ctrl}};\omega)
=D+A\frac{\gamma^2+\omega^2}
{\left(\gamma^2-\omega^2+(\Omega_{\mathrm{Ctrl}}+\Delta\omega)^2\right)^2
+4\omega^2\gamma^2}.
\]

拟合结果按原实验约定解释为：

- `Amp → S_beta`：可控噪声谱；
- `D → N_S1`：不可控噪声与电子噪声基线；
- `gamma`：线宽；
- `dw`：控制频率偏移。

## 输出

```text
data/Mx_Z_Noise_Spectrum/MMDD_HHMMSS_mx_z_noise/
  experiment_config.yaml
  raw/
    z_noise_scan_axes.npz
    waveform_Z0000.npz
    ...
    waveform_Z0499.npz
  results/
    psd_matrix.npz
    popt_fit.npz
    noise_spectra.npz
    noise_spectra.csv
    z_control_axis.png
    noise_spectrum_2d.png
    noise_spectra_extracted.png
    analysis.yaml
    analysis.json
```

原始文件保存 R、时间轴、目标失谐、绝对预测共振频率、实际 Z 电压和 HF2 实际
采样率。所有图中的标题、坐标轴和图例均使用英文。

## 安全结束状态

正常完成、异常、取消和 Ctrl+C 都调用共享 `run_safety_shutdown()`：Z/X/Y 场归零
并关闭，温控恢复为 5 V DC + Output ON，只断开 TEC 通信；主场、Pump/Probe 光
功率、Pump 100 MHz、`Time_sequence=5 V DC ON` 和全部 HF2 设置保持不变。
