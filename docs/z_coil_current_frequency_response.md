---
title: Z 线圈实际电流频率响应
type: Z_Coil_Current_Frequency_Response
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: swept_sine_drive
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_low_side_sense_CH4_trigger_optional_CH1_drive_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - CH1 驱动参考可选；未接 CH1 时用 DG 设定幅值归一，频响幅值可用，相位只相对触发点。
  - 未接 CH1 或其拟合质量不足时，结果不能作为闭环相位标定。
  - 相位随频率应平缓变化；相邻点相位折叠说明参考点取错，插值将失效。
  - 闭环按可靠覆盖自动收缩学习频带，未覆盖谐波既不插值也不外推。
experiment_id: z-coil-current-frequency-response
defaults_file: params/experiments/z-coil-current-frequency-response.yaml
---

# Z 线圈实际电流频率响应

当前默认参数：[参数文件](../params/experiments/z-coil-current-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `z-coil-current-frequency-response`。实验沿**对数等间隔频率轴**
逐点扫描从 `FREQUENCY_START_HZ` 到 `FREQUENCY_STOP_HZ` 的实际电流频率响应，
点数由 `FREQUENCY_POINTS_PER_DECADE` 按覆盖整个区间推算（只读显示为 `FREQUENCY_POINTS`）。
实验可不接 CH1；此时响应幅值按 DG 设定驱动幅值归一，测得相位包含启动时延，只用于观察，
不作为闭环相位标定。接入有效 CH1 时，同帧实测驱动相量才可给出消除启动时延的 I/V 复传递函数。

## 接线

| 通道 | 用途 |
|---|---|
| CH1（可选，`SCOPE_REFERENCE_CHANNEL`） | Z 通道输出经 T 型接头分一路；不接时设为 0 |
| CH3（`SCOPE_MEASURED_CHANNEL`） | 跨接低端采样电阻，按 `I=V_R/R_s` 换算实际电流 |
| CH4（`SCOPE_TRIGGER_CHANNEL`） | 共同触发，2.5 V 下降沿 |

接入 CH1 时，参考通道必须与电流通道**同一帧、同一时间轴**。实验早期版本以 CH4 下降沿作相位原点，
相干启动时延 Δt 会在线性相位上叠加 `2πf·Δt`（Δt 约 ms 级，在 1 kHz 以上即旋转多圈），
使对数扫频的相邻点相位差远超 π、展开相位失效。未接 CH1 时按命令幅值归一，幅值响应仍可测，
但绝对相位会保留启动时延；若 CH1 正弦拟合相关性低于 0.98，分析也会回退到命令幅值参考。

驱动固定为 DG 内置正弦 + 外部下降沿无限 Burst，每帧 `phase_init` 两路后相干重触发，
输出使用 High-Z/Vpp 数值基准，配置后回读幅度、偏置、频率和负载并校验一致。

## 采集

每个频率点重复 `SCOPE_REPEATS` 次（相位标准差判据需要至少两次重复）。
记录窗覆盖 `RESPONSE_SETTLE_CYCLES`（丢弃相干启动后的 L/R 瞬态）
加 `SCOPE_CYCLES` 个待分析周期，再加一个保护周期；由于 SDS 的 `timebase_delay=0`
把触发点放在记录窗中央，总时长取所需触发后窗口的两倍
（`SCOPE_CENTERED_CAPTURE`），并保持不小于共同触发两个周期。

采样率按频率动态选择：不低于 `SCOPE_MIN_SAMPLE_RATE_SA_S`，尽量保持
`SCOPE_SAMPLES_PER_CYCLE` 点/周期，不超过 `SCOPE_SAMPLE_RATE_SA_S`。
每帧都按实测阻值检查线圈电流与采样电阻功率。

## 分析与结果

对每帧以 CH4 下降沿为时间原点，取丢弃周期之后的完整周期，对 CH3 电流做
`offset + a*sin + b*cos` 最小二乘拟合。有效 CH1 存在时，`H = I_phasor / V_drive_phasor`；
否则 `H` 按 DG 设定的驱动幅值归一。未接 CH1 的相位保留相干启动时延。同一频率的重复帧做复数平均，
幅值响应可靠性判据：无示波器饱和且电流正弦拟合相关性 > 0.95；使用实测 CH1 时，复响应可靠性还要求驱动参考拟合相关性 ≥ 0.98、重复相位标准差 < 15°。未接 CH1 时只给幅值可靠性，`phase_reliable` 恒为假：

- 无示波器饱和
- 电流拟合相关性 > 0.95
- 实测 CH1 参考模式下重复相位标准差 < 15°

结果保存为 `results/frequency_response.npz`（`format_version=4`、
`output_protocol=coherent_swept_sine_current_response`、`command_voltage_reference=50_ohm`、
`phase_reference` 标记使用实测驱动或命令幅值作参考），含幅值可靠性掩码与 `phase_reliable` 相位掩码、帧数、拟合相关性、
相位标准差、饱和标志、采样电阻阻值与**可靠覆盖区间**
（`reliable_frequency_min_hz`/`reliable_frequency_max_hz`）；
另有 `frequency_response.yaml/json` 与 `transfer_function.png`。
不可靠点不删除，但会在报告里列入 `excluded_frequency_hz`。

## 闭环如何消费

只有 `phase_reference=measured_drive_voltage` 的运行可供闭环
（`z-aw-closed-loop-waveform-correction`）按目标波形周期网格上的每个谐波
`k·f_rep`，在**相邻可靠扫频点之间**按对数幅度与展开相位线性插值取 H：

- 不跨不可靠点插值：任何不可靠频点都会切断插值区间；
- 绝不外推：落在可靠覆盖之外的谐波不参与修正；
- 学习频带（`ERROR_CUTOFF_HZ`）按可靠覆盖自动收缩，并在日志中报告收缩后的频带、
  可靠覆盖范围与未覆盖谐波个数；
- 学习频带内没有任何被可靠覆盖的谐波时预检失败。

## 采集时长

低频点的记录窗由共同触发周期下限保证（100 Hz 触发 → 每帧不少于 40 ms 记录），
因此 75 点 × 3 次重复的总时长由低频段主导。若要压缩时长，可提高
`FREQUENCY_START_HZ`、降低 `SCOPE_REPEATS` 或放宽 `RESPONSE_SETTLE_CYCLES`。

## 历史兼容

`format_version=3` 的逐谐波 AW 网格协议（`TARGET_REPEAT_FREQUENCY_HZ`、`AW_POINTS`）
已废弃：它把标定与某一个目标波形强绑定，且每帧需要重新上传任意波。
历史运行目录保持原样，但闭环不再接受其 `frequency_response.npz`，需要按新协议重新标定。
schema 3 及更早的默认 YAML 中的旧键在参数入口被丢弃。

入口为 `experiments/Z_Coil_Current_Frequency_Response.py` 和
`experiments/Z_Coil_Current_Frequency_Response_plot.py`。
