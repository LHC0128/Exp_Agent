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
    role: common_trigger_and_reference_clock
  Temp_Switch:
    role: temperature_gate
  lockin_r:
    role: mf_mod_y_rf_and_demod4_readout
  coil_drive_amplifier:
    role: z_coil_power_amplifier
  main_magnetic_field:
    role: main_field_restored_on_exit
  temperature:
    role: optional_tec_target_only
required_devices:
  - DG4000
  - DG900 或 DG4000（温控开关）
  - MFLI
  - Aigtek 1200B
  - GS200
  - TEC103（可选设温）
---

# 射频相位灵敏度

在固定 **Y 向 5 kHz** 连续射频（幅度 `Y_RF_VPP` 可调，典型 `0.5 Vpp`）和已有 Z 双脉冲链条件下，全程仅通过 **MFLI 锁相放大器**测量原子读出对 Y 线圈驱动相位的静态斜率、基带幅频响应与输入等效相位灵敏度谱。结果描述当前装置、驱动与读出链的等效输入噪声，不声称是器件本征相位噪声。

Y 向载波、相位调节、**2° 峰值**相干边带调相及 `Y_BIAS_V` 偏置统一由 **MFLI Signal Output 1（`MF-MOD` 选件）**产生，不再使用 DG4000 Y 通道，也不再使用未同步外部参考时钟的 DHO924 示波器。本实验的 ID、参数、数据目录和历史与[射频相位响应](rf_phase_response.md)独立。每轮自行找工作点并标定，不从旧实验读入斜率、工作点或幅度扫描线宽。

## 接线与预检

- `Time_sequence_2` 主输出接入 `Z_magnetic_field` 外触发端；Z 链为 `Z_magnetic_field` → 1200B → 阻尼电阻 → Z 线圈 → 采样电阻 → 功放返回端，沿用既有外触发正负 π 双脉冲接法。
- MFLI **Signal Output 1 (`Out 1`)** 经串联大电阻接 Y 方向射频线圈回路；MFLI **Signal Input 1 (`Sig In 1`)** 接探测器，并与 DG 共用 **10 MHz 外部参考时钟**（`extclk = True`）。
- 启用 `MOD 1`（`mods/0`）后 MFLI 硬件固定占用 `Demod 1–3`（分别对应载波与两个边带），本实验使用 **`Osc 2`（`oscs/1`，5 kHz）** 作为载波与解调共用参考，**`Osc 3`（`oscs/2`）** 作为调制基带振荡器，并将原子信号正交读出固定绑定在完全独立的 **`Demod 4`（`DEMOD0_IDX = 3`）** 上同时采 `sample.x` 和 `sample.y`。
- `X_magnetic_field` 提供 X 方向静态偏置，Y 方向直流偏置 `Y_BIAS_V` 叠加在 MFLI `sigouts/0/offset` 上；主磁场由 `main_magnetic_field` 的 GS200 提供，设 0 表示关输出。A01 只解析映射和校验模型、`mfli_dev30661_signal_output_0` 安全限值与总包络（含调相边带与直流偏置）、峰值电流、实际脉冲平均功耗、MFLI `MF-MD` 四通道能力及 DAQ 采样资格，不连接硬件；A02 连接真机后再严格核对实际已安装的 `MF-MD` 与 `MF-MOD` 选件及公共 10 MHz 外部时钟回读。缺少映射、限值、型号或所需能力时拒绝启动，不自动换通道。

## 流程与收尾

| 节点 | 内容 |
| --- | --- |
| A01 参数与映射预检 | 不连接仪器，核对当前参数、映射、`mfli_dev30661_signal_output_0` 限值与 Z 脉冲安全边界。 |
| A02 连接设备并同步公共参考时钟 | 建立设备会话、核对真机 `MF-MD`/`MF-MOD` 选件并写入、严格回读公共 10 MHz 时钟。 |
| A03 整周相位响应扫描与工作点选择 | 开启 `MF-MOD` 连续无调制载波整周调相，先三分量粗定再经多阶谐波细化工作点与切线角，将该切线转到 `Demod 4` 的 `sample.y` 正交分量。 |
| A04 局部 K₀ 标定 | 在工作点两侧以 MFLI 指令角加密细扫，扣除首尾中心线性漂移后按理论式 `(2.2.2)` 拟合 `Demod 4` Y 色散斜率 `K₀` 与正交残留 `slope_x`（V/rad），并将 A05/A06 载波相移更新到局部拐点中心。 |
| A05 基带频率响应测量 | 仅对 A05/A06 放宽 `Demod 4` 低通与传输率（`DEMOD0_DYNAMIC_TC` / `DEMOD0_DYNAMIC_RATE`，保持对齐相移不变并保留 A03/A04 静态滤波不变），使能 `MF-MOD` **2° 峰值**相干边带 PM 并按 `PM_FREQUENCIES_HZ` 单次顺序切换 `Osc 3` 采集各档 XY 帧。 |
| A06 无调制噪声测量 | 保持 A05 的高带宽滤波不变，关闭上下边带、保留工作点 5 kHz 连续载波在 `Demod 4` 采集无调制 `noise_xy` 基线记录并回读载波状态。 |
| A07 安全结束会话 | 关闭会话并检查安全收尾记录；无前置依赖。 |
| B01 相位频响与输入等效噪声分析 | 独立离线处理同一运行目录：绘制 A03 含 `X` 放大插图相图与式 `(2.2.2)` 波形、A04 左相图右理论色散响应、A05 零频 `√Lorentzian` 拟合与 `H₀` 归一化频响及 A06 相位灵敏度谱（`µrad/√Hz`，含全频段 `√Lorentzian` 回补），并标注参考告警。 |

A03–A06 是同一轮的四段原始记录，必须同一次会话、同一运行目录完成，不能只补后段或跨轮拼接；任一段失败、取消或参数变化后，只能整链 A03–A06 重跑并新建运行目录。

1. **整周相位响应扫描（A03）。** 预先一次选定覆盖直流偏置与调相包络的 MFLI 输出量程并固定 `Y_RF_VPP` 对应的峰值 `Vpk = Vpp / 2`（避免中途切换输出挡位产生静态相移）；开启 `MOD 1`（`mode=1`, `freqdevenable=0`, 边带关闭）和 `Out 1`，采用 A03/A04 窄带滤波参数（`DEMOD0_TC`、`DEMOD0_RATE`），在线扫描 `mods/0/carrier/phaseshift`（`0–345°`）并用 `Z(φ) = sample.x + i·sample.y` 先以三分量模型寻找初始切线工作点，高次谐波显著且点数充裕时再在初定位 `±35°` 内以多阶傅里叶（最高 7 次谐波）细化工作点与切线方向，转动 **`Demod 4`** 参考相位使切线沿 `sample.y` 方向并回读确认（偏差不超过 `0.5°`）；离线成图按含微偏置 `v₀` 的理论色散式 `(2.2.2)` 拟合整周波形与相图。
2. **局部 K₀ 标定（A04）。** 保持 `Demod 4` 相位与 A03 静态滤波参数不变，仅在 MFLI `mods/0/carrier/phaseshift` 上以 A03 工作点为中心在 `±LOCAL_SPAN_DEG` 内加密细扫（每侧 16 档、含首尾中心点共 35 点，无示波器采集）。用首尾中心点估计线性时间漂移并扣除，对 Y 分量拟合对称理论色散式 `(2.2.2)` 提取零点中心斜率 `K₀`（`V/rad`）与拐点偏移量 `eq222_phi_offset_deg`（`slope_x` 仍线性回归），随即将载波相位更新至 `A03 工作点 + eq222_phi_offset_deg`（限幅在 `±LOCAL_SPAN_DEG` 内）供后续 A05/A06 使用，不再重转 `Demod 4` 相位。局部斜率信噪比或正交泄漏超参考线时记为告警但不中断采集。
3. **相干边带 PM 与幅频扫描（A05）。** 确认局部标定后，先将 `Demod 4` 切换至 `DEMOD0_DYNAMIC_TC` / `DEMOD0_DYNAMIC_RATE`（仅作用于 A05/A06、不改变对齐的 `phaseshift` 与 A03/A04 参数）并严格回读验证；保持 5 kHz 载波在 A04 更新后的工作点不断输出，使能 `mods/0/sidebands/0,1/enable` 施加固定 **`2°` 峰值**（`0.03491 rad`）相干边带调制，直接按 `PM_FREQUENCIES_HZ` 顺序切换 `Osc 3` 基带频率，每个频点回读核对 MFLI `mods/0/index` 并采集 `Demod 4` XY 帧。离线使用与数字正交解调等价的最小二乘单音投影（基频 sin/cos、常数、线性漂移、允许的二次谐波，以及与目标频点不碰撞的 50–250 Hz 工频基频及谐波）提取 `sample.y` 在调制频率处的峰值与标准误，并对实测 `|H(f)|` 直接拟合以 0 Hz 为中心的 `√Lorentzian` 线型 `H₀ / sqrt(1 + (f/fc)²)`（若非碰撞有效频点不少于 2 个则自动剔除 `50–250 Hz` 工频整倍频碰撞点；高带宽下不做软件滤波器反卷积）。
4. **无调制基线（A06）。** 沿用 A05 高带宽解调设置，关闭 `mods/0/sidebands/0,1`，保留工作点 5 kHz 连续载波，仅用 MFLI `Demod 4` 采集 `noise_xy` 记录并在前后核对锁相与调制器关闭回读。

**温控窗口。** 每条 MFLI 原子读数按「设置实际仪器条件 → 关闭 `Temp_Switch` → 系统稳定等待 → 连续采集到内存 → 打开 `Temp_Switch` → 恢复等待后保存」执行；关窗期间不写盘。故障时先尝试恢复温控；仅当恢复失败时允许抢救已读数据并记录失败，不能继续测量。`SET_TEC_TARGET` 只授权实验写 `TARGET_TEMPERATURE_C`，不启停 TEC、不等待温度判稳，不记录未测的实际温度；不启用时保留现场目标设定。

**安全收尾。** 先向 1200B 发 OFF。Z 和 X（DG4000 通道）各自先确认输出 OFF，再关闭调制和 Burst（Z 还关闭同步）、归零并在 DC 模式下复核（先切正弦写电平、再切回 DC 以规避 DG4162 在 DC 波形下忽略 OFFSET 写入的固件问题）。MFLI Y 通道先确认 `sigouts/0/on = 0` 后再清零 `mods/0/carrier/amplitude`、关闭 `mods/0/sidebands` 与 `mods/0/output/enable` 并将 `sigouts/0/offset` 归零，随后逐项回读全部节点记录进 `shutdown_readbacks`。输出 OFF、偏置/幅值清零与模式状态是独立检查；只要任一回读不符或不可判定，本轮不能标作安全收尾完成。`Time_sequence_2` 保持运行；恢复 `Temp_Switch` 为 5 V DC、输出 ON，恢复 GS200 原状态并停用本轮 `Demod 4` 解调传输。功放 OFF 为发送命令的记录；仪器开关与偏置回读不等价于外部钳位测量。异常、取消或断开会话走同一恢复；收尾未确认须现场核查，不把取消、退出或断开通信当作恢复完成。

## 参数与操作

当前默认值以[独立默认参数文件](../../params/experiments/rf-phase-sensitivity.yaml)为准，运行时以该次的 `experiment_config.yaml` 为准。

参数按采集节点归属并可编辑：A03 本轮共用条件（射频、偏置、A03/A04 锁相、静态点时长与温控等待），A04 局部外档角度，A05 高带宽锁相（`DEMOD0_DYNAMIC_TC`、`DEMOD0_DYNAMIC_RATE`）、基带频点与音调时长，A06 噪声时长与 Welch 参数；A01 只有运行标签。修改某节点的参数会使该节点及其后继失效，本轮未安全结束前不允许应用参数。

| 参数 | 含义和约束 |
| --- | --- |
| `Y_RF_FREQ_HZ`、`DEMOD0_IDX` | 界面只读：载波固定 5 kHz；为避开 `MOD 1` 占用的 `Demod 1–3`，原子读出固定使用独立 `Demod 4`（索引 `3`；历史旧目录索引 `0` 仍可读图分析）。 |
| `Y_RF_VPP` | Y 方向 5 kHz 正弦峰峰值幅度（Vpp，基本参数可编辑），按峰值 `Vpk = Vpp / 2` 下发到 MFLI `mods/0/carrier/amplitude`，须满足 `mfli_dev30661_signal_output_0` 安全限值及最大 10 V 量程。 |
| `DEMOD0_DYNAMIC_TC`、`DEMOD0_DYNAMIC_RATE` | 仅对 A05/A06 切换的高带宽 `Demod 4` 解调时间常数（s）与采样传输率（Sa/s，低于 70 kSa/s 维持 DAQ 采集）；A05/A06 共用且至少覆盖最高基带频点的 10 倍采样率。 |
| `PM_FREQUENCIES_HZ`、`PM_TONE_DURATION_S` | 严格递增的基带频点列表（`1–250 Hz` 内 2–40 个频点）及每档 MFLI 采集时长（s），按列表顺序单次扫描；记录至少覆盖最低频率 10 个周期。 |
| `STATIC_POINT_DURATION_S`、`LOCAL_SPAN_DEG` | 静态点采集时长及局部标定的对称外档指令角跨度（在 MFLI 载波相移上每侧等分 16 档，不含示波器）。 |
| `NOISE_DURATION_S`、`WELCH_SEGMENT_S` | 连续基线和 Welch 分段长度（s），前者至少为后者的 3 倍。 |
| `DEMOD0_SIGNAL_RANGE`、`DEMOD0_ORDER`、`DEMOD0_TC`、`DEMOD0_RATE` | `Demod 4` 输入量程、共用的滤波阶数，以及 A03/A04 静态相位扫描阶段使用的窄带时间常数与采样率（找工作点后不改对齐相移，仅到 A05 才切换带宽）。 |
| `SYSTEM_SETTLE_S`、`TEMP_SWITCH_ON_LAG_S` | 采集条件变化后的系统稳定等待（至少 10 × 解调滤波阶数 × 最大时间常数）与温控恢复后的等待。 |
| `Z_PULSE_*`、`Z_PEAK_CURRENT_MA`、`Z_TAIL_*`、`AMPLIFIER_*` | 现有 Z 双脉冲、拖尾与功放链参数；同时按电压、电流与电阻平均功耗限值检验。 |
| `X_BIAS_V`、`Y_BIAS_V`、`MAIN_FIELD_CURRENT_MA` | 本轮 X 偏置（DG4000 `X_magnetic_field` DC 输出）、Y 偏置（叠加于 MFLI `sigouts/0/offset`）及主磁场设定；在界面按需覆盖并与该轮快照一并保存。 |

在实验中心打开 **射频相位灵敏度**（`/experiments/rf-phase-sensitivity`）。「运行」在节点内编辑参数与基础/高级布局；顶部提供保存参数与布局、派生预览、仅预检和预检并运行。分步时按前置逐段选择 A03/A04/A05/A06，不自动补选；同一轮内可逐段向前执行，但不能只补后段。应用参数草稿后需显式重跑受影响节点；本轮未安全结束前不能改参数，空闲时可结束会话，执行中通过任务取消等待安全恢复。分步等待仍占全局硬件锁，状态以当前会话和回读为准。「历史」可查看同实验快照、下载现有产物，或将参数填回运行草稿；填回不保存默认值或启动实验。历史重分析及 B01 均绑定指定目录，不改采集快照和下一次实验参数。

## 数据及读图

参考质检口径（仅作为告警与图例标记提示，不再阻止采数或扣留出图）：B01 计算 `Y` 音调信噪比（参考门槛 5）与最低配置频点 `|H|/|K₀|`（参考范围 0.8–1.2），并在 `phase_transfer.csv`（`quality_passed` / `warnings`）、`phase_sensitivity_summary.json`（`quality_passed` / `advisory_warnings`）与图例中如实区分，由实验者自行判读；旧版含示波器监视帧与首末锚点复测的历史归档目录继续兼容读图。真正阻止计算与插值的仅限于缺失 `XY` 帧、时间轴不均匀、锁相配置前后不匹配或换算增益非正/非有限等硬条件。

- 数据位于 `data/rf_phase_sensitivity/<运行目录>/`。`experiment_config.yaml` 记参数快照、设备与时钟回读、采集与告警记录；`raw/*.npz` 保存 `coarse_*.npz`、`local_*_xy.npz`、`pm_*_xy.npz` 与 `noise_xy.npz` 锁相帧（`time_s/x_v/y_v`）。历史入口核对实验 ID、数据类型、参数与原始格式版本（`RAW_FORMAT_VERSION = 1`），兼容 `schema_version = 1` 与 `2` 的参数快照，并继续兼容早期含 `scope` 帧或 `DEMOD0_IDX = 0` 的归档目录。
- 分析产物位于 `results/`，包含四幅结果图与三份数据表：
  - `phase_response.png`：**A03 整周相图**，按 `Demod 4` 对齐坐标系画出 `1:1` `(X, Y)` 相图（含 `X` 放大插图以展开平台堆叠点）、工作点与切线矢量，以及整周 `X(φ)` / 理论式 `(2.2.2) + v₀` 拟合 `Y(φ)` 波形；
  - `local_calibration.png`：**A04 局部相位响应图**，采用与 A03 一致的左相图右响应布局：左侧画 `1:1` 局部 `(X, Y)` 轨迹（及 `X` 放大插图），右侧同比例画工作点附近 `X`/`Y` 随 `Δφ` 的变化与理论式 `(2.2.2)` 色散拟合 `K₀`；
  - `phase_transfer.png` 与 `phase_transfer.csv`：**A05 相位调制频率响应图与表**，给出幅频 `|H(f)|`（`V/rad`）、零频 `√Lorentzian` 拟合 `H₀ / sqrt(1 + (f/fc)²)` 及按零频截距 `H₀` 归一化的频响曲线 `|H(f)| / H₀`（表格同步保留 `normalized_gain = |H|/|K₀|` 与 `normalized_h0_gain = |H|/H₀`）；
  - `phase_noise.png`、`phase_noise.csv` 与 `phase_sensitivity_summary.json`：**A06 灵敏度谱、表格与摘要**，对 `sample.y` 计算 Welch 探测器噪声 ASD（`V/√Hz`），**`phase_noise.png` 纵轴统一使用 `µrad/√Hz`**，同时给出实测相邻频点插值带与按拟合 `√Lorentzian` 传递函数回补的全频段灵敏度谱；`phase_noise.csv` 保留 `rad/√Hz`、`°/√Hz` 与 `µrad/√Hz` 列以及全频段回补列。
