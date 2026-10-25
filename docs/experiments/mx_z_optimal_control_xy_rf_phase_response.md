---
title: Mx Z 最优控制 XY 平衡场 RF 相位响应
type: Mx_Z_Optimal_Control_XY_RF_Phase_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field:
    role: fixed_main_field
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger
  X_magnetic_field:
    role: x_dc_scan
  Y_magnetic_field:
    role: y_dc_scan_and_rf_offset
  rf_coil:
    role: rf_burst_on_y_channel
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
    role: demod0_r_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - X 外层、Y 蛇形网格按规范轴保存，Y 扫描值直接作为 rf_coil DC offset。
  - 相位轴取本轮参数，覆盖一个 360° 周期，至少 8 个偶数相位点，按 180° 配对顺序采集。
  - Z 最优控制只在实验开始时启动并持续运行，Y RF 每个相位点使用 OFF→ON 重新等待共同触发。
  - 拟合失败点保留原始曲线并在参数地图中标记为无效，不自动选择最佳平衡点。
experiment_id: mx-z-optimal-control-xy-rf-phase-response
defaults_file: params/experiments/mx-z-optimal-control-xy-rf-phase-response.yaml
---

# Mx Z 最优控制 XY 平衡场 RF 相位响应

## 采集时序与系统稳定

每个相位点先完成实际 RF 配置，再关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。180° 配对仅定义相位访问顺序，每个相位点仍各自关闭/恢复温控；本实验没有额外噪声采集或自动校相步骤。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。GS200 量程、PZT、6221 电流等专用准备等待在温控开启时完成，恢复等待和触发协议等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-z-optimal-control-xy-rf-phase-response.yaml)。GUI「保存参数与布局」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 目标与构型

稳定实验 ID 为 `mx-z-optimal-control-xy-rf-phase-response`。实验沿用
`Mx Z 最优控制 RF 灵敏度`的 Z 周期最优控制、共同触发、温控和 锁相放大器 工作点，
但将 X/Y DC 补偿值改为二维网格，并在每个网格点上扫描 Y RF Burst 的触发相位。

X 由 `X_magnetic_field` 通道输出 DC；Y 与 `rf_coil` 共用物理通道，Y 网格值作为
RF 正弦的 DC offset。Y RF 使用固定频率和固定幅度，只有 Burst phase 在相位扫描中改变。
`Time_sequence_2` 的下降沿同时启动 Z 控制和 Y RF Burst，实体分配线需要运行前人工确认。

## 扫描与采集

X/Y 扫描范围、点数及相位轴读取本轮参数。采集顺序为 X 外层、Y 蛇形；聚合数组仍按规范 X/Y 坐标保存。
实际相位顺序为相差 180° 的相位成对排列。相位轴须覆盖恰好一个周期、无重复并包含至少 8 个偶数相位点；
6 点虽足够某些拟合，却不能用于当前异常检测，因此在连接前拒绝，而不是采集后才报错。

每个相位点先重新 arm Y RF，再关闭温控并等待系统稳定后采集 Demod0 R；R 标准差超过门槛时保存
该次尝试并重采，最多执行配置的次数。每个 XY 点完成首轮相位扫描后，使用 R 相位曲线的
非线性色散模型稳健残差（MAD）识别偏离较大的跨相位点；未检出异常时保留现有一阶谐波保守兜底。异常点不超过
`PHASE_OUTLIER_MAX_REACQUIRE_POINTS` 时逐点重新 arm RF 并重采一次，重采结果替换进入拟合，
原始文件、重采文件、重采前后残差和掩码均写入 `xy_rf_phase_response_scan.npz`。
异常点超过上限时不自动重采，但会保留检测报告。每个 XY 点完成后执行正式非线性色散拟合；
同时用 `fit_phase_scan()` 保存 `abs(C + A*sin(phi-phi0))` 和 `C + A*abs(sin(phi-phi0))` 作为历史兼容诊断。
拟合不通过的 XY 点不会中止网格采集；只有单相位 R 标准差质量重试用尽才阻断后继采集。
重采后仍被检测为异常的相位点（包括超过重采上限而未重采的情况）会写入拒绝原因，
该 XY 点在汇总地图中标记为无效，但其全部原始曲线和拟合参数仍会保留。

采集时的跨相位重测和该 XY 点的正式接受判定均使用同一套非线性色散模型；
重测判定使用模型残差的稳健 MAD，最终判定额外检查 R²、协方差和响应信噪比。
旧的绝对值正弦拟合只作为兼容诊断保存，不再决定是否重测或接受该点。

离线分析阶段对保存的 Demod0 R 曲线重新使用非线性色散模型：

```text
B_eff(phi) = sqrt(A_rf^2 + C_res^2
                  + 2 A_rf C_res cos(phi - phi_res))
R(phi) = |scale (B_eff - B0) / ((B_eff - B0)^2 + width^2)|
```

其中 `A_rf` 取运行配置中的实际 RF 相位扫描幅度，`scale`、`B0`、`width`、
`C_res` 和 `phi_res` 在每个 XY 点独立拟合。该拟合只使用 Demod0 R；不需要
`kx`、`ky` 或 RF 转换系数的预先标定，X/Y 电压只作为网格坐标。该结果是
响应空间中的等效参数，不能单独用来唯一反推出物理的 `kx`、`ky` 或 RF 标定。
分析器保留采集时的绝对值正弦结果作为兼容对照，同时输出
`xy_rf_phase_response_nonlinear_maps.png` 和非线性参数地图。R²、响应峰谷差与
中位点噪声门槛默认按运行配置执行，历史重算可独立覆盖拟合最低 R² 和幅度显著性，覆盖值不写回采集参数。
两者与原有公式、最大 48 个拟合起点、协方差及拒绝规则共同生效；门槛失败的点仍保存参数、掩码、原因和图，不选择最佳 XY。

## 模块与可执行步骤

正式采集入口仍为 `experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response.py`，分析入口为同名 `_plot.py`。
模块目录为 `lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/`：

| 模块 | 职责 |
| --- | --- |
| `models.py`、`scan.py` | 稳定参数契约、轴和蛇形/配对顺序 |
| `preparation.py`、`excitation.py` | 无硬件预检、双来源冻结、工作点、Y RF arm |
| `acquisition.py`、`phase.py` | 单点质量重试、单轮异常重采、原始聚合和采集期拟合 |
| `workflow.py`、`session.py` | 完整/分步共用编排；复用串行命令、修订、取消和安全恢复 |
| `run_format.py`、`stages.py` | 采集/分析/历史共用格式资格；步骤依赖、参数展示和失效范围 |
| `analysis_steps.py`、`analysis_plotting.py` | 输入验收、原非线性算法及三组 paper 图 |
| `analysis.py` | 独立历史草稿、内容指纹、分阶段执行和下游产物归档 |

| 步骤 | 前置 | 实际内容及质量边界 |
| --- | --- | --- |
| A01 | 无 | 检查参数、映射、锁相能力和完整输出包络，冻结理论换算或闭环来源内容；不连接硬件 |
| A02 | A01 | 新建本轮目录、复制来源、连接并记录初态；GS200 保存原态 |
| A03 | A02 | 严格时钟同步、TEC 目标、Mx 工作点、首个 X/Y、持续 Z 与共同触发 |
| A04 | A03 | 蛇形网格、配对相位、逐点质量重试及单轮异常重采；逐尝试恢复温控后落盘 |
| A05 | A04 | X/Y 归零关闭、温控及 GS200 原态恢复；成功后标记本轮完成，A03 起失效 |
| A12 | 无 | 从任意阶段安全结束并释放会话；未采完或恢复失败不标记完整运行 |
| B01 | 无 | 明确目录的身份、格式、完成/安全记录、完整原始数组、相位顺序及实际速率资格 |
| B02 | B01 | 正式非线性重新拟合、历史诊断、三组 PNG/PDF 和 `evaluation.json` |
| B03 | B02 | 从本轮评价保存完整 `analysis.yaml` / `analysis.json` |

清单是执行顺序来源。只运行勾选节点，缺前置直接拒绝，不自动补跑；质量失败停止后继。
来源或配置内容变化使相关节点失效；重跑已执行的采集会新建目录，旧数据不覆盖。
未执行的参数草稿不改变已下发配置、历史参数快照或安全恢复所用参数。

## GUI 运行与历史

`/experiments/mx-z-optimal-control-xy-rf-phase-response` 提供“运行 / 历史”双 Tab。
运行页左右两列分别为采集与独立分析；保存参数与布局、派生预览、预检、完整运行、取消和任务状态在流程顶部。
每个节点可同时展开自己的参数，基础常显、高级折叠；共享 RF 幅度、X/Y 坐标及拟合门槛显示同一份值和分类。
工作点初始条件只在 A03 展示。改参先显示草稿及失效预览，应用后必须显式勾选重跑；预检失败不会启动。
“下载可编辑流程图”指向本包的 `workflow.excalidraw`，不是架构 Tab。

分析对象默认“下一次实验”，B02 门槛与 A04 的同键采集参数同步。选定具体历史目录后，门槛成为该目录独立覆盖值，
两个目录与下一次实验的草稿互不覆盖；此时保存默认仍只保存下一次实验参数及统一分类。
历史填回只修改当前表单，不自动保存或启动；历史保留参数快照、三组科学图、JSON/YAML/PDF 下载。
等待中的活会话仍占全局硬件锁；刷新可恢复，后端重启不重连硬件；取消、通信 EOF 和后端关闭均先安全收尾再释放锁。

## 数据与分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](../plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

两组参数地图采用三行两列布局；相位图使用周期色图 `twilight`，范围固定为 0–360°，接受标记范围固定为 0–1。逐 X 相位曲线最多三列，随 X 点数动态增加行数，保留所有 Y 曲线及拒绝标记，超过 12 个 X 点也不会遗漏或越界。完整曲线概览用于诊断，投稿主图需另选面板。

```text
data/Mx_Z_Optimal_Control_XY_RF_Phase_Response/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv        # 理论模式
    source_optimal_control_params.csv          # 理论模式
    source_z_calibration_analysis.yaml         # 理论模式
    source_corrected_control_waveform.npz       # 闭环模式
    source_manifest.yaml
    applied_control_waveform.npz
    grid_X###_Y###_phase_###_attempt_##.npz
    xy_rf_phase_response_scan.npz
  results/
    xy_rf_phase_response_maps.png
    xy_rf_phase_response_curves.png
    xy_rf_phase_response_nonlinear_maps.png
    *.pdf                                    # 三张图的同名 PDF
    evaluation.json
    analysis.yaml
    analysis.json
    before_<timestamp>/                      # 重算仅归档所选步骤及其下游旧产物
  analysis_state.json
```

分析器只读取运行目录，输出非线性模型的 selected phase、residual RF amplitude、resonance
amplitude、width、R²、RMSE 和拟合有效性二维地图，并列出全部拟合失败点；旧正弦模型结果
作为兼容对照保留。不自动选择最佳 XY 点，也不执行 RF 幅度灵敏度或噪声分析。

新采集在初始化写入 `raw_format_version: 1`，与参数 `schema_version: 2` 独立。
无格式标记的历史只兼容已验证的 schema v1/v2、`xy_grid_rf_phase_response` 且完整完成的运行；
不推断未来格式、布尔版本或未完成/安全恢复失败的运行。原始文件不重写，也不依赖外部来源仍在线。
输入内容、分析版本、覆盖值和产物 SHA256 绑定阶段；即使大小/mtime 不变也检测内容变化，旧修订拒绝。
重算前先验收输入，损坏输入不移动上次完整科学结果。历史 GET 只读；真正分析只更新分析状态和结果，不改变采集参数。

离线入口必须明确指定运行目录，不再自动选择 latest：

```powershell
.\\agent_exp_env\\Scripts\\python.exe experiments\\Mx_Z_Optimal_Control_XY_RF_Phase_Response_plot.py "<明确的运行目录>"
```

重分析会写入选定目录；做历史比对时先复制到临时目录再显式指定，不在原历史目录试跑。
本项无硬件回归分别位于包内 `test_workflow.py`、`test_analysis.py`，后端
`GUI/backend/test_mx_z_optimal_control_xy_rf_phase_response.py` 和前端同名页面测试。

## 安全结束状态

正常、取消和异常路径均保留 `Time_sequence_2` 当前状态，关闭归零 X 场和 Y RF，恢复 GS200 运行前状态，
并将温控恢复为 `5 V DC + Output ON`。Z 最优控制波形和 Output 保持，Pump/Probe、Pump 载波/门控
和 锁相放大器 设置保留。
