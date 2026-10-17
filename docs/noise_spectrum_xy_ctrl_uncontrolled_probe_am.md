---
title: XY 控制测量已知不可控 Probe AM 噪声谱
type: experiment_type
scan_mode: point_by_point
experiment_id: noise-spectrum-xy-uncontrolled-probe-am
defaults_file: params/experiments/noise-spectrum-xy-uncontrolled-probe-am.yaml
mapping_keys:
  main_magnetic_field:
    role: fixed
  Time_sequence_2:
    role: fixed
  Z_magnetic_field:
    role: fixed
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Probe_AOM_Carrier:
    role: fixed
  Probe_AOM_AM:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  temperature:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - DG4000 背板外部 AM 输入范围为 ±2.5 V；运行前核对接线与 AOM 输入规格。
  - 输出是锁相测量端噪声谱，不是经光学标定的 Probe 功率噪声谱。
---

# XY 控制测量已知不可控 Probe AM 噪声谱

## 采集时序与系统稳定

关闭温控使用 `0 V DC + Output ON`，恢复时写回开启电平并保持 `Output ON`；校相与配对采集都采用这一电压门控方式。

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

XY 噪声使用 `HF2_DAQ_RATE` 请求率、`HF2_DAQ_DURATION` 时长和 `HF2_DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 实验目的

在 Probe 光路 AOM 的射频载波上施加外部 AM 周期噪声，测量它在 XY 控制噪声谱中的贡献。可选择在主扫描期间向 Z 线圈持续注入独立配置的已知可控噪声；无论该选项如何，每个 XY 控制点仍交替采集 Probe AM 噪声关闭和开启两态，Z 输出在同一对采集中保持不变。离线沿用现有全控制轴分析，独立估计两态背景谱 `N_S1`，输出：

Z 注入用于检验较强可控噪声背景下，现有流程能否取得足够有效拟合点并提取 Probe AM 差谱；先采用参考实验已保存的注入幅度作为本实验独立默认值，实际结论以拟合诊断和原始谱为准。

`ΔN_S1 = N_S1,on − N_S1,off`

差值保留正负号。该量是 锁相放大器解调测量端的 PSD，不解释或换算为绝对 Probe 光功率谱。
Probe AM 注入频带相对 锁相放大器 解调频率的差频落入 Y 信号分析频带，是本实验观察差谱的频率映射假设；实际可见频带和幅度以原始波形、解调设置及分析结果为准。本次只报告现有模型提取的谱，不按噪声来源对各分量作额外归类。

参数 schema 与 GUI 回退值定义实验表单；GUI「保存为默认」写入的参数来源为[默认参数文件](../params/experiments/noise-spectrum-xy-uncontrolled-probe-am.yaml)，单次运行参数以运行目录的 `experiment_config.yaml` 为准。

## 设备与接线

- Probe 光路透射光仍作为测量 Probe 光。专用 DG4000 CH1 输出 AOM 射频载波，50 Ω 负载、外部 AM 源为 EXT；频率、幅度与 AM 深度读取[默认参数文件](../params/experiments/noise-spectrum-xy-uncontrolled-probe-am.yaml)及单次运行快照。
- 同一 DG4000 的 CH2 输出零偏置任意波，连接到该机背板 `Mod/FSK/Trig` 外部 AM 输入。设备手册规定该输入范围为 ±2.5 V，预检和上传前均按映射安全限值校验。[RIGOL DG4000 用户手册](https://www.rigol.com/dam/global/downloads/brochures/en/user-manual/waveform-generators/DG4000_UserGuide_EN.pdf)
- Probe AM 与 Z 可控噪声各有独立的 16384 点周期伪随机波形、谱段、重复频率、输出幅度和种子。设计 PSD 是参考值；实际电压谱按归一化波形及输出幅度计算，运行快照记录完整参数。当前 Probe AM 默认注入谱经锁相解调后落在分析频带内；具体数值以保存的参数文件为准。
- `Z_magnetic_field` 使用参考实验的 Z 小线圈注入链路，与 `Time_sequence_2` 分别占用同一 DG4000 的 CH1/CH2。后者经三通触发 X/Y 控制信号源。即使本次不注入 Z 噪声，CH1 也明确保持关闭并纳入安全收尾。
- 本实验同时需要 Probe AOM 与 Z/时序两组独立通道，不能把它们绑定到同一设备的同一 CH1/CH2。设备库允许跨实验复用；运行本实验前需在 Resource 下拉恢复独立绑定并点击“保存设备库”，冲突会在连接前被预检拒绝。
- 其余 GS200、Pump、Probe 光功率、Pump 门控、温控和 锁相放大器 映射沿用现有 XY 测量配置。

## 采集流程

采集按模块清单（`stages.py`，A01–A16）分步执行，完整运行与分步会话调用同一套业务函数：

1. A01 预检解析设备库和物理映射，确认 Probe AOM 载波与 AM 同机、Z 线圈与共用触发同机，并校验 AM 与所选 Z 波形的输出幅度。预览只计算波形与 PSD，不连接仪器。
2. A02 连接实验设备（含 Probe AM 信号源）、A03 同步参考时钟并保存设备库/映射快照；A04–A07 设置初始输出、目标温度设置、Pump 配置和共用触发。
3. A08 配置 Probe AOM 载波与外部 AM 并开启载波（校相与采集的信号来源），噪声输出保持关闭。
4. A09–A11 完成 锁相放大器 与 XY 相位校准；校相未收敛时标记质量未通过并禁止后续采集。
5. A12 生成并上传 Probe AM 任意波；启用 Z 注入时另生成并上传 Z 任意波，噪声输出均保持关闭。
6. A13 配置 DAQ；A14 在温控开启的采集初始化阶段保存 Probe AM 归一化波形、可选 Z 归一化波形、配置与扫描轴，然后按本次选项开启 Z 噪声并回读确认（主扫描全程不切换 Z 状态）。每个 XY 点的 Probe AM 两态仍按偶数点 OFF→ON、奇数点 ON→OFF 交替，逐态等待和内存采样，当前配对恢复温控后保存数据与时序；每轮扫描保存到独立运行目录。
7. A15 恢复解调器后，再次扫描需重新执行 A13；A16 无前置，正常结束、异常、取消或通信结束均执行共享安全收尾：关闭 Probe AM、AOM 载波和 Z 通道，恢复温控并断开 TEC；Pump 载波、Pump 门控与 锁相放大器 按共享保留策略处理。正常结束补做解调恢复时使用最近成功 A09 校相的参数快照；仅应用但未重跑的新采样率或时间常数不写入仪器，重新校相成功后更新基线。

## 离线分析

分析器只读取指定运行目录，不连接仪器，按阶段清单（B01–B04）持久化状态与产物指纹：B01 校验配置与成对波形；B02 计算 Probe AM ON/OFF Welch PSD 矩阵并保存实际任意波 PSD 预览；B03 用 OFF PSD 定位移动脊线；B04 完成两态拟合、谱分离与有符号差谱。`controlled_z_noise.enabled` 与参数快照记录 Z 注入状态和波形设置；现阶段不做 Z 开/关跨运行比较。运行快照须保留有效的实际 DAQ 采样率作为历史回退；存在逐波形采集旁文件时，优先用旁文件中的实际率构造 Welch 频率轴，并核对各波形及 ON/OFF 的频率轴一致，不从请求率猜测。两态只取成对有效控制点交集。若移动峰不足以自动生成控制轴，则按运行参数中的正弦控制标定 `K`、`B` 推算峰位，继续生成二维 PSD 图和拟合结果；该结果标记为质量未通过，仅供暂时分析，不能视为本次移动峰标定通过。两态使用同一控制轴调用共享 `noise_spectrum_xy.processing.fit_local_spectra(..., full_range=True)`；峰参数与背景独立验收，最终差分背景掩码是两态背景掩码的交集。

模型底座未通过时，允许从峰窗口之外的稳定远端平台提取背景：两侧各至少 8 点、四个控制轴分块的水平极差/中位数不超过 20%、逐块留出的中位相对误差不超过 40%。具体规则和假设见[共享噪声参数拟合](noise_spectrum_xy_ctrl.md#噪声参数拟合)。即使注入抬高背景、可控峰无法稳定拟合，仍能报告通过独立验收的 `N_S1`。可控响应只展示各态通过验收的部分，不用背景有效掩码放行峰参数。

重分析先复制 `results/` 顶层旧产物到 `before_<timestamp>/`。`popt_fit.npz` 保存完整峰/背景诊断；`popt/perr` 只描述洛伦兹模型，平台背景读取 `N_S1`，来源为 `off_resonance_plateau`。`noise_spectra.npz` 的 `valid_mask` 用于背景差谱，`controlled_valid_mask` 用于可控响应差谱，另保存各态掩码、背景来源 `background_method_on/off` 及详细拒绝原因。自动标定有效且有背景通过时即可完成背景提取，不要求可控响应同时通过。

结果包括两态 PSD、脊线、受控响应 `S_beta` 与背景 `N_S1` 拟合参数和拒绝原因、两套有效掩码、两态有符号差谱、任意波实际 PSD、CSV 和图。图表复用已知可控噪声实验的平均 PSD 二维图、ON/OFF 受控响应与不可控背景对比、若干频率列的洛伦兹线形/拟合和任意波 PSD 预览；另绘制受控响应及不可控背景的 OFF 基线与 ON−OFF 差谱双 Y 轴图。移动峰自动标定不足时，平均 PSD 二维图按运行参数 K/B 标注暂定峰位，并在 GUI 历史中显示质量未通过状态与提示。由于 Probe AM 没有独立的幅度到磁场/光功率标定，不生成真值链比较。拟合拒绝点保留 NaN 并记录原因，不做插值或外推；有效负差不裁零，也不按符号筛选。结果单位为锁相测量端 PSD 或拟合响应系数，不代表经标定的 Probe 光功率谱。

## GUI 使用与交互流程

实验页面提供「运行 / 历史」两个主 Tab；运行页是可勾选、带依赖的交互流程图，保存、预览、预检、完整运行和任务状态融入流程容器顶部。

### 采集节点

- 左列按分组展示 A01–A16：启动准备（A01–A03）、初始条件（A04–A05）、激励与触发（A06–A08）、相位校准（A09–A11）、噪声激励（A12）、扫描与结束（A13–A16）。勾选后可单独执行，缺少前置或重跑前置时按依赖拒绝并提示。
- 参数在节点内按基础／高级分类编辑：A08 编辑载波频率/幅度与 AM 深度，A12 编辑两组噪声谱段、幅度、种子与 `INJECT_CONTROLLED_NOISE`（是否注入 Z 可控噪声），A14 编辑扫描范围、K/B 与两态等待。共享参数的值与分类在所有入口同步。
- 分步会话等待期间保持连接、输出状态并占用硬件锁，界面明确提示；会话快照显示各通道输出（含 `Probe AM` 载波/噪声状态）、校相诊断与安全收尾结果。校相未收敛（A11 质量未通过）时禁止 A13/A14；A15 恢复解调器后再次扫描需重新 A13。
- 修改参数形成草稿，应用后按影响范围使后续模块失效，可预览预计需重跑的已完成步骤；每轮扫描保存到独立目录，旧数据不覆盖。
- 波形预览可分别对齐 Probe AM 与 Z 噪声的输出幅度，对齐值超出对应通道安全限值时按钮不可用。

### 分析节点

右列展示 B01–B04，默认操作“下一次实验”，也可切换到已结束扫描或历史运行目录；具体目录仅编辑分析覆盖值（频率格间距、频带范围、拟合半宽），与下一次实验参数互不影响。前置失败不执行依赖阶段；重算先归档受影响产物。移动峰标定不足但已生成暂定 PSD 二维图时，分析状态保持质量未通过并显示 K/B 暂定提示。

历史页读取该实验自己的运行列表与参数快照，分别展示背景与可控响应的有效点数、远端平台补充点数，可查看图表、填回兼容参数或重新分析；浏览历史不会启动采集。静态架构总览与子图已从 GUI 撤下并保留在[历史归档](old/architecture/noise-spectrum-xy-uncontrolled-probe-am.html)，运行页流程图顶部继续提供可编辑原图下载。

## 参数范围

| 参数 | 含义 | 单位与约束 |
|---|---|---|
| `NOISE_BAND_STARTS_HZ` / `NOISE_BAND_STOPS_HZ` | 设计噪声谱段边界 | Hz；与 PSD 数组等长、不重叠且位于 Nyquist 内 |
| `NOISE_BAND_PSDS_V2_PER_HZ` | 设计参考 PSD | V²/Hz；正值；实际谱以缩放波形的计算结果为准 |
| `NOISE_REPEAT_FREQ_HZ` | 任意波重复频率 | Hz；正值，决定采样率与谱线间距 |
| `NOISE_AMPLITUDE_VPP` | CH2 输出幅度 | Vpp；正值，峰值须位于外部 AM 安全范围内 |
| `PROBE_AOM_CARRIER_FREQ_HZ` / `PROBE_AOM_CARRIER_VPP` | CH1 载波频率与幅度 | Hz、Vpp；幅度为 0–0.05 Vpp，受安全限值约束 |
| `PROBE_AOM_AM_DEPTH_PERCENT` | 外部 AM 深度 | %；0–120 |
| `SYSTEM_SETTLE_S` | 每态切换后的稳定等待 | s；非负 |
| `NOISE_SEED` | 固定随机种子 | 非负整数 |
| `INJECT_CONTROLLED_NOISE` | 是否在整段主扫描持续注入 Z 可控噪声 | 布尔值；同一 Probe AM ON/OFF 对内不切换 |
| `Z_NOISE_BAND_STARTS_HZ` / `Z_NOISE_BAND_STOPS_HZ` | Z 已知噪声设计谱段边界 | Hz；启用时非空、数组等长且位于 Nyquist 内 |
| `Z_NOISE_BAND_PSDS_V2_PER_HZ` | Z 已知噪声设计 PSD | V²/Hz；启用时与边界数组等长且为正 |
| `Z_NOISE_REPEAT_FREQ_HZ` / `Z_NOISE_AMPLITUDE_VPP` | Z 任意波重复频率和输出幅度 | Hz / Vpp；启用时为正，幅度受 Z 安全限值约束 |
| `Z_NOISE_SEED` / `SYSTEM_SETTLE_S` | Z 波形种子和开启后等待 | 非负整数 / 非负秒数 |
| `HF2_DAQ_DURATION` / `ANALYSIS_BIN_WIDTH_HZ` | 每态采集时长与 Welch 目标格间距 | s、Hz；正值 |

## 伪代码

```text
预检：读取参数与映射 → 生成 Probe AM 与 Z 已知噪声的独立预览
     → 校验载波幅度、所选输出峰值和两组同机映射 → 返回预览，不连接仪器

采集：连接并同步设备 → 配置 Probe AOM 载波与 EXT AM，上传 AM 任意波并保持输出关闭
     → 选择注入时上传 Z 任意波并保持输出关闭 → 保存运行配置、映射快照和原始波形
     → 校相并配置 XY 与 锁相放大器 DAQ → 按开关开启 Z 噪声或保持关闭，扫描中不切换
     → 每个 XY 点设置控制幅度，温控窗口内按 OFF→ON / ON→OFF 交替采集
     → 每态只切换 Probe AM CH2，等待、采样、保存波形与时序并报告 ETA
     → finally：关闭 Probe AM、载波和 Z 通道，执行共享安全收尾

分析：使用记录的实际采样率计算两态 PSD → 对齐成对有效控制点
     → OFF 谱定位脊线 → 调用三实验共享全轴拟合 → 独立验收 N_S1，必要时检验两侧远端平台
     → 保存平均 PSD 二维谱、ON/OFF 两类噪声对比、若干列洛伦兹拟合
     → 分别计算 ΔS_beta 与 ΔN_S1 并保留正负 → 双 Y 轴绘制 OFF 基线与差谱
     → 保存实际任意波 PSD、拟合掩码、拒绝原因和分析摘要；不接真值标定链
```
