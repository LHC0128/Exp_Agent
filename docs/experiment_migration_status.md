# 实验迁移主交接

本清单于 2026-10-23 按 `lab_workflows/experiments/registry.py` 核对：完整目录共 62 项：54 项在维护的正式 Python 实验（38 个 typed_workflow、16 个 legacy_script），另 8 项代码归档只保留卡片（原模式为 7 typed、1 legacy）。Notebook、未注册脚本和示例不纳入；运行时以注册表为准，不从脚本数推断。本文作为持续更新的唯一主交接报告；原 `.cache/expcodegen/` 机器状态副本已按用户要求删除并停止维护，状态以本文为准。

新模式只表示强类型参数与执行契约，不能据此认定流程图或业务验收完成。当前**已完成31项：8项补齐、22项迁移、1项达标跳过；另7项归档排除，待迁移23项（7 typed、16 legacy）**。原迁移范围合计54项；另新增 `rf-control-scale-sensitivity`，不计入旧实验迁移完成数，完整目录总数为62（54项在维护的正式实验、8项仅保留卡片的代码归档）；默认 `list_experiments()` 只返回在维护的实验。本轮 `mx-y-rf-probe-detuning-optimization` 已完成开发、自验收与集成复核，复用 XY 分步界面，保留 PZT/Probe 蛇形网格及原双斜率科学算法；`mx-y-rf-power-optimization` 的独立 agent 完成记录保留。全仓既有场景缺口仍保留，不宣称全仓通过。已停止，不启动其他实验。

目录归档标记保存在 `params/experiment_catalog.yaml`；已移出主目录的代码归档清单以 `lab_workflows/experiments/archived.py` 为准，这些 ID 始终只显示卡片，不能仅修改目录标记恢复。专属代码、正式测试与原图按原相对路径保存在 [`old/experiments/`](../old/experiments/README.md)，共享算法、参数和历史数据原位保留；旧 GUI 运行/历史 API 返回 410。当前8项归档中，7项未完成迁移，已从待迁移范围排除，表中标为“归档，不迁移”；另 `rf-sensitivity-aw-r` 在完成后归档，保持原完成记录，不重复排除或回退计数。归档本身不算已完成，也不算达标跳过。每次接续先重读归档标记，用户未明确恢复时不迁移归档项。

原10/15/20/21项累计报告、单实验开发/验收交接及旧备份，已将有效完成结论、兼容边界及未解决问题合并到本文。原文逐字节保存在本地历史归档 `old/experiment_modularization_handoffs.zip`（不随 Git 同步），包内以原仓库相对路径存放，并附SHA清单；原机器状态副本（含 `checkpoint_history`）已停止维护，不再作为现存状态入口；旧交接路径可在归档内查到。早期“尚未接线”等阶段性待办以最终验收为准，旧分工、下一项和历史数量不再作为当前指令。

后续每次只处理用户指定的一个未归档稳定 ID；同一个 agent 完成开发、自验收、集成和交接后停止。不派子 agent，不自动选择下一项，已计入 ID 不重复计数。**没有异常时，只在本文对应行标记“已完成”，同步机器状态即可；不另写交付报告、验收报告、逐项handoff或重复工作区快照。**有异常时集中更新本文的异常章节，写明未完成项和必要证据，未通过不计数。正式测试与工具必要日志保留；事故材料除用户明确要求清理的运行关联材料外保留，不为精简删除。

既有完成项 `mx-z-optimal-control-xy-rf-sensitivity` 已完成：沿用合格的 Z 控制会话和通用分步界面，保留原蛇形网格、中心唯一校相及逐点科学算法，提供7采集/3分析节点、60个可见参数；参数 schema v2 与外部键不变，新 raw 格式独立为v1。本项 **37项 Python/API、6项 GUI 全部通过**，包含于85项 Python/API集成与351项全前端（2worker）；99项仪器/映射/时钟/锁相回归、Python编译、TypeScript、build、API生成及Skill一致性检查通过。真实历史仅在明确临时副本中重分析，36点/1有效点的14个科学数组在1e-12容差内一致，有效掩码、排名与最佳点均相同。目标历史1822文件的SHA/大小/mtime、除目录简介外的全部参数及暂存区均与本轮基线一致；目录简介仅改本ID，未另建交付或验收报告。

## 需要保留的异常与边界

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

### 本轮 `mx-z-optimal-control-xy-noise-spectrum`：已完成，自验收与集成复核通过

- 开发由同一 agent 完成，无子 agent。按职责拆分准备、噪声采集、分析与绘图，复用已验收的来源冻结、硬件会话、取消/EOF、公共历史与 XY 分步界面；7个采集/3个分析节点、42个可见参数，参数 schema v2 保留，新增独立 raw1。保留蛇形 XY DC、Y RF AC 关闭、逐条温控恢复后保存及原 Welch/频段中位数；具体历史目录可独立覆盖统计上下限。说明正文、定义、已有目录简介及两份 README 已同步，用户默认 YAML 未由本轮改写。
- 目标最终44项 Python/API 均通过：采集14、分析21、API9。证据为 `.cache/expcodegen/mx-z-xy-noise-workflow-final.xml`、`mx-z-xy-noise-analysis-api-final.xml` 和 `mx-z-xy-noise-main-api.xml`；三份无重复节点，0失败/跳过。6项目标 GUI 包含于43文件/357项全前端（2worker）全部通过，XML为 `mx-z-xy-noise-all-frontend.xml`。集成62项及79个子场景通过（`mx-z-xy-noise-integration.xml`），99项仪器/映射/时钟/锁相回归、18文件 Python 编译与3.10语法检查、TypeScript、build、OpenAPI生成及 Skill 一致性检查通过。注册、流程结构、可编辑原图和实验文档直接调用验证器检查均无错误；实际schema的已保存布局完整覆盖42字段。
- 保留的独立历史 `0901_150338_mx_z_optimal_control_xy_noise_spectrum` 已在显式临时副本中完成科学对照：121点、全部9个数值数组在 rtol=1e-12、atol=1e-18 内一致，排名及最佳点相同。证据 `.cache/expcodegen/mx-z-xy-noise-science.json` 仅保留此运行；临时副本位于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-noise-science-hobey7is/0901_150338_mx_z_optimal_control_xy_noise_spectrum`。
- **历史弃用处置**：用户明确弃用误写的 `0901_162328_mx_z_optimal_control_xy_noise_spectrum`，并要求清理其关联材料。该真实运行、对应临时科学副本、专属事故JSON及事故目录均已移入 Windows 回收站，原路径不存在；混合科学记录已移除该运行及失效聚合基线。永久删除曾被自动审批拒绝，工具仅返回“blocked by policy”，随后采用可恢复的回收站方式完成清理。独立的 `0901_150338...` 及共同上游保留。此前config原字节/时间戳与目录恢复无法完整核验的限制随用户弃用该运行解除，不声称已证明原样恢复；本项计入已完成，正式回归及通用验证日志保留。
- 根因已在目标薄入口修复：现在必须显式传入 `run_dir`，缺参数返回错误，不再隐式选择 latest；真实子进程回归核对临时目录产生结果，并验证无目录时拒绝。另修复原分析器重载外部控制来源的问题，离线只校验噪声参数与 raw，不依赖原理论/闭环来源目录；有单独回归和真实API子进程覆盖。**实现和无硬件检查已验证，本实验无剩余实现或无硬件验收阻塞。**
- 全 Git 验证实际失败：460 changed /61 selected /155 unmapped /33条其他ID场景缺口，checks=[]；其中7条对应归档项，26条仍在迁移范围，本ID无场景缺口。29路径实际范围的交付计划为56 selected/14 unmapped/31条其他ID缺口，checks=[]；因此未执行该计划中的保守跨ID业务批次，不把手工目标及集成通过冒充整份delivery通过。两份工具记录为 `.cache/expcodegen/mx-z-xy-noise-full-validation.json`、`mx-z-xy-noise-plan.json`；未映射的GUI/生成类型、文档、目录简介等14路径已人工核对。原有全仓缺口不在本轮扩展修复。
- 收尾复核：任务范围外330份原有改动的内容SHA均与事前一致，暂存区SHA保持 `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5`。归档清单仍为7项、ZIP内59份文件SHA清单全部一致。8000/PID5008、5173/PID43712仍监听，本轮没有重启、停止、取消或调用用户服务的操作接口；新增接口只在本地TestClient/假仪器进程验证。真实硬件与Python3.10解释器运行未验证，构建chunk/测试弃用提示保留。仅维护本主交接、机器状态及既有科学对照记录，不另建交付/验收/交接Markdown或工作区快照；正式回归与通用验证证据保留，用户明确弃用的运行关联材料按上述处置清理。

### 本轮 `mx-z-optimal-control-xy-leakage-response`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用既有 Z 控制会话、来源冻结、历史接口和 XY 流程容器，按准备／激励／采集／分析／绘图拆分；7采集、3分析节点，35个可见参数，保留 schema v2 与外部键，新增独立 raw1。保留同形带符号 XY 任意波、蛇形顺序、R 质量重试及实测最小值／最早采集平局规则，施加最佳点后不复测。按用户确认，正常、取消、异常、EOF 和关闭均保持 `Time_sequence_2` 当前状态；配置中断为 OFF 时也不强制重启。说明正文、定义、已有目录简介、根目录及 GUI README 已同步；目标默认 YAML 未改写。
- 目标 **41项 Python/API 全部通过**（采集14、分析19、API8，`mx-z-xy-leakage-target.xml`）；6项真实路由 GUI 包含于44文件／363项全前端（2worker）全部通过（`mx-z-xy-leakage-all-frontend.xml`）。99项仪器／映射／时钟／锁相回归、21文件 Python 编译与3.10语法检查、TypeScript、生产 build、OpenAPI生成和 Skill 一致性检查通过。直接调用验证器检查注册、说明、流程元数据及可编辑原图均无错误。真实硬件及 Python3.10 解释器运行仍未验证，既有构建 chunk／测试弃用提示保留。
- **原范围补验已齐，不改写失败批次**：28路径交付计划为56 selected／13 unmapped，结构检查无错误，本ID无场景缺口；33个其它已选ID仍缺场景声明，不据此认定其迁移完成。原交付调用的外层900秒超时使验证器未保存最终报告，业务子进程随后遇关闭输出流；原XML为 `business-cc0bbeb873254eea92fb222ed7f347f7.xml`，保留为未完成。原135个业务节点完整拆为28／28／28／28／23节点，最终34／36／39／77／23条、合计209条通过，0失败／跳过／遗漏；证据为 `mx-z-xy-leakage-business-part1.xml`、`part2.xml`、`part3.xml`、`part4-retry.xml`、`part5.xml`（均在 `.cache/expcodegen/`）。第4批首次420秒超时日志另保留，同范围在900秒上限内625秒通过。原131节点集成批次也曾900秒超时，已按原范围核对：24节点由通过的业务XML覆盖，剩余107节点分五批全部通过（`mx-z-xy-leakage-integration-part1.xml` 至 `part5.xml`）。目标API、GUI原命令所需全文件证据已齐。首次全前端工具超时仅形成38文件／329项部分XML，保留为 `mx-z-xy-leakage-all-frontend-incomplete.xml`，不冒充完整通过；重跑44文件／363项通过。逐节点核对与批次耗时保存于机器状态 `current_attempt`，最终各批均未超过900秒。
- **全仓仍未通过**：本轮完整Git检查为481 changed／61 selected／161 unmapped／32条其它ID阻断错误，`checks=[]`，见 `.cache/expcodegen/mx-z-xy-leakage-full-validation.json`；其中7条对应归档项、25条仍在迁移范围。本ID无缺口，未扩展修复其它实验。28路径原范围及计划见 `mx-z-xy-leakage-plan.json`，13个未映射的GUI接线／生成类型、文档、目录简介路径已人工核对并由对应测试覆盖。原全仓失败、原超时和初始红灯证据均保留；分批补验不把原工具调用改记通过。
- 三份真实历史均只在明确临时副本 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-leakage-science-kaue_8se/` 内重分析，各21×21点，全部原结果字段与旧分析器逐项完全相同，最小点与实际率一致；每份447个raw文件未改。旧基线与对照结果集中于既有机器状态 `current_attempt.science_baseline/science_comparison`，未另建科学交付报告。三个原目录共1353文件的路径集、大小／mtime／SHA均保持；50份非目录简介参数文件、任务外452份既有改动及暂存区SHA `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5` 保持。归档仍7项，ZIP内59份文件清单校验通过；8000/PID5008、5173/PID43712仍监听，未操作用户服务或真实仪器。临时科学副本、正式回归及必要工具日志保留，不另建交付／验收／交接Markdown或工作区快照。**本实验无剩余实现或无硬件验收阻塞，计入已完成后停止。**

### 本轮 `mx-z-optimal-control-xy-rf-phase-response`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用已验收的 Z 控制会话、来源冻结、安全收尾和 XY 流程容器，拆分准备、激励、相位拟合、采集、分析及绘图；6采集／3分析节点、45个可见参数，保留 schema v2 与外部键，新 raw 格式独立为1。保留蛇形 XY、180° 配对相位、R 质量重试、单轮异常重采、正式非线性色散及旧正弦诊断，不选择最佳 XY；修复原6点相位配置无法满足异常检测的问题，连接前明确要求至少8个偶数相位点。历史覆盖只允许两个拟合门槛，逐目录隔离；读取历史不写配置或状态，薄分析入口必须显式传入目录。正文、定义、已有目录简介及两份 README 已同步，用户默认 YAML 未改写。
- **43项 Python/API 全部通过**（采集13、分析21、API9）：证据为 `.cache/expcodegen/mx-z-xy-phase-workflow-recheck.xml`、`mx-z-xy-phase-analysis-api-final.xml`、`mx-z-xy-phase-api-history-lifespan-final.xml`，按模块选取最新节点去重核对，0失败／跳过；后者另含15项共享历史／生命周期回归。7项目标 GUI 的逐节点XML为 `mx-z-xy-phase-target-gui.xml`，无失败／跳过；45文件／370项全前端（2worker）通过，日志为 `mx-z-xy-phase-all-gui-two-workers.log`。共享采集契约12项、仪器／映射／时钟／锁相99项、22文件编译与3.10语法、TypeScript、build、OpenAPI精确生成对照及 Skill 一致性检查通过；流程清单、原图和文档直接验证无错误。桌面1920×1080两列及长诊断／下载名无页面横向溢出，未模拟API和页面／console错误均为0（`mx-z-xy-phase-desktop-final.log`）。本轮自建浏览器和 preview 已关闭，临时浏览器脚本已删除。
- **保留失败工具结果，原范围补验已齐**：完整Git验证仍为 **failed：505 changed／61 selected／167 unmapped／31条其它ID场景缺口，checks=[]**，见 `mx-z-xy-phase-full-validation.json`；其中7条对应归档项、24条仍在迁移范围，本ID无缺口，不扩展迁移其它实验。原18源码路径交付为56 selected／0 unmapped，`mx-z-xy-phase-delivery.json` 仍为 **failed/incomplete**：共享契约通过，141个业务节点整批900秒超时，后续API／GUI未在该命令内执行。135个原有节点按28／28／28／28／23分批，实际34／36／39／77／23条、合计209条全部通过；新增6个目标节点由上述13项采集回归覆盖。验证器逐节点核对原141节点无失败／跳过／遗漏，各批136／178／378／648／86秒，XML为 `mx-z-xy-phase-business-part1.xml` 至 `part5.xml`。实际30个本任务更改路径的计划也为56 selected／13 unmapped／errors=[]，13条GUI接线／生成类型、文档和目录简介路径已人工及对应回归核对，完整计划保存在机器状态 `current_attempt.actual_scope_plan`。默认并发前端超时及初始红灯／启动错误日志保留；最终补验不把原批次或全仓改记通过。
- 真实历史 `0828_165502_mx_z_optimal_control_xy_rf_phase_response` 只在显式临时副本 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-phase-science-d_fm0wy4/` 中完成新旧分析对照：11×11共121点、18相位，正式拟合64合格／57拒绝，**原分析JSON全部字段逐项完全相同**，保留3组PNG／PDF；对照路径集中于机器状态 `current_attempt.science_comparison`。8796份原历史文件的路径集、大小／mtime／SHA保持；474份任务外既有改动、50份非目录简介参数及暂存区SHA `05e2889a863b65cc2119ed03cb69b92a5fd01b16ff44c6dccd8598f4b3a541c5` 保持，目录简介仅改本ID；ZIP内59份文件清单仍一致。用户8000／5173服务未操作或重启，新接口仅由假仪器／TestClient验证；真实硬件及Python3.10解释器运行未验证，既有chunk／弃用提示保留。**本实验无剩余实现或无硬件验收阻塞，计入已完成后停止，不启动下一项。**

### 本轮 `mx-z-optimal-control-xyz-balance`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用已验收的家族 `Workflow`/串行会话、来源冻结、温度门控逐点采集与 `DeferredAcquisitionWrites`，按职责拆分 `preparation/excitation/acquisition/workflow/session/stages/run_format/linear_fit/analysis_steps`；5 个采集、3 个独立分析节点，37 个可见参数全部归属节点，参数 schema v2 与外部键不变，raw 格式独立为 v1 并兼容无标记的 schema v1/v2 历史。保留 Z 外层连续蛇形、单点轴 START=STOP、实测 `mean(R)` 最小且采集最早的选点规则、逐点温控配对与 R 标准差重试；逐层一维 V 形/复线性模拟合只作工作点诊断，不改实测结论。所有结束路径恢复温控、GS200 与运行前 X/Y 原态，保持 Z 最优控制与共同触发当前状态；分析薄入口必须显式传入目录。正文、定义简介、目录简介与根 README 已同步，用户默认 YAML 未改写。
- **修复工作区潜伏预检缺陷**：模块 `models.validate_model` 与旧 `run()` 调用 `build_applied_control(..., output_offset=...)`，而该函数签名为 `output_offset_v`，导致本实验 typed 预检必然 `TypeError`（此前无测试与 GUI 接线故未暴露）；已统一改回与已提交版本一致的 `output_offset_v`。
- 目标 **13 项采集回归**（假仪器真实写文件：六步顺序、慢盘、取消/异常部分保存、温控恢复失败抢救、写盘失败、质量重试阻断、分步与重跑保旧档、参数归属与失效、草稿不采信、来源漂移与哈希拒绝）与 **21 项分析回归**（平局最早、分步与全量一致、内容/产物指纹与归档、坏输入不动旧产物、历史资格、旧参数迁移、单点轴、真实子进程与薄入口缺目录拒绝）全部通过。**8 项后端 API 回归**（真实子进程会话、互斥、修订、EOF/取消/关闭收尾、历史只读分析、目录隔离、主路由发现与 lifespan）通过。依赖方与共享回归 63 项（Keithley×2、xy 相位响应、dg4000 偏置模型、工作点调用方、目录、workflow 历史/生命周期）通过；99 项仪器/映射/时钟/锁相回归、21 文件编译与 3.10 语法、TypeScript、生产 build、OpenAPI 精确生成及 Skill 一致性检查通过；结构检查（文档 frontmatter、流程清单、可编辑原图）无错误。
- **8 份真实历史全部在显式临时副本中完成新旧分析对照**：`measured_grid_minimum`、`response_summary`、形状/顺序/准则与警告前缀逐字段完全一致；linear_fit 为模块既有的新增诊断字段，旧结果未含属预期。原始历史目录未写入；临时副本位于系统临时目录，未另建交付或验收报告。
- 全前端 46 文件 / 376 项（2 worker）全部通过；既有构建 chunk 与测试弃用提示保留。真实硬件与 Python 3.10 解释器运行未验证；8000/PID5008、5173/PID43712 仍监听，本轮未重启、停止、取消或调用用户服务，新增接口只在本地 TestClient/假仪器进程验证。
- **交付工具如实失败**：限定本任务 29 条变更路径后仍剩 1 条跨 ID 缺口——归档项 `mx-keithley-6221-optimal-control-balanced-sensitivity`（被改文件 `analysis.py`/`linear_fit.py` 的静态调用方，无 ACQUISITION_CASES），工具 exit 1 且 checks 未执行；上述目标检查均已单独执行并通过，不把手动结果冒充交付通过，不迁移归档项。完整 Git 验证为 **30 条其他 ID 缺口（7 条归档、23 条仍在迁移范围），本 ID 无缺口**，checks 未执行，失败如实保留。应本轮要求未生成 `.cache` 报告文件，验证记录以本节文字为准。
- **2026-10-16 20:07 重复指定复核**：本 ID 在任务开始前已计入已完成，本次直接复用既有实现，**不重复迁移、不增加计数**。重新执行 42 项 Python/API（13 采集、21 分析、8 API）、6 项真实路由 GUI，XML 均为零失败、零跳过；共享采集契约 12 项、21 文件编译与 Python 3.10 语法检查、TypeScript、临时输出目录生产构建、文档/流程/原图与 fixture 一致性、Skill 同步检查通过。首次 GUI 测试已通过，但外层日志回显遇 GBK 编码错误；随后直接运行同一文件，退出码 0、6 项再次通过。完整 Git 验证仍 exit 1，30 条其他 ID 场景缺口（7 归档、23 待迁移）未解决、checks 未执行；本 ID 无缺口，不把单项通过写成全仓通过，不扩展修复其他实验。本实验没有新增实现或无硬件验收待办，真实硬件仍未验证。
- **本次机器状态同步至本文**：注册表仍为 61 项（44 typed、17 legacy），已完成 26、归档排除 7、待迁移 28；归档 ZIP 内 59 份文件逐项大小/SHA 校验通过。除本主交接外，525 份已有改动内容、51 份参数文件及目标 8 份历史共 5194 文件的大小/mtime/SHA 均与任务基线一致，Git index 与暂存差异 SHA 同样保持。20:07 只读复核 8000/PID5008、5173/PID43712 仍监听，未操作用户服务或真实仪器。所有写入性分析均显式指向测试临时目录；构建也输出至临时目录，未覆盖现有 dist。必要 XML/日志保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxzxyz-recheck-4vxrbp8y/`（`target.xml`、`gui-utf8.xml`、`validation.log` 及相应日志）；未重建 `.cache/expcodegen/`，未新增独立交付/验收/交接报告、工作区快照或临时测试脚本。已停止，不自动处理下一项。

### 本轮 `mx-z-optimal-control-dg4000-bias-xyz-balance`：已完成，自验收与集成复核通过

- 用户已明确选择交互流程图；同一 agent 完成，未派子 agent。复用家族串行会话、来源冻结、共享 R/X/Y 质量采集和温控恢复后写盘，按准备／激励／采集／拟合／绘图／分析编排拆分；6 个采集节点（含 A12 无前置结束）、3 个分析节点、40 个可见参数。schema v3 和全部外部键不变，新 raw 格式独立为 v1，兼容已验证的无标记 v1/v2/v3 同格式历史。GS200 只要求物理断开、不连接；Z 扫描只改 offset，包络用实际冻结 Vpp。正常、取消、异常、EOF 与关闭均按共享通道快照恢复 Z、共同触发和 X/Y，恢复失败不能封存完成；原任意波存储内容及未快照调制细节不在无硬件恢复证明内。保留原 soft-L1 多初值复数 Bloch 主结果、静态 Z 零场和实测 R 最小点诊断，不自动施加拟合点。补齐闭环模式独立 K_Z 来源冻结，校正原触发策略元数据与复噪声 GUI 标签；参数值未改。分析薄入口必须显式传入目录。说明正文、定义、已有目录简介、两份 README 和 API 类型已同步。
- **48 项 Python/API 全部通过**：17 采集、23 分析、8 API；6 项目标 GUI 包含于 47 文件／382 项全前端（2 worker）全部通过。另 21 项共享工作点／历史／生命周期及 99 项仪器／映射／时钟／锁相回归通过；21 文件编译与 Python 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成、Skill 一致性和注册／文档／流程／原图检查通过。OpenAPI 新增本 ID 的 13 个路径，既有 389 个路径及其余 schema 内容 SHA 保持。桌面 1920×1080 两列各 659 px，运行／历史及长诊断、长下载名无页面横向溢出，未模拟 API、页面与 console 错误均为 0；本次自建浏览器、临时 HTTP 服务已关闭，临时测试脚本已删除。真实硬件与 Python 3.10 解释器运行未验证，既有 chunk／测试弃用提示保留。
- **范围和工具结果分开记录**：完整 31 路径交付计划为 56 selected／14 unmapped／errors=[]，本 ID 无场景缺口；14 条 GUI 接线／生成类型、文档和目录简介路径已人工核对并由对应 API／GUI 覆盖。原 151 个业务节点未整批运行，而是按每批 10 节点、末批 1 节点分成 16 批全部执行，验证器逐节点核对 **233 个实际 XML testcase**，零失败／跳过／遗漏，各批均低于 900 秒（最长 392 秒）。单 ID 交付命令 exit 0：显式以目标 GUI 测试路径限定单项范围，执行共享契约 12 项、关联业务 9 项、API 8 项和 GUI 6 项；不把该限定命令冒充完整 31 路径整批交付，完整范围由上述 151 节点补齐。完整 Git 验证仍 exit 1，**29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks 未执行**，未扩展修复其他实验。初始导入、薄入口调用、假仪器断言与一次长命令核对失败的日志保留，修复后的正式结果如上；本 ID 没有剩余实现或无硬件验收待办。
- **9 份真实历史全部只在显式临时 run_dir 对照**：对任务前 HEAD 分析器和新分析器逐字段比较全部原结果，并比较每份 29 个 NPZ 数组，均在 rtol=1e-12、atol=1e-15 内一致；保留三组原科学图及其 PDF。必要证据集中于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-mxz-dg-bias-4rtg4sad/`：`workflow-fourth.xml`、`analysis-second.xml`、`api-first.xml`、`frontend-all.xml`、`business-01.xml` 至 `business-16.xml`、`business-verification-final.log`、`scope-plan.log`、`single-id-delivery.log`、`full-validation.log`、`science-part1.log`、`science-part2.log`、`desktop-first.log` 及 `science/` 临时运行。验证器另自动生成 `.cache/expcodegen/business-13383f6816e740f29381ea785db0e587.xml`，仅为必要测试证据；没有恢复机器状态 JSON 副本，没有新建独立交付／验收／交接报告或工作区快照。
- **机器状态同步至本文**：已完成 27、归档 7、待迁移 27，不重复计数；归档 ZIP 的 59 份文件逐项大小/SHA 一致。任务外 518 份已有改动、50 份非目录简介参数文件、目录配置中除本 ID 简介外的全部内容、9 份目标历史共 9834 文件的大小/mtime/SHA，以及 Git index 与暂存差异 SHA 均保持基线一致。21:48 只读核对 8000/PID5008、5173/PID43712 仍监听，未操作用户服务或连接真实仪器；当前后端未重启，新增接口仅在本地 TestClient／假仪器子进程验证。所有写入性分析及构建均在明确临时目录进行。本实验计入已完成后停止，不自动处理下一项。

### 本轮 `z-aw-current-waveform-scope-check`：已完成，2026-10-17 自验收与集成复核通过

- 同一 agent 完成，未派子 agent，只迁移本项。按用户选择复用 XY 两列分步界面、Z 电压波形实验的设备连接、时钟、Scope 采集、串行会话与安全收尾；目标电流准备、来源冻结、科学评估、绘图和分析编排分别归属职责模块。6 个采集、3 个分析节点，20 个可见参数全部有编辑入口；参数 schema v1、外部键、稳定 ID、data_type 和用户默认值保留，新 raw 格式独立为 v1，兼容已验证的无标记 current 历史。冻结目标电流直接取自闭环文件，应用草稿后显式重跑；每轮新建目录，结束停止 SDS、关闭 Z 输出并保持共同触发当前状态。修正旧分析将安培数值与伏特量程比较的问题，原延迟、形状、增益、频谱及重复统计算法不变。薄分析入口必须显式传入 run_dir。实验正文、定义、已有目录简介与两份 README 已同步。
- **目标 28 项 Python/API 全部通过**（10 采集、12 分析、6 API，另 20 个子场景），XML 为 `target-final.xml`；4 项真实路由 GUI 包含于完整 48 文件／397 项全前端通过结果（`frontend-all-serial.xml`）。另 53 项集成与 67 个子场景、99 项仪器／映射／时钟／锁相回归通过；17 文件 Python 编译及 3.10 语法检查、TypeScript、临时目录生产构建、OpenAPI 精确生成、Skill 一致性及文档／流程／原图校验通过。API 只增加本 ID 的 12 个路径，原 402 个路径及其余 schema 内容保持；真实保存参数分别通过工作流及 typed adapter 的禁止连接硬件预检。桌面 1920×1080 双列各 659 px，长诊断／下载名无页面横向溢出，未模拟 API、页面及 console 错误均为 0；自建浏览器与临时服务已关闭，本次浏览器测试脚本已删除。
- **完整实现范围已补齐，工具范围如实区分**：26 项实现／说明路径（另更新本主交接）的计划为 56 selected／12 unmapped／errors=[]，本 ID 无场景缺口；12 条 GUI 接线／生成类型、文档及目录简介路径已人工核对并由相应回归覆盖。原 155 个业务节点分 16 批全部执行，验证器逐节点核对 **237 条实际 XML testcase**，零失败／跳过／遗漏，每批低于 900 秒，最长 369.17 秒；证据为 `business-01.xml` 至 `business-16.xml` 和 `business-verification.log`。单 ID 交付命令 exit 0，显式以目标 GUI 测试路径限定范围，执行共享契约、关联业务、6 API 和 4 GUI；不把该限定命令冒充完整 26 路径整批交付。完整 Git 验证仍 **failed：573 changed／61 selected／188 unmapped／29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks=[]**，不扩展修复其他实验，不宣称全仓通过。
- **本轮失败与限制保留**：完整前端首轮 2 worker 为 46 文件／355 项通过、2 个 worker 启动超时，未执行完的文件为 `RFSensitivityDirectAWPage.test.tsx` 和 `NoiseSpectrumXYKnownNoisePage.test.tsx`。原 `frontend-all.log`／`frontend-all.xml` 保留为未完成，不能据 XML 中已执行部分记全套通过；同一完整范围改用 1 worker 后 48 文件／397 项全部通过。默认并发稳定性未在本项修复，既有构建 chunk 与测试弃用提示保留。 收尾只读发现根目录 `.cache/expcodegen` 已不存在，`.cache` 目录最后变动为 12:45:19，目前仅余 `Thumbs.db`；本轮未发出删除该目录的命令，来源未归因，也未重建缓存副本。本次必要 XML／日志均在下述系统临时目录完整保留，旧缓存链接不作为现存证据。真实硬件及 Python 3.10 解释器运行未验证；本 ID 没有剩余实现或无硬件验收待办。
- **机器状态同步至本文**：已完成 28、归档 7、待迁移 26，不重复计数。两份真实历史仅在显式临时 run_dir 重分析，各 5 帧；原科学指标与全部 4 个旧 NPZ 结果数组在 rtol=1e-12、atol=1e-15 内一致，两个历史的贴边比例修正前后均为 0。28 份真实历史文件的路径集、大小／mtime／SHA、任务外 546 份已有改动内容、51 份非目录参数文件、目录配置中除本 ID 简介外的全部字节，以及 Git index 和暂存差异 SHA 均保持；归档 ZIP 内 59 份文件逐项大小／SHA 一致。12:51 只读复核 8000、5173 均无监听，未操作用户服务或连接真实仪器，未取消或改写用户会话。所有写入性分析及构建使用明确临时目录。必要 XML、工具日志、科学临时运行和桌面证据统一保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-z-current-checks-39d8ddc9/`，主要索引为 `scope-plan.log`、`full-validation.log`、`single-id-delivery.log`、`science.log`、`desktop-verified.log`；验证器自动产生的业务 XML 仅作测试证据。不恢复机器状态 JSON 副本，不新建独立交付／验收／交接报告或工作区快照。已停止，不自动处理下一项。

### 本轮 `z-aw-closed-loop-waveform-correction`：已完成，自验收与集成复核通过

- 同一 agent 完成，未派子 agent。复用 XY 双列分步容器、Z 波形硬件会话、修订/取消/EOF 协议及内容指纹分析；6 个采集、2 个分析节点，39 个可见参数。按来源与 Scope 准备、采集、冻结导出、离线算法和编排拆分，保留 schema v13、三种方法、独立 holdout/重载验证和冻结格式，新 raw1 与参数版本独立。历史未使用的 `REPEATABILITY_RELATIVE_RMS` 保留键和值但不再展示，实际重复性门槛仍为 `HARMONIC_REPEATABILITY_FRACTION`。每次采集新目录，恢复危险输出后保存汇总；共同触发保持当前状态。历史保留原指标、轮次、阈值、静态标定、图、参数、填回和重新分析，原摘要 URL/字段及 operationId 保持。正文、定义、目录简介和两份 README 已同步；默认参数文件未改写，薄离线入口必须显式传入运行目录。
- **本 ID 最终 28 项 Python/API、3 个子场景及 9 项 GUI 全部通过**。11 项采集、12 项分析以 `target-final.xml` 为证，最终 API 合同与子进程由 `api-contract-final.xml` 和最终交付命令的 5 项 API 再次核对。复用实现的 71 项集成及 17 个子场景、99 项仪器/映射/时钟/锁相回归通过；完整前端 48 文件/397 项通过，最终页面另复验 9 项。26 文件 Python 编译与 3.10 语法、TypeScript、临时目录生产构建、OpenAPI 精确生成对照、发布 operationId 和 Skill 一致性通过。1920×1080 桌面采集/分析各 659 px，两列、长目录/诊断/下载名均无页面横向溢出，未模拟 API、页面/console 错误均为 0；只读核对全部 27 份真实历史列表/摘要通过。真实硬件及 Python 3.10 解释器运行未验证，既有构建 chunk 和测试弃用提示保留。
- **工具范围与异常如实保留**：完整 Git 验证仍为 **failed：595 changed /61 selected /193 unmapped /29 条其他 ID 场景缺口（7 归档、22 仍在迁移范围），checks=[]**。本次实际 30 路径计划为 **failed：56 selected /12 unmapped /27 条其他 ID 缺口，checks=[]**，本 ID 无场景缺口。12 个未映射路径均为 GUI/后端接线、生成类型、文档、目录简介和对应测试，已人工核对并由本项及共享集成覆盖。最终单 ID 交付命令 exit 0，以目标 GUI 测试路径限定范围，执行共享契约、6 条关联采集业务、5 项 API/3 子场景及 9 项 GUI；不把该限定命令或手动集成通过冒充完整 30 路径/全 Git 整批通过，不扩展迁移其他 ID。初始精度断言、假子进程 stdout 捕获和桌面隐藏元素等待的红灯已修正并补验，原日志保留。验证器首次拒绝仓库外 `--report` 路径，后改用仓库内临时路径；工具证据的首次跨盘 rename 被 Windows 拒绝，改用跨盘移动并逐份核对 SHA，证据完整保留，没有绕过限制。本 ID 无剩余实现或无硬件验收待办，其他未通过 ID 不计数。
- **机器状态仅同步至本文**：已完成 29、归档 7、待迁移 25，不重复计数。schema v2 旧格式、v10 静态反馈、v12 时域反馈及 v13 谐波运行共四份真实历史，只在显式临时 `run_dir` 中完成新旧算法对照，全部科学报告字段在 rtol=1e-12、atol=1e-15 内一致，原轮次汇总及冻结 NPZ/CSV 字节保持。6074 份真实历史文件（2,985,537,653 字节）的路径集/大小/mtime/SHA、任务外 564 份已有改动、51 份非目录参数文件、目录配置中除本 ID 简介外的全部字节，以及 Git index/暂存差异 SHA 均保持；归档仍为 7 项，ZIP 内 59 份清单校验一致。13:40 只读复核 8000、5173 均无监听，临时 6187 preview 和自建浏览器已关闭；未操作用户服务、用户会话或真实仪器。必要 XML、工具日志、四份科学临时运行、唯一保护核验清单和桌面证据统一保留于 `C:/Users/LiuHC/AppData/Local/Temp/exp-agent-z-closed-loop-checks-lla15365/`，主要索引为 `target-final.xml`、`api-contract-final.xml`、`integration.xml`、`frontend-all.xml`、`gui-final.xml`、`single-id-delivery-final.log`、`science.log`、`history-api-readonly.log`、`preservation-audit.log` 和 `desktop-final.log`；验证器自动 JSON/XML 仅作必要工具证据，已移至同目录。临时浏览器测试脚本及本轮自动空白 UI snapshot 已删除，正式测试保留。不恢复机器状态 JSON 副本，不新建独立交付/验收/交接报告或工作区快照。已停止，不自动处理下一项。

## 新增实验

`rf-control-scale-sensitivity`：按用户确认新增统一多控制 × Scale RF 响应与灵敏度实验；只复用控制多选，不注入人为噪声，倍率范围 0.9–1.1。初版 v1 各控制只在 α=1 校相；当前 v2 改为每个控制 × Scale 独立四级校相与完整 RF 测量，外层为 10 个采集与 4 个分析节点。提供独立参数、采集/离线分析薄入口和运行/历史 GUI。原迁移范围和既有验收结论不变。说明见 [实验文档](rf_control_scale_sensitivity.md)。本实验 23 项 Python/API、既有 RF 同族 94 项、仪器/映射/时钟/锁相 99 项、共享采集契约 12 项及全前端 49 文件/400 项通过；22 文件编译及 3.10 语法、TypeScript、生产构建、OpenAPI 生成、文档/流程结构和 Skill 一致性检查通过。完整 `--delivery` 按实际变更追踪共享调用方，被 27 项旧实验场景缺口提前阻塞，报告 `.cache/expcodegen/rf-control-scale-delivery.json` 中业务状态仍为 not_run，不能当成全库交付通过；上述实际独立测试证据分别见 `rf-control-scale-target.xml`、`rf-control-scale-analysis-api.xml` 和 `rf-control-scale-sibling-regression.xml`。本项不加入旧迁移完成计数，未连接真实仪器。

后续离线分析修正为 DirectAW 四参数色散绝对中心斜率，四面板依次为斜率、色散等效 HWHM、洛伦兹绝对中心频率和公共频带灵敏度中位数；保留 3–150 Hz，HWHM 不再作为整组硬截止。该轮分析版本为 `rf-control-scale-v3-noise-quality`，支持保守组内高值筛选/保留全部记录，记录完整原始 PSD、接纳掩码、未剔除指标和清单。增加数据目录便捷脚本及正式 `noise_diagnostics` 模块，独立只读诊断整组偏差、单次异常和混合情况。真实运行 `data/RF_Control_Scale_Sensitivity/1020_194731_rf_control_scale` 的 18 组/810 条记录中识别并在主分析排除 7 条，保留 803 条，871 个原始文件和采集参数不变；只读诊断另核验 909 个既有文件未变。旧主结果归档于该运行 `results/before_noise_record_rejection_20261021_005345_251360/`。本轮 42 项分析/诊断/API 回归通过，后续 7 项重点补跑通过；前端 49 文件/400 项、TypeScript、构建、21 文件编译及 3.10 语法、文档/流程结构与 Skill 一致性检查通过。完整变更验证仍被同样 27 项旧实验采集场景缺口阻塞，不将其表述为全库验收通过；证据见 `.cache/expcodegen/rf-control-scale-noise-{quality-tests,final}.xml`、`rf-control-scale-noise-reprocess.json` 和 `rf-control-scale-noise-validation.json`。

当前采集结构进一步按用户要求改为“控制 × Scale”逐点完整四级校相：共享连接、时钟与固定准备，A13 内依次运行 Demod0、当前倍率 XY、RF 线路准备、RF 触发相位扫描、RF 解调器校相，再测响应/可选噪声/R 扫频；不从其他点或旧轮次继承相位。首点校相失败后重试也新建目录。新采集为 raw v2，分析版本为 `rf-control-scale-v4-per-point-phase`；参数 schema 和所有外部参数键保持不变。保留已验证 raw v1 的固定标称相位兼容路径，摘要明确区分条件，拒绝混合版本、错倍率、缺失/非有限校相及与响应不符的相位。GUI 清单、可编辑原图、模型说明和文档正文同步；A08–A12 移入 A13，不再作为外层可单独执行节点。最终 65 项 Python/采集/分析/API、前端 49 文件/400 项、TypeScript/构建、17 文件编译及 Python 3.10 语法、文档/流程结构和 Skill 同步检查通过。真实旧运行 18 组可只读加载，871 个原始文件和子配置不变；未连接真实仪器，未改写旧结果。完整 Git 变更验证仍有同样 27 项旧实验采集场景缺口，不能声称全库交付通过。证据：`.cache/expcodegen/rf-control-scale-per-point-{final.xml,frontend.log,structure.json,validation.json}`。后端没有热重载，须先安全结束旧会话，再重启后端使用新清单与代码。

## 新模式

| 稳定实验 ID | 模块 | 状态 |
|---|---|---|
| `bell-bloom-z-field-calibration` | `lab_workflows/experiment_modules/bell_bloom_z_field_calibration/` | 迁移完成并通过独立无硬件验收；10 个采集 / 4 个分析节点，保留专属结果与历史接口；索引原子保存防止覆盖失败损坏 |
| `detection-chain-frequency-response` | `lab_workflows/experiment_modules/detection_chain_frequency_response/` | 补齐完成并通过独立无硬件验收；8采集/3分析，恢复失败也抢救当前已读数据，旧四键NPZ明确兼容，完整23路径delivery通过 |
| `scope-capture` | `lab_workflows/experiment_modules/scope_capture/` | 迁移完成并通过独立无硬件验收；5采集/3分析、14字段，真实Scope分步会话与原波形/PSD/双曲线历史操作，恢复后原子保存与历史资格 |
| `static-sensitivity` | `lab_workflows/experiment_modules/static_sensitivity/` | 补齐完成并通过独立无硬件业务验收；12采集/4分析，0V+ON温控、实际率旁文件指纹、raw3独立资格；19路径delivery其它ID缺口失败如实保留 |
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
| `mx-keithley-6221-main-field-calibration` | `lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-rf-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-xyz-balance` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/` | 归档，不迁移 |
| `mx-keithley-6221-optimal-control-balanced-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/` | 归档，不迁移 |
| `mx-main-field-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_noise_spectrum/` | 归档，不迁移 |
| `mx-main-field-scope-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/` | 待迁移 |
| `mx-z-field-calibration` | `lab_workflows/experiment_modules/mx_z_field_calibration/` | 迁移完成并通过独立无硬件验收；10 个采集 / 4 个分析节点，保留原科学拟合、诊断与历史 |
| `mx-z-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_noise_spectrum/` | 归档，不迁移 |
| `mx-xy-residual-field-calibration` | `lab_workflows/experiment_modules/mx_xy_residual_field_calibration/` | 待迁移 |
| `noise-spectrum-xy` | `lab_workflows/experiment_modules/noise_spectrum_xy/` | 补齐完成并通过独立无硬件验收；14采集/5分析，独立raw1与早期AMP兼容、内容指纹、通用分析资格与B01旧产物保护；完整delivery跨ID缺口失败保留 |
| `noise-spectrum-xy-known-noise` | `lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/` | 补齐完成并通过独立无硬件验收；15采集/6分析、56字段，静磁场raw3与窄旧均值兼容、归档前采样/预算保全、内容指纹；88项+47子场景及16GUI，完整Git跨ID缺口失败保留 |
| `noise-spectrum-xy-uncontrolled-probe-am` | `lab_workflows/experiment_modules/noise_spectrum_xy_probe_am/` | 补齐完成并通过独立无硬件验收；保留已有流程，补足配对故障、真实分析和 GUI 隔离证据；温控门控统一为 0 V DC 且 Output ON |
| `noise-spectrum-xy-demod3-r` | `lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/` | 待迁移 |
| `projection-noise` | `lab_workflows/experiment_modules/projection_noise/` | 待迁移（SDS 原始 PD 采集） |
| `rf-sensitivity-direct-aw-frequency` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/` | 待迁移 |
| `rf-sensitivity-aw` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw/` | 补齐完成并通过独立无硬件验收；参数 schema v4，17 个采集 / 4 个分析节点（含可选带宽步骤）；支持多控制独立采集分析、可关闭 MAD 异常谱审计和混合跨次对比，保留共用触发，兼容已验证的 v2/v3/v4 格式 |
| `rf-sensitivity-aw-r`（完成后归档，仅留卡片） | `old/experiments/lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/` | 补齐完成并通过独立无硬件验收；15采集/4分析，保留R标定与独立噪声频率/X噪声，真实分组/原子保存故障保护、63字段真实GUI；完整delivery跨ID缺口失败保留 |
| `rf-control-scale-sensitivity` | `lab_workflows/experiment_modules/rf_control_scale_sensitivity/` | 新增；多控制 × Scale，v2 每点独立四级校相，无人为噪声，兼容 v1 标称固定相位，独立采集/离线分析与交互流程图；不计入原迁移范围 |
| `rf-sensitivity-aw-known-noise` | `lab_workflows/experiment_modules/rf_sensitivity_aw_known_noise/` | 达标跳过并通过独立无硬件验收；17采集/4分析，固定噪声ON及各控制独立校相/标定；共享可关闭 MAD 异常谱筛选、逐控制与聚合审计，保留既有原始数组和 v2/v3/v4 历史兼容 |
| `t2-calibration` | `lab_workflows/experiment_modules/t2_calibration/` | 待迁移 |
| `xy-direct-aw-dc-calibration` | `lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/` | 待迁移 |

`xy-direct-aw-dc-calibration` 的当前参数见[默认参数文件](../params/experiments/xy-direct-aw-dc-calibration.yaml)。
扫描电压必须落在 AW 输出幅度与 offset 可表达的范围内；超限时预检阻止启动。
迁移代码不会静默替换用户参数。

## 旧模式

| 稳定实验 ID | 实验 | 迁移状态 |
|---|---|---|
| `noise-spectrum-xy-v2` | XY 控制噪声谱 v2 | 归档，不迁移 |
| `photon-shot-noise` | 光子散粒噪声 | 待迁移 |
| `rf-sensitivity-aw-frequency` | RF 灵敏度频率扫描（外部 AM） | 待迁移 |
| `rf-sensitivity-constxy-frequency` | RF 灵敏度频率扫描（ConstXY） | 待迁移 |
| `z-field-bandwidth` | Z 场带宽 | 待迁移 |
| `bb-duty-scan-burst` | BB Pump 占空比扫描 | 待迁移 |
| `bb-power-scan-burst` | BB Pump 功率扫描 | 待迁移 |
| `t1-calibration` | T1 标定 | 待迁移 |
| `xy-aw-voltage-calibration` | XY 任意波电压标定 | 待迁移 |
| `xy-dc-voltage-calibration` | XY DC 电压标定 | 待迁移 |
| `aw-const-omega-check` | 恒定 Omega 标定检查 | 待迁移 |
| `aw-const-omega-awscale-check` | 恒定 Omega 标定检查（AWScale） | 待迁移 |
| `static-sensitivity-optimize` | 静磁场灵敏度优化 | 待迁移 |
| `static-sensitivity-optimize-local` | 静磁场灵敏度局部优化 | 待迁移 |
| `aw-waveform-scope-check` | 任意波示波器检查 | 待迁移 |
| `dg-am-comp-transfer-check` | DG AM 到 COMP 传递检查 | 待迁移 |
| `temperature-switch-pid-cycle` | 温控开关 PID 周期测试 | 待迁移 |

迁移旧实验时保持实验 ID、`data_type`、历史运行目录和已发布参数键不变，并依次补齐
强类型模型、无副作用预检、取消检查点、异常安全恢复、离线分析入口和契约测试。

## 可复用的继续提示词

```text
阅读 D:/Code/exp_agent/docs/experiment_migration_status.md，核对实际仓库、机器状态及归档清单。
本次指定实验 ID：<填写稳定实验 ID>。

只处理这个未归档实验，由同一 agent 完成开发、自验收和必要集成检查，不派子 agent。
遵守 AGENTS.md 和 expcodegen，已有合格实现直接复用，按实际影响验证，不降低交付标准。
保留已有未提交改动和用户参数，不连接真实仪器、不操作用户服务；写入性分析使用明确的临时 run_dir。

没有异常时，只在这份主交接报告中标记已完成并同步机器状态，不生成单独交付/验收/交接报告或重复快照。
有异常时在主交接的异常章节记录问题、实际验证结果和未完成项，未通过不计数；正式回归和必要证据保留。
结束后给出主交接报告链接并停止，等待我指定下一个实验；不迁移归档项，不重复计数。
```
