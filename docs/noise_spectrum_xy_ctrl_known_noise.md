---
title: X/Y 交流控制测量已知可控噪声谱
type: Noise_Spectrum_XY_Ctrl_Known_Noise
execution_mode: typed_workflow
description: 在 Z 小线圈注入已知周期伪噪声，K_Z 由 A11 手动输入（Hz/V），预览与采集不依赖 Z 标定结果目录。运行页以采集、分析两列节点编辑参数并按依赖执行；相邻控制点交替 ON/OFF 顺序，同一温控窗口配对并逐段保存。分析完成全控制轴谱分离后，选择静磁场
  v3 直流逐点历史曲线拟合检测增益，与实际波形 PSD × 本轮保存的手动 K_Z² 真值比较；未选曲线时保留 B01～B04 并等待标定。历史目录保留独立分析草稿，保存默认值只影响下一次实验与统一布局。分步等待保持连接、输出和硬件锁；校相质量未通过禁止扫描。锁相输入采用高阻、DC
  耦合。
keywords:
- noise spectrum
- known noise injection
- XY control
- differential PSD
- arbitrary waveform
- dispersion line fit
- truth chain
- 锁相放大器 DAQ
version: 4
paper_ref: paper/optimal_control.tex
scan_mode: point_by_point
mapping_keys:
  X_magnetic_field:
    role: sine_x
    description: DG4000 CH1，正弦模式输出 X 控制
  Y_magnetic_field:
    role: sine_y
    description: 同一 DG4000 CH2，正弦模式输出 Y 控制，与 X 正交 90°
  Z_magnetic_field:
    role: noise_injection
    description: Z 通道连续播放分段平顶谱任意波，采集阶段不测内置色散
  Time_sequence_2:
    role: trigger
    description: 同一台 DG4000 CH2，输出固定方波并经三通触发 dg_comp CH1/CH2 Ext Trig
  main_magnetic_field:
    role: fixed
    description: 主磁场（GS200 恒流），决定共振中心；K_Z 标定的 f_0V 不属于本实验真值链
  Pump_laser_power:
    role: fixed
    description: Pump 光功率 DC 电平控制
  Probe_laser_power:
    role: fixed
    description: Probe 光功率 DC 电平控制
  temperature:
    role: fixed
    description: 气室温度（TEC103 控制）
  Temp_Switch:
    role: temp_gating
    description: 温度开关，每点采集时关闭以消除温控磁场干扰
  Pump_modulation:
    role: pump
    description: DG4000 (DG4E222800868)：CH1 100MHz 正弦，CH2 脉冲门控
  lockin_r:
    role: detection
    description: 所选解调器校相和逐点 ON/OFF Y DAQ
fixed_params:
- main_magnetic_field
- Pump_laser_power
- Probe_laser_power
- temperature
- Temp_Switch
learned_notes:
- DG4000 任意波上限 16384 点；提高重复频率而非点数扩展频带（4 Hz → 65.5 kSa/s → Nyquist 32.7 kHz）
- 周期伪噪声的谱线间隔 = 重复频率，远细于 50 Hz 分析格即可视为连续谱
- 差分在洛伦兹拟合参数层进行（A_on - A_off），不在 PSD 矩阵层相减后再拟合
- 真值谱从实际生成波形的数值 Welch PSD 计算，不用理论平顶谱
- 色散斜率使用全部有效色散点拟合的零交叉点斜率，不用中心段线性拟合；不再剔除起止端点
- slots dataclass 参数子类不能使用零参 super()，必须显式绑定父类调用
- 检测增益来自只读静磁场 v3 原始电压轴与 Y 中位值，来源变化只使 B05/B06 失效
experiment_id: noise-spectrum-xy-known-noise
defaults_file: params/experiments/noise-spectrum-xy-known-noise.yaml
required_devices:
- GS200
- DG900
- DG4000
- 锁相放大器
---

# X/Y 交流控制测量已知可控噪声谱

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

XY 噪声使用 `HF2_DAQ_RATE` 请求率、`HF2_DAQ_DURATION` 时长和 `HF2_DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/noise-spectrum-xy-known-noise.yaml)。GUI「保存参数与布局」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml` 为准。

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
| 注入 | Z 通道连续播放 16384 点分段平顶谱伪噪声（固定 seed，重复频率参数化） |
| 差分 | 同一温控窗口内采两态，相邻点交替 OFF→ON / ON→OFF，两态均等待；洛伦兹拟合参数层做 A_on − A_off |
| 被测侧增益 | 分析阶段选择静磁场灵敏度 v3 直流逐点历史曲线，拟合零交叉斜率 dY/dV，再除以本轮 K_Z 得 σ（V/Hz） |
| 注入侧真值 | 实际波形数值 PSD × K_Z²；K_Z 手动输入并保存为本轮快照，不应用线圈频率响应 |
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

### 被测侧增益换算（静磁场历史曲线）

新采集不再执行内置 Z DC 色散扫描。分析参数 `ANALYSIS_DISPERSION_SOURCE_RUN` 默认留空，
仅分析阶段验证，不影响预检、采集或安全结束。下拉列出当前可用的
[静磁场灵敏度](static_mag_sens_v2.md) **v3 直流逐点色散**历史运行。
来源不能仍在采集，所有色散点须完成且采样记录完整；原始曲线须通过共享色散拟合质量门槛。
来源的噪声分析不必完成。

B05 使用来源 `raw/dispersion_scan.npz` 的原始 `offsets_v` 与 `y_median`，
复用 `sensitivity_analysis.fit_dispersive` 及静磁场实验的 `QUALITY_THRESHOLDS`，拟合

$$Y(v)=C+A(v-v_0)/((v-v_0)^2+w^2),\qquad
\sigma=\frac{|\mathrm{d}Y/\mathrm{d}V|_{v_0}}{K_Z}=\frac{|A|/w^2}{K_Z}.$$

这里的 K_Z 来自本轮保存的手动标定快照，不采用静磁场来源运行的磁场换算斜率。
拟合至少需要 7 个有效点；R²、线宽相对不确定度及斜率门槛与静磁场实验一致。
无效来源直接拒绝 B05，已完成 PSD 和谱分离仍可保留。

界面显示来源温度、Pump/Probe 光功率、主磁场以及可取得的检测条件与本轮快照的差异；
不同条件允许使用，增益是否可迁移需实验判断。本轮 `results/dispersion_source.npz/json`
保存电压轴、曲线、拟合参数与协方差、质量门槛、来源参数、差异及文件 SHA-256，来源目录保持只读。

静态极限沿用历史换算：

$$S_\beta(\omega)=\frac{A_\mathrm{on}(\omega)-A_\mathrm{off}(\omega)}
{\gamma_\mathrm{on}(\omega)^2\sigma^2}\quad [\mathrm{Hz}^2/\mathrm{Hz}].$$

该连接仍要求检测增益与控制态的小信号响应假设成立，不能通过事后经验缩放使真值比值接近 1。
旧 schema 1/2 运行含内置色散记录时，仍从其原始曲线离线重拟合并保留原斜率对照；
旧色散采集参数加载时忽略，不再显示或执行。历史采集数据不改写。

### 注入侧真值链

$$S_\text{truth}(\omega) = S_v(\omega)\cdot K_Z^2$$

- $S_v$：从 `raw/known_noise_waveform.npy` 数值 Welch PSD 计算（非理论平顶谱，含随机相位实现的实际谱）；
- $K_Z$：`Z_CALIBRATION_K_HZ_PER_V` 手动输入的标定斜率（Hz/V），必须为有限正数，f_0V 不使用。

新运行在 `parameters.Z_CALIBRATION_K_HZ_PER_V` 与 `truth_chain.K_Z_Hz_per_V` 保存本轮数值，
并记录 `truth_chain.z_calibration_source: manual`。未提供标定不确定度，保存为未知，不将其解释为零误差。
预检、预览和采集不再读取 Z 标定结果目录；旧参数键 `Z_CALIBRATION_SOURCE_RUN` 仅保留加载兼容且不再显示或参与新运行计算。
离线分析优先使用运行自己的 K_Z 快照，不受当前默认值改变影响；旧运行缺少快照时，仍可读取其记录的旧标定来源。

### 波形生成

分段平顶谱由三个等长数组参数定义（`NOISE_BAND_STARTS_HZ` / `NOISE_BAND_STOPS_HZ` / `NOISE_BAND_PSDS_V2_PER_HZ`，
单段即平顶谱，多段为阶梯谱）。生成流程：谱线幅度 $A_k = \sqrt{2 S \Delta f}$（$\Delta f$ = 重复频率）、
随机相位、irfft；生成后按与主分析一致的 Welch 分段做带内中位自检（容差 25%），并归一化到 [-1, 1] 上传。
实际注入谱 = 设计谱 × (幅度 / (2×设计峰值))²，缩放系数写入运行配置并在 GUI 预览中显示。

## GUI 使用与交互流程

真实路由为 `/experiments/noise-spectrum-xy-known-noise`，保留「运行 / 历史 / 架构」Tab。
运行页复用 `WorkflowPanel`，桌面为采集与分析两列，窄屏改为单列。
保存参数与布局、波形与 Welch 预览、仅预检、完整运行、取消及任务日志位于流程容器顶部。
预览不连接仪器；「对齐注入幅度」将幅度设为设计波形峰峰值，绝对真值始终使用实际幅度。
在 A11「生成与上传 Z 噪声任意波」的基础参数中编辑「K_Z 手动标定（Hz/V）」；
修改后预览使用新值，存活会话应用参数会使 A11～A14 失效，需要显式重跑。

基础参数名称常显，展开节点编辑，高级参数单独折叠；多个节点可同时展开。
同键共享过程参数及基础/高级分类同步，例如恢复温控等待时间同时出现在校相与配对扫描节点。
初始化条件只在配置节点提供入口。修改存活会话参数先显示草稿与预计失效范围，
空闲时「应用参数到会话」，然后显式勾选重跑；保存默认值和预检不自动重跑。

分析对象默认是下一次实验的自动分析设置，也可选择某个已结束的扫描/历史目录。
具体目录保留独立分析草稿，切换目录或主 Tab 后仍保留；执行分析时应用该目录覆盖值。
「保存参数与布局」只保存下一次实验参数和统一分类，不保存正在编辑的历史覆盖值。
历史填回只更新下一次实验草稿，不自动保存或启动。谱段数组以逗号分隔编辑，三组须等长。
旧历史参数没有手动 K_Z 键时，填回将此字段设为 0，需人工输入有效标定后才能启动；旧运行自身的离线分析仍使用原快照。

### 采集节点

| 节点 | 业务 | 前置 |
| --- | --- | --- |
| A01 | 启动预检 | 无 |
| A02 | 设备连接 | A01 |
| A03 | 参考时钟同步并严格回读 | A02 |
| A04 | 初始输出设置 | A03 |
| A05 | 目标温度设置 | A04 |
| A06 | Pump 配置 | A05 |
| A07 | 共用触发配置 | A06 |
| A08 | Demod 配置与校相 | A07 |
| A09 | XY 正弦与 Burst 初始化 | A08 |
| A10 | XY 迭代校相与质量门槛 | A09 |
| A11 | 使用手动 K_Z，生成、上传 Z 任意波，安全检查与回读 | A10 |
| A12 | DAQ 准备 | A11 |
| A13 | ON/OFF 配对扫描 | A12 |
| A14 | 恢复校相解调器设置 | A13 |
| A15 | 安全收尾并结束会话 | 无 |

A01～A10 复用正弦实验，校相未收敛标记 `quality_failed`，禁止配置 DAQ 和扫描，允许调整后重试。
A11 上传任意波后检查形状、点数、频率、幅度、偏置、电压单位和负载，Z 保持 OFF。
A13 只回读 XY、Z 与 DAQ 设置，拒绝实际模式漂移，不重传任意波。
DAQ、校相与恢复均使用本轮 `HF2_DEMOD_IDX`；高阻、DC 耦合初始化保持不变。

每个控制点按 OFF→调幅→校准相位→ON 等共用外触发，关闭温度开关后完成两态。
相邻点交替 OFF→ON / ON→OFF；每态均等待 `SYSTEM_SETTLE_S`，每态缓存波形和主机时间，整对恢复温控后保存。
每点后恢复温控并等待，扫描、稳定等待和采集提供取消检查。
异常先尝试恢复温控，再尽力保存已采段；重扫创建新目录，原始目录不覆盖。
A14 后再次扫描必须重新执行 A12。等待下一步保持连接、输出与全局硬件锁，刷新恢复活会话；
后台重启不从完成记录恢复硬件。空闲时 A15/结束会话，执行中使用取消；通信结束自动安全收尾。

### 分析节点

| 节点 | 业务 | 前置 |
| --- | --- | --- |
| B01 | 加载本轮配置与原始数据 | 无 |
| B02 | ON/OFF Welch PSD | B01 |
| B03 | OFF 移动脊线重标定 | B02 |
| B04 | 全控制轴两态拟合、谱分离及背景验收 | B03 |
| B05 | 选择静磁场曲线并拟合检测增益 | B01 |
| B06 | 增益换算、注入真值比较及汇总 | B04、B05 |

未选来源时完整运行完成采集、收尾及 B01～B04，摘要为 `waiting_calibration`，
B05/B06 显示「待选择标定曲线」。选择曲线后可直接执行 B05/B06；有预设来源时自动执行全部分析。
B05 可在 B01 后独立运行，不要求先做 PSD。来源变化只使 B05/B06 失效，PSD、脊线及谱分离保留。
参数、来源和产物指纹变化按真实依赖传播；重算归档受影响产物，不移动仍使用的前置或 raw。
阶段失败保存诊断，完成分析与定量判据通过分别展示。

`ANALYSIS_FIT_HALF_WIDTH_HZ` 约束峰定位、覆盖和质量门槛；拟合仍使用有效支持区间的完整控制轴。
OFF 脊线搜索覆盖保存的扫描目标终点，并受实际 Nyquist 限制；最终谱只输出指定分析频带。
目标范围不替代实测 K/B、不放宽门槛或外推支持区间。旧背景半窗口键加载时忽略。

架构 Tab 提供总览、采集、单点时序和数据分析子图；
[可编辑 Excalidraw 原图](../lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/XY%20已知可控噪声谱.excalidraw)
由 `/api/experiments/noise-spectrum-xy-known-noise/workflow/diagram` 下载。

### 信号链与模块

```text
Z_magnetic_field CH1 → Z 小线圈（连续任意波，无 Burst）
Time_sequence_2 CH2 → 三通 → X/Y 信号源 Ext Trig
所选锁相解调器 sample.y → DAQ → 每点 off/on 波形
静磁场 v3 历史 raw/dispersion_scan.npz → B05（离线、只读）
```

仪器型号、resource、通道和参考时钟以当前 `params/devices.yaml`、`params/mapping.yaml` 为准。
人工确认 Z 电压定义与手动输入 K_Z 的标定链路一致，软件不能核验物理接线或实际线圈负载。
信号源高阻使用 `INF`，回读查询标记通过 `normalize_output_load()` 归一化。
准备、激励、配对扫描分别位于 `preparation.py`、`excitation.py`、`acquisition.py`；
`workflow.py`/`session.py` 编排完整运行和同一分步业务，`analysis.py` 管理阶段与持久化，
科学算法在 `analysis_steps.py`/`analysis_core.py`，来源检查在 `calibration_source.py`。
模块导入、schema、预检和页面加载不连接真实硬件。Probe AM 保留独立旧采集入口及页面行为。

## 输出数据

### 运行目录结构

```text
data/Noise_Spectrum_XY_Ctrl_Known_Noise/MMDD_HHMM_tag/
  experiment_config.yaml           # schema 4；known_noise / truth_chain 本轮手动 K_Z 快照
  acquisition_state.json           # 会话执行记录
  analysis_state.json              # 分析覆盖值、修订、指纹与阶段状态
  raw/
    control_scan_axes.npz
    known_noise_waveform.npy
    waveform_C0000_off.npy
    waveform_C0000_on.npy
    waveform_C0000_{off,on}_acquisition.npz  # 每段实际采样率、时间轴与采集快照
    pair_timing.jsonl               # 各段状态与主机时间，非硬件触发时间
  results/
    psd_matrices.npz                # B02 两态 PSD 和成对行号
    known_noise_preview.npz/png/pdf
    calibration.npz                # B03 OFF 脊线、杂散掩码和支持区间
    popt_fit.npz                    # B04 两态参数、拒绝原因及质量掩码
    separated_spectra.npz           # B04 保存谱分离数组，供独立 B06 复用
    fit_diagnostics.npz
    dispersion_source.npz/json      # B05 来源曲线、拟合、条件和 SHA-256
    dispersion_scan.png/pdf         # B05 曲线和拟合
    noise_spectra.npz/csv           # B06 测得谱、真值、比值、掩码、两态背景
    controlled_uncontrolled_comparison.png/pdf
    measured_vs_truth.png/pdf
    analysis_summary.json
    before_<timestamp>/            # 仅归档受影响阶段产物
```

B03/B04 还输出二维谱、差分热图、线形拟合及分离响应系数图。新运行没有内置色散 raw；
旧运行 `raw/dispersion_scan.npz` 和 `dispersion_steps/` 保留只读离线兼容。
无来源运行可下载 PSD、脊线和谱分离结果，不生成伪增益或伪真值比较。
离线分析只读取保存的采集旁文件；实际采样率不一致时拒绝合并 PSD，旧数据继续使用本轮配置中的实际速率。

架构图反映当前工作区草稿，源码未提交冻结，不绑定旧 commit；
总览已同步手动 K_Z 输入，当前[图规格](../GUI/frontend/public/architecture/noise-spectrum-xy-known-noise.json)
与[HTML 产物](../GUI/frontend/public/architecture/noise-spectrum-xy-known-noise.html)保持一致。
整套子图的历史 SHA-256 与浏览器/感知复核见[历史交付验证记录](xy_known_noise_workflow_validation.json)。

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
- 重算按真实依赖仅归档受影响结果至 `before_<timestamp>/`；原始数据和未受影响结果保留，分析覆盖值保存在独立状态文件。

### 增益解释与历史兼容

本次将增益来源移到分析阶段并增加交互节点，继续保留既有
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
- [历史经验] 旧内置色散的 DC 切换会退出任意波模式；新流程采集阶段不切换到色散 DC
- [经验] 色散斜率取全部有效点线形拟合的零交叉点斜率；中心段线性拟合在两端偏离线性区时带系统偏差，
  线形拟合至少需要 7 个有效点，不提供端点剔除参数
- [经验] `0922_1725/1756_noise` 的中心段线性拟合给出 1.04–1.05 V/V，而同一份原始数据的
  线形拟合给出 6.44–6.49 V/V（R²≈0.995），差约 6.2 倍；离线上限必须用重拟合结果，
  否则 `S_meas ∝ 1/σ²` 会把绝对定量压低约 38 倍
- [经验] 判据只在注入带内评估。此前实现把带外点混入中位数（本次运行带外真值仅 1e-12 Hz²/Hz），
  中位比值被抬到 3e6–1e8；限带后同一数据给出 3.9 与 17.0
- [经验] 强注入的磁响应尾部会污染局部拟合底座；应使用完整有效控制轴并检验远端预测，不强制 ON/OFF 背景相等。
- [经验] 背景无法独立约束时输出 NaN 与原因；按带内/带外分别报告可辨识覆盖率，不能用全频中位数代替。
- [历史经验] 旧内置色散采用分组温控防止长时间关闭温控；新流程只在两态配对窗口关闭温控
- [经验] 可控/不可控对比图中不可控噪声随注入开关变化时，先排查链路与差分假设，再采信绝对定量结论
- [经验] 注入幅度半幅小于设计波形峰值时谱按平方缩放，预览给出的 realized_psd_scale 即缩放系数
- [经验] 增益公式 A/(γ²σ²) 的静态极限连接是当前唯一待理论确认环节，摘要中保留提示

## 统一安全收尾与验证

正常完整运行、异常、取消、Ctrl+C、通信 EOF 和后端关闭均执行统一安全收尾：
归零并关闭 X/Y、Z 噪声及共用触发 `Time_sequence_2`，恢复温控为 5 V DC + ON。
主磁场、Pump/Probe 光功率、Pump 载波与门控沿用既有保留策略，只断开 TEC。
本实验显式覆盖共享步骤默认的时序保留策略，不改变其他实验的输出策略。
安全动作逐项尝试并记录失败，原始错误保留；设备失联时软件只能报告关闭失败，需人工核查输出。

假仪器回归覆盖依赖、校相门槛、配对保存、新目录、参数失效、时钟故障、异常与取消收尾；
真实子进程会话/API 覆盖硬件锁、修订、刷新、结束及 EOF。
分析回归覆盖整次/分步一致、等待标定、无效来源拒绝、来源切换和历史兼容；
手动 K_Z 回归覆盖无标定目录预检/预览、有限正数校验、采集快照、默认值独立性及旧来源回退；
页面从真实路由验证节点、共享参数、布局、历史草稿隔离，并回归正弦和 Probe AM。

```powershell
agent_exp_env/Scripts/python.exe -m unittest lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_workflow lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_analysis lab_workflows.experiment_modules.noise_spectrum_xy_known_noise.test_acquisition GUI.backend.test_noise_spectrum_xy_known_noise
```

自动化验证不连接真实硬件；实际触发时序、通信故障后的物理输出及跨条件增益精度仍需实验确认。
