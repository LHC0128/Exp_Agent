---
title: 线圈驱动链路电学特性
type: coil_drive_characterization
scan_mode: point_by_point
experiment_id: coil-drive-characterization
defaults_file: params/experiments/coil-drive-characterization.yaml
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
  - 线性 RL 回路中含拖尾的脉冲面积守恒；最短可用脉宽同时要求峰值到达平台。
  - 示波器削顶时看不到真实电流，量程逐级放大到最大档后才判定越限。
---

# 线圈驱动链路电学特性

当前默认参数：[参数文件](../../params/experiments/coil-drive-characterization.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以运行目录的 `experiment_config.yaml` 为准。

## 目的

在原子中打 π 脉冲的第 1 步，不需要原子。测量 `Z_magnetic_field` 通道 → Aigtek 1200B → 阻尼电阻 → Z 方向线圈 → 采样电阻 → 功放返回地 这一回路的电学特性：

- 功放输入为 0 V 时线圈中的直流零偏电流；
- 实际 I/V 与按面板增益预测值之比（增益核对）；
- 正弦扫频的电流传递系数 |I/V_drive|、相对同步沿的相位与 −3 dB 带宽；
- 0→I 矩形脉冲的上升/下降时间、过冲、∫I dt 与名义面积偏差，以及最短可用脉宽。

结果用于选择 π 脉冲宽度：脉宽应远大于电流上升时间，且以实测 ∫I dt 而非信号源设定值计算脉冲面积。

## 接线与映射

| 映射键 | 设备 | 用途 |
|---|---|---|
| `Z_magnetic_field` | DG4000 通道 | 驱动功放输入；同步输出接示波器 |
| `coil_drive_amplifier` | Aigtek 1200B | 线圈驱动功放；面板设增益与 1 MΩ 输入阻抗，本实验不修改 |
| `coil_current_scope` | DHO900 | `SCOPE_CURRENT_CHANNEL` 测采样电阻电压（I = V/R_sense），`SCOPE_SYNC_CHANNEL` 接同步输出并作上升沿触发 |

1200B 输出须 DC 耦合且返回端对地：示波器地夹接返回地。

## 参数

字段、单位与范围以 GUI schema 为准，下表只写含义。

| 键 | 含义 |
|---|---|
| `AMPLIFIER_GAIN_DB`、`AMPLIFIER_INPUT_IMPEDANCE_OHM`、`AMPLIFIER_OUTPUT_IMPEDANCE_OHM`、`AMPLIFIER_WRITE_SETTINGS` | A02 连接后写入 1200B 的增益（0–20 dB、0.5 dB 步进）与输入（50 Ω/1 MΩ）、输出阻抗（1 Ω/50 Ω），并把信号源输出负载配成与输入阻抗一致（1 MΩ→高阻，50 Ω→50 Ω）。写入值就是预测电流依据，输出阻抗与线圈回路串联、计入回路总电阻。`AMPLIFIER_WRITE_SETTINGS=false` 时不写硬件，只按声明值预测并要求面板一致。输出阻抗默认 50 Ω：本机 1 Ω 档实测静默不出输出 |
| `SINE_PEAK_CURRENT_MA`、`PULSE_CURRENT_MA`、`GAIN_CHECK_CURRENT_MA` | 扫频峰值、脉冲平台、增益核对的目标电流（mA） |
| `FREQUENCY_START_HZ`、`FREQUENCY_STOP_HZ`、`POINTS_PER_DECADE`、`SINE_CYCLES` | 对数扫频范围、密度与每频记录周期数 |
| `PULSE_WIDTHS_US`、`PULSE_REPETITION_HZ`、`PULSE_WINDOW_MULTIPLE`、`PULSE_MIN_WINDOW_US` | 脉宽列表（µs）、重复频率、每个脉冲的记录窗倍数与记录窗绝对下限（窗口 = max(倍数 × 脉宽, 下限)）；最宽脉冲占空比须 <50%。下限是为短脉冲留够 RL 衰减拖尾 |
| `SENSE_RESISTOR_OHM`、`DAMPING_RESISTOR_OHM`、`COIL_RESISTANCE_OHM`、`RESISTOR_RATED_W` | 回路电阻与电阻额定功率；回路总电阻另加 `AMPLIFIER_OUTPUT_IMPEDANCE_OHM`，线圈电阻取 0 使预测电流偏大、偏保守 |
| `ZERO_FRAMES`、`ZERO_WINDOW_S`、`GAIN_TOLERANCE`、`GAIN_LOWER_RATIO`、`SETTLE_S` | 零偏帧数、零偏/增益每帧采样时间（取 50 Hz 工频周期 20 ms 的整数倍可平均掉工频干扰）、增益上偏容许倍数与下偏容许比例、输出切换后等待 |
| `SCOPE_ACQUIRE_DELAY_S` | 示波器整帧 STOP 确认后、停止并读取波形前的等待（s）。真机固件在 STOP 置位后波形缓冲尚不可读，立即读取会返回空数据（报「期望 N，实际 1」）；取 0 表示不等待，仅作 A/B 对照。每帧实际等待值随 `meta.scope.requested.acquire_delay` 保存 |
| `SCOPE_*`、`SYNC_*`、`MAX_MEMORY_DEPTH` | 示波器通道、同步触发电平/档位与最大存储深度 |

示波器不设采样率：每帧记录窗取 1-2-5 序列的十格主时基（扫频为 `SINE_CYCLES ÷ 频率`，脉冲为 `max(PULSE_WINDOW_MULTIPLE × 脉宽, PULSE_MIN_WINDOW_US)`，零偏/增益为 `ZERO_WINDOW_S`），触发居中，因此触发后可用窗口只有总窗长的一半，脉冲记录窗下限就是为短脉冲留够这段拖尾。存储深度设为 AUTO，由示波器给出采样率。`MAX_MEMORY_DEPTH` 是**上限**而不是精确深度：AUTO 点数不超过上限时照用，超过则改用上限深度；部分时基下示波器给不出上限内的确定深度（实测 5 µs/div 显式设 10000 点会回读与窗口不对齐的采样率），此时退回 AUTO 深度并如实记录。实际采样率与点数由回读换算，随每帧写入 `raw/*.npz` 的 `meta.scope`。上限 10000 时每帧 6250–31250 点（211–737 KB）、单轮 38 帧约 8 MB。

`MAX_SAMPLE_RATE_SA_S`（schema v1）已删除，读取旧参数时自动忽略。
| `AREA_TOLERANCE` | 离线判定最短可用脉宽的容差；历史目录可独立覆盖 |

## 安全

- `params/safety_limits.yaml` 的 `z_pulse_coil_current`（A）必须有有限上下界，否则预检拒绝。连接前按写入的增益与回路总电阻检查全部计划电流、信号源电平（`Z_magnetic_field` 限值）和电阻平均功耗（不超过额定一半），并核对记录窗参数可组成合法时基。
- A02 连接后按参数写入 1200B 增益与输入/输出阻抗，并把信号源输出负载配成与输入阻抗一致；写入顺序是信号源停输出 → 功放停输出 → 写输入/输出阻抗 → 写增益 → 配负载。1200B 无回读，写入值即预测依据，同时记入运行状态；功放输出开启时 safety_check 拒绝再改这些设置。
- 1200B 开启前经驱动 `safety_check` 重做整次预检；本实验只发送开/关命令与上述参数内的增益/阻抗写入。
- 每帧实测电流越过限值、增益核对实测 I/V 超过预测 `GAIN_TOLERANCE` 倍或低于 `GAIN_LOWER_RATIO` 倍、或最大量程仍削顶时立即中止。
- A05 每次 `setup_pulse` 后回读 `:PULSe:WIDTh`：与设定相差超过 0.1% 即中止，并把实际脉宽、上升/下降沿写入每帧 `meta`。DG4000 会把小于下限的脉宽静默钳位（边沿约为周期的 1/512、最小脉宽约为边沿的 1.6 倍：50 Hz→62.5 µs、1 kHz→3.1 µs、20 kHz→156 ns），错误队列仍为 0，只有回读能发现。
- A04/A05 用 NORMal 同步触发，依赖信号源同步信号。DG4000 同步只在主输出 OFF→ON 边沿且边沿时同步已使能才起振，状态位回读不可靠，故每个扫描段首帧用 `arm_sync()`（关输出→开同步→开输出）锁存一次，并每帧用同帧实测同步通道峰峰值判定：低于 1.0 V 即丢帧重采（最多 4 次），仍缺失则中止并安全收尾（A03 用 AUTO 强制触发，不依赖同步、不做此检查）。
- 结束（正常、异常、取消、通信中断）先关闭 1200B，再把 Z 通道写 DC 0 V、关闭同步与输出，停止 DHO900，断开三台设备。1200B 无状态查询，输出状态为最近写入的命令。

纯电学工具，登记在 `acquisition_policy.ACQUISITION_EXCEPTIONS`，不连接 TEC、不操作温控开关；每帧采集后立即写入 `raw/`。

## 流程（运行 Tab）

| 节点 | 内容 | 前置 |
|---|---|---|
| A01 | 参数、映射与安全限值预检，不连接硬件 | — |
| A02 | 连接信号源、1200B、DHO900，同步 Z 信号源参考时钟 | A01 |
| A03 | 功放关态采基线 → 开功放、信号源 0 V 采零偏 → 小直流核对增益 | A02 |
| A04 | 正弦扫频，同步上升沿触发，逐频保存 | A03 |
| A05 | 0→I 矩形脉冲阶跃，触发居中，记录窗 = max(倍数 × 脉宽, 下限)、触发后可用 ≥2.5 倍脉宽 | A03 |
| A06 | 安全结束 | — |

A04 与 A05 只依赖 A03，可单独执行或重跑。顶部提供保存参数与布局、派生预览（信号源电平、预测 I/V、点数）、仅预检和完整运行。参数在节点内编辑：增益、电阻、示波器与等待参数在 A03 编辑，修改后 A03–A05 全部失效；`SETTLE_S`、`SCOPE_ACQUIRE_DELAY_S`、`MAX_MEMORY_DEPTH` 同时显示在 A04/A05。草稿须「应用参数到会话」后显式重跑。

参数变化后再次运行测量模块会新建运行目录；若 A03 仍有效，其零偏与增益帧复制到新目录（`zero_inherited_from` 记录来源），原目录不改动。每个测量模块完成后目录即停止写入，会话等待期间也可离线分析。

## 数据

```text
data/coil_drive_characterization/<MMDD_HHMMSS_tag>/
  experiment_config.yaml     # 参数、零偏、增益核对、时钟与收尾记录
  raw/zero_off_NN.npz、zero_on_NN.npz、gain_check.npz、sine_NN.npz、pulse_NN.npz
  results/transfer.json、summary.json、coil_drive_transfer.png、coil_drive_phase.png、
          coil_drive_pulse.png、coil_drive_sine.png
```

结果图一张只画一个物理量：`coil_drive_transfer`（幅频 + −3 dB 参考线与交点）、`coil_drive_phase`（相频）、`coil_drive_pulse`（脉冲响应）、`coil_drive_sine`（逐频点时域波形，各频点共用纵轴范围，避免逐面板自动缩放抹平幅值差异）。样式按 `docs/plotting_style.md` 的 `paper` 配置：面板不写标题、默认无网格、坐标标签用数学模式并写明 `pk` 与单位，脉宽等连续量用 viridis 顺序色标加色条而不堆叠图例，每张图同时导出同名 PNG 与 PDF；缺对应数据的图不生成。

每个 NPZ 含 `time`（s，触发为 0）、`sense_v`（采样电阻电压，V）、`sync_v`（V）与 `meta`（JSON：帧类型、激励参数、示波器配置快照，以及波形 `preamble`：`xorigin`/`xincrement`/`xreference` 与读取窗口起点 `window_start_points`、是否居中 `window_centered`）。时间轴就是由 preamble 生成，`raw_format_version=1`。

## 离线分析

- **B01** 零偏 = （功放开均值 − 功放关均值）/R_sense；增益核对 I/V；每个扫频帧以给定频率做含直流项的正弦最小二乘拟合，得到 |I/V_drive| 和相对同步沿的相位，−3 dB 带宽以最低频点为参考对数插值。未运行 A04 时传递函数为空。每个扫频帧同时输出到时域诊断图 `coil_drive_sine.png`：实测电流与拟合正弦叠画，逐帧标注频率、|I/V|、相位与拟合质量 `q = 1 − 残差RMS/(A/√2)`，用于核对削顶、失真、丢周期与拟合偏差。
- **B02** 每个脉冲以触发前中值为基线、脉宽 80%–97% 段均值为平台，求 10–90% 上升/下降、50% 延迟、过冲与含拖尾 ∫I dt；以最宽脉冲平台为名义电流比较面积，并给出峰值达到平台比例。每个上升沿再拟合 `I(t) = b + I_p(1 − e^{−(t−δ)/τ})`，把「同步与触发点的固定延迟 δ」和「电感时间常数 τ」分开，结果写入 `summary.json` 的 `step_fit`（`width_s`、`delay_s`、`tau_s`、`plateau_a`、`baseline_a`、`residual_rms_a`、`frame`），并在 `coil_drive_pulse.png` 上叠画拟合曲线、标注中位 δ 与 τ；τ·R_loop 给出回路电感，δ 与正弦相位联立可核对固定延迟。最短可用脉宽是面积偏差 ≤ `AREA_TOLERANCE` 且峰值 ≥ 1 − `AREA_TOLERANCE` 的最短实测脉宽，不外推。记录窗未包含衰减尾部的帧（下降 10% 穿越落在窗外）整帧跳过并记入 `summary.json` 的 `pulse_skipped`（帧名、脉宽、原因），不因单帧中止整段；只有全部脉冲帧都不可用时 B02 才判失败。

线性一阶 RL 回路中含拖尾的面积守恒，面积偏差反映功放压摆、非线性或记录窗截断；短脉冲是否到达平台另由峰值比例判断。相位包含信号源同步与功放的固定延迟，只作相对比较。

## 历史 Tab

列出本实验运行，详情读取该目录的参数快照与 `results/`；「填回参数」只填入运行 Tab，不保存、不启动；「重新分析」绑定该目录，`AREA_TOLERANCE` 覆盖值只作用于该目录。

## 待查

- 触发点不在帧中心（2026-11-14 定位并修复）：`MAX_MEMORY_DEPTH=10000` 小于该时基的 AUTO 深度时，驱动把深度改成 10000，但屏幕窗口点数 `n = 采样率 × 窗长` 小于该深度（20 µs 窗 6250 点、10 ms 窗 5000 点），而读取固定从 `:WAVeform:STARt 0` 取 `n` 点，于是拿到内存前段而非以触发点为中心的屏幕窗口。同批 42 帧的触发点位置精确等于 `5000/n`（6250→80%、5000→100%、10000→50%），48 帧全部吻合。影响：相位与 |I/V| 不受影响（时间轴仍由 preamble 给出、t=0 仍是同步沿），但 4 µs 脉冲帧只有 +4 µs 后窗、拖尾被截断而进 `pulse_skipped`。修复：RAW 的 `XORigin` 是内存首点时间、`XREFerence` 恒为 0（手册 3.28.7/3.28.8），驱动改为从 `触发点 − 窗长/2` 起读并在时间轴上加窗口起点，居中失败时拒帧；preamble 与读取起点随帧保存。

- DHO900 深帧读回 preamble 点数不符（2026-11-13 真机受控实验定位根因并已修复）：固件在已处 RAW 模式时重写 `:WAVeform:MODE RAW` 不刷新内部点数状态，`:WAVeform:POINts` 被锁死在屏幕默认点数（实测 5000），深帧 preamble 点数因此与配置深度不符，采集器拒绝读帧。修复：`acquire_channel` 读帧前先切 NORMal 再切 RAW 强制重置波形读取状态机。此前 1 s 窗 AUTO 稳定复现（配置 500000 点、preamble 恒 5000），重置后恢复。
- DG4162 同步输出条件与命令语义（2026-11-13 真机受控实验查清，远程命令可靠）：同步发生器只在「主输出 OFF→ON 边沿、且边沿时同步已使能」时起振。输出已开启时写 `SYNC:STATe ON` 只更新状态位，同步口无信号（CH3 实测仅 0.13 V 噪声），面板显示同步已开；补一次输出边沿后立即出现 0→3.5 V TTL，边沿与写 ON 之间是否等待无影响。DC 波形下没有同步。`APPLy` 重绘波形既不产生也不破坏已锁存的同步：一次锁存后 10 Hz→500 kHz 连续 5 个频点、脉冲 50 Hz/1 ms 与 100 kHz 正弦都保持 3.5 V。`SYNC:STATe ON/OFF` 是设置绝对状态、非切换（ON×2 仍 ON、OFF×2 仍 OFF）。受控对照（每例前复位到 DC 0 V/输出 OFF/同步 OFF）：APPLy 后直接写 ON 0/3 有同步；「APPLy→关输出→开同步→开输出」3/3；「APPLy→关输出→开输出→开同步」0/2。该锁存本身偶发失败（多轮 4/6、5/6、4/4 波动），因此 A04/A05 只在各自首帧 `arm_sync()`（关输出→开同步→开输出）一次，同帧同步峰峰 <1.0 V 时丢帧重采、最多 4 次才按 SafetyAbort 中止。此前把根因归为「APPLy 后未重开同步」有误：写 ON 本身不产生同步，必须给输出边沿。CH2 接采样电阻，功放未开时读数近零属预期。
- DHO900 AUTO 深度回读竞态（2026-11-13 真机定位并已修复）：固件改时基后先按旧深度给出采样率，随后才重算 AUTO 深度（实测 1 s 窗先回读 625 kSa/s、再变 500 kSa/s），按帧校验会把好帧判成「帧参数与已配置的严格采样请求不符」。修复三处：深度与采样率一起稳定才算确定深度（间隔 0.25 s）；快照采样率改由已核对的点数与主时基换算；整帧不符时丢帧重做一次，仍不符才上抛。1 s↔20 ms↔1 ms 反复换挡 6/6 通过。
- DG4000 脉冲边沿与最小脉宽（2026-11-13 真机实测）：脉冲边沿时间上限约为周期的 1/512（50 Hz→39.06 µs、1 kHz→1.95 µs、20 kHz→97.66 ns），最小脉宽约为边沿的 1.6 倍（50 Hz→62.5 µs、1 kHz→3.1 µs、20 kHz→156 ns）。请求更短的脉宽会被静默钳位、错误队列仍为 0：50 Hz 下请求 10/20/50 µs 实际都是 62.5 µs、39 µs 边沿的梯形，这是 1113_204232/1113_195157 里 50 Hz 短脉宽帧呈斜坡的原因。A05 现已回读校验并在钳位时中止；`PULSe:HOLD` 若处于 DUTY，脉宽还会由占空比反推，写脉宽不生效，配置脉冲时应先设为 WIDTh。
- 脉冲记录窗下限（2026-11-13 真机定位并已修复）：记录窗原先只取 `PULSE_WINDOW_MULTIPLE × 脉宽` 且触发居中，1 µs 脉宽只得 5 µs 窗、触发后仅 2.5 µs，而 τ≈0.66 µs 的衰减要 ~3.3 µs 才到 10%，尾部落到窗外，B02 报「t>1e-06 s 内未找到 0.1 的穿越」使整段分析失败（1113_233438、1113_234008 两轮 5 kHz/200 mA/1-2-5-10 µs 采集本身完整）。现记录窗取 `max(倍数 × 脉宽, PULSE_MIN_WINDOW_US)`（默认 20 µs），并让 B02 跳过截断帧、记入 `pulse_skipped`。同一批数据里 2/5/10 µs 帧本来就能算，说明是窗口而不是脉冲本身的问题。
- 同步锁存的实现位置：`lab_workflows/steps/sync_output.py` 的 `arm_sync_output()`（关输出→开同步→开输出），线圈实验 A04/A05 首帧调用；判据仍是同帧实测同步幅度，不要用 `SYNC:STATe` 回读。
- STOP 置位后波形缓冲不可读的固件内部原因未确认；`SCOPE_ACQUIRE_DELAY_S` 的默认值是诊断验证值，不是已验证的最小必需等待，也不保证充分：2026-11-13 真机 20 ms 窗 + 1.5 s 等待 6/6 完整（0 s 对照未复现空数据）；但 3 ms 窗（156.25 MSa/s）下即使 1.5 s 等待，紧跟 DG 改波形/开关同步/开关输出之后仍连续 3 次复现「期望 10000，实际 1」空数据。空数据为间歇性，与窗口/采样率或 DG 状态变化的关联待查。2026-11-13 19:51 起用上限 10000 连跑 38 帧（含 A04 全部 20 个频点、A05 7 个脉宽）未再复现。
- DHO900 ASCII 波形流偶发丢分隔符：2026-11-13 真机整帧读回出现相邻数值间无逗号（如 `+3.226667e-02+4.533333e-02`），导致解析失败/「分块点数不符」；与同步问题独立，驱动层需重读或校验。
- 增益比 ≈0.078 已解释（2026-11-13）：1200B 面板增益实为 10 dB（3.162 倍）而不是声明的 20 dB，且输出阻抗档位是 50 Ω、与 22 Ω 回路串联使回路总电阻变成 72 Ω。预测应为 3.162/72 = 0.0439 A/V；10 Hz 帧实测 9.67 mA / 0.22 V = 0.04395 A/V（差 0.1%）。原预测按 20 dB + 22 Ω 得 0.4545 A/V，比值 0.077 由此而来，不是功放或接线故障。现已把增益与输入/输出阻抗做成参数、由 A02 写入并把输出阻抗计入回路总电阻，增益核对同时校验上偏与下偏。
- 1200B 输出阻抗 1 Ω 档不出力（2026-11-13 真机受控实验，待查原因）：面板切换到 1 Ω（写入生效、面板显示 1 Ω）后功放静默不出输出——正弦 10 Hz、0.22 V 峰值驱动下，功放开/关的采样电阻峰值都是 ~3 mA（只有 DG 直通串扰），实测 I/V 0.00027 A/V，而按 10 dB/1 Ω 应得 0.1375 A/V。逐档对照：20 dB/50 Ω 实测 0.1347（预测 0.1389，差 3%）、10 dB/50 Ω 实测 0.0404（预测 0.0439，差 8%），均正常。因此 `AMPLIFIER_OUTPUT_IMPEDANCE_OHM` 默认取 50 Ω；1 Ω 档的固件/保护原因待厂家或面板观察确认。
- 增益核对用直流 0.046 V 驱动，实测约为同一配置正弦值的 0.8 倍（10 dB/50 Ω：直流 0.0358、正弦 0.0404 A/V）；`GAIN_TOLERANCE`/`GAIN_LOWER_RATIO` 的 1.25/0.5 已容下该差异，但不宜再收紧下偏比例。

