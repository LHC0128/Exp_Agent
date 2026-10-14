---
title: Mx Y RF 光功率灵敏度优化
type: Mx_Y_RF_Power_Optimization
scan_mode: nested_scan
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
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时跳过设温和稳定等待。
  - 完整复用 Mx Y 向 RF 场灵敏度的幅度等效线宽、R 响应和零 RF 噪声算法。
  - Pump 为外层、Probe 为内层蛇形扫描，矩阵仍按两条升序功率轴保存。
  - 每个功率点开始前强制恢复 Y RF 正弦波和 锁相放大器 响应配置，避免噪声采集状态泄漏。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - 二维网格使用 tqdm 显示完成比例、处理速度和预计剩余时间。
  - 原色散拟合导数法与零点实测数据斜率法分别输出矩阵、排名和最优点。
  - 所有结束路径恢复 Pump 0.5 V、Probe 0.3 V 并保持输出开启。
experiment_id: mx-y-rf-power-optimization
defaults_file: params/experiments/mx-y-rf-power-optimization.yaml
---

# Mx Y RF 光功率灵敏度优化

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-y-rf-power-optimization.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 实验目标

在主磁场和 Pump 光沿 Z、Probe 光沿 X、待测 RF 场沿 Y 的 Mx 构型下，二维扫描 DG900 的 Pump/Probe 光功率控制电压。每个功率组合执行一次完整的 Y RF 幅度响应和零 RF 噪声测量，以校准后的平坦频段灵敏度中位数寻找最优工作点。

## 扫描与采集

- Pump/Probe 扫描范围与点数由参数文件指定。
- 采集顺序固定为 Pump 外层、Probe 内层蛇形扫描，不随机化也不重复整个网格。
- 设置新光功率后不增加独立等待；首个 RF 幅度点仍执行参考实验已有的温控关闭前导和响应稳定时间。
- 每个功率点使用配置的 Y RF 频率、幅度轴、噪声段数与时长。
- 每个功率点开始幅度扫描前，工作流都会按配置频率重新配置 Y RF SINE，并把 锁相放大器 Demod0 恢复为配置的响应采样率、时间常数和滤波阶数；因此上一点的零 RF DC 和高采样率噪声配置不会泄漏到下一点。
- 每个 Y RF 频率点、幅度点和零 RF 噪声段均按“设置 Y RF → 关闭温控开关 → 等待稳定 → 采集 → 恢复温控开关 → 等待”的顺序执行；重采和异常路径同样恢复温控开关。
- 进入 Pump/Probe 二维网格后，`tqdm` 按完整功率点更新完成比例、处理速度和 ETA；GUI 日志每完成一点会输出一行可持续刷新的进度快照。
- 单个 R 点重试达到配置上限后仍超过 `std(R)` 阈值时，该功率组合标记为无效并继续；设备通信、配置或安全错误立即终止实验。

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
    zero_point_sensitivity_heatmap.png
    zero_point_slope_heatmap.png
    hwhm_heatmap.png
    best_point_full_analysis.png
    best_zero_point_full_analysis.png
    points/
      pump_000_probe_000/
        analysis.yaml
        full_analysis.png
```

`point_manifest.yaml` 在每个功率点后更新，因此取消或异常后仍可对已经完整保存的功率点手动重新分析。

## 最优点判据

离线分析逐点复用 `mx-y-rf-sensitivity` 的绝对值色散拟合、PSD、幅度等效 HWHM 和自动平坦段识别。每个采集完整的功率点都会在 `results/points/<point>/full_analysis.png` 生成双面板完整分析图；上图标题同时标出 Pump/Probe 控制电压和质量状态。被严格质量门槛拒绝的点仍会继续计算仅供排障的诊断谱，标题标为 `DIAGNOSTIC ONLY - INVALID`，其数值不会写入有效矩阵、不会参与排序。

原方法保持不变：只有响应拟合成功且平坦段识别成功的点才参与 `ranking`，目标为最小化 `flat_median_ft_per_sqrt_hz`。

额外的零点实测数据斜率法使用全局色散拟合确定 V0，在零点两侧各取至少 2 个最近实测点并补足至少 5 点，通过 R 对 |V-V0| 的局部回归计算斜率。它独立输出：

- `optimization.npz` 中的 `zero_point_flat_median_ft_per_sqrt_hz`、`zero_point_slope_v_per_vpp` 和 `zero_point_valid_mask`；
- `zero_point_sensitivity_heatmap.png` 与 `zero_point_slope_heatmap.png`；
- `optimization.yaml/json` 中 `zero_point_method.ranking` 和 `zero_point_method.best_point`；
- `best_zero_point_full_analysis.png`。

新方法仍要求全局响应拟合成功，因为 V0 和幅度等效 HWHM 来自该拟合；局部斜率必须为正有限值，且自动平坦段识别必须成功。局部 R² 作为诊断信息与警告保存，不单独作为硬拒绝门槛。分析只报告两种方法各自的最优点，不会把仪器自动切换到该功率。

若没有有效点，分析器仍保存矩阵和逐点失败原因，并明确报告未找到可用最优点。

## 安全结束状态

正常、取消和异常路径均关闭并归零 Z 辅助场和 X/Y 场、恢复温控开关为 5 V DC + ON，并恢复 DG900 Pump=0.5 V、Probe=0.3 V 且保持两路输出开启。Pump 100 MHz 载波、主场和 锁相放大器 设置按参考实验策略保留，正常结束仅断开 TEC。
