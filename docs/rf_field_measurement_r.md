---
title: RF 灵敏度（DirectAW R）
type: experiment_type
description: 在 DirectAW 准备与 XY 校相后先采集 Demod3 R 绝对色散响应，再独立改频执行 Demod3 校相与零 RF X 噪声采集。运行/历史双 Tab 提供依赖流程图、节点参数编辑、分阶段离线分析和多次运行对比导出。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
experiment_id: rf-sensitivity-aw-r
defaults_file: params/experiments/rf-sensitivity-aw-r.yaml
schema_version: 2
scan_mode: point_by_point
mapping_keys:
  X_magnetic_field: {role: fixed}
  Y_magnetic_field: {role: fixed}
  Z_magnetic_field: {role: scan}
  Time_sequence_2: {role: fixed}
  main_magnetic_field: {role: fixed}
  Pump_laser_power: {role: fixed}
  Probe_laser_power: {role: fixed}
  Pump_modulation: {role: fixed}
  Time_sequence: {role: fixed}
  Temp_Switch: {role: fixed}
  temperature: {role: fixed}
  lockin_r: {role: detection}
required_devices: [GS200, signal_generator, HF2, TEC103]
learned_notes:
  - A13 在 A12 之前执行，编号保留但依赖与目录判断使用流程清单顺序。
  - R 绝对值曲线的尖点导数不能当作拟合中心斜率，使用底层色散中心斜率幅值。
  - 响应与噪声的 RF 频率独立；跨频率增益一致性及等效线宽补偿待实测验证。
  - HF2 AuxOut2 使用已确认的 ±10 V 安全规则，缺少范围或预计越界禁止启动/写入。
---

# RF 灵敏度（DirectAW R）

本实验与 [RF 灵敏度（DirectAW）](rf_field_measurement.md) 共用仪器准备、XY DirectAW 校相、
有符号 RF 采集、PSD、安全收尾及 GUI，会分别保存默认参数和运行历史。
正式 ID 为 `rf-sensitivity-aw-r`，模式为 `typed_workflow`。默认参数的唯一来源是
[新实验参数文件](../params/experiments/rf-sensitivity-aw-r.yaml)，初始设置从当前 DirectAW 文件复制；
历史参数以各自的 `experiment_config.yaml` 为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 入口与参数

GUI 路由为 `/experiments/rf-sensitivity-aw-r`，提供“运行 / 历史”双 Tab。
完整采集执行 `agent_exp_env/Scripts/python.exe experiments/RF_Field_Sensitivity_AW_R.py`；
离线分析执行 `agent_exp_env/Scripts/python.exe experiments/RF_Field_Sensitivity_AW_R_plot.py <run_dir>`，
只读取明确指定的目录，不连接仪器。

`Z_RF_FREQ` 为 A10/A13 的响应扫描频率，`Z_RF_NOISE_FREQ` 为 A12/A14 的校相/噪声频率，
二者独立编辑和保存，不随 AW 自动改变。共同触发沿用原实验；AW 仍要求触发间隔内为整周期，
本实验 RF 可为非整周期。A11 的三个相位扫描字段隐藏，正 RF 相位始终使用 `Z_BURST_PHASE_INIT`，
负幅度加 180°，每点用 `set_amplitude()` 和 Burst 重臂，保持共同触发和 XY 波形连续运行。

## 采集模块与依赖

| 节点 | 行为 | 前置 |
| --- | --- | --- |
| A01 | 映射、参数、锁相能力、波形和 AuxOut2 安全预检 | 无 |
| A02 | 复用物理资源连接设备 | A01 |
| A03 | 按设备库同步并严格回读参考时钟 | A02 |
| A04 | 初始输出；关闭 XY/Z，恢复温控为 5 V DC + ON | A03 |
| A05 | 温度稳定；TEC 不可用时记录外部温控状态 | A04 |
| A06 | Pump 载波与门控 | A05 |
| A07 | XYZ 共同触发及通道 SYNC | A06 |
| A08 | Bell-Bloom / Demod0 校相 | A07 |
| A09 | 上传 XY DirectAW、迭代校相与质量检查 | A08 |
| A10 | 回环初始化，同步设置 RF / Demod3 响应频率 | A09 |
| A13 | 扫描有符号 RF 幅度，记录 Demod3 R、X/Y、时序与统计量 | A10 |
| A12 | 同步切至校相/噪声频率，按正 RF 校相幅度校准 Demod3 | A13 |
| A14 | 关闭 Z RF，在零 RF 工作点采集 Demod3 X 噪声 | A12 |
| A15 | 恢复响应滤波，安全结束本轮；保持设备会话和硬件锁 | A14 |
| A16 | 安全收尾并结束设备会话，通信结束、取消或异常也会收尾 | 无 |

A11 跳过。分步执行按上表依赖和顺序排列，不按编号排序；A13 后单独执行 A12/A14 仍写入同一目录。
重跑 A13 自动恢复响应频率并使 A12/A14/A15 失效，创建新运行目录保留旧原始数据。
修改校相/噪声频率只使 A12 及其后继失效；新轮需要沿用的有效响应副本记录来源运行 ID。
仅执行 A01 不使已完成的步骤失效。XY 校相未收敛或信号过低时保存诊断并阻止后继，分步模式可重试。

## 节点编辑与历史

参数在节点内分基础/高级编辑，允许同时展开多个节点；共享字段在各节点同步值与分类。
草稿先显示依赖失效范围，应用后显式重跑；执行中禁止受限修改，校验失败保留草稿。
保存参数与布局、派生预览、预检、完整运行、取消和任务状态位于流程容器。
刷新恢复存活会话；后端重启后不恢复硬件连接，需要重新建会话。

下一次实验的分析参数与采集设置共用值，具体运行目录使用独立分析覆盖值；切换分析对象保留各自草稿，
保存默认值只保存下一次实验设置。历史填回只改变表单，不自动保存或运行。
历史支持同实验多次运行对比、自定义单行英文图例及 PNG/PDF 导出；X 标定实验的数据不能混入 R 对比。
界面和图表明确标注“R 标定 / X 噪声”，拟合失败结果明确标记“估算”。
可编辑流程图源文件位于
[RF 灵敏度（DirectAW R）.excalidraw](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/RF%20灵敏度（DirectAW%20R）.excalidraw)，
运行页提供下载，接口为 `/api/experiments/rf-sensitivity-aw-r/workflow/diagram`。

## 离线分析

B01 校验完整原始数据；B02 以有符号 RF 场轴拟合

\[
R(B)=\left|\frac{A(B-B_0)}{(B-B_0)^2+\gamma^2}+C\right|,\qquad
S=\frac{|A|}{\gamma^2}.
\]

这里的 S 是底层色散函数中心斜率的幅值，用于换算 X 噪声。复用现有绝对色散拟合，
不增加 R² 或参数不确定度门槛，保留其数值有效性、中心和线宽检查。
拟合失败时回退为 `max(abs(gradient(R, B)))`，记录原因和估算标记，关闭线宽补偿。
B03 使用每条噪声记录的实际采样率计算 Welch PSD，并按已有规则对齐频率轴后平均。
B04 保存原始灵敏度 `sqrt(PSD_X)/S` 及补偿灵敏度；拟合成功时沿用
`gamma_nT × LARMOR_PER_NT` 作为等效频域 HWHM，补偿因子为 `sqrt(1+(f/HWHM)^2)`。
分阶段与完整分析共用算法和缓存；前置或产物变化后不能复用过期结果，重算先归档受影响产物。
全部图表使用英文标注与 `lab_workflows.plotting` 的 `paper` 样式。

当前换算假定响应频率的 R 标定中心斜率可用于噪声频率的 X 噪声。
频率、该未验证假定以及等效线宽补偿来源都写入分析结果；增益一致性和等效线宽仍须实测验证。

## 接线与安全

接线沿用原实验：XYZ Ext Trig 共用 `Time_sequence_2` 主输出；
HF2 的 Demod0 Y → AuxOut2 → BNC → Signal Input2 → Demod3，索引分别为 AuxOut2=1、Input2=1、Demod3=3。
新实验同样拒绝 MFLI 的双主电压回采，不自动改接线或切换设备。

AuxOut2 使用设备库 ID `hf2_dev18246` 对应规则 `hf2_dev18246_aux_output_1`。
预检检查参数预期总输出；A10 写入前根据实际 Demod0 输入来源和量程重新计算
`offset ± abs(scale) × sqrt(2) × input_range`，通过已确认的 ±10 V 范围后才配置回环。
缺范围或预计越界时拒绝启动/写入，不使用只读 `lockin_r` 规则代替输出安全规则。
参考时钟严格回读；其他设置回读用作记录，DAQ 使用实际采样率。

收尾关闭 XY/Z，保留共同触发和 SYNC、Pump 门控及 `STANDARD_PRESERVED_OUTPUTS`，
温控恢复为 5 V DC + ON。不以断开通信代替关闭输出；收尾失败保留原始错误和安全报告。

## 数据结构

运行目录为 `data/RF_Field_Sensitivity_AW_R/<时间戳>_<tag>/`，包含 `experiment_config.yaml`、`raw/` 和 `results/`。
`response_data.npz` 保留 R/X/Y 均值、标准差、每点时序、幅度场轴、Burst 相位和响应频率；
`noise_D3_*.npz` 保存 X 噪声、实际采样率和噪声阶段 RF / Demod3 频率。
配置的 `stage_frequencies`、`hf2_response_snapshot`、`hf2_calibration_snapshot`、`hf2_noise_snapshot`
分别记录响应、校相和噪声；后续改频不得覆盖 R 标定快照。
结果包括 `response_fit.npz`、`psd_avg.npz`、`sensitivity.npz`、`analysis.yaml/json` 及四组 PNG/PDF 图。
