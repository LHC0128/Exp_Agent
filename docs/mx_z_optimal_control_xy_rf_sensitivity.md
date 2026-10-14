---
title: Mx Z 最优控制 XY 补偿偏置 RF 灵敏度
type: Mx_Z_Optimal_Control_XY_RF_Sensitivity
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger
  X_magnetic_field:
    role: x_dc_compensation_scan
  Y_magnetic_field:
    role: y_dc_compensation_scan_and_rf_offset
  rf_coil:
    role: per_point_rf_sensitivity_drive
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
    role: detection_r
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - RF 相位只在 X/Y 扫描轴算术中心校准一次，并复用于全部 XY 点。
  - X 外层、Y 内层使用蛇形顺序；Y 坐标直接作为 rf_coil 的 DC offset。
  - 每个 XY 点独立完成带符号 RF 幅度扫描和 RF-off 噪声采集。
  - R 点质量重试耗尽后该点标记无效并继续，硬件和安全错误立即终止。
  - 分析只报告最佳 XY 点，不自动切换或保持最佳补偿输出。
experiment_id: mx-z-optimal-control-xy-rf-sensitivity
defaults_file: params/experiments/mx-z-optimal-control-xy-rf-sensitivity.yaml
---

# Mx Z 最优控制 XY 补偿偏置 RF 灵敏度

## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xy-rf-sensitivity.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-rf-sensitivity`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制、共同触发、主场、光功率、温控
和 锁相放大器 工作点，在 X/Y DC 补偿偏置上建立二维网格。

X 由 `X_magnetic_field` 输出 DC；Y 与 `rf_coil` 共用物理通道，Y 网格值作为
RF 波形的 DC offset。X/Y 坐标单位均为信号发生器电压 V，当前没有 XY 物理标定
时不转换为 nT。

## 校相与扫描

校相点固定为 X/Y 扫描起止值的算术中心。该点执行现有 Demod0 R/X/Y 复数正交
RF 校相，得到建设性 RF Burst 相位；相位校准失败时不进入灵敏度网格。

正式网格采用 X 外层、Y 内层蛇形顺序。每个点先设置 X DC 和 Y offset，再使用
统一校准相位执行带符号 RF 幅度扫描。负幅度由 RF 相位增加 180° 表示，零幅度
切换为该点的纯 Y DC 补偿。RF-off 噪声采集时保持 Y DC offset。

每个响应 R 点按 `R_POINT_MAX_ATTEMPTS` 重试；重试仍不满足标准差门槛时，保留
已有原始记录，将该 XY 点标记为 `invalid_quality` 并继续。设备通信、参数配置或
安全校验错误会终止整个运行。

## 数据与分析

```text
data/Mx_Z_Optimal_Control_XY_RF_Sensitivity/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    phase_scan.npz
    point_manifest.yaml
    xy_bias_grid.npz
    points/
      x_###_y_###/
        amplitude_scan.npz
        amplitude_*_attempt_*.npz
        noise_*.npz
  results/
    phase_calibration.yaml
    phase_calibration.png
    optimization.npz
    optimization.yaml
    optimization.json
    analysis.yaml
    analysis.json
    sensitivity_heatmap.png
    slope_heatmap.png
    zero_point_sensitivity_heatmap.png
    zero_point_slope_heatmap.png
    hwhm_heatmap.png
    best_point_full_analysis.png
    best_zero_point_full_analysis.png
```

离线分析逐点复用 Mx Z 最优控制 RF 灵敏度的幅度分析约定，以及 Mx Y RF 的
等效 HWHM、Welch PSD、平坦频段检测和零点局部斜率实现。幅度拟合固定从
`V0=0 Vpp` 开始，避免把扫描端点的最低 R 值误当成共振中心；`gamma` 相对
不确定度仍写入诊断，但不作为 XY 网格点的硬拒绝条件。主判据为最小化
`flat_median_ft_per_sqrt_hz`；无效点保留失败原因并从排名中排除。最佳点只写入
分析结果，不改变仪器状态。

## 安全结束状态

正常、取消和异常结束均保留共同触发当前状态，关闭归零 X 场和 Y RF，保持 Z 最优控制波形及
Output 状态，恢复 GS200 运行前完整状态，并将温控恢复为 `5 V DC + Output ON`。
Pump/Probe、Pump 载波/门控和 锁相放大器 设置保留，正常结束只断开 TEC。
