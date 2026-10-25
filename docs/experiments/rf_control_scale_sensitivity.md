---
title: 多控制 Scale RF 响应与灵敏度
type: RF_Control_Scale_Sensitivity
scan_mode: nested_scan
experiment_id: rf-control-scale-sensitivity
defaults_file: params/experiments/rf-control-scale-sensitivity.yaml
mapping_keys:
  X_magnetic_field: {role: scan}
  Y_magnetic_field: {role: scan}
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
required_devices: [GS200, signal_generator, lockin_amplifier]
learned_notes:
  - 每个控制 × Scale 独立校相；RF 相位可逐点扫描或共用本实验手动设定，其余校相不跨点继承；RF 目标频率固定，不自动追峰。
  - 灵敏度使用对应倍率的色散拟合绝对中心斜率和噪声，不能跨倍率复用标定。
---

# 多控制 Scale RF 响应与灵敏度

本实验比较多个控制方案随完整包络 Scale 变化的 RF 响应、中心频率和灵敏度。扫描倍率由起点、终点和点数等间距生成，支持降序、零和负数，不限制在标称值附近，也不强制包含 1。每个控制另有手动补偿倍率，未设置时为 1；结果图横轴仍显示扫描倍率。
**每个控制 × Scale 组合都独立校相，再测幅度响应、可选零 RF X 噪声和 R 扫频；RF 触发相位可逐点扫描，也可关闭扫描后共用本实验的一个手动相位**。
设备连接、时钟和固定条件只准备一次，不因换点重新连接；Demod0、XY 和 RF 解调器校相仍逐点独立执行，不从其他点继承。
支持从已结束的 v2 运行续测：在新目录复制已完整采集点并记录来源，中断点按同一扫描／手动相位设置重新校相并整点重测，原目录不修改。扫描模式或手动相位与来源不一致时拒绝续测。
沿用 [RF DirectAW](rf_field_measurement.md) 的真实校相算法、XY 控制、Z RF、共同触发及两级解调；
仅参考 [已知噪声实验](rf_field_measurement_known_noise.md) 的控制波形多选，
**不合成人为噪声，不增加噪声注入接线**。

当前默认值以 [独立参数文件](../../params/experiments/rf-control-scale-sensitivity.yaml) 为准。
初次使用必须读取控制列表并勾选波形，不自动勾选最新版本。
标定、主场和锁相初始设置从新增时实际 RF DirectAW 默认文件复制为独立配置，之后互不覆盖；
运行前须核对本次接线和标定。历史参数只读取本次运行的 `experiment_config.yaml`。

## 控制来源及缩放定义

`CONTROL_WAVEFORM_FILES` 读取 `oc_sens/<控制名>/waveforms/optimal_control_waveform.csv`，
支持 vN 版本和自定义名称。各控制的设计 RF 频率必须与本轮 `Z_RF_FREQ` 一致；
源目录、公共周期、实际设备波形点数和所有倍率的电压极值在连接仪器前校验，不自动改变时间轴。

使用 `CONTROL_SCALE_START`、`CONTROL_SCALE_STOP`、`CONTROL_SCALE_POINTS` 设置起点、终点和点数。多个点时包含两端点，可以递增或递减；点数为 1 时仅使用起点。起终点须为有限数值，点数须为正整数，生成的倍率不能重复（包含浮点精度导致的重复），不要求包含 1。旧配置仅有 `CONTROL_SCALES` 时继续使用原列表，允许非均匀、乱序的有限且不重复倍率；填写三个范围字段后，范围优先，实际列表仍按稳定键 `CONTROL_SCALES` 保存。

`CONTROL_SCALE_FACTORS` 是以控制名称为键的手动补偿倍率映射，未设置的控制使用 1；该常数用于手动幅度补偿，不根据电感自动计算，也不是频率相关的预加重。缩放对象是完整的物理控制包络：
`Omega_alpha(t) = alpha_scan * factor_control * Omega_nominal(t)`，包括包络平均分量。
补偿按控制名称绑定，不因勾选顺序改变而交换；未选控制的已填补偿保留但不生效。图横轴仍为 `alpha_scan`，不是实际总倍率。
实际电压为 `(Omega_alpha - XY_CTRL_B_HZ) / XY_CTRL_K_HZ_PER_V`；
有非零标定截距时不能直接把电压乘倍率冒充该物理定义。
不改变 GS200 主场、独立补偿或 RF 信号标定。
自动 AW 满量程和归一化共同保留实际幅度，不会抵消倍率变化。所有控制、全部扫描倍率与补偿相乘后的电压极值仍在连接前校验，有限数值限制、设备范围和安全限值不会取消。零倍率在参数层面合法；零电压包络使用合法的最小 AW 满量程配全零 DAC 数据，不伪造最小输出场。实际校相或测量缺少有效信号时，仍按既有质量门槛停止，不为了允许零倍率而跳过校相。

不同控制可以有不同标称均方根、峰值或周期；本实验比较完整控制方案在**每点校相及明确 RF 相位设置后**的
Scale 依赖，不是等功率条件下波形形状的独立因果比较。开启 RF 扫描时各点独立选相；关闭时只固定 RF 触发相位，其他校相仍逐点进行。
这与历史 v1 将其他倍率也固定为标称校相结果的协议不同，不应混为同一测量条件；扫描模式与手动模式也必须在结果中明确区分。

## 参数与流程

| 参数 | 含义 |
| --- | --- |
| `CONTROL_WAVEFORM_FILES` | 从实际理论目录读取并勾选一个或多个控制 |
| `RESUME_RUN_ID` | 留空为完整新测；指定本实验已结束运行 ID，在新目录保留完整点，仅补测缺失或失败点；采集参数、标定、设备库、映射和控制源须与来源一致 |
| `TEC_EXTERNAL_CONTROL` | 显式由外部软件控制 TEC，完全不打开串口、不下发目标；仍控制独立 `Temp_Switch`，记录外部控制及未知实际温度 |
| `CONTROL_SCALE_START/STOP/POINTS` | 扫描倍率起点、终点和点数；有限起终点、正整数点数，支持降序、零和负数，不强制包含 1 |
| `CONTROL_SCALE_FACTORS` | 按控制名称填写手动补偿倍率，未设为 1；只影响 XY 完整物理包络，结果横轴仍用扫描倍率 |
| `CONTROL_SCALES` | 实际倍率列表及旧参数兼容键，GUI 隐藏；旧配置未填写范围时按原列表扫描，范围字段优先 |
| `INCLUDE_NOISE` | 是否采零 RF X 噪声；关闭时仍采幅度响应和 R 扫频，不输出灵敏度数值 |
| `RF_AMP_START/STOP/POINTS` | 跨越零场、至少 5 点的有符号 RF 幅度扫描；负号通过已校准相位加 180° 表示 |
| `SLOPE_FIT_POINTS` | 仅兼容历史参数，已隐藏；当前色散拟合使用整条 RF 响应，不再读取该键 |
| `BW_FREQ_START/STOP/POINTS` | 必测 R 频率响应窗口，完整包含固定目标 RF 频率；不随拟合中心自动追峰 |
| `Z_RF_AMPLITUDE_INIT` | 每点 RF 相位校准及 R 扫频使用的弱 RF 幅度，扫描前须确认处于弱驱动区 |
| `Z_PHASE_SCAN_ENABLED` | 是否在每个控制 × Scale 内扫描 RF 触发相位；关闭后全部组合共用一个手动相位，旧配置缺键时仍扫描 |
| `Z_BURST_PHASE_INIT` | 正 RF 触发相位，°；开启扫描时为初始值，关闭时为全体控制与倍率共用的手动值，按 360° 归一化，负 RF 加 180°；当前默认值见参数文件 |
| `NOISE_N_AVG/DURATION/RATE/NPERSEG` | 噪声记录数、记录时长、请求采样率和 Welch 段长；计算使用返回的实际采样率 |
| `NOISE_OUTLIER_POLICY` | `mad_high` 使用统一保守高值筛选，`none` 保留全部记录；只影响 B03 及下游分析，不改变采集配置 |
| `SENSITIVITY_BAND_MIN_HZ/MAX_HZ` | 所有控制与倍率共同使用的基带统计频带；不逐点挑选最佳频带 |
| `SYSTEM_SETTLE_S` | 配置和温控共同稳定等待，每次换点/换态重新计时 |
| `TEMP_GROUP_SIZE` | RF 触发相位扫描、幅度响应及 R 扫频的温控分组点数，末组不足一组也恢复后保存 |

其余 RF 线路、参考时钟、温控、光功率和相位参数沿用 DirectAW 的真实含义。
隐藏的 `LINEWIDTH_MODE` 固定为实测频率扫描，不使用色散等效线宽代替中心频率测量。

| 节点 | 职责及前置 |
| --- | --- |
| A01–A07 | 顺序预检、连接、严格参考时钟同步、初始输出、TEC 目标、Pump、共同触发 |
| A13 | 依赖 A07；逐控制 × Scale 原子执行“Demod0 → 当前倍率 XY → RF 线路准备 → RF 相位扫描或手动设置 → RF 解调器校相 → 幅度响应 → 可选零 RF X 噪声 → R 扫频” |
| A15 | 恢复响应设置、安全收尾并封存所有子运行，依赖 A13 |
| A16 | 无前置的安全结束会话 |
| B01–B04 | 顺序校验封存数据、色散斜率/线宽及洛伦兹中心拟合、各倍率噪声质量与 PSD、四项指标与公共频带中位数比较；频带和筛选策略变更从 B03 失效 |

A13 的每个点都使用全新的校相上下文，依次调用 DirectAW 内部 A08–A12 业务；其中 A10 是线路准备。
开启 RF 扫描时执行原四级校相；关闭时内部 A11 仅应用本实验共用的手动相位，不进行相位扫描、拟合或关闭温控，
A08、A09、A12 仍逐点执行真实校相。任何校相或相位设置未通过都不会进入该点测量或下一点。
即使在首点、首个响应样本之前校相失败，重试也新建运行目录，不覆盖失败诊断、不继承旧相位。
外层不再提供先校完全部点再统一采集的 A08–A12 节点；其参数集中在 A13，防止误用跨点校准。
完整运行和分步运行调用同一条链路；逐点校相会增加采集时间。
幅度响应与噪声始终位于固定目标 RF 频率；R 扫频同步修改 RF 源和解调器频率，
结束恢复目标频率并关闭 Z RF。所有控制采用相同 RF 幅度标定。

### 温控及安全

遵守“配置 → 关温控 → 稳定 → 采集至内存 → 恢复温控 → 保存”。
组内换点后重新等待，整组恢复温控后保存；慢磁盘不占用关闭窗口。
TEC 默认仅下发 UI 目标温度，不启停、不判稳；连接失败告警跳过。
外部软件占用 TEC 时可显式启用 `TEC_EXTERNAL_CONTROL`，此模式连串口也不尝试打开，
不改变外部软件的目标或启停状态；按 `external_software` 记录，实际温度记为 `null`。
该选择需在连接前确定，存活设备会话中切换来源须先结束并新建会话。
取消、EOF、异常与结束均使用 DirectAW 安全收尾：关闭 XY/Z，温控恢复 5 V DC + ON，
保持主场、光功率、Pump 和共同触发。恢复失败停止后继并尽力抢救已读数据；
原始错误不被清理或保存错误覆盖。

## 离线分析与统计边界

- 直接复用 **RF 灵敏度（DirectAW）** 的四参数色散模型：
  `X(B) = A(B-B0) / ((B-B0)^2 + gamma^2) + offset`，拟合整条有符号 RF 响应。
  比较斜率为拟合中心处的 **`abs(A)/gamma^2`**；斜率及其归一化均使用绝对值，
  不再使用局部多项式或数值梯度回退。另存有符号斜率和零 RF 点导数供诊断。
- 色散线宽取 **`abs(gamma)`（HWHM，nT）**，按 DirectAW 的 `LARMOR_PER_NT`
  换成等效 Hz，并独立保存该值及标准误；它不是 R 扫频的洛伦兹线宽。
- R 扫频继续使用已有洛伦兹幅度模型，输出绝对中心频率、相对目标的频移及洛伦兹 HWHM。
  两种拟合分别记录有效状态，单个拟合失败不抹掉另一个拟合的有效结果；不插值补点。
- `full_analysis.png/pdf` 按从左上至右下的顺序展示：**色散拟合绝对斜率（V/nT）、
  色散等效 HWHM（Hz）、洛伦兹中心频率（Hz）、灵敏度中位数（fT/√Hz）**。
  只有采集了扫描倍率 1 且该点拟合有效、斜率为正时，才保存相对自身扫描倍率 1 的绝对斜率保持率；参考点与自身相除严格为 1、形式误差为零。没有该参考点或参考无效时，保留所有绝对指标，`normalization_scale=null` 并记录原因，不插值、不擅自换参考。每个控制的补偿倍率固定应用于该组，因此参考仍指扫描倍率 1，而非实际总倍率 1。
- 每条噪声记录先按实际采样率计算 Welch PSD，再在公共频带内以 ASD 中位数评分。
  `mad_high` 仅在至少 5 条且中位数为正时剔除满足
  `score > max(3 × median, median + 6 × 1.4826 × MAD)` 的**高值**记录；不剔除低值，
  不删除整组，也不依据曲线是否好看来选点。样本不足时不自动剔除；`none` 可恢复全部记录。
  保留所有单条 PSD、接纳掩码、未剔除平均 PSD、逐条评分 CSV 和剔除汇总 JSON。
- 对接纳记录的 **PSD 求平均后开方**，再用各自色散拟合绝对斜率换算谱，
  **对设定公共基带频带内的灵敏度 ASD 取中位数**。不改变设定频带，
  不按控制或倍率分别挑选最佳频带。去除突发高噪声后的数值属于稳态估计，
  未剔除指标同样保留；不能将其解释为整个采集期间始终达到该性能。
- 洛伦兹 HWHM 不是硬截止：某组线宽略低于公共频带上限，不代表该组噪声异常，
  不再屏蔽该组或整个实验的中位数；在结果中通过 `band_exceeds_lorentzian_hwhm` 单独提示。
  只有实际 PSD 不覆盖设定频带或有效频点不足时，才将该组灵敏度统计标为无效。
- 主比较使用斜率折算谱的中位数，同时保存基于各倍率洛伦兹线宽的单极点补偿估计。
  噪声仍在零 RF 下采集；拟合中心斜率与零场实际增益的等同性，需结合保存的 `B0`
  和零场导数判断。有限基带完整灵敏度仍依赖 X 动态响应，不能把色散或洛伦兹线宽当成独立频响标定。
- 斜率形式标准误由四参数完整协方差传播，包含 A 与 gamma 的相关项；线宽标准误来自 gamma。
  未包含标定、相关残差及跨运行重复性。图例直接保留所选控制波形名称，包括中文原名；其他标注为英文。

## 数据与入口

```text
data/RF_Control_Scale_Sensitivity/<timestamp>_<tag>/
  experiment_config.yaml
  raw/point_manifest.json
  controls/<control>/
    experiment_config.yaml
    raw/                       # 预留，不存储跨倍率校相结果
    results/
    scales/<scale>/
      experiment_config.yaml   # 本点校相倍率、五步执行状态、扫描／手动相位来源
      raw/                     # 初始/最终 XY 波形、扫描模式的 z_phase_scan.npz、响应/噪声/R 扫频时序
      results/                 # 本点 RF 校相诊断图
  results/                     # 聚合 CSV/JSON/YAML/NPZ、PNG/PDF
```

参数 schema 为 **2**，原始格式仍为 **v2**，写入 `phase_calibration_scope=per_control_scale`；响应、噪声和带宽数组格式不变。范围参数与实际 `CONTROL_SCALES` 同时写入新运行快照；每点清单、配置及分析指标记录 `control_scale_factor`（控制补偿倍率）和 `effective_control_scale`（扫描 × 补偿），`control_scale` / `scale` 继续表示扫描倍率。旧快照缺少补偿字段时按 1 读取，不改写历史文件。续测比较实际扫描列表、所选控制的有效补偿及其他采集参数；只改变范围表示法不改变点列、或补充显式补偿 1，不视为测量条件变化，改变有效补偿则拒绝复用旧点。
每点 `calibration_scale` 必须等于该点 `control_scale`，其余自动校相来源为自身，并验证完整五步状态、有限相位和初始 XY 原始文件。
扫描模式仍强制要求 `z_phase_scan.npz`，选定相位必须与配置及实际响应相位一致。手动模式不生成该文件或扫描图，
必须同时保存显式 `Z_PHASE_SCAN_ENABLED=false`、`mode=manual`、`scan_skipped=true`、零扫描点和与共用参数一致的最终相位，
并核对响应文件的正负 RF 相位；不能将缺失或损坏的扫描数据当作手动模式。旧 v2 缺开关时严格沿用扫描校验。
已验证的历史 **v1** 仍要求 `calibration_scale=1`，按原固定标称相位规则读取，不能伪装成 v2；
同一运行不允许混合两种格式。旧文件、运行目录和测量结果不重写。
只接收本实验标记与完整清单，不猜测其他实验历史格式；离线分析只依赖本次保存的数据，
不再读取当前理论源目录或连接仪器。输入指纹覆盖所有子运行配置和原始文件；
显式重新分析才归档失效结果，不改原始数据。
分析版本为 `rf-control-scale-v6-scale-range-factors`；摘要、指标表和 GUI 区分历史 v1 标称校相、v2 逐点 RF 扫描及 v2 共用手动 RF 相位。
不把旧 v1 数据描述为逐点重新校相，也不把手动模式描述为完成了 RF 相位扫描。

```powershell
.\agent_exp_env\Scripts\python.exe experiments/RF_Control_Scale_Sensitivity.py
.\agent_exp_env\Scripts\python.exe experiments/RF_Control_Scale_Sensitivity_plot.py data/RF_Control_Scale_Sensitivity/<run_id>
```

## 只读噪声记录诊断程序

数据类型目录提供本地便捷入口 `data/RF_Control_Scale_Sensitivity/analyze_noise_records.py`。
与正式分析共用 Welch、实际采样率校验、色散拟合与高值规则，**直接从 raw 重算**，
无需先生成 `results/psd_data.npz`，不连接仪器，不修改原始记录、采集快照、主分析结果或分析状态。
仓库维护实现也可直接使用模块入口，便于迁移到其他已完成运行：

```powershell
.\agent_exp_env\Scripts\python.exe data/RF_Control_Scale_Sensitivity/analyze_noise_records.py data/RF_Control_Scale_Sensitivity/<run_id>
.\agent_exp_env\Scripts\python.exe -m lab_workflows.experiment_modules.rf_control_scale_sensitivity.noise_diagnostics data/RF_Control_Scale_Sensitivity/<run_id> --reference-scale 1 --whole-group-ratio 2
```

- 单次异常：使用上述同一条组内高值规则，比较全部记录和接纳记录的平均 PSD 指标。
- 整组灵敏度较差：以**同一控制的参考 Scale** 为基准；接纳记录下四分位数仍超过参考组
  接纳记录中位数的指定倍数才标记。可同时标记“两种问题并存”，不会因整组偏差自动删组。
  该分类是相对灵敏度判断；斜率下降也会造成整组灵敏度变差，不能直接认定电压噪声增加。
  缺少有效斜率或参考时明确记为未完成整组判定。
- 频带和 Welch 段长沿用该运行有效分析设置，可用 `--band-min-hz`、`--band-max-hz`、
  `--nperseg` 仅覆盖本次诊断。`--whole-group-ratio` 是诊断分类标准，不是删点阈值。
- 输出 `results/noise_record_diagnostics/<时间戳>/` 下的 `report.md`、`diagnostics.json`、
  `group_summary.csv`、`record_scores.csv`、`noise_record_diagnostics.png/pdf`；
  `--output` 可指定**新目录**，已存在目录拒绝覆盖。记录图为每组独立对数纵轴，
  同时显示单次异常阈值和整组参考倍数线，不可直接凭各子图的屏幕高度比较数值。
- 数据目录脚本与数据均为本地保留项；复用或备份时一起保留。长期实现和回归位于实验包。

## GUI

通过实验中心进入“多控制 Scale RF 响应与灵敏度”，主视图为“运行 / 历史”。
运行页采用采集/分析两列交互流程图：在节点内编辑基础/高级参数，勾选模块并检查依赖。
顶部集中保存参数与布局、只读预览、预检、完整运行、任务日志及取消；
等待分步命令时连接和硬件锁保持，须显式结束会话。

共享参数在真实使用节点同步，分类偏好按键统一。草稿先预览影响范围，应用后显式重跑，
不会自动补做前置。A13 控制列表逐行显示「波形名称｜勾选框｜补偿倍率」，保持多选；补偿默认 1、可在未选时预填，改选不交换或删除各控制的设置。波形与补偿作为一组移动基础/高级分类。A13 另行编辑扫描倍率起点、终点及点数；这些参数变更影响 A13 及其后继，应用后须显式重跑。顶部预览区区分扫描范围和补偿；结果表同时显示扫描倍率、补偿倍率和实际总倍率，图横轴保持扫描倍率。
A13 内还可编辑全部校相参数和「扫描 RF 触发相位」；选择「否」后填写「正 RF 触发相位」（沿用已有基础/高级分类），
全部所选控制及倍率共用这一个值，不提供逐点相位表。更改开关或手动相位并应用后使 A13 和后继失效，须显式重跑；不提供独立的全局标称校相入口。
每次普通重跑新建目录，其余自动校相不继承任何点的结果，原运行原始数据保留。
需要断点续测时，在 A13 高级参数中填写 `RESUME_RUN_ID`；新建会话并完整运行 A01–A15，
仍重新连接、同步时钟和配置固定条件。完整点须通过原始文件、点数、噪声记录数、逐点校相及扫描／手动相位来源、
采集相位和控制源一致性校验，再复制到新目录；失败点的部分响应或噪声不拼接，全部重新校相并测量。
副本保留原采集参数、时间和相位，在 `resumed_from` 记录来源；父运行记录复用点数和来源温控/收尾状态。
新旧点具有不同采集时间，不能把跨时段续测解释为一次连续扫描；长期漂移应另行评价。
TEC 外部控制在 A02/A05 节点设置。续测完成后清空来源 ID，避免下一次新测误用续测来源。
分析可选择下一次实验或已封存运行目录；B03 可编辑筛选策略及公共频带，结果表显示剔除/总条数。
具体目录的 Welch、筛选策略和公共频带覆盖值独立保存，保存默认值不会写回历史采集参数或其他目录的草稿。

历史提供分页、参数快照、各控制/倍率的指标和结果图、CSV/NPZ/PDF 下载、填回参数和重新分析；
浏览和填回不启动采集。历史填回通过只读派生接口复用参数模型规范化，不从当前默认范围覆盖旧运行；旧列表在三个范围字段均为空时继续生效。可编辑流程原图随流程图下载，不增加架构 Tab。

## 无硬件验证

正式回归位于本实验包的 `test_workflow.py`、`test_analysis.py`、`test_noise_diagnostics.py`，
API 位于 `GUI/backend/test_rf_control_scale_sensitivity.py`，
真实实验路由交互位于 `GUI/frontend/src/pages/RFControlScaleSensitivityPage.test.tsx`。
采集回归使用真实业务函数和实际文件写入配假仪器，验证多个控制与倍率的自动校相调用顺序、扫描模式各点实测相位、手动模式共用相位及其余校相仍独立执行，
以及校相失败阻止测量、首点失败后新目录重试、v1/v2 格式隔离、手动来源与实际响应相位一致及续测拒绝不同模式／相位；覆盖分组、末组、慢盘、取消、
采集/恢复/保存失败和部分数据保护。倍率回归另覆盖无 1、零和负数、降序、单点、无效点诊断、独立补偿、浮点近邻目录不冲突、越界在连接前拒绝、旧列表/范围优先级及 YAML 往返。
本次核心 Python/API 首轮 70 项通过，后续零包络、DirectAW 正交规则、旧点列改为范围且显式补偿 1 的续测、API/流程 fixture 一致性分别复核通过；仪器接口及共享采集契约 111 项、前端全量 405 项（单工作进程复跑）、类型检查和生产构建通过，文档与流程结构单独检查通过。扩展同族 Python 批次达到 900 秒上限，未完成，不能记为通过。全库基础与完整交付验证仍因共享调用方中的旧实验场景声明缺口而未通过，不能据此声称全库验收完成；报告位于 `.cache/expcodegen/control-scale-validation.json` 与 `.cache/expcodegen/control-scale-delivery.json`。未经真实仪器验证，正式运行前仍需核对接线、标定与弱驱动范围。
