---
title: Mx 主磁场控制噪声谱
type: Mx_Main_Field_Noise_Spectrum
execution_mode: typed_workflow
scan_mode: point_by_point
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
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - 主场标定来自 0720_124208_mx_main_field_cal，线性拟合成功且 R² 为 0.9999942015。
  - 正控制频率用于噪声分离拟合；原始数据同时保存方向相反的有符号失谐。
  - Z 方向 DG4000 仍连接，但始终保持 0 V 且 Output OFF。
  - 设置 GS200 后不增加独立等待；温控关闭后的配置等待是统一稳定时间。
experiment_id: mx-main-field-noise-spectrum
defaults_file: params/experiments/mx-main-field-noise-spectrum.yaml
---

# Mx 主磁场控制噪声谱

当前默认参数：[参数文件](../params/experiments/mx-main-field-noise-spectrum.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

本实验保持主场和 Pump 光沿 Z、Probe 光沿 X，以 GS200 主场电流代替
`Z_magnetic_field` DG4000 的直流扫描。辅助 Z 场以及 X/Y 场在整个实验中归零并关闭。
Pump AOM 保持 100 MHz 连续载波，`Time_sequence` 保持 5 V DC、Output ON。

锁相放大器 Demod0 使用 Input 1，振荡器固定为配置的参考频率，只采集 `sample.r`。每个主场点
按配置时长采集一条时间序列，不做重复平均。

## 标定与扫描轴

标定来源由参数文件选择。下面保留 `Mx_Main_Field_Calibration/0720_124208_mx_main_field_cal` 的历史结果示例，非当前默认参数：

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

锁相放大器 参考频率、控制轴范围和点数读取参数文件，再由标定反算 GS200 电流。原始数据同时保存
`signed_detuning=f(I)-f_HF2=-Omega_Ctrl`，不从当前默认值推定历史范围。

## 采集流程

1. 使用 `DeviceSession` 连接 GS200、场控制 DG4000、光功率 DG900、Pump DG4000、
   温控开关 DG900、TEC103 和 锁相放大器。
2. 在任何输出设置前记录 GS200 的源模式、电流、输出、量程和限流，并按
   `params/devices.yaml` 中各设备的 `reference_clock` 设置、回读所有已连接时钟。
3. 强制 Z/X/Y 场为 0 V、Output OFF，配置光功率、120 °C 温度、Pump 连续工作点和
   按配置参考频率设置 Demod0；保存 锁相放大器 相位及硬件返回的实际采样率。
4. 每个控制频率点依次执行：
   - 按标定式反算 GS200 电流并再次执行安全与标定范围校验；
   - 设置电流并保持 GS200 Output ON；
   - 将温控开关设为 0 V DC、Output ON，按配置统一等待；
   - 按 锁相放大器 实际采样率按配置时长采集 Demod0 R；
   - 在 `finally` 中恢复温控为 5 V DC、Output ON，并按配置等待。

扫描和等待均支持 GUI 取消。扫描时间由实际点数、等待与采集时长累加得到，不含连接与首次温度稳定。

## 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

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
锁相放大器 配置按标准策略保留，正常结束只断开 TEC 通信。
