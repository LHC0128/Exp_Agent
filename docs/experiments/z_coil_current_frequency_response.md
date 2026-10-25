---
title: Z 线圈实际电流频率响应
type: Z_Coil_Current_Frequency_Response
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: swept_sine_drive
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_low_side_sense_CH4_trigger_optional_CH1_drive_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - CH1 驱动参考可选；未接 CH1 时用 DG 设定幅值归一，频响幅值可用，相位只相对触发点。
  - 未接 CH1 或其拟合质量不足时，结果不能作为闭环相位标定。
  - 每频至少两次有效重复才允许相位合格；单次测量保留幅值与相位诊断。
  - 相位随频率应平缓变化；相邻点相位折叠说明参考点取错，插值将失效。
  - 闭环按可靠覆盖自动收缩学习频带，未覆盖谐波既不插值也不外推。
experiment_id: z-coil-current-frequency-response
defaults_file: params/experiments/z-coil-current-frequency-response.yaml
---

# Z 线圈实际电流频率响应

当前默认参数：[参数文件](../../params/experiments/z-coil-current-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `z-coil-current-frequency-response`。实验沿**对数等间隔频率轴**
逐点扫描从 `FREQUENCY_START_HZ` 到 `FREQUENCY_STOP_HZ` 的实际电流频率响应，
点数由 `FREQUENCY_POINTS_PER_DECADE` 按覆盖整个区间推算（只读显示为 `FREQUENCY_POINTS`）。
实验可不接 CH1；此时响应幅值按 DG 设定驱动幅值归一，测得相位包含启动时延，只用于观察，
不作为闭环相位标定。接入有效 CH1 时，同帧实测驱动相量才可给出消除启动时延的 I/V 复传递函数。
闭环相位还要求该频率至少两次有效重复，且圆周相位标准差 < 15°；单次测量仍保存幅值与相位诊断。
运行页提供节点内参数编辑、明确依赖的采集模块和独立离线分析；完整运行仍自动完成采集、收尾与分析。
历史分析只读取该目录的参数快照和原始帧，与下一次实验的参数草稿隔离。

## 接线

| 通道 | 用途 |
|---|---|
| CH1（可选，`SCOPE_REFERENCE_CHANNEL`） | Z 通道输出经 T 型接头分一路；不接时设为 0 |
| CH3（`SCOPE_MEASURED_CHANNEL`） | 跨接低端采样电阻，按 `I=V_R/R_s` 换算实际电流 |
| CH4（`SCOPE_TRIGGER_CHANNEL`） | 共同触发，2.5 V 下降沿 |

接入 CH1 时，参考通道必须与电流通道**同一帧、同一时间轴**。实验早期版本以 CH4 下降沿作相位原点，
相干启动时延 Δt 会在线性相位上叠加 `2πf·Δt`（Δt 约 ms 级，在 1 kHz 以上即旋转多圈），
使对数扫频的相邻点相位差远超 π、展开相位失效。未接 CH1 时按命令幅值归一，幅值响应仍可测，
但绝对相位会保留启动时延；若 CH1 正弦拟合相关性低于 0.98，分析也会回退到命令幅值参考。

驱动固定为 DG 内置正弦 + 外部下降沿无限 Burst，每帧 `phase_init` 两路后相干重触发，
保留 `command_voltage_reference=50_ohm` 的 Vpp 命令数值基准，设备连接时使用现有负载策略。
不把命令数值或最近写入值当作实测驱动；同帧 CH1 是复传递函数的实测分母。

## 采集

每个频率点重复 `SCOPE_REPEATS` 次。参数允许设为 1；分析按实际帧质量计数，至少两次有效重复才允许闭环相位合格，不能用设定重复数或总帧数代替。
记录窗覆盖 `RESPONSE_SETTLE_CYCLES`（丢弃相干启动后的 L/R 瞬态）
加 `SCOPE_CYCLES` 个待分析周期，再加一个保护周期；由于 SDS 的 `timebase_delay=0`
把触发点放在记录窗中央，总时长取所需触发后窗口的两倍
（`SCOPE_CENTERED_CAPTURE`），并保持不小于共同触发两个周期。

采样率按频率动态选择：不低于 `SCOPE_MIN_SAMPLE_RATE_SA_S`，尽量保持
`SCOPE_SAMPLES_PER_CYCLE` 点/周期，不超过 `SCOPE_SAMPLE_RATE_SA_S`。
每帧都按实测阻值检查线圈电流与采样电阻功率。

本实验属于纯电学采集例外，不连接 TEC 或操作温控开关。每频先关闭激励并切频、配置并回读 SDS，
完成切频等待后每帧相干重触发；采集到内存并完成电流/功率校验后立即保存该帧，再进行下一帧。
实际采样率由时间轴计算，随帧写入 NPZ；请求值和 SDS 回读设置分别保存，不能把采样率上限当作实际值。
每轮从 UI 首频量程/偏置重新准备，同轮频率和重复间保留自动量程状态；SDS 模式发生变化后必须重做 Scope 准备。

## 可执行流程与 GUI

运行页顶部在同一流程容器内提供「保存参数与布局」、派生预览、仅预检、完整运行、取消和任务状态。
基础参数名称常显，节点展开后编辑；高级参数在节点内折叠，允许多个节点同时展开。保存只更新下一次实验参数和统一分类偏好。

| 节点 | 实际职责与前置 |
|---|---|
| A01 | 参数、安全限值与低端接线配置预检，不连接仪器；单独重做预检不使已完成采集失效 |
| A02 | A01 完成后复用映射设备会话，连接同机信号源和 SDS |
| A03 | A02 后同步参考时钟并严格回读 |
| A04 | A03 后设置正弦、外部下降沿无限 Burst 和共同触发，初始输出关闭 |
| A05 | A04 后按对数轴首频准备动态 SDS 采样率/记录窗与可选参考通道，回读实际设置 |
| A06 | A05 后逐频切换、相干重复采集、电流/功率校验和真 NPZ 写盘，每轮新建独立目录 |
| A07 | 无前置的安全收尾与结束会话 |
| B01 | 具体运行目录的实验身份、raw 格式、波形、正频率和 CH4 下降沿资格 |
| B02 | B01 后换算电流、逐帧正弦相量拟合、统一参考选择、有效重复计数和可靠频带；至少一个幅值可靠点才可继续导出，相位资格不足仍保留诊断 |
| B03 | B02 后从有效拟合产物绘图并导出报告，不重新拟合 |

采集与分析两列分别勾选；前置未完成且未显式选择时拒绝，不自动补选。质量未通过保存诊断并阻断后继。
`SCOPE_CYCLES` 与启动后丢弃周期在 A05/A06 共享编辑，其值及基础/高级分类同步；驱动、触发和频率轴初始化条件只在 A04 出现。
改参先形成草稿，预览合并多个参数真实影响范围；还原后预览消失。空闲时「应用参数到会话」重新全量预检，
成功后只标记相关模块及后继失效，仍须显式重跑；失败保留草稿，执行期间限制编辑。
阶段依赖、展示键和失效范围以实验包 `stages.py` 为唯一事实来源；原图在同包 `workflow.excalidraw`，可从运行页下载编辑。

分步等待期间保持设备连接和最后输出，并持续占用全局硬件队列。刷新可恢复当前进程仍存活的会话；后台重启不从磁盘恢复硬件。
空闲时用 A07/「结束会话」，执行中用取消；取消、异常、通信 EOF 和后台关闭均在收尾完成后释放硬件锁。
收尾停止 SDS、把 Z 设为 DC 0 V 并关闭，保持共同触发最后实际状态后断开连接。
应用但未重跑的参数不会在收尾时重配保留输出。关闭失败单独记录，不能宣称安全完成，也不会覆盖原始采集异常。
此前成功保存帧和部分索引保留，末频点与重复帧不因重跑被覆盖。

## 分析与结果

对每帧以 CH4 下降沿为时间原点，取丢弃周期之后的完整周期，对 CH3 电流做
`offset + a*sin + b*cos` 最小二乘拟合。有效 CH1 存在时，`H = I_phasor / V_drive_phasor`；
否则 `H` 按 DG 设定的驱动幅值归一。未接 CH1 的相位保留相干启动时延。同一设定频率的重复帧做复数平均，按原始记录中精确相同的频率分组；相邻但不同的扫描频率不合并，也不能互算为两次有效重复。分析版本升级后旧阶段缓存失效，使用原始数据重新分析，不改写历史 raw。
原有复数平均、拟合相关均值和全帧圆周相位标准差算法保持不变。
幅值可靠性 `amplitude_reliable` 要求该点无示波器饱和、全部传递相量有限非零且电流正弦拟合相关均值 > 0.95。
`frame_count` 记录实际总帧数，`valid_frame_count` 只计入逐帧传递相量有限非零、无饱和、电流拟合相关性 > 0.95 的帧；
实测 CH1 参考模式下，该帧驱动拟合相关性还须有限且 ≥ 0.98。参考选择仍按整轮判定：任一帧参考缺失或质量不足时整轮退回命令幅值参考。

`phase_reliable` 仅在实测 CH1 参考、`valid_frame_count >= 2` 且圆周相位标准差 < 15° 时为真。
单帧圆周标准差即使等于 0 也不合格，两帧中只有一帧质量通过同样不合格。
`reliable` 保留既有含义：命令幅值模式等于幅值质量，实测参考模式还须通过相位资格；未接 CH1 时 `phase_reliable` 恒为假。
闭环可用性还要求至少两个同时满足 `reliable` 和 `phase_reliable` 的频点，报告用 `closed_loop_usable` 明示；最终目标谐波覆盖仍由闭环预检判定。

结果保存为 `results/frequency_response.npz`（`format_version=4`、
`output_protocol=coherent_swept_sine_current_response`、`command_voltage_reference=50_ohm`、
`phase_reference` 标记使用实测驱动或命令幅值作参考），含原有 `reliable`、独立 `amplitude_reliable` 与 `phase_reliable` 掩码、总帧数和有效重复数、拟合相关性、
相位标准差、饱和标志、采样电阻阻值与**可靠覆盖区间**
（`reliable_frequency_min_hz`/`reliable_frequency_max_hz`）；
另有 `frequency_response.yaml/json` 与 `transfer_function.png`。
NPZ 和文本报告保存 `valid_frame_count_rule=exact_frequency_v1`，证明重复数按精确相同的设定频率统计。闭环加载要求该标量标记匹配；旧结果即使带有重复数或合格标记，缺少当前规则证据也需从已有 raw 重新分析。
不可靠点不删除，但会在报告里列入 `excluded_frequency_hz`。
文本报告的缺测值写为 `null`，NPZ 中保留原有 NaN 与掩码。全部频率仅有一次有效重复而幅值质量通过时，B02/B03 仍完成真实诊断导出，
`closed_loop_usable=false`，可靠复响应覆盖的两端在 JSON 中为 `null`、在 NPZ 中为 NaN；不会把全单次误报为闭环标定。
混合单双次按每频实际有效重复判定，相位不合格的点仍保留，其位置会切断闭环插值区间。
没有任何幅值可靠点时 B02 标记 `quality_failed`，
仍写出逐点 `frequency_evaluation.json` 和分析摘要，禁止 B03，不能把失败当作闭环标定。

`analysis_state.json` 记录阶段修订、输入和产物指纹；配置或 raw 文件变化、产物被更改/缺失后不能复用旧拟合。
本次有效重复规则使用新的分析算法指纹，旧完成记录自动标记需重算；读取状态不会重写历史原始帧或参数快照。
重算只归档当前阶段与受影响后继的产物到 `results/before_*`，重做 B03 不移动仍在使用的 B02 输入。
旧无阶段记录的运行从 B01 重建；正在写入的目录拒绝分析，分阶段与旧重新分析共用同一目录互斥。
本实验没有独立科学参数覆盖（`options={}`）；具体目录的电阻等物理来源始终取自采集快照。
分析列的「下一次实验」用于选择后续自动分析对象；切换历史目录和主 Tab 保留下一次实验草稿，保存默认值不会写入历史采集参数。
历史 Tab 保留列表、参数填回、重新分析、I/V 图、参考模式/可靠覆盖诊断和结果下载；填回不自动保存或启动。

## 闭环如何消费

只有 `phase_reference=measured_drive_voltage` 且每频至少两次有效重复、相位质量合格的运行可供闭环
（`z-aw-closed-loop-waveform-correction`）按目标波形周期网格上的每个谐波
`k·f_rep`，在**相邻可靠扫频点之间**按对数幅度与展开相位线性插值取 H：

- 不跨不可靠点插值：任何不可靠频点都会切断插值区间；
- 加载时取 `reliable`、`phase_reliable` 与 `valid_frame_count >= 2` 的交集，不能信任旧报告仅凭单帧标准差为零而生成的可靠标记；
- 绝不外推：落在可靠覆盖之外的谐波不参与修正；
- 学习频带（`ERROR_CUTOFF_HZ`）按可靠覆盖自动收缩，并在日志中报告收缩后的频带、
  可靠覆盖范围与未覆盖谐波个数；
- 学习频带内没有任何被可靠覆盖的谐波时预检失败。

## 采集时长

低频点的记录窗由共同触发周期下限保证，总时长受低频记录窗、密度和重复次数共同影响。若要压缩时长，可提高
`FREQUENCY_START_HZ`、降低 `SCOPE_REPEATS` 或放宽 `RESPONSE_SETTLE_CYCLES`。

## 历史兼容

`format_version=3` 的逐谐波 AW 网格协议（`TARGET_REPEAT_FREQUENCY_HZ`、`AW_POINTS`）
已废弃：它把标定与某一个目标波形强绑定，且每帧需要重新上传任意波。
历史运行目录保持原样，但闭环不再接受其 `frequency_response.npz`，需要按新协议重新标定。
schema 3 及更早的默认 YAML 中的旧键在参数入口被丢弃。

参数版本维持 `schema_version=4`；新运行独立记录 `raw_format_version=1`，不因参数版本变化拒绝同一 raw 格式。
没有 raw 格式标记的历史运行，仅按已发布的本实验 ID、data_type、逐点扫描与相干正弦电流协议识别，
不改写旧目录或把未知 AW 格式猜成当前版本。
既有 `format_version=4` 结果若缺少 `valid_frame_count` 或相位资格证据，闭环要求对已有 raw 显式重新分析；
不静默沿用旧 `phase_reliable`，也不要求为此重新采集或改变原始格式。

## 模块职责

`workflow.py` 只负责完整/分步编排与电流实验注入；准备、设备连接、正弦激励、SDS 配置/重试/逐帧写盘和安全收尾
分别复用 `z_coil_inductance_frequency_response` 的 `preparation.py`、`excitation.py`、`acquisition.py`。
本实验 `acquisition.py` 提供实测电流与功率校验，参数模型继续提供真实对数轴；没有复制另一套扫描循环。
`analysis_steps.py` 负责完整周期选取、电流相量、参考和可靠性计算以及绘图；`analysis.py` 仅编排资格、计算、导出和指纹归档。
`session.py` 使用共用串行子进程协议，`run_format.py` 统一采集/分析/历史资格，两个 `experiments/` 文件保持薄入口。

入口为 `experiments/Z_Coil_Current_Frequency_Response.py` 和
`experiments/Z_Coil_Current_Frequency_Response_plot.py`。
