---
title: Mx Z 最优控制 XYZ 平衡场
type: Mx_Z_Optimal_Control_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: z_balance_scan
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: optimal_control_trigger
  X_magnetic_field:
    role: x_dc_balance_scan
  Y_magnetic_field:
    role: y_dc_balance_scan
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_r_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - 三维数组使用 Z/X/Y 规范索引，采集按 Z 外层连续蛇形执行。
  - 任一轴点数为 1 时，必须满足该轴 START=STOP，并固定在该值。
  - 最优点只按实测 Demod0 mean(R) 选择，不拟合、不复测、不换算 nT。
  - 分析图按每个 GS200 Z 电流分别显示 XY 平面，并对所有平面使用共同色标。
  - 扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON。
  - Z 最优控制 DG4000 Burst 使用共同触发方波的下降沿启动。
experiment_id: mx-z-optimal-control-xyz-balance
defaults_file: params/experiments/mx-z-optimal-control-xyz-balance.yaml
---

# Mx Z 最优控制 XYZ 平衡场

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xyz-balance.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xyz-balance`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制来源、Z 通道标定换算、共同触发、
Pump/Probe、温控和 锁相放大器 Demod0 配置；最优控制只在开始时触发一次并持续运行。

平衡场由三路独立 DC 输出扫描：

- X：`X_magnetic_field` DG4000 CH1，单位 V；
- Y：`Y_magnetic_field` DG4000 CH2，单位 V；
- Z：`main_magnetic_field` GS200，单位 mA。

`Z_magnetic_field` DG4000 CH1 只输出周期最优控制，不作为 Z 平衡场扫描源。
`Time_sequence_2` CH2 只需接入 Z 控制 DG4000 的 Ext Trig；X/Y 使用 DC 模式，
不需要共同触发。Z 控制 Burst 使用下降沿启动，与 Keithley 6221 Trigger Link
输入保持同沿。

## 扫描与采集

X/Y 电压及 Z 电流的范围与点数由参数文件指定，总点数为三个轴点数之积。数据数组固定使用 `(Z, X, Y)` 索引；实际
采集以 Z 为外层，X/Y 使用跨行、跨平面连续蛇形顺序，减少相邻点的大幅跳变。

每个轴都允许配置为单点固定值。当 `*_FIELD_POINTS=1` 时，对应的
`*_FIELD_START` 与 `*_FIELD_STOP` 必须严格相等；点数大于 1 时必须满足
`START<STOP`。预检与运行时都会执行同样的约束。

每个点先设置三轴补偿值，再将温控开关设为 0 V DC + Output ON，按配置等待并采集 Demod0 R，保存实际采样率；随后在 `finally` 中恢复温控为
5 V DC + Output ON 并按配置等待。标准差超限时完整保存本次尝试并在配置的尝试上限内重采。X/Y 和 GS200 在扫描值为零时也保持 Output ON，避免跨零点时引入输出
开关状态变化。

## 数据与分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

```text
data/Mx_Z_Optimal_Control_XYZ_Balance/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    grid_Z*_X*_Y*_attempt_*.npz
    xyz_balance_scan.npz
  results/
    balance_results.npz
    xyz_balance_grid.csv
    xyz_balance_slices.png
    analysis.yaml
    analysis.json
```

主结果是所有有效实测点中 Demod0 `mean(R)` 最小的第一个采集点；相同 R 时按
采集顺序最早者确定。结果只报告 X/Y 电压和 GS200 Z 电流设定值，不进行连续拟合、
最佳点复测或 nT 换算。分析图按每个实测 GS200 Z 电流分别显示一个 XY 平面，所有
平面共用同一 Mean R 色标，便于直接比较不同 Z 电流下的二维响应；全局实测最小点
只标在对应的 Z 电流面板中。输出文件名继续使用 `xyz_balance_slices.png`，以兼容
既有结果入口。若 Z 轴只有一个点，图中只生成一个 XY 平面。最小点位于任一扫描轴
边界时，分析结果会给出警告。

## 安全结束状态

连接后、修改输出前先保存 X/Y DG4000 通道波形、幅度、偏置、相位、Burst、调制和
Output 状态，以及 GS200 的源模式、电流、量程、限流和 Output 状态。正常完成、
取消、异常和 Ctrl+C 均恢复这些运行前状态。`Time_sequence_2` 保持当前状态；
`Z_magnetic_field` 保留当前最优控制波形与 Output ON。温控恢复为 5 V DC +
Output ON，Pump/Probe、Pump 载波/门控和 锁相放大器 设置保持，正常结束只断开 TEC。
