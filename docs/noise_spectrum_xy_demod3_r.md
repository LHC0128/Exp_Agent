---
title: XY 控制 Demod3 R 噪声谱
type: Noise_Spectrum_XY_Demod3_R
execution_mode: typed_workflow
scan_mode: nested_scan
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
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - Demod0 Y 通过 AuxOut2 和物理线缆接入 Signal Input 2 DC，再由 Demod3 解调。
  - 每个 Demod3 频点连续采集 0.1 s 的 sample.r，只保存算术平均值，不保存时序数组。
  - 每扫描 25 个 Demod3 频点恢复温控 2 s；切频后的 0.2 s 稳定等待同时覆盖波形上传和温控关闭后的等待。
experiment_id: noise-spectrum-xy-demod3-r
defaults_file: params/experiments/noise-spectrum-xy-demod3-r.yaml
---

# XY 控制 Demod3 R 噪声谱

## 锁相采集方式与实际速率

Demod3 R 扫描使用 `DEMOD3_RATE_SA_S` 请求率、`DEMOD3_ACQUISITION_DURATION_S` 时长和 `DEMOD3_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/noise-spectrum-xy-demod3-r.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

锁相设备在「仪器控制 → 设备库 → 锁相放大器」中全系统统一选择。**本实验当前接线需要两路独立主电压输入，暂时只支持 HF2；选用 MFLI 时预检禁止启动。**MFLI 的 Aux Input 回采尚未验证，程序不会自动改接线、降频或回退 HF2。该限制不影响已有 HF2 数据的离线分析。接口差异见 [锁相接口说明](lockin_amplifier.md)。

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

校相完成后不再扫描相位，Demod3 振荡器按参数文件指定的解调频率轴扫描。

## 二维扫描与温控

- 外层控制频率与内层解调频率的范围、点数读取参数文件；控制频率通过 K/B 标定反算 DirectAW 包络；AW 满量程逐点自动跟随包络（vpp = 2 × |V_env|，offset 恒 0），零包络点上传全零波形并保持输出 OFF。
- 切频等待、采集时长、Demod3 rate 和温控批次大小均读取参数文件，使用实际采样率，只保存 R 平均值。
- 批次开始时关闭温控，结束后恢复温控并按配置等待。
- 理论扫描时间为采集点总数乘单点等待与时长，再加批次数乘温控恢复等待；不包含升温、校相和通信开销。

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
DirectAW、Z 场和非时序外触发通道；`Time_sequence`、`Time_sequence_2` 和 Pump 门控
保持当前状态，主磁场、Pump/Probe 光功率、Pump 载波和 HF2 配置保持不变，正常结束只断开 TEC。
