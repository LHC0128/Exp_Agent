# 实验迁移主交接

## 当前汇总与统计口径

文档只读整理基线日期：2026-10-24；后续任务按各自实际实现与验收更新状态和下表，未重验项目沿用既有结论。本文是唯一持续更新的迁移状态入口；运行中实验以 `lab_workflows/experiments/registry.py` 为准，代码归档以 `lab_workflows/experiments/archived.py` 为准。Notebook、未注册脚本和示例不纳入统计。

| 口径 | 数量 | 说明 |
|---|---:|---|
| 在维护的正式 Python 实验 | 45 | 45 typed_workflow、0 legacy_script；默认 `list_experiments()` 仅返回这些 ID，包含其它任务尚在验收的模式切换 |
| 代码归档，仅保留卡片 | 17 | 原模式 7 typed、10 legacy；未明确恢复前不迁移，旧 GUI 运行/历史 API 返回 410 |
| 完整目录 | 62 | 在维护与代码归档的并集，不从脚本数量推断 |
| 旧实验迁移基数 | 61 | 已完成 39 ＋ 待迁移 6 ＋ 归档排除 16；不含新增实验 |
| 已完成 | 39 | 8 项补齐、30 项迁移、1 项达标跳过；含完成后归档的 `rf-sensitivity-aw-r`；ConstXY、BB 功率扫描与 Z 场带宽各由对应任务增加一次 |
| 待迁移 | 6 | 按当前注册表为 6 typed、0 legacy；其它并行任务仍由各自验收，不自动选择下一项 |
| 归档排除 | 16 | 未完成迁移即归档，不算已完成或达标跳过 |
| 新增实验 | 1 | `rf-control-scale-sensitivity`；不计入旧实验迁移完成数 |

**两个“45”不是同一集合**：在维护范围包含新增实验、不包含完成后归档项；旧迁移口径的“完成＋待迁移”包含完成后归档项、不包含新增实验。后续只更新此处汇总及对应状态行，不在历史段落中追改当时计数。

新模式只表示强类型参数与执行契约，不等于 GUI、业务验收或硬件验证全部完成。表中“已完成”保留当时确认的迁移结论；自测/集成通过、独立复核通过与真实硬件验证分别判断。历史独立验收不代表本次针对当前源码重新做了独立验收；未验证或超时不得记为通过。

快速入口：[实验状态表](#当前实验状态表) · [当前验收与证据边界](#当前验收与证据边界) · [继续执行规则](#继续执行规则与验证方式) · [历史附录](#附录历史验收与异常记录)

## 当前实验状态表

以下保留原稳定 ID、迁移分类、完成计数和历史验收说明，修正已归档模块的实际位置，并按当前执行模式分组。执行模式与迁移验收状态分别维护：切换为 typed_workflow 后仍可处于待迁移状态，模式切换本身不增加完成计数。当前归档标记另见 `params/experiment_catalog.yaml`；代码归档不能只改该标记恢复。

### 新模式

| 稳定实验 ID | 当前模块位置 | 迁移状态及历史验收说明 |
|---|---|---|
| `bell-bloom-z-field-calibration` | `lab_workflows/experiment_modules/bell_bloom_z_field_calibration/` | 迁移完成并通过独立无硬件验收；10 个采集 / 4 个分析节点，保留专属结果与历史接口；索引原子保存防止覆盖失败损坏 |
| `detection-chain-frequency-response` | `lab_workflows/experiment_modules/detection_chain_frequency_response/` | 补齐完成并通过独立无硬件验收；8采集/3分析，恢复失败也抢救当前已读数据，旧四键NPZ明确兼容，完整23路径delivery通过 |
| `scope-capture` | `lab_workflows/experiment_modules/scope_capture/` | 迁移完成并通过独立无硬件验收；5采集/3分析、14字段，真实Scope分步会话与原波形/PSD/双曲线历史操作，恢复后原子保存与历史资格 |
| `static-sensitivity` | `lab_workflows/experiment_modules/static_sensitivity/` | 补齐完成并通过独立无硬件业务验收；12采集/4分析，0V+ON温控、实际率旁文件指纹、raw3独立资格；19路径delivery其它ID缺口失败如实保留 |
| `static-sensitivity-optimize-local` | `lab_workflows/experiment_modules/static_sensitivity_optimize_local/` | DC 逐点模块化及交互流程实现完成；6 采集／3 批次分析节点、26 可见参数，30 项目标 Python/API 和 6 GUI 定向通过；完整范围仍有跨实验关联缺口及全前端失败，暂不增加完成计数，证据和边界见下节 |
| `mx-y-rf-sensitivity` | `lab_workflows/experiment_modules/mx_y_rf_sensitivity/` | 迁移完成并通过最终独立安全复验；12 个采集 / 3 个分析节点，应用未重跑参数不污染 Pump/响应恢复，保留 R 解调、旧入口与历史格式 |
| `mx-y-rf-frequency-response` | `lab_workflows/experiment_modules/mx_y_rf_frequency_response/` | 补齐完成并通过独立无硬件验收及历史诊断补验；10采集/3分析、28字段，保留嵌套扫描与原γ/不确定度规则，旧NaN经只读接口展示而下载字节保留 |
| `mx-y-optimal-control-rf-frequency-response` | `lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/` | 迁移完成并通过独立无硬件验收；7采集/3分析、37字段，冻结控制/标定来源与常数对照，原专属结果/历史保留；一次历史误写已按SHA恢复并留限制证据 |
| `mx-y-rf-sensitivity-drift` | `lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/` | 迁移完成并通过独立无硬件验收；11采集/3父趋势分析、38字段，绝对调度与逐轮即时分析，父options={}，全无效诊断导出；106pytest条目/9GUI，真实历史原件保护 |
| `mx-z-optimal-control-rf-sensitivity` | `lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/` | 迁移完成并通过自验收+集成复核；8采集/3分析、52字段，保留两模式/原科学图与v5历史；来源冻结、已执行配置收尾和恢复失败保护，33 Python/API唯一节点+6 GUI |
| `mx-z-optimal-control-xy-rf-sensitivity` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/` | 已完成，自验收+集成复核通过；7采集/3分析、60字段，复用来源冻结与会话，保留原蛇形扫描/科学图；37 Python/API+6 GUI、14数组历史对照通过，原范围补验与全仓既有缺口见异常章节 |
| `mx-z-optimal-control-xy-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/` | 已完成，自验收+集成复核通过；7采集/3分析、42字段，44 Python/API+6 GUI及独立历史9数组对照通过；用户弃用受影响运行及关联材料，处置与全仓既有缺口见异常章节 |
| `mx-z-optimal-control-xy-leakage-response` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/` | 已完成，自验收+集成复核通过；7采集/3分析、35字段，41 Python/API+6 GUI及3份历史科学对照通过；保持共同触发当前状态，原范围补验与既有全仓缺口见异常章节 |
| `mx-z-optimal-control-xy-rf-phase-response` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/` | 已完成，自验收+集成复核通过；6采集/3分析、45字段，43 Python/API+7 GUI及121点历史逐字段对照通过；保留非线性色散与旧正弦诊断，不选最佳XY，原范围补验与既有全仓缺口见异常章节 |
| `mx-z-optimal-control-xyz-balance` | `lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/` | 已完成，自验收+集成复核通过；6采集/3分析、37字段，复用家族分步会话与来源冻结，保留实测三维最小点与逐层 V 形拟合；修复预检关键字拼写潜伏问题，8份历史临时副本对照通过，交付工具缺口与全仓既有缺口见异常章节 |
| `mx-z-optimal-control-dg4000-bias-xyz-balance` | `lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/` | 已完成，自验收与集成复核通过；6采集/3分析、40字段，复用会话与双来源冻结，保留 Z-offset 蛇形网格、原复数 Bloch 算法和 Z/触发/XY 原态恢复；48 Python/API、6 GUI、9份历史临时副本对照通过；完整范围与全仓边界见异常章节 |
| `z-aw-waveform-scope-check` | `lab_workflows/experiment_modules/z_aw_waveform_scope_check/` | 迁移完成并通过独立无硬件验收；6 个采集 / 3 个分析节点，保留电学例外和旧导出 |
| `z-coil-inductance-frequency-response` | `lab_workflows/experiment_modules/z_coil_inductance_frequency_response/` | 迁移完成并通过独立无硬件验收及原范围交付重跑；7 个采集 / 3 个分析节点，模型不可用仍可保存和导出严格 JSON 诊断 |
| `z-coil-current-frequency-response` | `lab_workflows/experiment_modules/z_coil_current_frequency_response/` | 迁移完成并通过独立无硬件验收及原 18 路径 delivery；7 个采集 / 3 个分析节点，精确同频至少两次有效重复才允许闭环相位合格；旧结果缺计数规则须从 raw 重分析 |
| `mx-z-current-coupling-calibration` | `lab_workflows/experiment_modules/mx_z_current_coupling_calibration/` | 迁移完成并通过独立无硬件验收及原 20 路径 delivery；11 个采集 / 4 个分析节点，98 项独立 Python/API 与 15 项 GUI 通过；两份历史副本科学结果一致，旧 JSON 只读兼容不改原文件 |
| `z-aw-current-waveform-scope-check` | `lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/` | 已完成；6采集/3分析、20字段，复用 XY 界面与 Z 波形会话，保留双来源与原电流算法；28 Python/API、4 GUI、两份历史临时副本对照通过；完整范围及既有异常见本轮记录 |
| `z-aw-closed-loop-waveform-correction` | `lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/` | 已完成 |
| `mx-y-rf-power-optimization` | `lab_workflows/experiment_modules/mx_y_rf_power_optimization/` | 已完成 |
| `mx-y-rf-probe-detuning-optimization` | `lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/` | 已完成 |
| `mx-main-field-calibration` | `lab_workflows/experiment_modules/mx_main_field_calibration/` | 迁移完成并通过独立无硬件验收；10 个采集 / 4 个分析节点，含应用参数后未重跑的收尾回归 |
| `mx-keithley-6221-main-field-calibration` | `old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-rf-sensitivity` | `old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-xyz-balance` | `old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-balanced-sensitivity` | `old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/` | 归档，不迁移 |
| `mx-main-field-noise-spectrum` | `old/experiments/lab_workflows/experiment_modules/mx_main_field_noise_spectrum/` | 归档，不迁移 |
| `mx-main-field-scope-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/` | 已完成 |
| `mx-z-field-calibration` | `lab_workflows/experiment_modules/mx_z_field_calibration/` | 迁移完成并通过独立无硬件验收；10 个采集 / 4 个分析节点，保留原科学拟合、诊断与历史 |
| `mx-z-noise-spectrum` | `old/experiments/lab_workflows/experiment_modules/mx_z_noise_spectrum/` | 归档，不迁移 |
| `mx-xy-residual-field-calibration` | `lab_workflows/experiment_modules/mx_xy_residual_field_calibration/` | 已完成 |
| `noise-spectrum-xy` | `lab_workflows/experiment_modules/noise_spectrum_xy/` | 补齐完成并通过独立无硬件验收；14采集/5分析，独立raw1与早期AMP兼容、内容指纹、通用分析资格与B01旧产物保护；完整delivery跨ID缺口失败保留 |
| `noise-spectrum-xy-known-noise` | `lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/` | 补齐完成并通过独立无硬件验收；15采集/6分析、56字段，静磁场raw3与窄旧均值兼容、归档前采样/预算保全、内容指纹；88项+47子场景及16GUI，完整Git跨ID缺口失败保留 |
| `noise-spectrum-xy-uncontrolled-probe-am` | `lab_workflows/experiment_modules/noise_spectrum_xy_probe_am/` | 补齐完成并通过独立无硬件验收；保留已有流程，补足配对故障、真实分析和 GUI 隔离证据；温控门控统一为 0 V DC 且 Output ON |
| `noise-spectrum-xy-demod3-r` | `lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/` | 已完成 |
| `photon-shot-noise` | `lab_workflows/experiment_modules/photon_shot_noise/` | 已完成；8采集／3分析节点、21字段，保留原 ASD² 加权拟合与历史格式，验证边界见异常章节 |
| `projection-noise` | `lab_workflows/experiment_modules/projection_noise/` | 已完成 |
| `z-field-bandwidth` | `lab_workflows/experiment_modules/z_field_bandwidth/` | 已完成；10采集／3分析节点、50字段；47项目标 Python/API、7 GUI及五份505频点历史对照通过，保留0 Hz背景、三模式、原带宽与早期标量R边界；工作区验收限制见下节 |
| `bb-duty-scan-burst` | `lab_workflows/experiment_modules/bb_duty_scan_burst/` | 迁移实现与专项无硬件验收完成；8 采集/3 分析节点、33 参数，保留门控输出/Burst/SYNC；53 项 Python/API（含共享回归）、52 子场景及 504 项前端通过，5 份历史共 50 点与原算法逐字段一致；完整范围仍有跨实验关联缺口，暂不增加完成计数，证据见下节 |
| `bb-power-scan-burst` | `lab_workflows/experiment_modules/bb_power_scan_burst/` | 迁移实现完成，定向无硬件自验收通过；8 采集／3 分析节点、33 可见参数，保留门控输出/Burst/SYNC 与最后写入 Pump 电压；70 Python/API＋62 子场景、6 GUI、类型与构建通过；全量验证仍有缺口／超时，见下节，不宣称全仓通过 |
| `rf-sensitivity-constxy-frequency` | `lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/` | 已完成；11采集／3分析节点、76可见参数，同一 agent 自验收与必要集成通过；0 Hz 保持 XY，仅关 Z，结束统一安全收尾；43 Python/API、7 GUI及五份历史对照通过，工作区验证边界见下节 |
| `rf-sensitivity-direct-aw-frequency` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/` | 待迁移 |
| `rf-sensitivity-aw` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw/` | 补齐完成并通过独立无硬件验收；参数 schema v4，17 个采集 / 4 个分析节点（含可选带宽步骤）；支持多控制独立采集分析、可关闭 MAD 异常谱审计和混合跨次对比，保留共用触发，兼容已验证的 v2/v3/v4 格式 |
| `rf-sensitivity-aw-r`（完成后归档，仅留卡片） | `old/experiments/lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/` | 补齐完成并通过独立无硬件验收；15采集/4分析，保留R标定与独立噪声频率/X噪声，真实分组/原子保存故障保护、63字段真实GUI；完整delivery跨ID缺口失败保留 |
| `rf-control-scale-sensitivity` | `lab_workflows/experiment_modules/rf_control_scale_sensitivity/` | 新增；多控制 × Scale，v2 每点独立四级校相，无人为噪声，兼容 v1 标称固定相位，独立采集/离线分析与交互流程图；不计入原迁移范围 |
| `rf-sensitivity-aw-known-noise` | `lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/` | 达标跳过并通过独立无硬件验收；17采集/4分析，固定噪声ON及各控制独立校相/标定；共享可关闭 MAD 异常谱筛选、逐控制与聚合审计，保留既有原始数组和 v2/v3/v4 历史兼容 |
| `t1-calibration` | `lab_workflows/experiment_modules/t1_calibration/` | 模块化与流程图实现完成；8 采集／3 分析节点、22 参数；CH1 差分、CH4 同步，X/Y/Z 归零关闭，保留 Pump/Probe 与门控。T1 专项 46 个 Python/API 节点及 6 GUI 用例无失败，完整范围仍有共享缺口与超时，暂不增加完成计数；证据见下节，未连接真实硬件 |
| `t2-calibration` | `lab_workflows/experiment_modules/t2_calibration/` | 待迁移 |
| `xy-direct-aw-dc-calibration` | `lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/` | 待迁移 |

`xy-direct-aw-dc-calibration` 的当前参数见[默认参数文件](../params/experiments/xy-direct-aw-dc-calibration.yaml)。
扫描电压必须落在 AW 输出幅度与 offset 可表达的范围内；超限时预检阻止启动。
迁移代码不会静默替换用户参数。

### 旧模式

| 稳定实验 ID | 实验 | 迁移状态 |
|---|---|---|
| `noise-spectrum-xy-v2` | XY 控制噪声谱 v2 | 归档，不迁移 |
| `rf-sensitivity-aw-frequency` | RF 灵敏度频率扫描（外部 AM） | 归档，不迁移 |
| `xy-aw-voltage-calibration` | XY 任意波电压标定 | 归档，不迁移；原采集／分析脚本位于 `old/experiments/experiments/`，GUI 仅保留卡片 |
| `xy-dc-voltage-calibration` | XY DC 电压标定 | 归档，不迁移；原采集／分析脚本位于 `old/experiments/experiments/`，GUI 仅保留卡片 |
| `aw-const-omega-check` | 恒定 Omega 标定检查 | 归档，不迁移；原采集／分析脚本位于 `old/experiments/experiments/`，GUI 仅保留卡片 |
| `aw-const-omega-awscale-check` | 恒定 Omega 标定检查（AWScale） | 归档，不迁移 |
| `static-sensitivity-optimize` | 静磁场灵敏度优化 | 归档，不迁移；原单体脚本位于 `old/experiments/experiments/`，无独立分析入口 |
| `aw-waveform-scope-check` | 任意波示波器检查 | 归档，不迁移；原脚本位于 `old/experiments/experiments/AW_Control_Waveform_Scope_Check.py`，GUI 仅保留卡片 |
| `dg-am-comp-transfer-check` | DG AM 到 COMP 传递检查 | 归档，不迁移 |
| `temperature-switch-pid-cycle` | 温控开关 PID 周期测试 | 归档，不迁移 |

迁移旧实验时保持实验 ID、`data_type`、历史运行目录和已发布参数键不变，并依次补齐
强类型模型、无副作用预检、取消检查点、异常安全恢复、离线分析入口和契约测试。

## 当前验收与证据边界

- **2026-10-24 静磁场局部优化 DC 迁移**：按用户确认改为与静磁场灵敏度相同的 DC 逐点连续波形中位数及 Z OFF Y 噪声；每候选重新从同一基准出发、只改一个量并重校相，不累计上一维最优值。6 采集／3 父分析节点、26 可见字段，复用公开静磁场 Workflow 接口、串行会话和双 Tab GUI；静磁场仅增加可选进度回调，原算法不变。父清单冻结单点结论，独立分析只重建基准漂移、排名和建议；没有历史覆盖值，不自动施加最佳点。旧批次仅聚合已有 RAMP 结论，历史原目录不迁移、不转换。
  - **目标无硬件证据**：30 个唯一 Python/API 节点通过（14 采集/参数、9 分析、7 API）；最终 GUI 6/6、TypeScript、生产构建通过。采集/参数见 `.cache/expcodegen/static-local-final.xml` 的对应 14 项；API 7 项见 `static-local-api-final.xml`（68.3 s），最后严格格式/布尔检查及两条真实分析入口复验见 `static-local-analysis-final.xml`（43.0 s）。`static-local-final.xml` 原批 86 项中有一次假子进程测试启动失败，随后修正测试执行目录并完整重跑 API，原失败不改写。包含真实父/子会话、完整适配器返回父目录、修订/前置拒绝、等待持锁、取消/EOF/后台关闭恢复后释放锁、DC 和噪声慢盘/恢复/读数/保存失败与部分数据抢救；全部测试只写临时目录，不连接硬件。
  - **历史科学结果**：真实 `0709_0951_opt_local` 的副本保留 15 点原科学字段及基准中位数、漂移、改善比例和建议，重建结果与旧 YAML 一致；代表副本重新生成原三维局部扫描图的 PNG/PDF。新 DC 假仪器运行的父指标逐值等于标准静磁场子分析。父分析不写子结果或采集快照，原历史文件仅只读访问，不声称对全部真实数据建立了新哈希验收。
  - **扩展范围仍有失败**：239 条目共享/集成批次有 3 项失败，原 XML `static-local-target-final.xml` 保留；本任务的旧温控迁移属性缺失和测试解释器路径问题已修复并复验，共享 `test_stage_declarations_match_registered_defaults` 在未修改的 `rf-control-scale-sensitivity` 默认关闭噪声时仍断言 2 阶段而失败，未改用户默认值或删测试。完整显式 39 文件范围的验证报告为 `.cache/expcodegen/static-local-validation-final.json`（42.83 s）：32 条其他 ID 的关联/场景缺口、`checks=[]`、`business_status=missing_mapping`；本 ID 无结构错误、场景或源码覆盖缺口，但工具未运行业务测试，目标通过证据来自上述真实 XML，不手改报告。记录全部实际共享调用方；不缩小文件范围或改验证器来宣称通过。GUI、生成类型、文档等 unmapped 项已按真实路由、schema、接口及引用人工核对。
  - **全前端如实保留**：默认并行 521 项为 462 通过／59 失败；保留范围和超时、仅限制 4 worker 后 503 通过／18 失败，本页 6 项在两批均通过；最后本页状态汉化和可聚焦诊断滚动后再次 6/6（含类型/构建），见 `static-local-gui-final.json`。原全批分别为 `static-local-frontend-all.json`、`static-local-frontend-4workers.json`，不称全套通过，也不擅改其他页面。
  - **桌面与收尾**：独立 Ego 空间、随机端口的全 mock 服务，1920×1080 双列各约 760.7 px；250 字符运行名、503 字符下载名及 6008 字符模拟诊断不造成页面横向溢出，诊断限高 240 px、可键盘聚焦滚动。验证缺前置拒绝、结束、历史及原图实际下载，下载字节与源一致，未模拟接口为 0。自建服务和浏览器空间已关闭、临时服务脚本已删除，未操作 8000/5173 用户服务；Ego 提示可升级，未升级。截图与布局数据位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-static-local-pwg8t519/`。新增[实验正文](static_sensitivity_optimize_local.md)、主实验/兼容/采集说明及两份 README 已同步；新说明受本地 `docs/` 忽略规则影响，尚未纳入 Git。独立子代理及真实硬件验证均未执行，现有会话需用户安全结束后自行重启后端加载接口。
  - **耗时**：本轮约 70 分钟，粗分为调研 12、主干实现 14、测试编写与修错 27、历史对照 1、全前端/构建 5、桌面与收尾 11 分钟；为墙钟估算，不重复累计并行工作。其中实际命令等待约 28 分钟，其余为阅读、实现与整理，返工不归因为硬件安全要求。必要证据保留在上述 XML/日志，不新增独立状态或耗时报告。

- **T1 模块化与交互流程迁移**：保留稳定 ID、`T1_Calibration` 目录、旧单点及扫描 NPZ；按用户确认使用 CH1 PDB 差分、CH4 同步下降沿，结束 X/Y/Z 归零关闭。8 采集／3 分析节点、22 可见参数，完整和分步共用业务；恢复温控后逐帧保存，失败保留原始异常和已读数据。旧单点、无波形旧扫描及真实 `0529_1405_test` 临时副本的 T1、协方差误差、倒数线性拟合对照通过；保留无阶段记录的旧图归档与超过 99 点的索引顺序。说明正文、默认来源、运行/历史 GUI 与可编辑原图均已同步。
  - 当前证据：采集/参数 28 个 T1 节点在本轮 XML 中无失败；最终分析/API 批次 18 项通过（68.672 s），两组去重为 46 个 T1 Python/API 节点。最后一次 GUI 专项 6 项通过（7.640 s）；24 个 Python 文件编译、API 类型生成、TypeScript 和生产构建通过。浏览器全 mock API 检查不连接仪器，复用现有响应式布局。
  - 完整范围 `.cache/expcodegen/t1-validation.json` 被共享注册表/requirements 的跨实验分析关联缺口拦截；未把结构计划记为测试通过。原计划全部 Python 目标另行执行，绕过 Windows 命令行长度限制但不缩减范围，900.047 s 超时，未完成项不计通过。包含修改过的共享测试文件的专项批次为 57 passed、1 failed：失败为并行 `StaticSensitivityOptimizeLocalParams` 缺少 `removed_temperature_keys`，该批整体未通过。前端全套两轮分别 460 passed／61 failed（94.62 s）、431 passed／90 failed（105.51 s），存在页面等待/测试超时，未提高超时或关闭并行规避。未修改其它实验来消除上述失败，也不宣称全仓通过。
  - 本轮记录：`.cache/t1-delivery-summary.json`、`.cache/expcodegen/t1-target-validation.json`、`.cache/expcodegen/t1-analysis-final.json`、`.cache/t1-planned-python-result.json` 与对应日志。专项分析报告通过不替代原完整范围报告；没有真实硬件验收。

- **本轮四项归档**：按用户要求归档 `static-sensitivity-optimize`、`aw-const-omega-check`、`xy-dc-voltage-calibration` 和 `xy-aw-voltage-calibration`，不执行模块化或流程图迁移。7 份专属脚本按原字节移至 `old/experiments/experiments/` 并记录 SHA256；保留稳定 ID、原注册 `data_type`、schema v2、参数键、采集／分析和安全收尾。GUI 仅留静态卡片，旧运行、参数和历史 API 返回 410；三个原离线分析入口保留于归档，全局优化仍无独立分析器。历史、参数和共享 CSV 不移动，尤其不拆分实际共用的 `data/Static_Magnetic_Field_Sensitivity/`。局部优化、静磁场主实验、XY DirectAW DC 标定和其它并行迁移不在本次归档范围；只减少待迁移数，不增加迁移完成数。汇总执行模式包含 T1／局部优化等并行任务已接入但尚在验收的切换，不代替这些任务更新完成状态。
  - 归档／目录 API 的 36 个 XML 节点无失败，前端 36 项通过（6.77 s），临时目录生产构建通过（4.14 s），10 个 Python 文件编译及两套 Skill 一致性检查通过。Python 整批 28.30 s、XML 含 36 通过／1 失败：额外共享注册表检查在并行局部优化模型缺少 `removed_temperature_keys` 时失败；原 pytest 日志另报告 37 passed、1 failed、49 subtests，不把整批记为通过。类型检查 1.38 s 失败于并行 `StaticSensitivityOptimizeLocalPage.tsx` 两处可空参数错误，未改该页面或模型。证据在 `.cache/legacy-four-archive/python-tests.xml`、`python-results.json`、`frontend-results.json` 及各日志。
  - 完整本次 31 路径验证器 42.72 s，`business_status=missing_mapping`、`checks=[]`，36 条其它实验关联／采集场景及并行迁移结构缺口，原报告为 `.cache/legacy-four-archive/validation.json`；不缩小范围、改验证器或宣称全仓通过。原脚本按字节／SHA256 核对，8 份共享输入 SHA256 不变，7863 份历史文件核对路径、大小与 mtime；初次全历史哈希读取在 45 s 超时，未宣称完成全量历史哈希。未连接仪器、运行采集脚本、操作用户服务或清理其他工作区改动。

- **本轮 `aw-waveform-scope-check` 归档**：用户明确取消迁移，原脚本按字节移至 `old/experiments/experiments/` 并记录 SHA256；不拆模块、不生成流程图、不修改硬件或绘图算法。默认注册表移除，GUI 仅保留静态归档卡片，旧参数、运行和历史 API 返回 410；无独立分析入口，旧 Python 显式兼容加载也不宣称可重新分析。共享波形 CSV、参数、历史数据与纯电学采集时序例外保留；不增加迁移完成计数。
  - 本次定向无硬件回归通过：29 项 Python/API＋54 子场景（命令 22.91 s）、32 项前端（5.12 s），类型检查 0.88 s、临时目录生产构建 3.23 s；修改 Python 编译和两套 Skill 一致性检查通过。原脚本 SHA256 与移动前一致，48 个历史／共享输入文件哈希未变。归档清单和两份 README、采集例外、旧入口及绘图链接同步；未连接仪器、未操作用户服务。
  - 保留完整本次 16 路径的验证器报告 `.cache/aw-waveform-archive/validation.json`：42.94 s、37 条注册表关联的既有 analysis／采集场景缺口，`business_status=missing_mapping`、`checks=[]`，不缩小范围或修改验证器冒称通过。定向结果见同目录 `python-tests.xml`、`python-results.json`、`frontend-results.json` 及日志；不代表全仓业务验收。

- **本轮 `bb-duty-scan-burst`**：按用户确认完成强类型参数、准备／激励／采集、科学拟合／绘图与编排拆分，完整与分步共用业务；提供运行／历史双 Tab、8 采集／3 分析节点、节点参数与草稿失效、独立目录分析及可编辑原图。保持稳定 ID、参数键、历史目录和原科学算法；按旧脚本真实收尾保留 Time_sequence 输出/Burst/SYNC，仅断开 TEC。正式正文为 [bb_duty_scan_burst.md](bb_duty_scan_burst.md)，两份 README、采集时序与旧分析说明同步；该新增文档受本地 `docs/` 忽略规则影响，尚未纳入 Git。
  - **最终专项通过**：48 项 Python/API、52 个子场景，0 失败／跳过，测试耗时 153.09 s；另有 5 项全注册会话生命周期回归通过（3.38 s）。专项验证器总耗时 186.812 s，实际值见 `.cache/expcodegen/bb-duty-scan-burst-business-validation.json` 为准；其采集、分析和参数均 `passed`，共享历史默认安全读取与真实假仪器子进程均覆盖。
  - **前端与构建**：真实路由 6/6（7.75 s）；同一前端全套 504/504 在 4-worker 并行下通过（144.49 s），未关闭并行、扩大超时或跳过测试，未修改持久测试配置。默认并行的两次全套曾分别有 114／85 项失败或超时，原 JSON 与日志保留于 `.cache/bb-duty-migration/frontend-tests.*`、`frontend-full-rerun.*`，不覆写为成功；完整通过证据为 `frontend-full-four-workers.json`。TypeScript、生产构建、25 个修改 Python 文件编译／3.10 语法和 Skill 一致性检查通过；API 已生成类型，旧配置读取兼容补丁后 schema 确认未变。
  - **真实历史只读对照**：5 份已有记录共 50 点，新旧拟合的全部返回字段逐项完全相等；570 个配置、原始数据及已有结果文件哈希不变。最早一份含 NumPy YAML 数组，只按已核实的一维 float64 格式白名单解析，任意 Python 对象仍拒绝，未使用 `unsafe_load`。仅在临时副本完成该记录的完整分析和 10 个 PNG/PDF 导出；26.38 s，证据 `.cache/bb-duty-migration/historical-numeric-comparison.json`。临时基准脚本及副本已清理，正式回归保留。
  - **完整范围仍未通过，不等同专项通过**：包含全部本次共享注册、映射要求、GUI 接线和历史 helper 的原范围验证器重跑 40.77 s，结果为 37 条跨实验关联／场景缺口、`checks=[]`、`business_status=missing_mapping`，没有手改成通过；报告 `.cache/expcodegen/bb-duty-scan-burst-full-validation.json`。专项是额外真实证据，不替代这份完整失败报告，暂不增加汇总完成计数。本轮未连接真实硬件、未启动或重启用户服务，也未进行独立子代理审查或真实 Python 3.10 解释器运行。

- **2026-10-24 `z-field-bandwidth` 完成（仅本 ID 增加一次）**：同一 agent 实施、自验收，无子 agent。准备、Pump 激励／校相、逐点内存采集／原子保存、原科学计算、绘图、完整／分步编排分离；10采集／3分析节点、50可见字段，复用既有流程容器、全局队列、修订与历史 API。保留参数 schema v2、原有效外部键、三种模式与目录，独立 raw v1；0 Hz 为 Z OFF 背景、XY 保持 OFF、正频率连续正弦，未引入 ConstXY 的载波／Burst／Bloch 拟合。校相质量失败可同连接重试，仍阻断后继与目录分析；非有限 Y/R 同样抢救诊断，恢复／写盘／部分连接清理失败不报告安全完成。新增[正式说明](z_field_bandwidth.md)，同步定义简介、薄入口、两份 README、采集和兼容正文；目录无本 ID 简介覆盖值，不新增覆盖。新说明受本地 `docs/` 忽略规则影响，尚未纳入 Git。未改设备库、真实历史或用户服务，当前 MFLI 映射仍连接前拒绝。
  - **最终47项目标 Python/API全通过**，包含于最新 `target-final.xml` 的 **59项＋52子场景**（另12项共享采集契约）。此前 `integration-final.xml` 为151项＋269子场景通过，含仪器／映射／时钟／锁相、会话生命周期、历史和目录；增加非有限读数抢救后已按最新源码重跑全部目标采集／分析／API，不把旧整批冒称已覆盖新增节点。24文件编译及 Python 3.10语法、Skill同步检查、API生成与fixture／清单一致性通过；未用3.10解释器或真实硬件运行。
  - **前端批次保留实际结果**：默认并发369通过／135失败，存在大量超时；限制2 worker、仍保留并行后503通过／1失败，目标7项全部通过。余下 `UpdatesPage.test.tsx` 按钮等待失败，文件未改，随后单独3项复验通过；整批仍不是全绿。并行任务的 BB 页面类型错误在其后续代码变化后消失，本任务未改其实现；最终TypeScript和临时目录生产构建通过。不提高超时、删测试或跳过失败来宣称全套通过。
  - **五份真实历史完整比数值**：每份101点，前三份各27、后两份各29个原科学数组及报告全部字段在rtol=1e-12、atol=1e-18内一致；早期标量R不伪装成矢量。仅显式临时副本写入，`0701_1039_z_bw`、`0701_1323_z_bw`两个代表完整生成新旧PNG/PDF，其它不冒称重新出图；原581份文件的路径／大小／mtime／SHA保持。
  - **39路径的完整本次验证仍失败**：50 selected、16 unmapped，因其它ID的37条关联／场景缺口而 `business_status=missing_mapping`、`checks=[]`；本ID无场景及源码关联缺口，工具未运行业务测试，实际证据来自上述XML。保留 `.cache/expcodegen/z-bandwidth-validation.json`，不缩小范围冒充通过，不改验证器或补其它实验。未映射的GUI／生成类型／文档接线已结合实际调用链和对应回归人工核对。
  - **桌面及收尾**：隔离Ego／随机端口mock，1920×1080双列各约760.7px；385字符运行名、1922字符诊断及492字符下载名未撑破页面。缺前置拒绝、历史切换、结束及原图实际下载验证，下载字节与源一致，未模拟API为0。浏览器空间和自建服务已关闭，8000／5173用户服务未操作；由用户安全结束现有会话后重启后端加载新接口。Ego提示更新，未升级。必要日志／XML／科学副本／截图集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-z-bandwidth-t3gcmv/`，主要入口 `target-final.xml`、`integration-final.xml`、`frontend-all-bounded.xml`、`science-results.json`、`desktop-metrics-final.json`、`desktop-history-metrics.json`；早期失败日志保留，不另建交接报告或机器状态副本。

- **2026-10-24 `bb-power-scan-burst`**：按用户确认的 A 策略完成强类型模块化、运行／历史与 8 采集／3 分析流程图；普通运行和分步共用真实业务。正常、异常、取消、EOF 均保留 Time_sequence 输出/Burst/SYNC 和最后实际写入 Pump 电压，温控恢复 5 V DC + ON，只断开 TEC。保留稳定 ID、外部键、原逐帧／平均 NPZ、历史目录及科学算法；原拟合逐键数值对照与冻结基准通过。新增正式说明 [bb_power_scan_burst.md](bb_power_scan_burst.md)，同步旧说明、两份 README 和 API 类型；本检出新增说明被 `.git/info/exclude` 的 `docs/` 规则忽略，尚未纳入 Git。
  - 本轮目标及相关共享回归实际完成 **70 项 Python/API＋62 子场景**，零失败／跳过，耗时 219.19 s；真实路由 **6/6**（10.32 s），TypeScript、临时目录生产构建（13.21 s）、修改 Python 编译／3.10 语法及 Skill 同步检查通过。证据为 `.cache/expcodegen/bb-power-scan-burst/target-final.xml`、同名日志及 `build.log`；没有连接真实硬件、启动或重启用户服务，也没有独立子代理验收。
  - **完整范围不通过**：保留全部本次文件（含共享注册表、映射要求和共享测试）运行验证器，首次外层 150 s 超时未产出正式 JSON；原范围重跑 80.61 s 得到 `validation.json`：37 条其它实验关联／场景缺口、`checks=[]`，本 ID 无场景或源码覆盖缺口但自动业务状态仍为 `not_run`，不手改为通过。上述目标回归以独立实际 XML 为证，不用缩小验证范围掩盖全范围失败。
  - 前端全量默认并行运行出现 85 项失败，保留 `frontend-all.json`；保留完整 64 文件／504 测试、原超时和并行，仅将 worker 上限设为 4 后重跑 129.82 s，**503 通过、1 超时**（`RFSensitivityKnownNoiseAcceptance.test.tsx` 的存活会话／共享参数测试），本实验 6 项在该批均通过。详见 `frontend-all-4workers.json`；未删场景、skip、加超时或修改测试配置，不宣称前端全套通过。其它并行任务和真实硬件结果仍由各自任务验证，本任务只增加本 ID 的迁移计数。

- **2026-10-24 `rf-sensitivity-constxy-frequency`**：本 ID 迁移完成，同一 agent 实施和自验收，未派子代理、不计入其它并行实验。强类型参数、准备／激励／校相／采集／存储／科学计算／绘图和编排分离，完整与分步共用业务；用户确认 0 Hz 仅关闭 Z、XY 保持开启，结束采用统一收尾。保留稳定 ID、外部键、参数 schema v2、原索引／点数据与科学算法，独立 raw v1；修复旧脚本 helper 定义顺序及关闭 Bloch offset 时的维数错误。正文、定义、已有目录简介和两份 README 同步；具体历史分析不改采集快照。
  - **目标最终 43 项 Python/API 全通过**（22采集／参数、14分析、7API），含真实假仪器完整与 stdin 会话子进程；`target-stable.xml` 整批 55 项还覆盖共享采集契约12项，0失败／跳过。历史／会话生命周期／目录的23项集成节点在 `target-final.xml` 中通过；该旧整批另有并行 BB 中间态造成的失败，保留，不改记全批通过。仪器／映射／输出策略／时钟／锁相71项、207子场景通过。26文件 Python 编译及3.10语法、Skill一致性、OpenAPI重新生成、TypeScript与临时目录生产构建通过；真实硬件和3.10解释器运行未验证。
  - **前端范围如实保留**：本页7项全部通过，含真实路由、共享／分类／草稿、依赖、历史隔离、布尔与空值恢复及旧接口安全结束。最新全前端 `frontend-final.xml` 为504项中的500通过／4失败，失败均为并行 BB Duty／Power 页面，不能称全前端通过；不擅改这些实验。桌面1920×1080双列、445字符运行名、1684字符诊断和504字符下载名无页面横向溢出；诊断限高240px并可键盘聚焦滚动，下载原图与源码字节一致。仅本任务 Ego 空间及随机端口 mock 服务已关闭，未操作用户服务；Ego 提示更新，未升级。
  - **验证器范围并未通过**：真实43路径、50 selected、19 unmapped；注册表／要求元数据的保守传播及其它 ID 关联缺口产生37条错误，`checks=[]`，本 ID 无缺口但工具状态仍为 not_run。手工目标／集成证据不能冒充该报告通过；19条 GUI、类型、说明与目录路径按实际调用链核对。原42路径失败报告与最新 `.cache/expcodegen/constxy-validation-final.json` 均保留，不缩小范围、不修改验证器或扩展修复其它 ID。
  - **历史只在临时副本对照**：全部五份、每份104个原NPZ字段及原JSON科学报告在 rtol=1e-12、atol=1e-18 内一致；`0701_1918_constxy_freq` 代表运行完整生成新旧图组，其余只做数值。583份真实历史的路径／大小／mtime／SHA保持，原54份参数中除目录配置外的53份及 Git index 保持；目录内其它并行修改保留。必要日志、XML、科学副本与桌面证据位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-constxy-8ultfki5/`，主要入口为 `target-stable.xml`、`instrument-final.xml`、`frontend-final.xml`、`science-results.json`、`desktop-final-metrics.json`、`preservation-confirmed.json`。初始红灯及中间态日志保留，未另建交付／验收／交接 Markdown 或机器状态副本。新增实验说明受本地 `docs/` 忽略规则影响，未强制加入暂存。新接口由用户在安全结束旧会话后自行重启后端加载；当前 MFLI 映射会按双主电压限制被预检拒绝。

- **没有本次重验的全仓结论**：附录的 selected/unmapped、checks=[]、失败批次、超时、服务 PID 和目录计数均为各任务当时记录，不是当前待执行清单、服务状态或全仓缺口数量。需要接续时按目标 ID、当前源码和现行验证规则重新确定范围，不将旧失败自动改成通过，也不为整理文档重跑全库。
- **验证规则以当前约定为准**：遵守 `AGENTS.md` 和 expcodegen 的交付验证说明；使用 `--changed-file` 等明确任务范围，保留真实共享调用方。分析/参数用 `REGRESSION_CASES` 关联实际源码和节点，修改源码须被来源或其依赖闭包覆盖，动态入口继续追踪共享依赖；缺少证据是待验证，不以结构检查或不相干测试替代。
- **采集与硬件边界不变**：参数或清单影响真实输出、时序、分组/配对、取消或安全收尾时，显式追加完整采集验收；不明确时保守追加。自动化只用假仪器，写入性分析使用明确的临时 run_dir，不运行默认 latest，不操作真实硬件或用户服务。
- **验证器后续复核事项（不是迁移计数变化）**：验证器最近一次 fresh 子代理验收在超时前发现源码覆盖、动态依赖和默认 YAML 测试的缺口，主代理修复后已有 58 项针对性回归通过；**完整独立复核仍未完成**，不能升级为“独立验收通过”，也不据此回退或增加任何实验迁移计数。本机现存必要工具证据为[独立验收状态说明](../.cache/expcodegen/independent-verification-status.md)和[修复复验记录](../.cache/expcodegen/independent-findings-fix-validation.json)；它们不是另行维护的迁移状态副本，不随 Git 同步，后续引用仍需先确认存在。

### 证据可用性（2026-10-24 只读核对）

历史“当时通过/失败”和“证据现在可访问”是两件事。证据缺失不证明当时未执行，也不能据此生成新的通过结论。本文不恢复机器状态副本，不补造证据，不删除事故记录或正式回归；确需追溯时先定位经核实的现存归档或原始日志。

- 原 `.cache/expcodegen/` 机器状态副本及 `checkpoint_history` 已停止维护。`current_attempt.*` 只保留为历史字段名，不再是可读取的当前状态入口。
- 旧记录称原文保存在 `old/experiment_modularization_handoffs.zip`，但本次未找到该文件，不能再将其当作现存归档入口。历史原文叙述留在本文附录，旧 ZIP 引用不代表已恢复文件。
- 抽查可直接解析的 12 条单文件缓存引用（不含通配/花括号简写），以下 9 条当前未找到。未列出的历史相对文件名、临时目录及包内文件未在本次逐份验证，不能一并宣称可访问。

| 原引用 | 本次可用性 |
|---|---|
| `.cache/expcodegen/business-13383f6816e740f29381ea785db0e587.xml` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/checkpoint20-history-incident-restoration.json` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-leakage-full-validation.json` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-noise-full-validation.json` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-noise-science.json` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-noise-workflow-final.xml` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-phase-workflow-recheck.xml` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-rf-delivery.json` | 当前未找到，保留为历史引用 |
| `.cache/expcodegen/mx-z-xy-rf-full-validation.json` | 当前未找到，保留为历史引用 |

## 继续执行规则与验证方式

1. 每次只处理用户指定的一个未归档稳定 ID；先核对状态表、实际注册表和代码归档。已完成项直接复用，不重复计数；不自动选择下一项，不迁移归档项。
2. 默认由一个主 Agent 负责实现、自测、集成及交接。**未经用户授权不派子代理**；用户明确授权时可用 fresh 上下文的只读子代理独立验收，主 Agent 汇总证据并负责最终判断。子代理超时/启动失败不算验收通过，不用主代理自测冒充独立复核；历史“未派子 agent”的事实不改写。
3. 没有异常时更新本文对应状态行及唯一汇总，不再同步、重建或维护任何已停用机器状态副本；不另写逐项交付/验收/交接报告或重复工作区快照。正式测试及验证工具必要 XML/日志可保留，不能作为另一份持续状态清单。
4. 有异常时在“当前验收与证据边界”写明未完成项、实际验证范围及经核实的证据入口；失败/未完成不计作新完成。历史失败和事故经过留在附录，解决后追加当前结论，不覆写旧批次。
5. 保留用户参数、历史数据、已有暂存/未提交改动和其他任务接线。用户服务不自动重启、停止或取消；新增接口需用户在安全结束旧会话后自行重启加载。事故材料仅按用户明确授权处置，不为精简文档删除。

### 效率优化与耗时统计

以下优化减少重复工作和验证返工，不降低采集时序、输出安全、取消／异常收尾、部分数据抢救及既有必要回归要求。

- **复用已有能力，控制改造范围**：优先复用已合格的业务步骤、串行会话、API 和流程图容器，只补目标实验缺失的职责与接线；不顺带迁移其它实验或新增通用框架。
- **先定向小测，再按影响合并验收**：先跑通新增或修改的测试，最终按本次源码、环境和实际调用方统一执行必要回归，不在验证器前后重复跑同一组。只修改分析/参数时不自动跑完整采集；修改前端才做对应类型检查和构建，局部页面跑相关测试，共享前端变化或新增/整体交付再跑全套。相关代码、配置或依赖再次变化后补跑受影响检查，不能用旧绿灯代替；保留失败批次的真实记录。
- **历史全量比数值，代表样本验出图**：对纳入兼容性验证的历史保留完整科学数值对照，包括矩阵、有效掩码、逐点科学字段、排名和最佳点；不默认给全部点重复生成新旧两套图片。选择覆盖实际模式与正常／无效诊断的代表运行或工作点验证完整出图；绘图算法、格式或兼容路径变化时扩大对应检查。明确记录数值与绘图各自覆盖范围，不能将仅数值通过写成全部历史已重新出图。所有写入性分析仍使用显式临时运行目录。
- **区分本次影响与历史全仓记录**：先列出本次任务文件和工作区其他改动，按真实共享调用方选测；范围不清时再生成计划，不重复生成相同计划。按现行规则记录本次实际执行及未覆盖项，只有明确要求全仓验收时才另做全仓检查。附录的旧全仓失败/超时不作为今天的自动执行清单，也不改写成通过；不得以任务限定为由漏掉真实共享依赖或扩展修复无关 ID。
- **桌面验收先确认测试工具链**：复用已安装的浏览器工具、现有 fixture 和 Mock 约定，先确认页面能正常加载、接口返回格式及隐藏节点定位，再检查双列、交互和长文本。不要为单页验收重复搭建工具链；自建浏览器和临时服务结束后关闭，不操作用户服务或真实硬件。
- **交付时附分项耗时**：在最终回复中统计调研、主干实现、测试编写与修错、历史对照、构建／全前端检查、桌面验收及收尾的墙钟耗时；区分命令执行等待与其余开发整理时间，并行任务不重复累计。优先依据实际日志和时间戳，无法精确拆分时标明估算，不把测试工具或自身实现失误造成的返工归因于硬件安全要求。不另建独立耗时报告、交接报告或工作区快照。

### 可复用的继续提示词

```text
阅读 D:/Code/exp_agent/docs/experiment_migration_status.md 的当前汇总、状态表、当前边界和继续执行规则，核对实际注册表与代码归档。
本次指定实验 ID：<填写稳定实验 ID>。
仅按需查阅本 ID 的历史附录，不把其中旧任务数量、命令范围、服务状态或“本轮”指令当成当前要求。

只处理这个未归档实验，由主 Agent 负责实现、自测、必要集成及交接。
未经我明确授权不派子代理；若本次已授权，可用 fresh 只读子代理独立验收，未完成/超时不算通过。
遵守 AGENTS.md 和 expcodegen 的现行分层验证，保留真实共享调用方；分析/参数关联须覆盖实际修改源码。
已有合格实现直接复用，按实际影响验证，不无条件重跑全仓，不降低安全和交付标准。
按本文效率规则减少重复验证，结束时给出有日志依据的分项耗时，估算须注明。

保留已有暂存/未提交改动、用户参数和历史数据，不连接真实仪器、不操作用户服务。
写入性分析使用明确的临时 run_dir，禁止默认 latest。
无异常时仅更新本文对应状态行和汇总，不维护机器状态副本，不另建逐项交付/验收/交接报告或重复快照。
有异常时记录当前问题、真实结果和未完成项，失败不计数；历史失败、正式回归和必要证据保留。
结束后给出本文链接并停止，等待我指定下一个实验；不迁移归档项，不重复计数。
```

## 附录：历史验收与异常记录

**以下是历史记录，不是当前执行指令。** “本轮/当前”、机器状态字段、数量、端口/PID、临时路径和通过/失败均按原任务时点理解；原机器副本已停用，证据现存性以本文前述核对为准。保留旧失败、超时、事故和恢复限制，不因本次整理升级、撤销或重算既有迁移结论，也不重跑真实历史。

### 整理前的交接叙述（历史原文）

本清单于 2026-10-24 按 `lab_workflows/experiments/registry.py` 核对：完整目录共 62 项：50 项在维护的正式 Python 实验（39 个 typed_workflow、11 个 legacy_script），另 12 项代码归档只保留卡片（原模式为 7 typed、5 legacy）。Notebook、未注册脚本和示例不纳入；运行时以注册表为准，不从脚本数推断。本文作为持续更新的唯一主交接报告；原 `.cache/expcodegen/` 机器状态副本已按用户要求删除并停止维护，状态以本文为准。

新模式只表示强类型参数与执行契约，不能据此认定流程图或业务验收完成。当前**已完成36项：8项补齐、27项迁移、1项达标跳过；另11项归档排除，待迁移14项（3 typed、11 legacy）**。当前迁移范围合计50项；另新增 `rf-control-scale-sensitivity`，不计入旧实验迁移完成数，完整目录总数为62（50项在维护的正式实验、12项仅保留卡片的代码归档）；默认 `list_experiments()` 只返回在维护的实验。本轮 `photon-shot-noise` 完成强类型模块化、8 采集／3 分析节点的 XY 风格双列交互流程图及运行／历史接线，保留 Probe 电压轴、逐次温控门控和原 ASD² 加权拟合，仅本 ID 增加一次计数。同日并行的 `projection-noise` 已由其自身任务独立完成并计入，按用户确认全程不修改 Pump 载波／门控，保留可选整段 OFF→ON、原 Welch／自由洛伦兹拟合和旧 HF2 兼容分支。此前 Demod3 R、XY 剩磁、主场示波器噪声谱、Probe 失谐及光功率优化的完成记录保留。全仓既有场景缺口仍保留，不宣称全仓通过。已停止，不启动其他实验。

目录归档标记保存在 `params/experiment_catalog.yaml`；已移出主目录的代码归档清单以 `lab_workflows/experiments/archived.py` 为准，这些 ID 始终只显示卡片，不能仅修改目录标记恢复。专属代码、正式测试与原图按原相对路径保存在 [`old/experiments/`](../old/experiments/README.md)，共享算法、参数和历史数据原位保留；旧 GUI 运行/历史 API 返回 410。当前12项归档中，11项未完成迁移，已从待迁移范围排除，表中标为“归档，不迁移”；另 `rf-sensitivity-aw-r` 在完成后归档，保持原完成记录，不重复排除或回退计数。归档本身不算已完成，也不算达标跳过。此前按用户要求将 `rf-sensitivity-aw-frequency` 归档、不再迁移：专属采集脚本移入归档，共用 plot 入口原位服务于 DirectAW 并在归档中保留原始副本。本轮仅追加 `temperature-switch-pid-cycle` 归档，采集／分析脚本按原字节移动，专用 TEC/PID 协议和采集例外保留；参数、历史数据及科学算法不变，不增加完成计数。每次接续先重读归档标记，用户未明确恢复时不迁移归档项。

原10/15/20/21项累计报告、单实验开发/验收交接及旧备份，已将有效完成结论、兼容边界及未解决问题合并到本文。原文逐字节保存在本地历史归档 `old/experiment_modularization_handoffs.zip`（不随 Git 同步），包内以原仓库相对路径存放，并附SHA清单；原机器状态副本（含 `checkpoint_history`）已停止维护，不再作为现存状态入口；旧交接路径可在归档内查到。早期“尚未接线”等阶段性待办以最终验收为准，旧分工、下一项和历史数量不再作为当前指令。

既有完成项 `mx-z-optimal-control-xy-rf-sensitivity` 已完成：沿用合格的 Z 控制会话和通用分步界面，保留原蛇形网格、中心唯一校相及逐点科学算法，提供7采集/3分析节点、60个可见参数；参数 schema v2 与外部键不变，新 raw 格式独立为v1。本项 **37项 Python/API、6项 GUI 全部通过**，包含于85项 Python/API集成与351项全前端（2worker）；99项仪器/映射/时钟/锁相回归、Python编译、TypeScript、build、API生成及Skill一致性检查通过。真实历史仅在明确临时副本中重分析，36点/1有效点的14个科学数组在1e-12容差内一致，有效掩码、排名与最佳点均相同。目标历史1822文件的SHA/大小/mtime、除目录简介外的全部参数及暂存区均与本轮基线一致；目录简介仅改本ID，未另建交付或验收报告。

### 历史异常与验收记录

- **2026-10-24 `temperature-switch-pid-cycle` 归档（不增加迁移完成数）**：按用户要求将采集／离线分析脚本原字节移至 `old/experiments/experiments/`，GUI 仅留卡片，旧运行／参数／历史 API 返回 410，保留原分类、20 字段 schema v1、Python 按需兼容和 TEC/PID 专用采集例外。79 份归档清单 SHA 全部通过；两份脚本、52 份非目录参数与 Git index 保持，目录配置仅新增此 ID 的归档标记。本检出无此实验历史数据文件，未进行真实历史重分析；未连接硬件或操作用户服务。
  - **26 项 Python/API（含目录 8 项、10 子场景）及 23 项相关前端测试通过**；TypeScript、临时目录生产构建、Python 编译和 Skill 一致性通过。初次 GUI 验证命令因未提供实验 ID 被解析器拒绝，未执行测试，原日志保留；代码归档项不属于在维护注册列表，随后使用完整 17 路径的限定基础验证，并单独完成真实路由与 API 回归，无重复执行已通过节点。
  - **限定验证如实失败**：`scope_source=explicit_files`，17 changed／48 selected／16 unmapped，另列 144 条任务外改动；本次注册表元数据经静态依赖扩散，产生 29 条其他实验 analysis 关联缺口与 12 条采集场景缺口，`checks=[]`、`business_status=missing_mapping`，不宣称工具或全仓业务验收通过，也不缩小真实变更范围规避失败。16 条未映射的归档、脚本移动、GUI、目录与说明路径由上述回归、源文件哈希和人工核对覆盖；未修改其他实验或验证器。报告为 `.cache/expcodegen/pid-cycle-archive-validation.json`；必要日志、XML 位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-pid-archive-y_swuvy7/`。前述 RF 归档与其它已暂存工作原样保留；新归档后端由用户安全结束现有会话后自行重启加载。本次仅归档此 ID 后停止。

- **2026-10-24 `rf-sensitivity-aw-frequency` 归档（不增加迁移完成数）**：按用户最新要求停止迁移，专属采集脚本按原字节移至 `old/experiments/experiments/`，共用 plot 入口原位保留并另存归档副本。默认注册列表移除此 ID，GUI 仅留静态卡片，旧运行／历史 API 返回 410，保留原分类、名称、62 字段旧 schema 与 Python 按需兼容。77 份归档清单的 SHA 全部一致，目标 921 份历史文件的路径、大小、mtime／SHA 及 52 份非目录参数保持；目录配置仅新增本 ID 归档标记并更新其简介。
  - 归档／目录／锁相输入及共享采集契约 **41 项 Python/API、73 子场景通过**；全前端 **60 文件／477 项通过**，TypeScript、临时目录生产构建、Python 编译与 Skill 一致性通过。规定的完整 Git 验证仍因其他 ID 既有采集场景缺口退出 1，未进入检查执行阶段，不宣称全仓业务验收通过、不扩展修复其他实验。必要日志和 `archive-regression.xml` 保存在 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-rf-am-frequency-dy9mrnk0/`。未连接硬件、操作用户服务、重分析真实历史或新增独立交付／交接报告；新归档后端需在安全结束现有会话后由用户自行重启加载。仅本 ID 归档后停止。

- **2026-10-24 `photon-shot-noise`**：已完成，同一 agent 调研、实现、自验收与集成，未派子 agent，仅迁移本 ID。8 采集／3 分析节点、21 个可见参数；参数、准备、采集、原子存储、科学计算、绘图及编排分离，复用连接路由、时钟、安全收尾、串行会话、历史 API 和流程容器。参数 schema v2，保留全部 18 个原可见外部键；独立 raw v1，兼容已验证的旧手工配置及无 raw 标记 schema v1/v2。保留 Probe 逐点、重复逐次门控及恢复等待；取消、恢复或写盘失败抢救当前点已读重复，原子替换失败保留旧文件并另存 rescue。所有结束路径恢复温控，只断开 TEC，保持已执行主场、Probe、Pump OFF 与锁相设置，草稿不污染收尾；断开失败同步标记本轮不安全。薄分析入口必须显式给 run_dir。
  - **最终 53 项 Python/API 通过**：26 采集、20 分析、7 API。最新采集/API 为 `workflow-api-final.xml` 33 项，分析由 `integration-delivered.xml` 中 20 项覆盖；后者整批 151 通过、66 子场景通过、1 条已知跨 ID 测试明确排除，不把最终新增硬件取整节点冒称已在旧整批执行。6 项目标 GUI 包含于当时工作区全前端 **60 文件／475 项（1 worker）全部通过**；99 项仪器／映射／时钟／锁相、共享历史／生命周期／目录／采集契约及锁相回归通过。22 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成、fixture 与模型/清单一致性及 Skill 同步检查通过。新增本 ID 12 条路径，保留并行 projection 的接线与生成类型，不将其计入本项。
  - **保留原失败证据**：早期测试 UTF-8 读取、假进程解释器临时根目录定位及缺少 `removed_temperature_keys` 均已修正；归档任务观察到的 photon schema 中间态不再是剩余阻塞。自检按真实驱动发现 `setup_dc()` 自动开启输出，改为 Pump OFF 后使用不改变开关的 DC 接口，并用两型号真实 SCPI 捕获证明没有 Pump ON 命令；量程/滤波时间常数保存请求与硬件取整实际值，后续漂移仍拒绝采集。旧脚本未下发的参考频率／输入量程已接线，取消不再被坏记录捕获。首次扩大回归另有既有 `lab_workflows/test_lockin_acquisition.py::test_stage_declarations_match_registered_defaults` 因 `rf-control-scale-sensitivity` 关闭 INCLUDE_NOISE 时仍预期两阶段而失败；该业务、参数与测试未在本轮修改，最终明确排除此已知节点，不宣称全仓回归通过。
  - **范围如实区分**：最终完整 Git 检查 failed：142 changed／52 selected／55 unmapped／13 条其他 ID 场景缺口，checks=[]；本次 37 路径计划同样 failed：52 selected／16 unmapped／相同 13 条缺口，checks=[]。本 ID 无缺口；16 条未映射 GUI、类型、说明及配置路径由对应回归和人工核对覆盖。限定目标 GUI 测试路径的单项 delivery passed，实际执行共享契约 12、关联业务 8、API 7、GUI 6，不冒充 37 路径或全仓通过。报告 `.cache/expcodegen/photon-{full-final,scope-plan,delivery-final}.json` 保留，不扩展修复其他 ID。
  - **五份完整真实历史只在临时副本对照**：三份 11 点、两份 31 点，各 13 个原科学数组及原报告全部 13 个数值字段在 rtol=1e-12、atol=1e-18 内一致；`0708_1735_shot_noise_cal` 代表运行完整生成新旧四组 PNG/PDF，其余完整比数值，不冒称全部重新出图。另四个无 scan_meta 的目录不计科学验收。原 134 份历史的路径／大小／mtime／SHA、52 份原非目录参数及 Git index 均保持；仅新增本 ID 默认 YAML，精准更新其已有目录简介。原 Welch→ASD 平坦段中位数→重复均值平方加权拟合、SD 权重、RMS 和四组图保留，不加入积分或洛伦兹分离。正文、定义、目录简介、两份 README 和旧入口说明已同步。
  - **桌面及交付边界**：2560×1281 实际视口，两列各 1080.5 px；394 字符运行名、1816 字符诊断和490 字符下载名无页面横向溢出，未模拟 API／页面错误均 0。独立 Ego 空间及 mock 服务已关闭，未操作 8000/5173 用户服务或连接真实仪器；新接口需用户安全结束旧会话后自行重启后端。真实硬件和 Python 3.10 解释器运行未验证，既有 chunk／Starlette 弃用提示保留。证据集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-photon-x4m14k90/`，主要索引 `workflow-api-final.xml`、`integration-delivered.xml`、`frontend-all.xml`、`science-results.json`、`desktop-metrics.json`、`preservation-final.json`；临时比较／浏览器脚本已删除，正式回归与原分析器基线保留。不新增独立交付／验收／交接报告或持续机器状态副本，仅本 ID 增加一次完成计数并停止。

- **2026-10-24 `projection-noise`**：已完成，同一 agent 实施、自验收与集成，不派子 agent，只计入本 ID 一次。9 采集／2 分析节点、16 个可见参数；准备、采集、分析／拟合和绘图分离，复用串行会话、历史 API 与 XY 流程容器。保留参数 schema v6、默认 YAML、稳定键和原 ADC 帧格式，新 raw 独立为 v1；兼容已验证的未标记 SDS v2–v6 与旧 HF2 双文件。用户明确要求 **Pump 载波／门控全程不修改**，已从连接、时钟、配置和全部正常／异常／取消／EOF 收尾路径排除，仍检查其端点占用保护；光功率 DC 参数继续生效。保留可选整段 OFF→ON 和量程继承；逐帧恢复后原子保存，后续量程失败及恢复失败抢救最新已读帧，索引替换失败保留原文件和待替换文件，不覆盖原异常。A08 成功恢复后再采须从 A04 重新准备，A09 无前置结束；薄分析入口必须显式指定目录。
  - **最终目标 48 项 Python/API 全部通过**（19 采集、22 分析、7 API），包含于 83 项集成及 66 个子场景；7 项目标 GUI 包含于全前端 **60 文件／476 项（1 worker）全部通过**。99 项仪器／映射／时钟／锁相、共享采集契约 12 项、18 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成与 Skill 一致性通过；新增本 ID 12 条流程／会话／历史 API 路径。正文、定义简介、已有目录简介及两份 README 已同步，fixture 和实际清单／schema 精确一致。
  - **全部 36 份真实 SDS 历史仅在明确临时副本中对照**：原 NPZ 共 696 个数组／标量字段、原报告全部字段及 CSV 一致，数值容差 rtol=1e-12、atol=1e-15，掩码／字符串一致；其中两份覆盖 OFF/ON 和 ON-only 的代表运行完整生成新旧 PNG/PDF，其余仅禁用两边绘图，不冒称全量重新出图。原 924 份历史文件的路径、大小／mtime／SHA 保持。**原拟合限制不顺带修改**：高权重合成数据可落入边界局部解但优化器仍报告收敛，迁移前后完全一致；正文已提示核对边界、R² 和残差，不将优化收敛等同科学质量通过。
  - **验证范围如实区分**：完整 Git 为 failed：142 changed／49 selected／55 unmapped／13 条其它 ID 既有采集场景缺口，checks=[]；本轮 29 路径实际范围计划亦 failed：49 selected／13 unmapped／相同 13 条缺口，checks=[]。本 ID 无缺口；13 条 GUI／生成类型／文档／目录接线未映射路径已人工核对并由对应回归覆盖。不扩展修复其它实验。以本 ID 采集测试文件限定的单项 delivery 为 passed，实际执行共享契约 12、关联采集业务 10、API 7 和 GUI 7；不将其冒充 29 路径或全仓通过。工具记录 `.cache/expcodegen/projection-{full-final,scope-plan,delivery-final}.json`；早期导入、假仪器构造、科学测试预期和桌面 mock 路径红灯保留，后续通过不改写失败批次。
  - **桌面与保护边界**：隔离 Ego／mock 服务实测 2560×1281，采集／分析两列各 1080.5 px，343 字符运行名、1877 字符诊断和 477 字符下载名无页面横向溢出，页面异常及未模拟 API 均为 0，可编辑原图实际下载并核对。仅本轮浏览器和随机端口 mock 服务已关闭；用户 8000／5173 服务未操作，新接口须用户安全结束当前会话后自行重启后端加载。原有 52 份非目录参数、目标默认值及 Git index 保持；67 份既有任务外改动保持，`requirements.py` 另有并行任务更改，未由本轮改写或回退；目录配置的其它并行更改同样保留。不计入并行光散粒噪声或两项归档的完成数，不连接真实仪器。真实硬件及 Python 3.10 解释器运行未验证，既有 chunk／弃用提示保留；Ego 提示有新版本，未自动升级。
  - 正式回归、XML、科学临时副本及必要证据集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-projection-tbrqx8eq/`，主要索引为 `target-integration-final.xml`、`frontend-all.xml`、`science-results.json`、`desktop-metrics.json`、`preservation-audit.json` 和 `delivery-final.log`。本轮临时验证脚本清理，原分析器和正式回归保留；不另建交付／验收／交接 Markdown 或机器状态副本。仅本 ID 标记已完成后停止。

- **追加两项代码归档（不增加迁移完成计数）**：`aw-const-omega-awscale-check`、`dg-am-comp-transfer-check` 的4个采集／分析脚本移至 `old/experiments/experiments/`，75份归档源文件 SHA256 全部保持；GUI 只留卡片，旧运行／历史 API 返回410，旧 Python schema 路径及版本兼容。DG AM 的历史采集例外与在用波形检查的数据依赖保留，154份目标历史文件的路径、大小和mtime未改；设备、限值及既有默认参数未改，目录文件中的其它并行修改未覆盖。归档API／目录23项、前端定向21项、TypeScript、临时目录生产构建与Python编译通过；共享采集批次为35通过／1失败，失败是执行时 `photon-shot-noise` 的schema缺少唯一 `SYSTEM_SETTLE_S`，不在本次修复范围。完整Git验证也保留该参数错误及其它ID的既有场景缺口，不宣称全仓通过。未连接仪器或操作用户服务；必要日志位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-archive-two-lzv_gom9/`。

- **2026-10-24 `noise-spectrum-xy-demod3-r`**：已完成，同一 agent 开发、自验收与集成，无子 agent，仅处理本 ID。12 采集／3 分析节点、60 可见参数；准备、激励/校相、批次采集、分析接线、绘图和编排分离，复用公共连接路由、安全收尾、串行会话、历史 API 与流程容器。保留参数 schema v2、外部键和用户默认值，新 raw 独立为 v1，兼容原 schema v1/v2 四键矩阵。控制频率外层、Demod3 同向内层和末批恢复后保存不变，R 为 V、不是 PSD；保留原脊线标定/逐列拟合及非有限值掩码。修复旧安全校验缺导入、未创建目录即引用 `run_dir`、取消被坏点捕获等阻断问题；原子替换失败保留旧矩阵并另存抢救矩阵。A11 本轮收尾后必须重新准备/校相，A12 无前置结束；草稿不改变已执行 Pump/时序收尾，回读模式不符拒绝采集，恢复失败不报告安全完成。薄分析入口必须显式给目录。
  - **最终 52 项 Python/API 全部通过**：24 采集、21 分析、7 API，含真实完整入口及 stdin 假仪器子进程；证据 `target-final-pass.xml`。6 项目标 GUI 包含于全前端 **58 文件／461 项（1 worker）全部通过**；99 项仪器/映射/时钟/锁相、36 项共享历史/生命周期/目录/锁相/XY 分析及 24 子场景、共享采集契约 12 项通过。23 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、API 精确生成和 Skill 一致性通过；仅新增本 ID 12 条 API 路径，原路径和 schema 内容一致。正文、定义简介和两份 README 已同步，目录没有本 ID 简介覆盖值，无需新增；参数文件未改。
  - **范围如实区分**：完整 Git 检查 failed：76 changed／49 selected／27 unmapped／15 条其他 ID 既有场景缺口，checks=[]；30 个本次实现/说明路径（另更新主交接）的计划亦 failed：49 selected／12 unmapped／相同 15 条缺口，checks=[]。本 ID 无缺口，12 条未映射的后端/前端接线、类型、fixture 与文档由对应回归和人工核对覆盖，不扩展迁移其他实验。最终单项 delivery 为 passed，明确以目标 GUI 测试文件限定范围，实际执行共享契约 12、关联业务 10、API 7、GUI 6；不冒充完整 30 路径或全仓通过。工具记录 `.cache/expcodegen/demod3-{full,scope-plan,delivery-final}.json`；早期测试、CLI 导入顺序和桌面定位红灯保留，最终完整目标及交付补验不改写原失败日志。
  - **科学与桌面边界**：本检出没有目标真实历史运行，不冒称真实历史验收。正常、NaN 列、Inf 列三组合成原格式仅在明确临时目录完成迁移前后完整分析/PNG/PDF 对照，每组 24 个原科学数组在 rtol=1e-12、atol=1e-15 内一致、原报告全部字段一致；全无效组两边均按相同原因拒绝标定。桌面实际 2560×1281，两列各 1080.5 px，357 字符运行名、1707 字符诊断、468 字符下载名无页面横向溢出；未模拟 API 和浏览器记录错误均 0。只使用隔离 mock 服务和 Ego 任务空间，均已关闭；8000/5173 用户服务未操作，新增后端接口须用户安全结束会话后自行重启。当前用户映射仍为 MFLI，本实验双主电压回环的 HF2 限制照常预检拒绝，未自动切换配置或连接真实硬件。真实硬件和 Python 3.10 解释器运行未验证，既有 chunk/弃用提示保留。
  - **保护与收尾**：任务外 45 份既有改动的大小/mtime/SHA、53 份参数文件及 Git index 均保持；目标真实历史仍为 0 文件。fixture 经 JSON 归一化与实际 schema/清单精确一致，初次元组/列表比较误报经补验消除。正式回归、XML、原分析器、科学运行及桌面证据位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-demod3-7iyg228b/`，主要索引 `target-final-pass.xml`、`frontend-all.xml`、`science-final-results.json`、`desktop-metrics.json`、`preservation-audit.json` 和 `delivery-final.log`；本次两个临时验证脚本已删除，没有残留目标取消标记。不创建独立交付/验收/交接报告或机器状态副本；仅本 ID 增加一次完成计数，已停止。

- **2026-10-23 `mx-xy-residual-field-calibration`**：已完成，同一 agent 实施、自验收与集成，未派子 agent。10 采集／3 分析节点、20 可见参数，准备／采集／拟合／绘图与编排分离，复用既有连接、收尾、串行会话和 GUI 容器；保留参数 schema v3、外部键和原科学算法，新 raw 独立为 v1。修正旧定义误要求锁相的映射声明，实际仅用 SDS；每轮重新注册 GS200 原态恢复，草稿不污染已执行 Pump 收尾，重跑不覆盖旧 raw，后续量程、恢复和保存失败抢救已读帧。薄分析入口必须显式给目录。正文、定义、已有目录简介、两份 README 及 API 类型已同步，默认 YAML 未改。
  - **目标 41 项 Python/API 全部通过**：16 采集、18 分析、7 API，`target-pass.xml` 零失败／跳过；另有 65 项受影响共享／主场示波器回归及 57 子场景通过。原 `integration-final.xml` 整批为 104 通过／2 失败（新增非有限参数测试在预期异常区外构造参数），修正测试后由完整目标 41 项及最后 2 项补验覆盖，不把原失败批次改记通过。99 项仪器／映射／时钟／锁相、12 项共享采集契约、21 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建和 Skill 一致性检查通过；OpenAPI 仅新增本 ID 12 条路径，已有路径和 schema 内容一致。
  - **六份真实 v2 历史仅在明确临时副本中对照**：两份 21×21、四份 51×51，各 35 个原科学数组及原报告全部字段在 rtol=1e-12、atol=1e-15 内一致，连续中心、最近网格点、交叉验证和警告均保留。`0723_145413...` 代表历史完整生成新旧 PNG/PDF，其余只禁用两边渲染而完整比数值，不冒称全部重出图；另有合成数据真实 CLI 出图和 v1/v3 格式回归。
  - **验证范围不混淆**：完整 Git 为 failed：53 changed／49 selected／21 unmapped／16 条其他 ID 既有场景缺口，checks=[]；本次 32 路径实际范围计划亦 failed：49 selected／15 unmapped／相同 16 条缺口，checks=[]，本 ID 无缺口。15 条 GUI／生成类型／文档／目录简介未映射路径均人工核对并由对应回归覆盖；共享 CSS 另有实际浏览器检查。仅以目标测试文件限定的单项 delivery 为 passed，实际执行共享契约 12、关联业务 10、API 7、GUI 6；不将其写成 32 路径或全仓通过，不扩展迁移其他 ID。工具记录为 `.cache/expcodegen/mx-xy-residual-{full-final,scope-plan,delivery}.json`。
  - **桌面及保护核对**：最终全前端 57 文件／455 项（1 worker）全部通过。桌面 2560×1281，两列各 1080.5 px，335 字符运行名、长诊断及 445 字符下载名无横向溢出；发现并以一条公共换行样式修复分析对象说明的长目录溢出，720 px 代表窄屏冒烟亦通过；页面异常和未模拟 API 均 0。原历史 11376 文件的路径／大小／mtime／SHA、52 份非目录参数、目录中除本 ID 简介外的原字节以及 Git index 保持；保护脚本的 Windows 路径／CRLF 和 JSON 元组比较错误经补验消除，没有发现历史差异。8000／5173 用户服务未操作，隔离浏览器及 6189 mock 服务已关闭；新增后端接口需用户安全结束现有会话后自行重启加载。真实硬件和 Python 3.10 解释器运行未验证，既有绘图布局、构建 chunk 与 Starlette 弃用提示保留。
  - 正式回归与必要 XML／日志、科学临时运行和桌面证据保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxxy-9lrygbpp/`，主要为 `target-pass.xml`、`frontend-final.xml`、`science-results.json`、`preservation-audit.json`、`desktop-metrics.json`；本次临时验证脚本清理，未创建独立交付／验收／交接报告或机器状态副本。仅本 ID 增加一次完成计数，停止，不启动下一项。

- **2026-10-23 `mx-main-field-scope-noise-spectrum`**：本 ID 已完成，同一 agent 开发、自验收与集成，不派子 agent。10 采集/2 分析节点、32 个可见参数；准备、扫描/故障帧暂存、科学接线、绘图、会话与编排分离，保留参数 schema v8、稳定外部键与旧入口，新增独立 raw v1。主场外推、固定 XY DC、AUTO/FTRIG、逐次门控和自适应量程语义不变；GS200 原态、SDS DC 耦合、Z/XY 归零关闭、温控及已执行 Pump 参数收尾有回归。后续量程失败、取消或恢复失败抢救已读帧，恢复/断开失败不报告成功；草稿不污染收尾，重跑不覆盖旧 raw。历史详情/填回复用模型的 XY、稳定等待及二维低频迁移，读取不改原快照；薄分析入口必须显式给目录。另将控制点数不足的除零预检潜伏问题改为明确拒绝。
  - **目标 38 项 Python/API 唯一节点全部通过**：最新 `workflow-delivered.xml` 16 项采集，与 `target-final.xml` 中 15 项分析/7 项 API 合并，0 失败/跳过；后者整批 52 项（此前 15 采集、15 分析、7 API、15 共享历史/生命周期）通过，新增结束断开错误回归另在最新工作流 XML 通过，不将其冒称为已整批执行 53 项。6 项目标 GUI 包含于 56 文件/449 项全前端（1 worker）全部通过；99 项仪器/映射/时钟/锁相及 218 子场景、共享采集契约 12 项、17 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确对照及 Skill 一致性通过。API 只新增本 ID 12 条路径，原路径与 schema 内容保持。文档、定义和已有目录简介同步，用户默认 YAML 未改。
  - **验证器范围如实区分**：完整 Git 检查及 28 路径实际范围计划均为 failed：49 selected、13 unmapped、17 条其他 ID 既有场景缺口，checks=[]；本 ID 无场景缺口。13 条未映射的 GUI/后端接线、生成类型、fixture、文档、README 与目录简介均人工核对；实际受影响会话、历史与仪器回归如上。限定目标 GUI 测试路径的单项 delivery 为 passed，实际执行共享契约 12、关联业务 8、API 7、GUI 6；不把该单项结果写成 28 路径/49 ID 或全仓通过，不迁移其它 ID。工具结果保留于 `.cache/expcodegen/mx-main-scope-{plan,full-final,delivery-final}.json`，不恢复机器状态副本。
  - 两份真实历史仅在显式临时副本中完整比科学数值：`0721_151253...`（schema 3，100 点）与 `0728_075438...`（schema 7，201 点），全部原科学 JSON 字段和各 48 个 NPZ 数组在 rtol=1e-12、atol=1e-15 内一致，含有效掩码与二维诊断。两边仅禁用文件渲染，不宣称这些真实历史全部重新出图；完整真实 PNG/PDF 由合成波形全局分析和真实子进程回归单独覆盖。原 2458 份历史及 52 份非目录参数的大小/mtime/SHA 和路径集保持，目录配置除本 ID 简介外一致，Git index 未动。首次保护检查因 Windows 路径分隔符未排除预期简介变更而红灯，历史文件已逐项验证；保留失败日志后补齐剩余 49 份参数及目录语义比较，未报历史差异。
  - 桌面实际视口 **2560×1281**，采集/分析各 1080.5 px，运行/历史及 311 字符运行名、长诊断、432 字符下载名无页面横向溢出，未模拟 API 和页面错误均 0；使用 Ego 独立任务空间及临时静态/mock 服务，已关闭。8000/5173 用户服务只读确认仍监听，未重启/停止/取消或连接真实仪器；新增接口需用户安全结束会话后自行重启后端。真实硬件及 Python 3.10 解释器运行未验证，既有构建 chunk、Starlette 弃用和原二维算法 All-NaN 提示保留。必要 XML、日志、科学临时运行及桌面截图集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mx-scope-nqq30skv/`（`target-final.xml`、`workflow-delivered.xml`、`frontend-all.xml`、`science-results.json`、`desktop-metrics.json`、`preservation-{first,supplement}.log`）；本次两个临时测试脚本已删除，正式回归保留。仅本 ID 计入完成后停止。

- **代码归档整理（不增加迁移完成计数）**：8 项专属代码、正式测试与原图移至 `old/experiments/`，源文件逐份 SHA256 与移动前一致；共享相位算法原位保留。归档相关 API/目录/历史管理 60 项、全前端 443 项、仪器/映射/时钟/锁相 99 项、78 文件编译、TypeScript、临时目录生产构建、API 类型生成及 Skill 一致性检查通过。全 Git 验证仍因其它 ID 的 18 条既有业务场景缺口失败，未执行该计划的检查；扩大回归中的 `lab_workflows/test_lockin_acquisition.py::test_stage_declarations_match_registered_defaults` 仍因 `rf-control-scale-sensitivity` 关闭 `INCLUDE_NOISE` 时旧断言仍预期两个阶段而失败，该测试、参数及业务实现的字节均与任务基线一致，不在本次扩展修复。原 RF R 修改接口预期已从 404/405 更新为 410 并复验通过。测试产生的一个空 `.gui_jobs/*.cancel` 标记在确认假进程结束后移到临时证据目录，未删除原有文件；原 `data/`、`results/` 共 180904 文件的路径、大小与 mtime 保持基线一致。未连接硬件、未操作用户服务；必要日志位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-archive-ka6jtiz1/`。

- **2026-10-23 `mx-y-rf-probe-detuning-optimization` 验证边界**：本 ID 已完成，9 个采集／2 个分析节点，40 个可见参数；准备、网格采集、分析接线与编排分离，复用原网格拟合、PSD、双斜率排名与绘图，不重写科学算法。参数 schema v2、稳定键及用户默认 YAML 保留，新增独立 raw v1，兼容已验证无标记 schema v1/v2 历史。不连接 TEC，取消／异常／EOF／关闭均恢复 PZT/扫描与已执行基准，恢复失败不标记安全成功；薄分析入口必须显式传入目录。
  - **目标46项 Python/API（16采集、24分析、6 API）全部通过**，包含于81项集成及10个子场景；6项目标 GUI 包含于55文件／445项全前端（1 worker）全部通过。另99项仪器／映射／时钟／锁相及12项共享采集契约、19文件编译与3.10语法、TypeScript、临时目录生产构建、OpenAPI精确对照、fixture和Skill一致性通过。桌面1920×1080两列各768 px，309字符运行名、1620字符诊断和426字符下载名无横向溢出，页面／console错误及未模拟API均为0；仅使用隔离浏览器、静态临时服务与mock API，均已关闭，临时浏览器脚本已删除。输出目录联接到raw的归档风险有正式回归，拒绝移动采集文件。
  - **工具范围不混淆**：完整Git验证及27路径实际范围计划仍因25条其它ID场景缺口失败（7条归档、18条仍在迁移范围），checks未执行；本ID无缺口。实际范围为57 selected／13 unmapped，未映射的GUI接线／生成类型、文档和目录简介均人工核对并由对应回归覆盖。限定目标工作流路径的交付命令exit 0，共享契约12、关联采集业务6、API6及GUI6实际通过；不将此限定通过冒充完整27路径或全仓通过，也不扩展修复其它ID。
  - 三份真实历史仅在明确临时副本完成新旧完整分析／出图：27／21／21点，有效点25／20／16；全部原科学JSON字段、每份13个NPZ数组、双排名及最佳点一致（数值rtol=1e-12、atol=1e-15，字符串／掩码一致）。原2040份历史文件的路径集／大小／mtime／SHA及目标默认参数SHA保持。其它agent的光功率实验文件未操作，共用接线保留其改动；用户8000/PID25188、5173/PID32340服务未重启、停止或取消，新接口须用户安全结束会话后自行重启后端加载。真实硬件及Python3.10解释器运行未验证，既有chunk／弃用提示保留。
  - 必要XML、日志、临时科学运行与桌面证据保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-probe-detuning-o4ab4bio/`：`target-integration-final-pass.xml`、`frontend-all.xml`、`science.log`、`desktop-final.log`、`instrument-regression.log`、`delivery.log`、`full-validation.log`。工具JSON为 `.cache/expcodegen/probe-detuning-{actual-scope,delivery}.json`，前者failed、后者仅限定范围passed；初始提取／测试／浏览器mock与定位红灯证据保留，最终补验不改写旧失败结果。不创建独立交付／验收／交接Markdown或机器状态副本。仅本ID计入已完成后停止。


- **2026-10-23 `mx-y-rf-power-optimization` 验证边界**：本 ID 已完成，10采集/3分析节点、40可见字段；采集12、分析7、API8共27项 Python/API，以及6项目标 GUI 均通过，包含于55文件/445项全前端（2worker）。共享契约12、集成27、仪器/映射/时钟/锁相99项、20文件编译及3.10语法、TypeScript、临时目录生产构建、OpenAPI精确对照和Skill一致性通过。桌面1920×1080两列、长诊断和文件名不撑破布局；仅使用只读隔离服务，临时服务和浏览器已关闭。
  - 完整 Git 验证仍为 **failed：177 changed /62 selected /70 unmapped /25条其它ID场景缺口 /checks=[]**；9个目标生产源码路径的保守交付也为 **failed：57 selected /0 unmapped /同25条缺口 /checks=[]**，见 `.cache/expcodegen/power-full-validation.json`、`power-target-delivery.json`。其中7条对应归档项、18条仍在迁移范围，本ID无场景缺口，不扩展修复其它实验。仅以目标采集文件隔离的交付检查 **passed**，共享契约、关联业务6条、API8条和GUI6条均实际执行，见 `power-isolated-delivery.json`；隔离通过不改写上述全范围失败。
  - 三份真实历史均仅在明确临时目录中对照，各49点，有效点41/39/44，全部10个科学数组、逐点科学字段、双排名和最佳点一致（rtol=1e-12、atol=1e-15）。原整批600秒工具超时，只完成第一份完整分析/出图，原批次保留为未完成；其余两份补验仅禁用两边绘图，调用真实旧/新科学函数完成数值对照，不冒充三份均重新出图。坏波形保留原逐点拒绝/掩码规则，不阻断其它点；取消不被旧逐点诊断捕获吞掉，缺少显式目录的薄入口拒绝 latest。
  - 原4301份目标历史的路径集/大小/mtime/SHA、52份非目录参数文件及Git index均保持。工作区另有独立Probe失谐迁移任务，本轮未派子agent、未操作其文件或进程；共用接线文件保留两者改动，不将其计为本轮完成项。用户8000/PID25188、5173/PID32340服务未操作或重启，新后端接口尚需用户自行重启加载。必要XML、日志、科学临时运行及唯一保护清单保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxy-power-checks-gdbxzph1/`，主要为 `target.xml`、`analysis-latest.xml`、`frontend-latest.xml`、`integration.xml`、`science.json`、`preservation-audit.log`、`desktop-1920.log`；隔离交付业务XML仍在报告指定的 `.cache/expcodegen/` 路径。不创建独立交付/验收/交接Markdown或机器状态副本，正式回归保留；真实硬件和Python3.10解释器运行未验证。仅本ID计入已完成后停止。

- 上一完成项的完整Git验证为 **failed：439 changed /61 selected /149 unmapped /34条其他ID场景缺口 /checks=[]**，没有执行业务checks，见 `.cache/expcodegen/mx-z-xy-rf-full-validation.json`。其中7条对应归档项、27条仍在迁移范围；本ID无场景缺口。先前420/61/143/35的失败记录继续保留；归档排除不修复原验证，不宣布全仓通过，不扩展到其他实验。
- 本项26路径交付计划为56 selected/13 unmapped、结构与场景检查无错误；13个未映射路径均为GUI接线/生成类型、文档和目录简介，已人工核对并由API/GUI集成覆盖。原 `.cache/expcodegen/mx-z-xy-rf-delivery.json` 仍为 **failed/incomplete**：共享采集契约12项通过，原125节点整批超过900秒，后续API/GUI未在该命令内执行。保留原失败结果后，原125节点完整分为42/42/41节点补验，分别57/61/76个实际条目通过，合计194条、0跳过/失败/遗漏；XML为同目录 `mx-z-xy-rf-business-part1.xml`、`mx-z-xy-rf-business-part2.xml`、`mx-z-xy-rf-business-part3.xml`。验证器的XML核对函数确认全部原节点实际执行；API文件全部8条已在85项集成XML中逐节点核对，原GUI命令补跑6条通过（`mx-z-xy-rf-gui.xml`）。原范围验收证据已补齐，原超时批次不改记通过；该实验没有剩余实现或无硬件验收待办。
- 自动审批检查拒绝清理本轮历史分析临时副本，工具只返回“blocked by policy”；未绕过限制。临时运行位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxzxy-history-93ucet5t/0901_080215_mx_z_optimal_control_xy_rf_sensitivity`，已保留作科学对照证据，不作为新的交接报告或工作区快照；正式回归和原失败证据同样保留。
- 全前端默认并发曾为311通过/34失败；当时同一345项以2worker全部通过，本轮完整351项以2worker全部通过。首次失败日志保留，默认并发稳定性未修复。既有构建chunk与测试弃用提示保留，实际环境为Python3.12.4、Node24.16.0/npm11.13.0；本实验包另通过Python3.10语法检查，但未另装Python3.10运行验证。
- 最优控制频响独立验收曾误运行默认latest分析入口，写入一份真实历史的config、分析状态并移动5个旧结果。config原字节SHA及5个结果原字节/mtime已按证据恢复，raw未写；但缺目录事前mtime与analysis_state原本不存在的完整直接盘点，不能声称从未改写历史或全部时间戳恢复。事故与恢复证据保留在 `.cache/expcodegen/checkpoint20-history-incident-restoration.json` 及对应incident JSON；完整经过在历史原文归档中。所有写入性分析测试须显式使用临时run_dir，禁止默认latest入口。
- 电流闭环 FFT 频带端点的浮点容差已在本轮 `z-aw-closed-loop-waveform-correction` 核对并修复：只吸附同一物理端点的舍入误差，不外推或跨过不可靠点；截止频率与实际采样奈奎斯特边界另有回归。精确同频至少两次有效重复及 `exact_frequency_v1` 资格保持，旧结果缺资格仍须显式从 raw 重分析。
- 已有未提交改动与用户参数必须保留；用户服务不自动重启、停止或取消。本轮2026-10-16复核8000/PID5008、5173/PID43712仍监听且未操作；后端进程未重启，尚未加载本次新增接口。另只读观测到 `data/.gui_jobs/70c6ed34d0ad41c0b825890b8b12539d/session.json` 于00:18创建，已关闭、A02记录VISA I/O错误；未操作该会话，来源未归因。上一项目标及来源15460文件的SHA/大小/mtime保持等历史证据继续保留，旧PID/health不冒充当前检查。
- 全程未连接真实仪器；真实接线、触发、时钟物理锁定、断联物理输出、温漂及绝对增益均未验证。主交接、机器状态和历史归档受本地忽略规则影响，换工作区时一并保留，不自动提交或force-add。

#### 本轮 `mx-z-optimal-control-xy-noise-spectrum`：已完成，自验收与集成复核通过

- 开发由同一 agent 完成，无子 agent。按职责拆分准备、噪声采集、分析与绘图，复用已验收的来源冻结、硬件会话、取消/EOF、公共历史与 XY 分步界面；7个采集/3个分析节点、42个可见参数，参数 schema v2 保留，新增独立 raw1。保留蛇形 XY DC、Y RF AC 关闭、逐条温控恢复后保存及原 Welch/频段中位数；具体历史目录可独立覆盖统计上下限。说明正文、定义、已有目录简介及两份 README 已同步，用户默认 YAML 未由本轮改写。
- 目标最终44项 Python/API 均通过：采集14、分析21、API9。证据为 `.cache/expcodegen/mx-z-xy-noise-workflow-final.xml`、`mx-z-xy-noise-analysis-api-final.xml` 和 `mx-z-xy-noise-main-api.xml`；三份无重复节点，0失败/跳过。6项目标 GUI 包含于43文件/357项全前端（2worker）全部通过，XML为 `mx-z-xy-noise-all-frontend.xml`。集成62项及79个子场景通过（`mx-z-xy-noise-integration.xml`），99项仪器/映射/时钟/锁相回归、18文件 Python 编译与3.10语法检查、TypeScript、build、OpenAPI生成及 Skill 一致性检查通过。注册、流程结构、可编辑原图和实验文档直接调用验证器检查均无错误；实际schema的已保存布局完整覆盖42字段。
- 保留的独立历史 `0901_150338_mx_z_optimal_control_xy_noise_spectrum` 已在显式临时副本中完成科学对照：121点、全部9个数值数组在 rtol=1e-12、atol=1e-18 内一致，排名及最佳点相同。证据 `.cache/expcodegen/mx-z-xy-noise-science.json` 仅保留此运行；临时副本位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-noise-science-hobey7is/0901_150338_mx_z_optimal_control_xy_noise_spectrum`。
- **历史弃用处置**：用户明确弃用误写的 `0901_162328_mx_z_optimal_control_xy_noise_spectrum`，并要求清理其关联材料。该真实运行、对应临时科学副本、专属事故JSON及事故目录均已移入 Windows 回收站，原路径不存在；混合科学记录已移除该运行及失效聚合基线。永久删除曾被自动审批拒绝，工具仅返回“blocked by policy”，随后采用可恢复的回收站方式完成清理。独立的 `0901_150338...` 及共同上游保留。此前config原字节/时间戳与目录恢复无法完整核验的限制随用户弃用该运行解除，不声称已证明原样恢复；本项计入已完成，正式回归及通用验证日志保留。
- 根因已在目标薄入口修复：现在必须显式传入 `run_dir`，缺参数返回错误，不再隐式选择 latest；真实子进程回归核对临时目录产生结果，并验证无目录时拒绝。另修复原分析器重载外部控制来源的问题，离线只校验噪声参数与 raw，不依赖原理论/闭环来源目录；有单独回归和真实API子进程覆盖。**实现和无硬件检查已验证，本实验无剩余实现或无硬件验收阻塞。**
- 全 Git 验证实际失败：460 changed /61 selected /155 unmapped /33条其他ID场景缺口，checks=[]；其中7条对应归档项，26条仍在迁移范围，本ID无场景缺口。29路径实际范围的交付计划为56 selected/14 unmapped/31条其他ID缺口，checks=[]；因此未执行该计划中的保守跨ID业务批次，不把手工目标及集成通过冒充整份delivery通过。两份工具记录为 `.cache/expcodegen/mx-z-xy-noise-full-validation.json`、`mx-z-xy-noise-plan.json`；未映射的GUI/生成类型、文档、目录简介等14路径已人工核对。原有全仓缺口不在本轮扩展修复。
- 收尾复核：任务范围外330份原有改动的内容SHA均与事前一致，暂存区SHA保持 `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5`。归档清单仍为7项、ZIP内59份文件SHA清单全部一致。8000/PID5008、5173/PID43712仍监听，本轮没有重启、停止、取消或调用用户服务的操作接口；新增接口只在本地TestClient/假仪器进程验证。真实硬件与Python3.10解释器运行未验证，构建chunk/测试弃用提示保留。仅维护本主交接、机器状态及既有科学对照记录，不另建交付/验收/交接Markdown或工作区快照；正式回归与通用验证证据保留，用户明确弃用的运行关联材料按上述处置清理。

#### 本轮 `mx-z-optimal-control-xy-leakage-response`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用既有 Z 控制会话、来源冻结、历史接口和 XY 流程容器，按准备／激励／采集／分析／绘图拆分；7采集、3分析节点，35个可见参数，保留 schema v2 与外部键，新增独立 raw1。保留同形带符号 XY 任意波、蛇形顺序、R 质量重试及实测最小值／最早采集平局规则，施加最佳点后不复测。按用户确认，正常、取消、异常、EOF 和关闭均保持 `Time_sequence_2` 当前状态；配置中断为 OFF 时也不强制重启。说明正文、定义、已有目录简介、根目录及 GUI README 已同步；目标默认 YAML 未改写。
- 目标 **41项 Python/API 全部通过**（采集14、分析19、API8，`mx-z-xy-leakage-target.xml`）；6项真实路由 GUI 包含于44文件／363项全前端（2worker）全部通过（`mx-z-xy-leakage-all-frontend.xml`）。99项仪器／映射／时钟／锁相回归、21文件 Python 编译与3.10语法检查、TypeScript、生产 build、OpenAPI生成和 Skill 一致性检查通过。直接调用验证器检查注册、说明、流程元数据及可编辑原图均无错误。真实硬件及 Python3.10 解释器运行仍未验证，既有构建 chunk／测试弃用提示保留。
- **原范围补验已齐，不改写失败批次**：28路径交付计划为56 selected／13 unmapped，结构检查无错误，本ID无场景缺口；33个其它已选ID仍缺场景声明，不据此认定其迁移完成。原交付调用的外层900秒超时使验证器未保存最终报告，业务子进程随后遇关闭输出流；原XML为 `business-cc0bbeb873254eea92fb222ed7f347f7.xml`，保留为未完成。原135个业务节点完整拆为28／28／28／28／23节点，最终34／36／39／77／23条、合计209条通过，0失败／跳过／遗漏；证据为 `mx-z-xy-leakage-business-part1.xml`、`part2.xml`、`part3.xml`、`part4-retry.xml`、`part5.xml`（均在 `.cache/expcodegen/`）。第4批首次420秒超时日志另保留，同范围在900秒上限内625秒通过。原131节点集成批次也曾900秒超时，已按原范围核对：24节点由通过的业务XML覆盖，剩余107节点分五批全部通过（`mx-z-xy-leakage-integration-part1.xml` 至 `part5.xml`）。目标API、GUI原命令所需全文件证据已齐。首次全前端工具超时仅形成38文件／329项部分XML，保留为 `mx-z-xy-leakage-all-frontend-incomplete.xml`，不冒充完整通过；重跑44文件／363项通过。逐节点核对与批次耗时保存于机器状态 `current_attempt`，最终各批均未超过900秒。
- **全仓仍未通过**：本轮完整Git检查为481 changed／61 selected／161 unmapped／32条其它ID阻断错误，`checks=[]`，见 `.cache/expcodegen/mx-z-xy-leakage-full-validation.json`；其中7条对应归档项、25条仍在迁移范围。本ID无缺口，未扩展修复其它实验。28路径原范围及计划见 `mx-z-xy-leakage-plan.json`，13个未映射的GUI接线／生成类型、文档、目录简介路径已人工核对并由对应测试覆盖。原全仓失败、原超时和初始红灯证据均保留；分批补验不把原工具调用改记通过。
- 三份真实历史均只在明确临时副本 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-leakage-science-kaue_8se/` 内重分析，各21×21点，全部原结果字段与旧分析器逐项完全相同，最小点与实际率一致；每份447个raw文件未改。旧基线与对照结果集中于既有机器状态 `current_attempt.science_baseline/science_comparison`，未另建科学交付报告。三个原目录共1353文件的路径集、大小／mtime／SHA均保持；50份非目录简介参数文件、任务外452份既有改动及暂存区SHA `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5` 保持。归档仍7项，ZIP内59份文件清单校验通过；8000/PID5008、5173/PID43712仍监听，未操作用户服务或真实仪器。临时科学副本、正式回归及必要工具日志保留，不另建交付／验收／交接Markdown或工作区快照。**本实验无剩余实现或无硬件验收阻塞，计入已完成后停止。**

#### 本轮 `mx-z-optimal-control-xy-rf-phase-response`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用已验收的 Z 控制会话、来源冻结、安全收尾和 XY 流程容器，拆分准备、激励、相位拟合、采集、分析及绘图；6采集／3分析节点、45个可见参数，保留 schema v2 与外部键，新 raw 格式独立为1。保留蛇形 XY、180° 配对相位、R 质量重试、单轮异常重采、正式非线性色散及旧正弦诊断，不选择最佳 XY；修复原6点相位配置无法满足异常检测的问题，连接前明确要求至少8个偶数相位点。历史覆盖只允许两个拟合门槛，逐目录隔离；读取历史不写配置或状态，薄分析入口必须显式传入目录。正文、定义、已有目录简介及两份 README 已同步，用户默认 YAML 未改写。
- **43项 Python/API 全部通过**（采集13、分析21、API9）：证据为 `.cache/expcodegen/mx-z-xy-phase-workflow-recheck.xml`、`mx-z-xy-phase-analysis-api-final.xml`、`mx-z-xy-phase-api-history-lifespan-final.xml`，按模块选取最新节点去重核对，0失败／跳过；后者另含15项共享历史／生命周期回归。7项目标 GUI 的逐节点XML为 `mx-z-xy-phase-target-gui.xml`，无失败／跳过；45文件／370项全前端（2worker）通过，日志为 `mx-z-xy-phase-all-gui-two-workers.log`。共享采集契约12项、仪器／映射／时钟／锁相99项、22文件编译与3.10语法、TypeScript、build、OpenAPI精确生成对照及 Skill 一致性检查通过；流程清单、原图和文档直接验证无错误。桌面1920×1080两列及长诊断／下载名无页面横向溢出，未模拟API和页面／console错误均为0（`mx-z-xy-phase-desktop-final.log`）。本轮自建浏览器和 preview 已关闭，临时浏览器脚本已删除。
- **保留失败工具结果，原范围补验已齐**：完整Git验证仍为 **failed：505 changed／61 selected／167 unmapped／31条其它ID场景缺口，checks=[]**，见 `mx-z-xy-phase-full-validation.json`；其中7条对应归档项、24条仍在迁移范围，本ID无缺口，不扩展迁移其它实验。原18源码路径交付为56 selected／0 unmapped，`mx-z-xy-phase-delivery.json` 仍为 **failed/incomplete**：共享契约通过，141个业务节点整批900秒超时，后续API／GUI未在该命令内执行。135个原有节点按28／28／28／28／23分批，实际34／36／39／77／23条、合计209条全部通过；新增6个目标节点由上述13项采集回归覆盖。验证器逐节点核对原141节点无失败／跳过／遗漏，各批136／178／378／648／86秒，XML为 `mx-z-xy-phase-business-part1.xml` 至 `part5.xml`。实际30个本任务更改路径的计划也为56 selected／13 unmapped／errors=[]，13条GUI接线／生成类型、文档和目录简介路径已人工及对应回归核对，完整计划保存在机器状态 `current_attempt.actual_scope_plan`。默认并发前端超时及初始红灯／启动错误日志保留；最终补验不把原批次或全仓改记通过。
- 真实历史 `0828_165502_mx_z_optimal_control_xy_rf_phase_response` 只在显式临时副本 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-phase-science-d_fm0wy4/` 中完成新旧分析对照：11×11共121点、18相位，正式拟合64合格／57拒绝，**原分析JSON全部字段逐项完全相同**，保留3组PNG／PDF；对照路径集中于机器状态 `current_attempt.science_comparison`。8796份原历史文件的路径集、大小／mtime／SHA保持；474份任务外既有改动、50份非目录简介参数及暂存区SHA `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5` 保持，目录简介仅改本ID；ZIP内59份文件清单仍一致。用户8000／5173服务未操作或重启，新接口仅由假仪器／TestClient验证；真实硬件及Python3.10解释器运行未验证，既有chunk／弃用提示保留。**本实验无剩余实现或无硬件验收阻塞，计入已完成后停止，不启动下一项。**

#### 本轮 `mx-z-optimal-control-xyz-balance`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用已验收的家族 `Workflow`/串行会话、来源冻结、温度门控逐点采集与 `DeferredAcquisitionWrites`，按职责拆分 `preparation/excitation/acquisition/workflow/session/stages/run_format/linear_fit/analysis_steps`；5 个采集、3 个独立分析节点，37 个可见参数全部归属节点，参数 schema v2 与外部键不变，raw 格式独立为 v1 并兼容无标记的 schema v1/v2 历史。保留 Z 外层连续蛇形、单点轴 START=STOP、实测 `mean(R)` 最小且采集最早的选点规则、逐点温控配对与 R 标准差重试；逐层一维 V 形/复线性模拟合只作工作点诊断，不改实测结论。所有结束路径恢复温控、GS200 与运行前 X/Y 原态，保持 Z 最优控制与共同触发当前状态；分析薄入口必须显式传入目录。正文、定义简介、目录简介与根 README 已同步，用户默认 YAML 未改写。
- **修复工作区潜伏预检缺陷**：模块 `models.validate_model` 与旧 `run()` 调用 `build_applied_control(..., output_offset=...)`，而该函数签名为 `output_offset_v`，导致本实验 typed 预检必然 `TypeError`（此前无测试与 GUI 接线故未暴露）；已统一改回与已提交版本一致的 `output_offset_v`。
- 目标 **13 项采集回归**（假仪器真实写文件：六步顺序、慢盘、取消/异常部分保存、温控恢复失败抢救、写盘失败、质量重试阻断、分步与重跑保旧档、参数归属与失效、草稿不采信、来源漂移与哈希拒绝）与 **21 项分析回归**（平局最早、分步与全量一致、内容/产物指纹与归档、坏输入不动旧产物、历史资格、旧参数迁移、单点轴、真实子进程与薄入口缺目录拒绝）全部通过。**8 项后端 API 回归**（真实子进程会话、互斥、修订、EOF/取消/关闭收尾、历史只读分析、目录隔离、主路由发现与 lifespan）通过。依赖方与共享回归 63 项（Keithley×2、xy 相位响应、dg4000 偏置模型、工作点调用方、目录、workflow 历史/生命周期）通过；99 项仪器/映射/时钟/锁相回归、21 文件编译与 3.10 语法、TypeScript、生产 build、OpenAPI 精确生成及 Skill 一致性检查通过；结构检查（文档 frontmatter、流程清单、可编辑原图）无错误。
- **8 份真实历史全部在显式临时副本中完成新旧分析对照**：`measured_grid_minimum`、`response_summary`、形状/顺序/准则与警告前缀逐字段完全一致；linear_fit 为模块既有的新增诊断字段，旧结果未含属预期。原始历史目录未写入；临时副本位于系统临时目录，未另建交付或验收报告。
- 全前端 46 文件 / 376 项（2 worker）全部通过；既有构建 chunk 与测试弃用提示保留。真实硬件与 Python 3.10 解释器运行未验证；8000/PID5008、5173/PID43712 仍监听，本轮未重启、停止、取消或调用用户服务，新增接口只在本地 TestClient/假仪器进程验证。
- **交付工具如实失败**：限定本任务 29 条变更路径后仍剩 1 条跨 ID 缺口——归档项 `mx-keithley-6221-optimal-control-balanced-sensitivity`（被改文件 `analysis.py`/`linear_fit.py` 的静态调用方，无 ACQUISITION_CASES），工具 exit 1 且 checks 未执行；上述目标检查均已单独执行并通过，不把手动结果冒充交付通过，不迁移归档项。完整 Git 验证为 **30 条其他 ID 缺口（7 条归档、23 条仍在迁移范围），本 ID 无缺口**，checks 未执行，失败如实保留。应本轮要求未生成 `.cache` 报告文件，验证记录以本节文字为准。
- **2026-10-16 20:07 重复指定复核**：本 ID 在任务开始前已计入已完成，本次直接复用既有实现，**不重复迁移、不增加计数**。重新执行 42 项 Python/API（13 采集、21 分析、8 API）、6 项真实路由 GUI，XML 均为零失败、零跳过；共享采集契约 12 项、21 文件编译与 Python 3.10 语法检查、TypeScript、临时输出目录生产构建、文档/流程/原图与 fixture 一致性、Skill 同步检查通过。首次 GUI 测试已通过，但外层日志回显遇 GBK 编码错误；随后直接运行同一文件，退出码 0、6 项再次通过。完整 Git 验证仍 exit 1，30 条其他 ID 场景缺口（7 归档、23 待迁移）未解决、checks 未执行；本 ID 无缺口，不把单项通过写成全仓通过，不扩展修复其他实验。本实验没有新增实现或无硬件验收待办，真实硬件仍未验证。
- **本次机器状态同步至本文**：注册表仍为 61 项（44 typed、17 legacy），已完成 26、归档排除 7、待迁移 28；归档 ZIP 内 59 份文件逐项大小/SHA 校验通过。除本主交接外，525 份已有改动内容、51 份参数文件及目标 8 份历史共 5194 文件的大小/mtime/SHA 均与任务基线一致，Git index 与暂存差异 SHA 同样保持。20:07 只读复核 8000/PID5008、5173/PID43712 仍监听，未操作用户服务或真实仪器。所有写入性分析均显式指向测试临时目录；构建也输出至临时目录，未覆盖现有 dist。必要 XML/日志保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxzxyz-recheck-4vxrbp8y/`（`target.xml`、`gui-utf8.xml`、`validation.log` 及相应日志）；未重建 `.cache/expcodegen/`，未新增独立交付/验收/交接报告、工作区快照或临时测试脚本。已停止，不自动处理下一项。

#### 本轮 `mx-z-optimal-control-dg4000-bias-xyz-balance`：已完成，自验收与集成复核通过

- 用户已明确选择交互流程图；同一 agent 完成，未派子 agent。复用家族串行会话、来源冻结、共享 R/X/Y 质量采集和温控恢复后写盘，按准备／激励／采集／拟合／绘图／分析编排拆分；6 个采集节点（含 A12 无前置结束）、3 个分析节点、40 个可见参数。schema v3 和全部外部键不变，新 raw 格式独立为 v1，兼容已验证的无标记 v1/v2/v3 同格式历史。GS200 只要求物理断开、不连接；Z 扫描只改 offset，包络用实际冻结 Vpp。正常、取消、异常、EOF 与关闭均按共享通道快照恢复 Z、共同触发和 X/Y，恢复失败不能封存完成；原任意波存储内容及未快照调制细节不在无硬件恢复证明内。保留原 soft-L1 多初值复数 Bloch 主结果、静态 Z 零场和实测 R 最小点诊断，不自动施加拟合点。补齐闭环模式独立 K_Z 来源冻结，校正原触发策略元数据与复噪声 GUI 标签；参数值未改。分析薄入口必须显式传入目录。说明正文、定义、已有目录简介、两份 README 和 API 类型已同步。
- **48 项 Python/API 全部通过**：17 采集、23 分析、8 API；6 项目标 GUI 包含于 47 文件／382 项全前端（2 worker）全部通过。另 21 项共享工作点／历史／生命周期及 99 项仪器／映射／时钟／锁相回归通过；21 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成、Skill 一致性和注册／文档／流程／原图检查通过。OpenAPI 新增本 ID 的 13 个路径，既有 389 个路径及其余 schema 内容 SHA 保持。桌面 1920×1080 两列各 659 px，运行／历史及长诊断、长下载名无页面横向溢出，未模拟 API、页面与 console 错误均为 0；本次自建浏览器、临时 HTTP 服务已关闭，临时测试脚本已删除。真实硬件与 Python 3.10 解释器运行未验证，既有 chunk／测试弃用提示保留。
- **范围和工具结果分开记录**：完整 31 路径交付计划为 56 selected／14 unmapped／errors=[]，本 ID 无场景缺口；14 条 GUI 接线／生成类型、文档和目录简介路径已人工核对并由对应 API／GUI 覆盖。原 151 个业务节点未整批运行，而是按每批 10 节点、末批 1 节点分成 16 批全部执行，验证器逐节点核对 **233 个实际 XML testcase**，零失败／跳过／遗漏，各批均低于 900 秒（最长 392 秒）。单 ID 交付命令 exit 0：显式以目标 GUI 测试路径限定单项范围，执行共享契约 12 项、关联业务 9 项、API 8 项和 GUI 6 项；不把该限定命令冒充完整 31 路径整批交付，完整范围由上述 151 节点补齐。完整 Git 验证仍 exit 1，**29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks 未执行**，未扩展修复其他实验。初始导入、薄入口调用、假仪器断言与一次长命令核对失败的日志保留，修复后的正式结果如上；本 ID 没有剩余实现或无硬件验收待办。
- **9 份真实历史全部只在显式临时 run_dir 对照**：对任务前 HEAD 分析器和新分析器逐字段比较全部原结果，并比较每份 29 个 NPZ 数组，均在 rtol=1e-12、atol=1e-15 内一致；保留三组原科学图及其 PDF。必要证据集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-dg-bias-4rtg4sad/`：`workflow-fourth.xml`、`analysis-second.xml`、`api-first.xml`、`frontend-all.xml`、`business-01.xml` 至 `business-16.xml`、`business-verification-final.log`、`scope-plan.log`、`single-id-delivery.log`、`full-validation.log`、`science-part1.log`、`science-part2.log`、`desktop-first.log` 及 `science/` 临时运行。验证器另自动生成 `.cache/expcodegen/business-13383f6816e740f29381ea785db0e587.xml`，仅为必要测试证据；没有恢复机器状态 JSON 副本，没有新建独立交付／验收／交接报告或工作区快照。
- **机器状态同步至本文**：已完成 27、归档 7、待迁移 27，不重复计数；归档 ZIP 的 59 份文件逐项大小/SHA 一致。任务外 518 份已有改动、50 份非目录简介参数文件、目录配置中除本 ID 简介外的全部内容、9 份目标历史共 9834 文件的大小/mtime/SHA，以及 Git index 与暂存差异 SHA 均保持基线一致。21:48 只读核对 8000/PID5008、5173/PID43712 仍监听，未操作用户服务或连接真实仪器；当前后端未重启，新增接口仅在本地 TestClient／假仪器子进程验证。所有写入性分析及构建均在明确临时目录进行。本实验计入已完成后停止，不自动处理下一项。

#### 本轮 `z-aw-current-waveform-scope-check`：已完成，2026-10-17 自验收与集成复核通过

- 同一 agent 完成，未派子 agent，只迁移本项。按用户选择复用 XY 两列分步界面、Z 电压波形实验的设备连接、时钟、Scope 采集、串行会话与安全收尾；目标电流准备、来源冻结、科学评估、绘图和分析编排分别归属职责模块。6 个采集、3 个分析节点，20 个可见参数全部有编辑入口；参数 schema v1、外部键、稳定 ID、data_type 和用户默认值保留，新 raw 格式独立为 v1，兼容已验证的无标记 current 历史。冻结目标电流直接取自闭环文件，应用草稿后显式重跑；每轮新建目录，结束停止 SDS、关闭 Z 输出并保持共同触发当前状态。修正旧分析将安培数值与伏特量程比较的问题，原延迟、形状、增益、频谱及重复统计算法不变。薄分析入口必须显式传入 run_dir。实验正文、定义、已有目录简介与两份 README 已同步。
- **目标 28 项 Python/API 全部通过**（10 采集、12 分析、6 API，另 20 个子场景），XML 为 `target-final.xml`；4 项真实路由 GUI 包含于完整 48 文件／397 项全前端通过结果（`frontend-all-serial.xml`）。另 53 项集成与 67 个子场景、99 项仪器／映射／时钟／锁相回归通过；17 文件 Python 编译及 3.10 语法检查、TypeScript、临时目录生产构建、OpenAPI 精确生成、Skill 一致性及文档／流程／原图校验通过。API 只增加本 ID 的 12 个路径，原 402 个路径及其余 schema 内容保持；真实保存参数分别通过工作流及 typed adapter 的禁止连接硬件预检。桌面 1920×1080 双列各 659 px，长诊断／下载名无页面横向溢出，未模拟 API、页面及 console 错误均为 0；自建浏览器与临时服务已关闭，本次浏览器测试脚本已删除。
- **完整实现范围已补齐，工具范围如实区分**：26 项实现／说明路径（另更新本主交接）的计划为 56 selected／12 unmapped／errors=[]，本 ID 无场景缺口；12 条 GUI 接线／生成类型、文档及目录简介路径已人工核对并由相应回归覆盖。原 155 个业务节点分 16 批全部执行，验证器逐节点核对 **237 条实际 XML testcase**，零失败／跳过／遗漏，每批低于 900 秒，最长 369.17 秒；证据为 `business-01.xml` 至 `business-16.xml` 和 `business-verification.log`。单 ID 交付命令 exit 0，显式以目标 GUI 测试路径限定范围，执行共享契约、关联业务、6 API 和 4 GUI；不把该限定命令冒充完整 26 路径整批交付。完整 Git 验证仍 **failed：573 changed／61 selected／188 unmapped／29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks=[]**，不扩展修复其他实验，不宣称全仓通过。
- **本轮失败与限制保留**：完整前端首轮 2 worker 为 46 文件／355 项通过、2 个 worker 启动超时，未执行完的文件为 `RFSensitivityDirectAWPage.test.tsx` 和 `NoiseSpectrumXYKnownNoisePage.test.tsx`。原 `frontend-all.log`／`frontend-all.xml` 保留为未完成，不能据 XML 中已执行部分记全套通过；同一完整范围改用 1 worker 后 48 文件／397 项全部通过。默认并发稳定性未在本项修复，既有构建 chunk 与测试弃用提示保留。 收尾只读发现根目录 `.cache/expcodegen` 已不存在，`.cache` 目录最后变动为 12:45:19，目前仅余 `Thumbs.db`；本轮未发出删除该目录的命令，来源未归因，也未重建缓存副本。本次必要 XML／日志均在下述系统临时目录完整保留，旧缓存链接不作为现存证据。真实硬件及 Python 3.10 解释器运行未验证；本 ID 没有剩余实现或无硬件验收待办。
- **机器状态同步至本文**：已完成 28、归档 7、待迁移 26，不重复计数。两份真实历史仅在显式临时 run_dir 重分析，各 5 帧；原科学指标与全部 4 个旧 NPZ 结果数组在 rtol=1e-12、atol=1e-15 内一致，两个历史的贴边比例修正前后均为 0。28 份真实历史文件的路径集、大小／mtime／SHA、任务外 546 份已有改动内容、51 份非目录参数文件、目录配置中除本 ID 简介外的全部字节，以及 Git index 和暂存差异 SHA 均保持；归档 ZIP 内 59 份文件逐项大小／SHA 一致。12:51 只读复核 8000、5173 均无监听，未操作用户服务或连接真实仪器，未取消或改写用户会话。所有写入性分析及构建使用明确临时目录。必要 XML、工具日志、科学临时运行和桌面证据统一保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-z-current-checks-39d8ddc9/`，主要索引为 `scope-plan.log`、`full-validation.log`、`single-id-delivery.log`、`science.log`、`desktop-verified.log`；验证器自动产生的业务 XML 仅作测试证据。不恢复机器状态 JSON 副本，不新建独立交付／验收／交接报告或工作区快照。已停止，不自动处理下一项。

#### 本轮 `z-aw-closed-loop-waveform-correction`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用 XY 双列分步容器、Z 波形硬件会话、修订/取消/EOF 协议及内容指纹分析；6 个采集、2 个分析节点，39 个可见参数。按来源与 Scope 准备、采集、冻结导出、离线算法和编排拆分，保留 schema v13、三种方法、独立 holdout/重载验证和冻结格式，新 raw1 与参数版本独立。历史未使用的 `REPEATABILITY_RELATIVE_RMS` 保留键和值但不再展示，实际重复性门槛仍为 `HARMONIC_REPEATABILITY_FRACTION`。每次采集新目录，恢复危险输出后保存汇总；共同触发保持当前状态。历史保留原指标、轮次、阈值、静态标定、图、参数、填回和重新分析，原摘要 URL/字段及 operationId 保持。正文、定义、目录简介和两份 README 已同步；默认参数文件未改写，薄离线入口必须显式传入运行目录。
- **本 ID 最终 28 项 Python/API、3 个子场景及 9 项 GUI 全部通过**。11 项采集、12 项分析以 `target-final.xml` 为证，最终 API 合同与子进程由 `api-contract-final.xml` 和最终交付命令的 5 项 API 再次核对。复用实现的 71 项集成及 17 个子场景、99 项仪器/映射/时钟/锁相回归通过；完整前端 48 文件/397 项通过，最终页面另复验 9 项。26 文件 Python 编译与 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成对照、发布 operationId 和 Skill 一致性通过。1920×1080 桌面采集/分析各 659 px，两列、长目录/诊断/下载名均无页面横向溢出，未模拟 API、页面/console 错误均为 0；只读核对全部 27 份真实历史列表/摘要通过。真实硬件及 Python 3.10 解释器运行未验证，既有构建 chunk 和测试弃用提示保留。
- **工具范围与异常如实保留**：完整 Git 验证仍为 **failed：595 changed /61 selected /193 unmapped /29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks=[]**。本次实际 30 路径计划为 **failed：56 selected /12 unmapped /27 条其他 ID 缺口，checks=[]**，本 ID 无场景缺口。12 个未映射路径均为 GUI/后端接线、生成类型、文档、目录简介和对应测试，已人工核对并由本项及共享集成覆盖。最终单 ID 交付命令 exit 0，以目标 GUI 测试路径限定范围，执行共享契约、6 条关联采集业务、5 项 API/3 子场景及 9 项 GUI；不把该限定命令或手动集成通过冒充完整 30 路径/全 Git 整批通过，不扩展迁移其他 ID。初始精度断言、假子进程 stdout 捕获和桌面隐藏元素等待的红灯已修正并补验，原日志保留。验证器首次拒绝仓库外 `--report` 路径，后改用仓库内临时路径；工具证据的首次跨盘 rename 被 Windows 拒绝，改用跨盘移动并逐份核对 SHA，证据完整保留，没有绕过限制。本 ID 无剩余实现或无硬件验收待办，其他未通过 ID 不计数。
- **机器状态仅同步至本文**：已完成 29、归档 7、待迁移 25，不重复计数。schema v2 旧格式、v10 静态反馈、v12 时域反馈及 v13 谐波运行共四份真实历史，只在显式临时 `run_dir` 中完成新旧算法对照，全部科学报告字段在 rtol=1e-12、atol=1e-15 内一致，原轮次汇总及冻结 NPZ/CSV 字节保持。6074 份真实历史文件（2,985,537,653 字节）的路径集/大小/mtime/SHA、任务外 564 份已有改动、51 份非目录参数文件、目录配置中除本 ID 简介外的全部字节，以及 Git index/暂存差异 SHA 均保持；归档仍为 7 项，ZIP 内 59 份清单校验一致。13:40 只读复核 8000、5173 均无监听，临时 6187 preview 和自建浏览器已关闭；未操作用户服务、用户会话或真实仪器。必要 XML、工具日志、四份科学临时运行、唯一保护核验清单和桌面证据统一保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-z-closed-loop-checks-lla15365/`，主要索引为 `target-final.xml`、`api-contract-final.xml`、`integration.xml`、`frontend-all.xml`、`gui-final.xml`、`single-id-delivery-final.log`、`science.log`、`history-api-readonly.log`、`preservation-audit.log` 和 `desktop-final.log`；验证器自动 JSON/XML 仅作必要工具证据，已移至同目录。临时浏览器测试脚本及本轮自动空白 UI snapshot 已删除，正式测试保留。不恢复机器状态 JSON 副本，不新建独立交付/验收/交接报告或工作区快照。已停止，不自动处理下一项。

### 新增实验历史记录（不计入旧迁移完成数）

`rf-control-scale-sensitivity`：按用户确认新增统一多控制 × Scale RF 响应与灵敏度实验；只复用控制多选，不注入人为噪声，倍率范围 0.9–1.1。初版 v1 各控制只在 α=1 校相；当前 v2 改为每个控制 × Scale 独立四级校相与完整 RF 测量，外层为 10 个采集与 4 个分析节点。提供独立参数、采集/离线分析薄入口和运行/历史 GUI。原迁移范围和既有验收结论不变。说明见 [实验文档](rf_control_scale_sensitivity.md)。本实验 23 项 Python/API、既有 RF 同族 94 项、仪器/映射/时钟/锁相 99 项、共享采集契约 12 项及全前端 49 文件/400 项通过；22 文件编译及 3.10 语法、TypeScript、生产构建、OpenAPI 生成、文档/流程结构和 Skill 一致性检查通过。完整 `--delivery` 按实际变更追踪共享调用方，被 27 项旧实验场景缺口提前阻塞，报告 `.cache/expcodegen/rf-control-scale-delivery.json` 中业务状态仍为 not_run，不能当成全库交付通过；上述实际独立测试证据分别见 `rf-control-scale-target.xml`、`rf-control-scale-analysis-api.xml` 和 `rf-control-scale-sibling-regression.xml`。本项不加入旧迁移完成计数，未连接真实仪器。

后续离线分析修正为 DirectAW 四参数色散绝对中心斜率，四面板依次为斜率、色散等效 HWHM、洛伦兹绝对中心频率和公共频带灵敏度中位数；保留 3–150 Hz，HWHM 不再作为整组硬截止。该轮分析版本为 `rf-control-scale-v3-noise-quality`，支持保守组内高值筛选/保留全部记录，记录完整原始 PSD、接纳掩码、未剔除指标和清单。增加数据目录便捷脚本及正式 `noise_diagnostics` 模块，独立只读诊断整组偏差、单次异常和混合情况。真实运行 `data/RF_Control_Scale_Sensitivity/1020_194731_rf_control_scale` 的 18 组/810 条记录中识别并在主分析排除 7 条，保留 803 条，871 个原始文件和采集参数不变；只读诊断另核验 909 个既有文件未变。旧主结果归档于该运行 `results/before_noise_record_rejection_20261021_005345_251360/`。本轮 42 项分析/诊断/API 回归通过，后续 7 项重点补跑通过；前端 49 文件/400 项、TypeScript、构建、21 文件编译及 3.10 语法、文档/流程结构与 Skill 一致性检查通过。完整变更验证仍被同样 27 项旧实验采集场景缺口阻塞，不将其表述为全库验收通过；证据见 `.cache/expcodegen/rf-control-scale-noise-{quality-tests,final}.xml`、`rf-control-scale-noise-reprocess.json` 和 `rf-control-scale-noise-validation.json`。

当前采集结构进一步按用户要求改为“控制 × Scale”逐点完整四级校相：共享连接、时钟与固定准备，A13 内依次运行 Demod0、当前倍率 XY、RF 线路准备、RF 触发相位扫描、RF 解调器校相，再测响应/可选噪声/R 扫频；不从其他点或旧轮次继承相位。首点校相失败后重试也新建目录。新采集为 raw v2，分析版本为 `rf-control-scale-v4-per-point-phase`；参数 schema 和所有外部参数键保持不变。保留已验证 raw v1 的固定标称相位兼容路径，摘要明确区分条件，拒绝混合版本、错倍率、缺失/非有限校相及与响应不符的相位。GUI 清单、可编辑原图、模型说明和文档正文同步；A08–A12 移入 A13，不再作为外层可单独执行节点。最终 65 项 Python/采集/分析/API、前端 49 文件/400 项、TypeScript/构建、17 文件编译及 Python 3.10 语法、文档/流程结构和 Skill 同步检查通过。真实旧运行 18 组可只读加载，871 个原始文件和子配置不变；未连接真实仪器，未改写旧结果。完整 Git 变更验证仍有同样 27 项旧实验采集场景缺口，不能声称全库交付通过。证据：`.cache/expcodegen/rf-control-scale-per-point-{final.xml,frontend.log,structure.json,validation.json}`。后端没有热重载，须先安全结束旧会话，再重启后端使用新清单与代码。
