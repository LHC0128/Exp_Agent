---
title: π 脉冲面积标定
type: Pi_Pulse_Calibration
experiment_id: pi-pulse-calibration
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/pi-pulse-calibration.yaml
mapping_keys:
  Z_magnetic_field:
    role: drive_source_and_sync
  coil_drive_amplifier:
    role: power_amplifier
  coil_current_scope:
    role: sense_resistor_and_sync_trigger
required_devices:
  - DG4000
  - Aigtek 1200B
  - DHO900
learned_notes:
  - 内插只用实测 ∫I dt，不依赖线圈常数；`COIL_CONSTANT_HZ_PER_MA` 只用于预测宽度与事后对照。
  - 等效面积按「宽度 − 沿宽」计，升余弦沿吃掉一半沿面积；5.0 µs 宽度对应 4 µs 有效积分长度。
  - 脉冲帧垂直档位由峰值决定（190 mA 约 1 V/div、21 nT/码），基线量化已进入单发散布。
  - 190 mA 以上功放开始压缩（约 207 mA 到顶），峰值上限留 5% 余量。
---

# π 脉冲面积标定（电学域）

固定脉冲峰值、逐个单脉冲宽度打 ±π 双脉冲，用采样电阻原始波形逐发积分出 θ，线性内插出 θ = π 的宽度，并与按线圈常数预测的宽度对比。本实验不连接原子、TEC 或 GS200，只判电学链路的脉冲面积。

## 目的

把「π 脉冲」从「按线圈常数名义算出来的宽度」变成「实测内插出来的宽度」。已有 Z 链路稳定性实验证明：190 mA、5 µs 的脉冲串两小时稳定在 0.970π ± 0.0054π（漂移 0.005π/day），但绝对角度受 `COIL_CONSTANT_HZ_PER_MA` 的 ±5% 与正负支路 5% 差异限制。本实验给出在当前 K 下、与波形无关的宽度内插值，并给出有效平顶电流以便与峰值对照。

## 链路

与 Z 链路实验相同：DG 的 Z 通道 → Aigtek 1200B（20 dB、1 MΩ 输入、50 Ω 输出）→ 阻尼电阻 → 14 匝线圈 → 10 Ω 采样电阻 → 回功放地。DHO900 的 `SCOPE_CURRENT_CHANNEL` 接采样电阻、`SCOPE_SYNC_CHANNEL` 接同步输出并作 NORMal 触发。

- 信号源写升余弦双脉冲：正脉冲宽 `W`、间隔半个周期后负脉冲宽 `W`，周期 `PULSE_PERIOD_US`。
- 峰值 `PULSE_PEAK_CURRENT_MA` 由 Vpp 与链路阻抗换算：`vpp = 2·I/(gain/R_loop)`。
- 等效面积按 `W − PULSE_EDGE_US` 计（升余弦沿宽吃掉一半沿面积）。

## 阶段

- **A01** 参数、映射与安全限值预检（不连硬件）：`z_pulse_coil_current` 与 `Z_magnetic_field` 必须有有限上下界，峰值/偏置电平、电阻平均功耗全部在限内。
- **A02** 连接信号源、1200B、DHO900，写功放增益与阻抗、配平输出负载，参考时钟严格回读。
- **A03** 扫宽度：每个宽度打 ±π 双脉冲，捕获 `SHOTS_PER_WIDTH` 次，每次存 `raw/scan_<width>_<shot>.npz`，逐发写 `raw/scan.csv`。实测电流越限或同步缺失即中止。
- **A04** 安全结束：先关 1200B 输出，再 Z 通道 DC 0 V、关同步与输出，停示波器并断开三台设备。
- **B01** 离线分析：按宽度汇总 θ 的均值与标准误，对正负两支分别做加权线性拟合，求 θ = 目标角 的宽度，并由 ∫I dt 对宽度的斜率给出有效平顶电流；产出 `results/scan_summary.json` 与 `results/scan_fit.png`。

## 参数要点

| 键 | 含义 |
| --- | --- |
| `PULSE_WIDTHS_US` | 扫描宽度列表；至少两个点，默认 4.6–5.4 µs 五点 |
| `SHOTS_PER_WIDTH` | 每个宽度的捕获次数，默认 8（每次捕获 8 发） |
| `PULSE_PEAK_CURRENT_MA` | 固定峰值，默认 190 mA；高于约 207 mA 功放压缩 |
| `PULSE_EDGE_US` | 升余弦沿宽，默认 1 µs |
| `TARGET_ANGLE_PI` | 内插目标，默认 1.0（即 π） |
| `COIL_CONSTANT_HZ_PER_MA` | 仅用于预测宽度与结果对照，不参与实测 θ |

## 数据

- `raw/scan.csv`：`width_us, shot, sign, q_a_s, theta_pi, i_base_a, peak_a, elapsed_s`
- `raw/scan_<width>_<shot>.npz`：`time, sense_v, sync_v, meta`
- `results/scan_summary.json`：逐宽度汇总、两支的拟合斜率/截距/残差、`width_pi` 及其标准误、`effective_edge_us`、`flat_current_ma` 与 `flat_over_peak_ratio`
- `results/scan_fit.png`：θ 随宽度的散点与拟合，标出 π 水平线与预测宽度

## 判据与限制

- 电学判据：两支拟合的 `width_pi` 之差 < 1%（正负对称），残差 < 单发散布。`flat_current_ma` 与 `peak_current_ma` 之比只描述波形过冲，不是线圈常数，不能据此判断 K 的偏差；`width_pi` 本身也依赖 K，K 改变时按 1/K 缩放。
- 内插只覆盖扫描区间；`width_pi` 落在区间外时拟合是外推，须扩大扫描。
- 脉冲帧垂直档位由峰值决定（1 V/div，约 21 nT/码），基线量化已进入单发散布，角度差小于约 0.003π 时无法分辨。
- 本实验只给电学域的 π 宽度；原子域验证另见 `theory_agent/simulate/pi_pulse_readout`（RF 解调相位读出）与后续实验。

## 待查

- 采样电阻/阻尼电阻的实际温漂对 Vpp→电流换算的影响未实测。
