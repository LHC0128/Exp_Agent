---
title: Mx Keithley 6221 主磁场频率标定
type: Mx_Keithley_6221_Main_Field_Calibration
scan_mode: nested_scan
mapping_keys:
  keithley_6221_main_field:
    role: scan
  main_magnetic_field:
    role: forced_off
  Z_magnetic_field:
    role: fixed_off
  X_magnetic_field:
    role: fixed_off
  rf_coil:
    role: detection_drive
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
  - Keithley 6221
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - Keithley 6221 与 GS200 控制同一只 Z 主线圈，但本实验只允许 6221 物理接入。
  - GS200 必须物理断开；仅关闭输出不能替代物理断开确认。
  - 最终线性拟合同时求解斜率与截距，不强制通过原点。
experiment_id: mx-keithley-6221-main-field-calibration
defaults_file: params/experiments/mx-keithley-6221-main-field-calibration.yaml
---

# Mx Keithley 6221 主磁场频率标定

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-keithley-6221-main-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 接线与启动条件

Keithley 6221 通过 `keithley_6221_main_field` 独占 Z 主线圈。GS200 使用原有
`main_magnetic_field` mapping，但必须从该线圈物理断开。不得把两台电流源并联在线圈上。

启动前必须勾选 `CONFIRM_GS200_PHYSICALLY_DISCONNECTED`。初始化会将 GS200
设置为 `0 mA + 输出关闭`，并将 `Z_magnetic_field` 与 X 场归零关闭。6221 配置为
由 `KEITHLEY_CURRENT_RANGE_MA` 选择的固定量程、关闭自动量程、`SLOW` 响应、
关闭模拟滤波和 `1 V` Compliance。GUI 提供从 `2 nA` 到 `100 mA` 的九个
标准档位；所选档位必须覆盖扫描起点和终点的绝对值，否则预检失败。

## 扫描

电流轴由参数文件指定。根据配置的初始斜率和参考点预测局部共振中心，
再使用配置的频率窗口与步进生成各电流点的扫频轴。预测关系只用于定位窗口，
不作为最终标定结果。电流直接设置到首点，不做阶梯爬升，改变电流后按参数配置等待。

每个电流稳定后及每个 Y RF 采集点前后均查询 Compliance。`CALC3:LIM:FAIL?`
返回命中时，工作流立即尝试 `ABORT`、关闭 6221 输出、归零并终止实验。

## 分析与结果

每组频扫离线拟合带基线 Lorentzian，并使用共振中心不确定度执行加权线性拟合：

```text
f_Hz = K_f_Hz_per_mA * current_mA + f_0mA_Hz
```

结果包含 `K_f_Hz_per_mA`、`f_0mA_Hz`、协方差、参数不确定度、R2、残差和逐电流
拟合质量。线性 R2 门槛读取运行参数快照。图中的标注均使用英文和 `paper` 样式。

```text
data/Mx_Keithley_6221_Main_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    keithley_main_field_scan_index.npz
    current_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    calibration_results.npz
    frequency_response_fits.png
    keithley_main_field_frequency_calibration.png
    keithley_main_field_frequency_residuals.png
```

采集入口为 `experiments/Mx_Keithley_6221_Main_Field_Calibration.py`，离线分析入口为
`experiments/Mx_Keithley_6221_Main_Field_Calibration_plot.py`。

## 安全结束状态

正常完成、取消、异常、连接后配置失败和 Compliance 命中时，均先关断并归零 6221，
再关断并归零 GS200，随后关闭 Y RF、Z/X 辅助场并恢复温控。不会恢复任一电流源
运行前的非零状态。
