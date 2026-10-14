---
title: Mx Y RF 灵敏度长飘
type: Mx_Y_RF_Sensitivity_Drift
scan_mode: repeated_measurement
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
  - 锁相放大器
learned_notes:
  - 每轮子实验复用 Mx_Y_RF_Sensitivity 的幅度响应和零 RF 噪声分析。
  - 父目录只保存轮次清单、表格和趋势图；原始数据保存在标准子运行目录。
  - 单轮分析失败或 R 点质量门限失败记录为无效轮次并继续；硬件采集异常停止任务。
experiment_id: mx-y-rf-sensitivity-drift
defaults_file: params/experiments/mx-y-rf-sensitivity-drift.yaml
---

# Mx Y RF 灵敏度长飘

## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-y-rf-sensitivity-drift.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

该实验启动后立即执行第一轮，随后按绝对时间间隔重复单轮 `Mx_Y_RF_Sensitivity` 测量。`DRIFT_DURATION_S` 和 `DRIFT_INTERVAL_S` 可在 GUI 中修改，轮数按半开时间区间自动计算；计划轮数根据当前参数计算，不在文档固定。

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

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。
