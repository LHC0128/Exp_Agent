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
  - HF2 (dev18246): 解调与 DAQ 采集
optional_devices:
  - TEC103 (tec103_com3): 可连接时设置并等待气室温度稳定
learned_notes:
  - Pump 为零用于无自旋极化对照；两种 Pump 条件下都需在采集时关闭温控
  - 双边带解调会部分抵消高频衰减
  - 测量精度依赖于 AM 调制幅度稳定性
---

# 探测链路频率响应标定

## 实验概述

设置 Pump/Probe 光功率后扫描 Probe 光 AOM 的 AM 调制频率，固定调制幅度，HF2 Demod0 解调到基带并测量 Y 信号功率谱密度（PSD）峰值幅度。HF2 解调和采样设置对照“XY 控制测量噪声谱（正弦）”，本实验单独关闭信号输入 AC 耦合，以检查该实验中不可控噪声随频率下降的来源。AM 频率等于 HF2 参考频率时落在零基带，不参与 PSD 响应曲线和归一化。无论 Pump 光功率多少，每点采集前都关闭温控以避开加热信号，采集后恢复。TEC103 可连接时由程序设温并等待稳定；串口被外部软件占用时由外部软件维持温度。

用于标定 **BPD + HF2 联合探测链路**的频率响应，验证噪声谱实验中观测到的高频下降（-0.010% / kHz）的来源。

### 关键特点

- **两种 Pump 条件**：0 V 用于关闭 Pump 的对照，非零用于带 Pump 测量；两者均在每点采集前关闭温控、采集后恢复，以抑制加热信号干扰
- **固定 AM 幅度**：排除调制深度变化的影响
- **双边带解调**：HF2 同时接收上下边带，部分抵消高频衰减
- **基带谱峰提取**：非零基带的 AM 频率对应谱峰；零基带点保留原始波形，但不计算 Welch 峰值

## 物理原理

### 信号链路

```
Probe AOM: 100 MHz 载波 + AM 调制（50-130 kHz）
    ↓
BPD 探测（频率响应 ~1 MHz 带宽）
    ↓
HF2 解调（90 kHz 参考）→ 基带信号（|AM_freq - 90kHz|）
    ↓
DAQ 采集 + Welch PSD → 峰值幅度
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

### HF2 解调参数

- **解调器索引**：`HF2_DEMOD_IDX`，应与 `lockin_r` 映射一致；当前使用 Demod0，与两项 XY 控制噪声谱实验一致
- **探测输入**：HF2 信号输入 0 关闭 AC 耦合（DC 耦合），使用单端、50 Ω，量程使用 `HF2_SIGNAL_RANGE_V`；XY 正弦控制噪声谱实验使用 AC 耦合
- **解调频率**：`HF2_OSC_FREQ_HZ`（手动设置振荡器 0 的参考频率，用于把 AM 扫频映射到基带）
- **采集时长**：`hf2_daq_duration_s`（每点采集时间）
- **采集解调时间常数**：`HF2_DAQ_TC_S` 直接设置 Demod0 低通；`HF2_DEMOD_ORDER` 设置滤波阶数，`HF2_DAQ_RATE_SA_S` 设置解调输出速率，采样使用 HF2 回读的实际速率。这些参数与 XY 正弦控制噪声谱采集阶段对齐。
- 历史键 `HF2_DEMOD_TC_S` 仍可读取，但本实验不做相位校准，采集时不使用该值。Demod0 相位沿用 HF2 当前相位，并记录在单次运行配置中。

### 固定参数

- **Pump 光功率**：`FIXED_PARAMS.Pump_laser_power`，经 DG900 自动设置；无论取值如何，每点采集期间都关闭温控
- **Probe 光功率**：`FIXED_PARAMS.Probe_laser_power`，经 DG900 自动设置
- **温度**：`FIXED_PARAMS.temperature`；仅在 TEC103 可连接时自动设置并等待稳定，外部软件占用串口时不应用该值也不读取温度
- **温控门控等待**：`TEMP_SWITCH_OFF_LEAD_S`、`TEMP_SWITCH_ON_LAG_S`，分别用于每点采集前关闭温控后的等待、采集后恢复温控的等待

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

### HF2 锁相放大器

- **输入**：BPD 信号接 HF2 信号输入 0，本实验配置为 DC 耦合、单端、50 Ω
- **Demod0**：与 XY 控制噪声谱实验一致，读取 Y 通道，参考频率由 `HF2_OSC_FREQ_HZ` 设置
- **DAQ**：采集 Y 分量波形

### 固定参数设备

- DG900 的 Pump/Probe 光功率通道按 `Pump_laser_power`、`Probe_laser_power` 映射连接并设置 DC 输出。
- 每次运行都连接 `Temp_Switch` 映射的 DG900 通道；通过 DC 0 V 关闭温控、DC 5 V 恢复温控，物理输出始终 ON。
- 程序尝试按 `temperature` 映射连接当前配置的 `COM13`。连接成功时设置目标温度并等待稳定，等待支持 GUI 取消；连接失败时记录警告和 `temperature_control.control_source: external_software`，跳过设温与温度检测并继续扫频。此时需由外部软件维持温度。

### 实验前检查

1. ✅ 确认光功率 DG900 与温控开关 DG900 均已连接；若 TEC103 串口由外部软件占用，先在外部软件中确认目标温度和稳定状态
2. ✅ 确认 Probe AOM 载波和 AM 信号接线正确
3. ✅ 确认 HF2 输入连接到 BPD
4. ✅ 测试一个频率点，确认能看到基带峰

## 执行流程

### 采集阶段

1. **初始化**：按映射连接 DG4000、光功率 DG900、温控开关 DG900 和 HF2。尝试连接可选 TEC103，并检查已连接设备参考时钟
2. **设置固定参数**：
   - 按参数设置 Pump/Probe 光功率
   - 将温控开关置于 5 V DC 开启状态
   - TEC103 连接成功时设温并等待稳定；失败时记录外部温控状态，跳过温度检测
3. **配置 HF2**：关闭信号输入 0 的 AC 耦合，设置单端、50 Ω 和量程，再设置振荡器 0 及 Demod0 采集低通、阶数和速率；相位沿用当前值并保存快照
4. **设置 Probe AOM 载波**：
   - CH1: 100 MHz，启用外部 AM
5. **扫描 AM 频率**：对每个频率点
   - 设置 CH2 频率
   - 将温控开关置于 0 V DC，再等待温控关闭与 AM 频率稳定时间
   - DAQ 采集 Y 信号波形
   - 立即恢复温控开关至 5 V DC，等待恢复稳定时间；采集异常或取消也恢复
   - 计算 PSD，提取非零基带峰值；零基带点标记为无效
   - 保存原始波形
6. **保存结果**：频率响应数据到 `raw/`
7. **恢复状态**：归零并关闭 Probe AOM 两通道；再次确认温控开关恢复 5 V DC + 输出 ON；保留 Pump/Probe 光功率和 HF2 配置，仅在连接过 TEC 时断开

### 分析阶段

1. **加载数据**：从 `raw/frequency_response.npz` 读取
2. **归一化**：排除 AM 频率等于 HF2 参考频率的零基带点；以参考频率两侧最近的有效测量点的峰值平均作基准（只有一侧时使用该侧最近点）。历史运行也按此规则重新分析，原始数据不改写
3. **计算关键指标**：
   - 上边带下降（105-115 kHz）
   - 下边带下降（75-65 kHz）
   - 双边带平均下降（15-25 kHz 基带）
4. **对比噪声谱**：与 N_S1 下降（-0.010% / kHz）对比
5. **绘图**：零基带点在曲线上留空，不跨点连线；绝对响应显示峰值幅度谱密度（V/√Hz）
   - AM 频率 vs 归一化响应
   - 基带频率 vs 响应变化
6. **保存结果**：到 `results/`

## 数据目录结构

```
data/Detection_Chain_Frequency_Response/MMDD_HHMM_freq_resp/
  experiment_config.yaml          # 实验参数快照
  raw/
    frequency_response.npz        # 频率响应数据；新运行的零基带峰值为 NaN
    waveform_0000.npy             # 原始 Y 信号波形
    waveform_0001.npy
    ...
  results/
    results_summary.yaml          # 采集摘要
    frequency_response.png        # 响应曲线图
    analysis_results.npz          # 分析结果，含零基带排除标记及归一化参考频率
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
3. **实验结束或取消**：Probe AOM 两通道归零并关闭输出；温控开关恢复 5 V DC + 输出 ON；HF2 保留本次设置（信号输入 0 仍为 DC 耦合）；程序连接过 TEC 时保留目标设置并断开连接
4. **长时间测量**：注意激光功率漂移（~41 点需 5-10 分钟）

## 已知限制

1. **AOM 频率响应**：假设 AOM 在 50-130 kHz 范围内响应平坦
2. **单频vs宽带**：测量单频信号，噪声谱是宽带噪声
3. **Pump 条件影响**：0 V 时无自旋极化，BPD 探测模式可能不同；非零时仍需结合 XY 噪声实验判断不可控噪声下降来源
4. **时间漂移**：长时间扫描可能引入系统漂移

## 示例用法

### GUI 操作

实验中心进入“探测链路频率响应标定”后，“运行”页按基础/高级参数分组编辑，可设置 Pump 光功率、HF2 采集解调低通及温控门控等待；支持移动参数分组、保存参数与布局、仅预检及预检后启动采集。运行时程序关闭 HF2 信号输入 0 的 AC 耦合；任何 Pump 设置都逐点关闭并恢复温控。结果图排除零基带点，以最近的非零基带点归一化。任务进度和最近一次结果图显示在同页；运行中的任务可通过任务组件取消。

“历史”页列出本实验的运行记录，可分页、刷新并查看单次运行的参数快照、采集与分析状态以及已有结果图和分析文件。TEC103 未连接时显示外部温控提示，实际连接错误及温控来源保存在该次 `experiment_config.yaml`。“填回参数”只将当前表单认识的历史参数写回运行页，不保存默认值或启动采集；“重新分析”仅处理选中运行的原始数据。没有结果图时页面显示暂无结果图。默认参数仍以[默认配置文件](../params/experiments/detection-chain-frequency-response.yaml)为准，历史参数以该次运行的 `experiment_config.yaml` 为准。

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

## 相关实验

- [XY 控制噪声谱](noise_spectrum_xy.md)：观测到的高频下降
- [Noise_Spectrum_XY_Ctrl_v2](../experiments/Noise_Spectrum_XY_Ctrl_v2.py)：噪声谱 v2

## 参考文献

- Thorlabs PDB2xx 平衡探测器：DC to 1 MHz 带宽
- Zurich HF2 锁相放大器用户手册
