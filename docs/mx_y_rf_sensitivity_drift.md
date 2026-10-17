---
title: Mx Y RF 灵敏度长飘
type: Mx_Y_RF_Sensitivity_Drift
scan_mode: repeated_measurement
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
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  temperature:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - 锁相放大器
learned_notes:
  - 每轮子实验复用 Mx_Y_RF_Sensitivity 的幅度响应和零 RF 噪声分析。
  - 父目录只保存轮次清单、表格和趋势图；原始数据保存在标准子运行目录。
  - 单轮分析失败或 R 点质量门限失败记录为无效轮次并继续；硬件采集异常停止任务。
experiment_id: mx-y-rf-sensitivity-drift
defaults_file: params/experiments/mx-y-rf-sensitivity-drift.yaml
---

# Mx Y RF 灵敏度长飘

## 运行与历史界面

实验中心的 `mx-y-rf-sensitivity-drift` 页面提供「运行 / 历史」两个 Tab。运行页使用本实验的交互流程图：顶部集中保存参数与布局、派生预览、预检、完整运行、任务日志和安全停止；采集与父分析分列，节点、分组、依赖和质量门槛均读取实际阶段清单。可从流程顶部下载[可编辑 Excalidraw 原图](../lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/workflow.excalidraw)。默认值仍保存到[本实验参数文件](../params/experiments/mx-y-rf-sensitivity-drift.yaml)。

参数只在实际设置或使用它的节点编辑，基础参数名称常显，高级参数在节点内折叠；主场、光功率、TEC 和 Pump 等初始化条件不在后继节点重复展示。基础/高级分类按参数键保存，切换 Tab 和分析对象不会丢失下一次实验的值。当前清单没有跨节点重复编辑键；A09 的拟合、线圈标定和噪声分析设置只控制下一次采集及即时子分析，父 B 列没有覆盖参数，始终使用 `options={}`。

存活会话改参后显示未应用草稿及预计需重跑的已完成节点，撤回全部修改会清除预览；应用失败保留草稿，应用成功只使实际受影响步骤失效，需再次显式勾选执行。保存默认值、预检和浏览历史都不启动采集。等待下一步时子进程保持设备会话、输出和全局硬件锁；刷新页面可恢复存活会话，后台重启后不从历史阶段记录恢复硬件。空闲时使用「结束会话」或无前置 A11；执行中使用「安全停止」。取消、通信 EOF 和后台关闭均完成本实验安全收尾后才释放硬件锁，通信结束后不再接受新命令。

历史列表及详情读取所选父目录的参数和已有结果。两张趋势图分别按实际经过时间与上海日期时间展示，轮次表格保留原索引、采集状态、科学有效掩码、两种灵敏度、幅度等效 HWHM、R²、排除点数及已有无效原因；CSV 和原科学产物可下载。旧摘要经专属 `GET /runs/{run_id}/summary` 只读转换非有限缺值为 `null`，浏览器不直接解析旧含 `NaN` 的 JSON；全无效轮仍可导出诊断。没有新阶段记录时显示“未开始分步分析”，已有旧表格或趋势图照常展示，不将其解释为从未分析。

父分析对象可切换到具体已结束目录，操作只绑定该父目录，不修改下一次实验设置或历史采集快照。分步分析需明确选择 B01–B03；历史「重新分析」调用同一父分析入口，均与该目录正在执行的分析互斥。正在写入的父目录可展示已有在线趋势缓存，但离线分析拒绝 `running`、`acquired` 状态。历史「填回参数」只把当前 schema 可识别的字段填入下一次实验并切回运行页，不自动保存、应用会话或运行。

## 采集时序与系统稳定

每个幅度点和每次噪声都执行“设置实际仪器配置 → `Temp_Switch` 置 0 V DC 且 Output ON → 唯一的 `SYSTEM_SETTLE_S` → 采集至内存 → 恢复 5 V DC 且 Output ON → 保存”。父轮次只是重复调度，不把整轮幅度扫描或噪声平均合并成一个关温控窗口；本实验没有 OFF/ON 配对或校相步骤。父目录、标准子目录和初始配置都在关闭温控前准备。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。记录 UI 目标值，未知实测温度为 `null`。设备和采集器准备在温控开启时完成，每次恢复后保留 `TEMP_SWITCH_ON_LAG_S`。恢复失败时先尝试恢复，再尽力保存已经读到的当前点并停止后续采集；原始采集异常不能被恢复或状态保存错误覆盖。参数迁移、异常数据保护和适用边界见[统一采集规范](acquisition_timing.md)。


## 锁相采集方式与实际速率

幅度响应使用 `RESPONSE_RATE_SA_S` 请求率、`RESPONSE_DURATION_S` 时长和 `DEMOD_IDX` 解调器；噪声使用 `NOISE_RATE_SA_S` 请求率、`NOISE_DURATION_S` 时长和同一解调器。长飘固定使用幅度等效线宽，不执行模式1扫频；历史扫频起止、点数和时长键仍保存及加载，界面不再展示这些未执行设置。`FREQUENCY_RF_AMPLITUDE_VPP` 仍用于准备 Y RF 正弦的初始幅度，保留编辑入口。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。标准子运行的原文件名、R 信号键和 NPZ 目录保持，采集元数据包含实际模块、速率及时长；旧文件缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-y-rf-sensitivity-drift.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

`DRIFT_START_IMMEDIATELY=true` 时首轮偏移为零，否则首轮等待一个 `DRIFT_INTERVAL_S`。所有计划偏移落在 `[0, DRIFT_DURATION_S)`，不包含终点。绝对计时起点是 A09 真正开始逐轮运行的时刻，前面准备或分步等待不计入轮次间隔。每轮执行标准 `Mx_Y_RF_Sensitivity` 的带符号幅度响应和零 RF 噪声；单轮耗时超过间隔时仍按原计划顺序补跑所有到期轮，不自动跳过。计划轮数由当前参数计算，不在文档固定。

采集代码按 `preparation.py`（标准准备职责）、`scheduling.py`（时间计划、取消等待、清单）和 `acquisition.py`（标准子运行与即时分析）组织，`workflow.py` 负责完整与分步共同编排。稳定采集节点为：

| 节点 | 实际职责 |
| --- | --- |
| A01–A03 | 无硬件预检、设备连接与初态回读、参考时钟严格同步回读 |
| A04–A07 | 主场/光功率/辅助场、TEC 目标、连续 Pump、Y RF 正弦与 R 解调准备 |
| A08 | 生成半开区间的轮次计划；调度参数只在此编辑 |
| A09 | 按计划逐轮采集、立即单轮分析并刷新父趋势 |
| A10 | 用最近成功配置恢复响应模式，安全关闭辅助场并封存父运行 |
| A11 | 无前置的安全结束；取消或异常同样执行收尾 |

缺失或失效的前置不会自动补跑。应用参数只使真实受影响节点及后继失效，不写入硬件；主场、光功率、TEC 和 Pump 等初始化条件只在实际设置节点展示。A09 的等待、采集和分析设置在该节点编辑。A10 收尾后，再采集必须显式重新执行 A07/A08；每次重跑新建父目录和标准子目录，保留先前原始数据及参数。收尾保持主场、Pump/Probe 光功率、连续 Pump 与门控，辅助 X/Y RF/Z 关闭，温控恢复开启，TEC 通信断开；不会把只应用但未执行的新 Pump 或响应参数写入硬件。

每个子运行仍以 `data/Mx_Y_RF_Sensitivity/<child_run>/` 保存原始数据，父目录只引用子目录。单轮 R 点超限重采用尽、拟合质量不通过或单轮分析异常记为无效轮并继续；硬件采集、温控恢复或原始数据保存失败停止后续轮。父清单同时保留已完成、失败与尚未执行的计划记录，失败子运行保留已保存的点及拒绝诊断。

新父运行和 `drift_manifest.yaml` 显式记录独立的 `raw_format_version=1`，清单结构版本仍为 1，父参数 schema 仍为 2。标准子运行独立使用其当前参数 schema 和原始格式标记，不用父参数版本推断子数据格式。缺标记旧清单仅在已有长飘身份、重复测量模式、标准子 ID/清单路径和合法记录结构均成立时识别；已核对的旧失败清单允许递增稀疏轮次索引，新格式仍要求从零连续编号，不重写历史运行。

每轮完成采集后立即分析标准子运行，随后通过保留的 `refresh_trend(parent_run_dir)` 在线接口更新父目录中的：

- `results/drift_summary.csv`
- `results/drift_summary.json`
- `results/drift_trend.png`
- `results/drift_trend_clock_time.png`

`drift_trend.png` 使用实验开始后的实际经过时间作为横轴；`drift_trend_clock_time.png` 使用 `Asia/Shanghai` 时区下每轮实际开始测量的日期和时刻作为横轴。两张趋势图均包括平坦频段灵敏度、零点实测斜率方法灵敏度、幅度等效 HWHM、拟合 R² 和无效轮次。幅度等效 HWHM 不是扫频共振线宽。

## 父目录离线分析与分阶段重算

父分析只读取清单所引用的标准 `Mx_Y_RF_Sensitivity` 子运行中已经存在的 `results/analysis.json`，提取灵敏度、零点斜率方法灵敏度、幅度等效 HWHM、响应拟合 R²、排除点数及平坦频段；不连接仪器、不重新拟合或重算子实验。父分析没有可调覆盖参数，`options={}`、`ANALYSIS_KEYS=()`；A09 中的拟合和噪声分析设置仅用于即时子分析，不能作为 B 列的父趋势覆盖值。

`analysis.py` 只编排阶段；`analysis_steps.py` 负责父清单、子目录及摘要资格和原表格，`analysis_plotting.py` 保留原绘图和报告。完整分析与分步分析调用同一组职责函数，在线 A09 同样复用其中的输入提取、表格和绘图。

| 节点 | 前置与真实门槛 | 产物（位于父 `results/`） |
| --- | --- | --- |
| B01 父清单与标准子摘要资格 | 父、子身份和原始格式有效；清单索引/偏移、子轮次与父引用一致；实际读取路径限制在当前项目的标准子运行树；父/子已停止写入；允许全无效轮导出诊断 | `analysis_inputs.json`，包含内容 SHA 指纹、有效轮数及各无效轮的原因 |
| B02 轮次表格与数值趋势 | B01 完成且输入指纹仍有效；只提取已有子摘要 | `drift_summary.csv`、`drift_summary.json` |
| B03 原趋势图与报告 | B02 完成且数值产物 SHA 仍有效 | 两张 `drift_trend*.png`、`analysis.json`、`analysis.yaml` |

普通离线分析和 B01 拒绝 `running`、`acquired` 的父运行，也拒绝仍在写入的引用子运行。A09 专用在线刷新允许父运行尚未封存、当前子轮尚无分析摘要，不创建离线阶段完成记录，也不修改采集参数。已结束的失败父运行可以分析已完成部分；未执行、质量失败或子分析失败的轮次保留原索引、状态及原因。完整、分步和在线模式都保留原父分析的全无效诊断导出能力：此时执行可完成，`valid_cycle_count=0`，表格保留所有无效轮，两张图只标出无效轮；不将导出成功解释为科学有效，也不把缺值当零。缺标记旧 schema 1 的已验证稀疏清单保持原有空洞，当前带原始格式标记的清单仍须从零连续编号。

已盘点的 `0807_164227_mx_y_rf_drift` 旧 CSV/JSON 停留在 80 条已有子运行的缓存时点，清单实际另含 32 条 `pending`。原父分析本就逐条遍历整个清单，因此按当前清单重算得到 112 行：原 80 行按原索引保真，32 个既有待执行记录照常输出；索引中原有缺轮保持缺失，不重编号、不填造轮次。这是缓存与清单时点差异，不能声称新旧整表全等。

有效掩码沿用原规则：子摘要 `success=true`、`flat_detection.success=true` 且存在幅度等效 `hwhm_hz`。清单的 `completed` 本身不代表科学有效，未找到平坦频段的已完成轮仍排除；父分析不新增 R² 门限。有效轮用于趋势的数值必须有限，灵敏度非负、线宽和斜率为正、平坦频带顺序合法。已验证的旧无效轮允许既有 `NaN` 缺值，CSV 保留 `nan`，新 JSON、YAML 和阶段状态使用 `null`；不把缺值改成零，也不改写原有子摘要或旧 JSON。任何轮次的无穷值、有效轮的 `NaN` 仍在归档前拒绝。所有轮次都会写入原表格；经过时间仍为 `scheduled_offset_s + (start_lag_s or 0)`，日期图沿用原 ISO 时间解析与上海时区规则，缺少可解析日期的轮次不进入日期横轴图。

父配置、清单、引用子配置和子摘要均参与输入 SHA 指纹；阶段产物单独保存 SHA，内容变化即使大小和 mtime 未变也会使相应阶段及后继失效。读取快照不写盘，应用分析时明确选择需要重跑的节点，不自动补齐前置。输入损坏、目录逃逸、错误身份或坏摘要在归档旧结果之前拒绝，并保存阶段拒绝诊断。重跑只归档当前节点及后继产物到 `results/before_<timestamp>/`；B03 不移动或改写 B02 的 CSV/JSON，B02 不移动 B01 输入报告。父分析只更新父分析状态与结果，不把参数或结果写回标准子运行。

旧父运行即使已有表格和趋势图，也可能没有 `analysis_state.json`。此时 B 列 `not_started` 只表示“未开始分步分析”，不能解释为该目录从未分析。后端通过只读 `read_summary(run_dir, project_root=...)` 读取已有父摘要，将旧 JSON 中的非有限缺值转成 `null` 后提供给页面；不让浏览器直接解析含 `NaN` 的旧文件，不据此补写阶段状态或重算结果。在线缓存也可只读展示，启动离线 B 列仍受停止写入资格限制。

调用分析 API 时显式提供目标父目录和项目根，例如在仓库根目录使用 `analyze(Path("data/Mx_Y_RF_Sensitivity_Drift/<parent_run>"), project_root=Path.cwd())`；路径须替换为实际、已停止写入的父运行。保留的 `_plot.py` 薄入口通过适配器默认选择最新目录；直接运行它时不能依赖 `LAB_RUN_DIR` 替代显式的目录选择。

数据布局：

```text
data/Mx_Y_RF_Sensitivity_Drift/<parent_run>/
  experiment_config.yaml
  analysis_state.json
  raw/drift_manifest.yaml
  results/analysis_inputs.json
  results/drift_summary.csv
  results/drift_summary.json
  results/drift_trend.png
  results/drift_trend_clock_time.png
  results/analysis.json
  results/analysis.yaml
  runs are referenced as data/Mx_Y_RF_Sensitivity/<child_run>/
```

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。
