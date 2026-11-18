---
title: 横向静态场补平（10 kHz 偶部）
type: bperp_null
experiment_id: bperp-null
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/bperp-null.yaml
mapping_keys:
  Z_magnetic_field:
    role: pulsed_pi_field_and_amplifier_input
  X_magnetic_field:
    role: x_static_bias
  Y_magnetic_field:
    role: y_static_bias
  Time_sequence_2:
    role: external_burst_trigger
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: demod0_1f_and_demod1_2f_readout
  coil_drive_amplifier:
    role: power_amplifier
  main_magnetic_field:
    role: output_off_during_run_then_restored
  temperature:
    role: cell_temperature_readout
required_devices:
  - DG4000
  - MFLI
  - Aigtek 1200B
  - GS200
learned_notes:
  - 关射频后静态 Bx、By 只进入 10 kHz 偶部；拖尾、α≠π 与拖尾横向分量随脉冲反号，主要进入 5 kHz 奇部，所以 10 kHz 复振幅零点与 Bz、拖尾、脉宽无关。
  - 当前残余 Bz/2π ≈ 1426 Hz（约 204 nT）、Γ/2π ≈ 548 Hz；6.6 µs 处 10 kHz 的 2×2 条件数约 3.9，8.0 µs 会丢一个方向，7.0 µs 时 5 kHz 接近盲点。
  - 两路解调器必须共用同一个 5 kHz 振荡器（第 0 路谐波 1、第 1 路谐波 2），参考相位都写 0；第二个振荡器不参与。
  - 判据用 10 kHz 的 X/Y 复数零点，不用 R 的极小；R 的极小在噪声下有偏。
  - 模型预言的零点偏移：拖尾横向分量每 ε=0.02 约 13–18 nT、线圈轴倾斜约 4.4 nT、泵浦倾斜约 1.6 nT；本实验不换算 nT，只在报告里列为已知系统项。
  - 一次运行只测一轮网格；把 B01 的零点填回 `X_BIAS_MV`/`Y_BIAS_MV` 后再跑下一轮，脉宽对照也走独立运行。
  - 2026-11-18 首轮实测（190 mA、6.6 µs、±100 mV 网格）：2f 复振幅约 5 mV，重复间差约 0.03 mV；零点 +86.0/−13.4 mV（±8.0/±2.1 mV），用下一轮偏置居中重测为 +91.7/−15.6 mV（±2.2/±0.5 mV），相对偏置只移 5.7/−2.3 mV。
  - 同轮把脉冲峰值降到 0.1 mA，2f 只剩约 0.005 mV：10 kHz 没有可测的电气拾取，判据信号完全来自原子。
  - 5 kHz 零点在 −17 mV，与 2f 零点相差约 100 mV；没有线圈常数时无法判断这是 slow_tilt 的电压当量还是 2f 特有的系统项，列为待查。
  - 5.0 µs 脉宽下 2f 信号明显变弱、平面拟合不稳（零点 −354±68 mV），脉宽对照需要更长平均或更大网格。
---

# 横向静态场补平（10 kHz 偶部）

## 目的

在 π 脉冲模式下把原子处的静态横向场 Bx、By 补平：关射频，Z 向连续打 +π/−π，MFLI 用两个解调器分别读 5 kHz 与 10 kHz 的复数 X/Y，用 **10 kHz 复振幅 c2 = 0** 作判据，给出 X、Y 信号源的直流补偿电压及其不确定度。

本实验只输出直流电压（V）和 c2 对偏置电压的复斜率（mV/V），不做线圈常数、nT/V 或补偿电流换算。

## 判据

| 场 | 两个半周期之间 | 进入 P_x 的频率 | 单独能否产生 P_x |
| --- | --- | --- | --- |
| 静态 Bx、By | 相同 | 10 kHz（偶部） | 能 |
| 静态残余 Bz | 相同 | 不单独出现，改变偶部斜率 | 不能（泵浦沿 z） |
| 理想 π 脉冲 | 反号，但 R(+π)=R(−π) | 等效于相同 | 不能 |
| 转角偏离 π、Z 慢拖尾、有限脉宽 | 反号 | 5 kHz（奇部） | 不能 |
| 拖尾自带的横向分量 | 反号 | 5 kHz 为主 | 能 |

所以 B⊥ = 0 时 c2 = 0，且零点不随 Bz、拖尾和脉宽移动；已知系统偏移来自拖尾横向分量、线圈轴倾斜和泵浦倾斜，量级见 frontmatter 的 learned_notes。

## 数据与判据

- 解调器 0：振荡器 0（脉冲频率，5 kHz），谐波 1，相位 0 → `c1 = X1 + iY1`。
- 解调器 1：同一振荡器，谐波 2，相位 0 → `c2 = X2 + iY2`（判据）。
- 两路在同一个 DAQ 采集里同时订阅，每个网格点重复 `N_REPEAT` 次。

## 阶段

- **A01**：参数、映射与安全限值预检；脉宽不超过半个周期且不小于两倍沿宽；X/Y 网格非空；X/Y/Z 电平与 `z_pulse_coil_current` 在限内；两路解调器同速率且走 DAQ；用波形文件时按下节规则校验文件，电阻功耗按文件的 mean(v²) 计算（内置脉冲仍按占空比）。
- **A02**：连接 DG、MFLI、1200B、GS200（TEC 可选）；严格回读参考时钟；写 1200B 增益/阻抗并配平 Z 负载；振荡器 0 = 脉冲频率，第 0 路谐波 1、第 1 路谐波 2，相位 0 并回读；按 `X_BIAS_MV`/`Y_BIAS_MV`（mV，后端换算成 V）给 X、Y 线圈加直流（Y 通道不出射频）；GS200 主磁场默认关闭并回读，`MAIN_FIELD_ENABLED` 开启时改为按 `MAIN_FIELD_CURRENT_MA` 设直流电流、回读电流后打开输出并回读（见下节）；温控 ON。
- **A03**：先上传 Z 任意波并 arm 无限 burst（内置脉冲或波形文件；文件原样复制到 `raw/z_waveform.csv`，SHA-256 与注释头写入 `experiment_config.yaml` 的 `z_waveform`，复制前核对文件自上传后未被改动）；再在 `X_BIAS_MV`/`Y_BIAS_MV` 附近按 `X_SCAN_*`、`Y_SCAN_*` 偏移扫网格；每点先写 X/Y 偏置，再按温控分组关窗、等 `SYSTEM_SETTLE_S`、采 `SCAN_POINT_DURATION_S`；组末恢复温控并等待后落盘；写 `raw/bias_grid.csv`。
- **A04**：安全收尾。
- **B01**：按 (x,y) 聚合重复读数，复平面拟合 `c2 = a + b·x + c·y`，解 `c2 = 0` 得补偿电压与蒙特卡洛不确定度；对 `c1` 做同样拟合，给出 5 kHz 零点、两个零点之差与补偿点处的残余 `|c1|`；写 `results/null_summary.json`、`results/next_run.yaml`、`results/null_map.png`（2f）、`results/null_map_1f.png`（1f）、`results/component_maps.png`（2f/1f 并排）、`results/complex_plane.png`、`results/residuals.png`；图与摘要的横纵轴都用绝对补偿电压（mV），不是相对偏置的偏移。

## 主磁场直流偏置（提高 |Bz|）

拖尾补偿后，2f 雅可比较弱方向的灵敏度只剩基线的 0.3–0.5 倍。这一方向的灵敏度来自 z 向场：拖尾消除后，只剩残余 Bz ≈ 1.4 kHz。
- `MAIN_FIELD_ENABLED` 开启时，A02 先快照 GS200 运行前状态，关闭输出，按 `MAIN_FIELD_CURRENT_MA`（mA，带符号，按 `main_magnetic_field` 安全限值校验）设电流。电流回读一致后才打开输出，输出回读为 ON 才继续；任一回读失败即中止，输出保持关闭。A04 恢复运行前状态。
- 换算：约 11 kHz/mA（8.07 mA ≈ 90 kHz，2026-11-18 用户给出）。残余 Bz 的方向未知，所以取 ±0.40 mA：两种符号下合成 |Bz| 约 3.0 或 5.9 kHz。按模型，补偿波形下较弱方向灵敏度分别约为现状的 1.9 倍和 6.7 倍。
- 关闭时（默认）行为与以前相同。

## Z 波形文件（拖尾补偿）

`Z_WAVEFORM_FROM_FILE` 关闭时 Z 输出内置 ±π 升余弦脉冲（与以前相同）；开启时上传 `Z_WAVEFORM_FILE`，下拉列出运行目录 `data/z_waveforms/` 下的 `*.csv`。

- 用途：屏蔽筒使原子处 Z 场约 54% 瞬时出现、其余以 τe ≈ 47 µs 滞后（s_f ≈ 0.544）。在每个脉冲后接反号指数尾 −A·exp(−(t−w)/τc)，τc = s_f·τe，A 由模型精确给出，脉冲后的 z 场为零；峰值不变，π 宽度因此变长（190 mA 时约 7.93 µs）。原理与模型验证见运行目录 `data/pi_width_scan/_pi_width_investigation_20261118/10_tail_compensation/结论.md`。
- 格式：UTF-8 文本；`# key: value` 注释行（必须有 `period_us`，建议有 `width_us`），其后正好 16384 行，一个周期的归一化电流，从 +脉冲上升沿起算；最大绝对值 1 对应 `Z_PEAK_CURRENT_MA`。DG4000 按 ±1 → ±Vpp/2 固定映射，不按内容重新缩放。
- 预检拒绝：点数不等于 16384、含非有限值、最大绝对值不等于 1、首尾跳变超过 0.05、缺 `period_us` 或与脉冲周期不一致、`width_us` 与 `Z_PULSE_WIDTH_US` 不一致、文件名带路径。电流上限仍按 ±`Z_PEAK_CURRENT_MA` 校验。
- 生成：`python tools/make_z_waveform.py --width 7.93 --s-f 0.544 --tau-e 46.9`（按模型给精确尾），或 `--tail-a 0.12 --tau-c 25.5` 直接给尾参数；`--tail-a 0` 得到与内置相同的脉冲。尾的周期叠加使最大值略高于主脉冲平顶（约 0.3%），注释 `normalization_scale` 记录该比例。
- `bias_grid.csv` 的 `width_us` 列仍取 `Z_PULSE_WIDTH_US`，所以用文件时两者须一致。

## 运行序列

| 运行 | 脉宽 | 偏置 | 偏移范围 | 目的 |
| --- | --- | --- | --- | --- |
| R1 | 6.6 µs | 0 / 0 | −40…+40 mV，步 20 | 粗定零点 |
| R2 | 6.6 µs | R1 的零点 | 建议范围（`next_run.yaml`） | 验证零点与不确定度 |
| R3/R4 | 6.0/7.0 µs | R2 的偏置 | 同 R2 | 脉宽对照 |

每次运行的 B01 会把下一轮建议（`X_BIAS_MV`/`Y_BIAS_MV`，单位 mV）写入 `results/next_run.yaml`，手工填回运行参数即可；不自动改 `params/` 默认值。

## 限制

- 只测电压，不测电流；X/Y 线圈的 nT/V 常数未定，因此模型预言的 nT 级零点偏移暂不能换算成电压。
- 5 kHz 通道可能含触发方波的电气拾取（理想 50% 方波没有偶次谐波，所以 10 kHz 判据受影响很小）；5 kHz 残余量只作指示量。
- 残余 Bz 必须保持非零；Bz = 0 时 2f 判据会丢一个方向。主磁场电流选错符号可能把 |Bz| 抵消到接近零，所以电流大小要让两种符号下都远离零。

## 首轮实测记录（2026-11-18）

| 运行 | 条件 | 2f 零点（相对当轮偏置） | 备注 |
| --- | --- | --- | --- |
| `1118_115459_bperp_null` | 6.6 µs、190 mA、±100 mV 网格、2 次重复 | +86.0 / −13.4 mV（±8.0 / ±2.1） | 首次完整运行 |
| `1118_115831_bperp_null` | 6.6 µs、190 mA、偏置居中 ±24 mV、3 次重复 | +5.7 / −2.3 mV（±2.2 / ±0.5） | 验证上轮零点 |
| `1118_120202_bperp_null` | 脉冲 0.1 mA、其余同上 | — | 2f 仅约 0.005 mV，排除 10 kHz 电气拾取 |
| `1118_120437_bperp_null` | 5.0 µs、190 mA、其余同上 | −354 ± 68 mV | 信号弱、拟合不稳，仅作初步对照 |

已知问题：连续两次运行之间偶发 `VI_ERROR_RSRC_NFOUND`（A02 连接失败）和一次 `VI_ERROR_TMO`（A03 第 16 次读数），先跑 `tools/check_devices.py` 再重试即可恢复；与工作总结里记录的间歇性 VISA 故障一致。
