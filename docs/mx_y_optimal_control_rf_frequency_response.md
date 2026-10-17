---
title: Mx Y 最优控制 RF 频率响应
type: Mx_Y_Optimal_Control_RF_Frequency_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field: fixed
  Z_magnetic_field: optimal_control
  Time_sequence_2: common_trigger
  X_magnetic_field: fixed
  rf_coil: scan
  Pump_laser_power: fixed
  Probe_laser_power: fixed
  Pump_modulation: fixed
  Time_sequence: fixed
  Temp_Switch: point_gate
  lockin_r: detection
required_devices: [GS200, DG900, DG4000, 锁相放大器]
learned_notes:
  - 最优控制阶段每个频率点完整采集 配置的 Y RF Burst 相位轴对应的 Demod0 R，响应按全部有效相位取中位数。
  - 启用常数对照后，最优控制扫描必须完整结束后才切换 Z 通道为恒定 DC 并扫描同频段。
  - 常数控制阶段 Y RF 相位固定 0°，每个频率点只保留一个被接受的采样点。
  - 常数控制的恒定 Z 电压由电流耦合标定反解，允许超出标定有效范围的全局线性外推。
  - 不做相位拟合、峰值、带宽或 Bloch 拟合。
experiment_id: mx-y-optimal-control-rf-frequency-response
defaults_file: params/experiments/mx-y-optimal-control-rf-frequency-response.yaml
---

# Mx Y 最优控制 RF 频率响应

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。本实验只有逐点频率×相位扫描与可选常数对照；每个被接受点或坏点重采各自恢复温控后写盘，不把频率行或两种Z控制组成温控关闭组，也没有OFF/ON配对。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-y-optimal-control-rf-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

实验在 Z 向闭环最优控制波形保持运行时，扫描 Y RF 频率与 Burst 相位，记录 Demod0 R。
可选地在最优控制扫描结束后，把 Z 通道切换为恒定 DC，再扫描一次 Y RF 频率作为对照。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 采集流程

单次运行按顺序完成两个阶段：

1. **最优控制阶段**：Z 通道输出闭环最优控制任意波，逐频率、逐 Burst 相位采集 Demod0 R。
   原始数据写入 `raw/optimal_control_phase_frequency_scan.npz`，数组形状为
   `(频率点数, 相位点数)`。
2. **常数控制阶段（可选）**：仅当 `COMPARISON_ENABLED` 为真时执行。先关闭 Z 通道输出与
   Burst，改用 `setup_dc()` 输出恒定电压，回读确认波形形状为 `DC` 且输出为 ON；随后逐频率
   采集单个 `0°` 相位点，写入 `raw/constant_control_frequency_scan.npz`。
   该阶段结束后把 Z 通道恢复为最优控制任意波并重新开启输出，再进入安全收尾。

两阶段共用同一套共同触发、Pump、温控、锁相放大器 与光路配置，只有 Z 通道输出形态和 Y RF 相位不同。

相位扫描不要求至少 8 个点；按配置的起点、终点和步进采集，包含首尾点。起点仍须小于终点，
步进须为正且整除扫描范围，例如 0–180°、步进 90° 对应 3 个相位点。每个相位点的 锁相放大器
时间序列仍要求至少 8 个有效样本，用于计算 R 均值和标准差。

## 常数 Z 电压反解

启用对照时，`build_constant_control_plan()` 按以下步骤求恒定 Z 电压：

1. 读取 `data/Mx_Z_Current_Coupling_Calibration/<运行>/results/analysis.yaml`，
   校验 `experiment_id` 为 `mx-z-current-coupling-calibration` 且 `success: true`。
2. 用 `f0 = K_Z_Hz_per_A · I + f_0_at_zero_current_hz` 反解目标线圈电流
   `I = (f_target − intercept) / slope`。
3. 取标定 `curves` 中全部 `fit.success: true` 的点（不分正反扫描方向），拟合
   `current_a = gain_a_per_v · z_bias_v + intercept_a`。
4. 反解恒定电压 `V = (I − intercept_a) / gain_a_per_v`，并调用
   `validate_safety_limit("Z_magnetic_field", V)`。
5. 若 `V` 落在标定电压支撑范围之外，置 `extrapolated: true` 并在结果中保留警告元数据。

运行配置记录标定运行名、`analysis.yaml` 的 SHA-256、拟合斜率与截距、目标 Larmor 频率、
目标线圈电流、目标恒定 Z 电压、是否外推以及标定有效点范围。

对照反解方案在 A01 冻结为本轮快照，A05 始终使用该快照；标定来源文件随后变化不会混入本轮，更新方案须显式重跑 A01。闭环冻结波形的 SHA 变化则拒绝继续，并要求重新执行 A01 来源预检。

## 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

`analysis.py` 负责编排，`analysis_steps.py` 只读取 `raw/` 中两个 NPZ 与 `experiment_config.yaml`，输出到 `results/`：

- `optimal_control_median_response.npz`：`frequency_hz`、`response_median_v`、
  `valid_phase_count`。
- `r_phase_frequency.png`：相位-频率诊断热图（x=相位，y=频率，color=Demod0 R）。
- `rf_frequency_response_comparison.png`：最优控制相位中位数曲线与常数控制曲线叠加。
  只画这两条曲线，不画差值、比值或归一化增益。
- `analysis.yaml` / `analysis.json`：响应定义、标定元数据、外推标记与有效点计数。

所有图使用 `lab_workflows.plotting` 的 `paper` 样式，图中文字为英文。

## 专属 GUI

原架构 Tab 已从 GUI 撤下，原图保留在[历史归档](old/architecture/mx-y-optimal-control-rf-frequency-response.html)。

实验 ID `mx-y-optimal-control-rf-frequency-response` 保持专属「运行 / 历史」双 Tab。运行区顶部是可执行流程图，保存参数与布局、派生预览、预检、完整运行、取消和任务日志都在同一容器；下方继续展示相位-频率热图、常数对照曲线、有效点与标定外推信息。历史列表和详情保留专属科学摘要、标定信息、结果图、填回与重新分析。

采集与分析各列按实际模块执行，完整运行与分步调用同一套职责函数：

| 模块 | 前置 | 实际职责与门槛 |
| --- | --- | --- |
| A01 预检与来源 | 无 | 参数/映射/锁相能力、闭环冻结文件版本和波形一致性；启用对照时标定身份、成功状态、电压反解与安全限值 |
| A02 连接与目录 | A01 | 复用设备连接，创建运行目录，保存闭环源文件、来源清单和实际下发波形 |
| A03 工作点 | A02 | 严格回读参考时钟，设置主场/光路/TEC目标/Pump、共同触发、Z任意波与R解调 |
| A04 最优控制扫描 | A03 | 按频率×Burst相位全扫；每点至少8个有效样本、R标准差合格，否则保存拒绝点并限定重采，耗尽阻断后继 |
| A05 常数对照 | A04 | 关闭对照时跳过；启用时确认Z为DC且ON，以0°扫描同频率轴，沿用R点质量门槛 |
| A06 本轮封存 | A05 | 恢复Z最优控制，再安全收尾；成功后才能标记完成与允许分析，A03及后继变为需重跑 |
| A12 安全结束会话 | 无 | 无前置可执行；空闲时结束，执行中取消，EOF与后台关闭同样先收尾再释放锁 |
| B01 分析资格 | 无 | 身份、独立raw格式、已封存状态、必需矩阵字段/形状/扫描轴；对照启用时要求其原始数据齐备 |
| B02 相位中位数 | B01 | 每频率至少一个有限相位结果，保存有效相位计数；无有效相位时保留诊断并拒绝B03 |
| B03 图与报告 | B02 | 读取B02数值缓存生成原科学图和报告；无相位、峰值、带宽或Bloch拟合 |

参数来自实际强类型schema，全部可见键都有节点编辑入口。基础名称常显，高级参数在节点内折叠；`SYSTEM_SETTLE_S`、采样时长、频率轴和R质量门槛在A04/A05共享同一值和分类。初始化条件仅在A03编辑，来源/对照开关在A01；对照关闭时隐藏两个对照条件输入，保留值。分类移动不改数值，保存时确认完整布局回读。

存活会话等待下一步时保持输出并占用全局硬件锁。每轮A06封存保留TEC通信以便重跑，A12或自动结束时才断开。修改只是草稿，界面预览失效范围；应用只更新参数与真实失效状态，不执行硬件。显式重跑会创建新目录，保留原目录和参数快照；收尾采用最近实际执行的配置，不把未重跑的新草稿写入硬件。主Tab切换保留会话和分析操作对象，刷新恢复存活会话；后台重启不会从磁盘假装恢复硬件。

分析列默认针对下一次实验，也可选择已封存历史目录；本实验没有可调分析覆盖值，`options={}`，不会新增科学选项。重新分析只更新该目录的分析状态与产物，不覆盖历史采集参数。输入配置/raw与产物采用SHA-256内容指纹；变更使前置失效。重算先校验原始资格，再归档受影响结果，单独重画B03不移动B02数值缓存。

新运行显式记录 `raw_format_version: 1`，与参数schema独立。无raw标记的历史只按已验证的参数schema2/3及新版矩阵契约兼容；schema1旧格式不纳入专属列表。合法新运行尚未分析时仍可在列表查看和选择分析。`run_format.format_block_reason()` / `analysis_block_reason()`由列表、文件和分析入口共用，其他实验身份或未知raw版本拒绝。

Z波形只来自所选闭环冻结文件；沿用公共加载器对历史v1/v2/v3与独立重载验证v4的规则，不改写旧冻结文件。历史外部键 `Z_AW_OUTPUT_VPP` / `Z_AW_OUTPUT_OFFSET` 仍可读，但本实验实际未使用这两个继承值，故不再提供无效编辑；真实幅度、偏置、来源运行与SHA在运行配置和专属摘要保留。

可编辑源图位于[实验包原图](../lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/workflow.excalidraw)，运行页可下载。分组、依赖与质量门槛随实际清单同步；不恢复旧架构Tab。

### 任务进度与动态 ETA

- 总采集点数为 `频率点数 × 相位点数`，启用常数对照时再加 `频率点数`。
- 每完成一个被接受的采集点（含坏点重采耗时）上报一次进度：采集阶段占用总进度的
  `0～90%`，自动分析从 `90%` 开始，任务结束为 `100%`。
- ETA 使用全部已完成点的累计平均耗时估算剩余采集时间；首个点完成前不显示 ETA，
  分析阶段只显示“分析中”。
- 子进程通过 `report_runtime_progress()` 以 JSONL 行协议上报，GUI 后端保存并在 SSE、
  `GET /api/jobs`、`GET /api/jobs/{id}` 中返回 `estimated_remaining_seconds`。
- 专属页任务状态保存在页面层，重新进入页面时自动恢复最新任务、百分比、日志与 ETA。

## 安全注意

- 恒定 Z 电压在设置前必须通过 `validate_safety_limit("Z_magnetic_field", V)`。
- Z 通道切换为 DC 后必须回读确认波形形状与输出状态。
- 结束保持Z最优控制、共同触发、主场、光功率和Pump；关闭X/Y RF横向场、恢复温控，断开TEC通信。常数扫描结束后恢复 Z 最优控制任意波；恢复失败会计入 `failure_reason` 并把完成状态改为
  `failed`，不会静默忽略。
