---
title: Mx Y RF Probe 光功率与失谐灵敏度优化
type: Mx_Y_RF_Probe_Detuning_Optimization
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  PZT_VOLTAGE_START_V: 20.0
  PZT_VOLTAGE_STOP_V: 120.0
  PZT_VOLTAGE_POINTS: 21
  PZT_SETTLE_TIME_S: 1.0
  PROBE_POWER_START_V: 0.3
  PROBE_POWER_STOP_V: 0.3
  PROBE_POWER_POINTS: 1
  FIXED_PARAMS.Pump_laser_power: 0.2
  FIXED_PARAMS.Probe_laser_power: 0.3
  Y_RF_FREQUENCY_HZ: 90000.0
  Y_RF_NT_PER_VPP: 1517.79147
mapping_keys:
  probe_laser:
    role: scan
  Probe_laser_power:
    role: scan
  Pump_laser_power:
    role: fixed
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
  - DLC_PRO
learned_notes:
  - PZT Scan Offset 仅作为未经光频标定的 Probe 失谐代理量。
  - PZT 为外层、Probe 功率为内层蛇形扫描，每个 PZT 行只等待一次稳定时间。
  - Probe 起点与终点相等且点数为 1 时，兼容为固定 Probe、仅扫描 PZT。
  - 本实验不连接 TEC103、不设置目标温度，也不等待温度稳定；每次按“设置 Y RF→关闭 Temp_Switch→等待→采集→恢复 Temp_Switch→等待”执行。
  - 每个网格点完整复用 Mx Y RF 灵敏度的响应、噪声和双斜率分析。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - 实验期间关闭 DLC pro 内置扫描并将扫描幅度置零，结束后恢复进入实验前的完整状态。
---

# Mx Y RF Probe 光功率与失谐灵敏度优化

## 实验目标

在主磁场和 Pump 光沿 Z、Probe 光沿 X、待测 RF 场沿 Y 的 Mx 构型下，固定 Pump 光功率，扫描 Probe 光功率控制电压和 TOPTICA DLC pro Laser 1 的 PZT Scan Offset。Probe 轴既可以进行多点二维扫描，也可以固定为单点，仅扫描 PZT。每个网格点执行一次完整 Y RF 幅度响应和零 RF 噪声测量，以校准后的平坦频段灵敏度中位数寻找最优工作点。

PZT 电压仅作为 Probe 激光失谐的代理坐标。当前实验不包含 PZT—光频标定，因此结果不得解释为 MHz 或绝对光学失谐。

## 扫描与采集

- PZT Scan Offset：20–120 V，共 21 点，作为外层升序轴。
- Probe 功率控制电压：默认固定 0.3 V；需要二维扫描时设置起点/终点/点数。
- 只扫描 PZT 时，将 `PROBE_POWER_START_V` 和 `PROBE_POWER_STOP_V` 设置为同一个固定电压，并将 `PROBE_POWER_POINTS` 设置为 `1`。例如固定 Probe=0.3 V 时填写 `0.3 / 0.3 / 1`。
- 当 Probe 点数大于等于 2 时，Probe 起点仍必须严格小于终点；点数为 1 但起止值不同的配置会在预检阶段拒绝。
- Pump 功率固定为 0.2 V；实验结束恢复 Probe 功率为 0.3 V。
- 实验不连接 TEC103/COM3，也不执行目标温度设置或稳定等待；气室温度必须由实验外部预先稳定。
- 每次切换 PZT 后等待 1.0 s；同一 PZT 行内改变 Probe 功率不增加独立等待。
- 每个点使用 90 kHz Y RF、21 个带符号幅度点和 5 段 1 s 的零 RF 噪声。
- `Temp_Switch` 仍正常控制：每个频率点、幅度点和零 RF 噪声段均先设置 Y RF，再切到 `0 V DC + ON`，等待稳定并采集；随后恢复为 `5 V DC + ON` 并执行恢复等待。
- 单个 R 点连续三次超过 `std(R)` 阈值时，该网格点标记为无效并继续；PZT 回读、设备通信、配置或安全错误立即终止实验。

采集入口：

```text
experiments/Mx_Y_RF_Probe_Detuning_Optimization.py
```

离线分析入口：

```text
experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py <run_dir>
```

## 原始数据

```text
data/Mx_Y_RF_Probe_Detuning_Optimization/<run>/
  experiment_config.yaml
  raw/
    probe_detuning_grid.npz
    point_manifest.yaml
    points/
      pzt_000_probe_000/
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
```

`point_manifest.yaml` 保存每个点的 PZT 请求值、Scan Offset 回读、PZT 实际电压、Probe 功率、采集状态和失败原因。清单在每点后更新，取消或异常后仍可重新分析已完整保存的点。

## 分析与最优点

离线分析复用 `mx-y-rf-sensitivity` 的绝对值色散拟合、PSD、幅度等效 HWHM、自动平坦段识别和两种斜率方法：

1. 色散拟合零点导数法。
2. 零点两侧最近实测数据的局部斜率法。

两种方法分别输出矩阵、排名和最优点，目标均为最小化 `flat_median_ft_per_sqrt_hz`。固定 Probe 时矩阵退化为单列 PZT 结果，热图、排名和最优点文件格式保持不变。质量无效点只保留诊断图，不进入有效矩阵或排名。分析器只写入建议工作点，不连接仪器，也不会自动应用最优设置。

## DLC pro 状态与安全恢复

开始扫描前，工作流检查控制器和激光头序列号、系统与激光头健康状态、联锁、Laser Enabled 和 Emission。实验不会改变 Emission、Laser Enabled、激光电流或激光温度。

连接后记录初始 PZT Scan Offset、PZT 实际电压、扫描幅度和扫描启停状态。扫描期间关闭内置扫描并把扫描幅度置零，使 PZT 成为静态控制轴。正常、取消和异常路径均按以下顺序恢复：

1. 关闭归零 Z 辅助场和 X/Y 场并将温控开关恢复为 `5 V DC + ON`；不会连接或断开 TEC。
2. 恢复初始 PZT Scan Offset。
3. 恢复初始扫描幅度。
4. 恢复初始扫描启停状态。
5. 恢复 Pump=0.2 V、Probe=0.3 V 且保持输出开启。

若 DLC pro 写入或恢复回读失败，运行配置会记录“设备状态可能未知”，操作员必须检查 DLC pro 面板和 TOPAS。
