---
title: 静磁场灵敏度测量
type: experiment_type
description: Z 直流逐点色散和多量程 Y 噪声采集；运行页采用节点编辑、显式勾选和依赖检查的两列交互流程图，完整与分步共用测量及分析步骤。时序 2 始终保持当前状态，最后一点后由安全恢复直接恢复温控；历史分析仅独立覆盖 Z 场换算系数。
keywords: [dispersion, sensitivity, PSD, DC step scan, lock-in]
version: 3
execution_mode: typed_workflow
scan_mode: point_by_point
experiment_id: static-sensitivity
defaults_file: params/experiments/static-sensitivity.yaml
mapping_keys:
  Z_magnetic_field:
    role: scan
    description: Z 小线圈的纯 DC 偏置，逐点扫描
  main_magnetic_field:
    role: fixed
  X_magnetic_field:
    role: fixed
    description: 固定 X DC 补偿，零值时关闭
  Y_magnetic_field:
    role: fixed
    description: 固定 Y DC 补偿，零值时关闭
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
    description: 100 MHz 载波，Gated Burst、外部门控
  Time_sequence:
    role: fixed
    description: Pump 脉冲门控
  Time_sequence_2:
    role: fixed
    description: 本实验不用，保持当前状态
  Temp_Switch:
    role: fixed
    description: 测量时物理输出 OFF，恢复 5 V DC + ON
  temperature:
    role: fixed
  lockin_r:
    role: detection
    description: 同一可配置解调器的连续 X/Y 色散 DAQ、逐样本 R 和 Y 噪声 DAQ
required_devices: [GS200, DG4000, DG900, TEC103, 锁相放大器]
learned_notes:
  - 温控物理 OFF 是本实验经确认的专属例外，不改变共享步骤默认的 0 V DC + ON 关闭策略。
  - setup_dc 会隐式打开输出，每点结束必须显式关闭 Z；分组恢复温控时 Z 保持关闭。
  - 对称轴均值的浮点残余须规范到精确零，避免 LM 数值差分把拟合中心锁住；色散模型和质量阈值不变。
---

# 静磁场灵敏度测量

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

色散使用 `hf2_demod_rate` 请求率、`dispersion_duration_s` 时长和 `hf2_demod_idx` 解调器；静磁噪声使用 `hf2_noise_rate` 请求率、`noise_duration` 时长和 `hf2_demod_idx` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数唯一来源为[版本化参数文件](../params/experiments/static-sensitivity.yaml)，GUI“保存参数与布局”更新该文件和本实验布局。单次运行以自己的 `experiment_config.yaml` 为准，不用当前默认值解释历史采集。

正式实现位于 `lab_workflows/experiment_modules/static_sensitivity/`。采集入口为 `experiments/Static_Magnetic_Field_Sensitivity.py`，只保存原始数据；`experiments/Static_Magnetic_Field_Sensitivity_plot.py` 调用离线分析器。GUI 自动分析、历史重新分析和分阶段分析共用同一套科学算法。`workflow.py`、`session.py` 和 `analysis.py` 仅负责编排，设备准备、安全恢复、激励、校相、采集和分析算法分别位于 `preparation.py`、`excitation.py`、`phase.py`、`acquisition.py` 和 `analysis_steps.py`。`stages.py` 是执行顺序、依赖、参数展示和失效范围的唯一来源。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 采集方法

本实验与 [XY 已知可控噪声实验](noise_spectrum_xy_ctrl_known_noise.md) 复用 `lab_workflows.steps.dispersion` 的单点连续采集步骤，以采样率和采集时间控制每个稳定后的直流点。扫描电压就是请求的 DC 电平，不通过 SYNC、时间轴或斜坡对称度重建；不区分上升/下降段。

保留静磁场原有的固定 X/Y DC 补偿、主场、Pump 调制和光功率条件，不施加 XY 交流控制，也不引入参考实验的噪声注入和 K_Z 真值链。

### 准备与校相

1. 读取当前设备库、物理映射和安全限值，按资源复用连接；参考时钟按设备库设置并严格回读。
2. Z 归零关闭；时序 2 保持当前状态，不修改 SYNC、Burst 或调制；固定 X/Y DC 补偿非零时开启、零值关闭。
3. 设置主磁场、Pump/Probe 光功率；温控恢复后初始化 TEC 温度控制，按现有温度容差、连续读数和超时判据等待。TEC 不可用时沿用外部温控软件模式并明确告警。
4. Pump 载波使用 100 MHz 正弦、Gated Burst、外部触发，脉冲门控频率、占空比、电平和延迟来自运行参数。保留最短门控边沿。
5. 锁相放大器输入固定为高阻、DC 耦合，按参数设置量程、解调器和滤波，振荡器匹配 Pump 调制频率；校相、色散读取、噪声和恢复均使用同一个 `hf2_demod_idx`。
6. 关闭温控物理输出并等待磁场消退，在 Z 输出关闭时校相；随后恢复温控并固定等待。

### 直流色散扫描

1. 从扫描起点到终点生成包含端点的等间距电压轴。
2. 每组开始关闭温控物理输出，等待 `SYSTEM_SETTLE_S`。
3. 每个电压点先做安全校验，设置 Z 纯 DC 并开启输出，等待 `SYSTEM_SETTLE_S`。
4. 锁相放大器 回读实际采样率 `f_actual`，按 `N=ceil(f_actual×dispersion_duration_s)` 计算样本数，至少 2 个。DAQ 使用连续、单帧、Exact（grid mode 4）模式，同时订阅同一解调器 X/Y，逐样本计算 `R=hypot(X,Y)`；X/Y/R 分别取中位数作为点值。Exact 的记录时长为 `N/f_actual`，相关定义见 [LabOne 官方说明](https://docs.zhinst.com/labone_api_user_manual/modules/daq/index.html)。
5. 完整波形与时间信息先原子保存到 `raw/dispersion_steps/point_<序号>.npz`，关闭 Z 输出，再原子更新 `dispersion_scan.npz` 的点值与完成掩码。
6. 每完成 `dispersion_temp_switch_interval_points` 点，恢复温控并按 `dispersion_temp_switch_on_settle_s` 固定等待。组间不重新运行 TEC 温稳判据；最后一点后不额外恢复等待，先恢复温控并保存本组数据，再进入安全收尾；间隔点数为 0 时整段扫描保持温控关闭。
7. 扫描结束 Z 归零关闭，先恢复温控并保存本组数据，再进入安全收尾，不额外等待。缺失信号、长度错误、非有限值、时间戳不连续、丢样或超时终止扫描；保存已完成点及故障点能够读出的波形。DAQ 开启丢样报错，等待期间检查取消，超时为实际记录时长加 5 秒。取消或异常均清理 DAQ 并安全收尾，不自动重采或插值。

### 多量程噪声采集

噪声测量保持 **Z=0、输出 OFF**，不把拟合中心 V0 应用到 Z 通道。锁相放大器 切换到噪声速率和时间常数；按配置切换 GS200 电流量程、等待并记录实际量程/电流。每次短暂关闭温控、等待原有磁场消退时间，连续 解调时序采集 Y，逐段保存，再恢复温控并按原有噪声节奏等待。

DAQ 等待支持协作式取消，退出时清理 DAQ 模块。采集工作流不计算 PSD；离线使用记录的实际采样率执行 Welch、等权平均和量程比较。

## 参数

上下限的外部键 `ramp_low` / `ramp_high` 为已发布稳定键，现在只表示**直流扫描起点/终点**。参数版本为 3；旧读数次数、读数间隔、RAMP 频率/对称度、旧触发采集时长、AUX 触发及旧时序幅度参数已移除，不转换旧配置。

| 参数 | 含义与约束 |
|---|---|
| `ramp_low` / `ramp_high` | DC 起止电压，V；起点小于终点，两端均满足 Z 安全范围 |
| `dispersion_steps` | 扫描点数，整数，至少 7 |
| `SYSTEM_SETTLE_S` | 每点设置 Z 后的稳定等待，非负秒数 |
| `hf2_demod_rate` | 色散与校相共用的请求采样率，有限正数，Sa/s；模块按请求值选择，分析使用波形最终实际速率 |
| `dispersion_duration_s` | 每点连续采集时间，有限正数，s；向上取整后至少 2 个样本 |
| `dispersion_temp_switch_interval_points` | 分组点数，非负整数；0 表示整段扫描保持温控关闭，仅在收尾恢复 |
| `dispersion_temp_switch_on_settle_s` | 每组后及校相后恢复温控等待，非负秒数；不作用于色散扫描结束后 |
| `SYSTEM_SETTLE_S` | 每组及校相前关闭温控等待，非负秒数 |
| `z_v_to_nt` | Z 电压到磁场换算，有限正数，nT/V |
| `hf2_demod_idx` | 校相、色散、噪声及恢复一致使用的解调器索引 |
| `hf2_ac_coupling` / `hf2_50_ohm` | 历史兼容键，统一固定为关闭；使用 DC 耦合、高阻，GUI 隐藏 |
| `temp_switch` | 恢复电平，须为 5 V；关闭动作仍为物理 OFF |
| `gs200_current_ranges` | 噪声量程列表，A；量程及余量必须容纳主场电流 |

其他光功率、主场、温度、门控和噪声字段保持原语义，实际值查看默认文件或运行快照。

## 数据与离线分析

```text
data/Static_Magnetic_Field_Sensitivity/<时间戳>_<tag>/
  experiment_config.yaml
  acquisition_state.json
  analysis_state.json
  raw/
    dispersion_scan.npz
    dispersion_steps/point_<序号>.npz
    noise_raw/gs200_range_<量程标签>/noise_<序号>.npy
  results/
    dispersion_fit.npz
    noise_psd.npz
    gs200_current_range_sensitivity.npz
    gs200_current_range_summary.yaml
    full_analysiswithoutpump.png
    full_analysiswithoutpump.pdf
    analysis.yaml
    analysis.json
    run_summary.csv
```

色散原始格式为 3。`dispersion_scan.npz` 保存 `offsets_v`、`x/y/r_median`、`sample_counts`、`completed_mask`、请求/实际速率与时长，以及每点主机起止时间；未完成点的点值为 NaN。完整波形逐点保存到 `dispersion_steps/point_0000.npz` 等文件：含 `x/y/r_samples`、X/Y 设备原始时间戳 `x/y_timestamp_ticks`、对齐时间戳 `timestamp_ticks`、相对时间 `time_s`、时钟基准、采集配置、预期/读出样本数、`completed` 和 `error`。故障点只记录能够读取的数据，不伪造缺失时间戳；设备时间与主机时间分开记录。

运行配置保存全部参数、设备/映射修订及快照、安全限值、时钟、校相、实际 锁相放大器 配置和采样率、实际 GS200 量程、电流、逐段完成计数以及安全收尾结果。PSD 汇总前中断时，可从已保存的噪声波形离线分析。分析产物（包括 CSV）只写本运行的 `results/`，不追加旧数据根目录的全局 CSV。

分析只接受明确的运行目录，不连接仪器。使用全部已完成且 Y 中位值有限的色散点拟合，不再提供起始/终止端点剔除参数；有效点至少 7 个。整条线形采用共享四参数模型：

`Y(v) = C + A (v−V0) / ((v−V0)² + gamma²)`。

扣除基线后的中心斜率为 `|A|/gamma²`，经 `z_v_to_nt` 转换成 V/fT；半高宽按既有 Larmor 常数转换到 Hz。不使用中心段线性拟合。对称轴均值的浮点残余规范为精确零初值，避免 LM 差分锁住中心；模型、质量阈值和增益定义均未改变。

灵敏度仍为 `sqrt(PSD_Y)/|dY/dB|`，并使用既有有限线宽响应校正和平坦频段统计。因为噪声保持 Z 输出关闭，而换算斜率取拟合中心，当 V0 明显偏离零时需复核中心增益是否代表实际噪声工作点；本次不自动对准工作点。

质量指标及拒绝原因写入 `dispersion_fit.npz` 和摘要。质量未通过时 B02 标记 `quality_failed`，保留拟合诊断和拒绝原因并拒绝 B04；拟合参数、中心斜率或噪声平坦段不可用也报告分析失败，不输出有效灵敏度。图展示逐点 X/Y/R 和完整线形拟合，使用[科研绘图规范](plotting_style.md)的 `paper` 配置，英文标注，并保留历史预览使用的 PNG 名称。

## GUI 与旧数据边界

保留“运行 / 历史”两个主 Tab。运行页是采集、分析两列交互流程图，顶部集中保存参数与布局、预检、完整运行、取消、任务状态和日志；结果预览放在流程之后。全部可见参数在节点内编辑，基础名称常显，高级参数折叠，允许同时展开多个节点。初始化条件仅在设置节点展示；校相与色散共用温控开关等待，修改值和基础／高级分类会同步到两处。切换主 Tab 保留节点选择、参数与各目录的分析草稿。

| 节点 | 步骤 | 前置 |
|---|---|---|
| A01 | 启动预检 | 无 |
| A02 | 设备连接与输入初始化 | A01 |
| A03 | 参考时钟同步 | A02 |
| A04 | 初始磁场、光功率与温控设置 | A03 |
| A06 | Pump 载波与脉冲门控 | A04 |
| A07 | 锁相色散配置 | A06 |
| A08 | Bell-Bloom 校相 | A07 |
| A09 | Z 直流逐点色散扫描 | A08 |
| A10 | 噪声模式准备 | A09 |
| A11 | GS200 多量程 Y 噪声采集 | A10 |
| A12 | 恢复色散配置、安全恢复并结束本轮 | A11 |
| A13 | 安全结束会话 | 无 |
| B01 | 加载与校验原始数据及快照 | 无 |
| B02 | 色散拟合、增益与线宽 | B01 |
| B03 | 各量程 Welch PSD 与平均 | B01 |
| B04 | 灵敏度、量程比较、绘图和摘要 | B02、B03 |

分步操作：创建会话后显式勾选步骤及尚未有效完成的前置，点击“运行所选 / 重跑”。缺少前置时拒绝执行，不补跑。编辑时显示草稿及预计受影响步骤，只有“应用参数到会话”成功后才使记录失效；应用失败保留草稿，重跑仍须显式选择。单独预检不改变其他阶段状态。刷新页面会恢复后台仍存活的会话；后台重启后不从磁盘记录恢复硬件连接。

等待下一步时保持设备会话和输出，并持续占用全局硬件锁。A12 结束本轮、恢复色散模式且使 A10 噪声准备失效；继续采噪声需重新选择 A10、A11。再次采集核对实际锁相模式及有效前置。空闲时使用“结束会话”或无前置的 A13；执行中使用任务取消，异常、取消、通信 EOF 和后台关闭均自动安全恢复，原始故障与收尾故障分别保留。

首次色散及随后噪声使用同一轮目录。重扫、重采或在已结束轮次继续采集会建立新目录，重名追加序号。仅重采噪声且色散前置仍有效时，复制色散聚合文件、逐点完整波形与相关快照，并记录 `dispersion_source_run_id`；改变色散前置条件后必须重新测量。旧原始数据、采集参数及已结束轮次不被后续收尾覆盖。

分析列可操作“下一次实验（自动分析设置）”或具体运行目录。下一次实验与采集共享同键设置；具体历史目录仅支持独立覆盖有限正数 `z_v_to_nt`，执行所选分析时生效，存入该目录的 `analysis_state.json`。修改只使 B02、B04 失效，B03 保持有效；不修改采集参数快照、默认参数或下一次实验草稿。历史节点只展示这一后端支持的覆盖字段；即使正在编辑历史分析，顶部保存默认值仍只保存下一次实验参数和统一布局。

分阶段记录包含配置、实际输入文件及产物指纹；原始文件、快照或产物改变后不能复用过期前置。重算归档受影响结果到 `results/before_<时间>/`，不移动仍有效的前置。正在写入的目录拒绝分析；分阶段分析和历史“重新分析”共用目录互斥。已有版本 3 数据无阶段记录时从 B01 重建，更早格式继续仅查看已有结果。可编辑原图为[静磁场灵敏度.excalidraw](../lab_workflows/experiment_modules/static_sensitivity/静磁场灵敏度.excalidraw)，运行页提供下载；静态架构展示已撤下并归档。

旧格式运行目录不迁移、不改原始文件；现有历史查看功能保留已有参数、图和结果，但旧格式不能填回或用新分析器重新分析。本次不增加旧配置或历史运行转换。没有运行状态的历史显示 `unknown`，不推定为完成；重新分析 API 在排队前拒绝旧格式。旧 `params/static_sensitivity.yaml` 仅留档，不再读取；旧独立参数/启动层已移除，`LAB_STATIC_*` 环境变量与旧命令行参数会明确报错。

原架构 Tab 已撤下，[单张总览](old/architecture/static-sensitivity.html)作为历史图保留，记录撤下时的 DC 点采集、中位数、完整波形落盘、分组温控和无 Z→锁相放大器 SYNC 触发链路。归档图中的 DAQ 采集步骤复用公共选择器，高速 MFLI 使用单段 Scope，每个 DC 点独立保存；这些标记仅描述撤下时的工作区版本，源码未冻结，不用于保证后续实现一致。

独立的 `Static_Magnetic_Field_Sensitivity_Optimize.py` 和 `_Optimize_Local.py` 不在此次采集替换范围。全局优化原有 2×2 汇总面板继续分别展示灵敏度、HWHM、斜率和 R²，以颜色及圆点/叉号区分有效和拒绝点；不混合单位、不补零。它们同样受共享色散拟合的零初值数值修正影响，但采集和选择逻辑不变。

## 接线与安全结束

设备 ID、端点和时钟以[设备库](../params/devices.yaml)及[映射](../params/mapping.yaml)为准。Pump 门控脉冲接同机载波外部门控输入；保持既有 AOM 保护与隔直措施。Z 输出接 Z 小线圈；本实验不需要 Z SYNC→锁相放大器 AUX 触发线，时序 2 的现有波形、输出、SYNC、Burst 和调制均不修改。

所有输出设置前校验安全限值。温控关闭采用物理 OFF 是用户确认的静磁场专属例外，不改变全局配置和共享步骤的关闭规范。

正常、异常、取消、通信 EOF、后台关闭与 Ctrl+C 均使用共享安全收尾：Z 归零关闭并关闭 SYNC，时序 2 保持当前状态，温控恢复 **5 V DC + ON**，锁相放大器恢复最后实际配置的色散参数与校准相位；A12 保持连接，A13 或会话结束只断开 TEC。主磁场、光功率、Pump 载波/门控与固定 X/Y DC 补偿按原策略保留。连接阶段失败释放已连接会话；收尾失败仍尝试其他动作并记为失败，不以断开通信替代关输出。

## 命令行入口

在仓库根目录运行采集（会连接真实仪器，不是测试）：

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Static_Magnetic_Field_Sensitivity.py
```

只做离线分析时必须明确指定新格式运行目录，不自动寻找最新目录；也可通过 `LAB_RUN_DIR` 指定：

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Static_Magnetic_Field_Sensitivity_plot.py "data\Static_Magnetic_Field_Sensitivity\<实际运行目录>"
```

无硬件回归入口：

```powershell
.\agent_exp_env\Scripts\python.exe -m unittest lab_workflows.experiment_modules.static_sensitivity.test_dc_scan
.\agent_exp_env\Scripts\python.exe -m pytest lab_workflows/experiment_modules/static_sensitivity/test_stages.py lab_workflows/experiment_modules/static_sensitivity/test_analysis_stages.py GUI/backend/test_static_sensitivity.py
```

## 架构图交付记录（历史归档）

2026-10-14 从 GUI 撤下原图，以下为既有交付记录；归档保持原文件与验收附件的内容不变。

- 实验：`static-sensitivity`；类型：`architecture`；仅更新单张总览，无子图。
- Canonical artifact：[`static-sensitivity.html`](old/architecture/static-sensitivity.html) 与同名 JSON；工作区版本，源码未冻结，无 pinned revision，不提供固定源码链接，不自动提交。
- `validate` / `deliver`：showcase 9/9，0 error，0 warning；浏览器 `visual-check`：passed，覆盖四个桌面视口（1440×900、1600×1000、1920×1080、2048×1320）。
- 感知复核：passed，实际检查两端视口的明暗截图；主路径清晰，无交叉、遮挡或裁剪，大桌面纵向布局平衡。本轮感知修正轮次：0。
- Specification SHA-256：`38185ea7e2b79adc36f1d5007dacac36f3dd7ebb890ae86a02386564c0adaa21`，7810 bytes。
- HTML SHA-256：`83322ec4f734e6627a1608aafe923de7f6c5e88b8967d8a3322f3f49a9aa6a08`，815938 bytes。
- 浏览器绑定回执和截图位于同目录 `static-sensitivity.visual-check.*`；自动回执的 `visualReview: pending` 不替代上述实际感知复核。
- 两项实验的最终图交付和无硬件验证汇总见[本次交付验证记录](dispersion_daq_validation.json)。

未执行真实采集；实际稳定时间、温漂接缝、拟合质量和重复性需由实验者验证，不预先宣称灵敏度数值提升。
