---
title: RF 灵敏度（DirectAW）
type: experiment_type
description: XY 直接任意波控制下测量 RF 灵敏度，采用可勾选、检查依赖的流程图，保持设备会话并按节点编辑参数。最优控制波形从 oc_sens 版本目录多选，各控制独立校相、采集和分析，共用本轮 RF 参数且不自动改频；PSD 支持按控制独立的可关闭 MAD 高值异常谱筛选，各控制可保存默认 1 的手动 XY 包络补偿系数；A11 可跳过触发相位扫描并使用手动输入相位，其余校相保持；随后按温控分组执行定长 DAQ，以 RF 解调器 X 响应斜率和 X 噪声 PSD 计算灵敏度；A14 噪声采集后，可选 A13B 恢复 A11 响应滤波，以校相幅度同步扫描 Z RF 源与解调器频率，对每点 R 均值做洛伦兹拟合，线宽用于频响补偿，未启用时回退色散拟合等效 HWHM；扫频步进由窗口与点数确定，无共同触发频率整数倍限制。仪器设置只回读记录，DAQ 使用实际采样率。XYZ 共用时序信号2主输出触发，HF2/MFLI 经所选物理线路或 MFLI 内部 AuxOut 两级解调；独立分析支持四阶段重算，历史支持多次对比和 PNG/PDF 导出。 RF 解调器索引与辅助通道可选，HF2 支持物理回采，MFLI 支持物理或内部 AuxOut 回采。锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
experiment_id: rf-sensitivity-aw
defaults_file: params/experiments/rf-sensitivity-aw.yaml
version: 9
schema_version: 3
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
required_devices: [GS200, signal_generator, lockin_amplifier, TEC103]
learned_notes:
  - XYZ 共用时序信号2的主输出外触发，校相前启动，后续保持连续输出；通道 SYNC 同步开启并持续运行。
  - 仪器在 DC 波形下会自动关闭通道 SYNC，切回方波/脉冲后需显式重新打开（setup_* 不会自动恢复）。
  - setup_dc 会顺带打开输出；初始化先归 DC 再统一关闭，Demod0 校相时 XY/Z 控制场真正关闭。
  - HF2 仅支持物理回采，MFLI 可选择物理或内部 AuxOut 回采；辅助输出与输入通道均可选。
  - XY 校相未收敛或 R 低于门槛时停止后继并保存诊断；分步会话允许原连接重试，完整运行自动收尾。
  - RF 触发相位扫描按 φ、φ+180° 成对顺序采集，正交拟合不设 R²/显著性门槛，拟合异常即停止。
  - 结束时保留时序信号2（共同触发及其 SYNC）与 Pump 门控脉冲继续运行，只关闭 XY 与 Z RF。
  - 温控开关关闭为 0 V DC + ON，相位/幅度/带宽扫描按温控分组点数分组关温控（末组不足分组点数也适用），组间恢复为 5 V DC 并按温控恢复后等待，结束保持 5 V DC + ON。
  - A13B 带宽扫频逐点同步 Z 通道频率与 RF 解调器振荡器；扫频结束（含异常/取消）恢复原 Z RF 频率并关闭 Z 输出。
---

# RF 灵敏度（DirectAW）

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

累积响应、可选带宽和校相 NPZ 先写临时文件，再原子替换目标文件；写入失败保留上一份完整快照并清理临时文件。温控恢复仍先于写盘，原始采集或恢复错误不被保存错误覆盖。单次噪声文件保持原有保存策略。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

RF 相位/响应/带宽使用 `DEMOD3_RATE` 请求率、`SCAN_POINT_DURATION_S` 时长和 `DEMOD3_IDX` 解调器；RF 噪声使用 `NOISE_RATE` 请求率、`NOISE_DURATION` 时长和 `DEMOD3_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

本实验在 Bell-Bloom 磁力仪的 XY 直接任意波控制条件下测量 Z 方向 RF 灵敏度。
运行页采用与 XY 正弦实验一致的可执行流程图：显式勾选步骤、校验依赖、按节点编辑参数，
在同一设备会话中分步执行或重试；完整运行与分步模式调用同一套业务步骤。
仪器设置后回读并记录实际值，DAQ 使用实际采样率；参考时钟严格回读，输出安全范围始终强制校验。
独立的 [DirectAW R 实验](rf_field_measurement_r.md) 共用本实验的准备、回环、采集及安全收尾，
采用 R 标定后改频采集 X 噪声的顺序，分别保存默认参数和历史。
最优控制包络读取 `D:\Code\theory_agent\simulate\results\oc_sens` 的版本化理论输出，
运行前在 GUI 中明确选择一个或多个版本。A01–A08 共用设备、时钟、固定条件与 Demod0 校相；A09–A14 和可选 A13B 按阶段依选择顺序遍历全部控制，恢复各自的 XY 相位与 RF 解调设置，独立完成校相、响应、零 RF 噪声和扫频。所有控制共用本轮 RF 参数，不按控制的设计频率自动改频，也不注入额外噪声。每个已选控制都在连接前检查补偿后的电压和仪器能力。
`CONTROL_SCALE_FACTORS` 按控制名称保存手动补偿倍率，未设为 1：先计算 `Omega_applied(t) = factor * Omega_nominal(t)`（包括平均分量），再以 `(Omega_applied - XY_CTRL_B_HZ) / XY_CTRL_K_HZ_PER_V` 转电压；有非零截距时不能简单把电压乘倍率。只改变 XY 包络，不改主场、Z RF、时间轴或独立校相策略；补偿后仍在连接前检查电压边界，零或低信号仍须通过原校相质量门槛。
初始化依次执行四级相位校准：Bell-Bloom（Demod0）、控制载波与 Pump 调制（XY DirectAW）、
控制 AM 调制与 RF 场（A11 可扫描 Z RF Burst 相位，也可跳过扫描并手动指定相位）、RF 解调器。
关闭 `Z_PHASE_SCAN_ENABLED` 时使用 `Z_BURST_PHASE_INIT` 作为正 RF 相位基准，不采集或拟合相位曲线；其他校相不跳过。
随后按温控分组扫描有符号 Z RF 幅度：
每点重臂后以 解调时序采集定长时序，取 **X 均值**（同时保存 Y/R 均值、标准差与时序）标定
电压到磁场响应，再在零 RF 输出下采集 **RF 解调器 X 噪声**，用 Welch PSD 计算
灵敏度谱。幅度标定采用 X；R 用于校相信号门槛、诊断与带宽扫频响应。
在 A14 零 RF X 噪声采集后，可选择执行 A13B 带宽扫频：恢复与 A11 相同的响应滤波，
以 Z 校相幅度在配置窗口内逐点改 Z RF 频率，同时将 RF 解调器振荡器设为相同频率，
扫频步进由窗口与点数确定，不要求为共同触发频率的正整数倍。
每点采集配置时长的 R 时序，对 R 均值做现有洛伦兹拟合得到线宽，
用于灵敏度谱频响补偿；未启用时沿用色散拟合的等效 HWHM。

历史页面支持星标、星标筛选、目录重命名和确认后永久删除本次数据，也可勾选多次完整分析的运行，将保存的 X 响应和灵敏度主谱叠加到双面板图中，
自定义英文图例并导出 PNG/PDF。新多控制运行按控制展开为独立曲线，可与旧单控制运行混合对比；中文控制名称在跨次英文图例中用控制序号标识，清单保留原名称。对比过程只读历史结果，拟合回退会明确提示。

当前正式入口为 `rf-sensitivity-aw`（`typed_workflow`、参数 schema version 4），
数据类型和原 Python 文件名保留。Notebook 是历史实验入口，本次没有迁移。
可编辑流程图源文件见 [RF 灵敏度（DirectAW）.excalidraw](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw/RF%20灵敏度（DirectAW）.excalidraw)，运行页提供下载。
当前页面没有架构 Tab。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

锁相设备在「仪器控制 → 设备库 → 锁相放大器」中全系统统一选择。HF2 支持所选 AuxOut 经 BNC 接所选 Aux Input 或原 Signal Input2；MFLI 支持所选 AuxOut 经 BNC 接所选 Aux Input，也支持直接读取所选 AuxOut 的内部线路。MFLI 需要 MF-MD 选件，连接后在启动实验输出前再次检查真实选件。预检拒绝不支持的组合，不自动改接线、降频或换设备；两款设备及不同线路的增益、带宽和相位响应仍待实机验证。接口差异见 [锁相接口说明](lockin_amplifier.md)。

## 入口与参数来源

- 采集：[RF_Field_Sensitivity_AW.py](../experiments/RF_Field_Sensitivity_AW.py)。
- 离线分析：[RF_Field_Sensitivity_AW_plot.py](../experiments/RF_Field_Sensitivity_AW_plot.py)。
- 实现：[实验模块](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw/)。
- 当前默认值：[params/experiments/rf-sensitivity-aw.yaml](../params/experiments/rf-sensitivity-aw.yaml)。

GUI「保存参数与布局」实际写入上述 YAML 和实验目录配置中的字段布局。模型中的值仅作
缺少参数文件或字段时的回退；运行覆盖优先于默认 YAML。单次运行的真实参数以其
`experiment_config.yaml` 的 `parameters` 为准，离线分析读取该快照与原始数据。

`CONTROL_WAVEFORM_FILES` 保存所选控制的有序列表，每项为相对于上述 `oc_sens` 目录的
`<控制名称>/waveforms/optimal_control_waveform.csv`。`ARB_WAVEFORM_FILE` 保留为旧单选兼容键，旧配置自动转为单项列表；完整多选键优先，显式空列表仍拒绝启动，不从旧键悄悄补选。GUI 仅列出存在波形文件且解析后仍位于该目录
内的 `vN` 版本，不列出 `spectra/` 或 `parameters/` 中的 CSV，也不自动选择最新版本。
目录不可用或版本尚未选择时预检会阻止采集。
直接运行脚本也读取参数 YAML，所选列表中的每项都必须指向有效版本。公共周期、标定和输出幅度仍按用户参数
校验，不因选版本而自动调整；采集保存的波形 NPZ 包含实际源文件路径和原始包络。

直接启动采集：

```powershell
.\agent_exp_env\Scripts\python.exe experiments/RF_Field_Sensitivity_AW.py
```

离线分析必须明确给出运行目录，例如下列占位路径需替换为真实的新格式记录：

```powershell
.\agent_exp_env\Scripts\python.exe experiments/RF_Field_Sensitivity_AW_plot.py data/RF_Field_Sensitivity_AW/<run_id>
```

## 接线与 RF 信号链

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
| 锁相 Signal Input1 | 主探测信号 → Demod0（振荡器0参考 Pump/Larmor 频率） |
| 物理线路 | **Demod0 Y → 所选 AuxOut → BNC → 所选 Aux Input → RF 解调器**；HF2 还可选择原 Signal Input2，振荡器1参考 Z RF 频率 |
| 内部线路（MFLI） | **Demod0 Y → 所选 AuxOut 内部值 → RF 解调器**，不需要 BNC 回环线 |

两款设备均可选 AuxOut1–4、Aux Input1–2；输出通道底层索引为 0–3。RF 解调器索引从 0 开始计数，由 GUI 的 `DEMOD3_IDX` 设置，禁止占用主解调器 0，并按型号/选件检查上限。HF2 物理输入编码为 Signal Input2=1、Aux Input1=2、Aux Input2=3；MFLI Aux Input1/2=8/9，内部 AuxOut1–4=4–7。`DEMOD3_ADC_SELECT` 仅作为隐藏兼容键，由线路选择派生，不再独立编辑。HF2 不支持内部 AuxOut 回采。[HF2 节点说明](https://docs.zhinst.com/hf2_user_manual/nodedoc.html#demods)、[MFLI 节点说明](https://docs.zhinst.com/mfli_user_manual/nodedoc.html#demods)

回环增益与偏置应用于所选 AuxOut。Signal Input2 量程和差分仅在 HF2 物理线路选用该输入时配置；辅助输入及内部线路不套用主输入设置。具体参数以默认 YAML 为准，每次运行另存实际节点回读与实际采样率。现场应核对时序信号2分路和所选物理线路的 BNC 接线。
锁相滤波时间常数、采样率等设置受仪器支持的离散值限制，请求值与实际回读值可以不同；
请求值保存在 `parameters`，实际值保存在节点快照中。

共用 A10 按所选设备和 AuxOut 索引读取 `params/safety_limits.yaml` 的 `<设备库ID>_aux_output_<索引>` 规则。两款设备全部四路输出均已确认允许 ±10 V；该输出范围不能替代 Signal Input2 的输入量程。预检按 Demod0 输入量程计算 `offset ± abs(scale) × sqrt(2) × input_range`；写入前再次检查实际来源与量程，缺少上下限或预计越界时拒绝启动/写入。MFLI 同时设置硬件上下限、清除 Preoffset，并拒绝启用 Tip Protect 的输出。
只读 `lockin_r` 规则不能用于回环输出校验；不会自动改接线、降增益或换仪器。

A10 内编辑 `RF_INPUT_ROUTE`、`RF_AUX_OUTPUT_IDX`、`RF_PHYSICAL_INPUT_SOURCE` 和 `DEMOD3_IDX`；修改并应用后使 A10 及后继失效，须显式重跑，重跑另建运行目录。内部线路的物理输入项不生效。缺少新线路键的旧参数按 HF2 AuxOut2 → Signal Input2 解释；历史填回保留该接线，使用 MFLI 时需明确改选辅助输入，否则预检拒绝。

稳定实验 ID、原始数据格式和 schema 2 历史兼容、`DEMOD3_*` 参数键、`demod3_*` 数据键和 `noise_D3_*` 文件名保留。新采集文件的 `demod_idx` 保存真实 RF 索引，分析按运行快照 `DEMOD3_IDX` 核对，旧快照缺该键时按 3。配置与历史摘要保存真实 `signal_chain`，不从当前默认值重建历史接线。

## 参数含义与预检

已有 GUI/YAML 外部键保持不变，模型内部使用 `snake_case`。

| 参数键 | 单位与约束 |
|---|---|
| `RF_AMP_START` / `RF_AMP_STOP` / `RF_AMP_POINTS` | 有符号 Vpp 扫描，起点小于终点，至少两个点；负号用 Burst 相差 180° 表示 |
| `Z_RF_FREQ` / `Z_BURST_PHASE_INIT` | Hz / °；`Z_BURST_PHASE_INIT` 是正 RF 触发相位（Burst）：开启扫描时为初始值，扫描成功后由选定相位取代；关闭扫描时使用此手动输入值，必须为有限数，按 360° 归一化，负幅度加 180°。当前默认值见参数文件 |
| `Z_PHASE_SCAN_ENABLED` | 是否扫描 RF 触发相位；开启时执行原 A11 扫描与拟合，关闭时仅应用手动相位并记录来源；旧参数缺少此键时仍执行扫描 |
| `Z_V_TO_NT` | Z 通道电压到 RF 磁场大小的转换系数，nT/V；响应磁场轴、配置快照和灵敏度换算使用该值，离线分析读取运行时保存的快照 |
| `Z_PHASE_SCAN_START_DEG` / `Z_PHASE_SCAN_STOP_DEG` / `Z_PHASE_SCAN_STEP_DEG` | RF 触发相位扫描范围与步进，°；开启扫描时范围被步进整除，至少 6 个偶数点恰好覆盖一个 360° 周期；关闭扫描时不校验扫描范围的一致性 |
| `SCAN_POINT_DURATION_S` | 相位扫描、幅度扫描与带宽扫频每点时序采样时长，s；三段扫描共用同一时长与 RF 解调器 响应滤波（原键 `Z_PHASE_SCAN_DURATION_S`，加载时自动迁移） |
| `LINEWIDTH_MODE` | 线宽模式：`frequency_sweep` 在 A14 后执行 A13B，以 R 均值的洛伦兹拟合线宽回补；`dispersive_equivalent` 沿用色散拟合等效 HWHM，完整运行不包含 A13B；当前值见默认参数文件 |
| `BW_FREQ_START` / `BW_FREQ_STOP` / `BW_FREQ_POINTS` | 带宽扫频窗口 Hz 与点数（至少 5）；窗口必须完整包含 `Z_RF_FREQ`，起点与终点须为共同触发频率的正整数倍；步进由 `(BW_FREQ_STOP - BW_FREQ_START) / (BW_FREQ_POINTS - 1)` 确定，无共同触发频率整数倍限制 |
| `TEMP_GROUP_SIZE` | 温控分组点数：相位/幅度扫描与带宽扫频中每关闭一次温控连续采集的扫描点数，组间恢复温控 |
| `SYSTEM_SETTLE_S` | RF 换点稳定等待，s：每次重新设置 Z RF 相位、幅度或频率（重臂）后等待（原键 `RF_SETTLE_TIME`，加载时自动迁移） |
| `SYSTEM_SETTLE_S` | 温控关闭后稳定等待，s：每次关闭温控后等待温控影响减小，XY 校相每轮重载波形后同样等待（原键 `XY_PHASE_SETTLE_TIME`，加载时自动迁移；旧键 `RESPONSE_N_AVG` / `RESPONSE_AVG_INTERVAL` 已删除并自动剥离） |
| `TEMP_SWITCH_ON_LAG_S` | 每个温控分组（末组不足分组点数也适用）或其他阶段恢复温控（置 5 V）后的等待，s |
| `NOISE_N_AVG` / `NOISE_DURATION` / `NOISE_RATE` / `NOISE_NPERSEG` | 噪声记录数、s、请求 Sa/s 和 Welch 段长；计算 PSD 使用实际采样率 |
| `CONTROL_WAVEFORM_FILES`（兼容旧 `ARB_WAVEFORM_FILE`）/ `AW_REPEAT_FREQ` | oc_sens 下的 `vN/waveforms/optimal_control_waveform.csv`；时间列为秒，AW 重复频率为 Hz；运行前必须选择版本 |
| `CONTROL_SCALE_FACTORS` | 以控制名称为键的有限手动补偿倍率；未设为 1，仅所选控制生效，不根据电感自动计算；旧配置缺键同样按 1 |
| `XY_CTRL_K_HZ_PER_V` / `XY_CTRL_B_HZ` | 控制包络频率到电压的 K/B 标定，K 不为零 |
| `XY_AW_OUTPUT_VPP`（已废弃）/ 满量程选择 | AW 满量程自动跟随包络峰值：`vpp = 2 × max|V_env|`，offset 恒为 0；旧键 `XY_AW_OUTPUT_VPP` / `XY_AW_OUTPUT_OFFSET` 已删除，加载时自动剥离；物理波形与满量程边界仍须通过 X/Y 安全限值，非零包络的自动幅度低于仪器最小 0.001 Vpp 时预检拒绝；显式零补偿且电压包络为零时采用合法最小满量程配全零 DAC，不伪造最小控制场，校相质量门槛不跳过 |
| `XY_CTRL_PHASE` / `XY_CTRL_QUAD` | 初始控制相位与 XY 正交相差，单位 ° |
| `XY_CTRL_PHASE_TOL_DEG` / `XY_CTRL_PHASE_MAX_ITER` | XY 校相容差和最大更新次数，初始测量另计一次 |
| `XY_CTRL_PHASE_MIN_R_V` / `XY_CTRL_PHASE_MIN_R_RATIO` | 绝对最低 R 及相对历史最佳 R 的门槛，低于门槛停止 |
| `XY_TRIGGER_*` / `Z_TRIGGER_*` | 同一共同触发的频率、Vpp、offset、占空比、相位；GUI 只呈现一组并同步提交两套旧键，预检拒绝不一致 |
| `DEMOD0_*` / `DEMOD3_*` | 锁相的响应、校相与噪声滤波参数；主解调器/振荡器固定为 0，RF 振荡器固定为 1，RF 解调器索引可选且不能为 0 |
| `RF_INPUT_ROUTE` / `RF_AUX_OUTPUT_IDX` | 物理/内部线路（内部仅 MFLI）；AuxOut1–4 对应索引 0–3 |
| `RF_PHYSICAL_INPUT_SOURCE` | Aux Input1/2 或 HF2 Signal Input2；仅物理线路生效 |
| `LOOPBACK_AUX_SCALE` / `LOOPBACK_AUX_OFFSET_V` | 所选辅助输出增益与偏置 V |
| `LOOPBACK_INPUT_RANGE_V` / `LOOPBACK_INPUT_AC` | 仅 HF2 Signal Input2 线路应用量程 V；AC 固定关闭，旧键保留且隐藏 |
| `LOOPBACK_INPUT_IMPEDANCE_OHM` / `LOOPBACK_INPUT_DIFF` | 仅 HF2 Signal Input2 线路应用单端/差分；主输入保持高阻，旧阻抗键保留且隐藏 |
| `FIXED_PARAMS.*` | 光功率、主磁场电流、温度和温控开启电平 |

预检只读取配置和 oc_sens 中选定的 CSV，不连接仪器；检查实际映射类型、端点、任意波与无限 Burst 能力，
以及共同触发两套键是否一致。AW 重复频率和 Z RF 频率均须为共同触发频率的正整数倍；
线宽模式选择扫频测线宽时，带宽窗口约束同顶预检检查。

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
| A05 | 目标温度设置 | A04 |
| A06 | Pump 载波与门控 | A05 |
| A07 | XYZ 共同触发 | A06 |
| A08 | Bell-Bloom / Demod0 校相 | A07 |
| A09 | XY DirectAW 上传与迭代校相 | A08 |
| A10 | RF 输入线路与 Z RF 响应准备 | A09 |
| A11 | 控制 AM 与 Z RF 触发相位扫描（可跳过扫描，改为应用手动相位） | A10 |
| A12 | RF 解调器校相 | A11 |
| A13 | 有符号 Z RF 幅度扫描 | A12 |
| A14 | 零 RF 工作点 X 噪声采集 | A13 |
| A13B | Z RF 扫频带宽测量（可选，需 `LINEWIDTH_MODE=frequency_sweep`） | A14 |
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
4. 配置所选 RF 输入线路和响应滤波，开启 Z 校相幅度。在 A11 节点内设置「扫描 RF 触发相位」和「正 RF 触发相位」。
   关闭扫描时仅将手动相位归一化后应用到 Z RF，保存 `mode=manual`、`scan_skipped=true` 和正负相位，
   不关闭温控、不采集相位时序、不拟合或生成相位图；随后 A12 仍在该手动相位上校准 RF 解调器。
   **分步模式仍需选择并执行 A11 来应用手动相位，不是仅取消勾选 A11 节点**；修改开关并应用后使 A11 及后继失效，
   修改相位值还会使使用该初始值的 A10 及后继失效，须显式重跑；完整运行自动使用同一设置。
   开启扫描时按 φ、φ+180° 成对顺序扫描 Z RF Burst 相位；每点重臂后按 `SYSTEM_SETTLE_S` 等待，再以统一采集模块按每点采样时长采集 RF 解调器 R/X/Y 并逐点保存。
   每完成一个温控分组（末组不足分组点数也算一组）
   将温控恢复为 5 V 并按 `TEMP_SWITCH_ON_LAG_S` 等待，下一组开始前再置 0 V 并按 `SYSTEM_SETTLE_S` 等待后继续，避免温度持续下降。
   扫描完成后用正交拟合（不设 R²/显著性门槛）选定最优 RF 触发相位，设为正 RF 相位基准并绘制
   `results/z_phase_scan.png`；拟合数值异常即停止。随后以共享步骤在该相位上校准
   RF 解调器，保存实际节点。
5. 扫描 Z RF 幅度：与相位扫描节奏一致，按温控分组关闭温控、组间恢复。每点只用 `set_amplitude()`
   调幅，负 RF 将 Burst 相位加 180°（相对选定相位），零 RF 关闭输出/Burst；重臂后按
   `SYSTEM_SETTLE_S` 等待，再以统一采集模块按每点采样时长采集 RF 解调器 R/X/Y，逐点保存均值、标准差与时序，
   相位取均值复矢量的角度。
6. A14 在 Z RF 关闭后切换 RF 解调器到噪声滤波，按噪声请求率选择模块；DAQ 按通信回读率配置网格，Scope 按最近硬件档位记录，逐次保存最终速率和真实时间轴
   `sample.x` 并保存，每次记录后恢复温控。
7. 可选带宽扫频（A13B，仅 `LINEWIDTH_MODE=frequency_sweep`）：先恢复 A11 使用的
   `DEMOD3_TC`、`DEMOD3_ORDER`、`DEMOD3_RATE` 响应配置并保存实际回读，主解调器保持响应配置。
   以 `Z_RF_AMPLITUDE_INIT` 和校准选定的正 RF 相位，在 `BW_FREQ_START`–`BW_FREQ_STOP`
   窗口内逐点改 Z RF 频率，同时将 RF 解调器振荡器设为相同频率，重臂并等待后采集
   `SCAN_POINT_DURATION_S` 时长的 R/X/Y 时序。温控分组与等待节奏复用 A11，末组同样恢复温控。
   每点直接取 R 时序均值计算频率响应，逐点保存均值、标准差、原始时序及实际采样率到 `raw/bandwidth_data.npz`，X/Y 保留作诊断。
   扫频结束（含异常/取消）恢复 Z RF 频率与振荡器到 `Z_RF_FREQ`，关闭 Z 输出并恢复温控；各项独立尝试，恢复错误不覆盖原始采集错误。
   分步模式下缺少有效 A14 时拒绝 A13B；线宽模式为色散等效时拒绝执行并提示先切换模式。
   未启用 A13B 时，由 A15 恢复响应配置；异常时结束会话的收尾中补做恢复。
8. A15 共享安全收尾成功后才标记完整采集，保存最终回读和报告。分步模式保持连接与硬件锁，
   可选择该目录独立分析；完整运行自动结束会话并通过分析子进程生成结果。

分步等待时保持最近输出状态并持续占用全局硬件锁。RF 扫描前回读 HF2 响应滤波、
XY/Z 波形与外触发 Burst、共同触发模式，仅保存实际设置，不比较请求值或阻止扫描。
A10 设置完成后同样保存回读，A11/A13/A14/A13B 开始前刷新记录；带宽阶段单独保存 `hf2_bandwidth_snapshot`，保留幅度扫描与噪声阶段的回读。响应扫描的 DAQ 网格使用通信回读率，Scope 使用选定档位，频谱分析统一使用返回波形的最终实际采样率。
A15 将 XY/Z 归 DC 并关闭，A09 及后继因此需重新执行；下一轮显式选择 A09–A15，
复用仍有效的准备步骤。重跑新建目录，保存当轮参数；有效校准或响应副本记录来源，
旧轮次的配置和原始数据保留。单独重跑 A13B，或在首次 A13B 前应用新的带宽参数时，
新目录继承有效的幅度扫描、X 噪声文件、完成计数和实际设置；修改噪声参数会使 A14 与 A13B 需重跑。

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
  controls/<control>/
    experiment_config.yaml
    raw/
      xy_aw_waveforms_initial.npz
      xy_aw_waveforms.npz
      z_phase_scan.npz          # 仅扫描相位时存在
      response_data.npz
      bandwidth_data.npz        # 仅扫频测线宽时存在
      noise_D3_0000.npz
    results/
      response_fit.npz
      psd_avg.npz
      noise_record_quality.csv
      noise_rejection_summary.json
      sensitivity.npz
      analysis.yaml
      full_analysis.png
  results/
    response_fits.json
    psd_index.json
    noise_record_quality.csv
    noise_rejection_summary.json
    sensitivity.npz             # 各控制键带名称前缀
    analysis.yaml
    analysis.json
    full_analysis.png          # 多控制响应与灵敏度汇总
    z_phase_response_comparison.png # 仅扫描相位时存在
```

多控制响应、灵敏度及相位汇总图使用英文图例：ASCII 控制名直接显示，中文等非 ASCII 名称按本轮选择顺序显示为 `Control N`；目录、配置及 GUI 保留原始名称，摘要的 `plot_label` 记录对应图例。

运行配置保存新格式标记、采集/校相/分析状态、触发来源、正负 RF 相位、校相历史、
RF 相位来源（扫描/手动）、跳过标记及最终相位、响应定长采集信息（时长、实际采样率、温控分组点数）、实际响应/噪声采样率、HF2 节点回读和安全收尾报告。
`hf2_response_snapshot` 保存最近一次响应设置回读，`response_output_snapshot` 保存对应的 XY/Z 与共同触发通道设置；
`hf2_noise_snapshot`、`hf2_final_snapshot` 分别记录噪声配置和收尾后的 HF2 设置。
`raw/z_phase_scan.npz` 保存成对扫描相位轴、每点 R/X/Y 时序与均值/标准差及正交拟合
中间量；跳过扫描时不生成该 NPZ 及相位 PNG/PDF，分析仍可读取完整响应与噪声数据。
`results/z_phase_scan.png` 上幅为 R/X/Y 随相位曲线，下幅为成对同相/正交分量
与拟合及选定相位，拟合失败时仅绘制实测曲线。原始响应包含有符号磁场轴、每点 X/Y/R 均值与
标准差、逐点时序矩阵、相位、采样时长与实际采样率；`raw/bandwidth_data.npz`（仅扫频测线宽模式）
保存频率轴、扫频幅度、每点 X/Y/R 均值与标准差、时序矩阵、逐点实际采样率 `point_actual_rate_Sa_s` 和恢复频率标记；保留 `actual_rate_Sa_s` 记录最近采集点的速率。噪声 NPZ 包含 `noise_x_V`、`actual_rate_Sa_s`、Demod 索引和信号路径。
X 色散拟合成功时，`results/response_fit.npz` 在既有 `popt`、`perr` 之外保存完整四参数协方差 `pcov`，
供相关参数的不确定度传播使用；模型、中心斜率 `abs(A)/gamma²` 及原有结果键不变，旧结果缺少该附加键仍可读取。
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
| B03 | 各控制 Welch X 噪声 PSD、可关闭 MAD 高值筛选及逐记录审计 | B02 |
| B04 | RF 灵敏度、洛伦兹频响补偿与结果图 | B03 |

运行目录支持 `NOISE_NPERSEG`、`NOISE_OUTLIER_POLICY`、`SENSITIVITY_BAND_MIN_HZ` 和 `SENSITIVITY_BAND_MAX_HZ` 分析覆盖值，单独保存在 `analysis_state.json`，不改变采集
参数快照或下一次实验默认值。修改筛选策略或评分频带只使 B03 及 B04 失效，不重做校相或响应拟合。阶段记录保存输入与产物指纹、修订和状态；配置、raw 或前置
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
首条记录的频率轴。策略为 `none` 时全部等权平均；`mad_high` 时先按下述规则筛选，再仅对保留记录等权平均 PSD。灵敏度首先按
`sens_raw(f) = sqrt(PSD_X(f)) / (slope_V_per_nT / 1e6)` 计算，单位为 fT/√Hz。
若 X 对 Z RF 场的色散拟合成功，则将拟合半宽 `gamma_nT` 按
`f_larmor_Hz = gamma_nT × LARMOR_PER_NT` 换算为等效频率 HWHM，并按
`sens_corrected(f) = sens_raw(f) × sqrt(1 + (f / f_larmor_Hz)^2)` 补偿洛伦兹型滚降。
线宽来源按运行时 `LINEWIDTH_MODE` 决定：`frequency_sweep` 时 B04 要求存在
`raw/bandwidth_data.npz`，对 R 均值 vs 频率做洛伦兹拟合（复用共享拟合，门槛 R²≥0.85、
γ 相对不确定度≤0.5、中心在窗内、HWHM 小于窗口跨度一半），拟合 HWHM 直接作为
`f_larmor_Hz`，`analysis.yaml` 的 `frequency_response_correction.source` 标为
`bandwidth_lorentzian_fit`，并输出 `results/bandwidth_fit.png/pdf`；拟合不合格即判质量
失败可重跑，不静默回退。带宽摘要记录 `response_component=R`，图的纵轴与图例均为 R。
历史重新分析读取保存的 R 均值；缺失 R、点数不完整或数据非有限时明确拒绝，不用 X 替代。
分析版本已更新，旧 X 带宽分析缓存会失效，重新执行时按现有归档机制保留旧结果。
`dispersive_equivalent` 时即使存在带宽文件也忽略，沿用色散
等效 HWHM（`source=dispersive_gamma`）。报告的中位数使用校正后的谱，在 `[3 Hz, HWHM]` 内取有限频点；没有有效 HWHM 时不补偿，
回退为所有 `≥3 Hz` 的有限频点。`results/sensitivity.npz` 同时保存原始谱、校正谱、校正因子、
`gamma_nT`、`f_larmor_Hz`（实际用于补偿的等效 HWHM）与 `bandwidth_hwhm_Hz`（未测为 NaN）。色散拟合的 HWHM 是幅度域等效线宽；
扫频测线宽模式在本实验内提供真实频率域线宽，不替代独立
`rf-sensitivity-direct-aw-frequency` 扫频实验的频率响应表征。

分析器接受参数 schema v2/v3/v4 的已验证格式，原始格式与参数版本独立：旧平铺数据继续读取，新多控制容器以 `raw_format_version=2`、`control_layout=controls` 标识，各子运行使用既有单控制记录布局。所有子快照成功保存后父容器才提交 `controls_sealed=true`；封存失败或任一控制不完整时禁止分析。先校验输入再写结果。旧格式、
缺失响应点或噪声记录会明确拒绝；旧目录不会删除或改写，本次不兼容其数据内容。
离线分析不读取仪器、不依赖当前波形 CSV 或当前默认参数。

## PSD 异常谱识别与剔除

`NOISE_OUTLIER_POLICY=mad_high` 复用 Scale 实验的保守高侧规则，`none` 保留全部记录。评分频带由 `SENSITIVITY_BAND_MIN_HZ` / `SENSITIVITY_BAND_MAX_HZ` 配置，当前默认值见[实际参数文件](../params/experiments/rf-sensitivity-aw.yaml)，**只用于异常谱评分，不改变 B04 的平坦频带定义**。

对每个控制分别计算每条记录在该频带的中位 ASD：`score = median(sqrt(PSD_record))`。仅在至少 5 条记录、评分中位数为正、所有记录实际带宽完整覆盖该频带且公共轴内至少两个频点时，剔除满足下式的整条记录：

`score > max(3 × median(score), median(score) + 6 × 1.4826 × MAD(score))`

不跨控制比较绝对噪声，不删频率点，不筛低值；小样本、零基线或频带不可用时不强行筛选，记录原因并保留全部数据。保留全部原始时序、逐记录 PSD 矩阵、`included_record_mask`、评分、未剔除平均 PSD；B04 同时保存 `unfiltered_sensitivity_fT_per_sqrtHz`，有剔除时在 PSD 和灵敏度图中显示前后对照。

各子目录及父目录都输出 `noise_record_quality.csv`、`noise_rejection_summary.json`，历史页显示每控制保留/总数和未筛选原因。改变策略或频带只重算 B03/B04，并归档父子受影响结果；不改采集快照、原始数据或下一次默认值。缺策略键的历史数据继续全部平均，可在目录分析覆盖中显式启用筛选。

以上目录是新多控制布局；旧单控制平铺目录及旧 Python 单文件调用仍保留支持。每个控制的原始数组键保持兼容；下文中 `raw/` 和单控制 `results/` 路径均指对应控制的子目录或旧平铺目录。

## GUI 运行 / 历史

实验中心 `/experiments/rf-sensitivity-aw` 提供两个主 Tab。

- **运行**：流程容器顶部保留参数与布局保存、RF 采集预览、独立预检、预检并完整运行、
  取消、进度日志和 ETA；左列为 17 个采集节点，右列为 4 个独立分析节点。
  A13B 位于 A14 之后，为可选节点：线宽模式为扫频测线宽时勾选执行，色散等效时勾选会在执行前被拒绝并提示；
  完整运行按线宽模式自动包含或跳过 A13B。
  基础参数名称常显，展开节点编辑，高级参数单独折叠，多个节点可同时展开。
  共享过程参数（如关闭温控后等待、恢复温控后等待、扫描点采样时长）跨使用节点同步；
  初始化条件只在设置节点展示，基础/高级分类按字段统一保存。
  存活会话的修改标为「草稿未应用」，预览预计需重跑的已完成步骤；应用成功后显式重跑，
  草稿不会改写执行记录，应用失败保留草稿。共同触发只显示一组并同步两套稳定参数键。
  RF 输入线路和 Z RF 相位扫描参数仍在相应节点；A09 的控制列表逐行显示「波形名称｜勾选框｜补偿倍率」，同次可勾选多个，每个控制独立运行与保存。未选控制也可预填系数，取消或改选不会把系数挪给其他控制；波形与补偿作为一组移动基础/高级分类。更改补偿并应用后使 A09 及后继失效，必须显式重新校相和测量。
  分析操作对象默认为「下一次实验」，与采集参数共用同键值；选择具体已完成目录后编辑独立
  覆盖值，执行分析时生效。各目录草稿分别保留，切换对象或主 Tab 后返回可继续编辑。
  保存默认值始终只保存下一次实验参数与统一分类，历史分析覆盖值不写入默认 YAML。
- **历史**：新格式运行分页列表；该次参数快照、采集/分析/校相状态、诊断、实际回读、
  结果图与下载。填回只更新当前 schema 识别的字段并切回运行，不保存默认值或自动采集。
  切换 Tab 保留未保存参数。旧快照缺少补偿映射时，填回使用模型声明的单位补偿，不继承当前默认文件或草稿中的额外补偿。补偿映射保存在运行参数，XY 原始文件额外记录实际 `control_scale_factor`，原始理论源列保持未缩放。旧单选键填回为单项列表；历史缺筛选策略时保持全部平均，除非用户显式启用。响应/噪声数组格式不变，新多控制采用独立子目录，不重写历史原始数据。
  完整采集且校相通过时可重新分析，使用现有非硬件任务，
  完成后刷新摘要和图片；未完成采集或校相失败只展示诊断。
  列表增加独立对比勾选框，右侧可切换「单次详情 / 对比分析」。选择至少两次完成分析、
  校相通过且结果齐备的运行，支持跨页选择；按勾选顺序绘制，每次运行填写一个英文图例，
  留空时使用运行 ID；中文目录使用英文时间标识，无时间前缀的中文旧目录使用英文顺序标识。
  切换主 Tab 保留选择与图例，点击「生成对比」后预览并下载 PNG/PDF。
  修改选择或图例会隐藏旧预览及下载链接，并提示重新生成。无效运行显示不可对比原因，
  缺损结果需通过原「重新分析」操作补齐，不会自动重算。对比任务可取消，任务执行期间
  禁止重复提交及同实验的重新分析、删除；完整双格式导出后才开放下载。
  本实验的每条记录可星标或取消星标，「仅显示星标」在分页前过滤，顺序仍为运行时间倒序；
  星标保存到运行目录内的 `run_metadata.json`，刷新或重启后保留。不存在该文件时默认未星标。
  单次详情可重命名，只编辑名称后缀（1–80 字，支持中文），同时移动完整数据目录。
  优先保留现有目录时间前缀；无前缀的「最优」「常数」等旧目录优先恢复配置 `timestamp`，
  其次从 `started_at` 生成前缀；时间缺失或无效时拒绝改名。非法 Windows 名称和同名冲突均拒绝，
  不覆盖目录。改名不改写采集参数、执行快照和分析产物，因此不会使分析指纹失效。
  「删除本次实验」在确认后永久删除该次参数、`raw/`、`results/` 及星标元数据，应用无法恢复。
  排队/运行中的同类采集、存活分步会话、该目录分析和同实验对比任务会阻止改名或删除。
  改名同步当前详情、对比选择及运行页的历史分析对象和草稿；删除清除这些引用并修正空页。
  星标筛选不改变运行页的分析对象。已生成的多次对比导出与来源清单保持生成时快照；当前
  涉及该记录的对比预览与下载链接失效，需要重新生成。外部保存的旧目录路径需手动更新。
  上述历史管理功能仅在 DirectAW 试点，DirectAW R 暂不开放。

历史列表和摘要分别使用 `GET /api/experiments/rf-sensitivity-aw/runs`、
`GET /api/experiments/rf-sensitivity-aw/runs/{run_id}/summary`，结果访问与重新分析
复用通用接口，浏览历史不会连接仪器。

历史列表支持 `starred_only=true`，列表和摘要提供 `name`（当前目录的名称后缀）与 `starred`。
重命名使用 `POST /api/experiments/rf-sensitivity-aw/runs/{run_id}/rename`，请求 `{"name": "新名称"}`；
星标使用同路径下的 `PUT .../star`，请求 `{"starred": true}`。两者返回更新后的运行记录，
重命名后以新的 `run_id` 访问摘要、分析和文件。删除复用 `DELETE /api/runs/rf-sensitivity-aw/{run_id}`。

对比使用 `POST /api/experiments/rf-sensitivity-aw/compare`，请求为
`{"runs": [{"run_id": "<run-id>", "legend": "<English label>"}, ...]}`，返回现有非硬件 Job。
源运行目录只读；独立产物保存到 `results/rf-sensitivity-aw/comparisons/<job_id>/`：
`full_analysis_comparison.png`、同名 PDF 和记录来源、顺序、图例与回退状态的 `comparison.json`。
图片通过专用的 `GET /api/experiments/rf-sensitivity-aw/comparisons/<job_id>/artifacts/<name>` 下载。
历史列表与摘要中的 `can_compare`、`comparison_block_reason` 提供对比资格和禁用原因。
