---
title: Z 链路零点与脉冲角稳定性
type: Z_Chain_Stability
experiment_id: z-chain-stability
execution_mode: typed_workflow
scan_mode: time_series
defaults_file: params/experiments/z-chain-stability.yaml
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
  - 零点长记既写窗口均值到 CSV，也默认每帧存原始波形 NPZ；窗口内噪声与窗口均值分别来自两者，不再单设短时快速段。
  - 示波器存储深度是上限，实际点数与采样率以 preamble 回读为准；触发居中和 STOP 后等待沿用线圈驱动实验。
  - ±π 双脉冲按 100 µs 周期、0 与 50 µs 两个 4 µs 反号脉冲；脉冲角 θ = 2π·K·∫(I−I_base)dt。
  - A04 的目标电流经手动 I–V 换算（k 默认取线圈常数标定 1116_173127 的 0.137027 A/V）写电压，不做 I–V 拟合，也不搜索 Vin0。
---

# Z 链路零点与脉冲角稳定性

当前默认参数：[参数文件](../../params/experiments/z-chain-stability.yaml)。GUI「保存为默认」会更新该文件；单次运行参数以其 `experiment_config.yaml` 为准。本文不重复记录可变默认数值。

## 目的

`线圈驱动链路电学特性` 给出链路传递与脉冲面积，`Z 线圈常数标定` 给出 `K`（Hz/mA）之后，本实验把这条链路当作**时间稳定性**来标定，不连接原子、TEC 或 GS200：

- **A03（可勾选）**：DG 0 V、功放开，测原始零点 `I_off` 的长时漂移（窗口均值序列）与每帧 20 ms 窗口内的噪声（原始波形）；不写任何 I–V 电压。
- **A04（可勾选）**：按目标电流经手动 I–V 换算写电压，记录工作基线的残流漂移与噪声，数据形态与 A03 相同。
- **A05（可勾选）**：连续输出 100 µs 周期的 ±π 双脉冲，脉冲骑在自己的直流偏置 `PULSE_BASELINE_V` 上（默认 0 V，即不写偏置、实际电流为功放零偏），按间隔捕获短窗并逐脉冲积分，给出 `θ=2π·K·∫(I−I_base)dt` 的分布、漂移与正负不对称。

三部分都是表征，不设通过判据；输出的是时间序列、Allan 偏差、漂移率与脉冲角分布。Mx 背景（环境场、主磁场、光频移）不在本实验测量；它此前工作正常，后续 π 实验用脉冲发/参考发差分消掉。

## 接线与映射

| 映射键 | 设备 | 用途 |
|---|---|---|
| `Z_magnetic_field` | DG4000 通道 | 驱动功放输入；同步输出接示波器 |
| `coil_drive_amplifier` | Aigtek 1200B | 线圈驱动功放；A02 按参数写入增益与输入/输出阻抗 |
| `coil_current_scope` | DHO900 | `SCOPE_CURRENT_CHANNEL` 测采样电阻电压（I = V/R_sense），`SCOPE_SYNC_CHANNEL` 接同步输出并作触发 |

1200B 输出须 DC 耦合且返回端对地；示波器地夹接返回地。射频开关不进入本链路，也不串在线圈回路里。

GUI「接线」页的目标接线图已按上表建立，8 条边与「线圈驱动链路电学特性」逐边一致；唯一差别是 A02 会写 1200B 的增益与输入/输出阻抗，接线本身不变。

## 参数

字段、单位与范围以 GUI schema 为准；下表只写含义。

| 键 | 含义 |
|---|---|
| `AMPLIFIER_GAIN_DB`、`AMPLIFIER_INPUT/OUTPUT_IMPEDANCE_OHM`、`AMPLIFIER_WRITE_SETTINGS` | A02 写入 1200B 的增益与阻抗，并把信号源输出负载配成一致；写入值就是预测依据，1200B 无回读 |
| `SENSE_RESISTOR_OHM`、`DAMPING_RESISTOR_OHM`、`COIL_RESISTANCE_OHM`、`RESISTOR_RATED_W` | 采样电阻换算与回路功耗校验；线圈直流电阻取小值时预测电流偏大、偏保守 |
| `ZERO_WINDOW_S`、`ZERO_FRAMES` | 零点/直采窗口与首尾帧数；窗口须落在十格 1-2-5 时基上（如 20、50、100 ms），取 50 Hz 工频周期的整数倍可平均掉工频干扰 |
| `ZERO_LOG_INTERVAL_S`、`ZERO_LOG_DURATION_S` | 零点长记的帧间隔与时长；单帧至少占用约 2 s，间隔短于此值时背靠背采集 |
| `ZERO_SAVE_ALL_FRAMES` | 开启时每帧都存 NPZ，可离线看窗口内噪声；关闭后按 `ZERO_RAW_SAVE_INTERVAL_S` 稀疏保存 |
| `SETTLE_EXCLUDE_S` | B01 做统计与拟合前丢弃开头这一段，用于排除开机或换基线后的热建立；0 表示全部保留 |
| `ZERO_RAW_SAVE_INTERVAL_S` | 仅在关闭「保存每帧原始波形」时生效；0 表示只存均值 |
| `BASELINE_CURRENT_MA`、`CONVERSION_AMPS_PER_VOLT`、`CONVERSION_OFFSET_MA` | A04 目标电流与手动 I–V 换算：`V = (I_target − I_offset)/k`；k 默认取线圈常数标定 1116_173127 的 0.137027 A/V |
| `PULSE_PERIOD_US`、`PULSE_WIDTH_US`、`PULSE_EDGE_US` | ±π 双脉冲周期、单脉冲宽度与升余弦沿宽；单脉冲面积 = 宽度 − 沿宽 |
| `PULSE_PEAK_CURRENT_MA` | 相对基线的 +π 峰值；−π 取相反号；默认按 K 估为约 1.0π |
| `PULSE_BASELINE_V` | ±π 脉冲所骑的 DG 直流偏置，与 `BASELINE_CURRENT_MA` 无关；0 V 表示不写偏置，实测电流为该偏置下的功放零偏 |
| `PULSE_CAPTURE_INTERVAL_S`、`PULSE_CAPTURE_PERIODS`、`PULSE_DURATION_S` | 捕获间隔、每次覆盖周期数与脉冲列持续时长；每次捕获都存原始帧 |
| `PULSE_TAIL_WINDOW_US` | 单脉冲积分窗；离线重算 θ 时用同一窗口 |
| `COIL_CONSTANT_HZ_PER_MA` | Z 线圈常数；`θ/π = 2·K·1000·Q[A·s]` |
| `MAX_MEMORY_DEPTH`、`SCOPE_*`、`SYNC_*`、`SCOPE_ACQUIRE_DELAY_S` | 示波器 AUTO 深度上限、通道、同步触发电平/档位与 STOP 后等待 |

## 安全

- `params/safety_limits.yaml` 的 `z_pulse_coil_current` 与 `Z_magnetic_field` 必须有有限上下界；连接前按写入增益与回路总电阻检查全部计划电流、基线电压、脉冲峰谷和电阻功耗。
- A02 在功放输出关闭、信号源停输出时写增益与阻抗；带载改这些设置被安全回调拒绝；开输出前重做整次预检。
- 每帧实测电流越限、最大量程仍削顶或 NORMal 帧同步峰峰值 <1 V 即中止/丢帧重采；A04 写电压前按手动换算校验。
- 结束先关闭 1200B，再把 Z 通道写 DC 0 V、关闭同步与输出，停止 DHO900，断开三台设备。

本实验是纯电学工具，登记为采集例外，不连接 TEC、不操作温控开关；长记与脉冲段都不进六步温控窗口。

## 流程（运行 Tab）

| 节点 | 内容 | 前置 |
|---|---|---|
| A01 | 参数、映射与安全限值预检，不连接硬件 | — |
| A02 | 连接信号源、1200B、DHO900，同步 Z 信号源参考时钟 | A01 |
| A03 | DG 0 V 零点与长时记录（可勾选） | A02 |
| A04 | 目标电流与手动换算基线（可勾选） | A02 |
| A05 | ±π 双脉冲角分布（可勾选） | A02 |
| A06 | 安全结束 | — |

A03/A04/A05 可单独勾选，也可同晚依次执行。三部分互不继承运行状态：A04 的基线电压只决定 A04 自己的直流工作点，A05 的脉冲偏置只由 `PULSE_BASELINE_V` 决定。顶部提供保存参数与布局、派生预览、仅预检、完整运行、取消与任务日志；参数在节点内按基础/高级编辑。

A03 与 A04 的零点段结构相同：先在功放关/开两态各采 `ZERO_FRAMES` 帧定零偏，再按 `ZERO_LOG_INTERVAL_S` 采满 `ZERO_LOG_DURATION_S`。每帧采一个 `ZERO_WINDOW_S` 窗口，写一行窗口均值到 CSV；`ZERO_SAVE_ALL_FRAMES` 开启时同帧原始波形另存 NPZ。测试时把时长与间隔一起缩短即可，字段与产物不变。

零点段的垂直量程按段记忆：首帧按预期电流选档，削顶时按 1-2-5 逐档上调，定档后整段沿用，不再每帧重新爬档——否则每帧都要多采一帧废数据，单点墙钟时间会翻倍。A03 的档位按 DG 0 V 下的 mA 量级选取，不受 `BASELINE_CURRENT_MA` 影响；A04 才按基线目标选档，并以写基线后实测帧定下的档位作为长记起点。

## 数据

```text
data/z_chain_stability/<MMDD_HHMM_tag>/
  experiment_config.yaml
  raw/
    zero_dg0.csv、zero_baseline.csv                      # timestamp、elapsed_s、mean_v、std_v、current_a
    pulse_angles.csv                                     # 采集时算的逐发 Q/θ，分析会从原始帧重算
    zero_dg0_*.npz、zero_baseline_*.npz                  # 逐帧原始波形（保存每帧时）
    zero_off_*.npz、zero_on_*.npz、baseline_setup.npz、pulse_*.npz   # pulse_*.npz 每次捕获一帧
  results/
    zero_summary.json、zero_timeseries.png/pdf、zero_allan.png/pdf
    zero_window_noise.png/pdf、zero_window_psd.png/pdf
    pulse_angles.csv、pulse_summary.json、pulse_timeseries.png/pdf
    pulse_hist_plus.png/pdf、pulse_hist_minus.png/pdf、pulse_base.png/pdf
```

每个 NPZ 含 `time`（s）、`sense_v`（V）、`sync_v`（V，同步帧才有）与 `meta`（帧类型、`elapsed_s`、时间戳、激励参数、示波器配置快照与 preamble 窗口起点）；时间轴来自 preamble。零点 CSV 每行只有该帧窗口的均值与标准差；逐帧 NPZ 保留窗口内全部采样点，10 000 点、20 ms 时每帧约 160 kB（10 s 间隔约 360 帧、58 MB/h）。脉冲帧为 400 µs 窗口、约 6 250 点，每帧约 100 kB，2 小时 1 440 帧约 145 MB。关闭「保存每帧原始波形」后零点只按稀疏间隔留少量整帧；脉冲帧始终逐次保存。

## 离线分析

- **B01**：读 `zero_*.csv` 给出各段均值、标准差、线性漂移率与其标准误、首尾各 10% 的均值差及其标准误、重叠 Allan 偏差，全部按 `K/(γ/2π)` 换算成 nT；再用逐帧 NPZ 的窗口内噪声算出白噪声底（`窗口内 std / √点数`）并给出实测帧间散布与它的比值，另出窗口内标准差时间序列与各帧 Welch 谱中位数（`zero_window_noise`、`zero_window_psd`）。Allan 的 τ 截到 n/4 且至少 10 个差分，避免长 τ 端只剩一两个差分。每段还记录采样完整性（间隔中位数、最大间隔、超长间隔数）。缺某段或没有逐帧 NPZ 时该段留空，并写出标注原因的占位图，不阻塞其余。
- **B02**：从 `raw/pulse_*.npz` 逐帧重算每发的 `Q` 与 `θ`，写出 `results/pulse_angles.csv`，再分别统计 +π、−π 的均值/标准差/漂移/Allan 与 `θ_+ + θ_-` 不对称；正负两侧各出一张直方图（`pulse_hist_plus`、`pulse_hist_minus`），只覆盖该侧均值 ±4σ，便于看分布形状与偏斜；逐次捕获的帧内基线单独统计（均值、极差、离散台阶数、台阶宽度、线性漂移，以及 `2K·δI·积分窗` 换算出的 θ 敏感度与漂移）并出 `pulse_base` 时间序列（纵轴按 nT，与 A03/A04 的零点段同一单位，可直接对比）——脉冲帧为了容纳 ±2 V 脉冲用较粗的垂直档位，基线会被量化成台阶，这一步把它的量级和影响明确写出来，图上标出实测的 1 ADC 码高度，`pulse_summary.json` 同时记录码宽、每码对应的电流、台阶占几码以及 nT 口径的均值/极差/台阶/漂移，便于区分量化跳变与真实漂移；没有原始帧的历史运行回退到 `raw/pulse_angles.csv`。缺数据时写空摘要、原因与占位图，不把分析完成当作科学质量通过。

原始帧是脉冲角的唯一依据：门限、积分窗或分割规则改动后直接重新分析即可，不必重采；采集时的 `raw/pulse_angles.csv` 只是当时的计算快照。

窗口内噪声与窗口均值是两段时间尺度：CSV 序列的节奏由 `ZERO_LOG_INTERVAL_S` 决定（受单帧约 2 s 开销限制），NPZ 提供每帧 20 ms 内的全部采样点。两者之间（约 0.5 Hz–数十 Hz）没有数据。

## 历史 Tab

列出本实验运行，详情读取该目录的参数快照与 `results/`；「填回参数」只填入运行 Tab，不保存、不启动；「重新分析」绑定该目录。

## 待查

- `K` 的正/负支路差约 5%，脉冲角绝对值仍以标定为准；本实验的分布宽度与漂移是相对量，不受该差影响。
- 每帧都要重新配置、单次布防并读回 ASCII 整帧，实测单帧约 4.2 s（含 `SCOPE_ACQUIRE_DELAY_S` 的停采等待）；零点量程若每帧重爬一次会翻倍到 8 s 以上，现已按段记忆。帧间盲区使约 0.12–50 Hz 频段没有数据；原先按 0.1 s 间隔的「短时快速段」达不到该节奏，已删除，短时噪声改由逐帧 NPZ 提供。
- 连续 ±π 列的 RMS 加热会让功放/线圈热态缓慢变化；本实验不记录温度，漂移成因不归因。
- 310 kHz 并联谐振落在 4 µs 脉冲边沿频谱附近；原始帧保留，形状与振铃可在离线诊断。
