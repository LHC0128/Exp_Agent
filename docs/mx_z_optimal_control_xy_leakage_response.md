---
title: Mx Z 最优控制 XY 泄露响应
type: Mx_Z_Optimal_Control_XY_Leakage_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger_preserved_on_exit
  X_magnetic_field:
    role: scanned_same_shape_arbitrary_waveform
  Y_magnetic_field:
    role: scanned_same_shape_arbitrary_waveform
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: Demod0_R_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - X/Y 使用与 Z 实际上传数组完全相同的归一化采样点和重复频率。
  - 负带符号幅度用绝对 Vpp 和相对 Z 基准相位增加 180° 实现。
  - 正常结束保留实测最小 R 网格点；异常与取消保留当时 X/Y 状态。
  - 所有结束路径均保留 Time_sequence_2 当前状态，同时保留 Z/X/Y 当前输出。
  - Z/X/Y 任意波 DG4000 Burst 均使用共同触发方波的下降沿启动。
experiment_id: mx-z-optimal-control-xy-leakage-response
defaults_file: params/experiments/mx-z-optimal-control-xy-leakage-response.yaml
---

# Mx Z 最优控制 XY 泄露响应

## 采集时序与系统稳定

每次实际 XY 任意波和共同触发配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集 R 至内存，恢复温控并等待 `TEMP_SWITCH_ON_LAG_S` 后保存波形和元数据。目录、来源快照与初始配置提前保存；每次重试重新配置并执行完整时序。本实验不做 RF 校相、噪声采集或 OFF/ON 成对扫描。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 等设备专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-optimal-control-xy-leakage-response.yaml)。GUI「保存参数与布局」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续工作点和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-leakage-response`。实验复用
`Mx Z 最优控制 RF 灵敏度`的 Z 控制来源、Z 标定、GS200、光功率、温度和
锁相放大器 工作点，但不输出独立 Y RF 正弦波，也不执行 RF 校相、噪声或灵敏度分析。

Z 通道输出按控制版本和 Z 标定换算后的最优控制波形。X/Y 通道复制 Z 实际上传的
归一化数组和重复频率，输出偏置固定为 0 V，只扫描硬件 Vpp。正幅度使用
`CONTROL_BURST_PHASE_DEG`；负幅度使用绝对 Vpp，并在该相位基础上增加 180°；
零幅度切换为 `0 V DC + Output OFF`。

`Time_sequence_2` 必须由操作者分配到 Z 控制 DG4000 和 X/Y 控制 DG4000 的
Ext Trig。所有任意波通道使用下降沿外触发，与 Keithley 6221 Trigger Link 输入
保持同沿。软件按当前设备映射配置两台设备的 Burst 和触发，但无法检查实体 BNC 连接或物理锁定。
连接后使用设备库的参考时钟目标，统一写入并严格回读；时钟失败停止采集，不自动切换设备。

## 二维扫描与采集

X/Y 扫描范围与点数读取参数文件，总网格点数为两轴点数之积。
扫描采用 X 外层、Y 蛇形顺序。切换每个网格点时先关闭共同触发和 X/Y Output，
再分别设置绝对 Vpp 与 Burst 相位，同时 arm 两路后重新开启共同触发；Z 不重新
arm并保持连续运行。

每个尝试在配置完成后关温控、按 `SYSTEM_SETTLE_S` 稳定，并按 `RESPONSE_DURATION_S` 采集 Demod0 R，
随后恢复温控并等待 `TEMP_SWITCH_ON_LAG_S`。R 样本标准差超过 `R_BAD_POINT_STD_THRESHOLD_V` 时，
仍完整保存该次拒绝数据，再重新执行触发、温控门控和采集；最多 `R_POINT_MAX_ATTEMPTS` 次，用尽则阻断后继。
慢磁盘只延长温控开启窗口。读取、恢复或写盘失败即停止；恢复失败也先尝试恢复，再抢救已读数据，保留原始异常。
已成功保存的尝试不因后续失败删除，不把不完整网格记为完成。

原始数据保存到：

```text
data/Mx_Z_Optimal_Control_XY_Leakage_Response/<run>/
  experiment_config.yaml
  acquisition_state.json
  analysis_state.json
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    grid_X###_Y###_attempt_##.npz
    xy_leakage_scan.npz
  results/
    evaluation.json
    analysis.yaml
    analysis.json
    xy_leakage_response.png
    xy_leakage_response.pdf
```

聚合 NPZ 始终按递增 X/Y 规范轴保存矩阵，并额外记录蛇形采集序号、硬件幅度、
相位、Output 状态、接受的重试文件和实际 锁相放大器 采样率。

## 运行页、分步与独立分析

GUI 保留「运行 / 历史」双 Tab，运行页为用户确认的 XY 可勾选、带依赖流程图。保存参数与布局、派生预览、预检、完整运行和任务状态位于流程容器顶部；参数在节点内按基础／高级编辑，多节点可同时展开，共享触发相位的数值与分类同步。
草稿显示预计失效范围，还原修改后清除；应用只使实际依赖过期，必须显式重跑，不会暗中操作仪器。初始化条件仅在 A03 编辑。完整与分步执行调用同一套业务。

| 节点 | 职责与产物 | 前置及门槛 |
| --- | --- | --- |
| A01 | 无硬件预检，冻结理论波形、参数和 Z 标定内容 | 无；映射、锁相兼容及输出包络合格 |
| A02 | 创建本轮目录、连接、记录初态并保存来源 | A01；来源漂移阻断，复用连接前核对设备配置 |
| A03 | 严格回读时钟、TEC 目标、Mx 工作点、零 XY 与 Z 任意波 | A02；无温度判稳，不能静默更换型号或通道 |
| A04 | X 外层、Y 蛇形任意波及逐点 R 重试，保存尝试与聚合矩阵 | A03；拒绝数据照常保留，重试用尽禁止 A05 |
| A05 | 施加实测最小网格点，记录最终 XY 状态 | A04；平局取采集最早，不复测 R |
| A06 | 恢复温控、GS200 原态并安全封存 | A05；恢复失败不标完成，工作点步骤过期，下轮须重新准备 |
| A12 | 结束会话并释放硬件锁 | 无；不代表原始数据完整，仍保持 Z/XY/触发当前状态 |
| B01 | 校验运行身份、格式、完整有限矩阵、坐标、序号与实际率 | 无；必须是完整安全封存的本网格 |
| B02 | 原最小点、R 均值／标准差热图、`evaluation.json` | B01；不拟合连续极值，不做绝对磁场标定 |
| B03 | `analysis.yaml/json` 完整汇总 | B02；前置或产物过期时拒绝跳步复用 |

会话等待期间继续占用全局硬件互斥；刷新恢复仍存活的会话，后台重启不恢复仪器状态。执行中不接受受限改参；修订不匹配、重复与并发请求拒绝。取消、通信 EOF 和后台关闭均走相同安全收尾。重跑新建目录，不覆盖原始数据；已执行配置与未执行草稿分离。

分析对象显式选择「下一次实验」或具体运行目录。本实验无可调分析覆盖参数；历史参数只读，填回只更新下一次草稿，不自动保存或运行。输入内容摘要、算法版本或产物变化使对应步骤及后继过期；重算只归档受影响的结果，不移动仍使用的前置或原始文件。坏输入不得搬走上次完整结果，失败状态与旧产物分别展示。

新采集记录 `raw_format_version: 1`，与参数 `schema_version` 独立。无格式标记的 schema v1/v2、`nested_scan` 已完成历史沿用已验证格式；其他版本、未封存或明确恢复失败拒绝重分析，但可只读查看旧结果。离线分析不要求原控制来源仍存在。

```powershell
# 写入分析必须明确指定目标目录；验证时使用临时副本，不能省略成 latest。
agent_exp_env/Scripts/python.exe experiments/Mx_Z_Optimal_Control_XY_Leakage_Response_plot.py "<explicit-run-dir>"
```

实现位于 `lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/`：`preparation.py` 预检／来源／工作点与收尾，`excitation.py` 同形任意波，`acquisition.py` 扫描与聚合，`workflow.py/session.py` 编排，`analysis_steps.py/analysis_plotting.py` 科学分析与英文科研图。`stages.py` 是 GUI 与执行共用清单，[可编辑流程图](../lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/workflow.excalidraw)可从运行页下载。

## 分析与结束状态

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

分析器只读取明确运行目录中的配置和聚合原始数据，不重载原理论目录或 Z 标定来源，生成 R 均值和标准差二维热图，并按
`R 均值最小、采集序号最早`的规则报告实测网格最小点。不执行连续拟合、网格外推
或 X/Y 电压到磁场的换算，因此结果不是泄露磁场绝对标定。

正常完成后，程序同时施加该实测最小点的 X/Y 波形，等待共同触发启动，但不额外
复测组合状态的 R。取消、Ctrl+C 或异常时不切换最佳点，而是保留当时的 Z/X/Y
状态。所有退出路径均保留 `Time_sequence_2` 当前状态、恢复温控为
`5 V DC + Output ON`、恢复 GS200 运行前完整状态，并保留 Pump/Probe、Pump
载波/门控和 锁相放大器 设置。由于这是显式安全例外，失败后 Z/X/Y 和共同触发可能继续开启。结束路径不额外关闭、归零或重新开启共同触发；若故障发生在点配置中途，保持故障当时的状态，而不是强制恢复到 ON。
只应用但尚未重跑的参数不会用于安全恢复；恢复失败不计为安全封存。
