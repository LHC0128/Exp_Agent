---
title: Mx Z 最优控制 XYZ 平衡场
type: Mx_Z_Optimal_Control_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: z_balance_scan
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: optimal_control_trigger
  X_magnetic_field:
    role: x_dc_balance_scan
  Y_magnetic_field:
    role: y_dc_balance_scan
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_r_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - 三维数组使用 Z/X/Y 规范索引，采集按 Z 外层连续蛇形执行。
  - 任一轴点数为 1 时，必须满足该轴 START=STOP，并固定在该值。
  - 最优点只按实测 Demod0 mean(R) 选择，逐层 V 形拟合仅作工作点诊断，不复测、不换算 nT。
  - 分析图按每个 GS200 Z 电流分别显示 XY 平面，并对所有平面使用共同色标。
  - 扫描期间 X/Y 与 GS200 在零设定点也保持 Output ON。
  - Z 最优控制 DG4000 Burst 使用共同触发方波的下降沿启动。
experiment_id: mx-z-optimal-control-xyz-balance
defaults_file: params/experiments/mx-z-optimal-control-xyz-balance.yaml
---

# Mx Z 最优控制 XYZ 平衡场

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xyz-balance.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xyz-balance`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制来源、Z 通道标定换算、共同触发、
Pump/Probe、温控和 锁相放大器 Demod0 配置；最优控制只在开始时触发一次并持续运行。

平衡场由三路独立 DC 输出扫描：

- X：`X_magnetic_field` DG4000 CH1，单位 V；
- Y：`Y_magnetic_field` DG4000 CH2，单位 V；
- Z：`main_magnetic_field` GS200，单位 mA。

`Z_magnetic_field` DG4000 CH1 只输出周期最优控制，不作为 Z 平衡场扫描源。
`Time_sequence_2` CH2 只需接入 Z 控制 DG4000 的 Ext Trig；X/Y 使用 DC 模式，
不需要共同触发。Z 控制 Burst 使用下降沿启动，与 Keithley 6221 Trigger Link
输入保持同沿。

## 扫描与采集

X/Y 电压及 Z 电流的范围与点数由参数文件指定，总点数为三个轴点数之积。数据数组固定使用 `(Z, X, Y)` 索引；实际
采集以 Z 为外层，X/Y 使用跨行、跨平面连续蛇形顺序，减少相邻点的大幅跳变。

每个轴都允许配置为单点固定值。当 `*_FIELD_POINTS=1` 时，对应的
`*_FIELD_START` 与 `*_FIELD_STOP` 必须严格相等；点数大于 1 时必须满足
`START<STOP`。预检与运行时都会执行同样的约束。

每个点先设置三轴补偿值，再将温控开关设为 0 V DC + Output ON，按配置等待并采集 Demod0 R，保存实际采样率；随后在 `finally` 中恢复温控为
5 V DC + Output ON 并按配置等待。标准差超限时完整保存本次尝试并在配置的尝试上限内重采。X/Y 和 GS200 在扫描值为零时也保持 Output ON，避免跨零点时引入输出
开关状态变化。

## 数据与分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

```text
data/Mx_Z_Optimal_Control_XYZ_Balance/<run>/
  experiment_config.yaml
  analysis_state.json
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    grid_Z*_X*_Y*_attempt_*.npz
    xyz_balance_scan.npz
  results/
    balance_results.npz
    xyz_balance_grid.csv
    xyz_balance_slices.png
    xyz_balance_slices.pdf
    balance_linear_fit.npz
    xyz_balance_linear_fit.png
    xyz_balance_linear_fit.pdf
    evaluation.json
    analysis.yaml
    analysis.json
```

主结果是所有有效实测点中 Demod0 `mean(R)` 最小的第一个采集点；相同 R 时按
采集顺序最早者确定。结果只报告 X/Y 电压和 GS200 Z 电流设定值，最佳组合不复测，
也不做 nT 换算。分析图按每个实测 GS200 Z 电流分别显示一个 XY 平面，所有
平面共用同一 Mean R 色标，便于直接比较不同 Z 电流下的二维响应；全局实测最小点
只标在对应的 Z 电流面板中。若 Z 轴只有一个点，图中只生成一个 XY 平面。最小点位于任一扫描轴
边界时，分析结果会给出警告。

在实测最小点之外，分析器沿每层网格最小点的行／列执行一维 V 形／复线性模
拟合，报告各层拟合零点、零点随 Z 电流的耦合斜率、Z 层二次顶点与拟合工作点，
并按邻域中位数偏差剔除孤立点。拟合仅作工作点诊断：拟合零点落在扫描窗口之外、
贴近边界或与网格最小点偏差超过一个步长时给出警告和下一轮扫描中心建议，
不改变实测最小点结论，也不做绝对磁场标定。

## 运行页、分步与独立分析

GUI 保留「运行 / 历史」双 Tab，运行页为用户确认的可勾选、带依赖流程图。保存参数与布局、派生预览、预检、完整运行和任务状态位于流程容器顶部；参数在节点内按基础／高级编辑，多节点可同时展开，共享触发相位的数值与分类同步。
草稿显示预计失效范围，还原修改后清除；应用只使实际依赖过期，必须显式重跑，不会暗中操作仪器。初始化条件仅在 A03 编辑。完整与分步执行调用同一套业务。

| 节点 | 职责与产物 | 前置及门槛 |
| --- | --- | --- |
| A01 | 无硬件预检，冻结理论波形、参数和 Z 标定内容 | 无；映射、锁相兼容及输出包络合格 |
| A02 | 创建本轮目录、连接、记录 X/Y 通道与 GS200 初态并保存来源 | A01；来源漂移阻断，复用连接前核对设备配置 |
| A03 | 严格回读时钟、TEC 目标、Mx 工作点；X/Y 置零关闭，GS200 置 Z 起点 | A02；无温度判稳，不能静默更换型号或通道 |
| A04 | Z 外层、X/Y 连续蛇形三轴 DC 及逐点 R 重试，保存尝试与聚合矩阵 | A03；拒绝数据照常保留，重试用尽禁止 A05 |
| A05 | 恢复温控、GS200 与运行前 X/Y 原态并安全封存 | A04；恢复失败不标完成，工作点步骤过期，下轮须重新准备 |
| A12 | 结束会话并释放硬件锁 | 无；不代表原始数据完整，仍保持 Z 与共同触发当前状态 |
| B01 | 校验运行身份、格式、完整有限三维矩阵、坐标、序号与实际率 | 无；必须是完整安全封存的本网格 |
| B02 | 实测最小点、逐层 V 形拟合、逐 Z 平面热图与 `evaluation.json` | B01；拟合仅作诊断，不做绝对磁场标定 |
| B03 | `analysis.yaml/json` 完整汇总 | B02；前置或产物过期时拒绝跳步复用 |

会话等待期间继续占用全局硬件互斥；刷新恢复仍存活的会话，后台重启不恢复仪器状态。执行中不接受受限改参；修订不匹配、重复与并发请求拒绝。取消、通信 EOF 和后台关闭均走相同安全收尾。重跑新建目录，不覆盖原始数据；已执行配置与未执行草稿分离。

分析对象显式选择「下一次实验」或具体运行目录。本实验无可调分析覆盖参数；历史参数只读，填回只更新下一次草稿，不自动保存或运行。输入内容摘要、算法版本或产物变化使对应步骤及后继过期；重算只归档受影响的结果，不移动仍使用的前置或原始文件。坏输入不得搬走上次完整结果，失败状态与旧产物分别展示。

新采集记录 `raw_format_version: 1`，与参数 `schema_version` 独立。无格式标记的 schema v1/v2、`nested_scan` 已完成历史沿用已验证格式；其他版本、未封存或明确恢复失败拒绝重分析，但可只读查看旧结果。离线分析不要求原控制来源仍存在。

```powershell
# 写入分析必须明确指定目标目录；验证时使用临时副本，不能省略成 latest。
agent_exp_env/Scripts/python.exe experiments/Mx_Z_Optimal_Control_XYZ_Balance_plot.py "<explicit-run-dir>"
```

实现位于 `lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/`：`preparation.py` 预检／来源／工作点与收尾，`excitation.py` X/Y 置零与三轴控制器，`acquisition.py` 蛇形扫描与聚合，`workflow.py/session.py` 编排，`linear_fit.py` 逐层拟合诊断，`analysis_steps.py` 科学分析与英文科研图。`stages.py` 是 GUI 与执行共用清单，[可编辑流程图](../lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/workflow.excalidraw)可从运行页下载。

## 安全结束状态

连接后、修改输出前先保存 X/Y DG4000 通道波形、幅度、偏置、相位、Burst、调制和
Output 状态，以及 GS200 的源模式、电流、量程、限流和 Output 状态。正常完成、
取消、异常和 Ctrl+C 均恢复这些运行前状态。`Time_sequence_2` 保持当前状态；
`Z_magnetic_field` 保留当前最优控制波形与 Output ON。温控恢复为 5 V DC +
Output ON，Pump/Probe、Pump 载波/门控和 锁相放大器 设置保持，正常结束只断开 TEC。
