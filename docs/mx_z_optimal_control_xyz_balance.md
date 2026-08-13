---
title: Mx Z 最优控制 XYZ 平衡场
type: Mx_Z_Optimal_Control_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  X_FIELD_START_V: -0.05
  X_FIELD_STOP_V: 0.05
  X_FIELD_POINTS: 11
  Y_FIELD_START_V: -0.05
  Y_FIELD_STOP_V: 0.05
  Y_FIELD_POINTS: 11
  Z_FIELD_START_MA: -0.02
  Z_FIELD_STOP_MA: 0.02
  Z_FIELD_POINTS: 5
  DEMOD_FREQUENCY_HZ: 30000.0
  RESPONSE_SETTLE_TIME_S: 0.1
  RESPONSE_DURATION_S: 0.2
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
  - HF2
learned_notes:
  - 三维数组使用 Z/X/Y 规范索引，采集按 Z 外层连续蛇形执行。
  - 任一轴点数为 1 时，必须满足该轴 START=STOP，并固定在该值。
  - 最优点只按实测 Demod0 mean(R) 选择，不拟合、不复测、不换算 nT。
  - 分析图按每个 GS200 Z 电流分别显示 XY 平面，并对所有平面使用共同色标。
  - 扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON。
  - Z 最优控制 DG4000 Burst 使用共同触发方波的下降沿启动。
---

# Mx Z 最优控制 XYZ 平衡场

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xyz-balance`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制来源、Z 通道标定换算、共同触发、
Pump/Probe、温控和 HF2 Demod0 配置；最优控制只在开始时触发一次并持续运行。

平衡场由三路独立 DC 输出扫描：

- X：`X_magnetic_field` DG4000 CH1，单位 V；
- Y：`Y_magnetic_field` DG4000 CH2，单位 V；
- Z：`main_magnetic_field` GS200，单位 mA。

`Z_magnetic_field` DG4000 CH1 只输出周期最优控制，不作为 Z 平衡场扫描源。
`Time_sequence_2` CH2 只需接入 Z 控制 DG4000 的 Ext Trig；X/Y 使用 DC 模式，
不需要共同触发。Z 控制 Burst 使用下降沿启动，与 Keithley 6221 Trigger Link
输入保持同沿。

## 扫描与采集

默认 X/Y 为 `-0.05 V` 至 `+0.05 V`、各 11 点，Z 为 `-0.02 mA` 至
`+0.02 mA`、5 点，共 605 个点。数据数组固定使用 `(Z, X, Y)` 索引；实际
采集以 Z 为外层，X/Y 使用跨行、跨平面连续蛇形顺序，减少相邻点的大幅跳变。

每个轴都允许配置为单点固定值。当 `*_FIELD_POINTS=1` 时，对应的
`*_FIELD_START` 与 `*_FIELD_STOP` 必须严格相等；点数大于 1 时必须满足
`START<STOP`。预检与运行时都会执行同样的约束。

每个点先设置三轴补偿值，再将温控开关设为 0 V DC + Output ON，等待 0.1 s，
以实际采样率约 1000 Sa/s 采集 0.2 s Demod0 R；随后在 `finally` 中恢复温控为
5 V DC + Output ON 并等待 2 s。若 `std(R)>0.1 V`，完整保存本次尝试后重采，
最多三次。X/Y 和 GS200 在扫描值为零时也保持 Output ON，避免跨零点时引入输出
开关状态变化。

## 数据与分析

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
取消、异常和 Ctrl+C 均恢复这些运行前状态。`Time_sequence_2` 归零关闭；
`Z_magnetic_field` 保留当前最优控制波形与 Output ON。温控恢复为 5 V DC +
Output ON，Pump/Probe、Pump 载波/门控和 HF2 设置保持，正常结束只断开 TEC。
