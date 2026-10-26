---
title: RF 频率响应（DirectAW）
type: RF_Field_Sensitivity_AW_FreqSweep_DirectAW
experiment_id: rf-sensitivity-direct-aw-frequency
execution_mode: typed_workflow
scan_mode: nested_scan
defaults_file: params/experiments/rf-sensitivity-direct-aw-frequency.yaml
description: 固定 Z 射频幅度，扫描频率与源相位；X/Y 由正交 DirectAW 输出。完整与分步运行共用真实业务步骤，GUI 提供运行／历史、节点编辑和独立目录分析；保留物理回环、恢复温控后保存及异常数据抢救。
mapping_keys:
  main_magnetic_field: {role: fixed}
  Pump_laser_power: {role: fixed}
  Probe_laser_power: {role: fixed}
  X_magnetic_field: {role: fixed}
  Y_magnetic_field: {role: fixed}
  X_magnetic_field_AM: {role: trigger}
  Y_magnetic_field_AM: {role: trigger}
  Z_magnetic_field: {role: scan}
  Pump_modulation: {role: fixed}
  Time_sequence: {role: fixed}
  Time_sequence_2: {role: idle}
  Temp_Switch: {role: fixed}
  temperature: {role: fixed}
  lockin_r: {role: detection}
required_devices: [GS200, signal_generator, lockin_amplifier]
learned_notes:
  - Z RF 使用连续正弦，Burst OFF；源相位通过 set_phase_adjust 修改，不使用外触发。
  - Demod0 Y 通过 AuxOut2 和物理电缆回到 Signal Input2，再送入 Demod3；不使用 adcselect=2 内部路由。
  - DirectAW 低 R 样本只保留诊断，不用于相位更新；收敛状态与步骤执行完成分开判断。
  - 保留原始频率／相位矩阵和逐频点格式，额外抢救文件不自动混入正常分析。
---

# RF 频率响应（DirectAW）

## 目的与参数来源

保持 Z RF 幅度不变，外层扫描 Z 驱动频率，内层扫描 Z 正弦源相位。X/Y 使用
完整周期的正交 DirectAW 控制场；主分析为跨相位的 R 中位数幅频曲线及相位诊断。
本入口不执行幅度色散标定或独立噪声灵敏度测量；这些能力见 [RF 灵敏度实验](rf_field_measurement.md)。

稳定 ID、GUI/YAML 参数键和 `RF_Field_Sensitivity_AW_FreqSweep_DirectAW` 数据目录保持不变。
当前默认值只读取 [参数文件](../../params/experiments/rf-sensitivity-direct-aw-frequency.yaml)，
历史运行以本轮 `experiment_config.yaml` 为准，不用当前默认值补写历史。

## 设备与接线

设备型号、资源与实际端点由当前 `params/devices.yaml` 和 `params/mapping.yaml` 解析，
下表的 CH1/CH2 是工作流逻辑通道，不是固定物理资源。

| 逻辑设备／信号 | 用途 |
| --- | --- |
| `dg_comp` CH1/CH2 | X/Y 正交任意波，INFinity Burst + 外部触发 |
| `dg_am` CH1/CH2 | 连续同相方波，连接 DirectAW 的外触发输入 |
| `dg_sweep` CH1 | Z RF 连续正弦，Burst 与调制关闭；0 Hz 点关闭 Z 输出 |
| `dg_sweep` CH2 | `Time_sequence_2`，本实验不以它产生 RF Burst |
| `dg_mod` CH1/CH2 | Pump AOM 载波与脉冲门控，CH2 SYNC 关闭 |
| GS200、`dg_laser` | 主磁场与 Pump/Probe DC 光功率 |
| `dg_temp`、TEC | 温控门控与可选目标温度下发 |
| 锁相放大器 | PD → 主输入 → Demod0 Y → AuxOut2 → 物理线 → Signal Input2 DC → Demod3 |

双主电压物理回环当前只支持 HF2。全局选用 MFLI 时，连接前拒绝启动；不会自动换输入、
降频或回退设备。连接后输入统一高阻、DC 耦合。Aux 输出复用公共限值检查，缺少明确上下限禁止写入。
参考时钟按设备库配置同步并严格回读。可选示波器连接失败不终止实验。

## 真实模块与可选择流程

业务位于 `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/`。
`workflow.py` 只编排；`preparation.py` 负责连接、初始条件和配置快照，`excitation.py`
负责波形与触发，`phase.py` 负责校相，`acquisition.py` 负责扫描和保存，
`analysis_steps.py` 保留原科学计算及绘图。完整运行和分步运行调用相同函数。

| 节点 | 实际业务与依赖 |
| --- | --- |
| A01 | 无硬件参数、映射、波形和安全预检 |
| A02 | 依赖 A01；资源复用连接、参考时钟同步与严格回读 |
| A03 | 依赖 A02；主场、光功率、TEC 目标与 Pump 初态 |
| A04 | 依赖 A03；Demod0 校相、物理回环配置与回读 |
| A05 | 依赖 A04；DirectAW 合成、触发与整体校相，切测量解调模式 |
| A06 | 依赖 A05；频率 × 源相位扫描，逐频点保存矩阵与索引 |
| A07 | 无前置；安全结束会话 |
| B01 | 独立核对运行身份与频率／相位矩阵，保存输入指纹清单 |
| B02 | 依赖 B01；原 R 中位数、均值、旧最大值与相干分量分析及科研绘图 |

开启校相时保留原低 R 判据、更新规则和收敛诊断；未收敛不被伪装成科学合格。
前置缺失会拒绝所选步骤，不偷偷补选；重跑前置使其后继失效。

## GUI：运行／历史

本实验清单显式声明 `run_step_id=A06`、`finish_step_id=A07`，不按编号或位置猜测角色。旧服务缺字段时阻止新分步执行，仍可恢复任务、取消和安全结束；“执行完成”不代表科学质量合格，诊断提示不新增硬件阻断。产物只按执行记录展示，不补造链接或回写历史元数据。共享参数失效、多参数合并及历史草稿隔离规则见 [GUI 分步契约](../../GUI/README.md#分步流程契约与兼容)。

从 `/experiments/rf-sensitivity-direct-aw-frequency` 进入运行页。顶部的保存参数与布局、
派生预览、预检、完整运行和任务状态位于流程容器；参数在实际使用节点内按基础／高级编辑。
`SYSTEM_SETTLE_S` 等共用参数跨节点同步，初始化条件只在设置它的节点编辑。
草稿显示预计失效范围；应用只更新会话参数，必须显式选择重跑，不自动设置输出。

分步等待仍保持设备连接、当前输出和全局硬件锁，不能当作实验结束。完成 A06 后点击
“结束会话”，或在执行中取消；正常、异常、取消和 stdin EOF 均走同一安全收尾。
采集中目录拒绝独立分析。每次显式重跑扫描创建独立目录，先前原始文件与参数快照保留。

分析列可以选择下一次实验或具体历史目录。原算法没有额外可覆盖的分析参数，目录分析只读取
该目录保存的配置。B01/B02 的修订、输入和产物指纹控制前置有效性；重算只归档受影响产物，
B02 重算不移动仍在使用的 B01 输入清单。历史页可浏览参数／结果、填回参数、明确目录重新分析；
填回和浏览都不自动运行、不保存默认值。流程源图可下载并在 Excalidraw 中编辑。

## 采集时序、故障和安全收尾

配置实际仪器 → 温控开关 `0 V DC + ON` → `SYSTEM_SETTLE_S` → 采集到内存 →
温控开关 `5 V DC + ON` → 保存。保留原频率／相位循环边界和温控恢复等待。
TEC 可连接时只写 UI 目标温度，不启停、不判稳；未知实测温度为 `null`。

正常文件格式不变；逐相位的已读样本另保存在 `raw/recovery/freq_NNNN_phase_NNN.npz`。
后续读取、恢复、取消或写盘失败时停止后继，先尝试恢复再抢救已有样本；抢救文件不自动参与正常分析。
二次收尾／配置保存错误不覆盖原始异常，收尾失败不记为完成。

结束恢复温控，X/Y DirectAW、Z RF 与 X/Y 外触发归零关闭；主场、Pump/Probe 光功率、
Pump 调制、`Time_sequence`／`Time_sequence_2` 当前状态及全部锁相设置保留，只断开 TEC。
连接阶段失败释放已建立连接。详细门控约定见 [统一采集规范](../acquisition_timing.md)。

## 数据与离线分析

```text
data/RF_Field_Sensitivity_AW_FreqSweep_DirectAW/<时间戳_tag>/
  experiment_config.yaml
  acquisition_state.json
  raw/
    xy_aw_waveforms_initial.npz
    xy_aw_waveforms.npz
    freq_*.npz
    frequency_index.json
    frequency_index.npz
    freq_phase_scan.npz
    recovery/
  results/
    input_manifest.json
    analysis.yaml
    analysis.json
    freq_response_amplitude.png
    freq_response_phase.png
    freq_phase_heatmap.png
    phase_scan_cv.png
  analysis_state.json
```

`freq_phase_scan.npz` 保留 `freq_Hz`、`phase_deg`、`r_matrix_V`、`x_matrix_V`、
`y_matrix_V`、`theta_matrix_deg`。逐频点文件仍沿用原最后相位时域／样本字段；完整的各相位
矩阵保持原含义，抢救副本不改变这些历史字段。

`ACQUISITION_MODE` 保留 `yfft`、`demod_rxy`、`both`。DAQ 结果按最终实际采样率记录；
当前原幅频分析要求存在有效 R 矩阵，纯时域模式而无有效 R 时明确拒绝，不伪造幅频曲线。
全 NaN 频率被原算法排除。相干一阶分量仍排除重复的 0°／360° 相位点；主方法仍是 `r_median`，
旧 `R_max` 只作诊断，-3 dB 带宽定义不变。

采集薄入口：`experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py`；
分析薄入口：`experiments/RF_Field_Sensitivity_AW_FreqSweep_plot.py`。
离线模块显式接收运行目录，不回退 `latest`，不依赖当前工作目录，也不改写历史 raw。

`experiments/RF_Field_Sensitivity_AW_FreqSweep_compare.py` 继续读取多个明确运行的
`results/analysis.json` 比较幅频响应，不连接仪器；比较对象与输出由该脚本参数区指定。
外部 AM 方案已归档，见 [归档说明](../../old/experiments/README.md)；
恒定 XY 方案见 [专属说明](rf_sensitivity_constxy_frequency.md)，不要混用三者的接线与分析。
