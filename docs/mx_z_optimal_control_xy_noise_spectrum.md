---
title: Mx Z 最优控制 XY 噪声谱
type: mx-z-optimal-control-xy-noise-spectrum
scan_mode: nested_scan
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
  - 锁相放大器
learned_notes:
  - XY 坐标为信号发生器电压，当前没有 X/Y 电压到磁场的独立标定。
experiment_id: mx-z-optimal-control-xy-noise-spectrum
defaults_file: params/experiments/mx-z-optimal-control-xy-noise-spectrum.yaml
---

# 实验目标

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xy-noise-spectrum.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

在最新 `corrected_run` Z 最优控制波形保持输出的条件下，扫描 X/Y 直流补偿偏置，测量 锁相放大器 Demod0 的 R 噪声谱。主排序指标为指定频段内的 R ASD 中位数，单位为 `V/√Hz`；本实验不输出未经标定的 `nT/√Hz`。

# 接线与状态

- Z 方向使用 `Z_magnetic_field` DG4000 CH1 输出闭环冻结的最优控制任意波，输出保持开启。
- X 方向使用 DG4000 `CH1` 输出 DC 偏置；Y 方向使用同一 DG4000 的 `CH2` 输出 DC 偏置。
- Y 通道与 RF 线圈共用物理通道。噪声采集前关闭 Y Burst 和 Modulation，只保留当前 Y DC；Y 偏置为 0 V 时关闭该通道输出。
- 锁相放大器 仅采集 Demod0 `R(t)`，不执行 RF 相位校准或 RF 幅值扫描。
- Pump/Probe 与 Pump 调制按 Mx Z 最优控制公共工作点配置。

# 采集时序

每个蛇形 XY 网格点设置 X/Y DC 后，重复 `NOISE_N_AVG` 次：确认 Y 纯 DC、将 `Temp_Switch` 设为 0 V、等待关闭延迟、采集 `NOISE_DURATION_S` 的 R(t)，然后恢复 `Temp_Switch=5 V` 并等待恢复延迟。每条记录保存实际采样率和 XY/RF/控制/温控状态元数据（`temperature_gate_state=off_during_acquire_then_on`）。取消、通信异常和异常退出均执行安全停机。

# 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

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

旧 `[-0.01,+0.01] V` 网格在 `3–200 Hz` 的最低点位于 `X=+0.010 V, Y=-0.010 V` 边界，因此后续扫描应向该边界外扩展；当前网格见参数文件。统计上限必须作为参数保存在运行配置中，因为不同截止频率可能给出不同最佳点。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。
