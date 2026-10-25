---
title: 静磁场灵敏度局部优化（已归档，DC 逐点）
type: Static_Magnetic_Field_Sensitivity
scan_mode: nested_scan
experiment_id: static-sensitivity-optimize-local
defaults_file: params/experiments/static-sensitivity-optimize-local.yaml
mapping_keys:
  Pump_laser_power:
    role: scan
  Probe_laser_power:
    role: scan
  Time_sequence:
    role: scan
  Z_magnetic_field:
    role: scan
  main_magnetic_field:
    role: fixed
  lockin_r:
    role: detection
required_devices: [GS200, signal_generator, lockin_amplifier, TEC103]
learned_notes:
  - 每个候选恢复同一基准，只覆盖一个变量，不累计上一维的最优值。
  - 新采集为 DC 逐点，不使用 RAMP 或 Z SYNC/AUX 触发。
  - 批次分析只重建已保存的单点结论，不重新拟合子运行。
---

# 静磁场灵敏度局部优化（已归档）

本实验已按[统一归档约定](../../old/experiments/README.md)移出活动注册表。GUI 只保留卡片和旧书签的归档提示，不提供参数、运行、会话、历史或重新分析入口，相关旧 API 返回 410；不能仅修改目录归档标记恢复。稳定 ID、默认参数及与主实验共用的 `data/Static_Magnetic_Field_Sensitivity/` 历史保持原位，未改写历史数据。以下实验过程与接口说明均为归档前实现及人工恢复参考，不代表当前可用的 GUI 能力。

## 归档前实验方案

在已有工作点附近按维度、候选值的给定顺序逐一测量。每个候选先重设完整基准，只改变目标变量，并重新完成 Pump 门控、锁相色散配置和 Bell-Bloom 校相。**不采用坐标下降，不累计前一维最佳值，也不自动施加推荐点。** 基准重复点照常执行，用于评估本轮漂移。

按用户确认，单点测量改用[静磁场灵敏度主实验](static_mag_sens_v2.md)的 **Z 纯 DC 逐点扫描**：连续采集 X/Y 并计算 R，保存逐点完整波形与中位数；随后 Z 归零关闭，采集 Y 噪声。拟合、Welch PSD、频率轴及灵敏度修正均由标准静磁场分析器执行，不再从 RAMP 时间重建电压。

归档前默认参数读取[版本化 YAML](../../params/experiments/static-sensitivity-optimize-local.yaml)，该文件原位保留，当前 GUI 不再提供本实验的默认值保存；历史真实参数以对应 `experiment_config.yaml` 为准。不会用当前静磁场主实验已保存的默认值覆盖局部优化基准。

## 模块与接口

归档实现位于 [`old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/`](../../old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/)，正式回归和原图一并保留，GUI 专属源码为恢复参考快照。`models.py` 管参数、旧键迁移和候选安全校验，`preparation.py` 构建计划，`acquisition.py` 调用标准 DC 工作流及即时分析，`workflow.py` 仅编排与统一收尾。`analysis_steps.py`、`analysis_plotting.py` 与 `analysis.py` 分别负责数值、绘图和独立阶段编排。

完整运行与分步会话共用 `Workflow.execute()`，通过公开的静磁场 `Workflow` 方法组合子运行，不复制设备配置和 DC 采集算法。静磁场工作流的可选进度回调仅用于转发嵌套进度，原入口默认行为不变；优化页始终显示父批次 ID、修订和节点状态。

| 节点 | 职责与前置 | 输入、产物或门槛 |
| --- | --- | --- |
| A01 | 无硬件预检；无前置 | 全部基准及候选的安全限值、GS200 量程、映射和锁相能力 |
| A02 | 冻结候选表；A01 | 批次标签、基准、扫描序列；写初始配置与 `raw/optimization_manifest.json` |
| A03 | 连接与时钟；A02 | 为首候选创建标准 DC 子目录，连接复用，时钟严格回读，不执行色散扫描 |
| A04 | 逐候选测量；A03 | 每候选重设基准与唯一覆盖量、重校相、DC 色散、Z OFF 噪声、安全恢复后即时拟合；更新冻结结论 |
| A05 | 封存；A04 | 核对最后子运行安全报告；全部坏点仍可封存并导出诊断 |
| A06 | 安全结束；无前置 | 恢复最后实际执行配置和危险输出，只断开 TEC，释放硬件锁 |
| B01 | 独立输入资格；无前置 | 父/子身份、原始格式、计划与参数对应、路径和冻结单点结论 |
| B02 | 原决策规则；B01 | 基准重复中位数、漂移、最佳有效点与改善比例 |
| B03 | 汇总与图表；B02 | CSV/YAML、PNG/PDF、分析摘要；最佳点仅作建议 |

采集节点为 A01→A02→A03→A04→A05，A06 可单独结束空闲会话；分析为 B01→B02→B03。不自动补选前置。A04 的每次重新执行创建新批次及新子运行，不覆盖先前原始数据。

## 参数与迁移

- `BASELINE_PARAMS` 在 A02 编辑，包含七个实际变量：Pump/Probe 光功率、Pump 占空比、目标温度、主场电流及 X/Y DC 补偿。每候选重新从此基准生成，不沿用上一候选的覆盖量。
- `OPTIMIZE_SEQUENCE` 在 A02 以 JSON 编辑，例如 `[{"parameter":"Pump_laser_power","values":[0.2,0.3]}]`，此为格式示例而非当前默认值。可扫描上述七个变量；顺序和重复值保留。旧 YAML 对象数组明确迁移为当前 JSON 文本，不更改外部键。
- `RAMP_LOW/RAMP_HIGH` 保留旧键，但现在表示 **DC 扫描两端**。`DISPERSION_STEPS`、`DISPERSION_DURATION_S` 表示点数和每点采集时长；不把旧整帧时长猜作 DC 单点时长。
- `DISPERSION_TEMP_SWITCH_INTERVAL_POINTS` 控制分组，零表示整段一组；末尾不足一组仍保留。`SYSTEM_SETTLE_S` 同时用于每次配置/门控后的校相、DC 点和噪声稳定；`DISPERSION_TEMP_SWITCH_ON_SETTLE_S` 保留固定恢复等待，GS200 换挡等待另行保留。
- `HF2_*`、`PUMP_MOD_*`、`RF_GATE_*`、`NOISE_*`、`GS200_CURRENT_RANGE_A`、`GS200_RANGE_SETTLE_TIME`、`Z_V_TO_NT` 保留已发布键。局部优化使用单个指定 GS200 量程，数值以实际回读和噪声旁文件为准。
- 旧 `RAMP_FREQ/RAMP_SYMMETRY/DAQ_DURATION/DAQ_TRIGGER_*` 仅为隐藏兼容键，不再驱动硬件。`SCAN_VARIABLE` 只允许 Z。旧基准中的 `Time_sequence/Time_sequence_2` 不迁移为新输出；时序2保持当前状态，Pump 门控按标准静磁场的专用参数配置。旧 `Temp_Switch` 只接受安全恢复电平。
- 旧序列 `stabilize_time` 合并为唯一系统稳定等待，显式新键优先。参数 schema 为 3，批次 raw 格式为 1，标准 DC 子运行仍使用独立的 raw 格式 3；参数版本不当作原始格式判断。

## 采集、安全与硬件边界

连接前校验基准和全部候选；输入固定为高阻、DC 耦合，不连接真实硬件进行导入/schema/预检。HF2/MFLI 按当前映射、选件、频率和采集模块能力检查，不回退仪器或更改接线。自动 DAQ/Scope 规则和最终实际率与静磁场主实验一致。

每组严格执行：配置实际仪器 → 温控 **0 V DC + ON** → 系统稳定 → 读入内存 → 温控 **5 V DC + ON** → 保存。组内换点后重新等待，组末恢复后统一保存当前组；恢复失败也先尝试恢复，再抢救已读波形并停止后续候选。文件写入、分析与绘图不放在关闭温控的窗口内。TEC 只下发 UI 目标温度，不启停、不判稳，连接失败告警跳过，未测实际温度为 null。

采集、设备通信、恢复或写盘异常立即停止；取消、通信 EOF、后台关闭和异常走同一 `finally`。仅在单点采集及安全恢复已经成功后，离线拟合质量失败可记录为坏点并继续。坏点不参加排名，无效值不补零、不插值。

结束恢复最后**实际执行**的色散参数与相位，Z 归零关闭并关闭 SYNC，温控恢复；保持主场、光功率、Pump 载波/门控、时序2和固定 X/Y DC 补偿，仅断开 TEC。不用尚未执行的参数草稿改变收尾，不把断开通信当作关闭危险输出。安全恢复失败不报告安全完成。

## 归档前的运行、历史与流程图

以下能力已撤下；当前访问 `/experiments/static-sensitivity-optimize-local` 只显示归档提示。归档前主视图只有「运行／历史」。运行页复用采集/分析双列容器，顶部集成保存参数与布局、派生点数预览、预检、完整运行、取消及任务日志。26 个可见参数位于 A02/A04；基础名称常显，高级项折叠，分类可移动并保存。基准初始化只在 A02 展示，过程参数在 A04 统一编辑，不为重复显示而伪造其他父节点的使用关系。

修改存活会话后先显示未应用草稿和后端声明的重跑范围；应用只使受影响节点失效，必须显式重跑。还原草稿会清除预览；保存默认值不执行节点。等待下一步保持连接、输出与全局硬件锁；空闲用 A06/「结束会话」，执行中用任务安全停止。刷新恢复存活会话，后台重启不从历史恢复硬件。

分析对象默认为下一次实验，也可选择已结束批次。**父批次无分析覆盖参数**，B01–B03 只读已冻结的即时单点结论并重建排名，不重跑子拟合，不改下一次参数、子目录或历史采集快照；`Z_V_TO_NT` 属于下一次候选测量的即时分析设置。具体目录没有可保存的独立数值草稿，切换对象不把历史参数混入下一次设置；顶部保存始终只保存下一次参数和统一分类。

历史列表严格区分父批次与标准 DC 子运行，显示原快照、分析状态、基准漂移、最佳点、图和下载；填回只填已有字段，不自动保存或启动。批次重分析与分步分析共用目录互斥，仍在写入/等待封存的批次拒绝分析。原图为 [`workflow.excalidraw`](../../old/experiments/lab_workflows/experiment_modules/static_sensitivity_optimize_local/workflow.excalidraw)，现可从归档源码读取；当前无运行页下载入口。

## 数据与结果

仍使用 `data/Static_Magnetic_Field_Sensitivity/`，不搬运主实验或旧优化历史：

```text
<批次运行目录>/
  experiment_config.yaml
  raw/optimization_manifest.json
  acquisition_state.json
  analysis_state.json
  results/
    analysis_inputs.json
    optimization_decision.json
    optimization_local_summary.csv
    optimization_local_summary.yaml
    optimization_local_summary.png / .pdf
    analysis.json / .yaml
<各候选的独立 DC 子运行>/
  experiment_config.yaml
  raw/dispersion_scan.npz
  raw/dispersion_steps/point_*.npz
  raw/noise_raw/gs200_range_*/noise_*.npy
  results/…
```

父清单保留候选顺序、基准标记、状态、子运行 ID、拟合质量、灵敏度、斜率和线宽。子目录保存完整仪器/映射快照、实际率、完整波形及标准静磁场结果。原始错误与清理结果分别记录，中断批次保留未执行点和已读数据。

决策沿用原规则：只选择拟合有效且灵敏度有限的点；有效基准重复的中位数作为参照，极差/中位数超过 30% 警告漂移，相对改善至少 10% 才建议采用最佳点。没有有效基准时明确说明，全部无效仍输出诊断图和报告。阶段输入/产物以 SHA 指纹校验，重算只归档受影响后继产物，不移动有效前置和原始文件。

旧 `optimization_local_summary.yaml` 批次按真实的五字段结构识别，保留原 CSV/PNG/YAML 下载、已有数值和可识别参数填回。旧点目录既不伪装成 DC 子运行，也不凭缺失配置猜测 RAMP 对称度重拟合；父重分析仅聚合原有结论，原文件不修改。缺失参数不以当前默认值冒充历史测量值。

## 归档入口与恢复验收

采集与分析薄入口位于 `old/experiments/experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py` 和同目录的 `_plot.py`。它们保留原行为，采集入口仍会操作真实仪器，不作为无硬件检查运行。历史 Python 包导入仅在显式请求本 ID 时通过归档搜索路径加载，不是 GUI 入口。

归档完整性、卡片、旧 API 的 410 和主实验保留由以下无硬件回归检查：

```powershell
.\agent_exp_env\Scripts\python.exe -m pytest GUI/backend/test_archived_experiments.py -q
```

原采集／分析与 GUI 正式回归均已原字节归档；恢复时须先核对路径、当前共享接口和专属会话接线，再按现行实验验收规则执行，不能把快照存在视为恢复验收通过。归档前完整运行自动执行父分析，采集薄入口仅做采集及候选即时分析；恢复后的离线分析仍须显式指定批次目录，禁止默认 latest。用户需安全结束现有会话后自行重启后端加载归档状态，本次不重启、取消或停止用户服务。
