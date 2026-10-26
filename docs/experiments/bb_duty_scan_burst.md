---
title: BB Pump 占空比 Burst 扫描
type: bb_rpump_duty_scan_burst
scan_mode: point_by_point
experiment_id: bb-duty-scan-burst
defaults_file: params/experiments/bb-duty-scan-burst.yaml
mapping_keys:
  Time_sequence:
    role: scan
  Pump_modulation:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  main_magnetic_field:
    role: fixed
  scope_waveform:
    role: detection
required_devices: [GS200, signal_generator, sds_acquisition, tec_controller]
learned_notes:
  - Time_sequence 的输出、Burst 与 SYNC 在结束时保留；停止 SDS 不等于停止 Pump 门控
  - 占空比为 0～1 比例，安全校验及仪器写入使用百分比
  - 误差限幅保留历史算法，不能将误差棒解释为未截断的标准不确定度
---

# BB Pump 占空比 Burst 扫描

## 目的与入口

固定 Pump 光功率控制电压和主场，扫描有限 Burst 内的脉冲占空比，利用 SDS 的 PD 瞬态测量
Bell-Bloom 建立和暗弛豫，计算有效泵浦率
`R_pump_eff = 1/tau_BB - 1/tau_dark`。这不是 Pump DC 功率扫描，也不通过 TTL 电平改变 Pump 强度。

- 采集薄入口：[BB_Rpump_DutyScan_Burst.py](../../experiments/BB_Rpump_DutyScan_Burst.py)。
- 分析薄入口：[BB_Rpump_DutyScan_Burst_plot.py](../../experiments/BB_Rpump_DutyScan_Burst_plot.py)。
- 业务模块：[bb_duty_scan_burst/](../../lab_workflows/experiment_modules/bb_duty_scan_burst/)。
- 当前参数只读取[版本化默认文件](../../params/experiments/bb-duty-scan-burst.yaml)，GUI 保存默认值写入同一文件。
  单次运行以其 `experiment_config.yaml` 为准，不用当前默认值覆盖历史快照。

## 接线、参数与准备

设备型号、资源、通道和参考时钟来自设备库与物理量映射，不写死 CH1/CH2。

1. `Pump_modulation` 连续正弦载波 → RF 开关/AOM；`Time_sequence` 固定 TTL 0/5 V 脉冲 → RF 开关控制端。
2. `Time_sequence` 使用内部触发的有限周期 Burst，正极性 SYNC → `SDS_TRIG_CHANNEL`，上升沿定义 `t=0`。
3. PD → 独立的 `SDS_PD_CHANNEL`；只读取 PD，SYNC 通道只触发、不保存其波形。
   Burst 结束由配置的 `BURST_CYCLES / MODULATION_FREQUENCY_HZ` 确定，不冒充实测边沿。
4. `Pump_laser_power` 与 `Probe_laser_power` 是 DC 控制电压；主场由 GS200 电流模式提供。
   映射中已配置的 X/Y/Z 及 AM 辅助场在本实验关闭；`rf_coil` 是 Y 通道别名，不作为独立输出再次占用。
5. `USE_HF2_MONITOR` 仅决定是否连接所选 HF2/MFLI 并初始化主电压输入为高阻、DC，不配置解调、不采集锁相数据。
   未启用时不要求或连接锁相。
6. 全部已连接时钟设备按设备库同步并严格回读；连接失败不切换仪器。
   TEC 串口失败告警跳过；连接成功只下发 UI 目标温度，不启停、不判稳，实际温度未知为 `null`。

主要参数含义如下；全部字段、单位和节点归属以模型 schema 与 `stages.definition()` 为准。

| 外部参数 | 含义与约束 |
|---|---|
| `DUTY_CYCLE_LIST` | 非空占空比列表，0～1 比例；每值乘 100 后按 `PUMP_MOD_DUTY` 安全限值检查 |
| `FIXED_PUMP_POWER_VOLTAGE`、`PROBE_POWER` | 固定 Pump/Probe DC 控制电压，单位 V，不是光学功率单位 |
| `MODULATION_FREQUENCY_HZ`、`BURST_CYCLES` | 脉冲频率和有限周期数；派生 Burst 时长 |
| `PRE_BASELINE_TIME_S`、`POST_DARK_TIME_S` | 触发前基线和 Burst 后暗弛豫记录时长；后者也用于暗衰减拟合 |
| `BURST_BURST_PERIOD_S` | 内部 Burst 触发周期，须大于完整采集窗口，避免相邻 Burst 重叠 |
| `SYSTEM_SETTLE_S` | 唯一的配置与温控门控共同稳定等待；旧 `TEMP_SWITCH_OFF_DELAY_S` 迁移，新键优先 |
| `TEMP_SWITCH_ON_DELAY_S` | 恢复温控后的专用等待，恢复不能被取消阻断 |
| `ACQ_REPEATS` | 每点重复数；最后一次探测帧作为第一个重复帧 |
| `SDS_*`、`SCALE_MIN/MAX`、`VERT_DIVS` | 通道、采样、触发、水平延迟与档位控制；实际波形须覆盖基线、Burst 和暗段 |

## 运行与历史 GUI

本实验清单显式声明 `run_step_id=A07`、`finish_step_id=A08`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

入口 `/experiments/bb-duty-scan-burst` 仅有「运行 / 历史」两个主 Tab。
运行页顶部统一提供保存参数与布局、派生预览、预检、完整运行、取消及任务状态；参数在流程节点内按基础/高级编辑。
初始化条件只在配置节点展示；`DUTY_CYCLE_LIST`、`POST_DARK_TIME_S` 等真实共享参数使用同一份下一次实验值。
修改产生“草稿未应用”和预计失效范围，应用成功后必须显式选择重跑，不自动补前置或执行硬件操作。

| 节点 | 职责与前置 |
|---|---|
| A01 | 无硬件参数、映射与安全预检；无前置 |
| A02 | 连接复用及可选锁相初始化；依赖 A01 |
| A03 | 参考时钟同步与严格回读；依赖 A02 |
| A04 | 关闭辅助场、设置主场、光功率与 TEC 目标；依赖 A03 |
| A05 | 连续载波、有限 Burst 和 SYNC；依赖 A04 |
| A06 | SDS 通道、时基与实际采样回读；依赖 A05 |
| A07 | 逐点采集、保存、部分索引与本轮安全封存；依赖 A06 |
| A08 | 显式安全结束会话；无前置，若与其他节点一起选择则最后执行 |
| B01 | 核对目录身份、数据格式、索引和波形；无前置 |
| B02 | 原建立/暗衰减拟合、误差传播和泵浦率；依赖 B01 |
| B03 | 五组原科学图与诊断图导出；依赖 B02 |

分步等待保持仪器连接、输出和全局硬件锁；空闲时用“结束会话”，执行中用任务取消。
刷新可恢复存活会话；后台重启不会依据磁盘记录恢复硬件。
完整运行使用同一套步骤并自动分析；每次重跑扫描创建新目录，不覆盖前轮快照或 raw。
A07 封存后 SDS 已停止，再扫描必须重新执行 A06；显式 A08 或异常/取消/EOF 均统一收尾。
可从 GUI 下载包内的[可编辑原图](../../lab_workflows/experiment_modules/bb_duty_scan_burst/workflow.excalidraw)。

分析列的“下一次实验”与采集列共享参数，完整运行使用这些设置；切换到具体已结束目录后，
只允许独立覆盖 `POST_DARK_TIME_S`。历史分析草稿按目录隔离，不影响下一次实验、其他目录或采集快照。
保存默认值始终保存下一次实验参数及统一布局，不保存正在编辑的历史覆盖值。
历史 Tab 展示真实快照、状态、产物和下载；浏览不启动分析，填回不自动保存或运行。
完整重分析和分阶段分析共用目录互斥，正在写入的目录拒绝分析。

## 采集时序与故障边界

每帧执行“实际配置占空比与 SDS re-arm → 关闭温控 → SYSTEM_SETTLE_S → 触发并采集至内存 → 恢复温控 → 保存”。
触发协议等待独立保留。关闭窗口内不保存波形、元数据、会话快照或分析结果。

每点最多三次档位探测，最后一帧作为 r0，后续重复锁定实际档位；`scale_used` 记录采集时的真实档位，
不再把下一档当作本帧档位。每个已读帧恢复温控后独立保存，不等待整个点结束才保护数据。
只有全部重复完成且时间轴一致才输出平均；取消、恢复或保存失败不伪造完整平均。
探测和故障帧保留在 raw 供审计，不自动混入科学拟合。
触发超时拒绝读取可能过期的记录，非有限波形、实际采样不足或时间网格不一致拒绝平均。

异常时先尝试恢复温控，再尽力保存已读帧和已完成点索引；恢复失败停止后继，保留原始异常并单独记录收尾错误。
正常或异常收尾停止 SDS、恢复温控至 5 V DC + ON、将辅助磁场归零关闭，仅断开 TEC；
主场、Pump/Probe 光功率、Pump 连续载波、Time_sequence 输出/Burst/SYNC 和锁相设置保持当前状态。
**保留门控意味着结束会话后 Pump Burst 仍可继续；需要停止它时应由操作者另行明确操作，不能误以为 SDS STOP 已关闭光路。**

## 数据、分析与兼容

```text
data/bb_rpump_duty_scan_burst/<MMDD_HHMM_tag>/
  experiment_config.yaml
  acquisition_state.json
  analysis_state.json
  raw/
    probe_d*_a*.npz
    shot_d*_r*.npz
    shot_d*_avg.npz
    duty_scan_summary.npz
  results/
    inputs.json
    fits.npz
    analysis_pub.yaml
    analysis_pub.json
    *_pub.png / *_pub.pdf
    before_*/
```

保留平均文件名及 `time_s`、`signal`、`duty_cycle`、Burst 起止等既有键，新 raw 格式独立标记为版本 1；
参数 schema 为版本 2，不因参数升级重写历史数据。
无 raw 标记仅兼容已核实的原分节配置和平均 NPZ，不猜测未知强类型格式；原数据只读。
早期 `duty_cycle_list` 的 NumPy YAML 数组仅按已验证的一维 float64 二进制格式白名单解码；
其它 Python 对象标签仍拒绝，不使用 `unsafe_load`。历史列表、摘要、通用重分析和离线入口共用此资格。
旧配置未记录的重复次数、量程边界等不在历史参数快照中伪造默认值；填回仅覆盖真实记录过的键。

离线分析必须显式指定目录，不自动选择 latest：

```powershell
agent_exp_env/Scripts/python.exe experiments/BB_Rpump_DutyScan_Burst_plot.py data/bb_rpump_duty_scan_burst/<run_id>
```

保留原 Hilbert 包络检测窗口、三参数建立拟合和五参数全振荡暗衰减拟合，
包括原固定 Larmor 拟合边界（85～95 kHz）、tau 协方差误差规则、速率误差的 100% 上游限幅、
可疑点判定及优先高质量点的原加权线性拟合。不能把这些误差棒宣称为未截断标准不确定度。
有效点不足或权重无效时仍保存逐点诊断、掩码和 NaN，不补零、不插值，也不把执行完成当作质量通过。

五组图保留瞬态示例、有效泵浦率、两路速率、瞬态放大和残差诊断，统一采用 `paper` 和英文标注。
阶段输入/产物用内容指纹防止复用过期结果，重算仅归档受影响阶段与后继，不移走仍使用的前置输入。
自动化仅使用假仪器与合成/临时数据；真实 Burst/SYNC 接线、SDS 固件时序和仪器回读仍需现场验收。
