---
title: Mx Z 最优控制 DG4000 偏置 XYZ 平衡场
type: Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  Z_magnetic_field:
    role: optimal_control_and_z_bias
  Time_sequence_2:
    role: optimal_control_trigger
  X_magnetic_field:
    role: x_dc_balance_scan
  Y_magnetic_field:
    role: y_dc_balance_scan
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_x_y_r_detection
required_devices:
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - GS200 必须与 Z 线圈物理断开；该实验不连接 GS200。
  - Z 总电压为最优控制波形电压加 DG4000 额外 offset。
  - 偏置到频率使用 K_Z_Hz_per_V，偏置到 Bz 使用 7.0 Hz/nT 报告。
  - 结果主判据为 Demod X/Y 复数稳健拟合；实测 Demod0 R 最小点只作为诊断保留。
  - 当前拟合是稳态 Bloch 型二维响应面，尚不是周期最优控制的严格 Bloch/Floquet 拟合。
experiment_id: mx-z-optimal-control-dg4000-bias-xyz-balance
defaults_file: params/experiments/mx-z-optimal-control-dg4000-bias-xyz-balance.yaml
---

# Mx Z 最优控制 DG4000 偏置 XYZ 平衡场

保持 Z 周期最优控制，以同一 DG4000 Z 通道的额外 offset 和 X/Y DC 构成三维蛇形网格；GS200 必须物理断开，本实验不连接它。同步采集所选 `DEMOD_IDX` 的 X/Y/R，保留原 soft-L1 多初值复数 Bloch 平面拟合与静态 Z 标定，实测 R 最小点只作诊断。运行/历史双 Tab 提供可勾选、按依赖执行的流程；不自动施加拟合平衡点。

## 采集时序与系统稳定

每个网格点及其每次重试均按“设置实际 X/Y DC 与 Z offset → 关闭 `Temp_Switch` → 等待唯一的 `SYSTEM_SETTLE_S` → 同步采集 X/Y/R 至内存 → 恢复温控 → 保存波形、采集元数据和质量记录”执行。目录、初始配置和来源快照在采集前完成；关闭窗口内不写盘。逐点边界保持不变，本实验没有额外校相、噪声或 OFF/ON 对照阶段。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。温控恢复和设备准备等待保留。复用 `acquisition_window` 与 `DeferredAcquisitionWrites`：取消或读取失败先恢复温控，已读帧在恢复尝试后抢救保存，保留原始异常；恢复或写盘失败停止后继采集。参数迁移和异常保护见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-dg4000-bias-xyz-balance.yaml)。GUI 流程顶部的「保存参数与布局」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 接线与计算

GS200 必须从该 Z 线圈断开。DG4000 的 `Z_magnetic_field` 通道同时承担
Z 周期最优控制任意波和额外 DC 偏置，`Time_sequence_2` 接该 DG4000 的
外部触发输入。

若最优控制波形为 `V_OC(t)`，扫描偏置为 `b`，则总输出为：

```text
V_Z(t; b) = V_OC(t) + b
f_Z(t; b) = f_0,Z + K_Z [V_OC(t) + b]
B_Z(t; b) = f_Z(t; b) / 7.0 Hz/nT
```

该换算只在同一线圈、线性输出、无削顶、无 AC 耦合且静态标定可代表工作频段时成立。
不能直接将 GS200 的 mA 与 DG4000 的 V 相加。

## 控制波形来源

参数 `CONTROL_WAVEFORM_SOURCE` 支持两种来源：

- `theory`：按 `CONTROL_VERSION` 读取理论 `Omega_ctrl` 波形，使用
  `K_Z_Hz_per_V` 换算 DG4000 电压；`CONTROL_SCALE`、`Z_AW_OUTPUT_VPP`
  和 `Z_AW_OUTPUT_OFFSET` 在此模式生效。
- `corrected_run`：读取
  `data/Z_AW_Closed_Loop_Waveform_Correction/<CORRECTED_CONTROL_SOURCE_RUN>/results/corrected_control_waveform.npz`，
  直接使用闭环冻结的电压波形、归一化数组、Vpp、offset 和重复频率，
  不再通过旧 Z 标定重新换算控制电压。

控制来源由参数文件选择的运行决定。无论使用哪种来源，
`Z_CALIBRATION_SOURCE_RUN` 仍用于将扫描的 DG4000 偏置换算为频率和 Bz，
并用于验证叠加偏置后的完整输出包络。每次运行都会在 `raw/` 保存来源快照、
`source_manifest.yaml` 和实际下发的 `applied_control_waveform.npz`。分步 A01 冻结来源指纹，A02 复制后核对 SHA；闭环模式同样额外冻结独立 Z 标定文件。来源、映射、设备库或安全限值改变时阻止继续执行，不静默换源。闭环模式的叠加包络以冻结波形实际 Vpp/offset 为准，不借用理论模式的幅度参数。

schema v1 历史配置缺少来源字段时自动迁移为 `theory`，保持原有实验行为。

## 扫描与结果

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

R 响应使用顺序色图；有符号的 Demod X/Y 使用以零为中心的对称发散色标。Bloch 汇总中的 Local R (mV) 与无量纲 Complex R² 分别使用左右轴，两者不能根据屏幕高度直接比较大小。用于比较的两个复残差幅值面共享从零到共同最大值的顺序色标；这里的复残差幅值为非负量。

实验以 Z 偏置为外层，X/Y 为连续蛇形扫描。每个点关闭温控开关后同步采集
锁相放大器选定解调器的 X、Y、R，并保存原始时间序列、均值、标准差和复噪声。`R_BAD_POINT_STD_THRESHOLD_V` 保留历史键，但实际重试资格为 `sqrt(std(X)^2 + std(Y)^2)`，不是 R 标准差；每次拒绝和接受的尝试均保留。重试用尽标记 `quality_failed`，不能执行封存节点。

离线结果对每个 Z 偏置平面同时拟合 Demod X/Y 的复数响应：两个分量共享 XY 平衡
中心和线宽，增益矩阵与输出偏置独立，并使用 soft-L1 损失降低孤立毛刺影响。各平面
中心的中位数作为 XY 拟合平衡点；实测网格 `mean(R)` 最小值仍输出在
`measured_grid_minimum` 中，供诊断和复核，不能直接当作真实磁场零点。

Z 方向的主结果仍由静态 `K_Z` 频率标定求零场，报告偏置电压、频率和绝对 Bz；这里的
偏置 Bz 是静态偏置坐标，不代表整个时变波形的瞬时 Bz。当前拟合属于稳态 Bloch 型二维
响应面，不是周期最优控制的严格 Bloch/Floquet 解；若要验证完整周期模型，需要进一步
实现周期稳态 Bloch 或 Floquet 求解器，并用同一套波形和线圈传递函数计算预测的 Demod X/Y。

结果文件包括 `bloch_fit_analysis.yaml/json/npz` 和 `bloch_fit_summary.png`，其中记录每个
Z 平面的拟合中心、复数 `R²`、RMSE、局部 R 诊断量和静态 Z 零场。

## 安全恢复

每个偏置点只改变 DG4000 Z 通道 offset，不覆盖任意波。完整任意波包络和固定
输出包络都要通过 `Z_magnetic_field` 安全限值。正常完成、取消、异常和 Ctrl+C
以及 stdin EOF、后端关闭和显式结束会话，均按 `DGChannelState` 快照恢复运行前的 Z、共同触发、X、Y 通道波形选择、电平、频率、相位、Burst/调制开关与 Output 状态；温控恢复为开启，Pump 与锁相沿用已执行配置。四通道恢复失败不得标为完成，也不能盖掉原始采集异常。此处恢复的是共享快照接口可回读的工作模式字段；任意波存储内容、未快照的调制细节和物理输出未做真实硬件验证。

## 运行 / 历史与分步流程

参数在节点内按基础/高级分类编辑，可同时展开多个节点；初始化条件只在 A03 展示，网格与复噪声重试条件只在 A04 展示。本实验没有需在两个实际业务节点重复编辑的过程参数，不为展示共享而制造额外入口。分类按参数键统一保存。顶部集中保存参数与布局、预检、完整运行及任务状态；编辑是草稿，应用后仅更新失效状态，必须显式勾选重跑，不自动补跑前置。

| 节点 | 职责与前置 | 主要文件 / 产物 |
| --- | --- | --- |
| A01 | 无硬件预检、GS200 断开确认、理论/闭环波形及独立 Z 标定冻结 | `preparation.py`，来源指纹 |
| A02 | 依赖 A01；新目录、连接、Z/触发/XY 原态快照 | `preparation.py`，`raw/source_manifest.yaml` 与实际波形 |
| A03 | 依赖 A02；参考时钟严格回读、TEC UI 目标、光功率、Pump、解调及 Z 控制 | `preparation.py`；工作点与实际率快照 |
| A04 | 依赖 A03；连续蛇形 XYZ、只改 Z offset、同步 X/Y/R 与复噪声重试 | `excitation.py`、`acquisition.py`；逐尝试 NPZ、`raw/xyz_balance_scan.npz` |
| A05 | 依赖 A04；恢复四通道与温控，成功后封存；再次采集须重跑 A03 | `workflow.py`；安全结果与完成状态 |
| A12 | 无前置；安全结束当前会话 | `session.py` 复用串行命令、修订与取消/EOF 协议 |
| B01 | 独立运行目录身份、格式、完整矩阵、实际率与静态标定资格 | `run_format.py`、`analysis_steps.py` |
| B02 | 依赖 B01；原复数 Bloch 拟合、静态 Z 零场及原科学图 | `bloch_fit.py`、`analysis_plotting.py`；拟合、CSV、NPZ 与三组 PNG/PDF |
| B03 | 依赖 B02；保存完整汇总 | `analysis.yaml`、`analysis.json` |

等待下一步时会话仍持有全局硬件锁并保持输出，不能当作运行结束。刷新可恢复存活会话；后端重启不恢复硬件。A05 成功后恢复原态但保留通信会话，A12 或任务取消完成必要收尾后才释放锁。重跑创建新目录，保留上一轮 raw 和参数。运行页提供本实验可编辑 `.excalidraw` 原图下载，不新增架构 Tab。

历史 Tab 浏览该轮参数、复数拟合汇总、科学图与下载，不启动仪器或自动分析；填回只编辑下一次实验，不自动保存或运行。独立分析必须绑定具体目录，分析覆盖参数固定为 `{}`，不从当前默认值或已删除的外部来源重建历史。保存默认值始终保存下一次实验参数和统一布局，不改历史采集参数。

## 原始格式与离线入口

参数 schema 保持 v3，新增独立 `raw_format_version=1`；原 NPZ 字段与单位不变。无标记的已知 schema v1/v2/v3 历史按同一 R/X/Y 三维格式读取，不改写历史 raw。身份、完成状态、恢复结果、轴/矩阵形状、非负标准差、有效实际率及无重复采集序号不合格时拒绝分析；Bloch 拟合仍至少需要两个 X 点和两个 Y 点，Z 可固定为单点。没有完整安全封存的运行不能分析。

输入和产物采用内容指纹。分步与全量共用原算法；重算先核验当前输入，再归档受影响产物，坏输入不搬走旧结果，不归档仍在使用的前置。质量诊断按原科学字段保留，不把单次优化求解完成当成严格周期模型或额外质量门槛。

```powershell
.\agent_exp_env\Scripts\python.exe experiments\Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_plot.py <明确的运行目录>
```

薄入口缺少 `run_dir` 时直接拒绝，不再隐式选择 latest。自动化验证只对显式临时运行目录写入，不在真实历史原件上重分析。
