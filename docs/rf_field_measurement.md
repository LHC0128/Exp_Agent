---
title: RF 灵敏度（DirectAW）
type: experiment_type
description: XY 直接任意波控制下测量 RF 灵敏度，采用可勾选、检查依赖的流程图，保持设备会话并按节点编辑参数。最优控制波形从 oc_sens 版本目录选择；四级校相后按温控分组执行定长 DAQ，以 Demod3 X 响应斜率和 X 噪声 PSD 计算灵敏度，并用等效 HWHM 补偿洛伦兹频响。仪器设置只回读记录，DAQ 使用实际采样率。XYZ 共用时序信号2主输出触发，HF2 经物理回环两级解调；独立分析支持四阶段重算，历史支持多次对比和 PNG/PDF 导出。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
experiment_id: rf-sensitivity-aw
defaults_file: params/experiments/rf-sensitivity-aw.yaml
version: 8
schema_version: 2
scan_mode: point_by_point
mapping_keys:
  X_magnetic_field: {role: fixed}
  Y_magnetic_field: {role: fixed}
  Z_magnetic_field: {role: scan}
  Time_sequence_2: {role: fixed}
  main_magnetic_field: {role: fixed}
  Pump_laser_power: {role: fixed}
  Probe_laser_power: {role: fixed}
  Pump_modulation: {role: fixed}
  Time_sequence: {role: fixed}
  Temp_Switch: {role: fixed}
  temperature: {role: fixed}
  lockin_r: {role: detection}
required_devices: [GS200, signal_generator, HF2, TEC103]
learned_notes:
  - XYZ 共用时序信号2的主输出外触发，校相前启动，后续保持连续输出；通道 SYNC 同步开启并持续运行。
  - 仪器在 DC 波形下会自动关闭通道 SYNC，切回方波/脉冲后需显式重新打开（setup_* 不会自动恢复）。
  - setup_dc 会顺带打开输出；初始化先归 DC 再统一关闭，Demod0 校相时 XY/Z 控制场真正关闭。
  - HF2 的内部输入枚举没有 Demod Y，必须连接 AuxOut2 到 Signal Input2。
  - XY 校相未收敛或 R 低于门槛时停止后继并保存诊断；分步会话允许原连接重试，完整运行自动收尾。
  - RF 触发相位扫描按 φ、φ+180° 成对顺序采集，正交拟合不设 R²/显著性门槛，拟合异常即停止。
  - 结束时保留时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲继续运行，只关闭 XY 与 Z RF。
  - 温控开关关闭为 0 V DC + ON，相位/幅度扫描按温控分组点数分组关温控（末组不足分组点数也适用），组间恢复为 5 V DC 并按温控恢复后等待，结束保持 5 V DC + ON。
---

# RF 灵敏度（DirectAW）

本实验在 Bell-Bloom 磁力仪的 XY 直接任意波控制条件下测量 Z 方向 RF 灵敏度。
运行页采用与 XY 正弦实验一致的可执行流程图：显式勾选步骤、校验依赖、按节点编辑参数，
在同一设备会话中分步执行或重试；完整运行与分步模式调用同一套业务步骤。
仪器设置后回读并记录实际值，DAQ 使用实际采样率；参考时钟严格回读，输出安全范围始终强制校验。
独立的 [DirectAW R 实验](rf_field_measurement_r.md) 共用本实验的准备、回环、采集及安全收尾，
采用 R 标定后改频采集 X 噪声的顺序，分别保存默认参数和历史。
最优控制包络读取 `D:\Code\theory_agent\simulate\results\oc_sens` 的版本化理论输出，
运行前在 GUI 中明确选择版本。
初始化依次执行四级相位校准：Bell-Bloom（Demod0）、控制载波与 Pump 调制（XY DirectAW）、
控制 AM 调制与 RF 场（扫描 Z RF Burst 相位）、Demod3。随后按与相位扫描相同的节奏扫描有符号 Z RF 幅度：
每点重臂后以 DAQ 采集定长时序，取 **X 均值**（同时保存 Y/R 均值、标准差与时序）标定
电压到磁场响应，再在零 RF 输出下采集 **Demod3 X 噪声**，用 Welch PSD 计算
灵敏度谱。R 用于校相信号门槛与诊断，不作为本实验的灵敏度响应分量。

历史页面可勾选多次完整分析的运行，将保存的 X 响应和灵敏度主谱叠加到双面板图中，
自定义英文图例并导出 PNG/PDF。对比过程只读历史结果，拟合回退会明确提示。

当前正式入口为 `rf-sensitivity-aw`（`typed_workflow`、schema version 2），
数据类型和原 Python 文件名保留。Notebook 是历史实验入口，本次没有迁移。
可编辑流程图源文件见 [RF 灵敏度（DirectAW）.excalidraw](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw/RF%20灵敏度（DirectAW）.excalidraw)，运行页提供下载。
当前页面没有架构 Tab。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

锁相设备在「仪器控制 → 设备库 → 锁相放大器」中全系统统一选择。**本实验当前接线需要两路独立主电压输入，暂时只支持 HF2；选用 MFLI 时预检禁止启动。**MFLI 的 Aux Input 回采尚未验证，程序不会自动改接线、降频或回退 HF2。该限制不影响已有 HF2 数据的离线分析。接口差异见 [锁相接口说明](lockin_amplifier.md)。

## 入口与参数来源

- 采集：[RF_Field_Sensitivity_AW.py](../experiments/RF_Field_Sensitivity_AW.py)。
- 离线分析：[RF_Field_Sensitivity_AW_plot.py](../experiments/RF_Field_Sensitivity_AW_plot.py)。
- 实现：[实验模块](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw/)。
- 当前默认值：[params/experiments/rf-sensitivity-aw.yaml](../params/experiments/rf-sensitivity-aw.yaml)。

GUI「保存参数与布局」实际写入上述 YAML 和实验目录配置中的字段布局。模型中的值仅作
缺少参数文件或字段时的回退；运行覆盖优先于默认 YAML。单次运行的真实参数以其
`experiment_config.yaml` 的 `parameters` 为准，离线分析读取该快照与原始数据。

`ARB_WAVEFORM_FILE` 保留原外部键，值为相对于上述 `oc_sens` 目录的
`vN/waveforms/optimal_control_waveform.csv`。GUI 仅列出存在波形文件且解析后仍位于该目录
内的 `vN` 版本，不列出 `spectra/` 或 `parameters/` 中的 CSV，也不自动选择最新版本。
目录不可用或版本尚未选择时预检会阻止采集。
直接运行脚本也读取参数 YAML，该键必须指向有效版本。公共周期、标定和输出幅度仍按用户参数
校验，不因选版本而自动调整；采集保存的波形 NPZ 包含实际源文件路径和原始包络。

直接启动采集：

```powershell
.\agent_exp_env\Scripts\python.exe experiments/RF_Field_Sensitivity_AW.py
```

离线分析必须明确给出运行目录，例如下列占位路径需替换为真实的新格式记录：

```powershell
.\agent_exp_env\Scripts\python.exe experiments/RF_Field_Sensitivity_AW_plot.py data/RF_Field_Sensitivity_AW/<run_id>
```

## 接线与 HF2 信号链

设备和通道来自 [设备库](../params/devices.yaml) 与 [物理映射](../params/mapping.yaml)，
同一物理 resource 复用会话，工作流不固定信号源型号或实际 CH 编号。

| 映射 / 接口 | 作用与接线 |
|---|---|
| `X_magnetic_field`、`Y_magnetic_field` | 两路 DirectAW 输出到 XY 线圈，外触发 Burst；不依赖 XY AM 映射或 dg_am |
| `Z_magnetic_field` | Z RF 正弦输出到 Z 小磁场线圈，外触发 Burst |
| `Time_sequence_2` | **主输出**连续方波分路连接 X、Y、Z 的 Ext Trig，频率手动设置；通道 SYNC 同步开启并持续运行 |
| `Pump_modulation`、`Time_sequence` | Pump 射频载波与脉冲门控 |
| `Pump_laser_power`、`Probe_laser_power` | 光功率 DC 电平 |
| `main_magnetic_field` | GS200 主磁场恒流，参数单位 mA |
| `temperature`、`Temp_Switch` | TEC 目标温度与 DC 温控开关；TEC 不可用时记录外部温控状态 |
| HF2 Signal Input1 | 主探测信号 → Demod0（振荡器0参考 Pump/Larmor 频率） |
| HF2 AuxOut2 → Signal Input2 | **Demod0 Y → AuxOut2 → BNC → Signal Input2 → Demod3**，振荡器1参考 Z RF 频率 |

HF2 使用从零开始的节点索引：AuxOut2 对应 `auxouts/1`，Signal Input2 对应 `sigins/1`。
配置 `auxouts/1/outputselect=1`（Y）、`auxouts/1/demodselect=0`，
`DEMOD3_ADC_SELECT` 保留为稳定参数键并固定为 1。`adcselect=2` 指向 Aux Input1，
不能表示内部 Demod0 Y。[HF2 官方节点说明](https://docs.zhinst.com/hf2_user_manual/nodedoc.html#demods)
列出了实际枚举。

回环增益、偏置、Input2 量程、耦合、阻抗和差分通过高级参数配置。初始来源为本次仪器
只读回读值，具体数值见默认 YAML；每次运行另存实际节点回读与实际采样率。现场应确认
时序信号2主输出的分路和 AuxOut2 → Signal Input2 的 BNC 接线。
HF2 滤波时间常数、采样率等设置受仪器支持的离散值限制，请求值与实际回读值可以不同；
请求值保存在 `parameters`，实际值保存在节点快照中。

共用 A10 使用锁相输出安全检查：设备库 `hf2_dev18246` 的 AuxOut2（索引 1）对应
`params/safety_limits.yaml` 中的 `hf2_dev18246_aux_output_1`，已确认允许 ±10 V。
预检按参数检查预计输出，写入前按实际 Demod0 主输入量程计算
`offset ± abs(scale) × sqrt(2) × input_range`，缺少上下限或预计越界时拒绝启动/写入。
只读 `lockin_r` 规则不能用于回环输出校验；不会自动改接线、降增益或换仪器。

## 参数含义与预检

已有 GUI/YAML 外部键保持不变，模型内部使用 `snake_case`。

| 参数键 | 单位与约束 |
|---|---|
| `RF_AMP_START` / `RF_AMP_STOP` / `RF_AMP_POINTS` | 有符号 Vpp 扫描，起点小于终点，至少两个点；负号用 Burst 相差 180° 表示 |
| `Z_RF_FREQ` / `Z_BURST_PHASE_INIT` | Hz / °；`Z_BURST_PHASE_INIT` 是 RF 相位扫描前的初始正 RF 相位基准，扫描成功后由选定相位取代 |
| `Z_V_TO_NT` | Z 通道电压到 RF 磁场大小的转换系数，nT/V；响应磁场轴、配置快照和灵敏度换算使用该值，离线分析读取运行时保存的快照 |
| `Z_PHASE_SCAN_START_DEG` / `Z_PHASE_SCAN_STOP_DEG` / `Z_PHASE_SCAN_STEP_DEG` | RF 触发相位扫描范围与步进，°；范围被步进整除，至少 6 个偶数点恰好覆盖一个 360° 周期 |
| `SCAN_POINT_DURATION_S` | 相位扫描与幅度扫描每点 DAQ 采样时长，s；两段扫描共用同一时长与 Demod3 响应滤波（原键 `Z_PHASE_SCAN_DURATION_S`，加载时自动迁移） |
| `TEMP_GROUP_SIZE` | 温控分组点数：相位/幅度扫描中每关闭一次温控连续采集的扫描点数，组间恢复温控（原工作流常量 5） |
| `RF_STEP_SETTLE_S` | RF 换点稳定等待，s：每次重新设置 Z RF 相位或幅度（重臂）后等待（原键 `RF_SETTLE_TIME`，加载时自动迁移） |
| `TEMP_OFF_SETTLE_S` | 温控关闭后稳定等待，s：每次关闭温控后等待温控影响减小，XY 校相每轮重载波形后同样等待（原键 `XY_PHASE_SETTLE_TIME`，加载时自动迁移；旧键 `RESPONSE_N_AVG` / `RESPONSE_AVG_INTERVAL` 已删除并自动剥离） |
| `TEMP_SWITCH_ON_LAG_S` | 每个温控分组（末组不足分组点数也适用）或其他阶段恢复温控（置 5 V）后的等待，s |
| `NOISE_N_AVG` / `NOISE_DURATION` / `NOISE_RATE` / `NOISE_NPERSEG` | 噪声记录数、s、请求 Sa/s 和 Welch 段长；计算 PSD 使用实际采样率 |
| `ARB_WAVEFORM_FILE` / `AW_REPEAT_FREQ` | oc_sens 下的 `vN/waveforms/optimal_control_waveform.csv`；时间列为秒，AW 重复频率为 Hz；运行前必须选择版本 |
| `XY_CTRL_K_HZ_PER_V` / `XY_CTRL_B_HZ` | 控制包络频率到电压的 K/B 标定，K 不为零 |
| `XY_AW_OUTPUT_VPP`（已废弃）/ 满量程选择 | AW 满量程自动跟随包络峰值：`vpp = 2 × max|V_env|`，offset 恒为 0；旧键 `XY_AW_OUTPUT_VPP` / `XY_AW_OUTPUT_OFFSET` 已删除，加载时自动剥离；物理波形与满量程边界仍须通过 X/Y 安全限值，自动幅度低于仪器最小 0.001 Vpp 时预检拒绝 |
| `XY_CTRL_PHASE` / `XY_CTRL_QUAD` | 初始控制相位与 XY 正交相差，单位 ° |
| `XY_CTRL_PHASE_TOL_DEG` / `XY_CTRL_PHASE_MAX_ITER` | XY 校相容差和最大更新次数，初始测量另计一次 |
| `XY_CTRL_PHASE_MIN_R_V` / `XY_CTRL_PHASE_MIN_R_RATIO` | 绝对最低 R 及相对历史最佳 R 的门槛，低于门槛停止 |
| `XY_TRIGGER_*` / `Z_TRIGGER_*` | 同一共同触发的频率、Vpp、offset、占空比、相位；GUI 只呈现一组并同步提交两套旧键，预检拒绝不一致 |
| `DEMOD0_*` / `DEMOD3_*` | HF2 的响应、校相与噪声滤波参数；解调器和振荡器索引按本实验信号链固定 |
| `LOOPBACK_AUX_SCALE` / `LOOPBACK_AUX_OFFSET_V` | AuxOut2 增益与偏置 V |
| `LOOPBACK_INPUT_RANGE_V` / `LOOPBACK_INPUT_AC` | Input2 量程 V；AC 固定关闭，旧键保留用于历史配置兼容，GUI 隐藏 |
| `LOOPBACK_INPUT_IMPEDANCE_OHM` / `LOOPBACK_INPUT_DIFF` | Input2 固定高阻（50 Ω 关闭，旧阻抗键保留且隐藏）与单端/差分设置 |
| `FIXED_PARAMS.*` | 光功率、主磁场电流、温度和温控开启电平 |

预检只读取配置和 oc_sens 中选定的 CSV，不连接仪器；检查实际映射类型、端点、任意波与无限 Burst 能力，
以及共同触发两套键是否一致。AW 重复频率和 Z RF 频率均须为共同触发频率的正整数倍。

X/Y 映射需要 `supports_arbitrary: true`，X/Y/Z 映射需要 `supports_infinite_burst: true`。
设备库读取和保存时按已支持的 DG4000/DG900 型号补全缺失的这两个能力字段，避免新入库设备
只记录通道而被误判为不支持；显式设置为 `false` 时仍拒绝启动。详见[设备能力规则](instrument_mapping.md#设备库)。

CSV 沿用旧脚本的公共周期合成：以等间隔 `time_s` 计算源周期，将控制包络周期性插值到
AW 公共周期；源包络频率和 Pump/Larmor 频率均须为 AW 频率的正整数倍，目标周期须
对应整数个 CSV 采样间隔，点数不得超过实际信号源能力。毫秒单位文件不得当作秒加载。

电压包络 `A(t) = (Omega_ctrl(t) - B) / K`。X 为 `A(t) cos(2π f_Pump t + phase)`，
Y 为 `A(t) cos(2π f_Pump t + phase + quad)`；正交相差 90° 时 Y 是负 sine。
按 `(waveform - offset) / (Vpp / 2)` 归一化，越界明确报错，不静默裁剪。

## 采集流程与取消

流程清单以 [stages.py](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw/stages.py) 为准。
前置必须已有效完成，或显式包含在本次选择中；缺项时列出步骤并拒绝执行。
重跑前置或应用受影响参数后，对应步骤及后继标为需重跑，程序不自动补做。

| 节点 | 执行职责 | 前置 |
|---|---|---|
| A01 | 无硬件启动预检 | 无 |
| A02 | 设备连接与同资源复用 | A01 |
| A03 | 参考时钟同步与严格回读 | A02 |
| A04 | 初始输出设置 | A03 |
| A05 | 温度稳定 | A04 |
| A06 | Pump 载波与门控 | A05 |
| A07 | XYZ 共同触发 | A06 |
| A08 | Bell-Bloom / Demod0 校相 | A07 |
| A09 | XY DirectAW 上传与迭代校相 | A08 |
| A10 | HF2 回环与 Z RF 响应准备 | A09 |
| A11 | 控制 AM 与 Z RF 触发相位扫描 | A10 |
| A12 | Demod3 校相 | A11 |
| A13 | 有符号 Z RF 幅度扫描 | A12 |
| A14 | 零 RF 工作点 X 噪声采集 | A13 |
| A15 | 恢复响应配置、关闭 XYZ、恢复温控并完成本轮 | A14 |
| A16 | 安全收尾并结束设备会话 | 无 |

业务按职责拆分为准备、激励、校相、采集与分析步骤，`workflow.py` / `analysis.py` 负责编排；
`experiments/` 仍是原文件名的薄入口。

1. 创建运行目录及参数、设备库、解析映射、安全限值快照；连接设备，严格同步并回读参考时钟。
2. 设置光功率、主场、温控、Pump 载波和门控。XY/Z 各通道先归 DC 0 再统一关闭输出
   （`setup_dc` 会顺带开输出），Demod0 校相时控制场真正关闭。启动时序信号2的连续
   共同触发并显式打开通道 SYNC（仪器在 DC 波形下会自动关闭 SYNC，切回方波不恢复），
   后续只重新装载或 arm 被触发通道，不重启触发源。
3. XY 与 Z 关闭时校准 Demod0。上传 XY DirectAW，在温控 0 V DC + ON 的窗口内迭代
   XY 控制相位；每次保存诊断和实际波形。未收敛、低 R 或非有限读数立即停止后续测量。
   分步模式标为「质量未通过」、恢复温控，允许同连接重试；完整运行抛出原错误并自动安全收尾。
4. 配置 HF2 物理回环和响应滤波，开启 Z 校相幅度，按 φ、φ+180° 成对顺序扫描 Z RF Burst
   相位；每点重臂后按 `RF_STEP_SETTLE_S` 等待，再以 DAQ 按每点采样时长采集 Demod3 R/X/Y 并逐点保存。
   每完成一个温控分组（末组不足分组点数也算一组）
   将温控恢复为 5 V 并按 `TEMP_SWITCH_ON_LAG_S` 等待，下一组开始前再置 0 V 并按 `TEMP_OFF_SETTLE_S` 等待后继续，避免温度持续下降。
   扫描完成后用正交拟合（不设 R²/显著性门槛）选定最优 RF 触发相位，设为正 RF 相位基准并绘制
   `results/z_phase_scan.png`；拟合数值异常即停止。随后以共享步骤在该相位上校准
   Demod3，保存实际节点。
5. 扫描 Z RF 幅度：与相位扫描节奏一致，按温控分组关闭温控、组间恢复。每点只用 `set_amplitude()`
   调幅，负 RF 将 Burst 相位加 180°（相对选定相位），零 RF 关闭输出/Burst；重臂后按
   `RF_STEP_SETTLE_S` 等待，再以 DAQ 按每点采样时长采集 Demod3 R/X/Y，逐点保存均值、标准差与时序，
   相位取均值复矢量的角度。
6. Z RF 关闭后切换 Demod3 到噪声滤波，读取实际 rate，以该 rate 配置 DAQ 网格，逐次采集
   `sample.x` 并保存。每次记录后恢复温控，A15 恢复 Demod3 响应配置；异常时在结束会话的收尾中补做恢复。
7. A15 共享安全收尾成功后才标记完整采集，保存最终回读和报告。分步模式保持连接与硬件锁，
   可选择该目录独立分析；完整运行自动结束会话并通过分析子进程生成结果。

分步等待时保持最近输出状态并持续占用全局硬件锁。RF 扫描前回读 HF2 响应滤波、
XY/Z 波形与外触发 Burst、共同触发模式，仅保存实际设置，不比较请求值或阻止扫描。
A10 设置完成后同样保存回读，A11/A13/A14 开始前刷新记录；响应扫描以回读的实际采样率配置 DAQ 网格。
A15 将 XY/Z 归 DC 并关闭，A09 及后继因此需重新执行；下一轮显式选择 A09–A15，
复用仍有效的准备步骤。重跑新建目录，保存当轮参数；有效校准或响应副本记录来源，
旧轮次的配置和原始数据保留。

扫描、稳定等待、校相和 DAQ 均有协作式取消检查点；取消、异常及 Ctrl+C 保留已采集文件、
状态和失败原因。执行中使用任务取消，空闲时用 A16 或「结束会话」；子进程 stdin EOF 和
后端关闭也会自动收尾。页面刷新恢复存活会话，后端重启不会恢复硬件连接。
采集进度和 ETA 经统一任务协议上报，分析及结束阶段清空 ETA。

## 安全结束状态

使用共享 `run_safety_shutdown()`，逐项尝试收尾，失败时继续执行其余动作并保存全部错误；
收尾错误不会覆盖原始 DAQ/取消异常，收尾失败不能作为成功运行进入分析。

- 归零并关闭 X/Y 与 Z RF，含相应 Burst 与调制。
- **时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲在收尾后保持运行**，不关闭其 SYNC；
  温控开关恢复为 **5 V DC + 输出 ON**。
- 按仓库标准保留主磁场、Pump/Probe 光功率、Pump 射频载波和 HF2 配置。
- A15 完成本轮时保留设备连接；A16 正常结束只断开 TEC 通信，完成收尾后释放硬件锁。
  已完整完成的轮次不因随后结束或取消等待而改写配置或撤销分析资格；连接阶段失败释放已连接设备。

## 数据与离线分析

```text
data/RF_Field_Sensitivity_AW/<run_id>/
  experiment_config.yaml
  acquisition_state.json
  analysis_state.json
  raw/
    xy_aw_waveforms_initial.npz
    xy_aw_waveforms.npz
    z_phase_scan.npz
    response_data.npz
    noise_D3_0000.npz
    ...
  results/
    z_phase_scan.png
    response_fit.npz
    psd_avg.npz
    sensitivity.npz
    analysis.yaml
    analysis.json
    response_curve.png
    noise_psd.png
    sensitivity.png
    full_analysis.png
```

运行配置保存新格式标记、采集/校相/分析状态、触发来源、正负 RF 相位、校相历史、
RF 相位扫描选定相位、响应定长采集信息（时长、实际采样率、温控分组点数）、实际响应/噪声采样率、HF2 节点回读和安全收尾报告。
`hf2_response_snapshot` 保存最近一次响应设置回读，`response_output_snapshot` 保存对应的 XY/Z 与共同触发通道设置；
`hf2_noise_snapshot`、`hf2_final_snapshot` 分别记录噪声配置和收尾后的 HF2 设置。
`raw/z_phase_scan.npz` 保存成对扫描相位轴、每点 R/X/Y 时序与均值/标准差及正交拟合
中间量；`results/z_phase_scan.png` 上幅为 R/X/Y 随相位曲线，下幅为成对同相/正交分量
与拟合及选定相位，拟合失败时仅绘制实测曲线。原始响应包含有符号磁场轴、每点 X/Y/R 均值与
标准差、逐点时序矩阵、相位、采样时长与实际采样率；噪声 NPZ 包含 `noise_x_V`、`actual_rate_Sa_s`、Demod 索引和信号路径。
结果图使用统一 `paper` 风格，并按公共绘图接口配套保存 PDF。`full_analysis` 为双面板总览：
上幅响应曲线同时绘制 X/Y/R 与色散拟合（标注 R²、dX/dB 斜率、色散半宽与换算后的频率 HWHM、拟合中心虚线），
下幅灵敏度谱绘制洛伦兹校正结果和未校正结果，高亮校正后的平坦频段并标注 HWHM 与中位数水平线；
轴范围与静磁场灵敏度分析一致：横轴 0.5 Hz 至 max(2×HWHM, 平坦段上限, 1000 Hz)，不再铺满全频段；
纵轴上限 20 倍平坦段中位数、下限 0.8 倍可见曲线最小值，裁掉 1/f 噪声与共振发散；
色散拟合回退时无 HWHM，横轴自动放宽到全频段且纵轴保持自动范围。

独立分析以具体运行目录为对象，四个阶段复用完整分析的算法与产物：

| 节点 | 执行职责 | 前置 |
|---|---|---|
| B01 | 加载配置、校验校相与原始数据 | 无 |
| B02 | X 色散响应拟合与斜率 | B01 |
| B03 | Welch X 噪声 PSD | B02 |
| B04 | RF 灵敏度、洛伦兹频响补偿与结果图 | B03 |

运行目录只支持 `NOISE_NPERSEG` 分析覆盖值，单独保存在 `analysis_state.json`，不改变采集
参数快照或下一次实验默认值。阶段记录保存输入与产物指纹、修订和状态；配置、raw 或前置
产物变化时拒绝复用。重算只将受影响阶段及后继的旧结果移入 `results/before_<时间戳>/`，
保留仍有效的前置输入。旧新格式记录没有阶段状态时从 B01 重建；正在采集的目录拒绝分析，
同一目录的分步分析和历史重新分析互斥，对比任务读取结果时也禁止重算。

历史对比独立读取已保存的响应拟合和灵敏度数组，保持各次的实际磁场坐标、
频率点和物理单位，使用同色表示同一次运行。对比上幅只绘制 X 响应点及已有色散拟合线，
下幅只绘制校正灵敏度主谱，不叠加原始谱、平坦段、中位数或 HWHM 标记，也不提供统计表；
下幅轴范围与单次运行灵敏度谱一致：横轴 0.5 Hz 至各已校正运行 max(2×HWHM, 平坦段上限, 1000 Hz) 的最大值，
全部未校正时退回全频段；纵轴上限 20 倍平坦段中位数的最大值、下限 0.8 倍可见曲线最小值。
拟合失败时，上幅保留实测 X 点，下幅使用已有未校正谱，页面明确报告数值斜率回退；
对比不会重算拟合、PSD 或灵敏度，不平移、归一化、平滑或重新采样。

X 响应使用原色散模型：

`X(B) = a (B - B0) / ((B - B0)^2 + gamma^2) + offset`，
`|dX/dB| = |a| / gamma^2`。不足五点或拟合失败时用数值梯度最大绝对值回退，
保留方法、拟合失败标记和原因。噪声采集工作点仍是零 RF，拟合中心只作诊断记录。

每条 X 噪声先去均值，Welch 段长取配置值与实际记录长度的较小者，50% 重叠；必要时插值到
首条记录的频率轴，再等权平均 PSD。灵敏度首先按
`sens_raw(f) = sqrt(PSD_X(f)) / (slope_V_per_nT / 1e6)` 计算，单位为 fT/√Hz。
若 X 对 Z RF 场的色散拟合成功，则将拟合半宽 `gamma_nT` 按
`f_larmor_Hz = gamma_nT × LARMOR_PER_NT` 换算为等效频率 HWHM，并按
`sens_corrected(f) = sens_raw(f) × sqrt(1 + (f / f_larmor_Hz)^2)` 补偿洛伦兹型滚降。
报告中位数使用校正后的谱，在 `[3 Hz, HWHM]` 内取有限频点；没有有效 HWHM 时不补偿，
回退为所有 `≥3 Hz` 的有限频点。`results/sensitivity.npz` 同时保存原始谱、校正谱、校正因子、
`gamma_nT` 和 `f_larmor_Hz`。这里的 HWHM 来自 RF 场幅度色散拟合，是等效线宽，不替代独立
`rf-sensitivity-direct-aw-frequency` 扫频实验。

分析器只接受完整采集且校相通过的 schema v2 新格式，先校验输入再写结果。旧格式、
缺失响应点或噪声记录会明确拒绝；旧目录不会删除或改写，本次不兼容其数据内容。
离线分析不读取仪器、不依赖当前波形 CSV 或当前默认参数。

## GUI 运行 / 历史

实验中心 `/experiments/rf-sensitivity-aw` 提供两个主 Tab。

- **运行**：流程容器顶部保留参数与布局保存、RF 采集预览、独立预检、预检并完整运行、
  取消、进度日志和 ETA；左列为 16 个采集节点，右列为 4 个独立分析节点。
  基础参数名称常显，展开节点编辑，高级参数单独折叠，多个节点可同时展开。
  共享过程参数（如关闭温控后等待、恢复温控后等待、扫描点采样时长）跨使用节点同步；
  初始化条件只在设置节点展示，基础/高级分类按字段统一保存。
  存活会话的修改标为「草稿未应用」，预览预计需重跑的已完成步骤；应用成功后显式重跑，
  草稿不会改写执行记录，应用失败保留草稿。共同触发只显示一组并同步两套稳定参数键。
  HF2 回环和 Z RF 相位扫描参数仍在相应节点；波形版本来自 oc_sens，运行前需手动选择。
  分析操作对象默认为「下一次实验」，与采集参数共用同键值；选择具体已完成目录后编辑独立
  覆盖值，执行分析时生效。各目录草稿分别保留，切换对象或主 Tab 后返回可继续编辑。
  保存默认值始终只保存下一次实验参数与统一分类，历史分析覆盖值不写入默认 YAML。
- **历史**：新格式运行分页列表；该次参数快照、采集/分析/校相状态、诊断、实际回读、
  结果图与下载。填回只更新当前 schema 识别的字段并切回运行，不保存默认值或自动采集。
  切换 Tab 保留未保存参数。完整采集且校相通过时可重新分析，使用现有非硬件任务，
  完成后刷新摘要和图片；未完成采集或校相失败只展示诊断。
  列表增加独立对比勾选框，右侧可切换「单次详情 / 对比分析」。选择至少两次完成分析、
  校相通过且结果齐备的运行，支持跨页选择；按勾选顺序绘制，每次运行填写一个英文图例，
  留空时使用运行 ID。切换主 Tab 保留选择与图例，点击「生成对比」后预览并下载 PNG/PDF。
  修改选择或图例会隐藏旧预览及下载链接，并提示重新生成。无效运行显示不可对比原因，
  缺损结果需通过原「重新分析」操作补齐，不会自动重算。对比任务可取消，任务执行期间
  禁止重复提交及同实验的重新分析、删除；完整双格式导出后才开放下载。

历史列表和摘要分别使用 `GET /api/experiments/rf-sensitivity-aw/runs`、
`GET /api/experiments/rf-sensitivity-aw/runs/{run_id}/summary`，结果访问与重新分析
复用通用接口，浏览历史不会连接仪器。

对比使用 `POST /api/experiments/rf-sensitivity-aw/compare`，请求为
`{"runs": [{"run_id": "<run-id>", "legend": "<English label>"}, ...]}`，返回现有非硬件 Job。
源运行目录只读；独立产物保存到 `results/rf-sensitivity-aw/comparisons/<job_id>/`：
`full_analysis_comparison.png`、同名 PDF 和记录来源、顺序、图例与回退状态的 `comparison.json`。
图片通过专用的 `GET /api/experiments/rf-sensitivity-aw/comparisons/<job_id>/artifacts/<name>` 下载。
历史列表与摘要中的 `can_compare`、`comparison_block_reason` 提供对比资格和禁用原因。
