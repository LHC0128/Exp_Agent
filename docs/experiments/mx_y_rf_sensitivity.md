---
title: Mx Y 向 RF 场灵敏度
type: Mx_Y_RF_Sensitivity
scan_mode: point_by_point
mapping_keys:
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - TEC103 为可选控制设备；设备库映射串口被外部温控软件占用时告警并跳过设温。
  - 主场与 Pump 光沿 Z，Probe 光沿 X，待测 RF 场沿 Y。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - Time_sequence 在所有结束路径都保持 5 V DC 和输出开启。
  - 采集与灵敏度分析只使用 Demod0 R，不采集 X/Y，也不执行相位校准。
  - 保留全局色散拟合导数法，并额外报告零点附近实测数据斜率法。
experiment_id: mx-y-rf-sensitivity
defaults_file: params/experiments/mx-y-rf-sensitivity.yaml
---

# Mx Y 向 RF 场灵敏度

主场与 Pump 光沿 Z、Probe 光沿 X，实验只采集锁相的 R，不执行 X/Y 相位校准。完整运行和交互流程图共用准备、激励、幅度响应、可选扫频线宽和零 RF 噪声业务；绝对值色散、两种斜率和灵敏度算法沿用既有科学方案。

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.mx_y_rf_sensitivity.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

离线分析保留类型、有限值及原模型分析字段上下限，不重新执行当前仪器输出与温控预检，不改写历史采集参数；采集仍执行完整安全校验。

## GUI 运行与历史

本实验清单显式声明 `run_step_id=A09`、`finish_step_id=A12`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

实验中心本实验页面包含「运行 / 历史」两个主 Tab。运行页的流程容器顶部提供保存参数与布局、派生预览、仅预检、完整运行和任务状态；采集列与独立分析列都从 `stages.py` 的清单加载，不另维护界面执行顺序。基础参数名常显，高级参数在节点内折叠，多个节点可以同时展开。

| 节点 | 职责与前置 |
| --- | --- |
| A01–A03 | 预检 → 设备连接与初态回读 → 参考时钟设置与严格回读 |
| A04–A06 | 主场、光功率与辅助场归零 → TEC 目标温度 → Pump 连续载波与常开门控 |
| A07 | 准备 Y RF 正弦与 R 解调；不做相位校准 |
| A08 | 在线宽模式为 `frequency_sweep` 时采集扫频并执行 Lorentzian 质量门槛；幅度等效模式记录无需扫频 |
| A09 | A08 通过后采集带符号幅度响应 |
| A10 | A09 后将 Y RF 归零关闭，采集 R 噪声 |
| A11 | A10 后恢复响应解调，执行本轮安全收尾并封存运行目录 |
| A12 | 无前置的安全结束；只结束会话，不要求采集完整 |
| B01 | 只读加载指定目录的采集参数与原始数据资格 |
| B02 | B01 后计算原有绝对色散、线宽、R PSD 与灵敏度，保存数值缓存与诊断 |
| B03 | B02 通过后读取其数值缓存，生成两种斜率的结果报告和英文图表 |

每行中连续节点依次依赖前一个节点；A04 依赖 A03，A07 依赖 A06，A08 依赖 A07。未选择且尚未完成的前置会明确拒绝，不自动补跑。R 点连续超限或扫频拟合未通过时标记 `quality_failed`、保留原始数据与诊断，并阻止后继。分析中的拟合质量失败同样保存诊断并阻止结果阶段；缺少线圈标定或平坦段不稳定仍按既有算法保留数值数组和原因，不伪造有效单值。

`SYSTEM_SETTLE_S`、`TEMP_SWITCH_ON_LAG_S` 和 R 点质量参数按真实使用出现在扫描/噪声节点，共用同一份参数值；主场、光功率等持续初始条件只在设置节点编辑。参数分类按键统一，移动分类不改变值。存活会话改参形成草稿，界面按后端 `parameter_impacts` 预览需重跑的已完成步骤；还原修改清除预览，应用参数不会自动执行步骤，应用失败保留草稿。

分步等待仍持有全局硬件锁并保持输出。空闲时用 A12 或「结束会话」，执行中用任务取消；取消、通信结束和后台关闭都先执行安全收尾再释放硬件锁。页面刷新恢复仍存活的会话，后台重启不从磁盘恢复硬件。A11 关闭 Y RF 后重新采集必须显式重新执行 A07；重跑建立新目录，保留旧 `raw/` 和采集参数快照。应用参数只使步骤失效，封存和结束均不把尚未重跑的 Pump 或 RF 新参数下发到硬件。

分析列默认编辑「下一次实验」的自动分析设置，完整运行使用同一份参数。选择具体运行目录后只编辑该目录支持的拟合、坏点筛选、标定、平坦段和斜率检查覆盖值；按目录保留草稿，不改变下一次实验参数或历史采集快照。保存默认值始终只保存下一次实验参数与分类。阶段记录保存原始输入、参数与产物指纹，输入变化或缓存缺失使相应阶段失效；重算仅归档受影响阶段及其后继产物。

历史页只读取本实验运行列表、当轮参数、分析状态和真实产物；缺少结果时显示未分析。填回参数切回运行页，不自动保存、采集或分析；可重新分析指定目录。可编辑源图位于 [workflow.excalidraw](../../lab_workflows/experiment_modules/mx_y_rf_sensitivity/workflow.excalidraw)，运行页提供下载。

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。扫频、幅度点和噪声段共用该等待；每个点或噪声段沿用原有独立温控窗口，质量重采也重新等待。本实验没有新增 OFF/ON 对照配对。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-y-rf-sensitivity.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 构型与信号链

- 主磁场沿 Z，Pump 光沿 Z，Probe 光沿 X。
- rf_coil 产生 Y 向 RF 场；X_magnetic_field 和 Z_magnetic_field 始终归零并关闭。
- Pump AOM 载波频率与幅度读取参数文件；统一负载策略将 Pump_modulation CH1 设为 50 Ω、Time_sequence CH2 设为 High-Z；Time_sequence 固定为 5 V DC 且输出开启，使 Pump 光常开。
- 锁相放大器 Demod0 使用 Input 1、DC 耦合、高阻、配置的输入量程，并以 Y RF 频率直接解调；DAQ 只订阅 R。
- 参考时钟遵循 `params/devices.yaml` 中当前绑定设备的 `reference_clock`。实验在任何输出配置前设置并回读验证，任一设备不一致即停止。

## 采集顺序

1. 模式 1 按 `FREQUENCY_START_HZ`、`FREQUENCY_STOP_HZ` 和 `FREQUENCY_POINTS` 扫频，并对 R 拟合带基线 Lorentzian；门槛不合格时停止后续采集。
2. 按 `Y_RF_AMP_START_VPP`、`Y_RF_AMP_STOP_VPP` 和 `Y_RF_AMP_POINTS` 扫描；负幅度通过硬件幅度取绝对值并增加 180° 相位实现。
3. 每个幅度点和模式 1 频率点都要求 std(R) 不超过 `R_BAD_POINT_STD_THRESHOLD_V`；超限时完整保存该次原始 R 并重新采集，达到 `R_POINT_MAX_ATTEMPTS` 仍失败则停止后继。
4. 先关闭 Y RF 输出，再将通道切换为 0 V DC 并再次确认 `Output OFF`，按 `NOISE_N_AVG` 和 `NOISE_DURATION_S` 采集 R 时间序列用于 PSD。每段噪声原始数据都记录 `y_rf_dc_v=0.0` 和 `y_rf_output_on=0`。

每个扫频点、幅度点和 PSD 段都按“设置 Y RF → 关闭温控开关 → 等待稳定 → 采集 → 恢复温控开关为 5 V DC + ON → 等待”的顺序执行。重采和异常路径也会恢复温控开关。

## 两种线宽

- frequency_sweep：由 R-Lorentzian 的 gamma 得到真实扫频 HWHM。
- amplitude_equivalent：由 R 的绝对值色散幅度响应拟合得到 gamma_Vpp。仅当 Y_RF_NT_PER_VPP > 0 时才换算为 Rabi/幅度等效 HWHM；该值不是扫频共振线宽。

幅度响应主拟合为

\[
R(V)=|A(V-V_0)/((V-V_0)^2+\gamma^2)+C|.
\]

拟合前按对应运行的标准差门槛剔除旧数据中的坏点；新数据在采集阶段已通过相同门控重采。原有主斜率取 |A|/gamma²，同时按 `LINEAR_CHECK_GAMMA_FRACTION` 定义的中心范围对 R 与 |V-V0| 做线性拟合诊断；两者差异超过 `SLOPE_AGREEMENT_TOLERANCE` 时给出警告。

分析器还并行计算 `adaptive_zero_point_absolute_linear` 方法：全局色散拟合只负责确定零点 V0，斜率不再取拟合曲线导数，而是在零点左右各选至少 2 个最近实测点，再按距离补足至少 5 点，对实测 R 与 |V-V0| 进行线性回归。局部回归的点数、左右点数、窗口、R² 和失败原因均写入结果；R² 低于全局拟合参考门槛时给出警告，但不会单独否决该方法。若全局零点不可信、零点任一侧点数不足、局部斜率不是正有限值或平坦段识别失败，则该方法无效。

## 灵敏度结果

主结果由零 Y RF 时的 R PSD 除以绝对值色散拟合得到的斜率幅值。若有有效 HWHM，则进行 Lorentzian 频率响应校正。单值灵敏度不再使用固定的 3 Hz 至 HWHM 频段，而是在校正后灵敏度谱上自动识别连续平坦段，再对该区间内未平滑的原始频谱值取中位数。`LOW_FREQ_SKIP_HZ` 只定义自动搜索允许使用的最低候选频率，不再直接作为平坦段下边界。

原方法继续写入 `sensitivity.npz` 和 `full_analysis.png`。零点实测数据斜率法独立写入 `sensitivity_zero_point.npz` 和 `full_analysis_zero_point.png`，并在 `analysis.yaml/json` 的 `zero_point_method` 下报告斜率、灵敏度单值、平坦段和有效性。两种方法共用同一份零 RF 噪声 PSD、HWHM 和平坦段识别算法，区别仅在响应斜率来源。公共保存接口同时生成同名矢量 PDF，GUI 保留 PNG 路径；图幅、默认无网格和导出规则遵循[科研绘图规范](../plotting_style.md)，不改变分析结果。

自动识别先对灵敏度取对数并做分箱中位数，以降低窄带尖峰的影响，再用带低频和高频非负铰链的鲁棒平台模型确定上下边界。分析器同时改变分箱数量和观察范围生成多组候选；只有候选中位数稳健 CV 不超过 3%、P10–P90 跨度不超过 8%、平台整体漂移不超过 20%、相对 MAD 不超过 15%，且平台后的高频上升证据不少于 3σ 时才报告单值。任一条件不满足时不回退到固定频段，而是在 `warnings` 中说明失败原因。检测边界、稳定性指标和判定结果写入 `analysis.yaml/json` 的 `flat_detection`，并以扁平字段写入 `sensitivity.npz`。

当 `Y_RF_NT_PER_VPP > 0` 时生成 `results/full_analysis.png`。图形使用项目统一的默认 `paper` 配置：上半部分绘制 Y RF 场幅度响应及绝对值色散拟合，下半部分绘制 `fT/√Hz` 原始与线宽校正灵敏度谱，并标出平坦频段、HWHM，以及以 flat 区间中位数绘制的实际灵敏度虚线；不再显示固定参考线。该中位数同时写入 `analysis.yaml/json` 和 `sensitivity.npz`。分析器不再生成 `sensitivity_voltage.png`；电压等效灵敏度仍保存在 `results/sensitivity.npz`，用于数值追溯。

线圈标定系数为 0 时仍保存 `Vpp/√Hz` 数值数组，但不生成磁场灵敏度图；模式 2 同时不报告等效 HWHM 和频段中位数。

原始数据位于 data/Mx_Y_RF_Sensitivity/<run>/raw/，结果位于同一运行目录的 results/。采集和离线分析入口分别为 experiments/Mx_Y_RF_Sensitivity.py 与 experiments/Mx_Y_RF_Sensitivity_plot.py。

新增运行记录 `raw_format_version=1`，与参数 `schema_version` 分开管理。原有 NPZ 文件名、键、单位和目录不变；带本实验身份、缺少格式标记的既有运行按原格式兼容，缺少 `r_mean_v` 时保留 `r_scalar_mean_v` 历史回退。未知身份和未知格式版本拒绝分析；不重写历史原始数据。`workflow.py` 保留旧设备配置、单工作点采集和安全收尾函数导出，供既有长飘、优化和控制实验继续调用。

## 安全例外

正常、取消和异常路径都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并恢复温控 5 V。Pump 连续载波和 Time_sequence=5 V DC + ON 是本实验的显式安全例外，所有路径均保留；正常结束只断开 TEC。

Pump 保留动作读取最近成功执行 A06 的载波频率、幅度和门控参数。首次 A06 之前结束时使用创建会话时的初始参数基线；后续「应用参数」不改变该基线，只有显式重跑 A06 成功后才更新。噪声后恢复响应解调和 Y RF 正弦时，同样读取最近成功执行 A07 或 A09 的响应配置，包含 RF 频率、初始幅度、解调器/振荡器索引、速率、时间常数和阶数，不使用尚未重跑的新值。噪声配置或读数中途失败也尝试这一恢复，然后继续执行安全归零关闭；原始错误和清理结果分别保留。上述执行配置只用于恢复，不覆盖历史 `parameters` 或当轮 `run_parameters`。

供长飘、功率与 Probe 优化复用的单工作点采集入口也保留原始异常：采集失败后，Y RF 归零或温控恢复再次失败时另记收尾诊断与结构化错误。调用方遇到收尾故障必须停止，不能按普通 R 点质量不合格继续扫描；只有收尾失败时仍抛出该错误，不能标为采集成功。
