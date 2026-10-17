---
title: Mx Z 实际电流-耦合强度标定
type: Mx_Z_Current_Coupling_Calibration
scan_mode: nested_scan
mapping_keys:
  Z_magnetic_field:
    role: dc_scan
  rf_coil:
    role: resonance_scan
  scope_waveform:
    role: CH3_low_side_sense
  lockin_r:
    role: resonance_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2 / MFLI（按全系统选择）
  - SDS
learned_notes:
  - 电流标定绑定主场、温度、光功率、采样电阻和示波器配置。
  - BIDIRECTIONAL_SCAN 决定是否在正向后执行反向扫描，用于报告斜率和零电流中心磁滞。
experiment_id: mx-z-current-coupling-calibration
defaults_file: params/experiments/mx-z-current-coupling-calibration.yaml
---

# Mx Z 实际电流-耦合强度标定

## 采集时序与系统稳定

每个 Z DC 工作点按原顺序先采 SDS CH3 电阻电压，再执行该工作点的局部 Y RF/R 共振频扫。SDS 每个工作点一个采集窗口，R 每个频点及每次质量重采各自一个窗口；这两种记录不组成 OFF/ON 配对。`BIDIRECTIONAL_SCAN` 开启时，沿正向轴到末端后反向返回，不重复转向端点。

每个窗口均按“实际仪器配置 → `Temp_Switch` 设为 0 V DC 且 Output ON → `SYSTEM_SETTLE_S` → 读入内存 → 恢复 5 V DC 且 Output ON → 真实写盘”执行。目录、初始配置和 Z 点子目录在关闭温控前建立。`CURRENT_SETTLE_TIME_S` 是 Z DC 设置后的专用电流准备等待，在温控开启时完成；温控恢复等待与 SDS 强制触发协议等待保留。

TEC 可连接时只下发界面的目标温度，不启停控制器、不等待达标；连接失败告警并跳过。运行快照记录 UI 目标值，未知实测温度为 `null`。恢复温控失败时先尝试恢复，再尽力抢救已有数据并停止后继；采集错误不会被次级恢复或保存错误覆盖。参数迁移和公共边界见[统一采集规范](acquisition_timing.md)。

## 采集职责与分步执行

`workflow.py` 只编排步骤与会话状态；`preparation.py` 负责本 ID 预检、设备连接和 SDS 准备，`excitation.py` 复用相同接线的 Pump、Z DC 与 Y RF/R 配置，`acquisition.py` 负责两类采集窗口、正反向扫描、质量检查及写盘。普通 `run(params)` 与 `Workflow.execute()` 执行以下同一套业务函数；采集阶段定义在本包 `stages.py`，不改变独立科学分析入口。

| 步骤 | 实际职责与门槛 |
| --- | --- |
| A01 | 无硬件预检：参数、当前设备映射、锁相完整频率窗口和电流/电阻安全输入 |
| A02 | 连接并复用设备，读取初态；SDS 为必需设备，TEC 可选 |
| A03 | 按设备库同步全部已连接参考时钟，严格回读，失败停止 |
| A04 | Z/X 辅助场归零关闭，设置 Pump/Probe 光功率和固定 GS200 主场电流，开启温控 |
| A05 | TEC 仅写目标温度 |
| A06 | 准备连续 Pump 载波与常开门控 |
| A07 | 准备 Y RF 正弦与锁相 R 解调，记录最终实际率 |
| A08 | 准备 SDS CH3 固定采样率、AUTO 强制触发和自动量程，保存实际配置 |
| A09 | 逐 Z 先采电阻电压，检查饱和、电流及功率，再做逐频点 R 扫描；R 超过标准差门槛时按现有次数重采，全部失败阻断后继 |
| A10 | 安全收尾并封存本轮；Y RF 恢复零电压 DC，因此 A07/A08/A09 失效，重采前显式重新准备 |
| A11 | 无前置的安全结束；未连接时不连接硬件，也不宣称输出已恢复 |

缺少有效前置会拒绝执行，不自动补跑；采集前核对实际 Y RF 正弦及 SDS 存储/触发模式，被外部改动时要求显式重做对应准备。应用参数只按真实使用位置使步骤和后继失效，不写硬件；初始化条件在实际配置节点提供编辑归属，共享的预测参数和 Y RF 幅度在 A07/A09 共用。只供预检的采样电阻误差在 A01 编辑，修改它不使无关采集步骤失效。每次重采建立独立目录，保留前轮参数、采集状态快照和 raw。安全收尾使用最近实际执行 A06 的 Pump 配置；尚未重跑的新参数不会借结束动作写入硬件。

正常封存或异常结束时，Z/X/Y RF 归零关闭，温控恢复 5 V DC 且 Output ON；保持已设置的 GS200 主场、光功率、Pump 载波/门控和锁相设置。正常结束仅断开 TEC；时钟失败另行释放设备连接。共享调用方的旧 `run_calibration(..., include_scope=True)` 导出保留。

## GUI 运行、历史与安全结束

实验中心的本 ID 页面提供「运行 / 历史」两个主 Tab。运行页直接读取本实验的 11 个采集节点与 4 个分析节点；顶部统一提供保存参数与布局、派生预览、仅预检、完整运行、任务状态和安全停止。完整运行仍执行全部采集、安全收尾和自动分析。可编辑流程图源文件可在流程顶部下载，节点、依赖和质量门槛与真实清单一致。

参数在节点内按基础/高级分类编辑，分类按参数键统一保存并在重新进入时回读。初始化条件只在实际设置节点展示；预测中心等共享参数在 A07/A09 使用同一份值。存活会话改参时显示未应用草稿和预计需重跑的已完成节点，还原值可清除预览；应用失败保留草稿和原执行记录。应用成功只使对应步骤失效，需要显式勾选重跑，不会自动改写仪器。

分步等待期间任务仍运行，保留设备连接和输出并占用全局硬件锁；页面刷新会恢复存活会话、修订和参数。空闲时选择无前置的 A11 或点击结束会话；扫描中使用安全停止。取消、通信 EOF 与后台关闭均先执行本实验安全收尾，收尾结束后才释放硬件锁。后台重启不会从旧磁盘记录恢复仪器连接。

分析列默认编辑「下一次实验」的自动分析设置，与下一次完整运行使用同一份参数；切换到具体历史目录后，只允许覆盖 `LINEAR_R_SQUARED_MIN`。每个目录独立保留分析草稿，切换目录或 Tab 不会串用；编辑历史目录时保存默认值仍只保存下一次实验参数和统一分类。历史页读取当次参数快照、摘要、两张科学图及下载文件，填回参数不自动保存或启动。分步分析与历史「重新分析」使用同一目录互斥，采集未结束或尚未安全封存时两个入口都拒绝创建分析任务。

结果区从真实 `analysis.json` 读取标定是否有效，展示电流/采样电阻电压耦合系数、零电流中心、有效曲线数和拒绝原因，并保留线性度、磁滞、重复性、工作点及逐曲线诊断。分析程序完成不代表标定通过：`analysis_state.json` 中的 `quality_failed` 和 `analysis.json.success=false` 会明确显示质量未通过，失败时仍展示原两张诊断图和结果下载。

浏览历史结果时，本实验的只读结果接口兼容旧 `analysis.json` 中的 `NaN`、`Infinity` 和 `-Infinity`，将非有限数转换为严格 JSON 的 `null` 供页面显示；缺少的旧诊断字段明确显示“不可用”。该读取不会重新分析或改写历史文件，已结束或失败目录中的诊断、拒绝原因和原两张图继续保留，下载仍返回原文件字节。历史科学 `success=false` 仍表示标定质量未通过，缺少质量结论时显示“标定质量不可用”。

## 原始记录与故障保留

运行目录继续使用 `data/Mx_Z_Current_Coupling_Calibration/<时间戳>_<tag>/`。新运行记录独立的 `raw_format_version=1`，参数 schema 仍沿用模型版本；历史目录不重写。原文件名与已有科学键保持：

- `raw/z_NNN/sense_current.npz`：真实时间轴、采样电阻电压、电流、均值/标准差、实际率与量程/offset 诊断；新增 `quality_accepted`，失败救回的完整帧另记 `acquisition_error`。
- `raw/z_NNN/frequency_IIII_attempt_JJ.npz`：每次 R 波形和质量记录，保留失败重采。
- `raw/z_NNN/frequency_scan.npz`：完成的该 Z 点频扫、电流均值、接纳文件和扫描方向。
- `raw/z_scan_index.npz`：完整扫描的 Z 轴、预测中心、方向与频扫文件索引。

HF2、SDS、取消或磁盘失败时，已完成的前一个 Z 组和当前组已保存点保留；不伪造未完成频扫的摘要或整次索引。SDS 自动量程后续读数失败时，保存最后已完整读回的一帧作为未接纳诊断，不继续 R 频扫。恢复失败时可能仍无法保证实际温控开启，运行状态与清理错误会如实记录。


## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和 `DEMOD_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/mx-z-current-coupling-calibration.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

稳定实验 ID 为 `mx-z-current-coupling-calibration`。每个 Z DC 工作点稳定后，SDS CH3 记录采样电阻电压，再执行 Y RF 共振频扫。离线分析拟合共振中心与实际电流和采样电阻电压的关系，保存 `K_Z_Hz_per_A`、`K_Z_Hz_per_Vsense`、零电流中心、线性度、重复性和正反向磁滞。

该实验不会覆盖历史 `mx-z-field-calibration` 的 `K_Z_Hz_per_V`。入口为 `experiments/Mx_Z_Current_Coupling_Calibration.py` 和 `experiments/Mx_Z_Current_Coupling_Calibration_plot.py`。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

在 GUI「仪器控制 → 设备库」的单张“锁相放大器”卡片中全系统选择 HF2 或 MFLI；探测信号接所选设备的主电压输入，SDS CH3 仍用于采样电阻电压。共振扫频沿用 Mx Z 标定的预测中心与扫频窗口，预检检查完整窗口、解调器和振荡器索引是否在设备能力范围内。MFLI 最高 5 MHz，不会自动降低频率或回退 HF2。连接后按设备库设置并回读参考时钟，原始锁相数据保留实际型号与速率。统一接口和验收见 [锁相模块说明](lockin_amplifier.md)。

## 离线分析与结果检查

重新分析指定运行目录：

```powershell
agent_exp_env\Scripts\python.exe experiments\Mx_Z_Current_Coupling_Calibration_plot.py "data\Mx_Z_Current_Coupling_Calibration\0914_184134_mx_z_current_coupling_calibration"
```

GUI 的自动分析和重新分析会通过 `python -m` 执行共享分析模块；模块必须保留
`if __name__ == "__main__"` 入口，确保真正调用 `main()`。

完整分析和分阶段分析共用同一组科学函数：`analysis.py` 编排状态与依赖，
`analysis_steps.py` 加载原始数据、逐组拟合及计算加权标定，`analysis_plotting.py` 保留原两张科学图。

| 步骤 | 实际职责与质量结论 |
| --- | --- |
| B01 | 核对本实验身份、独立原始格式及采集结束状态；加载完整频扫，检查 R 数组长度和实际电流、电阻电压字段，失败阻断后继 |
| B02 | 真实洛伦兹共振拟合，保留线宽、扫描边界及有效点判据；逐组记录排除原因，允许全部被排除后继续生成诊断 |
| B03 | 按共振中心不确定度加权，分别拟合实际电流与电阻电压；保存零电流中心、线性度、正反向磁滞及重复性，模型不可用也保留诊断 |
| B04 | 导出两张科学图、原有结果及质量结论；原判据不通过时记录 `quality_failed`，不能作为有效完成的前置 |

实际可独立覆盖的分析参数只有 `LINEAR_R_SQUARED_MIN`。覆盖值只影响 B03/B04，
不会重做共振拟合，也不会修改采集电阻值、历史 `experiment_config.yaml` 的参数或当前默认文件。
每个具体目录分别保存覆盖值；首次读取以该目录采集快照为准，不用当前默认值替代。
旧格式要求真实实验 ID、数据类型及已验证的 Z 主场/Z Pump/X Probe/Y RF 几何标识；
缺少 `raw_format_version` 不自动视为新格式。参数 schema 与原始格式独立。

缺少总索引时只发现已经完成的 `raw/z_*/frequency_scan.npz`，不把中断中的波形点拼成完整频扫。
已结束但采集失败的目录仍可诊断其完整组；正在采集或尚未安全收尾的目录拒绝分析。
分析不依赖在线仪器，也不要求历史单点波形仍全部存在。

阶段状态记录在运行目录的 `analysis_state.json`，包含分析参数、修订以及输入和产物的内容指纹。
参数、原始数据、分析版本或前置产物改变后，对应步骤及后继失效，必须显式重算，缺少前置不会自动补跑。
重算将受影响产物和旧状态归档到 `results/before_<时间>/`，继续使用的前置产物保留原位，原始数据不移动。
取消或分析写盘失败保留已完成诊断和原始异常，不把部分文件当成有效前置。

结果写入本次运行的 `results/`：

- `analysis.json` / `analysis.yaml`：标定是否有效、有效频扫数量、各组拟合参数及排除原因。
- `calibration_results.npz`：实际电流、共振中心、接纳掩码和标定系数。
- `current_coupling_calibration.png`：电流与共振中心关系，标出被排除的拟合中心。
- `frequency_response_fits.png`：每组原始频扫与洛伦兹拟合，标注接纳状态。
- `analysis_inputs.json`、`curve_fits.json`、`calibration_evaluation.json`：分阶段输入资格、逐组拟合和加权标定诊断。
- `analysis_summary.json`：当前分析状态及主要标定指标；是否可用仍以科学结果的 `success` 为准。

“分析完成”表示分析程序执行完毕；标定是否有效应检查 `analysis.json` 的 `success`。
即使所有频扫都被排除，仍输出诊断图和排除原因，此时 NPZ 耦合系数保留 NaN，
JSON、YAML 及会话中的非有限数记为 `null`，不可用于后续标定。
分析保留现有线宽、扫描边界等判据，不修改原始数据或采集参数。

## SDS CH3 设置

已有 `Z_Coil_Current_Frequency_Response` 运行表明，当前接线下采样电阻电压最大约为 ±0.82 V。GUI 的通道、耦合、阻抗、探头倍率、采样率、时长与量程读取参数文件的 `SENSE_SCOPE_*` 字段。工作流在每个 Z 直流点自动居中，并按配置阈值和尝试上限调节量程；只有打开 `SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK` 才允许缩小量程。

若更换采样电阻、线圈或驱动范围，应先在示波器上确认波形没有贴边，再在 GUI 中调整 `SENSE_SCOPE_INITIAL_SCALE_V_DIV`。初始量程应覆盖预计峰值，且必须位于 `SENSE_SCOPE_SCALE_MIN_V_DIV` 与 `SENSE_SCOPE_SCALE_MAX_V_DIV` 之间；不要把初始量程重新设为 0.01 V/div。
