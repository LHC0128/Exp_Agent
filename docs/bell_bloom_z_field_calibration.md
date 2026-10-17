---
title: Bell Bloom Z 磁场频率标定
experiment_id: bell-bloom-z-field-calibration
type: Bell_Bloom_Z_Field_Calibration
scan_mode: nested_scan
defaults_file: params/experiments/bell-bloom-z-field-calibration.yaml
description: 扫描 Z DC 与 Pump 调制/解调频率，仅采集 Demod0 R 并标定带符号 Hz/V。运行页支持节点编辑、显式应用和重跑、独立分析；历史参数与分析覆盖隔离，结束恢复最近已执行配置的零偏参考脉冲与锁相。
mapping_keys:
  Z_magnetic_field:
    role: scan
  Time_sequence:
    role: scan
  Pump_modulation:
    role: fixed
  main_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  X_magnetic_field:
    role: fixed_off
  Y_magnetic_field:
    role: fixed_off
  Time_sequence_2:
    role: fixed
  Temp_Switch:
    role: point_gate
  temperature:
    role: fixed
  lockin_r:
    role: detection
required_devices: [GS200, DG4000, DG900, 锁相放大器]
learned_notes:
  - TEC103 为可选设备；连接失败时由外部软件控温。
  - Z 电压保存命令值，不将信号源内部查询当作物理输出实测值。
  - 采集期间不执行拟合；离线拟合失败仍保存诊断结果。
  - Time_sequence_2 沿用原输出策略归零关闭，不作为本实验保持输出。
---

# Bell Bloom Z 磁场频率标定

## 采集时序与系统稳定

每个频点尝试先完成 Z DC、Pump 脉冲频率/脉宽与锁相频率设置和回读，再将 `Temp_Switch` 设为 0 V DC 且保持 Output ON，等待唯一的 `SYSTEM_SETTLE_S`，采集 Demod0 R 至内存。窗口退出时先恢复 5 V DC + Output ON 并执行 `TEMP_SWITCH_ON_LAG_S`，然后通过 `DeferredAcquisitionWrites` 保存真实 NPZ。初始化目录、设备库与映射快照在关闭温控前完成。

原门控边界保持为每个频点尝试，不跨频点或跨 Z 电压合并。重试保留全部尝试；每条 Z 频扫完整后保存曲线汇总和总索引，末个 Z 电压同样保存。总索引先写同目录临时文件，再原子替换；写盘中断保留上一份完整索引。中断时保留之前完整曲线和当前曲线已读频点，索引只包含完整曲线。采集、取消、温控恢复或写盘异常均先尝试恢复，再尽力保存已读数据；次级恢复/写盘错误不覆盖原始异常。

TEC 可连接时仅下发 UI 目标温度，不启停、不轮询判稳；串口失败告警并记录外部控温来源，实测温度未知记为 `null`。具体公共规则见[统一采集规范](acquisition_timing.md)。

## 锁相采集方式与实际速率

扫频使用 `RESPONSE_RATE_SA_S` 请求率、`FREQUENCY_DURATION_S` 时长和固定 Demod0 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

本实验以静磁场灵敏度实验的 Bell Bloom 外部门控设置为基础，改变 DG4000 的 Z 方向 DC 电压，同步扫描 Pump 脉冲频率和 锁相放大器 解调频率，采集 R。以 Mx Z 标定的拟合方法提取每个电压下的共振中心，得到 `f0 = K_Z × V_Z + b`。`K_Z` 的单位是 Hz/V，表示电压每增加 1 V 时的频率变化量，保留正负号；`b` 是拟合的零偏截距。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

本实验使用设备库中全系统选择的 HF2 或 MFLI；切换入口为「仪器控制 → 设备库 → 锁相放大器」。主探测信号接所选设备的主电压输入，保留原解调器索引。启动前检查频率与通道能力；MFLI 连接后回读实际选件，EXT 时钟须核对 10 MHz 接线和锁定。历史 `HF2_*` 参数与 `hf2_configuration` 键继续兼容，代表本次实际选择的设备。具体差异、数据速率和安全范围见 [锁相接口说明](lockin_amplifier.md)。

## 参数与接线

唯一默认值来源是 [版本化参数文件](../params/experiments/bell-bloom-z-field-calibration.yaml)。GUI 保存参数只更新这个实验的文件，不改变静磁场灵敏度或 Mx 的默认配置。

- `Z_BIAS_START_V / STOP_V / STEP_V` 定义包含端点的 Z 扫描，跨度必须为整数个步进。
- `ZERO_BIAS_CENTER_FREQUENCY_HZ` 是零偏预测中心和结束恢复频率；`Z_INITIAL_HZ_PER_V` 是带符号的预估斜率，仅用于定位窗口。
- 每个 Z 点的窗口中心为 `ZERO_BIAS_CENTER_FREQUENCY_HZ + Z_INITIAL_HZ_PER_V × V_Z`；扫频半宽和步长分别由 `FREQUENCY_HALF_WIDTH_HZ`、`FREQUENCY_STEP_HZ` 控制。实际频率必须为正，每条频扫至少五点。
- `PUMP_MOD_DUTY` 在扫频中保持不变，脉宽随频率按 `duty / 100 / f` 更新。
- 主场、光功率、温度、锁相放大器 采样参数、各等待时间、重采阈值和线性 R² 门槛均由表单显式声明。

设备地址和通道由当前映射解析：Pump 载波为 100 MHz，采用 Gated/External Burst；`Time_sequence` 的 0–5 V 脉冲接载波所在信号源的外部门控输入。锁相放大器 使用 Input1、Demod0、Osc0、基波解调，保留原相位。Z 通道使用 DC；X/Y 归零关闭，第二时序通道归零关闭。这里不使用 Y RF 驱动、Z RAMP 或外部 DAQ 触发。

## 采集流程

1. 无硬件副作用地预检参数、映射和所需设备类型。建立共享设备会话；连接失败清理已建立连接。
2. 同步参考时钟并回读验证；配置固定主场、光功率、载波门控和 锁相放大器。保存实际采样率、锁相放大器 配置、映射与设备库快照。
3. TEC 可用时只设置 UI 目标温度；串口连接失败时记录告警与外部控温来源后跳过。
4. 每个 Z 点设置并验证 DC 模式和输出开关，不额外插入 Z 专用等待。
5. 每个频点更新门控频率、脉宽及 锁相放大器 频率，回读拒绝仪器钳位或频率不一致。100 MHz 载波不在循环中重新配置。
6. 将温控切为 0 V DC + ON，执行系统稳定等待，按 锁相放大器 实际采样率采集 R；完整记录暂留内存。`finally` 先恢复温控至 5 V DC + ON，并执行恢复等待，再保存当前数据。
7. R 标准差超过阈值时重采，保留所有尝试；达到次数上限仍不合格即停止，不用坏点继续拟合。DAQ 通信失败直接停止。
8. 每完成一条 Z 频扫保存汇总并更新完整曲线索引。进度显示已完成频点数及由实际耗时估算的 ETA。

正常结束、取消和配置/采集异常均调用公共安全恢复：Z、X/Y 归零关闭，第二时序通道归零关闭，温控恢复 5 V ON，Pump 门控与锁相分别恢复最近已成功执行的零偏参考频率，并沿用已执行的占空比；保留主场、光功率与载波门控策略，仅断开 TEC。各恢复动作独立记录错误。恢复失败明确标记，不报告实验成功。仅应用参数不会改变这些收尾基线；必须显式执行相应 Pump/解调模块后才更新。未完成准备时不把新参数写入保留输出。

## 数据与离线分析

```text
data/Bell_Bloom_Z_Field_Calibration/<run>/
  experiment_config.yaml
  raw/
    z_scan_index.npz
    z_000/
      frequency_0000_attempt_00.npz
      frequency_scan.npz
    ...
  results/
    curve_fits.json
    calibration_evaluation.json
    analysis.yaml
    analysis.json
    calibration_results.npz
    frequency_response_fits.png
    z_frequency_calibration.png
    z_frequency_residuals.png
```

每次尝试记录时间轴、R、均值、标准差、有效样本数、命令 Z 电压、频率、预测中心、占空比、脉宽、实际采样率和接受状态。索引只包含完整频扫；取消或失败后可手动分析已完成的曲线，不续采未完成曲线。

每条响应拟合带基线 Lorentzian：`R(f) = C + A·gamma² / ((f−f0)² + gamma²)`。复用 Mx 的中心接受规则：拟合收敛、至少五个有效频点、线宽大于步长且小于跨度一半、中心距离窗口边界至少一个半高半宽。单曲线 R² 和参数不确定度用于诊断，不另设过滤门槛，不用离散峰值替代中心。

有效中心按中心标准不确定度加权拟合 `f0 = K_Z·V_Z + b`，至少两个不同电压点；权重、不确定度与 Mx 采用同一实现。最终质量判断使用保存的 `LINEAR_R_SQUARED_MIN`。质量未通过也保存系数、排除原因和图像；无完整曲线时报告分析失败。分析始终读取本次参数快照，不连接仪器、不改写原始数据、不自动回写其他实验的磁场换算系数。

采集入口：

```powershell
agent_exp_env/Scripts/python.exe experiments/Bell_Bloom_Z_Field_Calibration.py
```

脚本只采集；GUI 采集成功后由适配器自动分析。指定运行目录重新分析：

```powershell
agent_exp_env/Scripts/python.exe experiments/Bell_Bloom_Z_Field_Calibration_plot.py data/Bell_Bloom_Z_Field_Calibration/<run>
```

## 职责模块与可执行流程

完整采集与会话执行共用 `preparation.py`、`excitation.py`、`acquisition.py` 的真实职责函数；`workflow.py` 只编排和封存。`analysis_steps.py` 验证本实验原始输入并复用现有 Mx Z Lorentzian、加权线性与三图算法，`analysis.py` 管理相同分析步骤、修订和归档，不连接硬件。

| 节点 | 职责 | 前置与门槛 |
| --- | --- | --- |
| A01 | 参数、映射、设备能力预检 | 无前置；不连接硬件 |
| A02 | 复用物理连接、输入高阻/DC 初始化 | A01；部分连接失败清理已连接设备 |
| A03 | 时钟同步与严格回读 | A02；不一致阻断输出配置 |
| A04 | 辅助场归零、主场与光功率 | A03；设置前安全校验 |
| A05 | TEC 目标温度 | A04；不启停或判稳 |
| A06 | 100 MHz Gated/External 载波与 Pump 脉冲 | A05；频率、脉宽回读一致 |
| A07 | Input1、Demod0 R 和 Osc0 准备 | A06；实际率每频点至少八样本 |
| A08 | Z DC × Pump 调制/解调扫频 | A07；标准差重试上限失败阻断后继；恢复温控后写盘 |
| A09 | 零偏参考恢复、本轮封存 | A08；恢复错误不能标记采集完成 |
| A10 | 安全结束会话 | 无前置；保持原保留输出策略 |
| B01 | 历史身份、原始格式和有效频扫资格 | 采集已结束写入；索引路径和数组有效 |
| B02 | 逐曲线 Lorentzian 中心拟合 | B01；失败中心保留诊断并排除 |
| B03 | 加权线性带符号 Hz/V 标定 | B02；按原有效中心和 R² 判断标定质量 |
| B04 | 报告、NPZ 与三张结果图 | B03；保留质量不通过的 NaN、掩码与拒绝原因 |

每次重扫创建独立目录并保存当轮参数，原目录与原始文件保持。A09 恢复参考频率后 A07 及扫描后继失效，重采必须显式恢复解调准备。仅运行 A01 不会使已完成模块失效。参数应用按 `stages.py` 的真实影响范围标记需重跑，不执行硬件动作。可编辑源图为[workflow.excalidraw](../lab_workflows/experiment_modules/bell_bloom_z_field_calibration/workflow.excalidraw)。

分阶段分析以实际配置和全部原始文件的 SHA256 内容摘要识别输入，以内容摘要验证阶段产物。输入或前置产物变动后拒绝复用；阶段记录保存所用覆盖值、修订、输入与产物摘要。B03 重算只归档本阶段及后继，不移动仍在使用的 `curve_fits.json`。旧数据无阶段记录从 B01 开始；缺少格式号的原 Bell Bloom Z ID 与原 NPZ 布局保持兼容，参数 schema 与原始格式独立。分析执行完成与标定质量通过分别展示，质量不通过仍生成原诊断和三图。

## GUI 使用

实验中心保留“运行 / 历史”两个主 Tab。运行页的实验流程容器顶部集中保存参数与布局、派生预览、仅预检、完整运行及任务日志/取消；下方两列分别为采集和独立分析。参数在节点中编辑，基础参数名称常显，高级参数可折叠；可同时展开多个节点。占空比等共享参数在真实使用节点同步，固定主场、光功率和温度仅在配置节点编辑。基础/高级分类按同一参数键统一保存。

存活会话改参显示“草稿未应用”与预计重跑范围；还原修改会清除预览。应用成功只更新修订和真实失效状态，仍须显式选择重跑。分步等待保持输出并占用全局硬件锁，需主动结束；执行中取消、通信 EOF 和后台关闭同样执行安全恢复后再释放锁。页面刷新可恢复仍存活的会话，后台重启不从磁盘记录重新连接硬件。

分析列默认编辑“下一次实验”的自动分析设置，也可选择已结束的具体目录。唯一支持的分析覆盖为 `LINEAR_R_SQUARED_MIN`；各目录草稿独立保存，切回仍保留。保存默认值始终保存下一次实验参数和统一分类，不保存历史目录覆盖，不改写历史采集快照。旧重新分析与分步分析使用相同目录互斥，运行或尚未安全封存的目录拒绝分析。

最近结果与历史保留带误差的有符号 Hz/V、零偏截距、R²、标定质量、三张图及失败/安全恢复诊断。历史按完整开始时间倒序分页，支持查看、填回参数和重新分析。历史填回只改变下一次表单，不保存、不启动；切换主 Tab 保留草稿和活动任务。最新运行失败或尚未分析时显示该次真实状态，不替换为更早结果。历史只读取本实验目录，不迁移 Mx 或静磁场数据；没有架构 Tab。
