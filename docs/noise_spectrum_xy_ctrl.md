---
title: X/Y 交流控制噪声谱测量
type: Noise_Spectrum_XY_Ctrl
execution_mode: typed_workflow
description: 以 14 个采集模块和 5 个分析模块组织 XY 正弦噪声谱；运行页按五组流程显式选择步骤、检查依赖并复用硬件会话，XY 校相未收敛禁止扫描；扫描 X/Y 正弦峰值测量 PSD，每点关闭两路输出后调幅，按已校准的 Burst 相位重新开启并等待共用外触发；设置每点采集时长与频率格间距，自动计算 Welch 段长；标定或拟合失败仍保留二维 PSD，标定未通过时纵轴为实际扫描电压；与已知可控噪声、Probe AM 实验共用全控制轴分析，独立验收受控响应与不可控背景；分离谱排除 0 Hz，低频扫描边界使用固定线宽/偏移的边界模型，方法保存在结果数据中，图中不额外标记，不对失败点插值外推。
keywords: [noise spectrum, sine, XY control, PSD, Welch, Lorentzian, noise spectroscopy, 锁相放大器 DAQ]
version: 6
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
    role: off
    description: "同一台 DG4000 CH1，本实验不使用并保持关闭"
  Time_sequence_2:
    role: trigger
    description: "同一台 DG4000 CH2，输出固定方波并经三通触发 dg_comp CH1/CH2 Ext Trig"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流 ~9.3 mA），实验过程中固定不变"
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
    role: phase_calibration + noise_acquisition
    description: "锁相放大器：相位校准 + DAQ 模块采集解调输出 Y 信号"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - temperature
  - Temp_Switch
  - Z_magnetic_field

# ========== 修复经验 ==========
learned_notes:
  - 连接并同步时钟后，初始化、校相、扫描和采集数据写入统一用 try/finally 包裹，确保异常时恢复温度开关并关闭扫描输出
  - 目标 Omega_ctrl 使用 V_env = (Omega_ctrl - B) / K 反算
  - 目标范围反算后必须位于手动填写的标定有效电压范围内
  - 离线分析从 PSD 脊线重新标定 K/B，不使用采集配置中的预设 K/B
  - 脊线由二维背景归一化后的移动峰共识定位，再用鲁棒直线拟合；显著性、覆盖、残差和分段斜率须同时验收
  - dg_comp 两路只初始化一次正弦 Burst，每点关闭两路输出后用 set_amplitude() 调幅，再按已校准相位重新开启等待共用外触发
  - Time_sequence_2 CH2 在正弦校相前打开并初始化，之后保持连续输出
  - 相位更新采用 Output OFF →更新 Burst phase → Output ON，两路等待同一外触发沿
  - 外部触发同步：Time_sequence_2 CH2 方波经三通 → dg_comp CH1/CH2 Ext Trig
  - Demod0 与 X/Y 正弦校相使用 lab_workflows.steps 中的共享算法，避免各实验的更新方向和终止条件漂移
  - X/Y 校相拒绝低于绝对下限或历史最佳 R 的 10% 的样本，不用弱信号 theta 更新相位
  - 共享全控制轴拟合限制正值和线宽边界，独立验收峰与背景；峰失败不连带否定稳定的远端背景
  - 低频控制轴边界没有双侧点时，使用内部已验收线宽/偏移的固定形状拟合并写入 `fit_mode`，其他失败点保留 NaN 和拒绝原因
  - 用 .npz 替代 CSV 保存数据，保留 NaN 且 I/O 更快
  - 提取的噪声谱保存为 noise_spectra.npz，供最优控制设计 Notebook 加载
  - XY 控制频率与 Pump 调制频率相同，当前值见参数文件
experiment_id: noise-spectrum-xy
defaults_file: params/experiments/noise-spectrum-xy.yaml
---

# X/Y 交流控制噪声谱测量

当前默认参数：[参数文件](../params/experiments/noise-spectrum-xy.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

本实验已迁移到强类型新模式。显式参数模型、采集工作流和离线分析器位于
`lab_workflows/experiment_modules/noise_spectrum_xy/`；
`experiments/Noise_Spectrum_XY_Ctrl.py` 与对应 plot 文件仅为薄入口。GUI 表单字段
只来自模型声明，不会再因工作流中新增全大写运行时常量而发生变化；历史大写键和
`FIXED_PARAMS.*` 键继续兼容。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入；校相与 DAQ 共用 Demod0，HF2_DEMOD_IDX 必须为 0。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 代码入口与运行方式

采集模块提供 `run(params: NoiseSpectrumXYParams, *, project_root: Path | None = None) -> Path`，
成功后返回本次运行目录。`main()` 只加载运行参数并调用 `run()`；GUI 和原有薄入口
仍通过适配器在独立子进程中执行模块。直接导入参数模型或工作流不会读取运行配置、
连接仪器、创建数据目录或切换绘图后端。包级 `ADAPTER` / `DEFINITION` 在访问时加载。

在仓库根目录使用现有薄入口启动采集或离线分析：

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Noise_Spectrum_XY_Ctrl.py
.\agent_exp_env\Scripts\python.exe experiments\Noise_Spectrum_XY_Ctrl_plot.py
```

采集按 `preparation.py`（预检、连接、初始条件）、`excitation.py`（Pump 与触发）、
`phase.py`（锁相与 XY 校相）、`acquisition.py`（DAQ 与逐点扫描）组织；
`stages.py` 为脚本、API 和 GUI 共用的模块清单，`workflow.py` 编排同一套步骤，
`session.py` 处理子进程串行命令。分析由 `analysis.py` 的五个显式阶段完成。

程序内调用 `run(params)` 时，参数验证先于设备连接；省略 `project_root` 时根据模块位置
定位项目。设备、校准相位和实际采样率只保存在本次调用中，不修改传入模型。
`parameters` 保存原始输入相位，`xy_ctrl` 保存本次校准相位和诊断记录。离线分析继续使用
`analysis.analyze(run_dir)`，不依赖仪器连接。

## GUI 使用

实验中心中的专属页面保留“运行 / 历史 / 架构”三个 Tab：

- **运行**：编辑并保存参数与布局，查看 Welch 预览；在两列交互流程图中创建分步会话、勾选模块、运行所选或重跑、应用参数及手动结束；节点显示依赖、状态、错误和产物，任务显示日志、ETA、当前模块与输出状态。原“预检并运行实验”仍连续执行、收尾并自动分析。高级参数中的“恢复温控等待时间”（`TEMP_SWITCH_ON_SETTLE_S`，秒，允许为 0）控制校相测量与每点采集后开启温控的等待时长；等待中支持取消。
- **历史**：分页浏览本实验运行目录，读取各次 `experiment_config.yaml` 中的参数快照和 `results/` 中真实存在的图与数据文件；可把当前 schema 仍识别的参数填回运行页，也可启动离线重新分析。浏览和填回不会自动保存默认值或启动采集。
- **架构**：保留现有采集架构图；运行页提供可编辑 [Excalidraw 原图](../lab_workflows/experiment_modules/noise_spectrum_xy/XY%20控制测量噪声谱（正弦）.excalidraw) 下载入口。

切换主 Tab 不会清空尚未保存的参数或当前任务。历史详情缺少某张结果图时仅显示已有产物，不用当前默认值或占位指标补齐。

运行及历史结果显示标定支持点数、残差、通过验收的频率点数和解释警告；“分析完成”仅表示流程完成，并不代表全频带均可定量使用。标定失败显示“质量验收未通过”，同时展示已保存的二维噪声谱；未标定时纵轴为实际扫描电压，标定通过后的拟合失败则保留控制频率纵轴。可下载 `analysis_summary.json` 查看实际分析参数、二维图轴含义、排除原因计数和旧结果归档位置。

### 采集时长与频率格间距

基础参数只保留两项日常分段设置：

| GUI 参数 | 外部键 | 含义 |
| --- | --- | --- |
| 每点采集时长（s） | `HF2_DAQ_DURATION` | 每个控制点的波形时长；不是整轮扫描时长 |
| 频率格间距（Hz） | `ANALYSIS_BIN_WIDTH_HZ` | 唯一 Welch 分段设置；实际格间距因整数取整可略小 |

`HF2_DAQ_RATE`（DAQ 采样率）放入高级参数。新采集使用 schema v3，不再提供 `HF2_NPERSEG` 输入；
段长自动取 `max(2, ceil(actual_rate / ANALYSIS_BIN_WIDTH_HZ))`，采用 Hann 窗、50% 重叠。
运行页只读预览每段点数、每点平均段数、实际可达格间距及扫描采集与等待耗时；预览使用请求采样率，
不连接仪器。耗时包含幅度更新后固定等待、每点稳定和恢复温控等待，不含初始化、校相、通信及离线分析。
重叠段数不是统计独立平均次数。运行快照记录硬件实际采样率和派生段长，分析摘要按有效原始波形长度
记录平均段数范围；结果页显示实际数值，旧摘要缺少段数时显示“未记录”。

段长超过单次可采点数时，预检明确提示增加时长或增大格间距；实际采样率回读后和离线分析时再次校验，
不静默缩短段长。增加采集时长不会自动加长 Welch 分段；减小格间距则加长分段、减少可平均段数。

高级分析参数 `ANALYSIS_FREQUENCY_MIN_HZ` / `ANALYSIS_FREQUENCY_MAX_HZ` 是移动峰搜索及分离频带（Hz）；即使下限为 0，分析仍排除 0 Hz，只使用范围内的正频率；
`ANALYSIS_FIT_HALF_WIDTH_HZ` 是控制轴上的峰定位半窗口（Hz），约束峰区与参数边界，并定义独立背景平台需避开的共振区；实际拟合覆盖完整有效控制轴。分析参数不改变仪器设置。

**旧配置兼容**：历史运行 schema v1/v2（或无版本）重新分析仍取
`max(原 nperseg, ceil(actual_rate / ANALYSIS_BIN_WIDTH_HZ))`；缺少分析字段时使用模型回退值。
旧 YAML 用于新采集时移除 `HF2_NPERSEG`，显式格间距优先；若未提供格间距，以
`min(模型回退格间距, 请求采样率 / 旧段长)` 迁移。GUI 历史“填回参数”只填回当前可编辑字段，
旧段长不再填回，并提示新采集按表单格间距计算。原始数据与历史配置不改写；实际分段规则写入分析摘要。

## 论文对照

本文档对应论文 `paper/optimal_control.tex` 的噪声谱估计实验部分：

| 论文中的符号 | 实验实现 | 说明 |
|-------------|---------|------|
| $\Omega_\text{Ctrl}$ | GUI 中设置的目标控制频率 | 采集时由手动参数反算正弦峰值；分析时由 PSD 脊线重新标定 |
| $S_{S_1}(\omega, \Omega_\text{Ctrl})$ | Welch PSD of HF2 解调 Y 信号 | 每个目标控制频率点采集的噪声 PSD |
| $S_\beta(\omega)$ | 可调噪声（可控噪声） | 二维拟合提取的目标 1 |
| $N_{S_1}(\omega)$ | 不可调噪声（不可控噪声 + 电子噪声） | 二维拟合提取的目标 2 |
| $L(\omega, \Omega_\text{Ctrl})$ | 洛伦兹滤波函数 | 控制场调制的系统响应函数 |

## 原理

在 Bell-Bloom 磁力仪中，探测光 $S_1$ 的功率谱密度由可控噪声和不可控噪声共同贡献：

$$S_{S_1}(\omega) = N_{S_1}(\omega) + 4G^2 S_2^2 L(\omega, \Omega_\text{Ctrl}) S_\beta(\omega)$$

通过扫描目标控制频率 $\Omega_\text{Ctrl}$，在不同控制强度下 $S_\beta(\omega)$ 受到 $L(\omega, \Omega_\text{Ctrl})$ 的不同调制，而 $N_{S_1}(\omega)$ 保持不变。通过二维拟合同时提取二者。

### X/Y 交流控制信号

X/Y 两路使用 DG4000 的普通正弦模式，直流偏置固定为 0 V：

```
X 通道: V_peak · sin(2π · f_ctrl · t + θ)
Y 通道: V_peak · sin(2π · f_ctrl · t + θ + φ_quad)
```

仪器幅度参数为 `2·V_peak Vpp`。工作流在校相前只调用一次 `setup_sine()` 配置波形和外触发无限 Burst；扫描每点先关闭两路输出，再用 `set_amplitude()` 调幅，随后重新写入已校准的两路 Burst 相位并开启输出，等待共用外触发。普通正弦、无限周期 Burst 和外触发模式不重复配置。

仅改变幅度不能保证实际响应相位保持不变：仪器对照发现往返调幅后响应状态累积变化，而按原相位重新触发可恢复。逐点重新触发用于抑制这一调幅历史依赖，不等于每点重新执行相位反馈校准，也不是把触发源反复关闭再启动。

`Time_sequence_2 CH2` 先打开并初始化，之后在校相和扫描期间保持连续输出；其 BNC 输出经三通同时连接 `dg_comp CH1/CH2 Ext Trig`。θ 在相位校准中迭代确定。

### 相位校准流程

分两步：

1. **锁相放大器 Demod0 校相**：共享 `calibrate_demod_phase()` 读取 theta、更新 Demod0 phase shift，将信号旋转到 X 通道（Y ≈ 0）
2. **XY_CTRL_PHASE 迭代校准**：共享相位反馈算法每次先关闭 X/Y Output，再把修正后的相位写入两路 Burst phase，最后重新打开输出等待同一外触发正沿，直到 锁相放大器 相位偏移收敛（|偏移| < 1°）

X/Y 正弦校相每次测量还会检查 `R`：阈值取 GUI 中的绝对下限
`XY_PHASE_CAL_MIN_R_V` 与“本轮历史最佳 R × XY_PHASE_CAL_MIN_R_RATIO”两者中的较大值。
低于阈值的 theta 会记录为 `low_r_rejected`，但不会用于更新相位。
若达到最大次数仍未收敛，A10 标记质量未通过，保留最终相位、低 R 拒绝和测量历史；
分步会话保留本步骤结束时的输出，允许调整参数后重试，A11/A12 禁止执行。完整运行入口会自动收尾并报错。
校相容差取 `XY_PHASE_CAL_TOL_DEG`，不是文档固定数值。

### 正弦峰值采集标定与离线重标定

正弦峰值电压绝对值与有效控制频率 $\Omega_\text{Ctrl}$（Hz）之间采用线性标定：

$$\Omega_\text{Ctrl} = K \cdot |V_\text{env}| + B$$

运行前在 GUI 中手动填写 `K`、`B` 和标定有效包络范围，用于生成采集扫描轴。目标轴按

$$|V_\text{env}| = (\Omega_\text{Ctrl}-B)/K$$

反算。任一点超出标定有效范围或 X/Y 安全电压范围时，预检直接报错并提示修改目标噪声谱频率范围。

离线分析不把上述预设 `K/B` 当作最终标定。每次从原始波形重算 Welch，不无条件加载旧 PSD 缓存。缺失或无效波形保留原行编号，不能使后续波形与电压错位。

定位副本先取对数、减去逐频率背景，再做轻度平滑；跨控制点持续存在的窄线由中位谱相对局部背景识别并加保护带，而非硬编码 25 kHz。逐电压最多保留三个显著候选峰，以跨电压直线共识选择移动轨迹；强度接近的多轨迹会拒绝标定，要求缩小频带复核。随后用 Theil-Sen 和 MAD 细化，独立验收支持点比例、扫描覆盖、绝对残差上限与前后半段斜率一致性。阈值不能随随机点云的 MAD 无限扩大。常规四参数分离只在移动脊线的有效支持范围内报告；低端若频率仍落在实际控制扫描轴内，则进入单独的边界模型，不把脊线支持范围外的数据当作普通四参数结果。

失败时保存 `analysis_summary.json` 的原因、已计算的原始 PSD 及按扫描电压绘制的 `noise_spectrum_2d.png/.pdf`，不生成虚假的控制轴或继续分离。成功时峰候选、保留掩码、支持范围和残差写入 `calibration.npz`，二维谱更新为标定控制频率纵轴；标定图保留实际电压坐标，避免错误控制轴掩盖问题。归一化、定位平滑和杂散位置插值都不用于定量 PSD 拟合。

### 噪声参数拟合

正弦噪声谱、已知可控噪声谱、已知不可控 Probe AM 噪声谱统一调用 `noise_spectrum_xy.processing.fit_local_spectra(..., full_range=True)`，使用同一模型和验收规则。各自入口负责读取单态/配对数据、增益换算或实验专属统计。主结果使用完整有效控制轴，`S_beta` 与 `N_S1` 独立验收；峰不可辨识不再强制背景为空。历史 `*_full_range` 键保留为当前主结果的兼容别名。

在通过标定的频带内，排除固定窄线后，对原始线性 PSD 的完整有效控制轴做正值约束洛伦兹拟合。低频边界点仍使用实测控制轴数据，但因共振峰落在扫描边界外，不能同时自由确定线宽和频移：

$$S_{S_1}(\Omega_\text{Ctrl}; \omega) = N_{S_1}(\omega) + A \cdot \frac{\gamma^2 + \omega^2}{(\gamma^2 - \omega^2 + (\Omega_\text{Ctrl} + \Delta\omega)^2)^2 + 4\omega^2\gamma^2}$$

- 支持范围内的点使用四参数拟合（`gamma`、`Amp`、`D`、`dw`）。
- 低频边界点使用扫描内部已通过四参数验收点的鲁棒中位数 `gamma` 和 `dw` 固定形状，只拟合 `Amp` 与 `D`；结果 `fit_mode=edge_fixed_shape`，不是四参数独立线宽测量。
- 边界点仍须通过全控制轴残差、奇偶留出、幅度稳定性和远端尾部留出；不能通过的点仍为 NaN。`background_method` 会区分 `edge_fixed_shape` 与单侧远端平台 `off_resonance_edge_plateau`。

| 拟合参数 | 符号 | 含义 |
|---------|------|------|
| `gamma` | $\gamma$ | 洛伦兹线宽 (Hz) |
| `Amp` | $A$ | 未去除增益因子的响应系数；本式频率按 Hz 代入，量纲为 V²·Hz，不是绝对磁噪声 PSD |
| `D` | $N_{S_1}(\omega)$ | 基线噪声 = 不可控噪声 + 电子噪声 (V²/Hz) |
| `dw` | $\Delta\omega$ | 控制频率偏移 (Hz) |

以峰高参数化避免数值尺度病态，在线性 PSD 模型的对数残差上做稳健优化。背景、幅度非负，线宽和频移有界；边界解、弱峰、残差过大、参数不确定度过大均拒绝。每列按奇偶控制点拆分，两折分别重新拟合全部响应参数并预测留出点，检查预测误差及幅度一致性。

背景首先使用通过远端锚点、背景参数误差及整块尾部留出验收的模型底座，来源标记为 `lorentzian_fit`。不能通过时独立检查远端平台：

- 常规频率只使用标定支持范围内、距分析频率超过 `ANALYSIS_FIT_HALF_WIDTH_HZ` 的控制点；左右两侧各至少 8 点。
- 每侧沿控制轴分成近、远两块。四块 PSD 中位数的极差除以全部远端点的中位数不得超过 20%，排除明显漂移与尚未衰减的尾部。
- 逐块留出，用其余三块的中位数预测该块；最大的中位相对误差不得超过 40%。低频边界没有左侧远端时，改用右侧尾部内部的两块做同样检验，来源记为 `off_resonance_edge_plateau`。
- 通过后，以全部远端点的中位数给出 `N_S1`。峰参数及其掩码不因此变为有效；两种背景方法均失败才保留背景 NaN。固定窄线和实际扫描轴之外始终无效。

平台估计依赖远端背景在控制轴上稳定的假设；一致性检查不能排除完全平坦的其他噪声贡献。`N_S1` 是测量端不可控背景，包含电子噪声。普通点的 `popt/perr` 描述四参数洛伦兹模型；`edge_fixed_shape` 点的线宽/偏移来自内部参考点，误差表示参考点离散度，不是独立四参数协方差。平台方法的 `N_S1` 不等于失败拟合中的 `popt[:,2]`，也不能套用该参数的误差。来源、远端点数、四块水平差异及留出误差均保存供复核。

Mx 主磁场示波器噪声谱的非负二维共同线宽模型作为附加对照复用，保存 `global_comparison.npz`；其条件交叉验证不等于全部参数的独立验证，不替换共享核心的验收结果，也不填补失败点。这样不强迫不同控制强度共用一个线宽。

分析自动保存 `results/noise_spectra.npz`，保留历史键名；低频边界估计通过质量门槛后写入主谱，同时以 `fit_mode` / `edge_fit_mask` 标记，调用方仍必须使用质量掩码，禁止填补 NaN：

```python
data = np.load("results/noise_spectra.npz")
S_beta = data["S_beta"]          # 历史键：响应系数 A，尚未除去增益因子
N_S1 = data["N_S1"]              # 不可控噪声谱 (V²/Hz)
freq_axis = data["freq_axis"]    # 频率轴 (Hz)
valid = data["quantitative_valid_mask"]  # 可控响应的有效掩码
edge = data["edge_fit_mask"]  # 低频固定形状边界点，方法记录保留，图中不额外标记
fit_mode = data["fit_mode"]  # four_parameter / edge_fixed_shape / rejected
background_valid = data["background_fit_mask"]  # 不可控背景独立验收
background_method = data["background_method"]  # 模型底座、边界模型或远端平台
```

## 数据采集方式

按参数文件中的范围与点数扫描目标 $\Omega_\text{Ctrl}$，每点反算 X/Y 包络并通过 HF2 DAQ 模块采集解调器 Y 信号。

### 信号链

```
锁相放大器 解调器 Y 输出（digital）→ DAQ 模块 → 时域波形
```

采集 `sample.y`（校相 + 偏置后的正交分量），包含可控噪声信息。

### 扫描策略

正弦波形和 Burst 模式只在扫描前配置一次。每个非零目标幅值点按以下顺序执行：

1. 校验正负峰值安全限值，关闭 X/Y 两路输出。
2. 把峰值换算为 `2·V_peak Vpp`，通过 `set_amplitude()` 写入两路。
3. 复用 `set_xy_sine_phase()`，写入校准后的 `XY_CTRL_PHASE` 和 `XY_CTRL_PHASE + XY_CTRL_QUAD`，再开启两路输出等待共用外触发。
4. 保留原有调幅后固定等待及关闭温控后的 `XY_SETTLE_TIME`，随后采集。

零幅值点保持两路 OFF，不发送零 Vpp 或重新开启。Pump 载波、Pump 门控和 `Time_sequence_2 CH2` 共用触发源在扫描期间始终保持运行。软件仍按现有等待时间采集，并未增加触发到达回读检测；须确保外触发接线正常。
新运行的 `experiment_config.yaml` 中 `xy_ctrl.trigger_policy` 记录逐点调幅重触发策略，历史运行配置与数据不改写。GUI 无新增参数，相位仍沿用扫描前的校准结果。

校相和采集关闭温控时直接把温度开关通道设置为 **Output OFF**。测量后通过
`FIXED_PARAMS.Temp_Switch` 恢复开启电平并设置 Output ON，再等待 `TEMP_SWITCH_ON_SETTLE_S`。
`XY_SETTLE_TIME` 仍表示正式采集前、关闭温控后的稳定时间。初始开启和最终安全收尾不额外等待恢复时长。

上述时序由 [采集工作流](../lab_workflows/experiment_modules/noise_spectrum_xy/workflow.py)
的 `run()` 编排，设备、参数和安全限值显式传入各步骤。初始化、校相与扫描共用一次
安全收尾；测量失败时恢复到最终安全状态，不再重新开启 XY。

### 外部触发同步

```
dg_mod (DG4E222800868):
  CH1: 100MHz 正弦波 → RF 开关 IN
  CH2: 脉冲门控（频率与占空比见单次运行参数）→ RF 开关 CTRL

dg_sweep（当前 Z / Time_sequence_2 映射设备）:
  CH1: Z 场保持关闭
  CH2 / Time_sequence_2: XY_TRIGGER_FREQ 方波 → 三通 → dg_comp CH1/CH2 Ext Trig

dg_comp（当前 X/Y 映射设备）:
  CH1/CH2: 正弦模式，Ext Trig 触发两路 Burst 同步启动
```

参考时钟目标以 `params/devices.yaml` 中当前绑定设备的 `reference_clock` 为准。
工作流统一设置并回读验证，
保证参考时钟配置与仓库声明一致。

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | 当前 X/Y 映射信号源 | CH1 | 正弦模式，频率与 Pump 调制相同，扫描幅度为 `2·V_peak Vpp` |
| Y 控制 | 当前 X/Y 映射信号源 | CH2 | 正弦模式，与 X 正交 90° |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| X/Y 外触发 | 当前 Z / 触发映射信号源 | CH2 / Time_sequence_2 → 三通 → dg_comp CH1/CH2 Ext Trig | 校相前打开并初始化，之后持续输出 |
| 主磁场 | GS200 | - | 恒流模式，电流见当轮参数快照 |
| Pump 光功率 | 当前光功率映射信号源 | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | 当前光功率映射信号源 | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | 当前温度开关映射信号源 | CH2 | 测量时 Output OFF；测量后恢复开启电平与 Output ON |
| 噪声采集 + 相位校准 | 锁相放大器 | 信号输入 0 | 解调器 + DAQ 模块 |

## 实验流程

A01～A13 按编号依赖前一模块，A14 无前置、随时可用于结束空闲会话；执行中使用任务“安全停止”取消并自动收尾。

| 分组 | ID | 可执行模块与结果 |
| --- | --- | --- |
| 启动准备 | A01 | 启动预检：参数、映射、锁相兼容、目标轴和限值，不连接硬件 |
| 启动准备 | A02 | 设备连接：按当前映射建立/复用设备会话 |
| 启动准备 | A03 | 参考时钟同步：设置并严格回读，保存实际结果 |
| 初始条件 | A04 | 初始输出：Pump/Probe DC、主磁场保护与输出、温度开关；关闭 Z 与尚未启动的共用触发 |
| 初始条件 | A05 | 温度稳定：设置 TEC 目标，等待并检查取消；TEC 连接失败沿用可选策略 |
| 激励与触发 | A06 | Pump 配置：载波、脉冲门控及对应输出 |
| 激励与触发 | A07 | 共用触发配置：Time_sequence_2 方波、开启输出并初始化相位 |
| 相位校准 | A08 | Demod0 配置与校相：关闭 XY/温控，配置输入/振荡器/解调器，校相并恢复温控 |
| 相位校准 | A09 | XY 正弦与 Burst 初始化：两路无限 Burst、共用外触发和初始相位 |
| 相位校准 | A10 | XY 迭代校相：低 R 拒绝、反馈及收敛验收；未通过禁止后续采集 |
| 扫描与结束 | A11 | DAQ 准备：切换参数、回读采样率、计算网格及 Welch 分段 |
| 扫描与结束 | A12 | 扫描采集：新建目录；逐点 OFF XY、调幅、按校准相位重触发；关温控、采 Y、保存波形并恢复温控 |
| 扫描与结束 | A13 | 解调器恢复：恢复校相时间常数/采样率/相位；使 A11 失效 |
| 扫描与结束 | A14 | 安全收尾：关闭 X/Y/Z、恢复温度开关，按既有策略清理连接并释放硬件锁 |

| ID | 分析模块 | 结果与质量门槛 |
| --- | --- | --- |
| B01 | 加载配置与准备分析 | 绑定已结束扫描或历史目录，准备 results、归档旧产物、初始化摘要 |
| B02 | 计算 Welch PSD | 按波形编号对齐控制点，保存矩阵、无效行和未标定二维谱 |
| B03 | 移动脊线重标定 | 杂散与移动峰识别、鲁棒 K/B 验收；失败保留二维谱，禁止 B04 |
| B04 | 全控制轴拟合与背景验收 | 分离谱、参数、独立质量掩码、拒绝原因、CSV 和诊断图；失败点保留 NaN |
| B05 | Mx 对照与分析汇总 | 条件满足时计算共同线宽对照；不足时记录跳过，完成摘要 |

B02～B05 依赖前一模块。每个分析模块结束或失败时保存摘要，不要求最后一步成功。

### 会话操作与重跑

1. 创建分步会话后显式勾选需要运行的模块。前置必须有效完成，或同时被选中；后端与子进程都会列出缺失模块并拒绝，不自动补齐。
2. 等待下一步时，任务仍处于运行状态，设备连接、输出及全局硬件锁保持。页面刷新通过后端存活会话恢复；后台重启后不从历史完成记录恢复硬件操作。
3. 参数只在会话空闲时可编辑。点击“应用参数到会话”重新全量预检，并按参数使用关系使受影响模块及后继失效；“仅预检”不使已完成的无关硬件步骤失效。请求携带修订号，过期、重复和执行冲突返回拒绝。
4. 扫描后可以检查、改参数和重跑。每轮 A12 新建 `MMDD_HHMM_<tag>`，重名追加 `_01`、`_02`，保存本轮输入与实际状态；此前 raw 和采集配置不改写。
5. A13 恢复后必须再次完成 A11 才能扫描。重跑前回读 XY 波形/Burst/外触发模式以及 DAQ 采样率、时间常数、相位；不匹配时终止并收尾。
6. 正常“结束会话”先补做必要的解调器恢复，再执行 A14。异常、取消、通信断开自动收尾，原始错误与清理结果分开保留。只有收尾完成后释放硬件锁。

### 分阶段分析和产物复用

运行页的分析列独立选择具体运行目录与分析参数，和硬件会话分开执行；同一目录的分阶段分析与旧“重新分析”共享互斥。
`analysis.analyze(run_dir)` 仍连续运行全部阶段；也可传入 `step_ids`、`analysis_parameters`、`expected_revision`。
`analysis_state.json` 保存输入指纹、算法版本、参数修订和阶段产物签名；输入/阶段记录不一致或产物改变时拒绝继续复用。
旧运行缺少阶段记录，从 B01 重建。B01 归档旧顶层结果；后续重算仅归档本阶段与后继产物，不移动仍在使用的有效 PSD/标定输入。改变 Welch 参数需重做 B02；改变拟合窗口需重做 B04。

## 输出数据

### 运行目录结构

```
data/Noise_Spectrum_XY_Ctrl/
  MMDD_HHMM_<tag>[_序号]/
    experiment_config.yaml      # 本轮参数与实际采集配置
    acquisition_state.json      # 会话阶段和输出状态诊断
    analysis_state.json         # 分阶段分析的输入/参数/产物记录
    raw/
      control_scan_axes.npz     # 目标频率轴、反算包络轴和手动 K/B
      waveform_C0000.npy        # 每点原始波形（四位编号，numpy 二进制）
      waveform_C0001.npy
      ...
    results/
      psd_matrix.npz            # PSD 矩阵（amplitudes × frequencies）+ 频率轴
      calibration.npz           # PSD 脊线重标定的 k、b、筛选掩码、剔除点及 Omega_ctrl 轴
      popt_fit.npz              # 有界拟合参数、近似误差、质量掩码及逐频率拒绝原因
      noise_spectra.npz         # ★ 提取的噪声谱（S_beta, N_S1, freq_axis）
      noise_spectra.csv          # 同上数据的文本版；列为 frequency_Hz, S_beta_controllable_V2_per_Hz, N_S1_uncontrollable_V2_per_Hz, valid, background_valid, edge_fixed_shape；排除 0 Hz（旧运行目录中该两列曾命名为 response_coefficient_A_V2_Hz 与 N_S1_V2_per_Hz）
      calibration.png           # 标定图（散点 + 拟合 + PSD 伪彩图 + 验证）
      noise_spectrum_2d.png/.pdf # 标定前按扫描电压绘图；通过后改用标定控制频率，失败仍保留
      noise_spectra_extracted.png # 提取的可控/不可控噪声谱
      psd_column_diagnostics.png # 实测局部剖面与模型对照
      fit_quality.png          # 残差、留出误差、线宽与频移
      fit_diagnostics.npz      # 分析频带内实测矩阵、模型及杂散掩码
      global_comparison.npz    # Mx 共同线宽二维模型对照
      background_identifiability.png/.pdf # 模型背景与独立远端平台的来源对比
      analysis_summary.json    # 分析版本、参数、数据清单和质量状态
      before_<timestamp>/     # 每次重分析前归档的旧顶层产物
```

### 分析结果

二维噪声谱绘制不依赖标定或洛伦兹拟合成功：只要所选正频率分析频带内存在有效 PSD，
就先保存 `noise_spectrum_2d.png/.pdf`。标定未通过时，纵轴为 `Sine peak voltage (V)`，
标题注明 `uncalibrated control axis`；不使用预设 K/B 伪装为实测标定。标定通过后，
同名图的纵轴更新为 `Calibrated control frequency (kHz)`，后续拟合失败也保留该图。
分析摘要的 `noise_spectrum_2d.control_axis` 和 `calibrated` 记录实际轴含义；
失败状态及原始错误照常保存，生成诊断图不表示标定或噪声分离已经通过。
GUI 的运行结果及历史详情在分析失败时仍展示已有二维图和错误信息。没有有效 PSD
或所选频带为空时不能生成二维图，摘要记录具体原因。

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重分析先归档旧顶层产物，保留原始波形、采集配置和已有诊断子目录。分析谱、CSV、标定与拟合诊断仅保存所选分析频带中的正频率，去掉 0 Hz；完整原始 Welch 频率轴（含 0 Hz）保留在 `psd_matrix.npz`，原始波形和运行配置不改写。`noise_spectra_extracted.png` 使用双 y 轴同时显示可控响应系数 `S_beta` 与不可控/背景噪声谱 `N_S1`；低频边界点使用同一分量的普通散点，不再添加三角标记或 `Low-frequency edge` 图例。背景诊断图按模型背景/远端平台分组，边界方法仍记录在 NPZ、CSV 和分析摘要中。为减少视觉空白，仅在图层中对两条谱的内部缺口做对数线性插值，接受点仍以散点标出，`noise_spectra.npz`、CSV 和质量掩码保持原始 NaN，不把插值结果用于定量分析。

每个频率点的 PSD 与控制强度的关系用洛伦兹函数拟合，输出参数：

| 参数 | 含义 |
|------|------|
| gamma | 洛伦兹线宽 (Hz) |
| Amp | $\propto S_\beta(\omega)$，未去除增益因子的响应系数 (V²·Hz) |
| D | $N_{S_1}(\omega)$，基线噪声 (V²/Hz) |
| dw | 频率偏移 (Hz) |

## 注意事项

- [经验] 实际异常由统一收尾保护；XY 未收敛在分步会话中保留状态供重试，并禁止扫描。
- 峰识别质量不能仅由拟合返回成功判断。`0921_1758_noise` 的逐列寻峰将噪声起伏
  混入标定，得到 `K≈166 Hz/V, B≈28.875 kHz`，其控制轴及后续噪声分解不可作定量解释。
  独立诊断与参考 Mx 二维模型的试算见
  [峰识别诊断报告](../data/Noise_Spectrum_XY_Ctrl/0921_1758_noise/results/peak_diagnosis/README.md)；
  该历史试算未改变当时的正式算法；现在的正式分析采用上述移动轨迹验收、全控制轴拟合与独立背景验收流程，但仍不自动修改采集 K/B。固定峰的物理来源需独立验证，
  未被移动共振覆盖的频段不能仅靠拟合或插值报告可控噪声。

## 统一安全收尾

完整运行自动收尾，分步模式等待手动结束；异常、取消、Ctrl+C 和子进程通信断开均通过
`run_safety_shutdown()` 尽力归零并关闭 X/Y 与 Z，恢复温度开关为 **5 V DC + Output ON**，只断开 TEC。
主磁场、Pump/Probe 光功率、Pump 载波、Time_sequence 门控以及 Time_sequence_2 共用外触发均保留既有输出策略。
关闭通信不等于关闭输出；连接或时钟同步失败沿用释放全部已建立连接的策略。
校相通信异常不会再次开启 XY；清理诊断不覆盖原始异常。单点 DAQ 失败保存 NaN 占位并继续，取消和 Ctrl+C 中断；波形编号不变。

A12 保存实际采样率与完整 data_files 后，A13 或正常结束操作恢复校相时间常数、采样率和校准相位，随后 A14 收尾。
异常路径不额外恢复锁相配置，保留故障时设置；共享收尾本身不修改解调器参数。
分步等待可持续输出并占用硬件锁，现场检查完成后应明确结束会话。
