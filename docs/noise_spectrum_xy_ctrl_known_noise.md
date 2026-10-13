---
title: X/Y 交流控制测量已知可控噪声谱
type: Noise_Spectrum_XY_Ctrl_Known_Noise
execution_mode: typed_workflow
description: 在 Z 小线圈注入分段平顶谱的已知周期伪噪声，色散点先按采样率与采集时间连续采集 X/Y/R 波形并取中位数；相邻控制点交替开/关采集顺序、两态等待并逐段保存。与正弦噪声谱、Probe AM 实验共用全控制轴分析，独立验收峰与背景；有符号参数差用于定量统计，差分幅值图以绝对值展示；离线真值谱仅按 K_Z 换算，不应用线圈频率响应。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
keywords: [noise spectrum, known noise injection, XY control, differential PSD, arbitrary waveform, dispersion line fit, truth chain, 锁相放大器 DAQ]
version: 1
paper_ref: paper/optimal_control.tex

scan_mode: point_by_point

mapping_keys:
  X_magnetic_field:
    role: sine_x
    description: "DG4000 CH1，正弦模式输出 X 控制"
  Y_magnetic_field:
    role: sine_y
    description: "同一 DG4000 CH2，正弦模式输出 Y 控制，与 X 正交 90°"
  Z_magnetic_field:
    role: noise_injection
    description: "dg_sweep DG4000 CH1，连续播放 16384 点分段平顶谱伪噪声（无 Burst），色散扫描阶段临时切换 DC"
  Time_sequence_2:
    role: trigger
    description: "同一台 DG4000 CH2，输出固定方波并经三通触发 dg_comp CH1/CH2 Ext Trig"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流），决定共振中心；K_Z 标定的 f_0V 不属于本实验真值链"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率 DC 电平控制"
  Probe_laser_power:
    role: fixed
    description: "Probe 光功率 DC 电平控制"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，每点采集时关闭以消除温控磁场干扰"
  Pump_modulation:
    role: pump
    description: "DG4000 (DG4E222800868)：CH1 100MHz 正弦，CH2 脉冲门控"
  lockin_r:
    role: phase_calibration + noise_acquisition + dispersion_slope
    description: "锁相放大器：相位校准 + 每点 on/off Y DAQ + 色散 X/Y 连续 DAQ 与逐样本 R"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - temperature
  - Temp_Switch

# ========== 修复经验 ==========
learned_notes:
  - DG4000 任意波上限 16384 点；提高重复频率而非点数扩展频带（4 Hz → 65.5 kSa/s → Nyquist 32.7 kHz）
  - 周期伪噪声的谱线间隔 = 重复频率，远细于 50 Hz 分析格即可视为连续谱
  - 差分在洛伦兹拟合参数层进行（A_on - A_off），不在 PSD 矩阵层相减后再拟合
  - 真值谱从实际生成波形的数值 Welch PSD 计算，不用理论平顶谱
  - 色散扫描须切换 Z 通道到纯 DC（与静磁场灵敏度同条件），正常完成后重传任意波；故障或取消直接安全收尾
  - 色散斜率使用全部有效色散点拟合的零交叉点斜率，不用中心段线性拟合；不再剔除起止端点
  - 色散扫描按配置点数回插温控恢复，避免长时间关闭温控导致温度漂移
  - slots dataclass 参数子类不能使用零参 super()，必须显式绑定父类调用
experiment_id: noise-spectrum-xy-known-noise
defaults_file: params/experiments/noise-spectrum-xy-known-noise.yaml
---

# X/Y 交流控制测量已知可控噪声谱

当前默认参数：[参数文件](../params/experiments/noise-spectrum-xy-known-noise.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml` 为准。

本实验是 [XY 控制噪声谱（正弦）](noise_spectrum_xy_ctrl.md) 的姊妹验证实验：在正弦控制被动谱学的同一流程上，
通过 Z 小线圈注入**已知谱的周期伪噪声**，把"测得的差分谱"与"由独立标定链推算的注入真值谱"做绝对定量比较，
验证 XY 控制噪声谱方法的定量能力。强类型参数模型、采集工作流和离线分析器位于
`lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/`；
`experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py` 与 `_plot.py` 仅为薄入口。
参数模型继承正弦控制实验的全部扫描/解调字段，GUI 表单只来自模型声明。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 验证逻辑总览

| 环节 | 做法 |
|------|------|
| 注入 | Z 通道连续播放 16384 点分段平顶谱伪噪声（固定 seed，重复频率参数化，默认 4 Hz → Nyquist 32.7 kHz） |
| 差分 | 同一温控窗口内采两态，相邻点交替 OFF→ON / ON→OFF，两态均等待；洛伦兹拟合参数层做 A_on − A_off |
| 被测侧增益 | 内置 z 偏置色散扫描（控制场关、纯 DC，与静磁场灵敏度实验同条件）对整条色散线形拟合，取零交叉点斜率 σ（V/Hz），静态极限连接换算 |
| 注入侧真值 | 实际波形数值 PSD × K_Z²；K_Z 取 Bell Bloom Z 场标定运行，不应用线圈频率响应 |
| 判据 | 注入谱段内有效覆盖至少 50%，且有符号中位比值 S_meas/S_truth ∈ [RATIO_PASS_LOW, RATIO_PASS_HIGH]；阈值见运行配置，带外点不参与 |
| 对照 | 注入开关两态下的可控噪声 S_β 与不可控噪声 N_S1 分别对比（含本底可控噪声与真值注入谱） |

K_Z 只提供电压→频率偏移斜率；共振中心 f_0V 由主磁场（GS200）决定，**不属于真值链**，本实验不使用。

## 原理

### 差分洛伦兹分离

正弦控制实验的谱学模型为 $S_{S_1}(\omega) = N_{S_1}(\omega) + 4G^2 S_2^2\, L(\omega,\Omega_\text{Ctrl})\, S_\beta(\omega)$。
注入是线性叠加：注入开时 $S_\beta = S_{\beta,\text{本底}} + S_{\beta,\text{注入}}$。由于响应系数 A 对 $S_\beta$ 线性，
对每个分析频率，将全部有效控制点的 ON/OFF PSD 分别做洛伦兹拟合，再在**参数层**相减：

$$A_\text{diff}(\omega) = A_\text{on}(\omega) - A_\text{off}(\omega) \propto S_{\beta,\text{注入}}(\omega)$$

参数层差分避免了对可能为负的 PSD 差矩阵直接做对数残差拟合；本底谱作为副产物一并输出。
质量掩码取 ON 与 OFF 峰参数验收的交集；非正差分仍保留并参与带内统计，不裁零、不按符号筛选、不插值。背景另需远端锚点和整块尾部留出验证，背景不可辨识不自动否定磁响应峰。

差分系数、差分 PSD 和测得谱的图按绝对值显示，以便在注入带外同时查看正负起伏的幅度；原始有符号值仍保存在结果文件中，谱比值及其中位数判据仍按有符号值计算。

### 被测侧增益换算（色散斜率）

采集流程内置一段 z DC 偏置步进扫描（X/Y 控制场关），对**整条色散线形**做
$Y(v) = C + A\,(v-v_0)/\left((v-v_0)^2 + w^2\right)$ 拟合，取零交叉点斜率
$\mathrm{d}Y/\mathrm{d}v = |A| / w^2$，再经 $K_Z$ 换算成
$\sigma = (\mathrm{d}Y/\mathrm{d}v)/K_Z$（V/Hz）。这里不使用中心段线性拟合：
线形两端偏离线性区时，线性拟合的系统偏差会直接进入全部绝对定量结果。

全部有效色散点参与线形拟合，至少需要 7 点；起始/终止端点剔除参数已移除。
拟合质量（R²、线宽相对不确定度、斜率下限）按共享色散拟合门控判定，
结果与拒绝原因分别写入 `raw/dispersion_scan.npz` 和运行配置的 `dispersion` 段；
不通过时仍保留斜率用于换算，但 `analysis.yaml` 会带上复核警告。
共享 `sensitivity_analysis.fit_dispersive` 将对称扫描轴均值的浮点残余规范到精确零初值，
避免 LM 相对数值差分锁住拟合中心；线形模型、质量门控与本实验采集流程保持不变。

**离线重拟合**：与 `noise_spectrum_xy` 从 PSD 脊线重新标定 `K/B` 同理，离线分析
不直接采用采集阶段写入的斜率，而是从 `raw/dispersion_scan.npz` 的 `offsets_v` /
`y_median` 的全部有效点重新执行线形拟合，并把结果与采集记录并列写入摘要的 `dispersion` 段
（`source`、`recorded_slope_v_per_hz`、`recorded_relative_difference`）。两者相差
超过 10% 时摘要给出警告——中心段线性拟合在色散峰侧翼严重高估，斜率会被低估到
1/6 量级（`S_meas ∝ 1/σ²`，绝对定量会差约 38 倍）。当前分析只接受原始格式 3 且完成掩码全部为真的色散扫描；缺失、旧格式或不完整数据直接报错，不回退到旧记录斜率，不改写已有数据。

长时间关闭温控会让气室温度在整段扫描中持续漂移，因此每测完
`DISPERSION_TEMP_SWITCH_INTERVAL_POINTS` 个点打开温度开关一次，
按 `TEMP_SWITCH_ON_SETTLE_S` 等待后重新关闭并进入下一组；该值为 0 时保持整段关闭。

静态极限连接给出换算公式：

$$S_\beta(\omega) = \frac{A_\text{diff}(\omega)}{\gamma(\omega)^2 \, \sigma^2} \quad [\text{Hz}^2/\text{Hz}]$$

其中 $\gamma$ 为同次 ON 拟合的洛伦兹线宽。

该换算与论文 `paper/optimal control/optimal_control.tex` 的核对结论：
滤波函数形式已逐步核对一致——论文式 (Sx_static) 的分母
$|\Gamma+i\omega+\Omega_\text{Ctrl}^2/(\Gamma+i\omega)|^2$ 展开即
$(\Gamma^2-\omega^2+\Omega^2)^2+4\omega^2\Gamma^2$，与拟合模型完全对应；
静态极限 $S_x(0)=S_\beta(0)P_0^2/\Gamma^2$ 与 $S_1=2GP_xS_2$ 共同给出
$A=4G^2S_2^2P_0^2 S_\beta$。公式成立要求色散斜率幅度满足
$\sigma = 2GS_2P_0/\Gamma$；实验测得的 dY/dδf 与该理论量的幅度等价性
（因子层面，如色散与吸收线形归一化之差）在正文与仓库材料中无直接对照，
首轮运行建议用已知幅度单频正弦注入做一次自校准闭环确认。

### 注入侧真值链

$$S_\text{truth}(\omega) = S_v(\omega)\cdot K_Z^2$$

- $S_v$：从 `raw/known_noise_waveform.npy` 数值 Welch PSD 计算（非理论平顶谱，含随机相位实现的实际谱）；
- $K_Z$：`Z_CALIBRATION_SOURCE_RUN` 指定的 Bell Bloom Z 场标定运行斜率（Hz/V），f_0V 不使用；

### 波形生成

分段平顶谱由三个等长数组参数定义（`NOISE_BAND_STARTS_HZ` / `NOISE_BAND_STOPS_HZ` / `NOISE_BAND_PSDS_V2_PER_HZ`，
单段即平顶谱，多段为阶梯谱）。生成流程：谱线幅度 $A_k = \sqrt{2 S \Delta f}$（$\Delta f$ = 重复频率）、
随机相位、irfft；生成后按与主分析一致的 Welch 分段做带内中位自检（容差 25%），并归一化到 [-1, 1] 上传。
实际注入谱 = 设计谱 × (幅度 / (2×设计峰值))²，缩放系数写入运行配置并在 GUI 预览中显示。

## GUI 使用

实验中心中的专属页面包含"运行 / 历史 / 架构"三个主 Tab（架构 Tab 内嵌总览架构图与采集流程、单点时序、数据分析三张子图，可在新页面打开）：

- **运行**：编辑基础与高级参数、保存参数与布局、独立预检、启动或取消采集，查看任务阶段、日志与 ETA。
  运行页提供只读预览：任意波时域、目标噪声谱与由实际播放波形计算的 Welch 谱，以及 Welch 分段（段数/格间距）、扫描耗时和噪声波形预算——设计峰值、达到设计谱密度
  所需的对齐幅度、实际注入谱缩放、波形 RMS、等效频率噪声谱密度（Hz/√Hz，经 K_Z）与采样率/谱线间隔。
  "对齐注入幅度"按钮一键把注入幅度设为对齐值（2×设计峰值，此时实际注入谱 = 设计谱密度）；谱密度参数
  仍是名义参考值，绝对注入强度始终由幅度参数决定。预览不连接仪器。
- **历史**：分页浏览本实验运行目录，读取参数快照与 `results/` 真实产物；支持填回参数与离线重新分析。
  结果区显示差分谱热图、差分响应系数、可控/不可控噪声注入前后对比、测得谱 vs 真值谱、比值与阈值带、
  色散线形与线形拟合、若干频率列的洛伦兹线形；
  并显示中位比值、有效点数与判据通过状态。"分析完成"不代表判据通过，两者分别展示。

切换主 Tab 不会清空尚未保存的参数或当前任务。谱段数组在表单中以逗号分隔数值编辑，三个数组必须等长。

参数版本为 2。高级参数相邻展示 `HF2_DEMOD_RATE`（色散采样率，也用于 锁相放大器 校相）和 `DISPERSION_DURATION_S`（每点采集时间，s）；二者须为有限正数，向上取整后至少采集 2 个样本。旧 `DISPERSION_SAMPLES_PER_STEP` 已删除，不增加旧配置迁移。主扫描仍独立使用 `HF2_DAQ_RATE` 与 `HF2_DAQ_DURATION`，当前速率和其他用户默认值保留在参数文件中。

高级参数 `ANALYSIS_FIT_HALF_WIDTH_HZ` 仅用于峰定位、两侧采样覆盖、线宽/频移边界及峰区误差验收；拟合使用脊线有效支持区间内的完整控制轴，不要求分析频率两侧都有完整半窗。
已删除无效的 `ANALYSIS_BACKGROUND_HALF_WIDTH_HZ` 参数：GUI、新配置和新摘要不再提供，历史参数加载时自动忽略，旧运行快照不修改。不再拼接两个窗口的参数。历史页分别显示带内/带外背景比值及可辨识点数，非正差分标为保留。

采集参数只由强类型模型和保存默认文件提供；工作流内重复的默认值块已移除。
`HF2_DEMOD_IDX` 在解调器配置、校相、色散读取、DAQ 采集和结束恢复中使用同一索引；
输入通道和振荡器仍按既有接线使用 0。进度统一通过结构化消息上报，移除与 GUI 行协议冲突的终端进度条。
色散扫描、XY 校相、扫描电压换算 K/B 和标定电压范围仍参与测量或安全检查，不能作为废弃流程删除。

标定频带上限取运行快照中的扫描目标终点与分析上限的较大值，并受实际采样率的 Nyquist 限制；
最终谱仍只输出 `ANALYSIS_FREQUENCY_MIN_HZ`–`ANALYSIS_FREQUENCY_MAX_HZ`。
目标范围只用于寻找 OFF 移动峰，不替代实测 K/B、不放宽质量门槛或外推脊线支持区间。
历史快照没有目标终点时保留原分析频带。标定频带和支持范围写入摘要。

## 数据采集方式

### 信号链

```
dg_sweep CH1 (Z_magnetic_field) → Z 小线圈（连续任意波，无 Burst）
dg_sweep CH2 (Time_sequence_2) → 三通 → dg_comp CH1/CH2 Ext Trig
锁相放大器 解调器 Y 输出 → DAQ 模块 → 时域波形（每点两段：off/on）
```

### 扫描策略

初始化与正弦控制实验一致（连接、时钟同步、Pump 调制、锁相放大器 校相、XY 相位校准）。差异流程：

1. **设备初始化与噪声波形上传**：初始化时固定 DG4000 为 `INFINITY + VPP` 幅度基准；仅当设备当前处于 DC 时先应用正弦退出 DC，后续非 DC 状态重传任意波不再重复该动作或改写负载，生成波形 → 安全校验 ±Vpp/2
   → 上传任意波 → 显式选择 `CUSTom`/`USER` 并重写频率、幅度、偏置 → 回读任意波模式、点数、频率、幅度、偏置、负载和电压单位
   → 相位校准完成后打开噪声输出。扫描全程不再更新波形。
2. **色散斜率扫描**（校相与 XY 相位校准之后）：Z 通道临时切 DC，偏置 ±`DISPERSION_SPAN_V` 步进
   `DISPERSION_STEPS` 点，每步稳定等待后，复用 `lab_workflows.steps.dispersion`，回读 锁相放大器 实际速率并按 `N=ceil(f_actual×DISPERSION_DURATION_S)` 连续采集同一解调器 X/Y，逐样本计算 `R=hypot(X,Y)`，分别取中位数作为点值。DAQ 为连续、单帧、Exact 模式，记录时长 `N/f_actual`，见 [LabOne 官方说明](https://docs.zhinst.com/labone_api_user_manual/modules/daq/index.html)。
   每测完 `DISPERSION_TEMP_SWITCH_INTERVAL_POINTS` 点打开温度开关并按
   `TEMP_SWITCH_ON_SETTLE_S` 等待，随后重新关闭再继续；扫完用整条线形拟合得零交叉点斜率，
   使用全部有效色散点拟合，不剔除扫描轴两端。
   完整波形和时间信息逐点原子保存，点值与完成掩码原子更新。DAQ 开启丢样报错，等待期间检查取消，超时为实际记录时长加 5 秒；缺失信号、长度错误、非有限值、时间戳不连续、丢样或超时立即终止，保留已完成点和能够读取的故障点数据，清理 DAQ 并安全收尾。正常完成后先保存运行配置中的色散记录，再恢复 XY 等待共用外触发、重传任意波并进入主扫描；配置保存或恢复失败也安全收尾，故障或取消不重新开启噪声输出。
3. **主扫描**：每个控制点执行 `set_xy_sine_peak`（OFF→调幅→已校准相位→ON 等共用外触发）后，
   在同一温控窗口内，第一个点 OFF→ON，第二个点 ON→OFF，随后交替。
   每段设置注入状态后均等待 `Z_INJECTION_SETTLE_S`，采完立即保存 `waveform_C{i}_off.npy` 或 `waveform_C{i}_on.npy`。
   `raw/pair_timing.jsonl` 逐段记录点号、状态、顺序、等待时长和 DAQ 调用起止主机时间；不是硬件触发时间。
   同时记录样本数、非有限样本数和基础波形有效性；遇到非有限值、平直或非一维波形立即告警，保留原波形供核查，不插值或自动重采。
   第二段失败仍保留第一段，离线分析只使用有效成对文件。交替顺序减轻固定时序偏置，但不保证消除漂移。

扫描进度按点上报（含累计平均耗时估算的 ETA）；取消在任何检查点触发安全收尾。

```python
# try/finally 确保异常时恢复温度开关与输出
try:
    for i, (target_hz, envelope_v) in enumerate(scan_axes):
        set_xy_sine_peak(envelope_v)             # 与正弦实验相同的逐点重触发
        wait_for_settle(0.3)
        set_temp_switch(False)
        wait_for_settle(XY_SETTLE_TIME)
        acquire_pair(i, raw, dg_sweep, z, acquire, Z_INJECTION_SETTLE_S)
        set_temp_switch(True, wait=True)
finally:
    safe_scan_outputs_off()                      # 关闭 X/Y、Z 噪声、外触发；温控恢复 5 V ON
```

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| Z 噪声注入 | DG4000 (DG4E242401288) | CH1 | 16384 点任意波连续播放，色散扫描阶段临时 DC |
| X/Y 控制 | DG4000 (DG4E234902522) | CH1/CH2 | 正弦 Burst，与正弦实验一致 |
| X/Y 外触发 | DG4000 (DG4E242401288) | CH2 → 三通 → dg_comp Ext Trig | 常开方波 |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | 100 MHz 载波 + 脉冲门控 |
| 主磁场 | GS200 | - | 恒流，决定共振中心 |
| 光功率 | DG900 (DG9Q280100002) | CH1/CH2 DC | Pump/Probe |
| 温度开关 | DG900 (DG9Q271200104) | CH2 | 测量时 OFF，恢复 5 V ON |
| 采集 | 锁相放大器 | 信号输入 0 | 色散连续 DAQ（同一解调器 sample.x/y、逐样本 R）；主扫描 sample.y |

**运行前人工核对**（写入运行配置确认字段）：

- 确认 Z 链路与 K_Z 标定（0920_183423）采用一致的电压定义；本次离线真值计算不补偿线圈频响或其他链路的频率响应；
- 当前程序将 dg_sweep CH1 设置为高阻 `INFINITY`、幅度单位 `VPP`，每次上传后回读验证；应核对 K_Z 标定的幅度定义及真实负载是否与当前链路一致，不能假定标定时为 50 Ω。

## 输出数据

### 运行目录结构

```
data/Noise_Spectrum_XY_Ctrl_Known_Noise/
  MMDD_HHMM_known_noise/
    experiment_config.yaml      # 完整配置（含 known_noise / truth_chain / dispersion 段）
    raw/
      control_scan_axes.npz     # 目标频率轴、包络轴、手动 K/B
      known_noise_waveform.npy  # 归一化噪声波形（真值计算来源）
      dispersion_scan.npz       # 格式 3：中位数点值、完成掩码、采集元数据及线形拟合
      dispersion_steps/point_0000.npz # 每个 DC 点的完整 X/Y/R 波形与时间信息
      waveform_C0000_off.npy    # 每点注入关波形
      waveform_C0000_on.npy     # 每点注入开波形
      pair_timing.jsonl         # 新采集逐段顺序及主机时间；旧数据缺失不补造
    results/
      psd_matrices.npz          # ON/OFF 两个 PSD 矩阵 + 成对行号
      calibration.npz           # OFF 全标定频带的脊线与杂散掩码（频轴可宽于输出谱）
      popt_fit.npz              # ON/OFF 两组拟合参数与拒绝原因
      noise_spectra.npz         # ★ 测得谱、真值谱、比值、掩码、K_Z、色散斜率、可控/不可控 ON/OFF 谱
                                #   fit_mask=峰参数验收，background_fit_mask=背景可辨识性验收
      noise_spectra.csv         # 逐 bin 文本导出
      known_noise_preview.npz   # 任意波、目标 PSD 与波形计算 PSD 的完整数组
      known_noise_preview.png   # 任意波与两条 PSD 曲线
      fit_diagnostics.npz       # 差分/平均矩阵与杂散掩码
      differential_psd.png      # |ON−OFF| 差分 PSD 热图 + 脊线
      noise_spectrum_2d.png     # 平均 PSD 热图 + 脊线
      noise_spectra_extracted.png # |差分响应系数 A(ON)-A(OFF)|
      lorentzian_line_shapes.png # 若干分析频率下的洛伦兹线形与拟合曲线
      controlled_uncontrolled_comparison.png # 可控/不可控噪声注入前后对比
      measured_vs_truth.png     # |测得谱| vs 真值谱 + 有符号比值阈值带
      dispersion_scan.png       # 全部有效色散点与线形拟合曲线
      analysis_summary.json     # 分析版本、真值链参数、判据结果与警告
      before_<timestamp>/       # 重分析前归档的旧顶层产物
```

`dispersion_scan.npz` 保存 `offsets_v`、`x/y/r_median`、`completed_mask`、`sample_counts`、请求/实际速率与时长、每点主机起止时间及 K_Z。`dispersion_steps/point_<序号>.npz` 保存 `x/y/r_samples`、X/Y 原始设备时间戳 `x/y_timestamp_ticks`、对齐时间戳 `timestamp_ticks`、相对时间 `time_s`、时钟基准、采集配置、预期/读出样本数、`completed` 与 `error`。故障点保存能够读取的数据，缺失信息不补造；主机时间不是硬件触发时间。完整波形增加存储量，短记录的连续性和测量效果需用真实 锁相放大器 验收。

架构总览及采集、时序、数据分析子图标注本次工作区实现；相关源码未提交冻结，不将工作区内容绑定到旧 commit。各图类型、路径、SHA-256 和浏览器/感知复核结果见[本次交付验证记录](dispersion_daq_validation.json)。

### 共享全范围分离与独立背景验收

原局部常数底座模型在强磁噪声下会把尚未衰减完的响应尾部误分到背景。当前沿用相同的
洛伦兹传递函数，ON/OFF 分别拟合有效控制轴的全部点；幅度、线宽、峰位及模型底座来自同一组参数。模型不能约束背景时再独立验证两侧远端平台，背景提取不以峰验收通过为前提。
两态背景均为自由参数，不预设相等，不以接近真值或 ON/OFF 比值接近 1 选择拟合范围。

与正弦噪声谱、Probe AM 噪声谱统一复用 `noise_spectrum_xy.processing.fit_local_spectra(..., full_range=True)`，规则见[共享噪声参数拟合](noise_spectrum_xy_ctrl.md#噪声参数拟合)。
v3 已将脊线标定频带与输出谱频带分开，避免宽控制扫描被窄输出频带截断；当前版本在此基础上增加独立背景平台验收。
全范围模式的具体验收如下：

- PSD 必须有限且为正，坐标递增；固定窄线、脊线支持范围外及峰两侧采样不足的点不外推。
- 使用对数残差的 soft_l1 拟合；全轴与峰区中位相对残差均不超过 0.3。
- 对交错两半控制点分别重新拟合并预测另一半；最大中位误差不超过 0.4，幅度稳定性与参数不确定度继续验收。
- 背景须至少有 8 个预测磁响应不大于底座的控制点作锚点，并通过两半背景稳定性及相对不确定度验收。
- 另将距峰最远的 20% 控制点（至少 8 点）整块留出，使用其余点重新拟合，要求尾部中位预测误差不超过 0.4。
- 模型底座不能通过时，对峰窗口之外、左右各至少 8 点的远端平台执行四块水平一致性（极差/中位数 ≤20%）及逐块留出验证（中位误差 ≤40%）；通过后独立报告平台中位数，未通过才输出背景 NaN 与原因。

峰参数与背景分别保存验收掩码及背景来源。`popt/perr` 仅描述洛伦兹模型；平台背景必须读取 `N_S1`，不能从失败拟合底座取值。雅可比列归一化后估计模型参数误差，减少大动态范围导致的数值秩丢失；
这些误差是模型内近似，不含增益系统误差与时变漂移，也不等价于独立重复实验的置信区间。

### 输出和统计口径

- `S_measured_hz2_per_hz`：有符号参数差经既有增益公式换算；负值保留，NaN 为峰参数未通过验收。`S_truth_hz2_per_hz` 仅按 K_Z 换算，不应用线圈频响。
- `N_S1_on/off`：背景独立验收后的模型底座或远端平台中位数，来源见 `background_method_on/off`；不能自动等同于纯光噪声或纯电子噪声。
- `fit_mask`：两态峰参数通过；`background_fit_mask`：两态背景通过；另保存各态背景掩码。
- `negative_differential_mask`：标记有效非正差，不从统计中剔除。
- `criteria_band_mask`：注入带内且峰参数通过的频点；带外真值趋近零，比值无物理意义，不用于判据。
- `comparison.in_band_background/out_of_band_background`：分别给出背景 ON/OFF 中位比、可辨识点数、总点数及尾部留出误差；旧全频比值仅保留兼容，不能用来判断带内背景。
- 谱比值判据要求注入带内至少 50% 频点有效，并满足保存参数中的比值区间。`completed` 只代表分析执行完成，不能等同于判据通过。
- `popt_fit.npz` 附峰/背景拒绝原因、两态交错留出误差、尾部留出误差、背景锚点数、来源及平台水平差异；`noise_spectra.csv` 保存两态背景及其有效标记。
- 差分 PSD 热图、差分系数图和 measured-vs-truth 谱图用绝对值及对数纵轴显示；原始有符号差和统计输出保持不变，定量比值面板仍显示有符号值。线形图展示完整控制轴与有效背景，只叠加通过验收的峰曲线，灰色阴影只标记峰定位窗。ON/OFF 对比图各自展示通过的频点，差谱和比值才使用共同掩码。
- 重分析前，`results/` 顶层旧产物自动归档至 `before_<timestamp>/`；原始数据、配置和已有子目录保持不变。

### 增益解释与历史兼容

本次改进只处理分离和统计口径，继续保留既有
`(A_on-A_off)/(gamma_on²*sigma²)` 增益公式以便与历史比较。公式仍要求两态响应和色散校准的增益假设成立；
尤其不能把背景恢复正常理解为绝对增益验证成功。当前分别显示的两态可控谱采用各自 gamma，
当两态线宽变化时，其图上差值不严格等于上述差分谱；应结合线宽和实际控制态小信号标定判断。
不得为了让真值比值接近 1 而事后补一个经验缩放系数。

历史研究保留以下结论：早期中心段线性拟合低估色散斜率，离线应读取原始色散线形重拟合；
带外近零真值不能混入谱比值中位数；控制轴步长约 125–137 Hz 对几百 Hz 线宽偏粗。
原双窗口方案（峰窗口与背景窗口分别拟合）只能作为历史诊断，不再是当前处理流程。
原文以全频背景比值 1.02–1.09 判断背景接近不变的结论不充分，因为带外点掩盖了带内数十至数十万倍的局部拟合偏差。
也不再使用“纯磁注入所以拟合背景必须相等”作为选窗硬约束。

2026-09-22 的离线验证记录见
[诊断与重分析结果](../results/xy_known_noise_diagnosis_20260922/README.md)，新旧逐运行统计见
[before_after.json](../results/xy_known_noise_diagnosis_20260922/before_after.json)。
纯 PSD 矩阵差可为负：若直接拟合差谱，必须采用允许有符号输入的线性残差及合理权重；不能对负差取对数。
当前实现仍采用两态正 PSD 拟合后参数差分。

## 注意事项

- [经验] 当前采用参数层差分并保留非正值；若另用差谱拟合，不可使用对数残差。
- [经验] 波形自检容差 25% 来自随机相位实现的谱波动；换 seed 不改变带内中位
- [经验] 色散扫描与噪声注入互斥：DC 切换会退出任意波模式，扫完必须重传并回读校验
- [经验] 色散斜率取全部有效点线形拟合的零交叉点斜率；中心段线性拟合在两端偏离线性区时带系统偏差，
  线形拟合至少需要 7 个有效点，不提供端点剔除参数
- [经验] `0922_1725/1756_noise` 的中心段线性拟合给出 1.04–1.05 V/V，而同一份原始数据的
  线形拟合给出 6.44–6.49 V/V（R²≈0.995），差约 6.2 倍；离线上限必须用重拟合结果，
  否则 `S_meas ∝ 1/σ²` 会把绝对定量压低约 38 倍
- [经验] 判据只在注入带内评估。此前实现把带外点混入中位数（本次运行带外真值仅 1e-12 Hz²/Hz），
  中位比值被抬到 3e6–1e8；限带后同一数据给出 3.9 与 17.0
- [经验] 强注入的磁响应尾部会污染局部拟合底座；应使用完整有效控制轴并检验远端预测，不强制 ON/OFF 背景相等。
- [经验] 背景无法独立约束时输出 NaN 与原因；按带内/带外分别报告可辨识覆盖率，不能用全频中位数代替。
- [经验] 色散扫描每 `DISPERSION_TEMP_SWITCH_INTERVAL_POINTS` 点回插一次温控恢复，
  避免温控长时间关闭造成温度漂移；等待时长沿用 `TEMP_SWITCH_ON_SETTLE_S`
- [经验] 可控/不可控对比图中不可控噪声随注入开关变化时，先排查链路与差分假设，再采信绝对定量结论
- [经验] 注入幅度半幅小于设计波形峰值时谱按平方缩放，预览给出的 realized_psd_scale 即缩放系数
- [经验] 增益公式 A/(γ²σ²) 的静态极限连接是当前唯一待理论确认环节，摘要中保留提示

## 统一安全收尾

正常完成、异常、取消和 Ctrl+C 均调用共享 `run_safety_shutdown()`：归零并关闭 X/Y 正弦控制、
Z 噪声注入和非时序触发通道，恢复温度开关 5 V DC + ON；`Time_sequence_2`、Pump 调制载波与时序门控
保持当前状态，只断开 TEC；主磁场、Pump/Probe 光功率和全部 锁相放大器 设置保持不变。连接阶段失败释放已建立的连接。
