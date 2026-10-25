---
title: PDB 差分衰减与纵向弛豫时间 T₁ 标定
type: experiment_type
experiment_id: t1-calibration
defaults_file: params/experiments/t1-calibration.yaml
description: 低频方波开关 Pump，SDS CH1 采集 PDB 差分，CH4 同步下降沿标记 Pump OFF；重复平均后拟合 T1，支持单点与 Probe 控制电压扫描。运行/历史双 Tab 提供可勾选流程、节点参数和独立分析；每帧恢复温控后保存，结束时 X/Y/Z 归零关闭。
version: 3
scan_mode: point_by_point
geometry:
  main_field: X
  light_propagation: X
mapping_keys:
  X_magnetic_field:
    role: fixed
  Y_magnetic_field:
    role: fixed
  Z_magnetic_field:
    role: fixed
  Probe_laser_power:
    role: scan
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: fixed
  scope_waveform:
    role: detection
  temperature:
    role: fixed
required_devices:
  - instrument: signal_generator
    role: field_light_and_timing
  - instrument: sds_acquisition
    role: pdb_differential_reader
  - instrument: tec_controller
    role: optional_temperature_target
learned_notes:
  - 真实采集信号为 CH1 PDB 差分，不是两路独立 PD，也不是锁相 AC 解调输出。
  - CH4 只作 Time_sequence 同步下降沿触发；t=0 对应 Pump OFF。
  - 每次自适应档位重试都重新配置并打开新的温控采集窗口；只有相同时间轴的接受帧才平均。
  - 结束 X/Y/Z 归零关闭，保留 Probe、Pump 载波和低频门控，温控恢复 5 V DC + ON。
---

# PDB 差分衰减与纵向弛豫时间 T₁ 标定

本实验沿用现有采集程序的 **低频方波 Pump ON/OFF → CH1 差分衰减 → 重复平均 → 指数拟合** 方案。
支持单个 Probe 控制电压或任意电压列表扫描；横轴是 **控制电压 V，不是已标定的光功率**。
本次迁移不增加双 PD 采集、旋光角归一化或载波缓变沿关断。

磁场及光传播方向沿 X，RF 线圈沿 Y。实验用 `X_magnetic_field` 的 DC 输出建立纵向场，
不连接或修改 GS200 主磁场，也不连接锁相放大器。Y/Z 辅助场关闭。

## 参数与入口

当前默认值以 [GUI 实际保存的参数文件](../../params/experiments/t1-calibration.yaml) 为准，
字段、单位及校验来自 [强类型模型](../../lab_workflows/experiment_modules/t1_calibration/models.py)。
运行覆盖优先于保存默认值；单次真实参数以该目录的 `experiment_config.yaml` 为准。
文档不另维护默认数值表。

- 采集薄入口：[T1_Calibration.py](../../experiments/T1_Calibration.py)。完整运行和分步会话共用业务函数。
- 离线薄入口：[T1_Calibration_plot.py](../../experiments/T1_Calibration_plot.py)，必须明确指定目录。
- 业务模块：[t1_calibration/](../../lab_workflows/experiment_modules/t1_calibration/)。
- 可编辑原图：[workflow.excalidraw](../../lab_workflows/experiment_modules/t1_calibration/workflow.excalidraw)，GUI 同时提供下载。

```powershell
# 采集会连接真实仪器；请先在 GUI 核对映射、接线和安全预检。
.\agent_exp_env\Scripts\python.exe experiments\T1_Calibration.py
# 离线分析，不连接仪器；替换为需要分析的运行目录。
.\agent_exp_env\Scripts\python.exe experiments\T1_Calibration_plot.py data\T1_Calibration\0529_1405_test
```

保留 `DO_POWER_SCAN`、`PROBE_POWER`、`PROBE_POWER_LIST`、`PUMP_SWITCH_FREQ`、`PUMP_MOD_AMPLITUDE`、
`PUMP_PREP_TIME`、`X_FIELD_AMPLITUDE`、示波器及温度等已有外部键。
旧 `T1_BOUNDS`（单位 s）迁移到 `T1_MIN_S` 和 `T1_MAX_S`；显式新键优先，迁移发生在合并当前默认值之前。
`TEMP_SWITCH_ON_LAG_S` 表达原来每帧恢复后的专用等待，不并入采集前的唯一 `SYSTEM_SETTLE_S`。
参数 schema 与原始格式分别版本化；更改拟合参数不重写历史文件。

主要约束：制备时间及采集时长不能超过 Pump 半周期；采样窗口须有足够点数；
拟合上下限须为递增正秒数；初始档位位于自动档位上下限之间；扫描电压和全部输出须满足当前安全限值。
示波器触发固定为 CH4 下降沿，阈值须位于 TTL 高低电平之间。

## 接线与测量原理

```text
Pump_modulation: 100 MHz sine ─→ RF switch IN ─→ AOM ─→ Pump light
Time_sequence: low-frequency square ─→ RF switch CTRL
Time_sequence SYNC ─→ SDS CH4 (falling-edge trigger)
PDB differential output ─→ SDS CH1 (DC, 1 MΩ)
Probe: CW DC control ─→ cell, propagation along X
X_magnetic_field: DC longitudinal field; Y/Z outputs OFF
Temp_Switch: 0 V + ON while reading; 5 V + ON outside the window
```

100 MHz 是载波协议，门控为固定 0–5 V、50% 占空比、180° 相位的低频方波，频率由参数指定。
关闭继承的 Burst/调制，开启同步输出。采集不是原说明中的高频 Bell-Bloom 脉冲串，
也没有实现 50–100 μs 包络 ramp；缓变沿只能作为未来独立方案，不能当作本程序已执行的行为。

制备纵向极化后停止泵浦，纵向分量满足：

$$
\langle J_x(t)\rangle=\langle J_x(0)\rangle e^{-t/T_1},\qquad
\theta_F(t)=-\mathcal G\langle J_x(t)\rangle.
$$

当前读取的是 PDB 差分电压，在线与离线共用模型：

$$
V_{\mathrm{diff}}(t)=A e^{-t/T_1}+C,\quad t\ge0.
$$

不单独测量 $V_x+V_y$，因此没有执行双 PD 的 arcsin/小角度归一化；在固定探测条件下，
幅度和偏置由拟合吸收，提取的是衰减时间常数。应事先调节波片、使用足够弱的探测光，
并核对实际衰减覆盖和信噪比，不能由代码通过推断实验质量必然合格。

## 采集流程、依赖与安全结束

实际清单以 [stages.py](../../lab_workflows/experiment_modules/t1_calibration/stages.py) 为唯一来源：

| 节点 | 职责 / 结果 | 前置与真实门槛 |
|---|---|---|
| A01 | 参数、映射、安全预检 | 无；不连接硬件，拒绝不安全输出及通道冲突 |
| A02 | 信号源按资源复用、SDS、可选 TEC | A01；连接失败释放已连接设备，TEC 串口失败告警跳过 |
| A03 | 按设备库同步参考时钟并严格回读 | A02；失败不启动输出 |
| A04 | X 场 DC、Probe 初态、Y/Z OFF、温控及 TEC 目标 | A03；设置前安全校验，TEC 不启停、不判稳 |
| A05 | Pump 载波、低频方波与同步输出 | A04；实际通道来自当前映射，不假设固定型号/同机 |
| A06 | STOP→配置 CH1/CH4，SINGle/下降沿，审计点数和采样率 | A05；高阻/DC，非法读回拒绝 |
| A07 | 逐 Probe、逐重复帧自适应档位、平均、拟合及保存 | A06；核对 Pump/Scope 模式，超时拒绝旧波形；时间轴不同不插值平均 |
| A08 | 安全结束会话 | 无前置；X/Y/Z 归零关闭，恢复温控，只断开 TEC |

每次读取/档位重试严格遵守：

**设置 Probe 与实际示波器配置 → 关闭 Temp_Switch → SYSTEM_SETTLE_S → 采集至内存 → 恢复温控 → 保存。**

目录、初始配置和设备快照在关温控前写入；关闭窗口内不保存波形、元数据、配置或分析结果。
稳定后保留 CSS 制备及半周期等待、单次触发协议等待；恢复后的专用等待独立保留。
恢复不能被取消打断。每帧最多三次档位尝试，按原 40%/90% 阈值调整；最后接受帧记录真实使用档位。
恢复后执行拟合和扫描快照保存，慢磁盘不会拉长关闭窗口。

TEC 仅下发 UI 目标；实测温度未知为 `null`，来源按实际连接/设温结果记录，不从实测值是否存在推断。
取消、读取、恢复或保存失败时停止后继采集，先恢复再抢救已读帧，保留原始异常及单独的收尾错误。
已完成点与逐帧文件不会因后一点失败丢失；正常写盘失败尽力保存 `recovery_*.npz`。

**每轮正常采集后及最终结束时 X/Y/Z 都归零关闭**。再次采集必须显式重跑 A04→A05→A06→A07。
Probe 末点 DC、Pump 载波、低频门控及 SYNC 保持；GS200 和锁相未被本流程连接或修改。
温控恢复 `5 V DC + output ON`，不是关闭该通道输出。正常结束只断开 TEC，不能把通信断开解释为所有输出关闭。

## GUI：运行 / 历史

运行页顶部统一提供保存参数与布局、只读派生预览、仅预检、预检并完整运行及任务状态。
参数在各节点展开编辑，可统一移动基础/高级分类；TEC 等持续条件只在设置节点出现。
`PROBE_POWER` 的初态/采集入口共享值；拟合界限在采集与“下一次实验”的分析节点共享。

分步执行不补选缺失前置。空闲会话改参先成为未应用草稿，显示预计重跑范围；
应用成功后只使实际受影响步骤失效，必须显式勾选重跑，不自动写仪器。
还原修改会清除预览；仅预检不使无关已完成步骤失效。重跑创建新运行目录，不覆盖旧 `raw/` 或参数快照。
等待期间维持输出、连接并占用全局硬件锁；空闲时“结束会话”，执行中用任务取消。
刷新恢复存活会话；后台重启不从磁盘恢复硬件。取消、通信 EOF、后台关闭都会先安全收尾再释放锁。

历史页只读列出有已验证配置的运行，查看当轮快照、结果图、状态与下载；缺结果不阻断浏览。
“填回参数”只更新下一次草稿，不自动保存或运行。没有配置的已验证早期单点原始数据仍可用明确目录的离线入口分析，
但不伪造一份历史配置使其进入列表。

独立分析绑定明确目录，只支持拟合上下限覆盖；每个目录保留自己的草稿，与下一次实验隔离。
保存默认值始终只保存下一次参数和统一分类，即使正在编辑历史目录也不会保存历史覆盖值。
采集中目录禁止分析；分阶段分析与旧重新分析入口共用同目录互斥。

## 数据与独立分析

```text
data/T1_Calibration/MMDD_HHMM_tag/
  experiment_config.yaml
  acquisition_state.json
  raw/
    frame_p*_r*_a*.npz       # 每个尝试帧：time_s、signal_v、probe_power、scale_used
    point_p*.npz            # 完整点平均：旧 t_axis/vdiff_avg/T1 等键
    t1_measurement.npz       # 单点，仍只存 t≥0 的衰减段
    power_scan_results.npz   # 扫描，仍含 probe_power/T1/T1_err/scale_used/n_acquired
    power_scan_waveforms.npz # 扫描，仍含 t_axis/vdiff_stack/popt_stack/probe_power
  results/
    input_manifest.json
    fit_points.npz
    analysis.yaml
    analysis.json
    plot_manifest.json
    *.png / *.pdf
  analysis_state.json
```

单点和扫描兼容产物按实际模式生成，不要求所有文件同时存在。扫描中逐点更新完整点汇总；
不完整点只保留已读帧，不冒充完成平均。失败诊断保留 NaN、有效掩码及原因，不用插值补点。

| 节点 | 分析职责 | 门槛及产物 |
|---|---|---|
| B01 | 加载配置、身份/原始格式及数组资格 | 无前置；生成输入指纹和清单；错误身份、损坏数据或正在写入时拒绝 |
| B02 | 指数拟合及倒数弛豫率加权线性拟合 | B01；输出逐点拟合、有效掩码、拒绝原因及摘要 |
| B03 | 科研图与产物清单 | B02；导出原图名 PNG/PDF，记录内容指纹 |

指数拟合只用 $t\ge0$、至少十点的衰减段，保留有界 $A,T_1,C$ 拟合和协方差误差。
`T1_err` 是协方差平方根，**不是重复测量 SEM**；误差超过 T1 的三倍或非有限时剔除科学值但保留诊断。
扫描中至少两个不同控制电压的有效点才拟合：

$$
T_1^{-1}(V)=T_{1,0}^{-1}+\alpha_V V,\qquad
\sigma_{1/T_1}=\sigma_{T_1}/T_1^2.
$$

保留原逆误差加权 `polyfit` 和 `T1_0_inv`、`T1_0_ms`、`alpha` 字段。
这里 $\alpha_V$ 的单位是 s⁻¹/V，不能当作已标定的每单位光功率系数。截距非正时不报告有效本征 T1，保存外推失败原因。
分析命令完成与科学质量分开：`scientific_quality`、逐点掩码、拒绝原因及 `linear_fit_available` 才表达结果是否可用；
流程节点“已完成”不意味着拟合全部合格。诊断仍允许导出。

单点保留 `PDB_T1_decay`、`T1_fit_residual`；扫描保留 `T1_vs_probe_power`、
`inv_T1_vs_probe_power`、`T1_all_decay_curves`，条件不满足时只生成适用图。
全部使用 [公共 paper 科研绘图规范](../plotting_style.md) 的英文标注、PNG/PDF；衰减概览最多三列，保留全部功率点。

历史兼容基于已验证结构而非“没有版本号就猜新版”：支持旧配置单点、带波形扫描和只有 T1 序列的旧扫描；
旧扫描缺波形时沿用已保存 T1/误差，明确标注未重拟合，不虚构衰减图或应用拟合界限。
已验证的早期单点无配置时按特定 NPZ 键只读识别；未记录的仪器条件不是实测回读。
同原始格式不因参数 schema 升级失去兼容。重算归档受影响阶段及后继，保留仍在使用的前置；
原始数据、配置或产物指纹变化后不复用过期结果，旧运行没有阶段记录时从 B01 重建。

## 常见问题与验证边界

- 无触发：检查 CH4 接的是同步输出、边沿及 TTL 阈值；超时后拒绝旧帧，不自动换通道或设备。
- 衰减振荡：方波陡沿可能激发横向分量；当前程序未提供缓变沿，需另行验证方案。
- T1 不可靠：降低探测电压、检查波片和背景，保证足够衰减覆盖；不要把失败点插值补成有效值。
- 时间轴变化：逐帧保留并拒绝平均，核对实际采样和触发设置后重新准备。
- 采集后不能只重跑 A07：X 场已关闭，请明确重跑 A04 及其后继。

自动验收使用假仪器及真实临时写文件，覆盖六步时序、慢磁盘、取消、恢复/保存失败和部分数据抢救，
以及真实会话子进程、历史分析互斥、前端路由和旧格式离线分析。未连接真实硬件；
接线、同步输出相位与真实触发时刻仍需操作者在正式实验前核对。
