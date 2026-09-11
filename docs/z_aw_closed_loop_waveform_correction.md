---
title: Z 任意波实际电流静态闭环校正
type: Z_AW_Closed_Loop_Waveform_Correction
scan_mode: point_by_point
defaults:
  CURRENT_COUPLING_CALIBRATION_SOURCE_RUN: 0820_170037_mx_z_current_coupling_calibration
  CONTROL_SOURCE_SET: oc_sens
  CONTROL_VERSION: v6
  MAX_ITERATIONS: 100
  ITERATION_REPEATS: 5
  ITERATION_DAMPING: 0.1
  ERROR_CUTOFF_HZ: 40000.0
  TARGET_RELATIVE_RMS: 0.03
  REQUIRED_PASSES: 3
  OUTPUT_SETTLE_S: 0.1
  SCOPE_HEADROOM_FACTOR: 1.5
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
  - schema v10 仅使用静态电流标定，不加载频率响应。
  - 每帧只做整体时间平移，误差保留真实幅度和直流偏差。
  - 理论采样电阻电压乘 1.5 倍余量选择 CH3 档位，居中后采集。
  - 误差增大仍继续，不回退或自动减小更新系数。
  - 最终命令必须对应实际采集轮次，不另行低通。
---

# Z 任意波实际电流静态闭环校正

稳定 ID 为 `z-aw-closed-loop-waveform-correction`，采用 `typed_workflow`。
入口为 `experiments/Z_AW_Closed_Loop_Waveform_Correction.py`，离线入口为同名 `_plot.py`。

## 数据与单位

理论波形继续从 `D:/Code/theory_agent/simulate/results/<CONTROL_SOURCE_SET>/<CONTROL_VERSION>/`
下的 `waveforms/optimal_control_waveform.csv` 和 `parameters/optimal_control_params.csv` 读取。
控制耦合强度单位为 Hz，时间单位按来源文件约定解析。

只读取 `CURRENT_COUPLING_CALIBRATION_SOURCE_RUN` 的 `results/analysis.yaml`。
目标电流为 `I_target = CONTROL_SCALE * Omega_ctrl_Hz / K_Z_Hz_per_A`。
实测电流为 `I_measured = V_CH3 / sense_resistor_ohm`，阻值直接取同一标定，不再重复输入。
共振频率标定截距不加入目标电流。

从该文件全部 `curves` 的 `z_bias_v` 与 `current_a` 做带截距普通最小二乘拟合：

```text
I = g * u + b
u_initial = (I_target - b) / g
```

使用全部正反向记录，不按共振曲线拟合成败筛选电流点。斜率保留符号。
0820_170037 的 7 个点给出约 `g=0.00575104275 A/V`、`b=2.9307673e-5 A`、`R²=0.9999457`。
每次运行只拟合一次，保存拟合系数、全部来源快照及哈希。

静态斜率提供修正尺度，不代表所有频率的动态响应相同。
Z 输出沿用当前代码的 50 Ω/Vpp 数值基准，配置后回读幅度、偏置、频率和负载。
历史静态标定没有记录负载显示回读，不能从新设置反推旧设置；实际匹配仍需实机验证。

## 理论波形决定示波器档位

理论采样电阻电压 `V_target = I_target * R_s`，设其极值为 Vmin、Vmax。

```text
所需 V/div = (Vmax - Vmin) * SCOPE_HEADROOM_FACTOR / SCOPE_VERTICAL_DIVISIONS
初始 offset = -(Vmax + Vmin) / 2
```

余量默认 1.5，即理论波形占屏幕高度不超过约 2/3。档位向上选择
`0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10 V/div`；不足 0.01 取 0.01，
超过 10 报错，不截断波形。默认 oc_sens/v6 对应约 `0.5 V/div`。
实际采集时记录当前档位和偏置；若实测不能满足余量，只放大档位或重新居中并重采，
每帧最多三次尝试，全部原始尝试均保存。原始电压越出本帧量程或无效时直接结束。

## 对齐、评价和更新

CH4 下降沿定义每帧时间参考，CH3 为采样电阻电压。
截取采集窗口内完整周期，丢弃两端不完整周期，逐周期重采样到理论固定网格后平均。
每帧在一个周期内搜索使平方误差最小的循环平移，先整数点搜索再局部细化；
同等最优平移优先绝对值较小者。只改变时间，不做幅度拟合、去均值或时间伸缩。
对齐后各帧平均得到本轮反馈，平移量和每帧平均周期均保存。

```text
error = target - measured_aligned_mean
relative_rms_error = RMS(error) / RMS(target)
filtered_error = IRFFT(RFFT(error) * (frequency <= cutoff))
increment = alpha * filtered_error / g
next_command = current_command + increment
```

分母固定为目标 RMS，全零目标在预检阶段拒绝。评价使用低通前误差，同时记录绝对误差 RMS（A）。
滤波保留 DC 和截止频率处的离散频点，不改变目标。
截止频率必须为正且不高于命令网格和实际采样各自的奈奎斯特频率；重采样不增加实际带宽。
误差增量不加入截距，直接加到目标相位网格上的对应命令点，不再反向平移。
alpha、截止频率和静态斜率在一次运行中固定。

## 可执行迭代规则

```python
command = (target - intercept) / gain
pass_count = 0
for k in range(max_rounds):  # 包含初始命令的第一次测量
    upload_and_start(command)  # 写入前执行电压安全检查
    wait_for_settle()
    frames = capture_and_align(repeats)
    measured = mean(frames)
    error = target - measured
    score = rms(error) / rms(target)
    filtered = zero_phase_lowpass(error, cutoff)
    save_measured_round(command, measured, error, filtered, score)
    pass_count = pass_count + 1 if score <= threshold else 0
    if pass_count >= required_passes or k == max_rounds - 1:
        break
    candidate = command + alpha * filtered / gain
    save_candidate(candidate)
    if candidate_exceeds_voltage_range(candidate):
        break  # 越界命令不上传
    command = candidate  # 误差变大也继续，无回退或降步长
```

连续默认 3 轮低于 3% 才标记 `target_reached=true`。轮数上限默认 100，重复采集默认 5 帧。
越界、取消、采集失败或设备失败结束循环。`finally` 关闭 Z 和共同触发、停止示波器并保存状态。
沿用本实验不设置电流/功率上限的约定，DG 电压和原始采集有效性检查仍执行。

从已经完成测量的轮次中按原指标选择最小值，导出其原命令及对应反馈。
选优不改变迭代路径，没有独立 hold-out 验证，也不对导出命令再滤波。
取消或失败前已有有效轮次时仍导出最佳实测轮次；没有有效轮次时只保存失败记录。

## 文件与离线分析

```text
data/Z_AW_Closed_Loop_Waveform_Correction/<run>/
  experiment_config.yaml
  raw/
    theory_waveform.csv
    theory_parameters.csv
    static_calibration_source.yaml
    target.npz
    iteration_000/
      command.npz
      output_readback.json
      scope_capture_000_attempt_00.npz
      scope_capture_000.npz
      alignment_000.npz
      feedback.npz
      update.npz  # 只有实际提出下一轮命令时保存
  results/
    iteration_summary.yaml
    corrected_control_waveform.npz
    corrected_control_waveform.csv
    convergence.png
    waveform_comparison.png
    final_waveform_report.yaml
```

离线分析直接使用保存的反馈和指标，不重新对齐、归一化或改写命令。
图形统一使用 `lab_workflows.plotting` 的 paper 样式，标注为英文。
冻结 NPZ 使用 `format_version=2`，不含频响字段；共享读取器兼容新文件和旧冻结文件。

## 配置与历史兼容

schema v10 删除频响、动态逆、初始幅值匹配、续跑、回退、单轮限幅和最终命令滤波参数。
旧键在参数入口集中移除；正 `TIME_DOMAIN_CUTOFF_HZ` 迁为 `ERROR_CUTOFF_HZ`，
旧值 0 迁为明确的新默认 40000 Hz。旧指标基于目标标准差，旧阈值不直接冒充新指标阈值，
迁移后采用 `TARGET_RELATIVE_RMS=0.03`；显式新键优先。
旧固定示波器档位键移除，统一按理论电压和余量初始化。硬件和目标相关的保留键继续生效。

这是新算法的配置迁移，不承诺重现旧采集算法。原始历史目录不改写；
旧运行由 `legacy_analysis.py` 按历史快照分析。旧流程说明见
[历史 schema v9 文档](old/z_aw_closed_loop_waveform_correction_schema_v9.md)。
