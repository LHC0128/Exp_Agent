---
title: 射频相位响应
type: Rf_Phase_Response
experiment_id: rf-phase-response
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/rf-phase-response.yaml
mapping_keys:
  Z_magnetic_field:
    role: fixed_pi_double_pulse_dg_direct_drive
  X_magnetic_field:
    role: fixed_x_bias
  Time_sequence_2:
    role: common_trigger_and_reference_clock
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: mf_mod_carrier_and_demod4_readout
  main_magnetic_field:
    role: main_field_restored_on_exit
  temperature:
    role: optional_tec_target_only
required_devices:
  - DG4000
  - DG900/DG4000（温控开关）
  - MFLI
  - GS200
  - TEC103（可选设温）
learned_notes:
  - 11-25 实机记录：Y_RF_VPP 取 0.5 Vpp 时 Demod 4 读出饱和，0.05 Vpp 保持线性；默认取 0.05 Vpp，换探测器或量程后需重核。
  - 1200B 功放已物理旁路，DG4000 高阻档直驱 Z：Z_PULSE_VPP 上限 20 Vpp；线圈峰值电流按已知回路电阻（采样 10 Ω + 阻尼 10 Ω + 线圈 2 Ω + DG 源阻抗 50 Ω）估算约 138.9 mA，不是直接测量值。
  - 启用 MF-MOD 后 Demod 1–3 被调制器占用；原子读出固定 Demod 4（索引 3）、参考 Osc 2（索引 1），载波与边带振荡器分别固定 Osc 2 / Osc 3（索引 2）。
  - 整周 36 点（0–350°、10°）扫描后按实测切线把 Demod 4 相位转到工作点（回读偏差不超过 0.5°）；B01 按运行目录记录的 rotation_deg 把原始帧旋转到对齐帧，36 点解 5 个自由参数后还剩 31 个残差自由度。
  - 拖尾补偿默认关闭，只发原升余弦 ±π 双脉冲；历史 v1/v2 CSV 目录继续可分析，v2 无旋转做理论式 (2.2.2) 拟合，只有 R/θ 的 v1 回退三分量分解。
---

# 射频相位响应

在原子域表征射频相位响应：**MFLI Signal Output 1** 的 **MF-MOD** 无调制载波经串联大电阻驱动 Y 向 5 kHz 射频场，MFLI 用独立解调器 **Demod 4**（参考 `Osc 2`）读该频率上的同步 X/Y；Z 通道由 `Time_sequence_2` 外触发无限 burst，连续打 +π 与 −π 各一个固定宽度脉冲。A03 逐点改 MFLI 载波指令相位（`Y_RF_PHASE_START/STOP/STEP_DEG`，默认 0–350°、10° 共 36 点），每点在独立关温控窗口内采集一段同步 X/Y，恢复温控并回读后再落盘；扫描结束后按实测切线把 Demod 4 相位转到工作点并回读。B01 离线按 A03 记录的 `rotation_deg` 把原始帧旋转到对齐帧，对 Y(φ) 用理论式 (2.2.2) 整周拟合，给出 K₀、工作点与相量闭曲线。

当前默认值以[默认参数文件](../../params/experiments/rf-phase-response.yaml)为准，单次运行以该次的 `experiment_config.yaml` 为准。本实验只研究相位响应本身，不承担 π 宽度标定；宽度标定见 [π 脉冲宽度原子域定标](pi_width_scan.md)。

## 判据

对齐后的 Y 分量满足含静态微失衡偏置的理论式 (2.2.2)

```
Y(φ) = Y0 + A·v / (1 + v²),   v = v0 + r·sin(φ − φc)
```

- `φc`：色散曲线中心相位；`v0`：静态微失衡偏置；`r`：工作点偏离中心的归一化幅度；
- `K₀ = A·r·cos(δ)`、`δ = arcsin(−v0/r)`：过零点斜率（V/rad），即工作点相位灵敏度；
- `φz1 = φc + δ`：Z 分量过零对应的射频相位，图与摘要都标出。

拟合以 `(Y0, K₀ = A·r, b = r², v0, φc)` 参数化，弱场 `r → 0` 时退化为 `Y0 + K₀·sin(φ − φc)`，避免把简并的 `A` 与 `r` 单独解释。整周拟合的 5 个自由参数至少需要 6 个相位点。

质量判据为 `rms_v ≤ 5% × Y(φ) 峰峰值`：满足记 `fit_quality = good`，否则 `poor`；规则与限值随摘要一起写出（`fit_quality_rule`、`fit_quality_limit_v`）。X 分量不直接进拟合，只用 `[1, sin(φ−φc), cos(φ−φc), y_unit]` 做低阶正交回归拼出闭合相量轨迹；设计矩阵退化时把 X 取常数并在摘要标 `locus_degenerate = true`。

## 链路

- **Y 向射频**：MFLI Signal Output 1 → 串联大电阻 → Y 方向射频线圈；MF-MOD 用 `Osc 2`（索引 1）作 5 kHz 载波、`Osc 3`（索引 2）作 2° 峰值边带调制的基带振荡器。本实验只输出无调制载波，上下边带保持关闭，但调制深度 `mods/0/index = 2°` 仍写入并回读。不再使用 DG4000 Y 通道。
- **读出**：MFLI Signal Input 1 接探测器，`Demod 4`（`DEMOD0_IDX = 3`）参考 `Osc 2` 连续输出同步 `sample.x/sample.y`。启用 MF-MOD 后 `Demod 1–3` 被调制器固定占用，不能用它们做原子读出。
- **Z 通道**：`Z_magnetic_field` 由 DG4000 直驱 Z 线圈，1200B 已物理旁路；DG 负载配成高阻（`INF`），写入的 `Z_PULSE_VPP` 即开路电动势，回路电阻计 `Z_DRIVE_SOURCE_IMPEDANCE_OHM`（DG4000 固定 50 Ω）+ 阻尼 + 线圈 + 采样。`X_magnetic_field` 只给静态 DC 偏置。拖尾补偿默认关闭，只发原升余弦 ±π 双脉冲。
- **共同触发**：`Time_sequence_2` 主输出 5 kHz 方波接 Z 外触发端；Z 通道用无限 burst、burst 相位固定 0，射频相位只由 MFLI 载波指令角决定。DG4000 与 MFLI 共用 10 MHz 外部参考。
- **主磁场与温控**：GS200 按 `MAIN_FIELD_CURRENT_MA` 设主磁场（0 表示关输出），收尾恢复运行前状态；`TEC103` 可选，只下发目标温度，不启停也不判稳。不使用 DHO900/示波器，也不连接功放。

## 阶段

| 节点 | 内容 |
| --- | --- |
| A01 参数、映射与安全限值预检 | 不连接硬件；核对 5 kHz 载波、Demod 4 读出、相位网格非空、脉宽大于两倍沿宽且不超过半个周期，MFLI Signal Output 1 的偏置与 2° 调相包络、`Z_PULSE_VPP`、按回路电阻估算的峰值电流、X/触发电平与电阻平均功耗全部在限内。 |
| A02 连接设备、同步 10 MHz 并准备 MF-MOD 载波 | 连接 DG 的 Z/X/trigger/temp、MFLI、GS200 与可选 TEC；不连 Y 通道、1200B 与示波器。DG 直驱 Z 并配高阻档、按 `X_BIAS_V` 设 X 静态偏置、共同触发按 5 kHz 方波回读；MFLI Demod 4 按参数配置并参考 `Osc 2`；Signal Output 1 输出 MF-MOD 无调制载波并逐节点回读（载波幅值 = `Y_RF_VPP/2`、偏置 `Y_BIAS_V`、指令相位 0°）；主磁场按参数快照后设置并回读；温控开关置 5 V ON；DG 与 MFLI 参考时钟严格回读一致。 |
| A03 整周扫描射频相位并读 Demod 4 | 按 `Z_PULSE_WIDTH_US` 上传双脉冲任意波并 arm 无限 burst（DG 直驱，无 1200B 动作）；共享扫描逐点把 MFLI 载波相位写到网格点，每点在关温控窗口内采集 `SCAN_POINT_DURATION_S` 的同步 X/Y，恢复温控并回读、等待 `TEMP_SWITCH_ON_LAG_S` 后写 `raw/coarse_XX.npz`；扫描结束后按实测切线把 Demod 4 相位转到工作点并回读（偏差不超过 0.5°）。取消保留已完成帧。 |
| A04 安全结束会话 | 依次关闭并归零 Z 与 X（各自先确认输出 OFF，再关 Burst/调制、归零并回读）、关闭 MFLI Out1 后清 MF-MOD 载波幅值/边带/输出并归零偏置、恢复温控、停 Demod 4 传输、恢复 GS200 原状态；`Time_sequence_2` 作为共同触发保持运行。 |
| B01 相位响应 (2.2.2) 拟合与相量图 | 独立离线分析：按 A03 记录的 `rotation_deg` 把逐相位 XY 帧旋转到对齐帧，对 Y(φ) 用式 (2.2.2) 整周拟合，给出 K₀、Y0/A/r/v0/φc/φz1、残差 rms、工作点与拟合质量，画响应图与相量闭曲线；历史 CSV 目录按格式分派处理。 |

## 参数要点

参数按采集节点归属并可编辑；修改某节点的参数会使该节点及其后继失效，本轮未安全结束前不允许应用参数。

| 键 | 含义与约束 |
| --- | --- |
| `Y_RF_FREQ_HZ` | MFLI MF-MOD 载波频率，同时决定 Z 双脉冲周期 `1e6/f`（5 kHz → 200 µs）与共同触发频率；本方案只支持 5 kHz。 |
| `Y_RF_VPP` | 载波峰峰值，按 `Vpk = Vpp/2` 写入 `mods/0/carrier/amplitude`；界面可调。11-25 实机记录 0.5 Vpp 会读出饱和、0.05 Vpp 线性，默认取 0.05 Vpp；实际取值以单次运行的 `experiment_config.yaml` 为准。 |
| `Y_BIAS_V` | 叠加在 MFLI Signal Output 1 的直流偏置（`sigouts/0/offset`）；与 2° 调相包络之和必须在 ±10 V 安全限内。 |
| `X_BIAS_V` | DG X 通道的 DC 静态偏置；0 表示不加偏置并保持通道关闭。 |
| `Z_PULSE_VPP` | DG4000 直驱 Z 的高阻档幅度（Vpp），上限 20 Vpp；峰值 `Vpp/2`，线圈电流按回路电阻估算。 |
| `Z_PULSE_WIDTH_US` | 固定单脉冲宽度；须大于两倍沿宽且不超过半个射频周期。 |
| `Z_PULSE_EDGE_US` | 升/降沿宽度（升余弦），等效面积按「宽度 − 沿宽」计。 |
| `Z_TAIL_COMP_ENABLED`、`Z_TAIL_S_F`、`Z_TAIL_TAU_E_US` | 拖尾补偿开关与屏蔽模型参数（τc = s_f·τe）；默认关闭，只发原升余弦 ± 脉冲。 |
| `Z_DRIVE_SOURCE_IMPEDANCE_OHM` | DG 直驱时计入回路的源阻抗（DG4000 为 50 Ω），用于估算线圈电流。 |
| `SENSE_RESISTOR_OHM`、`DAMPING_RESISTOR_OHM`、`COIL_RESISTANCE_OHM`、`RESISTOR_RATED_W` | 采样/阻尼/线圈电阻与电阻额定功率；共同决定回路电流估算与平均功耗校验。 |
| `Y_RF_PHASE_START/STOP/STEP_DEG` | 整周相位网格；终点不写入，默认 0–360°、10° 共 36 点。 |
| `SCAN_POINT_DURATION_S` | 每个相位点在关温控窗口内的同步采集时长。 |
| `SYSTEM_SETTLE_S` | 关温控后、开始读数前的稳定等待。 |
| `TEMP_SWITCH_ON_LAG_S` | 每点恢复温控（5 V ON）并回读后的等待，之后才落盘。 |
| `MAIN_FIELD_CURRENT_MA` | GS200 主磁场电流（mA）；0 关闭输出，非零按该值输出并回读，收尾恢复原状态。 |
| `DEMOD0_IDX`、`DEMOD0_SIGNAL_RANGE`、`DEMOD0_ORDER`、`DEMOD0_TC`、`DEMOD0_RATE` | 读出解调器（只读固定 3）、Signal Input 1 量程、滤波阶数、时间常数与解调传输率；传输率低于 70 kSa/s，保持 DAQ 同步采集。 |
| `SET_TEC_TARGET`、`TARGET_TEMPERATURE_C` | 可选：只下发 TEC 目标温度，不启停 TEC、不判稳。 |
| `Z_PEAK_CURRENT_MA`、`AMPLIFIER_*`、`RF_DIRECTION`、`Y_RF_PHASE_DEG`、`N_REPEAT`、`TEMP_GROUP_SIZE` | 历史键：只在旧配置与历史快照里保留用于读数，新采集不再使用；DG 直驱时功放键不参与校验。 |

## 数据

- `raw/coarse_XX.npz`：新格式（`raw_format_version = 3`）逐相位原始帧，`XX` 为两位序号。数组为 `time_s, x_v, y_v`；`metadata` 是 JSON 字符串，含 `kind="xy"`、`label`、`rate_sa_s`、`time_source="module_timestamp"`、写入的载波指令相位 `burst_command_deg`/`carrier_command_deg`，以及 `lockin`（Demod 4 相位、时间常数、阶数、振荡器频率、传输率）与 `daq`（实际采样率、采集模块、单位）回读。帧先写临时文件再原子改名，本轮已存在的帧不覆盖。
- `raw/phase_response.csv`（历史格式 1/2）：每行一次读数，字段 `phase_deg, repeat, x_v, y_v, R_v, theta_deg, timestamp`；格式 2 起提供 `x_v/y_v`，格式 1 只有 R/θ。
- `raw/phase_<phase>_<repeat>.csv`（历史格式）：该次读数的完整序列 `time_s, x_v, y_v, R_v, theta_deg`。
- `experiment_config.yaml`：本轮参数快照、`predicted`（载波峰值、pulse_vpp、估算峰值电流、回路电阻、相位网格等）、`phase_response`（工作点 `burst_command_deg`/`carrier_command_deg`、`rotation_deg`、粗定拟合、Demod 4 回读）、`phase_response_acquisition`（扫描模式、帧标签前缀、逐点温控）、`z_waveform`、`temperature_gating`、`data_files` 与 `safety_shutdown`。
- `results/phase_response_summary.json`：`fit_mode`、`model`、`k0_v_per_rad`（`|v0| ≥ r` 时色散零点在扫描范围外，K₀ 不定义并置空）、`k0_fit_v_per_rad`（拟合原始值，仅诊断）、`k0_defined`、`k0_degenerate_reason`、`response_v_per_rad`（旋转不变的 `|c+1|+|c−1|`，弱响应扫描用）、`y0`、`amp`、`r`、`v0`、`phi_c_deg`、`phi_z1_deg`、`rms_v`、`y_peak_to_peak_v`、`fit_quality`/`fit_quality_rule`/`fit_quality_limit_v`、`work_point_deg`、`rotation_deg`、`rotated`、`phases_deg`、逐点 `points`、`components`（`c0/c_plus1/c_minus1`）、`residual_rms_three_component_v`、`locus_degenerate`、`locus`、`demod0_phase_deg`、`lockin`、`frame_failures`（仅 v3 路径）与基础字段（`rf_frequency_hz`、`rf_vpp`、`pulse_period_us`、`demod0_*`、`drift_correction="none"` 等）。
- `results/phase_response.png`：A03 风格两联图，与射频相位灵敏度 A03 共用 `z_chain_common.phase_plot` 的同一绘制实现——左侧为按 `rotation_deg` 对齐的 1:1 相图（X/Y 散点按相位着色，X 幅度过小时附 X 放大插图，标工作点、切线与式 (2.2.2) 轨迹），右侧为整周 X/Y 波形与式 (2.2.2) 拟合。
- `results/phase_response_phasor.png`：相量图——对齐相量轨迹（颜色表示射频相位、箭头指示扫描方向）、式 (2.2.2) 闭曲线、工作点与原点。
- 历史目录兼容：`raw_format_version = 1/2` 继续可读，历史参数快照 `schema_version = 1/2` 迁移到 Demod 4 读出（丢弃已停用的 `DEMOD0_PHASE_DEG`）。

## 判据与限制

- 新格式缺帧或可读点不足 6 个时，摘要写 `error` 与 `fit_mode="unavailable"`，并保存占位图；部分帧不可读时把原因记进 `frame_failures` 后继续拟合。
- `fit_quality` 只按残差 rms 相对 Y(φ) 峰峰值 5% 判定；弱场 `r → 0` 时 `A`、`r`、`v0`、`φc` 相互简并，只把 `K₀` 与 `φz1` 当稳健量。
- X 分量的闭曲线由低阶正交回归拼出，不是独立拟合；`locus_degenerate = true` 时相量图只覆盖 Y 方向，不能当拟合成功。
- 本实验不做漂移校正：逐相位点顺序采集，无重复读数，摘要明确写 `drift_correction="none"`；短时间热漂移会整段抬高或平移 Y(φ)。
- 历史 v1（只有 R/θ）保留原三分量分解与最佳读相位输出：`Z(φ) = c0 + c+1·e^{iφ} + c−1·e^{−iφ}`，至少 3 个相位；v2（带 X/Y）同样做无旋转的 (2.2.2) 拟合。
- 相位轴是 MFLI 载波**指令**相位；`rotation_deg` 是 Demod 4 原始坐标到对齐帧的旋转。若载波相位写入不是即时生效，角度轴会整体平移，K₀ 与 φz1 的解释随之偏移，重跑前需实测确认。
- 两幅图都用 `lab_workflows.plotting` 的 paper 样式（300 dpi，PNG 与 PDF 成对保存），图中文字用英文与 mathtext；`phase_response.png` 为 A03 公共两联图（左右面板用标题区分，无 (a)/(b) 面板标签），相量图仍为单面板。
- 无放大器时 Z 脉冲幅度上限 20 Vpp；脉冲宽度与拖尾补偿改变写入波形，换线圈或屏蔽条件后需重标。

## GUI 使用

在实验中心打开 **射频相位响应**（`/experiments/rf-phase-response`）。「运行」页顶部提供保存参数与布局、派生预览、仅预检和预检并运行；参数在节点内编辑，共享扫描参数（载波、相位网格、逐点温控、脉宽与拖尾）挂在 A03 上，A02 承载链路与读出配置。分步会话按 A01→A04 逐段执行，不自动补选前置；应用参数草稿后需显式重跑受影响节点，本轮未安全结束前不能改参数。执行中通过任务取消，等待真实安全恢复完成再释放硬件锁。「历史」页可查看同实验快照、下载现有产物，并把历史参数填回运行草稿；填回不保存默认值，也不启动实验。

## 待查

- 真机 MF-MOD 载波 + Demod 4 的短期漂移与相位噪声；首轮需核对单轮 `rotation_deg` 与逐点对齐相量是否自洽。
- MFLI 载波相位指令与实际射频相位的对应关系、改相位后需要多久稳定；当前 `SYSTEM_SETTLE_S` 默认 0.1 s 需首轮实测确认。
- 每点「0.2 s 采集 + 2 s 恢复温控」的逐点门控对整轮温漂与总时长的影响，首轮实测后确定是否调整。
- `fit_quality` 的 5% 峰峰值阈值在弱场（r→0）下是否合适、首轮哪些点会被判 poor，需实测确认。
- 无放大器时 `Z_PULSE_VPP = 20 Vpp`（估算峰值电流约 138.9 mA）能否在 8.6 µs 宽度内完成自旋翻转；π 宽度与最佳脉宽待扫。
- MF-MOD 的 2° 峰值调制深度在边带关闭时仍写入并回读；该设置对无调制载波是否有残余影响，需首轮沿时间观察。
