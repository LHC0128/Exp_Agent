---
title: Mx Keithley 6221 最优控制 RF 灵敏度
type: Mx_Keithley_6221_Optimal_Control_RF_Sensitivity
scan_mode: point_by_point
mapping_keys:
  keithley_6221_main_field:
    role: triggered_optimal_control_current
  main_magnetic_field:
    role: gs200_z_main_field
  Time_sequence_2:
    role: common_trigger
  rf_coil:
    role: scanned_detection_drive
  X_magnetic_field:
    role: fixed_x_dc_compensation
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
    role: detection_rxy_acquisition_r_fit_phase_calibration_and_r_measurement
required_devices:
  - Keithley 6221
  - GS200
  - DG4000
  - DG900
  - 锁相放大器
learned_notes:
  - 6221 接 Z 小磁场线圈，GS200 接独立的 Z 主磁场线圈；运行前确认物理接线正确。
  - Time_sequence_2 CH2 经 BNC T 同时接 6221 Line 1 和 Y RF DG4000 Ext Trig；Line 2 不接。
  - 外触发固定 Line 1、IGNORE=OFF、单周期；重触发会立即中止并重启当前周期。
  - 触发间隔 inactive 值按任意波幅度和偏置反算为物理 0 mA，波形末点也固定为 0 mA。
  - GS200 主磁场电流允许非零值；所有退出路径都将 6221 与 GS200 归零并关闭，不恢复运行前非零状态。
experiment_id: mx-keithley-6221-optimal-control-rf-sensitivity
defaults_file: params/experiments/mx-keithley-6221-optimal-control-rf-sensitivity.yaml
---

# Mx Keithley 6221 最优控制 RF 灵敏度

> **已归档：GUI 仅保留目录卡片，不再提供运行/历史页面。** 专属代码与正式测试保存于
> [`old/experiments/`](../old/experiments/README.md)，默认参数与历史数据原位保留。
> 下文说明归档前的方案与安全约定，仅供维护和历史复现；重新接入需先恢复代码及注册入口并完成无硬件回归。

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

归档时保留的参数：[参数文件](../params/experiments/mx-keithley-6221-optimal-control-rf-sensitivity.yaml)。当前 GUI 不再提供保存入口，该文件仍原位保留；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，归档前的 GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 实验目标

该实验仿照 `mx-z-optimal-control-rf-sensitivity` 的 Y RF 校相、带符号幅度扫描、
RF-off 噪声采集和离线分析，但将 Z 主场最优控制从 DG4000 任意波改为 Keithley
6221 内部 ARB0；GS200 同时提供独立 Z 主磁场偏置。原实验不修改，两种硬件方案保持独立。

## 相位校准

6221 变体不使用 X/Y 成对正交差分拟合，而是扫描 Y RF 触发相位时同步记录
Demod R/X/Y，并只用 **Demod R 信号**做相位拟合。正式模型是物理复合函数：

```text
R(φ) = |scale · (B − b0) / ((B − b0)² + w²)|          —— 色散线形的绝对值
B(φ) = |A·e^{iφ} + C·e^{iφc}|                        —— 等效 RF 幅度矢量合成
```

其中 `A` 固定为 `PHASE_CAL_RF_AMPLITUDE_VPP`，`C` 是剩磁射频成分的等效幅度。
改变 Y RF 相位即改变它与剩磁射频成分合成的等效幅度，R 对等效幅度的响应是
色散线形的绝对值；当工作点落在 |色散| 的非线性折叠区时，R(φ) 会出现双峰
结构（实测 0814_105507 运行中 |C+A·sin| 只有 R²=0.05，而复合模型 R²≈0.92）。
选中相位取拟合曲线峰值中距离观测最大相位最近的峰；同时保留
`C + A·|sin(φ − φ0)|` 作为诊断对照模型。校相点仍按 φ、φ+180° 紧邻的顺序
采集并做跨相位离群点重采，质量门槛由 `PHASE_FIT_R_SQUARED_MIN`
（R² 最低值）、`PHASE_FIT_AMPLITUDE_SIGMA_MIN`（响应峰谷差相对中位点噪声的
最低 σ 数）与 `PHASE_OUTLIER_SIGMA_THRESHOLD` 控制，拟合不合格或重采后仍有
跨相位异常点时拒绝正式分析。

## 标定与电流换算

按参数文件选择标定运行，并读取其自由斜率和截距：

```text
K_f = 标定斜率（Hz/mA）
f_0 = 标定截距（Hz）
I(t) = CONTROL_SCALE * (Omega_ctrl(t) - f_0) / K_f
```

理论频率来自所选波形，所需档位和裕量根据完整电流包络计算，不在说明中固定数值。设备波形 offset 固定为
`0 mA`，程序用归一化任意波点表达不对称包络，并把末点改为物理 0 mA，
不额外叠加直流偏置。

归档前的 GUI 的 `KEITHLEY_CURRENT_RANGE_MA` 提供从 `2 nA` 到 `100 mA` 的九个标准档位。
预检会用理论波形、标定斜率/截距和 `CONTROL_SCALE` 重新计算完整电流包络，要求：

```text
KEITHLEY_CURRENT_RANGE_MA >= max(abs(I_min), abs(I_max))
```

档位不足时，预检会报告控制上下限、实际所需最小档位和推荐标准档位并拒绝运行。
运行配置与 `raw/applied_control_waveform.npz` 同时保存所选档位、所需档位、裕量、
利用率和判断结果。

## 触发与接线

```text
Time_sequence_2 CH2
        |
      BNC T
       +-- Keithley 6221 Trigger Link 转接线 Line 1
       +-- Y RF DG4000 Ext Trig
```

`Time_sequence_2` 输出 `0 到 5 V`、50% duty 方波。6221 在下降沿响应，配置为
`EXTR:ENAB ON`、`EXTR:ILIN 1`、`EXTR:IGN OFF`、一个周期、`ARM`、`INIT`。
`IGNORE=OFF` 表示下一触发到来时会立即终止当前周期并从头开始；因此触发周期略短于
任意波周期时，尾部会被截断，这是本实验明确接受的行为。

`TRIGGER_FREQUENCY_HZ` 为只读派生字段，等于当前 `CONTROL_VERSION` 任意波
时间轴的重复频率；归档前的 GUI 中切换 `CONTROL_VERSION` 时自动刷新显示。控制重复频率
允许与理论 rf 频率不同（f_rf 必须是重复频率的正整数倍），解调用 rf 频率。
`Y_RF_FREQUENCY_HZ` 为可编辑参数，切换 `CONTROL_VERSION` 时自动填充为理论
rf 频率，可手动修改；锁相放大器 解调频率跟随 Y RF 频率。Y RF 频率与理论 rf 频率
不同时仅记录警告：共同触发只固定采集起始相位，采集期间相对相位按频差演化。

## 安全与退出

6221 使用 `KEITHLEY_CURRENT_RANGE_MA` 选择的固定档位、`FAST` 响应、关闭模拟滤波
和 15 V Compliance。
同一主线圈的真机诊断确认直流 10 mA 在 1 V 下不会进入 Compliance；但 30 kHz、
约 `-5.9155 到 +5.8449 mA` 的旧标定 ARB0 在持续输出期间使用 5 V 会进入 Compliance，
6 V 开始通过；使用 30 kHz 外部重复触发时 12 V 仍曾进入 Compliance，13 V 连续
2 秒通过。实验采用 15 V，为当前重复触发动态负载保留 3 V 余量，同时继续在每个
采集点前后检查 Compliance。该结果只适用于当前主线圈、接线和控制波形，改变其中
任何一项都应重新验收动态 Compliance。当前 `-89.9360 到 +88.9091 mA` 控制包络
尚未由上述旧幅度测试覆盖，首次正式运行前必须重新验收动态 Compliance。
启动前以及校相、幅度扫描和噪声采集的每个点前后检查 Compliance；命中或查询失败时
依次尝试 `ABORT`、关闭输出和归零，同时记录原始错误与安全关断错误。

正常、取消、异常及部分连接失败时均保持共同触发当前状态，关闭归零 X 场和 Y RF，恢复温控开关，保持
Pump 载波和门控，并使 6221、GS200 最终处于 `0 mA + output off`。

## 数据与分析

采集入口为 `old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py`，离线分析
入口为 `old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity_plot.py`。
运行目录保存理论源文件、标定分析快照、实际电流波形、相校数据、幅度扫描和 RF-off
噪声；分析过程沿用原 Mx Z 最优控制 RF 灵敏度实验。
