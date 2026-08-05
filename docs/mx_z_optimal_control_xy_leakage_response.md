---
title: Mx Z 最优控制 XY 泄露响应
type: Mx_Z_Optimal_Control_XY_Leakage_Response
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  CONTROL_VERSION: v2
  Z_CALIBRATION_SOURCE_RUN: 0728_161259_mx_z_cal
  CONTROL_SCALE: 1.0
  Z_AW_OUTPUT_VPP: 6.0
  DEMOD_FREQUENCY_HZ: 30000.0
  X_CONTROL_AMP_START_VPP: -0.1
  X_CONTROL_AMP_STOP_VPP: 0.1
  X_CONTROL_AMP_POINTS: 21
  Y_CONTROL_AMP_START_VPP: -0.1
  Y_CONTROL_AMP_STOP_VPP: 0.1
  Y_CONTROL_AMP_POINTS: 21
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger_disabled_on_exit
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
  - HF2
learned_notes:
  - X/Y 使用与 Z 实际上传数组完全相同的归一化采样点和重复频率。
  - 负带符号幅度用绝对 Vpp 和相对 Z 基准相位增加 180° 实现。
  - 正常结束保留实测最小 R 网格点；异常与取消保留当时 X/Y 状态。
  - 所有结束路径均关闭归零 Time_sequence_2，但保留 Z/X/Y 当前输出。
---

# Mx Z 最优控制 XY 泄露响应

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-leakage-response`。实验复用
`Mx Z 最优控制 RF 灵敏度`的 Z 控制来源、Z 标定、GS200、光功率、温度和
HF2 工作点，但不输出独立 Y RF 正弦波，也不执行 RF 校相、噪声或灵敏度分析。

Z 通道输出按控制版本和 Z 标定换算后的最优控制波形。X/Y 通道复制 Z 实际上传的
归一化数组和重复频率，输出偏置固定为 0 V，只扫描硬件 Vpp。正幅度使用
`CONTROL_BURST_PHASE_DEG`；负幅度使用绝对 Vpp，并在该相位基础上增加 180°；
零幅度切换为 `0 V DC + Output OFF`。

`Time_sequence_2` 必须由操作者分配到 Z 控制 DG4000 和 X/Y 控制 DG4000 的
Ext Trig。软件验证两台设备的 Burst 和触发配置，但无法检查实体 BNC 连接。

## 二维扫描与采集

默认 X、Y 均从 `-0.1 Vpp` 扫描到 `+0.1 Vpp`，各 21 点，共 441 个网格点。
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
相位、Output 状态、接受的重试文件和实际 HF2 采样率。

## 分析与结束状态

分析器只读取明确运行目录中的原始数据，生成 R 均值和标准差二维热图，并按
`R 均值最小、采集序号最早`的规则报告实测网格最小点。不执行连续拟合、网格外推
或 X/Y 电压到磁场的换算，因此结果不是泄露磁场绝对标定。

正常完成后，程序同时施加该实测最小点的 X/Y 波形，等待共同触发启动，但不额外
复测组合状态的 R。取消、Ctrl+C 或异常时不切换最佳点，而是保留当时的 Z/X/Y
状态。所有退出路径均将 `Time_sequence_2` 归零关闭、恢复温控为
`5 V DC + Output ON`、恢复 GS200 运行前完整状态，并保留 Pump/Probe、Pump
载波/门控和 HF2 设置。由于这是显式安全例外，失败后 Z/X/Y 输出可能继续开启。
