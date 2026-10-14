---
title: Mx 高主场 Z 磁场频率标定
type: Mx_Z_Field_Calibration
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: scan
  rf_coil:
    role: detection_drive
  X_magnetic_field:
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
  - 主场与 Z 偏置取自参数文件；普通线性模型要求扫描不跨过总场零点。
  - 采集工作流不执行共振拟合；全部 Lorentzian 和线性标定均由离线分析器完成。
  - 每个频点独立关闭温控采集，随后恢复 5 V 并按配置等待。
experiment_id: mx-z-field-calibration
defaults_file: params/experiments/mx-z-field-calibration.yaml
---

# Mx 高主场 Z 磁场频率标定

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-field-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标和构型

本实验在 Mx 构型下标定 `Z_magnetic_field`（DG4000 CH1）DC 电压与 Y RF 共振中心频率之间的关系：

\[
f_0(V_Z)=K_Z V_Z+f_{0V}.
\]

主场与 Pump 光沿 Z，Probe 光沿 X；`rf_coil` 沿 Y，锁相放大器 Demod0 只采集 R。主场、零偏预测中心和初始斜率均读取参数文件。`Z_INITIAL_HZ_PER_V` 只用于确定每个 Z 电压的局部扫频窗口，不作为最终标定结论。

`FIXED_PARAMS.main_magnetic_field` 与 `ZERO_BIAS_CENTER_FREQUENCY_HZ` 均允许设置为 `0`。当零偏预测中心为 `0 Hz` 时，采集前使用首个 Z 扫描点的预测中心初始化 Y RF 与 锁相放大器；所有实际扫频点仍必须大于 `0 Hz`，否则预检会拒绝运行。

## 扫描流程

Z 偏置按参数文件中的起点、终点与点数生成扫描轴。

GUI 必须明确选择正 Z 电压使预测中心升高或降低。每个 Z 点的预测中心为：

\[
f_\mathrm{pred}=f_\mathrm{GUI}+sK_\mathrm{initial}V_Z,
\]

其中 \(s=\pm1\)。每个 Z 点按配置的局部频率窗口和步进扫描。设置新的 Z DC 偏置时，DG4000 使用专用 `VOLTage:OFFSet` 命令；工作流只验证波形仍为 DC 且输出开关状态正确。DG4162 的内部电压查询不能作为物理输出回读，因此原始数据记录的是命令值 `z_bias_commanded_v`；实际链路是否响应应以示波器等外部测量为准。设置后不执行专用稳定等待。

每个 Y RF 频点依次执行：

1. 设置 Y RF 频率与配置的幅度，并同步 锁相放大器 振荡器。
2. 将温控开关设置为 0 V DC 且保持 Output ON。
3. 按参数配置等待温控关闭与频点稳定。
4. 按配置时长采集 Demod0 R。
5. 在 `finally` 中恢复温控为 5 V DC + Output ON，并按配置等待。

若单次记录超过配置的标准差门槛，完整保存该尝试并按配置的尝试次数重采。采集阶段不检查共振峰，不执行 Lorentzian 拟合，也不扩展扫频窗口。

## 离线分析

分析器从 `raw/z_scan_index.npz` 和各 `raw/z_*/frequency_scan.npz` 重建各 Z 偏置点的频率响应，对每条响应拟合带基线 Lorentzian。正式中心只执行拟合所需的基础有效性检查：

- 拟合正常收敛且输入中至少有 5 个有限频率点；
- HWHM 大于频率步进且小于扫描跨度的一半；
- 中心位于扫描窗口内，且距边缘不少于一个 HWHM。

单曲线 R²、中心标准不确定度和 HWHM 相对不确定度只作为诊断结果，不再参与中心取舍。离散最大值也只作为诊断。有效中心使用标准不确定度加权线性拟合；线性拟合在数学上至少需要 2 个有效中心，最终线性 R² 仍需达到配置阈值。不再计算或输出有效 Z 点数与有效 Z 跨度。

结果写入：

```text
data/Mx_Z_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    z_scan_index.npz
    z_000/frequency_scan.npz
    ...
  results/
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    z_frequency_calibration.png
    z_frequency_residuals.png
```

采集与离线分析入口分别为 `experiments/Mx_Z_Field_Calibration.py` 和 `experiments/Mx_Z_Field_Calibration_plot.py`。

## 安全结束状态

正常、取消和异常路径均归零关闭 Z DC 与 Y RF，保持 X 场关闭，将温控恢复为 5 V DC + Output ON。主场、Pump/Probe 光功率、Pump 载波/门控和 锁相放大器 配置按标准策略保留；正常结束只断开 TEC。
