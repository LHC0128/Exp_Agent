---
title: Bell-Bloom Burst Pump 功率扫描
type: bb_rpump_power_scan_burst
scan_mode: point_by_point
experiment_id: bb-power-scan-burst
defaults_file: params/experiments/bb-power-scan-burst.yaml
mapping_keys:
  Pump_laser_power:
    role: scan
  main_magnetic_field:
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
  scope_waveform:
    role: detection
required_devices:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - Pump_modulation
  - Time_sequence
  - Temp_Switch
  - scope_waveform
learned_notes:
  - 扫描 Pump DC 控制电压，不扫描 RF 开关 TTL 高低电平。
  - 结束保留 Time_sequence 输出、Burst、SYNC 和最后写入的 Pump 电压，不自动停止泵浦。
  - 触发原点与 burst 终点由配置确定，不通过额外读取同步通道识别。
---

# Bell-Bloom Burst Pump 功率扫描

固定占空比和内部触发 Burst 门控，逐点改变 `Pump_laser_power` 的 DC 控制电压。SDS 读取 PD 的 Bell-Bloom 建立和暗弛豫波形；重复帧在 PC 上平均，离线以 Hilbert 包络拟合建立过程、以全振荡模型拟合暗弛豫，计算有效泵浦率。功率轴仍是**控制电压**，不把电压直接标成已标定的光功率。

本实验已使用强类型模块化工作流、运行／历史双 Tab 和可勾选的交互流程图。实验 ID、`data_type`、原入口及历史 NPZ 键保持不变；不迁移 Notebook，也不改变占空比扫描实验。

当前默认值以 [GUI 保存的参数文件](../../params/experiments/bb-power-scan-burst.yaml) 为准；单次实验使用其 `experiment_config.yaml`，本文不复制当前默认数值。

## 接线与预检

- `main_magnetic_field`：GS200 电流源主场，参数 `Z_FIELD_SETPOINT` 使用 mA，设备写入时换算为 A。
- `Pump_laser_power`：DC 电压扫描强度；`Probe_laser_power`：固定 Probe DC 电压。
- `Pump_modulation`：AOM 连续正弦载波；`Time_sequence`：RF 开关固定 TTL 脉冲门控，`TRIGgered + INTernal`、有限 `BURST_CYCLES`。SYNC 正极性接 SDS 触发通道。
- `SDS_PD_CHANNEL` 读取 PD 波形；`SDS_TRIG_CHANNEL` 仅作触发，不读取同步波形。两者不能相同。PD 使用高阻、DC 耦合。
- `Temp_Switch`：关闭为 0 V DC + Output ON，恢复为 5 V DC + Output ON，不能以输出 OFF 代替。
- TEC 串口连接失败告警跳过；可连接时只下发 `TEC_TEMPERATURE`，不启停、不判稳；实际温度未知记录为 `null`。
- `USE_HF2_MONITOR` 沿用旧入口的实际能力：仅连接所选锁相并初始化主电压输入为高阻/DC，不新增解调信号采集。改变此选项时，已经连接的会话须结束后新建。

型号、资源和端点来自 [设备库](../../params/devices.yaml) 与 [物理映射](../../params/mapping.yaml)，不依赖固定型号或硬编码 CH1/CH2。复用同一资源连接；可拆分的独立通道按各自映射路由。连接前拒绝独立物理量同通道冲突、输出越界、空扫描轴、无效占空比与不覆盖 burst/暗段的触发窗口，不自动换设备或接线。

## 职责与执行清单

代码位于 [`lab_workflows/experiment_modules/bb_power_scan_burst/`](../../lab_workflows/experiment_modules/bb_power_scan_burst/)：`models.py` 管参数与迁移，`preparation.py` 管预检、连接、初态及收尾，`excitation.py` 管 AOM/Burst，`acquisition.py` 管示波器和扫描，`workflow.py` 只编排。`fitting.py` 保留原科学算法，`analysis_steps.py` 管输入及结果，`analysis_plotting.py` 导出原图，`analysis.py` 编排分析。完整与分步模式调用同一套业务步骤。

唯一清单为 `stages.definition()`，前端不维护另一份执行顺序：

| ID | 模块 | 前置 | 输入、结果与门槛 |
|---|---|---|---|
| A01 | 参数、映射与安全预检 | 无 | 全量参数及当前配置；无硬件连接、无输出修改 |
| A02 | 设备连接与可选锁相初始化 | A01 | 按资源复用连接；TEC 可选失败不阻断；部分连接失败释放已连接对象 |
| A03 | 参考时钟同步与回读 | A02 | 依据设备库设置所有已连接时钟设备，严格回读；失败停止后继 |
| A04 | 主场、光功率、温控及 TEC 目标 | A03 | 关闭其他磁场，设置主场、初点 Pump、固定 Probe；温控开启；TEC 只设目标 |
| A05 | AOM 载波与内部触发 Burst | A04 | 固定 TTL，不将门控电平当作 Pump 扫描轴 |
| A06 | 示波器配置与采样审计 | A05 | 请求采样率、通道与触发；保存实际点数、采样率及时基 |
| A07 | 逐功率试探、重复平均与保存 | A06 | 原双阈值档位规则、最多三次试探及重复平均；逐帧恢复温控后保存 |
| A08 | 结束会话并保留门控与末点 Pump | 无 | 恢复温控、关闭其他磁场、只断开 TEC；不关闭门控/Burst/SYNC 或降低 Pump |
| B01 | 加载配置与平均波形资格 | 无 | 核验身份、raw 版本、必要字段及实际时间轴；拒绝采集中目录 |
| B02 | Hilbert 包络、建立与暗弛豫拟合 | B01 | 原拟合算法、有效泵浦率和不确定度；失败仍保存诊断，不冒充科学质量合格 |
| B03 | 科研图导出 | B02 | 原五组 PNG/PDF 和图清单，不连接仪器 |

缺前置必须显式选择或先完成，不自动补选。重跑前置使后继失效，失败节点不能作为有效前置。A01 只预检不使已完成模块失效。一次扫描结束后 A06 失效，再次采集须显式准备示波器；Burst 或示波器模式回读发生漂移时拒绝采集，要求重跑相应准备，不自动重设输出。每轮扫描新建独立目录，不覆盖前一轮。

## 采集时序与结束状态

每帧严格使用：

```text
写入实际功率及准备示波器
→ Temp_Switch = 0 V DC + ON
→ SYSTEM_SETTLE_S（配置与门控共同稳定）
→ 触发协议等待、冻结示波器、读取 PD 至内存
→ Temp_Switch = 5 V DC + ON
→ 保存当前帧
```

设备准备、目录和初始配置在关温控前完成。关闭窗口内不写波形、元数据、运行快照或分析产物。触发协议等待、设备准备等待和 `TEMP_SWITCH_ON_DELAY_S` 与唯一的 `SYSTEM_SETTLE_S` 分开保留。旧 `TEMP_SWITCH_OFF_DELAY_S` 迁入系统稳定时间，显式新键优先。

保留原双阈值调档策略（低于满量程 40% 放大、高于 90% 缩小），以及最多三次试探；逐帧记录读数时档位，不以读完之后调整的档位冒充实际采集档位。重复帧不插值；实际时间轴或长度不一致时保留逐帧而拒绝不可靠平均。触发超时沿用旧入口的告警后采集策略，新增 `trigger_timeout` 审计标记；拟合成功不能单独证明该帧触发可信。

每次只缓冲当前帧，使用 `acquisition_window` 和 `DeferredAcquisitionWrites`。取消、采集错误或恢复错误均先尝试恢复温控，再抢救已读数据；恢复失败停止后继并报告未安全完成。先前完整功率点、已保存逐帧和汇总保留，不把失败重复帧拼成完整平均。写盘失败尽力另存 `recovery_*.npz`，同时保留原始异常。

**按用户确认的 A 策略，正常结束、取消、异常及通信 EOF 均保留 `Time_sequence` 的输出、Burst、SYNC，以及最后实际写入的 Pump 电压。** 不自动降到扫描最小值。主场、Probe、AOM 和锁相设置按既有策略保持；其他磁场关闭；温控恢复为 5 V DC + ON；正常只断开 TEC。应用参数但未重跑时，收尾不得将下一轮草稿写入硬件。结束会话、释放硬件锁或关闭 GUI 不等于停止这些保留输出。

## GUI 使用

本实验清单显式声明 `run_step_id=A07`、`finish_step_id=A08`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

入口为 `/experiments/bb-power-scan-burst`，只有「运行／历史」两个主 Tab，不恢复静态架构页。

- 运行页按采集／分析两列展示清单。参数在节点内编辑，基础名称常显，高级参数折叠，可同时展开多个节点；主场、Probe 和 TEC 等初始化条件只在 A04 编辑。
- `POST_DARK_TIME_S` 在 A05 的采集时间窗与 B02 的下一次实验分析设置共享同一份值。编辑归属与失效范围由后端分别声明；基础／高级分类按参数键同步，可保存并回读。
- 顶部流程容器提供保存参数与布局、派生预览、仅预检、完整运行和任务状态／取消。预检失败不启动采集；完整运行自动收尾并分析。
- 创建分步会话后显式勾选步骤执行。等待期间仍保持连接、输出和全局硬件锁。空闲应用参数只使受影响步骤失效，必须显式重跑，不自动调整仪器；草稿还原后取消重跑预览。执行中不能受限改参。
- 刷新恢复仍存活的会话；后端重启不从磁盘伪恢复硬件状态。空闲用「结束会话」，执行中用任务取消；旧接口缺流程元数据仍保留取消／结束入口。
- 独立分析可选择「下一次实验」或已结束的具体目录。下一次实验的暗段时长与采集共享；历史目录仅独立覆盖 `POST_DARK_TIME_S`，多个目录各自保留草稿，不修改下一次参数或原采集快照。保存默认值始终只保存下一次实验参数与统一分类，不保存历史分析覆盖。
- 历史页展示真实参数快照、阶段状态及产物，支持填回和重新分析；浏览不启动采集或分析，填回不自动保存或运行。原始文件只读；完整重新分析与分阶段分析共用同目录互斥。
- 提供[可编辑 Excalidraw 源图](../../lab_workflows/experiment_modules/bb_power_scan_burst/workflow.excalidraw)下载。

## 数据、科学分析与历史兼容

```text
data/bb_rpump_power_scan_burst/<时间戳>_<tag>/
  experiment_config.yaml
  raw/
    probe_p*_*.npz            # 自适应试探审计，分析不把它算作功率点
    shot_p*_r*.npz            # 原逐帧格式
    shot_p*_avg.npz           # 原平均格式，科学分析输入
    power_scan_summary.npz
    recovery_*.npz            # 发生保存错误时尽力抢救
  results/
    input_manifest.json
    fit_points.npz
    analysis_pub.yaml / analysis_pub.json
    analysis.yaml / analysis.json
    transient_fit_examples_pub.png / .pdf
    rpump_eff_vs_pump_power_pub.png / .pdf
    gamma_rates_vs_pump_power_pub.png / .pdf
    transient_fit_zoom_pub.png / .pdf
    residuals_diagnostics_pub.png / .pdf
    plot_manifest.json
```

`time_s` 是权威时间轴。Burst 起点为触发原点、终点为 `BURST_CYCLES / MODULATION_FREQUENCY_HZ`，不从未读取的同步通道检测边沿。保留原 `pump_power_voltage`、`signal`、burst 时间与实测采样审计键。

原科学规则保持：Hilbert 包络、90% 建立活跃窗、建立最多 5 ms、3 参数包络建立模型；暗段使用原活跃窗和 5 参数带阻尼正弦模型；`Gamma_BB = 1/tau_BB`、`Gamma_dark = 1/tau_dark`、`R_pump_eff = Gamma_BB - Gamma_dark`，不确定度按原公式传播。原暗段拟合频率边界仍为 85–95 kHz，初值为 90 kHz，不会因修改门控频率自动重定标分析模型。低质量规则仍为建立 R² < 0.7、负有效泵浦率或非有限结果；无有效点时仍导出诊断，标为 `diagnostic_only`，不声称科学质量合格。图上标注使用英文，沿用公共 `paper` 风格。

参数版本 `schema_version=2` 与 `raw_format_version=1` 独立。缺 raw 标记的旧数据只按已验证的原 `experiment_type` 和 `burst_pump`、`pump_power_scan`、`main_field`、`probe`、`aom_carrier`、`scope` 配置结构识别；不猜测未知格式。旧历史参数从当次配置和平均 NPZ 的真实重复次数还原，不用当前默认值冒充历史记录。旧结果不重写；有阶段记录后按内容指纹核验前置，重算归档受影响后继产物，不移动仍在使用的前置输入。

## 脚本入口与无硬件验收

```powershell
# 正式采集；此命令会连接真实仪器，仅在实际实验时运行
agent_exp_env/Scripts/python.exe experiments/BB_Rpump_PowerScan_Burst.py

# 离线分析必须显式指定运行目录，不使用 latest
agent_exp_env/Scripts/python.exe experiments/BB_Rpump_PowerScan_Burst_plot.py data/bb_rpump_power_scan_burst/<run_id>
```

正式无硬件回归分别在实验包 `test_workflow.py`、`test_analysis.py`、`GUI/backend/test_bb_power_scan_burst.py` 和 `GUI/frontend/src/pages/BBPowerScanBurstPage.test.tsx`。覆盖真实业务加假仪器、慢磁盘、取消、恢复失败、原始错误和部分帧保护、完整／分步与子进程、参数迁移、原算法冻结基准及页面实际路由。自动验证不连接真实硬件；外部时钟真实锁定、实际 RF 开关接线、SDS 触发与真实气室结果仍需实机验证。
