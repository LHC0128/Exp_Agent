---
title: 射频相位灵敏度
type: rf_phase_sensitivity
experiment_id: rf-phase-sensitivity
execution_mode: typed_workflow
scan_mode: point_by_point
defaults_file: params/experiments/rf-phase-sensitivity.yaml
mapping_keys:
  Z_magnetic_field:
    role: fixed_z_double_pulse
  X_magnetic_field:
    role: fixed_x_bias
  Time_sequence_2:
    role: common_trigger_and_scope_reference
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: mf_mod_y_rf_and_demod4_readout
  coil_drive_amplifier:
    role: z_coil_power_amplifier
  coil_current_scope:
    role: same_frame_phase_monitor
  main_magnetic_field:
    role: main_field_restored_on_exit
  temperature:
    role: optional_tec_target_only
required_devices:
  - DG4000
  - DG900 或 DG4000（温控开关）
  - MFLI
  - Aigtek 1200B
  - DHO924 / DHO924S
  - GS200
  - TEC103（可选设温）
---

# 射频相位灵敏度

在固定 **Y 向 5 kHz** 连续射频（幅度 `Y_RF_VPP` 可调）和已有 Z 双脉冲链条件下，测量原子读出对 Y 线圈电流相位的静态斜率、基带幅频响应和输入等效相位噪声。输入相位指 DHO924 所测 **CH4 线圈电流监视载波相对 CH3 共同触发上升沿**的相位；不是仪器设定角，也不代表磁场相位的独立绝对标定。结果描述当前装置、驱动与读出链的等效输入噪声，不声称是器件本征相位噪声。

自本版起，Y 向载波、相位调节、**2° 峰值**相干边带调相及 `Y_BIAS_V` 偏置统一由 **MFLI Signal Output 1（`MF-MOD` 选件）**产生，不再使用 DG4000 的 `Y_magnetic_field` 通道，消除了信号源在 Burst/PM 切换和调频时的相位跳变。本实验的 ID、参数、数据目录和历史与[射频相位响应](rf_phase_response.md)独立。每轮自行找工作点并标定，不从旧实验读入斜率、工作点或幅度扫描线宽。

## 接线与预检

- `Time_sequence_2` 主输出分路到 `Z_magnetic_field` 外触发端以及 DHO924 **CH3**；Z 链为 `Z_magnetic_field` → 1200B → 阻尼电阻 → Z 线圈 → 采样电阻 → 功放返回端，沿用既有外触发正负 π 双脉冲接法。
- MFLI **Signal Output 1 (`Out 1`)** 经串联大电阻接 Y 方向射频线圈回路；DHO924 **CH4** 接 Y 回路采样/监视点（不接 Z 电流采样）。CH3/CH4 均为 1×、高阻、DC 电压输入。示波器须处于主时基、普通采集、非滚动且无延迟时基的连续边沿触发，不接管 STOP、单次或录放中的面板，避免覆盖已冻结数据。软件只能核对映射与回读，实际接线须现场确认。
- MFLI 的 **Signal Input 1 (`Sig In 1`)** 接探测器，并与 DG 共用 **10 MHz 外部参考时钟**（`extclk = True`）。由于启用 `MOD 1`（`mods/0`）后 MFLI 硬件固定占用 `Demod 1–3`（分别对应载波与两个边带，其中 `Demod 1` 相移跟随 `mods/0/carrier/phaseshift`），本实验使用 **`Osc 2`（`oscs/1`，5 kHz）** 作为载波与解调共用参考，**`Osc 3`（`oscs/2`）** 作为调制基带振荡器，并将原子信号正交读出固定绑定在完全独立的 **`Demod 4`（`DEMOD0_IDX = 3`）** 上同时采 `sample.x` 和 `sample.y`。示波器与 MFLI 样本没有共用时间戳，不能以两个记录的相位差求传递相位或时延。
- `X_magnetic_field` 提供 X 方向静态偏置，Y 方向直流偏置 `Y_BIAS_V` 叠加在 MFLI `sigouts/0/offset` 上；主磁场由 `main_magnetic_field` 的 GS200 提供，设 0 表示关输出。A01 只解析映射和校验模型、`mfli_dev30661_signal_output_0` 安全限值与总包络（含调相边带与直流偏置）、峰值电流、实际脉冲平均功耗、MFLI `MF-MD` 四通道能力及 DAQ 采样资格，不连接硬件；A02 连接真机后再严格核对实际已安装的 `MF-MD` 与 `MF-MOD` 选件及公共 10 MHz 外部时钟回读。缺少映射、限值、型号或所需能力时拒绝启动，不自动换通道。

## 流程与收尾

| 节点 | 内容 |
| --- | --- |
| A01 参数与映射预检 | 不连接仪器，核对当前参数、映射、`mfli_dev30661_signal_output_0` 限值与 Z 脉冲安全边界。 |
| A02 连接设备并同步公共参考时钟 | 建立设备会话、核对真机 `MF-MD`/`MF-MOD` 选件并写入、严格回读公共 10 MHz 时钟。 |
| A03 整周相位响应扫描与工作点选择 | 先核对示波器与 DG 链路并开启 `MF-MOD` 连续无调制载波；整周调相并拟合最大切线，把该切线转到 `Demod 4` 的 Y 正交分量。 |
| A04 局部 K₀ 标定 | 在工作点两侧对称取内外两档，重复中心并拟合时间漂移；按 CH4 相对 CH3 的实测角度差标定 `K₀`（V/rad）。 |
| A05 基带频率响应测量 | 在线使能 `MF-MOD` **2° 峰值**相干边带 FM（不中断载波、无 Burst/PM 跳变）并核对载波中心；只改 `Osc 3` 基带调制频率，记录同帧电学分析与 `Demod 4` 音调。 |
| A06 无调制噪声测量 | 关闭上下边带、保留 5 kHz 连续载波并复核工作点；前后取同帧 CH3/CH4 复核载波相位中心与幅值，采集无调制基线。 |
| A07 安全结束会话 | 关闭会话并检查安全收尾记录；无前置依赖。 |
| B01 相位频响与输入等效噪声分析 | 独立离线处理已结束的同一运行目录；根据质量门槛输出合格的频带与失败诊断。 |

A03–A06 是同一轮的四段原始记录，必须同一次会话、同一运行目录完成，不能只补后段或跨轮拼接；任一段失败、取消或参数变化后，只能整链 A03–A06 重跑并新建运行目录。

1. **整周相位响应扫描。** 预先一次选定覆盖直流偏置与调相包络的 MFLI 输出量程并固定 `Y_RF_VPP` 对应的峰值 `Vpk = Vpp / 2`（避免中途切换输出挡位产生静态相移）；开启 `MOD 1`（`mode=1`, `freqdevenable=0`, 边带关闭）和 `Out 1`，在线扫描 `mods/0/carrier/phaseshift`（`0–345°`）并拟合 `Z(φ) = sample.x + i·sample.y`，选 `|dZ/dφ|` 最大处而不是 `R` 最大处；转动 **`Demod 4`** 的参考相位使该点切线主要落在 `sample.y`（保持 `MOD 1` 载波相位不动），并回读核对相位设定。零频斜率不是基带最低采样频点，也不使用历史值。
2. **局部 K₀ 标定。** 在工作点两侧取对称的内外两档，重复中心并拟合时间漂移。在 `LOCAL_SPAN_DEG` 跨度内按固定分档等分细扫（首尾各测一次中心）；用首尾中心估计线性漂移并扣除，再分别对内半区和外半区回归，检查局部线性。用 CH4 相对 CH3 的实测物理角度差（rad）拟合 `K₀ = d(sample.y)/dφ`（V/rad），并核对中心漂移、Y 投影和两档局部线性。
3. **相干边带 PM 与幅频扫描。** 保持 5 kHz 载波不断输出，启用 `mods/0/sidebands/0,1/enable` 施加固定 **`2°` 峰值**（`0.03491 rad`）相位调制后采集示波器锚点校验（`MF-MOD` 相干边带切换跳变典型在 `0.01°` 内，如实测偏离超过 `1°` 最多按实测误差直接回调 3 次，确认进入 `1°` 内再继续）。扫描只改变 `Osc 3` 基带频率，不变载波幅值、调制深度或 `Demod 4` 滤波；包含 20 Hz 时首尾都测 20 Hz 作锚点，否则首尾都测最低频点。用同帧 CH3 的上升沿确定实际载波频率，拟合 CH4 载波及其上下边带以分离实际 PM 与残余 AM，记录有效性、相位中心和载波幅值。示波器采用严格同帧双通道采集、自动采样深度，按较大分块读取；驱动对可重试的读帧错误最多重取一整帧，不拼接两次触发。
4. **无调制基线。** 关闭 `mods/0/sidebands/0,1`，保留 5 kHz 连续载波与工作点相移。记录 MFLI `Demod 4` 数据前后各取同帧 CH3/CH4，复核载波相位中心与幅值、剩余调制和锁相配置。幅频与基线使用相同的 MFLI 解调配置；任一检查未通过的数据保留诊断，不当作合格基线。

**温控窗口。** 每条 MFLI 原子读数按「设置实际仪器条件 → 关闭 `Temp_Switch` → 系统稳定等待 → 连续采集到内存 → 打开 `Temp_Switch` → 恢复等待后保存」执行。电学示波器帧在温控打开时获取；关窗期间不写盘。故障时先尝试恢复温控；仅当恢复失败时允许抢救已读数据并记录失败，不能继续测量。`SET_TEC_TARGET` 只授权实验写 `TARGET_TEMPERATURE_C`，不启停 TEC、不等待温度判稳，不记录未测的实际温度；不启用时保留现场目标设定。

**安全收尾。** 先向 1200B 发 OFF。Z 和 X（DG4000 通道）各自先确认输出 OFF，再关闭调制和 Burst（Z 还关闭同步）、归零并在 DC 模式下复核（先切正弦写电平、再切回 DC 以规避 DG4162 在 DC 波形下忽略 OFFSET 写入的固件问题）。MFLI Y 通道先确认 `sigouts/0/on = 0` 后再清零 `mods/0/carrier/amplitude`、关闭 `mods/0/sidebands` 与 `mods/0/output/enable` 并将 `sigouts/0/offset` 归零，随后在 `finally` 兜底后再逐项回读全部节点记录进 `shutdown_readbacks`。输出 OFF、偏置/幅值清零与模式状态是独立检查；只要任一回读不符或不可判定，本轮不能标作安全收尾完成。`Time_sequence_2` 保持运行；恢复 `Temp_Switch` 为 5 V DC、输出 ON，恢复 GS200 原状态与示波器本次触及的面板状态和连续触发（AUTO 深度回读是当前点数，按 AUTO 恢复；恢复 RUN 后等固件重算再回读面板），停止本轮 `Demod 4` 解调传输。示波器恢复前若已有采集超时留下的 SCPI 错误，记录为诊断；恢复动作新增的错误仍判收尾失败。功放 OFF 为发送命令的记录；仪器开关与偏置回读不等价于外部钳位测量。异常、取消或断开会话走同一恢复；收尾未确认须现场核查，不把取消、退出或断开通信当作恢复完成。

## 参数与操作

当前默认值以[独立默认参数文件](../../params/experiments/rf-phase-sensitivity.yaml)为准，运行时以该次的 `experiment_config.yaml` 为准。

参数按采集节点归属并可编辑：A03 本轮共用条件（射频、偏置、锁相、示波器时长与温控等待），A04 局部外档角度，A05 基带频点与音调时长，A06 噪声时长与 Welch 参数；A01 只有运行标签。修改某节点的参数会使该节点及其后继失效，本轮未安全结束前不允许应用参数。

| 参数 | 含义和约束 |
| --- | --- |
| `Y_RF_FREQ_HZ`、`DEMOD0_IDX` | 界面只读：载波固定 5 kHz；为避开 `MOD 1` 占用的 `Demod 1–3`，原子读出固定使用独立 `Demod 4`（索引 `3`；历史旧目录索引 `0` 仍可读图分析）。 |
| `Y_RF_VPP` | Y 方向 5 kHz 正弦峰峰值幅度（Vpp，基本参数可编辑），按峰值 `Vpk = Vpp / 2` 下发到 MFLI `mods/0/carrier/amplitude`，须满足 `mfli_dev30661_signal_output_0` 安全限值及最大 10 V 量程。 |
| `PM_FREQUENCIES_HZ`、`PM_TONE_DURATION_S` | 严格递增的基带频点列表（Hz，常用范围 5–80 Hz）及每档 MFLI 采集时长（s）；记录至少覆盖最低频率 10 个周期，锁相采样率至少为最高频率的 10 倍。 |
| `STATIC_POINT_DURATION_S`、`LOCAL_SPAN_DEG` | 静态点采集时长及局部标定的外档指令角跨度；`K₀` 的横轴使用示波器实测角度。 |
| `NOISE_DURATION_S`、`WELCH_SEGMENT_S` | 连续基线和 Welch 分段长度（s），前者至少为后者的 3 倍。 |
| `DEMOD0_SIGNAL_RANGE`、`DEMOD0_ORDER`、`DEMOD0_TC`、`DEMOD0_RATE` | `Demod 4` 输入与采样滤波；找工作点后不再改变解调相位，扫频和基线不切换滤波、不另作滤波修正。 |
| `SYSTEM_SETTLE_S`、`TEMP_SWITCH_ON_LAG_S` | 采集条件变化后的系统稳定等待（至少 10 × 解调滤波阶数 × 时间常数）与温控恢复后的等待。 |
| `Z_PULSE_*`、`Z_PEAK_CURRENT_MA`、`Z_TAIL_*`、`AMPLIFIER_*` | 现有 Z 双脉冲、拖尾与功放链参数；同时按电压、电流与电阻平均功耗限值检验。 |
| `X_BIAS_V`、`Y_BIAS_V`、`MAIN_FIELD_CURRENT_MA` | 本轮 X 偏置（DG4000 `X_magnetic_field` DC 输出）、Y 偏置（叠加于 MFLI `sigouts/0/offset`）及主磁场设定；在界面按需覆盖并与该轮快照一并保存。 |

在实验中心打开 **射频相位灵敏度**（`/experiments/rf-phase-sensitivity`）。「运行」在节点内编辑参数与基础/高级布局；顶部提供保存参数与布局、派生预览、仅预检和预检并运行。分步时按前置逐段选择 A03/A04/A05/A06，不自动补选；同一轮内可逐段向前执行，但不能只补后段。应用参数草稿后需显式重跑受影响节点；本轮未安全结束前不能改参数，空闲时可结束会话，执行中通过任务取消等待安全恢复。分步等待仍占全局硬件锁，状态以当前会话和回读为准。「历史」可查看同实验快照、下载现有产物，或将参数填回运行草稿；填回不保存默认值或启动实验。历史重分析及 B01 均绑定指定目录，不改采集快照和下一次实验参数。

## 数据及读图

首测的判断标准：电学帧的载波中心距工作点不超过 2°、距对准锚点不超过 1°，帧内分段跨度不超过 0.5°，载波幅度变化不超过 5%；有意施加的 PM 峰值须在 1.9°–2.1°，残余 AM 不超过 0.1%。B01 要求 Y 音调信噪比至少为 5，最低配置频点的 `|H|/|K₀|` 在 0.8–1.2 且首末锚点复测通过（对称相对差门槛为 15% 与两倍拟合标准误中的较大值，上限 30%）。采集完成但不满足这些条件时，不报输入等效灵敏度。

- 数据位于 `data/rf_phase_sensitivity/<运行目录>/`。`experiment_config.yaml` 记参数快照、设备与时钟回读、采集资格和质量记录；`raw/*.npz` 保存静态点、局部标定、PM 各档及基线帧。示波器同帧数组为 `time_s/reference_v/coil_v`，MFLI 为 `time_s/x_v/y_v`；各帧附有实际时间轴与采样率的元数据。历史入口核对实验 ID、数据类型、参数与原始格式版本（`RAW_FORMAT_VERSION = 1`）和完整参数快照，继续兼容早期 `DEMOD0_IDX = 0`（Burst/DG）归档目录，不把旧相位响应目录当成本实验。
- 分析根据有效 PM 档位的实测幅度，计算同轮 `|H(f)| = |Yₚₖ(f)| / φₚₖ(f)`（V/rad）和归一化 `G(f) = |H(f)| / |K₀|`，核查锚点复测及低频一致性。相位频响此处指相位输入的**幅频**响应，不输出复传递函数的相角。
- 对基线 `sample.y` 按实际采样率计算 Welch 单边 ASD（V/√Hz）：Hann 窗、50% 重叠、每段线性去趋势。除以合格的同轮 `|H(f)| = |K₀|·G(f)` 得到输入等效 ASD（rad/√Hz）。只在相邻合格频点间按 `log(f)`–`log(|H|)` 插值，不跨无效频点、不外推，低于 1 Hz 不做折算。各合格频带分别报告中心最近的谱点和中位数，不选取最低值宣传灵敏度。缺合格标定、有效频带或合格基线时保留状态和诊断；基线时基合格仍可报告探测器 `sample.y` ASD，但不补出输入等效数值。处理 `failed` 或 `cancelled` 的目录不意味着采集合格。
- 分析产物位于 `results/`：`phase_transfer.png` 与 `phase_transfer.csv`、`phase_noise.png` 与 `phase_noise.csv`，以及 `phase_sensitivity_summary.json`。无可用数据时图中注明原因，空表保留列名；摘要区分采集终态与分析质量。GUI 只展示该目录实际已有的图片和下载链接。旧历史数据不回写采集参数或原始帧。
