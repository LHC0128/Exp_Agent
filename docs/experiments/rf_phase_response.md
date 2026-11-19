---
title: 射频相位响应
type: Rf_Phase_Response
experiment_id: rf-phase-response
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/rf-phase-response.yaml
mapping_keys:
  Z_magnetic_field:
    role: fixed_pi_pulse_and_amplifier_input
  Y_magnetic_field:
    role: rf_excitation_5khz
  Time_sequence_2:
    role: external_burst_trigger
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: demod0_r_theta_readout
  coil_drive_amplifier:
    role: power_amplifier
  coil_current_scope:
    role: manual_pulse_waveform_check
  main_magnetic_field:
    role: output_off_during_run_then_restored
  temperature:
    role: cell_temperature_readout
required_devices:
  - DG4000
  - MFLI
  - Aigtek 1200B
  - DHO900
  - GS200
learned_notes:
  - 默认脉冲取自运行目录 `1118_220918_pi_width_scan` 的精确 π 波形：5 kHz、50 mVpp、190 mA、8.03 µs，拖尾 s_f 0.544 / τe 46.9 µs（A 0.11545 ≈ −21.9 mA、τc 25.51 µs）；X/Y 偏置取同次重标值。
  - 整周 36 个相位点解 3 个复分量，剩余 33 个自由度可直接当模型残差用；默认每点只读 1 次。
  - 0.2 Vpp 以内射频响应线性，一次谐波分解足够；更高次谐波在 1 mV 量级。
  - 相位响应带 c0 时椭圆被推离原点，R 最大相位不等于 |c−1| 最大；本实验同时给模型 |Z| 最大与实测 R 最大两个角度。
  - 8.03 µs π 波形下相位响应仍不对称：|c0| 约 12.7 mV 大于 |c±1|，R(φ) 两峰约 27.6 与 2.1 mV（`_pi_width_investigation_20261118/10_tail_compensation/结论.md` §4.6）。
---

# 射频相位响应

在原子域表征射频相位响应：Y 向 5 kHz 射频持续激励，MFLI 第 0 路解调器在该频率读 R/θ；Z 通道由 `Time_sequence_2` 外触发无限 burst，连续打固定宽度的 +π 与 −π 脉冲（相隔半个周期）。逐个射频 burst 相位读数，离线把复数读数分解为三个分量，给出相位响应曲线、模型残差与最佳读相位。

本实验只研究相位响应本身，不承担 π 宽度标定；宽度标定见 [π 脉冲宽度原子域定标](pi_width_scan.md)。

## 判据

射频在线性区时读数严格满足

```
Z(φ) = c0 + c+1 · e^{iφ} + c−1 · e^{−iφ}
```

- `c0`：与射频相位无关的 5 kHz 背景，由静态横场与脉冲共同产生；
- `c+1`：共旋分量，没有脉冲时也存在；
- `c−1`：反旋分量，只由脉冲把自旋翻转时出现。

N 个相位点解 3 个复分量后，残差 `residual_rms_v` 用来检验模型；`model_best_phase_deg` 是模型 |Z(φ)| 最大的相位，`measured_best_phase_deg` 是实测 R 均值最大的相位。两者不一致时说明 c0 把椭圆推离原点，按 R 选读相位会偏离真正的最敏感点。

## 链路

Z 通道链路与 π 脉冲宽度实验一致：DG 的 Z 通道 → Aigtek 1200B（20 dB、1 MΩ 输入、50 Ω 输出）→ 阻尼电阻 → Z 线圈 → 10 Ω 采样电阻 → 回功放地。`Time_sequence_2` 输出 5 kHz 方波同时触发 Z 与 Y 两条无限 burst。

- Y 向射频：`Y_magnetic_field` 5 kHz 正弦，频率 `Y_RF_FREQ_HZ`、幅度 `Y_RF_VPP`；相对相位只由 burst 相位决定，A02 用 `Y_RF_PHASE_DEG` 起振。
- 改相位顺序：关闭 burst → 写 burst 相位 → 打开 burst → 打开 output，再等 `SYSTEM_SETTLE_S` 才读数。
- Z 脉冲：一次上传双脉冲任意波（相位固定 0）后 arm 无限 burst 并发 1200B 的 AON；`Z_TAIL_COMP_ENABLED` 时每个脉冲后接该脉宽的精确反号指数尾。
- 读出：MFLI 的 Signal Input 1 → 第 0 路解调器，参考频率设为 `Y_RF_FREQ_HZ`，读 R 与 θ。
- X 通道按 `X_BIAS_V` 加静态偏置；Y 射频叠加 `Y_BIAS_V` 直流 offset，两者默认取运行目录的重标值。
- GS200 主磁场在 A02 关闭输出并回读为 OFF，A04 恢复运行前状态。

## 阶段

- **A01** 参数、映射与安全限值预检（不连硬件）：相位网格由起点/终点/步进生成且非空；脉宽大于两倍沿宽且不超过半个射频周期；`z_pulse_coil_current`、Z/Y 场电平、触发电平与电阻平均功耗全部在限内。
- **A02** 连接 DG、MFLI、1200B、DHO900、GS200 与 TEC（可选）：写功放增益与阻抗、配平 Z 通道负载；按 `X_BIAS_V` 加静态偏置、Y 射频叠加 `Y_BIAS_V`；配置 MFLI 第 0 路解调器并把参考频率设为射频频率；射频与 Z 都按无限 burst 起振；关闭 GS200 主磁场输出并回读；DG 与 MFLI 参考时钟严格回读。
- **A03** 扫射频相位：Z 双脉冲按 `Z_PULSE_WIDTH_US` 只写一次；逐角度按 DirectAW 顺序改射频 burst 相位；每个角度读 `N_REPEAT` 次，读数按 `TEMP_GROUP_SIZE` 分组——组首关闭温控并等 `SYSTEM_SETTLE_S` 再读，组末恢复温控并等 `TEMP_SWITCH_ON_LAG_S` 后落盘，关温控窗口内不写文件；写 `raw/phase_response.csv` 与逐点序列 `raw/phase_<phase>_<repeat>.csv`。取消时保留已完成读数。
- **A04** 安全结束：先关 1200B，Z 通道 DC 0 V 并关 burst/sync，关 Y 射频，停 MFLI 解调传输与 DHO900，恢复温控与 GS200 原状态，再断开全部设备；`Time_sequence_2` 保持运行。
- **B01** 离线分析：逐相位汇总 X/Y/R/θ，最小二乘解 `c0/c+1/c−1`，给出残差、模型最佳相位与实测最大相位，写 `results/phase_response_summary.json`、相位响应图 `results/phase_response.png` 与相量图 `results/phase_response_phasor.png`。

## 参数要点

| 键 | 含义 |
| --- | --- |
| `Y_RF_FREQ_HZ` | Y 向射频频率，同时决定脉冲周期 `1e6/f`（5 kHz → 200 µs） |
| `Y_RF_VPP` | Y 向射频幅度 |
| `Y_RF_PHASE_DEG` | A02 起振时的 burst 相位 |
| `Y_RF_PHASE_START/STOP/STEP_DEG` | 相位扫描网格；终点不写入，默认 0–360°、10° 共 36 点 |
| `Z_PULSE_WIDTH_US` | 固定的单脉冲宽度；不能超过半个周期，且不小于两倍沿宽 |
| `Z_PEAK_CURRENT_MA` | 脉冲峰值电流；−π 取相反号 |
| `Z_PULSE_EDGE_US` | 升余弦沿宽；等效面积按「宽度 − 沿宽」计 |
| `Z_TAIL_COMP_ENABLED` | Z 拖尾补偿开关；开启后按 s_f、τe 与脉宽生成精确反号指数尾 |
| `Z_TAIL_S_F` / `Z_TAIL_TAU_E_US` | 屏蔽模型快分量比例与慢分量时间常数；τc = s_f·τe |
| `N_REPEAT` | 每个相位的读数次数；默认 1，逐点标准误为 0，需要统计误差时再调大 |
| `SCAN_POINT_DURATION_S` | 每次读数在关温控窗口内的连续采样时长 |
| `SYSTEM_SETTLE_S` | 改相位或关温控后的稳定等待 |
| `TEMP_GROUP_SIZE` | 每关闭温控一次连续采集的读数个数 |
| `TEMP_SWITCH_ON_LAG_S` | 组末恢复温控后、落盘前的等待 |
| `X_BIAS_V` / `Y_BIAS_V` | X 静态偏置与 Y 射频直流 offset |
| `DEMOD0_*` | 第 0 路解调器的索引、量程、阶数、时间常数与采样率 |

## 数据

- `raw/phase_response.csv`：每行一次读数，字段 `phase_deg, repeat, x_v, y_v, R_v, theta_deg, timestamp`；`x_v/y_v` 是解调器正交分量，格式版本 2 起提供，版本 1 只有 R/θ
- `raw/phase_<phase>_<repeat>.csv`：该次读数的完整序列 `time_s, x_v, y_v, R_v, theta_deg`
- `experiment_config.yaml`：运行参数、`z_waveform`（各脉宽的尾幅、τc、归一化与 mean(v²)）、`phase_response` 扫描记录、解调相位回读
- `results/phase_response_summary.json`：`phases_deg`、逐相位 R/θ/X/Y 均值与标准误、`components`（`c0/c_plus1/c_minus1` 的 `re_v/im_v/abs_v/arg_deg/sem_v`）、`residual_rms_v`、`fit_quality`、`best_phase_deg`、`measured_best_phase_deg`、`signal_low`
- `results/phase_response.png`：上图为 R(φ) 散点与模型 |Z(φ)|、下图为 θ(φ) 散点（θ 在 ±180° 换支，不连线）
- `results/phase_response_phasor.png`：相量图，实测 Z(φ) 沿相位顺序的轨迹、模型闭曲线、原点与 c0 中心；颜色表示射频相位，箭头指示扫描方向

## 判据与限制

- 至少 3 个相位才能解三个分量；相位不足或缺行时写原因、空摘要与占位图。
- 两张图都用 `lab_workflows.plotting` 的 paper 样式（300 dpi，PNG 与 PDF 成对保存），图中文字用英文与 mathtext；面板标签为 (a)/(b)。
- 残差 RMS 超过逐相位 R 标准误中位数 3 倍时 `fit_quality = poor`；最大 R 小于标准误中位数 5 倍时 `signal_low = true`。
- 本实验不做漂移校正；重复读数只用于给逐点标准误。
- 相位扫描只覆盖写入 DG 的 burst 相位；若 DG 的相位写入不是即时生效，角度轴会整体平移，但三分量模量与最佳相位差不变。
- 默认脉冲的拖尾参数来自运行目录的历史标定，换线圈或屏蔽条件后需重标。
- 固定宽度下的相位响应不给 τπ；它给的是该宽度对应的响应形状与读相位。

## 待查

- DG4000 的 burst 相位在 `INFinity` + 外部触发下是否即时生效、相位分辨率与重启等待触发沿的行为，需示波器核对一次。
- 36 个相位点在 0° 与 360° 附近是否出现周期性拼接误差；首轮实测后用相邻点残差检查。
- 8.03 µs 波形下 |c0| 大于 |c±1|，模型残差在多大相位噪声下仍能判 good，需首轮实测确认阈值。
