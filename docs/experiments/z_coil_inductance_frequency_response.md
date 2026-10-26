---
title: Z 线圈电感效应频率响应
type: Z_Coil_Inductance_Frequency_Response
scan_mode: point_by_point
mapping_keys:
  Z_magnetic_field:
    role: sine_drive
  Time_sequence_2:
    role: external_burst_trigger
  scope_waveform:
    role: CH3_coil_voltage_and_CH4_trigger_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - Time_sequence_2 CH2 接 Z_magnetic_field CH1 Ext Trig，Z 正弦使用外部下降沿 Burst。
  - SDS CH3 测量 Z 线圈两端电压，CH4 接共同触发参考，并以 2.5 V 下降沿触发。
  - CH3/CH4 使用 1 MOhm、DC、1x；CH3 量程按实测波形自动调整。
  - CH3 初始垂直量程 `SCOPE_INITIAL_SCALE` 是基础参数，仅用于本次运行开始；
    切频继承上一频点最终量程和偏置，采集中继续自动放大、缩小和居中。
  - 当前接线没有独立电流测量，因此结果用于观察端电压频率响应，不能单独反演唯一电感值。
experiment_id: z-coil-inductance-frequency-response
defaults_file: params/experiments/z-coil-inductance-frequency-response.yaml
---

# Z 线圈电感效应频率响应

当前默认参数：[参数文件](../../params/experiments/z-coil-inductance-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.z_coil_inductance_frequency_response.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## 实验目的

使用固定幅度和偏置的 Z 方向正弦驱动，按 `FREQUENCY_START_HZ`、`FREQUENCY_STOP_HZ` 和 `FREQUENCY_POINTS` 构造包含两端的线性频率轴。每个频率点按 `SCOPE_CYCLES` 配置记录窗口，重复 `SCOPE_REPEATS` 次，用于观察线圈端电压相对于理想 DG 设定正弦的幅值、相位和波形差异。当前值以参数文件为准。

SDS 的每次记录窗口取 `max(SCOPE_CYCLES/f, 2/f_trigger)`，其中 `f_trigger` 是共同触发频率。这样在高频扫描时仍能保证 C4 记录中至少包含共同触发的下降沿；否则窗口可能只截到方波高电平，示波器虽已触发，离线仍无法确定相位零点。

`SCOPE_SAMPLE_RATE_SA_S` 是 GUI 中可调的基础参数，当前取值见参数文件。该参数不再与终止频率按固定倍数绑定，实验会按设置值配置 SDS，并把仪器实际采样率保存到运行配置中。

CH3 初始垂直量程由基础参数 `SCOPE_INITIAL_SCALE` 指定，当前取值见参数文件。该值仅在每次运行的首个频率点使用；应用配置后实验回读仪器实际接受的量程档位和偏置，后续频率点直接继承上一频点结束时的量程和偏置，不再恢复初始量程或归零偏置。重复采集和切频后的自动放大、缩小与居中逻辑保持不变，因此信号变化仍会触发调整，继承值合适时也不会因重置而多出调量程的采集帧。再次启动实验时重新从用户指定初始值开始，不继承上一次运行的状态。

理论值是 DG 的理想设定值，不是独立测量到的 DG 输出端电压。由于当前只测量线圈端电压，实验不能把信号源输出误差、线圈电阻和电感效应唯一分离，也不直接报告绝对电感值。

## 接线

- `Z_magnetic_field` CH1：驱动 Z 线圈。
- `Time_sequence_2` CH2：共同触发方波，接 Z CH1 外部触发输入。
- SDS CH3：跨接 Z 线圈两端，保存线圈端电压。
- SDS CH4：接共同触发方波，作为示波器下降沿触发参考。
- SDS CH3/CH4：1 MOhm、DC、1x，CH4 触发电平 2.5 V。

运行前应人工确认接地、线圈和示波器输入不会短路，且驱动端电压在实验允许范围内。

## 共用步骤与流程图

完整运行和交互流程图共用 `preparation.py`、`excitation.py`、`acquisition.py` 的业务步骤，`workflow.py` 只负责会话和顺序编排。参数仍由 `models.py` 的稳定 GUI/YAML 键声明，默认值保存位置不变。

| 节点 | 实际行为与前置 | 参数编辑与结果 |
| --- | --- | --- |
| A01 | 无硬件参数、映射及安全限值预检 | 预检重跑不使已完成步骤失效 |
| A02 | A01 后连接映射中的信号源与 SDS | 部分连接失败释放已经建立的连接 |
| A03 | A02 后同步并严格回读参考时钟 | 同步失败阻断后续；已有输出先安全关闭再断开 |
| A04 | A03 后配置固定幅度正弦、外部下降沿无限 Burst 和共同触发，输出暂关 | 驱动、触发及频率轴参数 |
| A05 | A04 后配置首频 SDS 窗口并回读实际采样率、量程与偏置 | 采样率、周期数、首频量程 |
| A06 | A05 后逐频配置、切频等待、重复采集、自动量程和逐帧保存 | 运行标签、切频等待、重复次数；每次重跑新建目录 |
| A07 | 无前置的安全收尾并结束会话 | 正常、取消、异常、通信 EOF 和后台关闭均走同一结束策略 |

本实验在 `acquisition_policy.py` 中属于纯电学例外：不调用 TEC，不关闭或恢复温控开关，不生成气室温控门控证据。每频内部保持“关闭相关输出并切频 → 配置 Scope → 等待 → 开启触发与正弦 → 采集与逐帧写盘”的专用协议；重复之间不人为添加 OFF/ON 配对。SDS 读取前停止采集，空帧或缺下降沿按既有协议重试，必要时软复位后重新配置。故障保留此前已读并成功写盘的原始帧、部分索引及原始异常；收尾错误另行保存，不覆盖采集错误。

## GUI 使用与参数生效

本实验清单显式声明 `run_step_id=A06`、`finish_step_id=A07`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

运行页保留“运行 / 历史”两个主 Tab。交互流程容器顶部集中保存参数与布局、派生预览、仅预检、完整运行、任务状态及取消入口；节点内分别编辑基础和高级参数，可以同时展开多个节点。可编辑原图由本实验的 `workflow.excalidraw` 下载。

`SCOPE_CYCLES` 同时用于首频配置和逐频记录窗，可以在 A05/A06 编辑，两处共享同一值。驱动、共同触发等持续条件只在设置节点展示。修改参数先形成草稿，显示预计失效范围；还原修改会清除预览。空闲时“应用参数到会话”重新全量预检，并由后端使受影响步骤及后继失效；应用不会自动运行，必须显式选择步骤重跑。缺少有效前置或 Scope 单次触发模式被改动时拒绝采集，后者需要先重跑 A05。

分步等待保持仪器会话和最近输出，并持续占用全局硬件锁。页面刷新恢复存活会话；后台重启不会从磁盘恢复硬件连接。空闲时使用“结束会话”，执行中使用任务取消。每次 A06 都建立新目录，从 UI 首频量程重新开始，旧目录的参数和原始帧保持不变。

分析列可操作下一次实验或具体历史目录。当前科学分析没有独立覆盖参数，具体目录使用其采集快照；保存默认值只保存下一次实验参数与统一分类，不改写历史快照。历史 Tab 可查看频响、波形、诊断文件、填回参数及重新分析，填回不会自动保存或启动。

## 数据和分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](../plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

采集入口为：

```text
experiments/Z_Coil_Inductance_Frequency_Response.py
```

离线分析入口为：

```text
experiments/Z_Coil_Inductance_Frequency_Response_plot.py
```

每次运行保存：

```text
data/Z_Coil_Inductance_Frequency_Response/<run>/
  experiment_config.yaml
  raw/
    scope_f000_r00.npz ...
    frequency_index.npz
    frequency_index.json
  results/
    frequency_response.npz
    frequency_response.yaml
    frequency_response.json
    waveform_overview.png
    waveform_overview.pdf
    frequency_response.png
    frequency_response.pdf
    analysis_inputs.json
    frequency_evaluation.json
  acquisition_state.json
  analysis_state.json
```

`waveform_overview.png` 按频率分别绘制子图，低频点优先保留，不再把不同频率叠加到同一坐标轴；长记录仅在绘图时均匀抽点，原始数据和拟合不变。

每帧使用

```text
v(t) = offset + a sin(2 pi f t) + b cos(2 pi f t)
```

拟合幅值、相位、偏置、残差 RMS 和相关系数，并与理想幅值 `DRIVE_AMPLITUDE_VPP / 2` 比较。报告中的幅值比、dB、相位差和重复标准差用于判断频率相关的线圈端电压变化。

对幅值数据同时拟合一阶端电压响应模型：

```text
A(f) = sqrt((A0^2 + Ainf^2 * (f/fc)^2) / (1 + (f/fc)^2))
```

拟合结果（`A0`、`Ainf`、`fc`、`R2` 和残差 RMS）写入 `frequency_response.yaml/json/npz`，并叠加到 `frequency_response.png`。其中 `A0` 是拟合的零频幅值；报告还给出每个频率相对 `A0` 的归一化幅值、百分比变化、带符号的幅值下降百分比和 dB 变化。最低实测频率归一化结果仍单独保留，便于区分“最低实测频率参考”与“拟合零频外推”。

离线分析按 B01“配置与原始帧资格校验”→ B02“逐帧正弦与频响模型拟合”→ B03“频响诊断图与报告导出”执行。B01 检查实验身份、独立的 `raw_format_version`、正频率、必需波形及下降沿；缺失或无效输入阻断后继。B02 复用原有正弦、圆周相位统计和一阶幅值拟合，保留 NaN 与模型不可用原因；至少四个有效频率点才尝试一阶幅值模型，模型不可用时仍导出原有测量诊断，不伪造拟合成功。NPZ 保留数值 NaN，JSON 报告及会话快照将不可用值表示为 `null`，保证缺少模型时仍可保存并展示诊断。B03 复用已保存的逐帧拟合结果绘图，不再次拟合。

每个分析阶段记录输入、产物指纹和修订，原始帧或产物变化后相关阶段失效；重算只归档该阶段及后继的文件，保留仍被使用的前置输入。正在写入的目录拒绝分析，完整重新分析和分步分析共享目录互斥。无阶段记录的历史数据从 B01 重建：只接受旧采集器明确写入的实验身份、`data_type`、逐点扫描和正弦驱动标识，不因参数 schema 升级重写或拒绝同格式数据。

## 安全收尾

正常结束、取消或异常时都会停止 SDS，将 Z CH1 设置为 DC 0 V、Output OFF；共同触发 CH2 保持当前状态，并断开本实验连接的设备。
