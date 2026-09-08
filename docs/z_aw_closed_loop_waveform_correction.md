---
title: Z 任意波实际电流闭环波形校正
type: Z_AW_Closed_Loop_Waveform_Correction
scan_mode: point_by_point
defaults:
  CONTROL_SOURCE_SET: oc_sens
  MAX_ITERATIONS: 8
  ITERATION_REPEATS: 2
  HOLDOUT_REPEATS: 1
  ITERATION_DAMPING: 0.35
  INVERSE_REGULARIZATION: 0.02
mapping_keys:
  Z_magnetic_field:
    role: iterative_arbitrary_output
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_current_feedback_and_CH4_trigger
required_devices:
  - DG4000
  - SDS
learned_notes:
  - 频响可靠带宽之外保留原命令，不执行逆滤波。
  - 每轮更新前校验 DG 电压范围、预测峰值电流和采样电阻 RMS 功率。
  - 误差恶化、饱和或越界时停止更新并冻结历史最佳波形。
---

# Z 任意波实际电流闭环波形校正

稳定实验 ID 为 `z-aw-closed-loop-waveform-correction`。基础参数中的
`CONTROL_SOURCE_SET` 可选择 `oc_sens` 或 `oc_broadband_v2`，程序固定从
`D:\Code\theory_agent\simulate\results\<CONTROL_SOURCE_SET>\<CONTROL_VERSION>\`
读取 `waveforms/optimal_control_waveform.csv` 和
`parameters/optimal_control_params.csv`。实验先用实际电流频响生成初始预加重，再按
`u_next = u + alpha * H_reg^-1 * error` 迭代。每轮保存原始采集、对齐反馈、命令哈希和独立
hold-out 采集；达到 NRMSE 阈值或误差恶化时停止。

`oc_broadband_v2` 当前只有包含完整任意波和参数 CSV 的版本可运行；本机结果中 `v2` 至
`v5` 可加载，`v1` 缺少目标 CSV，预检会明确报告文件缺失。旧配置中的
`CONTROL_RESULTS_ROOT` 仅用于迁移识别固定结果集，不再作为 GUI 可选路径。

`INVERSE_REGULARIZATION` 是无量纲相对比例。程序先在可靠频点中计算
`H_ref = max(|H|)`，再使用 `lambda = INVERSE_REGULARIZATION * H_ref`
作为与 `H` 同单位的 Tikhonov 正则化尺度。默认 `0.02` 表示可靠频响最大增益的
2%，不能直接作为 `0.02 A/V` 代入。schema v2 会保留旧配置中的同名数值，
但修正了 schema v1 把它误当作绝对 `A/V` 数值的错误。

连接仪器前，日志会明确显示静态 Z 标定生成的基准电压范围、相对及绝对正则化
尺度、动态预加重后的实际命令范围、DG 归一化峰值和预测电流。这样可以在首次
输出前发现毫伏级误缩放或超量程问题。

冻结结果包含时间轴、目标耦合、目标电流、命令电压、DG 归一化波形、Vpp、offset、两类标定运行和哈希。正式 RF 灵敏度测量只加载该文件，不在线继续迭代。

运行期间会实时输出设备配置、每轮反馈与 hold-out 采集进度、NRMSE、最佳轮次、安全检查和最终关闭状态；命令行与 GUI 使用同一日志流。

入口为 `experiments/Z_AW_Closed_Loop_Waveform_Correction.py` 和 `experiments/Z_AW_Closed_Loop_Waveform_Correction_plot.py`。
