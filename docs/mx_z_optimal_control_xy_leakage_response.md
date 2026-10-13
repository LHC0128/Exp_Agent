---
title: Mx Z 最优控制 XY 泄露响应
type: Mx_Z_Optimal_Control_XY_Leakage_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger_preserved_on_exit
  X_magnetic_field:
    role: scanned_same_shape_arbitrary_waveform
  Y_magnetic_field:
    role: scanned_same_shape_arbitrary_waveform
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
    role: Demod0_R_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - X/Y 使用与 Z 实际上传数组完全相同的归一化采样点和重复频率。
  - 负带符号幅度用绝对 Vpp 和相对 Z 基准相位增加 180° 实现。
  - 正常结束保留实测最小 R 网格点；异常与取消保留当时 X/Y 状态。
  - 所有结束路径均保留 Time_sequence_2 当前状态，同时保留 Z/X/Y 当前输出。
  - Z/X/Y 任意波 DG4000 Burst 均使用共同触发方波的下降沿启动。
experiment_id: mx-z-optimal-control-xy-leakage-response
defaults_file: params/experiments/mx-z-optimal-control-xy-leakage-response.yaml
---

# Mx Z 最优控制 XY 泄露响应

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xy-leakage-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-leakage-response`。实验复用
`Mx Z 最优控制 RF 灵敏度`的 Z 控制来源、Z 标定、GS200、光功率、温度和
锁相放大器 工作点，但不输出独立 Y RF 正弦波，也不执行 RF 校相、噪声或灵敏度分析。

Z 通道输出按控制版本和 Z 标定换算后的最优控制波形。X/Y 通道复制 Z 实际上传的
归一化数组和重复频率，输出偏置固定为 0 V，只扫描硬件 Vpp。正幅度使用
`CONTROL_BURST_PHASE_DEG`；负幅度使用绝对 Vpp，并在该相位基础上增加 180°；
零幅度切换为 `0 V DC + Output OFF`。

`Time_sequence_2` 必须由操作者分配到 Z 控制 DG4000 和 X/Y 控制 DG4000 的
Ext Trig。所有任意波通道使用下降沿外触发，与 Keithley 6221 Trigger Link 输入
保持同沿。软件验证两台设备的 Burst 和触发配置，但无法检查实体 BNC 连接。

## 二维扫描与采集

X/Y 扫描范围与点数读取参数文件，总网格点数为两轴点数之积。
扫描采用 X 外层、Y 蛇形顺序。切换每个网格点时先关闭共同触发和 X/Y Output，
再分别设置绝对 Vpp 与 Burst 相位，同时 arm 两路后重新开启共同触发；Z 不重新
arm并保持连续运行。

每个采集尝试均关闭温控、等待 0.1 s，再稳定 0.1 s并采集 0.2 s Demod0 R，
随后恢复温控并等待 2 s。若 R 标准差超过门槛，完整保存该次数据并重新执行触发、
温控门控和采集，最多三次。

原始数据保存到：

```text
data/Mx_Z_Optimal_Control_XY_Leakage_Response/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    grid_X###_Y###_attempt_##.npz
    xy_leakage_scan.npz
  results/
    analysis.yaml
    analysis.json
    xy_leakage_response.png
```

聚合 NPZ 始终按递增 X/Y 规范轴保存矩阵，并额外记录蛇形采集序号、硬件幅度、
相位、Output 状态、接受的重试文件和实际 锁相放大器 采样率。

## 分析与结束状态

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

分析器只读取明确运行目录中的原始数据，生成 R 均值和标准差二维热图，并按
`R 均值最小、采集序号最早`的规则报告实测网格最小点。不执行连续拟合、网格外推
或 X/Y 电压到磁场的换算，因此结果不是泄露磁场绝对标定。

正常完成后，程序同时施加该实测最小点的 X/Y 波形，等待共同触发启动，但不额外
复测组合状态的 R。取消、Ctrl+C 或异常时不切换最佳点，而是保留当时的 Z/X/Y
状态。所有退出路径均保留 `Time_sequence_2` 当前状态、恢复温控为
`5 V DC + Output ON`、恢复 GS200 运行前完整状态，并保留 Pump/Probe、Pump
载波/门控和 锁相放大器 设置。由于这是显式安全例外，失败后 Z/X/Y 输出可能继续开启。
