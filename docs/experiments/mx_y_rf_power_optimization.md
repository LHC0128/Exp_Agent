---
title: Mx Y RF 光功率灵敏度优化
type: Mx_Y_RF_Power_Optimization
scan_mode: nested_scan
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
    role: scan
  Probe_laser_power:
    role: scan
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
  - TEC103 为可选控制设备；串口不可用时告警跳过，仅设 UI 目标温度，不启停或判稳。
  - 完整复用 Mx Y 向 RF 场灵敏度的幅度等效线宽、R 响应和零 RF 噪声算法。
  - Pump 为外层、Probe 为内层蛇形扫描，矩阵仍按两条升序功率轴保存。
  - 每个功率点开始前强制恢复 Y RF 正弦波和 锁相放大器 响应配置，避免噪声采集状态泄漏。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - 二维网格使用 tqdm 显示完成比例、处理速度和预计剩余时间。
  - 原色散拟合导数法与零点实测数据斜率法分别输出矩阵、排名和最优点。
  - 所有结束路径恢复本轮已执行配置中的 Pump/Probe 基准光功率并保持输出开启。
experiment_id: mx-y-rf-power-optimization
defaults_file: params/experiments/mx-y-rf-power-optimization.yaml
---

# Mx Y RF 光功率灵敏度优化

## 采集时序与系统稳定

实际仪器配置完成后关闭 `Temp_Switch`，等待唯一的 `SYSTEM_SETTLE_S`，采集结果先留在内存，恢复温控后再保存波形、采集元数据和扫描快照。初始化目录及初始配置提前保存。每个功率点内的各幅度点、重采和噪声段分别执行完整时序；新光功率在首个关温控窗口之前设置。本实验只采 R，不增加校相或 OFF/ON 配对。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。设备、参考时钟与响应解调的准备在温控开启时完成，温控恢复等待保留。参数迁移、异常数据保护和适用边界见[统一采集规范](../acquisition_timing.md)。


## 锁相采集方式与实际速率

响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；本实验固定幅度等效线宽，不执行旧模式1扫频；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](../instruments/lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../../params/experiments/mx-y-rf-power-optimization.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](../instruments/lockin_amplifier.md)。

## 实验目标

在主磁场和 Pump 光沿 Z、Probe 光沿 X、待测 RF 场沿 Y 的 Mx 构型下，二维扫描 DG900 的 Pump/Probe 光功率控制电压。每个功率组合执行一次完整的 Y RF 幅度响应和零 RF 噪声测量，以校准后的平坦频段灵敏度中位数寻找最优工作点。

## 模块化入口与交互流程

运行页为 `/experiments/mx-y-rf-power-optimization`，保留「运行 / 历史」两个主 Tab。采集和分析两列共用现有 XY 流程容器；保存参数与布局、派生预览、预检、完整运行、取消及任务日志位于流程顶部，结果在其后显示。

40 个当前可见参数全部在节点编辑，基础名称常显，高级参数折叠。R 坏点阈值在 A08/B02、恢复光功率在 A04/A09 共享值和分类；主场等初始化条件只在配置节点展示。参数草稿不改执行记录，存活会话需先「应用参数到会话」再显式勾选重跑；还原草稿即清除重跑预览。保存或预检不自动执行模块。

| 节点 | 职责、前置与产物 |
| --- | --- |
| A01 | 无硬件预检：参数、映射、锁相能力和安全限值 |
| A02 → A03 | 连接与初态回读 → 全部已连接设备参考时钟同步及严格回读 |
| A04 → A05 | 主场、基准光功率和辅助场归零 → 可选 TEC 目标温度 |
| A06 → A07 | Pump 连续载波、常开门控 → Y RF 正弦与 R 响应解调准备 |
| A08 | 依赖 A07，执行完整蛇形网格；保存原 `power_grid.npz`、`point_manifest.yaml` 和逐点 NPZ |
| A09 | 依赖 A08，安全收尾并恢复已执行配置的基准光功率，封存本轮；再次采集需显式重跑 A07/A08 |
| A10 | 无前置，安全结束会话并释放硬件锁 |
| B01 | 检查明确目录的身份、格式、网格索引与路径；缺失或损坏波形由 B02 原算法逐点记录拒绝原因 |
| B02 | 依赖 B01，原逐点双斜率科学算法、矩阵、热图与诊断；无有效最优点保存诊断、标记质量未通过并阻断 B03 |
| B03 | 依赖 B02，从已有优化结果生成 `analysis.yaml/json` 双排名汇总，不重新采集或拟合 |

清单、参数影响和可编辑原图由实验包中的 `stages.py`、[workflow.excalidraw](../../lab_workflows/experiment_modules/mx_y_rf_power_optimization/workflow.excalidraw) 提供；后端和子进程都拒绝缺前置或过期修订，绝不自动补跑未勾选步骤。设备准备、网格采集、分析与报告分别在 `preparation.py`、`acquisition.py`、`analysis_steps.py`；`workflow.py` 和 `analysis.py` 只负责编排，完整运行与分步共用业务函数。

等待下一步仍保持连接、输出和全局硬件锁。刷新可恢复存活会话；取消、异常、通信 EOF 和后台退出均先收尾再释放锁。后端重启不从磁盘恢复硬件。每次重跑网格新建独立目录，不改写前轮 raw、参数和已封存记录。

分析列默认编辑「下一次实验」的自动分析设置，与同键采集参数共享。切换到具体目录后，仅允许覆盖 B02 声明的拟合门槛、R 坏点阈值、线圈标定、最低统计频率、线性检查范围和斜率一致性容差；各目录草稿独立保留，不修改采集快照或下一次实验。保存默认值始终只保存下一次实验参数与统一分类。内容指纹检查配置、递归 raw 和阶段产物；重算只归档本阶段与后继产物，B03 不移动仍使用的 B02 输入。

历史列表与摘要只读，展示本次参数、采集/分析状态、原热图和双排名文件；「填回参数」不自动保存或运行。「重新分析」与分步分析对同一目录互斥，正在写入的目录拒绝分析。原参数 schema v1/v2、无 raw 标记的已验证旧网格继续兼容；新运行独立记录 `raw_format_version=1`，不重写历史。

离线薄入口必须显式传入运行目录，不能隐式选择 latest：

```powershell
.\agent_exp_env\Scripts\python.exe experiments/Mx_Y_RF_Power_Optimization_plot.py data/Mx_Y_RF_Power_Optimization/<run>
```

## 扫描与采集

- Pump/Probe 扫描范围与点数由参数文件指定。
- 采集顺序固定为 Pump 外层、Probe 内层蛇形扫描，不随机化也不重复整个网格。
- 设置新光功率后不增加独立等待；首个 RF 幅度点配置完成并关闭温控后，执行唯一的 `SYSTEM_SETTLE_S`。
- 每个功率点使用配置的 Y RF 频率、幅度轴、噪声段数与时长。
- 每个功率点开始幅度扫描前，工作流都会按配置频率重新配置 Y RF SINE，并把 锁相放大器 Demod0 恢复为配置的响应采样率、时间常数和滤波阶数；因此上一点的零 RF DC 和高采样率噪声配置不会泄漏到下一点。
- 每个 Y RF 频率点、幅度点和零 RF 噪声段均按“设置 Y RF → 关闭温控开关 → 等待稳定 → 采集 → 恢复温控开关 → 等待”的顺序执行；重采和异常路径同样恢复温控开关。
- 进入 Pump/Probe 二维网格后，`tqdm` 按完整功率点更新完成比例、处理速度和 ETA；GUI 日志每完成一点会输出一行可持续刷新的进度快照。
- 单个 R 点重试达到配置上限后仍超过 `std(R)` 阈值时，仅在本点安全收尾成功后将该功率组合标记为无效并继续；设备通信、配置或安全错误立即终止实验。质量失败同时遇到 RF 归零或温控恢复失败时，保留原质量异常与收尾诊断并停止后继点。

原始数据按以下结构保存：

```text
data/Mx_Y_RF_Power_Optimization/<run>/
  experiment_config.yaml
  raw/
    power_grid.npz
    point_manifest.yaml
    points/
      pump_000_probe_000/
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
    points/
      pump_000_probe_000/
        analysis.yaml
        full_analysis.png
```

`point_manifest.yaml` 在每个功率点后更新，因此取消或异常后仍可对已经完整保存的功率点手动重新分析。

## 最优点判据

离线分析逐点复用 `mx-y-rf-sensitivity` 的绝对值色散拟合、PSD、幅度等效 HWHM 和自动平坦段识别。每个采集完整的功率点都会在 `results/points/<point>/full_analysis.png` 生成双面板完整分析图；上图标题同时标出 Pump/Probe 控制电压和质量状态。被严格质量门槛拒绝的点仍会继续计算仅供排障的诊断谱，标题标为 `DIAGNOSTIC ONLY - INVALID`，其数值不会写入有效矩阵、不会参与排序。

原方法保持不变：只有响应拟合成功且平坦段识别成功的点才参与 `ranking`，目标为最小化 `flat_median_ft_per_sqrt_hz`。

额外的零点实测数据斜率法使用全局色散拟合确定 V0，在零点两侧各取至少 2 个最近实测点并补足至少 5 点，通过 R 对 |V-V0| 的局部回归计算斜率。它独立输出：

- `optimization.npz` 中的 `zero_point_flat_median_ft_per_sqrt_hz`、`zero_point_slope_v_per_vpp` 和 `zero_point_valid_mask`；
- `zero_point_sensitivity_heatmap.png` 与 `zero_point_slope_heatmap.png`；
- `optimization.yaml/json` 中 `zero_point_method.ranking` 和 `zero_point_method.best_point`；
- `best_zero_point_full_analysis.png`。

新方法仍要求全局响应拟合成功，因为 V0 和幅度等效 HWHM 来自该拟合；局部斜率必须为正有限值，且自动平坦段识别必须成功。局部 R² 作为诊断信息与警告保存，不单独作为硬拒绝门槛。分析只报告两种方法各自的最优点，不会把仪器自动切换到该功率。

若没有有效点，分析器仍保存矩阵和逐点失败原因，并明确报告未找到可用最优点。

## 安全结束状态

正常、取消、异常和通信结束均归零关闭 Z 辅助场和 X/Y 场，恢复温控开关为 5 V DC + ON，并恢复本轮已执行参数 `FIXED_PARAMS.Pump_laser_power` / `FIXED_PARAMS.Probe_laser_power` 的基准光功率、保持两路输出开启。Pump 载波和常开门控按最后成功执行的配置保持，主场及锁相设置按参考实验策略保留，正常结束仅断开 TEC。已应用但未显式重跑的新参数不改变收尾基线。恢复失败明确报告，已有内存数据仍在尝试恢复后抢救，原采集异常不被清理错误覆盖。
