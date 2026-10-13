---
title: Mx Z 直流控制噪声谱
type: Mx_Z_Noise_Spectrum
execution_mode: typed_workflow
scan_mode: point_by_point
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
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - Z 电压严格使用保存的 Mx Z 标定斜率和截距反算，不把 0 V 强制定义为零失谐。
  - 锁相放大器 振荡器在全部 Z 点固定为配置的参考频率，只采集 Demod0 R。
  - 每点关闭温控后按配置等待；该等待同时承担 Z DC 与温控关闭后的稳定时间。
  - 每点恢复温控为 5 V DC + Output ON 后按参数配置等待。
  - 每个 Z 点按配置时长采集一条记录，不做重复平均。
experiment_id: mx-z-noise-spectrum
defaults_file: params/experiments/mx-z-noise-spectrum.yaml
---

# Mx Z 直流控制噪声谱

当前默认参数：[参数文件](../params/experiments/mx-z-noise-spectrum.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

本实验在 Mx 高主场构型下用恒定 Z 场代替 X/Y 交流控制载波：主磁场和
Pump 光沿 Z，Probe 光沿 X；`Z_magnetic_field` 输出逐点恒定的 DC 电压，X/Y
磁场通道始终归零关闭。Pump AOM 保持 100 MHz 连续载波，`Time_sequence`
保持 5 V DC 且 Output ON。

锁相放大器 Demod0 使用 Input 1，振荡器在整个扫描中固定为配置的零偏参考频率，DAQ
只订阅 `sample.r`。每个 Z 点按配置时长采集一条 R 时间序列，不做重复平均。

## Z 控制轴

目标控制量是相对 锁相放大器 固定参考频率的失谐：

\[
\Delta f = f_0(V_Z)-f_{\mathrm{HF2}}.
\]

标定来源由参数文件指定。以下为历史 `Mx_Z_Field_Calibration/0717_154520_mx_z_cal` 的换算示例，非当前默认参数：

\[
f_0(V_Z)=24224.007001623544\,V_Z+90302.74646653689\ \mathrm{Hz}.
\]

扫描轴范围与点数读取参数文件。实际运行使用所选标定的斜率 `K_Z`、截距 `f_0V` 和配置的 锁相放大器 参考频率，按下式反算：

\[
V_Z=\frac{f_{\mathrm{HF2}}+\Delta f-f_{0V}}{K_Z}.
\]

因此首点不是强制 0 V。模型预检会对反算的首末电压调用
`validate_safety_limit("Z_magnetic_field", value)`，每次实际输出前再次校验。

## 采集流程

1. 连接 GS200、Z/XY 场 DG4000、光功率 DG900、Pump DG4000、温控开关
   DG900、TEC103 和 锁相放大器；同一物理设备通过 `DeviceSession` 复用。
2. 按 `params/devices.yaml` 的 `reference_clock` 设置并回读全部已连接设备的参考时钟；任一设备
   不一致即在输出配置前停止。
3. 按参数文件配置主场、Pump/Probe 光功率、温度和 Pump 载波；Pump 门控保持 5 V DC ON。
4. 按参数文件配置 锁相放大器 参考频率、请求采样率、时间常数和滤波阶数；配置文件保存硬件返回的实际采样率和相位快照。
5. 对各目标失谐依次执行：
   - 严格反算并设置 Z DC；
   - 将温控开关设为 0 V DC 且保持 Output ON；
   - 按配置统一等待，不再增加其他点间稳定等待；
   - 按硬件实际采样率按配置时长采集 Demod0 R；
   - 在 `finally` 中恢复温控为 5 V DC + Output ON，并按配置等待；即使采集异常也执行恢复和等待。

扫描时长由点数、显式等待与采集时长累加确定，不含设备连接和首次温度稳定时间。

## 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

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

原始文件保存 R、时间轴、目标失谐、绝对预测共振频率、实际 Z 电压和 锁相放大器 实际
采样率。所有图中的标题、坐标轴和图例均使用英文。

## 安全结束状态

正常完成、异常、取消和 Ctrl+C 都调用共享 `run_safety_shutdown()`：Z/X/Y 场归零
并关闭，温控恢复为 5 V DC + Output ON，只断开 TEC 通信；主场、Pump/Probe 光
功率、Pump 100 MHz、`Time_sequence=5 V DC ON` 和全部 锁相放大器 设置保持不变。
