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
  - HF2
learned_notes:
  - DG4000 背板外部 AM 输入范围为 ±2.5 V；运行前核对接线与 AOM 输入规格。
  - 输出是锁相测量端噪声谱，不是经光学标定的 Probe 功率噪声谱。
---

# XY 控制测量已知不可控 Probe AM 噪声谱

## 实验目的

在 Probe 光路 AOM 的射频载波上施加外部 AM 周期噪声，测量它在 XY 控制噪声谱中的贡献。可选择在主扫描期间向 Z 线圈持续注入独立配置的已知可控噪声；无论该选项如何，每个 XY 控制点仍交替采集 Probe AM 噪声关闭和开启两态，Z 输出在同一对采集中保持不变。离线沿用现有全控制轴分析，独立估计两态背景谱 `N_S1`，输出：

Z 注入用于检验较强可控噪声背景下，现有流程能否取得足够有效拟合点并提取 Probe AM 差谱；先采用参考实验已保存的注入幅度作为本实验独立默认值，实际结论以拟合诊断和原始谱为准。

`ΔN_S1 = N_S1,on − N_S1,off`

差值保留正负号。该量是 HF2 锁相解调测量端的 PSD，不解释或换算为绝对 Probe 光功率谱。
Probe AM 注入频带相对 HF2 解调频率的差频落入 Y 信号分析频带，是本实验观察差谱的频率映射假设；实际可见频带和幅度以原始波形、解调设置及分析结果为准。本次只报告现有模型提取的谱，不按噪声来源对各分量作额外归类。

参数 schema 与 GUI 回退值定义实验表单；GUI「保存为默认」写入的参数来源为[默认参数文件](../params/experiments/noise-spectrum-xy-uncontrolled-probe-am.yaml)，单次运行参数以运行目录的 `experiment_config.yaml` 为准。

## 设备与接线

- Probe 光路透射光仍作为测量 Probe 光。专用 DG4000 CH1 输出 AOM 射频载波，50 Ω 负载、外部 AM 源为 EXT；频率、幅度与 AM 深度读取[默认参数文件](../params/experiments/noise-spectrum-xy-uncontrolled-probe-am.yaml)及单次运行快照。
- 同一 DG4000 的 CH2 输出零偏置任意波，连接到该机背板 `Mod/FSK/Trig` 外部 AM 输入。设备手册规定该输入范围为 ±2.5 V，预检和上传前均按映射安全限值校验。[RIGOL DG4000 用户手册](https://www.rigol.com/dam/global/downloads/brochures/en/user-manual/waveform-generators/DG4000_UserGuide_EN.pdf)
- Probe AM 与 Z 可控噪声各有独立的 16384 点周期伪随机波形、谱段、重复频率、输出幅度和种子。设计 PSD 是参考值；实际电压谱按归一化波形及输出幅度计算，运行快照记录完整参数。当前 Probe AM 默认注入谱经锁相解调后落在分析频带内；具体数值以保存的参数文件为准。
- `Z_magnetic_field` 使用参考实验的 Z 小线圈注入链路，与 `Time_sequence_2` 分别占用同一 DG4000 的 CH1/CH2。后者经三通触发 X/Y 控制信号源。即使本次不注入 Z 噪声，CH1 也明确保持关闭并纳入安全收尾。
- 其余 GS200、Pump、Probe 光功率、Pump 门控、温控和 HF2 映射沿用现有 XY 测量配置。

## 采集流程

1. 预检解析设备库和物理映射，确认 Probe AOM 载波与 AM 同机、Z 线圈与共用触发同机，并校验 AM 与所选 Z 波形的输出幅度。预览只计算波形与 PSD，不连接仪器。
2. 连接实验设备、同步参考时钟并保存设备库/映射快照与参数快照；保存 Probe AM 归一化波形，启用 Z 注入时另存 Z 归一化波形。
3. 配置 Probe AOM 载波与外部 AM，上传 AM 任意波并保持输出关闭；启用 Z 注入时另向 Z 通道上传任意波，仍保持输出关闭。
4. 完成 HF2 与 XY 相位校准并配置 DAQ 后，按本次选项开启 Z 噪声并等待稳定，或保持 Z 输出关闭。主扫描全程不切换 Z 状态；每个 XY 点的 Probe AM 两态仍按偶数点 OFF→ON、奇数点 ON→OFF 交替，逐态等待、采样并保存时序。
5. 取消或异常时进入共享安全收尾。结束时关闭 Probe AM、AOM 载波和 Z 通道，然后恢复温控并断开 TEC；Pump 载波、Pump 门控与 HF2 按共享保留策略处理。

## 离线分析

分析器只读取指定运行目录，不连接仪器。`controlled_z_noise.enabled` 与参数快照记录 Z 注入状态和波形设置；现阶段不做 Z 开/关跨运行比较。分析仍用配置中记录的 HF2 实际 DAQ 采样率计算 Probe AM ON/OFF Welch PSD 矩阵，取成对有效控制点交集，并用 OFF PSD 定位移动脊线。若移动峰不足以自动生成控制轴，则按运行参数中的正弦控制标定 `K`、`B` 推算峰位，继续生成二维 PSD 图和拟合结果；该结果标记为质量未通过，仅供暂时分析，不能视为本次移动峰标定通过。两态使用同一控制轴调用共享 `noise_spectrum_xy.processing.fit_local_spectra(..., full_range=True)`；峰参数与背景独立验收，最终差分背景掩码是两态背景掩码的交集。

模型底座未通过时，允许从峰窗口之外的稳定远端平台提取背景：两侧各至少 8 点、四个控制轴分块的水平极差/中位数不超过 20%、逐块留出的中位相对误差不超过 40%。具体规则和假设见[共享噪声参数拟合](noise_spectrum_xy_ctrl.md#噪声参数拟合)。即使注入抬高背景、可控峰无法稳定拟合，仍能报告通过独立验收的 `N_S1`。可控响应只展示各态通过验收的部分，不用背景有效掩码放行峰参数。

重分析先复制 `results/` 顶层旧产物到 `before_<timestamp>/`。`popt_fit.npz` 保存完整峰/背景诊断；`popt/perr` 只描述洛伦兹模型，平台背景读取 `N_S1`，来源为 `off_resonance_plateau`。`noise_spectra.npz` 的 `valid_mask` 用于背景差谱，`controlled_valid_mask` 用于可控响应差谱，另保存各态掩码、背景来源 `background_method_on/off` 及详细拒绝原因。自动标定有效且有背景通过时即可完成背景提取，不要求可控响应同时通过。

结果包括两态 PSD、脊线、受控响应 `S_beta` 与背景 `N_S1` 拟合参数和拒绝原因、两套有效掩码、两态有符号差谱、任意波实际 PSD、CSV 和图。图表复用已知可控噪声实验的平均 PSD 二维图、ON/OFF 受控响应与不可控背景对比、若干频率列的洛伦兹线形/拟合和任意波 PSD 预览；另绘制受控响应及不可控背景的 OFF 基线与 ON−OFF 差谱双 Y 轴图。移动峰自动标定不足时，平均 PSD 二维图按运行参数 K/B 标注暂定峰位，并在 GUI 历史中显示质量未通过状态与提示。由于 Probe AM 没有独立的幅度到磁场/光功率标定，不生成真值链比较。拟合拒绝点保留 NaN 并记录原因，不做插值或外推；有效负差不裁零，也不按符号筛选。结果单位为锁相测量端 PSD 或拟合响应系数，不代表经标定的 Probe 光功率谱。

## GUI 运行与历史

实验页面提供「运行 / 历史」两个主 Tab。运行页用独立按钮切换是否注入 Z 可控噪声，并可分别对齐 Probe AM 与 Z 噪声的输出幅度；对齐值超出对应通道安全限值时按钮不可用。参数区与右侧控制／最近结果各按内容自然增长，窄屏改为单列。页面读取 schema 参数，显示噪声波形与 Welch 预览，支持预检、保存参数布局、启动和恢复任务；预检失败时不会提交采集。历史页读取该实验自己的运行列表与参数快照，分别展示背景与可控响应的有效点数、远端平台补充点数，可查看图表、填回兼容参数或重新分析。移动峰标定质量未通过但已生成暂定 PSD 二维图时，历史页仍显示该图及 K/B 暂定提示。浏览历史不会启动采集。

## 参数范围

| 参数 | 含义 | 单位与约束 |
|---|---|---|
| `NOISE_BAND_STARTS_HZ` / `NOISE_BAND_STOPS_HZ` | 设计噪声谱段边界 | Hz；与 PSD 数组等长、不重叠且位于 Nyquist 内 |
| `NOISE_BAND_PSDS_V2_PER_HZ` | 设计参考 PSD | V²/Hz；正值；实际谱以缩放波形的计算结果为准 |
| `NOISE_REPEAT_FREQ_HZ` | 任意波重复频率 | Hz；正值，决定采样率与谱线间距 |
| `NOISE_AMPLITUDE_VPP` | CH2 输出幅度 | Vpp；正值，峰值须位于外部 AM 安全范围内 |
| `PROBE_AOM_CARRIER_FREQ_HZ` / `PROBE_AOM_CARRIER_VPP` | CH1 载波频率与幅度 | Hz、Vpp；幅度为 0–0.05 Vpp，受安全限值约束 |
| `PROBE_AOM_AM_DEPTH_PERCENT` | 外部 AM 深度 | %；0–120 |
| `INJECTION_SETTLE_S` | 每态切换后的稳定等待 | s；非负 |
| `NOISE_SEED` | 固定随机种子 | 非负整数 |
| `INJECT_CONTROLLED_NOISE` | 是否在整段主扫描持续注入 Z 可控噪声 | 布尔值；同一 Probe AM ON/OFF 对内不切换 |
| `Z_NOISE_BAND_STARTS_HZ` / `Z_NOISE_BAND_STOPS_HZ` | Z 已知噪声设计谱段边界 | Hz；启用时非空、数组等长且位于 Nyquist 内 |
| `Z_NOISE_BAND_PSDS_V2_PER_HZ` | Z 已知噪声设计 PSD | V²/Hz；启用时与边界数组等长且为正 |
| `Z_NOISE_REPEAT_FREQ_HZ` / `Z_NOISE_AMPLITUDE_VPP` | Z 任意波重复频率和输出幅度 | Hz / Vpp；启用时为正，幅度受 Z 安全限值约束 |
| `Z_NOISE_SEED` / `Z_NOISE_SETTLE_S` | Z 波形种子和开启后等待 | 非负整数 / 非负秒数 |
| `HF2_DAQ_DURATION` / `ANALYSIS_BIN_WIDTH_HZ` | 每态采集时长与 Welch 目标格间距 | s、Hz；正值 |

## 伪代码

```text
预检：读取参数与映射 → 生成 Probe AM 与 Z 已知噪声的独立预览
     → 校验载波幅度、所选输出峰值和两组同机映射 → 返回预览，不连接仪器

采集：连接并同步设备 → 配置 Probe AOM 载波与 EXT AM，上传 AM 任意波并保持输出关闭
     → 选择注入时上传 Z 任意波并保持输出关闭 → 保存运行配置、映射快照和原始波形
     → 校相并配置 XY 与 HF2 DAQ → 按开关开启 Z 噪声或保持关闭，扫描中不切换
     → 每个 XY 点设置控制幅度，温控窗口内按 OFF→ON / ON→OFF 交替采集
     → 每态只切换 Probe AM CH2，等待、采样、保存波形与时序并报告 ETA
     → finally：关闭 Probe AM、载波和 Z 通道，执行共享安全收尾

分析：使用记录的实际采样率计算两态 PSD → 对齐成对有效控制点
     → OFF 谱定位脊线 → 调用三实验共享全轴拟合 → 独立验收 N_S1，必要时检验两侧远端平台
     → 保存平均 PSD 二维谱、ON/OFF 两类噪声对比、若干列洛伦兹拟合
     → 分别计算 ΔS_beta 与 ΔN_S1 并保留正负 → 双 Y 轴绘制 OFF 基线与差谱
     → 保存实际任意波 PSD、拟合掩码、拒绝原因和分析摘要；不接真值标定链
```
