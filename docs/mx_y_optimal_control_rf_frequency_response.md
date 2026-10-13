---
title: Mx Y 最优控制 RF 频率响应
type: Mx_Y_Optimal_Control_RF_Frequency_Response
execution_mode: typed_workflow
scan_mode: nested_scan
mapping_keys:
  main_magnetic_field: fixed
  Z_magnetic_field: optimal_control
  Time_sequence_2: common_trigger
  X_magnetic_field: fixed
  rf_coil: scan
  Pump_laser_power: fixed
  Probe_laser_power: fixed
  Pump_modulation: fixed
  Time_sequence: fixed
  Temp_Switch: point_gate
  lockin_r: detection
required_devices: [GS200, DG900, DG4000, 锁相放大器]
learned_notes:
  - 最优控制阶段每个频率点完整采集 配置的 Y RF Burst 相位轴对应的 Demod0 R，响应按全部有效相位取中位数。
  - 启用常数对照后，最优控制扫描必须完整结束后才切换 Z 通道为恒定 DC 并扫描同频段。
  - 常数控制阶段 Y RF 相位固定 0°，每个频率点只保留一个被接受的采样点。
  - 常数控制的恒定 Z 电压由电流耦合标定反解，允许超出标定有效范围的全局线性外推。
  - 不做相位拟合、峰值、带宽或 Bloch 拟合。
experiment_id: mx-y-optimal-control-rf-frequency-response
defaults_file: params/experiments/mx-y-optimal-control-rf-frequency-response.yaml
---

# Mx Y 最优控制 RF 频率响应

当前默认参数：[参数文件](../params/experiments/mx-y-optimal-control-rf-frequency-response.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

实验在 Z 向闭环最优控制波形保持运行时，扫描 Y RF 频率与 Burst 相位，记录 Demod0 R。
可选地在最优控制扫描结束后，把 Z 通道切换为恒定 DC，再扫描一次 Y RF 频率作为对照。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 采集流程

单次运行按顺序完成两个阶段：

1. **最优控制阶段**：Z 通道输出闭环最优控制任意波，逐频率、逐 Burst 相位采集 Demod0 R。
   原始数据写入 `raw/optimal_control_phase_frequency_scan.npz`，数组形状为
   `(频率点数, 相位点数)`。
2. **常数控制阶段（可选）**：仅当 `COMPARISON_ENABLED` 为真时执行。先关闭 Z 通道输出与
   Burst，改用 `setup_dc()` 输出恒定电压，回读确认波形形状为 `DC` 且输出为 ON；随后逐频率
   采集单个 `0°` 相位点，写入 `raw/constant_control_frequency_scan.npz`。
   该阶段结束后把 Z 通道恢复为最优控制任意波并重新开启输出，再进入安全收尾。

两阶段共用同一套共同触发、Pump、温控、锁相放大器 与光路配置，只有 Z 通道输出形态和 Y RF 相位不同。

相位扫描不要求至少 8 个点；按配置的起点、终点和步进采集，包含首尾点。起点仍须小于终点，
步进须为正且整除扫描范围，例如 0–180°、步进 90° 对应 3 个相位点。每个相位点的 锁相放大器
时间序列仍要求至少 8 个有效样本，用于计算 R 均值和标准差。

## 常数 Z 电压反解

启用对照时，`build_constant_control_plan()` 按以下步骤求恒定 Z 电压：

1. 读取 `data/Mx_Z_Current_Coupling_Calibration/<运行>/results/analysis.yaml`，
   校验 `experiment_id` 为 `mx-z-current-coupling-calibration` 且 `success: true`。
2. 用 `f0 = K_Z_Hz_per_A · I + f_0_at_zero_current_hz` 反解目标线圈电流
   `I = (f_target − intercept) / slope`。
3. 取标定 `curves` 中全部 `fit.success: true` 的点（不分正反扫描方向），拟合
   `current_a = gain_a_per_v · z_bias_v + intercept_a`。
4. 反解恒定电压 `V = (I − intercept_a) / gain_a_per_v`，并调用
   `validate_safety_limit("Z_magnetic_field", V)`。
5. 若 `V` 落在标定电压支撑范围之外，置 `extrapolated: true` 并在结果中保留警告元数据。

运行配置记录标定运行名、`analysis.yaml` 的 SHA-256、拟合斜率与截距、目标 Larmor 频率、
目标线圈电流、目标恒定 Z 电压、是否外推以及标定有效点范围。

## 离线分析

结果图统一通过 `lab_workflows.plotting` 的 `paper` 配置导出同名 PNG（300 dpi）和 PDF；原 PNG 文件名继续用于历史预览。默认固定画布，英文标注，图幅和导出约定见[科研绘图规范](plotting_style.md)。重新分析生成新图，原始数据和数值结果字段保持原有契约。

`analysis.py` 只读取 `raw/` 中两个 NPZ 与 `experiment_config.yaml`，输出到 `results/`：

- `optimal_control_median_response.npz`：`frequency_hz`、`response_median_v`、
  `valid_phase_count`。
- `r_phase_frequency.png`：相位-频率诊断热图（x=相位，y=频率，color=Demod0 R）。
- `rf_frequency_response_comparison.png`：最优控制相位中位数曲线与常数控制曲线叠加。
  只画这两条曲线，不画差值、比值或归一化增益。
- `analysis.yaml` / `analysis.json`：响应定义、标定元数据、外推标记与有效点计数。

所有图使用 `lab_workflows.plotting` 的 `paper` 样式，图中文字为英文。

## 专属 GUI

实验 ID `mx-y-optimal-control-rf-frequency-response` 在 `ExperimentPage.tsx` 中路由到专属页面，
提供「运行 / 历史」两个 Tab。运行 Tab 含基础/高级参数分组、参数在两组间移动、预检并运行、
保存参数与布局以及最新结果预览；历史 Tab 提供运行列表、摘要、标定信息、结果图、填回参数与
重新分析。对照相关控件只在 `COMPARISON_ENABLED` 为真时显示，关闭时保留其取值。

参数移动只改变 `parameter_layout` 与分组归属，参数值不变；视图停留在当前分组，不自动切换 Tab
（在基础参数点击「移至高级」后仍停在基础参数，该字段从本组列表消失），并给出「已移至基础参数」
或「已移至高级参数」的提示。运行中移动按钮禁用。

历史列表只显示新格式运行（`experiment_config.yaml` 的 `schema_version >= 2`，
且 `raw/optimal_control_phase_frequency_scan.npz` 与 `results/analysis.yaml` 均存在）；
旧格式运行不进入该专属列表。

### 任务进度与动态 ETA

- 总采集点数为 `频率点数 × 相位点数`，启用常数对照时再加 `频率点数`。
- 每完成一个被接受的采集点（含坏点重采耗时）上报一次进度：采集阶段占用总进度的
  `0～90%`，自动分析从 `90%` 开始，任务结束为 `100%`。
- ETA 使用全部已完成点的累计平均耗时估算剩余采集时间；首个点完成前不显示 ETA，
  分析阶段只显示“分析中”。
- 子进程通过 `report_runtime_progress()` 以 JSONL 行协议上报，GUI 后端保存并在 SSE、
  `GET /api/jobs`、`GET /api/jobs/{id}` 中返回 `estimated_remaining_seconds`。
- 专属页任务状态保存在页面层，重新进入页面时自动恢复最新任务、百分比、日志与 ETA。

## 安全注意

- 恒定 Z 电压在设置前必须通过 `validate_safety_limit("Z_magnetic_field", V)`。
- Z 通道切换为 DC 后必须回读确认波形形状与输出状态。
- 常数扫描结束后恢复 Z 最优控制任意波；恢复失败会计入 `failure_reason` 并把完成状态改为
  `failed`，不会静默忽略。
