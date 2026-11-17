---
title: π 脉冲宽度原子域定标
type: Pi_Width_Scan
experiment_id: pi-width-scan
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/pi-width-scan.yaml
mapping_keys:
  Z_magnetic_field:
    role: pulsed_pi_field_and_amplifier_input
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
  - 判据模型的峰位就是 π 宽度；占空比 2–3.5%、ω/Γ≈25 时峰位稳定在 1.00π，不随射频强度移动。
  - 脉冲周期由 `Y_RF_FREQ_HZ` 反算（5 kHz → 200 µs），+π 与 −π 相隔半个周期，因此脉宽上限是半个周期而不是整个周期。
  - `COIL_CONSTANT_HZ_PER_MA` 只用于预测宽度与事后对照；原子域判据不经线圈常数。
  - 电学域参考宽度默认 5.122 µs，来自 `pi-pulse-calibration` 在 Z 链路的内插结果；由 `REFERENCE_PI_WIDTH_US` 参数给出，仅作比值对照。
  - Z 通道用无限 burst 与射频共同进入稳态；射频相位是参数且必须随运行记录。
  - 相位粗扫只扫 0–180°：`R ∝ |cos(φ − ψ0)|` 的周期是 180°，0–360° 会重复一遍，另一半没有信息量。
  - R 与 MFLI 的参考相位无关（单频解调只看模量），θ 的绝对值依赖它；本实验只用 R，因此不设置解调相位、只回读记录。
---

# π 脉冲宽度原子域定标

在原子域给 π 脉冲宽度定标：Y 向 5 kHz 射频持续激励，MFLI 第 0 路解调器在该频率上读 R/θ；Z 通道由 `Time_sequence_2` 外触发无限 burst，连续打 +π 与 −π 各一个脉冲（相隔半个周期 = 100 µs，周期 200 µs）。扫单脉冲宽度，用判据模型的峰位给出 π 宽度；扫描前可选做一次射频相位粗扫，把读出幅度放到峰上。

## 目的

把「π 宽度」从按线圈常数预测的名义值变成原子域实测值。电学链路（[π 脉冲面积标定](pi_pulse_calibration.md)）只能给出「电学域」的内插宽度，且受 `COIL_CONSTANT_HZ_PER_MA` 的 ±5% 限制；本实验直接看原子响应：脉冲面积越接近 π，自旋翻转越完全，解调 R 越接近峰值。判据模型

```
R(τ) = R_max · |sin(π(τ − τ0) / (2 τ_π))|
```

的峰位即 τ_π，理论核对显示占空比 2–3.5%、ω/Γ≈25 时峰位稳定在 1.00π，不随射频强度移动。

读出的幅度还随「射频相对脉冲串的相位 φ」变化，`R ∝ |cos(φ − ψ0)|`：曲线在 0–360° 内有两个**等高的峰**、相隔 180°，取任一峰即可。峰位（判据）与 φ 无关，但 φ 落在零点附近时信噪比差约 5 倍，所以默认用 `Y_RF_PHASE_DEG`=90°，并可先用 A03 做一次相位粗扫。R 是模量，与 MFLI 的参考相位无关；改射频相位改变的是物理响应本身（射频与脉冲串的相对相位），因此 R 变、θ 的绝对值随参考相位走但不参与判据。

## 链路

Z 通道链路与 π 脉冲面积标定完全一致：DG 的 Z 通道 → Aigtek 1200B（20 dB、1 MΩ 输入、50 Ω 输出）→ 阻尼电阻 → Z 线圈 → 10 Ω 采样电阻 → 回功放地。Z 与 Y 两条通道都由 `Time_sequence_2` 外触发做无限 burst：第一个触发沿启动后连续播放，两者同时进入稳态。

- Y 向射频：`Y_magnetic_field` 5 kHz 正弦，频率 `Y_RF_FREQ_HZ`、幅度 `Y_RF_VPP`，无限 burst 门控；相对相位只由 burst 相位决定，A02 用 `Y_RF_PHASE_DEG` 起振，A03/A04 改相位时不动波形。
- 改相位的顺序（DirectAW 校相的做法）：关闭 burst → 写 burst 相位 → 打开 burst → 打开 output，再等 `SYSTEM_SETTLE_S` 才读数。
- Z 脉冲：一次上传双脉冲任意波（相位固定 0）后 arm 无限 burst，随后发 1200B 的 AON（A02 收尾时功放是关的，不打开则脉冲只到功放输入、到不了线圈）；A03 用 `PHASE_CHECK_WIDTH_US` 写一次，A04 按每个扫描宽度重写。
- 读出：MFLI 的 Signal Input 1（探测器输出）→ 第 0 路解调器，参考频率设为 `Y_RF_FREQ_HZ`，读该频率上的模量 R 与相位 θ；解调档照 DirectAW 校相档配成 1 ms / 100 kSa/s。
- 共同触发：`Time_sequence_2` 输出 5 kHz 方波（固定 5 Vpp、2.5 V 偏置、50% 占空比）接 Z 通道与 Y 射频的外触发输入；它同时是共同触发，安全收尾后保持运行。
- DHO900 接采样电阻仅供人工核对脉冲波形，本实验不采集示波器数据。
- X 通道在 A02 按 `X_BIAS_V` 加静态偏置（默认 0 V、输出关闭），A05 归零并关输出；它用于补掉残余横向场，实测 −0.02 V 可把关射频时的 5 kHz 残留从 12.7 mV 压到 3.9 mV。
- GS200 主磁场在 A02 关闭输出并回读为 OFF，A05 恢复运行前状态。

## 阶段

- **A01** 参数、映射与安全限值预检（不连硬件）：脉宽列表由 start/stop/step 生成，逐项检查非空、不超过半个周期、不小于两倍沿宽；射频相位粗扫的起点/终点/步进必须能生成非空角度列表；`z_pulse_coil_current`、Z/Y 场电平、触发电平与电阻平均功耗全部在限内；检查 Z 信号源的任意波点数与 burst 能力。
- **A02** 连接 DG、MFLI、1200B、DHO900、GS200 与 TEC（可选）：写 1200B 增益与输入/输出阻抗并把 Z 通道负载配成一致；按 `X_BIAS_V` 在 X 通道加静态偏置；按参数配置 MFLI 第 0 路解调器（量程、阶数、时间常数、采样率）并把参考频率设为射频频率，解调相位沿用库默认值并回读记录；射频与 Z 都按无限 burst 起振；关闭 GS200 主磁场输出并回读；DG 与 MFLI 参考时钟严格回读。
- **A03** 射频相位粗扫（可勾选）：Z 脉冲串按 `PHASE_CHECK_WIDTH_US` 只写一次；逐角度按 DirectAW 顺序改射频 burst 相位，随后与 A04 一样按 `TEMP_GROUP_SIZE` 分组采集——组首关闭温控并等 `SYSTEM_SETTLE_S` 再读、组末恢复温控并等 `TEMP_SWITCH_ON_LAG_S` 后落盘，关温控窗口内不写文件；每角度读 `N_REPEAT` 次 R/θ，写 `raw/phase_scan.csv` 与 `results/phase_vs_phase.png`；取 R 峰，峰不在端点时用相邻三点抛物线插值给出亚步进精化角度；`chosen_deg`、`refined_deg`、`R_chosen`、`R_curve` 写进运行配置。`PHASE_CHECK_ENABLED=false` 时本阶段整段跳过、不写任何文件。
- **A04** 扫脉宽（可勾选）：射频相位取 A03 的 `chosen_deg`（有 `refined_deg` 时优先用精化值），A03 未运行或关闭时回落到 `Y_RF_PHASE_DEG`，来源记进摘要的 `phase_source`；逐点上传该宽度的 ±π 双脉冲并 arm 无限 burst；关闭温控、等待 `SYSTEM_SETTLE_S`，在关闭窗口内连续采 `SCAN_POINT_DURATION_S` 的 R/θ，每点重复 `N_REPEAT` 次；每 `TEMP_GROUP_SIZE` 个读数恢复温控并等待 `TEMP_SWITCH_ON_LAG_S`，之后才写盘。取消时保留已完成点。
- **A05** 安全结束：先关 1200B，Z 通道 DC 0 V 并关 burst/sync，关 Y 射频，停 MFLI 解调传输与 DHO900，恢复温控与 GS200 原状态，再断开全部设备；`Time_sequence_2` 保持运行。
- **B01** 离线分析：按宽度汇总 R 的均值与标准误，加权拟合判据模型，给出 τ_π ± 1σ、R_max、残差 RMS 与 keep 判据，产出 `results/scan_summary.json` 与 `results/r_vs_width.png`；摘要同时记录实际生效的射频相位 `rf_phase_deg` 与时间偏移 `rf_phase_offset_us`、`phase_source`、相位粗扫曲线、`REFERENCE_PI_WIDTH_US` 对照与 MFLI 解调相位回读；R_max 小于单点标准误中位数的 5 倍时加 `signal_low` 提示。

## 参数要点

| 键 | 含义 |
| --- | --- |
| `Y_RF_FREQ_HZ` | Y 向射频频率，同时决定脉冲周期 `1e6/f`；默认 5 kHz → 200 µs（1° = 0.556 µs） |
| `Y_RF_VPP` | Y 向射频幅度；默认 0.05 Vpp |
| `Y_RF_PHASE_DEG` | A03 未运行或关闭时使用的固定射频相位，默认 90°（0–360°） |
| `PHASE_CHECK_ENABLED` | 相位粗扫开关，默认开启：关闭则 A03 跳过，A04 用 `Y_RF_PHASE_DEG` |
| `Y_RF_PHASE_START/STOP/STEP_DEG` | 相位粗扫网格，默认 0–180°、步进 22.5°（9 点）；只扫 0–180° |
| `PHASE_CHECK_WIDTH_US` | A03 期间 Z 双脉冲串的单脉冲宽度，默认 5.0 µs；与 A04 的扫描网格无关 |
| `X_BIAS_V` | X 通道静态偏置，默认 0 V（输出关闭）；实测 −0.02 V 把 5 kHz 残留从 12.7 压到 3.9 mV |
| `Z_PEAK_CURRENT_MA` | 固定的脉冲峰值电流，默认 190 mA；−π 取相反号 |
| `Z_PULSE_EDGE_US` | 升余弦沿宽，默认 1 µs；等效面积按「宽度 − 沿宽」计 |
| `Z_PULSE_WIDTH_START/STOP/STEP_US` | 脉宽扫描网格，默认 4.0–7.0 µs、步进 0.5（7 点） |
| `N_REPEAT` | 每个点的读数次数，默认 5；A03 每个角度、A04 每个宽度共用这一项，逐点标准误由这些读数给出 |
| `SCAN_POINT_DURATION_S` | 每次读数在关温控窗口内的连续采样时长（A03 与 A04 共用），默认 0.2 s |
| `SYSTEM_SETTLE_S` | 关温控后的稳定等待（A03 与 A04 共用），默认 0.5 s；A03 每次读数前都会等，因此改相位后也覆盖在内 |
| `TEMP_GROUP_SIZE` | 每关闭温控一次连续采集的读数个数（A03 与 A04 共用），默认 5 |
| `TEMP_SWITCH_ON_LAG_S` | 组末恢复温控后、落盘前的等待（A03 与 A04 共用），默认 0.5 s |
| `DEMOD0_*` | 第 0 路解调器的索引、输入量程（2.0 V）、阶数（4）、时间常数（1 ms）与采样率（100 kSa/s），照 DirectAW 校相档 |
| `COIL_CONSTANT_HZ_PER_MA` / `TARGET_ANGLE_PI` | 只用于预测宽度与结果对照，不参与峰位判定 |
| `REFERENCE_PI_WIDTH_US` | 电学参考 π 宽度，默认 5.122 µs；只进入 `ratio_to_electrical` 并写入摘要 |

θ 的绝对值依赖 MFLI 的解调相位设置（本实验沿用库默认值、只回读记录在 `demod0_phase_deg`），本实验只用 R，不依赖它。

## 数据

- `raw/phase_scan.csv`：A03 相位粗扫读数，字段 `phase_deg, repeat, R_v, theta_deg, timestamp`；A03 关闭时不生成
- `results/phase_vs_phase.png`：R 随射频相位的散点（带误差棒）与选中/精化点标注
- `raw/scan.csv`：`width_us, repeat, R_v, theta_deg, timestamp, rf_vpp, rf_freq_hz, z_peak_ma, temp_c`；每行一次读数的均值（θ 按圆周平均）
- `raw/R_<width>_<repeat>.csv`：该次读数的完整序列 `time_s, R_v, theta_deg`
- `experiment_config.yaml`：`phase_check` 记录粗扫结果（`enabled/chosen_deg/refined_deg/R_chosen/R_curve`），`phase_source` 记录相位来源，`rf_phase_deg`/`rf_phase_offset_us` 记录最后一次写入的相位
- `results/scan_summary.json`：每点均值/标准误/温度、拟合参数 τ_π ± 1σ、R_max、τ0、残差 RMS、`keep` 判据、`fit_quality`、`rf_phase_deg`、`rf_phase_offset_us`、`phase_source`、`phase_check`、`demod0_phase_deg`（MFLI 解调相位回读，可为 `null`）、`signal_low`（信号可忽略时）、与 `REFERENCE_PI_WIDTH_US` 及预测宽度的比值
- `results/r_vs_width.png`：R 随宽度的散点（带标准误）、拟合曲线、τ_π 竖线与预测宽度线

## 判据与限制

- 拟合是三个参数的加权最小二乘（R_max、τ0、τ_π），权重为逐点标准误；至少需要 4 个宽度点。拟合同时给出零点偏移 τ0：曲线在 τ = τ0 + τ_π 处取极大，判据量仍是 τ_π。
- 单点离开拟合曲线超过 3 倍标准误时该点 `keep=false`；残差 RMS 超过单点标准误中位数的 3 倍时 `fit_quality=poor` 并在摘要里写明原因，整体 `keep=false`。单点标准误全为零时（重复读数相同或 `N_REPEAT`=1）无法比较残差与标准误，摘要注明原因且不据此剔除点。
- `R_max` 小于单点标准误中位数的 5 倍时摘要加 `signal_low: true` 与原因，提示可能是射频相位落在零点附近；只提示，不改变拟合与 keep 判定，应回看 `phase_check` 与 `rf_phase_deg`。
- 相位只改变幅度、不改变峰位：射频相位与相位粗扫都不进入判据模型。A03 只在 A04 之前做一次，选出的相位对整轮 A04 有效；扫描中途改相位需重跑 A04。
- A03 与 A04 可以各自单独勾选：只勾 A03 只产出 `raw/phase_scan.csv` 与相位曲线；只勾 A04 直接用 `Y_RF_PHASE_DEG` 扫描（摘要 `phase_source: "parameter"`）。
- θ 的绝对值依赖 MFLI 的解调相位设置（本实验沿用库默认值，只回读记录在 `demod0_phase_deg`），本实验只用 R，不依赖它。
- 本实验不做漂移校正；多次重复只用来给单点标准误。
- 峰位落在扫描区间外时拟合是外推，须扩大 `Z_PULSE_WIDTH_START/STOP_US`。
- 脉宽不能超过半个周期：+π 与 −π 各占半个周期，超过会重叠。
- 脉冲峰值电流是链路换算的名义值；本实验不采集采样电阻波形，没有实测电流守卫。

## 待查

- `DEMOD0_TC`=1 ms / `DEMOD0_RATE`=100 kSa/s 照 DirectAW 校相档设定，但 100 kSa/s 超过 MFLI 自动选模阈值 70 kSa/s：`daq.acquire_data` 会选解调 Scope 采集（需要 MF-DIG 选件，仓库声明的 MFLI 已装），解调传输率不再重写，实际采样率取最近的 Scope 档（约 117 kSa/s）、每点约 2.3 万点；`SCAN_POINT_DURATION_S`×`DEMOD0_RATE`=2 万点只是名义值。真实采样率与时间轴以每次读数的采集元数据为准，需首轮实测确认。
- DG4000 的 burst 相位在 `INFinity` + 外部触发下是否真的决定射频起步相位、相位分辨率与「关 burst → 写相位 → 开 burst」后是否等下一个触发沿才重启，都需要示波器核对；若相位写入不是即时生效，A03 的角度轴会整体平移。
- Z 与 Y 两条通道共用一个 5 kHz 触发沿，长时运行时相对相位是否保持（两次独立 burst 的自由运行各按自身时基）；若漂移，相位粗扫的结果只在短时间内有效。
- 相位粗扫的脉冲宽度由 `PHASE_CHECK_WIDTH_US` 给定（默认 5.0 µs）：真实 π 宽度偏离它时，R 幅度整体变小，但 `|cos|` 的峰位不受影响。
- 抛物线精化只用相邻三点：`|cos|` 不是抛物线，精化值在真实零点附近通常差零点几度，需按 `refined_deg` 的重复性判断是否值得采信。
- 共同触发 5 Vpp/2.5 V 偏置与上升沿、burst 相位 0 是否让脉冲落在周期起点，需示波器核对一次。这三个电平是接线常量（`preparation.TRIGGER_VPP`/`TRIGGER_OFFSET_V`/`TRIGGER_DUTY`），没有做成参数；若现场需要改电平，应连同安全限值一起评审。
- 收尾保留 `Time_sequence_2` 运行（与 DirectAW 的“共同触发持续运行”一致），只关闭 Z 与 Y 的 burst；若该通道是本实验独占、希望收尾后也停，需要单独确认。
- 电学参考值由 `REFERENCE_PI_WIDTH_US` 给出（默认 5.122 µs，取自 `pi-pulse-calibration` 的历史内插结果），重跑电学标定后应同步更新。
