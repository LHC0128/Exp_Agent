---
title: 探测链路频率响应标定
type: calibration
scan_mode: point_by_point
experiment_id: detection-chain-frequency-response
defaults_file: params/experiments/detection-chain-frequency-response.yaml
mapping_keys:
  Probe_AOM_Carrier:
    role: control
  Probe_AOM_AM:
    role: scan
  lockin_r:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  temperature:
    role: fixed
  Temp_Switch:
    role: control
required_devices:
  - DG4000 (dg4e261300744): Probe AOM 载波与 AM 信号
  - DG900 (dg9q280100002): Pump/Probe 光功率
  - DG900 (dg9q271200104): 每点采集时控制温控开关
  - HF2 / MFLI（按全系统选择）: 解调与 解调时序采集
optional_devices:
  - TEC103 (tec103_com3): 可连接时仅设置 UI 目标温度
learned_notes:
  - Pump 为零用于无自旋极化对照；两种 Pump 条件下都需在采集时关闭温控
  - 双边带解调会部分抵消高频衰减
  - 测量精度依赖于 AM 调制幅度稳定性
---

# 探测链路频率响应标定

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

探测链路扫频使用 `HF2_DAQ_RATE_SA_S` 请求率、`HF2_DAQ_DURATION_S` 时长和 `HF2_DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 实验概述

设置 Pump/Probe 光功率后扫描 Probe 光 AOM 的 AM 调制频率，固定调制幅度，锁相放大器 Demod0 解调到基带并测量 Y 信号功率谱密度（PSD）峰值幅度。锁相放大器 解调和采样设置对照“XY 控制测量噪声谱（正弦）”，所有正式实验统一关闭信号输入 AC 耦合和 50 Ω 终端，以检查该实验中不可控噪声随频率下降的来源。AM 频率等于 锁相放大器 参考频率时落在零基带，不参与 PSD 响应曲线和归一化。无论 Pump 光功率多少，每点采集前都关闭温控以避开加热信号，采集后恢复。TEC103 可连接时由程序仅设置目标温度；串口被外部软件占用时由外部软件维持温度。

用于标定 **BPD + 锁相放大器 联合探测链路**的频率响应，验证噪声谱实验中观测到的高频下降（-0.010% / kHz）的来源。

### 关键特点

- **两种 Pump 条件**：0 V 用于关闭 Pump 的对照，非零用于带 Pump 测量；两者均在每点采集前关闭温控、采集后恢复，以抑制加热信号干扰
- **固定 AM 幅度**：排除调制深度变化的影响
- **双边带解调**：锁相放大器 同时接收上下边带，部分抵消高频衰减
- **基带谱峰提取**：非零基带的 AM 频率对应谱峰；零基带点保留原始波形，但不计算 Welch 峰值

## 物理原理

### 信号链路

```
Probe AOM: 100 MHz 载波 + AM 调制（50-130 kHz）
    ↓
BPD 探测（频率响应 ~1 MHz 带宽）
    ↓
锁相放大器 解调（90 kHz 参考）→ 基带信号（|AM_freq - 90kHz|）
    ↓
解调时序采集 + Welch PSD → 峰值幅度
```

### 频率映射

| AM 调制频率 | 基带频率 | 对应噪声谱 |
|-----------|---------|----------|
| 75 kHz    | 15 kHz  | 下边带   |
| 90 kHz    | 0 kHz   | 直流，响应曲线中排除 |
| 105 kHz   | 15 kHz  | 上边带   |
| 115 kHz   | 25 kHz  | 上边带   |

在噪声谱实验中，基带 15 kHz 的噪声同时来自 75 kHz（下边带）和 105 kHz（上边带），两者平均后部分抵消了 BPD 的高频衰减。

## 实验参数

参数详见 [默认配置文件](../params/experiments/detection-chain-frequency-response.yaml)。

### 扫描参数

- **AM 频率范围**：`am_freq_start_hz` ~ `am_freq_stop_hz`（推荐 50-130 kHz）
- **扫描点数**：`am_freq_points`（推荐 41 点，约 2 kHz 步进）
- **稳定等待**：`am_settle_time_s`（每点稳定时间）

### Probe AOM 设置

- **载波频率**：`probe_aom_carrier_freq_hz`（100 MHz，固定）
- **载波幅度**：`probe_aom_carrier_amplitude_vpp`（CH1 输出，允许 0–0.05 Vpp，受 `Probe_AOM_Carrier` 安全限值约束）
- **AM 幅度**：`probe_aom_am_amplitude_vpp`（CH2 输出，固定）

### 锁相放大器 解调参数

- **解调器索引**：`HF2_DEMOD_IDX`，应与 `lockin_r` 映射一致；当前使用 Demod0，与两项 XY 控制噪声谱实验一致
- **探测输入**：锁相放大器 信号输入 0 关闭 AC 耦合（DC 耦合），使用单端、高阻，量程使用 `HF2_SIGNAL_RANGE_V`；XY 正弦控制噪声谱实验同样使用 DC 耦合与高阻
- **解调频率**：`HF2_OSC_FREQ_HZ`（手动设置振荡器 0 的参考频率，用于把 AM 扫频映射到基带）
- **采集时长**：`hf2_daq_duration_s`（每点采集时间）
- **采集解调时间常数**：`HF2_DAQ_TC_S` 直接设置 Demod0 低通；`HF2_DEMOD_ORDER` 设置滤波阶数，`HF2_DAQ_RATE_SA_S` 设置解调输出速率，采样使用 锁相放大器 回读的实际速率。这些参数与 XY 正弦控制噪声谱采集阶段对齐。
- 历史键 `HF2_DEMOD_TC_S` 仍可读取，但本实验不做相位校准，采集时不使用该值。Demod0 相位沿用 锁相放大器 当前相位，并记录在单次运行配置中。

### 固定参数

- **Pump 光功率**：`FIXED_PARAMS.Pump_laser_power`，经 DG900 自动设置；无论取值如何，每点采集期间都关闭温控
- **Probe 光功率**：`FIXED_PARAMS.Probe_laser_power`，经 DG900 自动设置
- **温度**：`FIXED_PARAMS.temperature`；仅在 TEC103 可连接时仅设置目标温度，外部软件占用串口时不应用该值也不读取温度
- **温控门控等待**：`SYSTEM_SETTLE_S`、`TEMP_SWITCH_ON_LAG_S`，分别用于每点采集前关闭温控后的等待、采集后恢复温控的等待

## 接线与准备

### 信号发生器接线（DG4000, dg4e261300744）

- **CH1 (Probe_AOM_Carrier)**：
  - 输出：100 MHz 正弦波
  - 连接：Probe AOM 射频输入
  - 设置：启用外部 AM（`AM Source = EXT`）

- **CH2 (Probe_AOM_AM)**：
  - 输出：可变频率正弦波（50-130 kHz）
  - 连接：通过背板连接到 CH1 的外部 AM 输入
  - 设置：固定幅度

### 锁相放大器

- **输入**：BPD 信号接 锁相放大器 信号输入 0，本实验配置为 DC 耦合、单端、高阻
- **Demod0**：与 XY 控制噪声谱实验一致，读取 Y 通道，参考频率由 `HF2_OSC_FREQ_HZ` 设置
- **DAQ**：采集 Y 分量波形

### 固定参数设备

- DG900 的 Pump/Probe 光功率通道按 `Pump_laser_power`、`Probe_laser_power` 映射连接并设置 DC 输出。
- 每次运行都连接 `Temp_Switch` 映射的 DG900 通道；通过 DC 0 V 关闭温控、DC 5 V 恢复温控，物理输出始终 ON。
- 程序尝试按 `temperature` 映射连接当前配置的 `COM13`。连接成功时只设置 UI 目标温度，不启停 TEC 或等待达标；连接失败时记录警告和 `temperature_control.control_source: external_software`，跳过设温与温度检测并继续扫频。此时需由外部软件维持温度。

### 实验前检查

1. ✅ 确认光功率 DG900 与温控开关 DG900 均已连接；若 TEC103 串口由外部软件占用，先在外部软件中确认目标温度和稳定状态
2. ✅ 确认 Probe AOM 载波和 AM 信号接线正确
3. ✅ 确认 锁相放大器 输入连接到 BPD
4. ✅ 测试一个频率点，确认能看到基带峰

## 执行流程

完整运行与分步会话共用同一套业务步骤；模块清单在 `lab_workflows/experiment_modules/detection_chain_frequency_response/stages.py` 中定义，采集流程图 GUI、后端会话接口与执行子进程三方共用。

### 采集阶段（A01–A08）

| 步骤 | 标题 | 前置 | 说明 |
|---|---|---|---|
| A01 | 启动预检 | — | 参数、锁相兼容、安全限值与 `HF2_DEMOD_IDX` 映射全量校验 |
| A02 | 设备连接 | A01 | 连接 DG4000（载波+AM）、DG900×2（光功率、温控开关）、锁相放大器；TEC103 可选；统一初始化锁相输入 |
| A03 | 参考时钟同步 | A02 | 按设备库配置批量写入并严格回读参考时钟 |
| A04 | 初始输出与温控 | A03 | 设置 Pump/Probe 光功率；温控开关置 5 V DC；TEC103 连接成功时仅设置目标温度，失败时记录外部温控来源 |
| A05 | HF2 解调配置 | A04 | 关闭信号输入 0 的 AC 耦合与 50 Ω 终端，设置单端和量程、振荡器 0、Demod0 采集低通/阶数/速率；相位沿用当前值并保存快照 |
| A06 | AOM 载波与外部 AM | A05 | CH1 输出 100 MHz 载波并启用外部 AM（`AM Source = EXT`） |
| A07 | 频率扫描采集 | A06 | 创建独立运行目录；逐频点设置 CH2 频率 → 温控开关置 0 V DC → 等待稳定 → 解调时序采集 Y 波形 → 恢复 5 V DC → Welch PSD 提取非零基带峰值；保存原始波形与 `raw/frequency_response.npz` |
| A08 | 安全收尾 / 结束会话 | 无（最后执行） | Probe AOM 两通道归零并关闭；温控开关恢复 5 V DC + 输出 ON；仅在连接过 TEC 时断开 |

完整运行入口按 A01→A07 顺序执行并在结束后自动收尾；分步会话中可勾选任意满足前置的模块重跑，前置重跑会使后续步骤失效，每轮扫描保存到独立目录。

### 分析阶段（B01–B03）

| 步骤 | 标题 | 前置 | 产物 |
|---|---|---|---|
| B01 | 加载配置与准备分析 | — | 归档旧产物并建立分析摘要 |
| B02 | 归一化响应与斜率对照 | B01 | `analysis_results.npz` |
| B03 | 响应曲线绘图与汇总 | B02 | `frequency_response.png` / `.pdf` |

1. **归一化**：排除 AM 频率等于 锁相放大器 参考频率的零基带点；以参考频率两侧最近的有效测量点的峰值平均作基准（只有一侧时使用该侧最近点）。历史运行也按此规则重新分析，原始数据不改写
2. **计算关键指标**：上边带下降（105-115 kHz）、下边带下降（75-65 kHz）、双边带平均下降（15-25 kHz 基带）
3. **对比噪声谱**：与 N_S1 下降（-0.010% / kHz）对比
4. **绘图**：零基带点在曲线上留空，不跨点连线；绝对响应显示峰值幅度谱密度（V/√Hz）
5. **保存结果**：到 `results/`

分阶段分析绑定具体运行目录，与硬件会话分开执行；输入指纹（配置与 `raw/` 文件）或产物变化会使后续阶段失效并要求重算，重算前旧产物归档到 `results/before_*` 子目录。没有有效谱峰点时分析标记为质量未通过并保留诊断，不产出归一化结果。

## 数据目录结构

```
data/Detection_Chain_Frequency_Response/MMDD_HHMM_freq_resp/
  experiment_config.yaml          # 实验参数快照
  acquisition_state.json          # 分步会话的采集阶段状态快照
  analysis_state.json             # 分阶段分析状态与输入指纹
  raw/
    frequency_response.npz        # 频率响应数据；新运行的零基带峰值为 NaN
    waveform_0000.npy             # 原始 Y 信号波形
    waveform_0001.npy
    ...
  results/
    results_summary.yaml          # 采集摘要
    frequency_response.png        # 响应曲线图（成对导出 .pdf）
    analysis_results.npz          # 分析结果，含零基带排除标记及归一化参考频率
    analysis_summary.json         # 分阶段分析摘要
    before_*/                     # 重算前归档的旧产物
```

## 结果解释

### 预期结果

基于 BPD 1 MHz 带宽（-3dB）的一阶低通模型：

- **单边带预测**：-0.0217% / kHz（只看上边带）
- **双边带预测**：-0.0022% / kHz（上下边带平均）
- **噪声谱观测**：-0.010% / kHz

### 结果判读

零基带点的 Welch PSD 默认去均值，不能把其残余低频峰当成 90 kHz 的真实响应。旧运行的 `raw/` 数据及采集摘要保留原值；重新分析时仅在 `results/` 中排除该点。其余频点的相对响应以靠近零基带的有效测量点为基准。

| 测量结果 | 含义 |
|---------|-----|
| 两侧响应相近 | 可用双边带平均观察整体趋势 |
| 两侧响应不同 | 先检查 AM 注入、探测链路和各点谱峰定位 |
| 高频响应下降 | 可与噪声谱下降趋势比较，但不能仅按斜率比例推算噪声贡献 |

本实验测量注入的单频 AM 响应；噪声谱还包含噪声源本身的频谱。两种斜率只作为对照，不输出“可解释百分比”。

## 安全注意事项

1. **Probe AOM 功率**：CH1 输出受 `safety_limits.yaml` 限制
2. **加热信号**：Pump 取任何值都逐点关闭温控开关并在采集后恢复，不能用 DG 输出 OFF 模式代替 0 V DC
3. **实验结束或取消**：Probe AOM 两通道归零并关闭输出；温控开关恢复 5 V DC + 输出 ON；锁相放大器 保留本次设置（信号输入 0 仍为 DC 耦合）；程序连接过 TEC 时保留目标设置并断开连接
4. **长时间测量**：注意激光功率漂移（~41 点需 5-10 分钟）

## 已知限制

1. **AOM 频率响应**：假设 AOM 在 50-130 kHz 范围内响应平坦
2. **单频vs宽带**：测量单频信号，噪声谱是宽带噪声
3. **Pump 条件影响**：0 V 时无自旋极化，BPD 探测模式可能不同；非零时仍需结合 XY 噪声实验判断不可控噪声下降来源
4. **时间漂移**：长时间扫描可能引入系统漂移

## 示例用法

### GUI 操作

实验中心进入“探测链路频率响应标定”后，页面包含“运行 / 历史 / 架构”三个主 Tab。“运行”页为可勾选、带依赖的交互实验流程图：左列采集流程 A01–A08、右列分析流程 B01–B03，节点按分组展示前置依赖、执行状态、失败原因与产物链接，可展开节点按基础/高级分类编辑该步骤使用的参数（共享参数的值与分类跨节点同步）。顶部操作区保留保存参数与布局、仅预检、预检并运行实验（完整运行自动执行全部步骤并收尾）及采集任务状态。

分步执行时先“创建分步会话”（一个子进程持有设备会话并占用硬件锁，等待期间保持输出），再勾选模块“运行所选 / 重跑”——前置必须已完成或包含在本次选择中，缺项会被拒绝且不自动补齐；前置重跑会使后续步骤标为“需重跑”。修改参数后先“应用参数到会话”，界面按后端失效规则预览预计需重跑的已完成步骤。结束时用“结束会话”执行安全收尾；执行中取消、通信中断或后端关闭也会自动收尾后再释放硬件锁。流程图标题旁提供[可编辑流程图](../lab_workflows/experiment_modules/detection_chain_frequency_response/探测链路频率响应标定.excalidraw)下载（Excalidraw 源文件）。

右列分析默认操作“下一次实验”（完整运行的自动分析设置，本实验无可调分析覆盖值），也可切换到已结束的历史目录分阶段重算：勾选 B01–B03 执行，重算归档受影响阶段的旧产物，不移动仍被前置使用的输入。运行时程序关闭 锁相放大器 信号输入 0 的 AC 耦合；任何 Pump 设置都逐点关闭并恢复温控。结果图排除零基带点，以最近的非零基带点归一化。最近一次结果图显示在流程图下方；运行中的任务可通过任务组件取消。

“历史”页列出本实验的运行记录，可分页、刷新并查看单次运行的参数快照、采集与分析状态以及已有结果图和分析文件。TEC103 未连接时显示外部温控提示，实际连接错误及温控来源保存在该次 `experiment_config.yaml`。“填回参数”只将当前表单认识的历史参数写回运行页，不保存默认值或启动采集；“重新分析”仅处理选中运行的原始数据。没有结果图时页面显示暂无结果图。默认参数仍以[默认配置文件](../params/experiments/detection-chain-frequency-response.yaml)为准，历史参数以该次运行的 `experiment_config.yaml` 为准。

“架构”Tab 内嵌本实验的 Archify 架构图：总览（仪器接入、驱动与探测主路径、数据链）、采集流程（温控门控逐频扫描）、单点时序（0 V 断温控 → 解调时序采集 → 5 V 恢复的每点硬时序）与数据分析（零基带排除与归一化链路）四张子图按需加载，均可[在新页面打开](../GUI/frontend/public/architecture/detection-chain-frequency-response.html)。

### 采集数据

```bash
# 使用默认参数
python experiments/Detection_Chain_Frequency_Response.py

# 运行目录由程序按时间戳自动创建在 data/Detection_Chain_Frequency_Response/
```

### 离线分析

```bash
# 分析最新运行
python experiments/Detection_Chain_Frequency_Response_plot.py

# 分析指定运行
python experiments/Detection_Chain_Frequency_Response_plot.py data/Detection_Chain_Frequency_Response/0924_1620_freq_resp
```

## 历史记录

- **2026-09-24**：初始版本，用于验证噪声谱高频下降来源
- **2026-10-14**：改造为分步会话与交互流程图（A01–A08 / B01–B03），采集与分析按职责拆分模块，新增分阶段离线分析

## 相关实验

- [XY 控制噪声谱](noise_spectrum_xy_ctrl.md)：观测到的高频下降
- [Noise_Spectrum_XY_Ctrl_v2](../experiments/Noise_Spectrum_XY_Ctrl_v2.py)：噪声谱 v2

## 参考文献

- Thorlabs PDB2xx 平衡探测器：DC to 1 MHz 带宽
- Zurich 锁相放大器用户手册
