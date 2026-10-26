---
title: Mx Y RF Probe 光功率与失谐灵敏度优化
type: Mx_Y_RF_Probe_Detuning_Optimization
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  probe_laser:
    role: scan
  Probe_laser_power:
    role: scan
  Pump_laser_power:
    role: fixed
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
  - DLC_PRO
learned_notes:
  - PZT Scan Offset 仅作为未经光频标定的 Probe 失谐代理量。
  - PZT 为外层、Probe 功率为内层蛇形扫描，每个 PZT 行只等待一次稳定时间。
  - Probe 起点与终点相等且点数为 1 时，兼容为固定 Probe、仅扫描 PZT。
  - 本实验不连接 TEC103、不设置目标温度，也不等待温度稳定；每条记录按“设置 Y RF→关闭 Temp_Switch→SYSTEM_SETTLE_S→读入内存→恢复温控→保存”执行。
  - 每个网格点完整复用 Mx Y RF 灵敏度的响应、噪声和双斜率分析。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - 实验期间关闭 DLC pro 内置扫描并将扫描幅度置零，结束后恢复进入实验前的完整状态。
experiment_id: mx-y-rf-probe-detuning-optimization
defaults_file: params/experiments/mx-y-rf-probe-detuning-optimization.yaml
---

# Mx Y RF Probe 光功率与失谐灵敏度优化

## 离线模块与运行目录

分析模块可在仓库外、未设置运行上下文时导入，不创建运行目录或产物；原公开导出保留。以 `python -m lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.analysis` 执行时，必须通过 `LAB_RUN_DIR` 指定具体目录；缺失或不存在时明确拒绝，不回退最新目录。薄脚本仍沿用各自的 CLI 参数规范。

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。校相、扫描和噪声共用该等待；组首配置前移，组内每次换点或换态后重新等待，保留原分组和 OFF/ON 配对边界。

本实验明确不连接 TEC、不设置温度，也不判稳；隐藏的历史温度参数只为配置兼容保留，不参与设备连接。PZT 行切换的 `PZT_SETTLE_TIME_S` 在温控开启时完成，与每条记录的 `SYSTEM_SETTLE_S` 分开；温控恢复等待保留。异常时也先尝试恢复温控，再抢救已读数据，保留原始异常。参数迁移与共享时序见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和同一解调器。本实验固定使用幅度等效线宽，不执行频率扫描。每个网格点重新准备响应解调，再采集零 RF 噪声；分析按各自最终实际率计算。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-y-rf-probe-detuning-optimization.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 实验目标

在主磁场和 Pump 光沿 Z、Probe 光沿 X、待测 RF 场沿 Y 的 Mx 构型下，固定 Pump 光功率，扫描 Probe 光功率控制电压和 TOPTICA DLC pro Laser 1 的 PZT Scan Offset。Probe 轴既可以进行多点二维扫描，也可以固定为单点，仅扫描 PZT。每个网格点执行一次完整 Y RF 幅度响应和零 RF 噪声测量，以校准后的平坦频段灵敏度中位数寻找最优工作点。

PZT 电压仅作为 Probe 激光失谐的代理坐标。当前实验不包含 PZT—光频标定，因此结果不得解释为 MHz 或绝对光学失谐。

## 扫描与采集

- PZT Scan Offset：范围与点数读取参数文件，作为外层升序轴。
- Probe 功率控制电压：由参数文件指定；需要二维扫描时设置起点/终点/点数。
- 只扫描 PZT 时，将 `PROBE_POWER_START_V` 和 `PROBE_POWER_STOP_V` 设置为同一个固定电压，并将 `PROBE_POWER_POINTS` 设置为 `1`。示例（非当前默认值）：固定 Probe=0.3 V 时填写 `0.3 / 0.3 / 1`。
- 当 Probe 点数大于等于 2 时，Probe 起点仍必须严格小于终点；点数为 1 但起止值不同的配置会在预检阶段拒绝。
- Pump 功率固定为 `FIXED_PARAMS.Pump_laser_power`；结束恢复已实际执行配置的 Pump/Probe 基准，两路保持开启。基准 Probe 参数为 `FIXED_PARAMS.Probe_laser_power`，与 Probe 扫描轴独立。
- 实验不连接 TEC103 及其映射串口，也不执行目标温度设置或稳定等待；气室温度必须由实验外部预先稳定。
- 每次切换 PZT 后等待 `PZT_SETTLE_TIME_S`；同一 PZT 行内改变 Probe 功率不额外增加 PZT 等待，每次响应/噪声读数仍执行系统稳定等待。
- 每个网格点的 RF 频率、带符号幅度轴、响应时长及零 RF 噪声段数/时长均读取当前参数文件，原完整灵敏度采集算法不变。
- `Temp_Switch` 仍正常控制：每个频率点、幅度点和零 RF 噪声段均先设置 Y RF，再切到 `0 V DC + ON`，等待稳定并采集；随后恢复为 `5 V DC + ON` 并执行恢复等待。
- 单个 R 点达到重试上限仍超过 `std(R)` 阈值时，仅在本点安全收尾成功后将该网格点标记为无效并继续；PZT 回读、设备通信、配置或安全错误立即终止实验。质量失败同时遇到 RF 归零或温控恢复失败时，保留原质量异常与收尾诊断并停止后继点。

采集入口：

```text
experiments/Mx_Y_RF_Probe_Detuning_Optimization.py
```

离线分析入口（必须显式指定目录；缺少参数时报错，不自动选择 latest）：

```text
experiments/Mx_Y_RF_Probe_Detuning_Optimization_plot.py <run_dir>
```

## 原始数据

```text
data/Mx_Y_RF_Probe_Detuning_Optimization/<run>/
  experiment_config.yaml
  raw/
    probe_detuning_grid.npz
    point_manifest.yaml
    points/
      pzt_000_probe_000/
        amplitude_scan.npz
        amplitude_*_attempt_*.npz
        noise_*.npz
  results/
    optimization.yaml
    optimization.json
    optimization.npz
    sensitivity_heatmap.png
    slope_heatmap.png
    zero_point_sensitivity_heatmap.png
    zero_point_slope_heatmap.png
    hwhm_heatmap.png
    best_point_full_analysis.png
    best_zero_point_full_analysis.png
    grid_inputs.json
  acquisition_state.json
  analysis_state.json
```

参数版本仍为 schema v2；新采集独立记录 `raw_format_version: 1`。兼容已验证的无 raw 标记、schema v1/v2 且身份/清单/原始字段匹配的历史；未知格式拒绝，不重写旧 raw。

`point_manifest.yaml` 保存每个点的 PZT 请求值、Scan Offset 回读、PZT 实际电压、Probe 功率、采集状态和失败原因。清单在每点后更新，取消或异常后仍可重新分析已完整保存的点。

## 分析与最优点

离线分析复用 `mx-y-rf-sensitivity` 的绝对值色散拟合、PSD、幅度等效 HWHM、自动平坦段识别和两种斜率方法：

1. 色散拟合零点导数法。
2. 零点两侧最近实测数据的局部斜率法。

两种方法分别输出矩阵、排名和最优点，目标均为最小化 `flat_median_ft_per_sqrt_hz`。固定 Probe 时矩阵退化为单列 PZT 结果，热图、排名和最优点文件格式保持不变。质量无效点只保留诊断图，不进入有效矩阵或排名。分析器只写入建议工作点，不连接仪器，也不会自动应用最优设置。

## 交互流程图与运行/历史

本实验清单显式声明 `run_step_id=A07`、`finish_step_id=A09`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

入口：`/experiments/mx-y-rf-probe-detuning-optimization`。运行页复用 XY 采集/独立分析双列布局，40 个可见参数在节点内按基础/高级分类编辑；保存参数与布局、派生预览、仅预检、完整运行和任务日志均位于流程顶部。下载的[可编辑源图](../../lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/workflow.excalidraw)与后端[模块清单](../../lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/stages.py)一致，不另建静态架构 Tab。

| 节点 | 业务与依赖 | 主要产物/门槛 |
| --- | --- | --- |
| A01 | 启动预检，无前置，不连接仪器 | 参数、映射、锁相能力和安全范围 |
| A02 | 连接与 DLC pro 初态回读；依赖 A01 | 身份、健康、出光、初态扫描包络检查；不连接 TEC |
| A03 | 时钟同步；依赖 A02 | 按设备库目标写入并严格回读 |
| A04 | 主场、光功率和辅助场归零；依赖 A03 | 已执行配置与输出状态 |
| A05 | Pump 连续载波与常开门控；依赖 A04 | 保持原激励，不增加校相 |
| A06 | 静态 PZT/响应解调准备；依赖 A05 | 关闭内置扫描、幅度归零、响应实际率 |
| A07 | 蛇形网格完整灵敏度；依赖 A06 | 原网格/逐点 NPZ 与清单；质量重试规则如上 |
| A08 | 本轮恢复与封存；依赖 A07 | 恢复失败不得标记成功 |
| A09 | 安全结束会话，无前置 | 空闲时结束，执行中使用任务取消 |
| B01 | 加载网格与原始资格，无前置 | 验证结构后生成 grid_inputs.json |
| B02 | 双斜率拟合、排名与结果图；依赖 B01 | 复用原科学算法，无有效点仍保存诊断并标记质量未通过 |

- 只执行勾选节点，缺少有效前置时拒绝，不自动补选。完整运行与分步共用准备/采集/分析函数。每轮扫描新建独立目录，重跑不覆盖前轮 raw；完成 A08 后再次扫描须显式重跑 A06。
- 主场、光功率、Pump 仅在实际设置节点展示；响应时间常数等在准备和每点重新准备处共享编辑，值与分类同步。编辑只形成草稿，预览使用后端的参数影响范围；应用成功后必须显式重跑，不因保存、仅预检或应用自动采集。未执行的新草稿不用于旧轮安全收尾。
- 分步等待时连接、输出及全局硬件锁仍保持；刷新恢复存活会话。取消、EOF 或后端关闭自动收尾，完成恢复后释放硬件锁；后端重启不会从磁盘恢复硬件会话。
- 分析列默认编辑下一次完整实验的分析参数。选择具体运行目录后，仅编辑该目录支持的拟合/阈值/标定覆盖值；各目录草稿独立，切换对象或主 Tab 后保留。执行分析时生效，保存默认值只保存下一次实验参数及统一分类，不改历史采集快照。
- 历史 Tab 提供只读列表、参数快照、结果图/文件、填回与重新分析。浏览不触发硬件；填回不自动保存或启动。正在写入的目录拒绝分析；分步分析和重新分析使用同一目录互斥。
- 原始文件、配置和产物以内容指纹判断有效性；原始输入或前置产物变化会使后继失效。重算按阶段归档旧结果，B02 不移动仍在使用的 B01 输入；坏结构在归档旧产物之前拒绝。

实现职责：`preparation.py` 负责预检/连接/DLC pro 保护；`acquisition.py` 负责网格及行/点切换；`workflow.py` 只编排。分析入口通过 `analysis_steps.py` 调用现有共享网格拟合、PSD、排名及绘图算法，不复制或改写科学模型。

## DLC pro 状态与安全恢复

开始扫描前，工作流检查控制器和激光头序列号、系统与激光头健康状态、联锁、Laser Enabled 和 Emission。实验不会改变 Emission、Laser Enabled、激光电流或激光温度。

连接后记录初始 PZT Scan Offset、PZT 实际电压、扫描幅度和扫描启停状态。扫描期间关闭内置扫描并把扫描幅度置零，使 PZT 成为静态控制轴。正常、取消和异常路径均按以下顺序恢复：

1. 若已进入噪声模式，先按最后实际执行的参数恢复响应解调；不使用未执行草稿。
2. 按初始 PZT Scan Offset → 初始扫描幅度 → 初始扫描启停状态恢复 DLC pro。
3. 按最后实际执行的配置恢复 Pump/Probe 基准，保持两路开启。
4. 通过共享安全收尾归零关闭 Z 辅助场和 X/Y 场，将温控开关恢复为 `5 V DC + ON`，保持主场、Pump 连续载波与常开门控；不会连接或断开 TEC。

各类恢复独立尝试，PZT 恢复失败不跳过危险输出关闭。错误写入运行及会话状态，状态未知不得显示为安全完成。

若 DLC pro 写入或恢复回读失败，运行配置会记录“设备状态可能未知”，操作员必须检查 DLC pro 面板和 TOPAS。
