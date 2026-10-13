---
title: Bell Bloom Z 磁场频率标定
experiment_id: bell-bloom-z-field-calibration
type: Bell_Bloom_Z_Field_Calibration
scan_mode: nested_scan
defaults_file: params/experiments/bell-bloom-z-field-calibration.yaml
description: 扫描 Z DC 与 Pump 门控及 锁相放大器 解调频率，采集 R 并离线拟合共振中心，得到带符号 Hz/V。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
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
    role: fixed_preserved
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
---

# Bell Bloom Z 磁场频率标定

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

设备地址和通道由当前映射解析：Pump 载波为 100 MHz，采用 Gated/External Burst；`Time_sequence` 的 0–5 V 脉冲接载波所在信号源的外部门控输入。锁相放大器 使用 Input1、Demod0、Osc0、基波解调，保留原相位。Z 通道使用 DC；X/Y 归零关闭，未使用的第二时序通道保持当前状态。这里不使用 Y RF 驱动、Z RAMP 或外部 DAQ 触发。

## 采集流程

1. 无硬件副作用地预检参数、映射和所需设备类型。建立共享设备会话；连接失败清理已建立连接。
2. 同步参考时钟并回读验证；配置固定主场、光功率、载波门控和 锁相放大器。保存实际采样率、锁相放大器 配置、映射与设备库快照。
3. TEC 可用时设置温度并等待稳定；连接失败时记录外部控温来源，跳过设温和稳定等待。已连接 TEC 的温稳超时仍停止实验。
4. 每个 Z 点设置并验证 DC 模式和输出开关，不额外插入 Z 专用等待。
5. 每个频点更新门控频率、脉宽及 锁相放大器 频率，回读拒绝仪器钳位或频率不一致。100 MHz 载波不在循环中重新配置。
6. 将温控切为 0 V DC + ON，执行关闭后等待和频点稳定等待，按 锁相放大器 实际采样率采集 R；立即保存完整记录。`finally` 恢复温控至 5 V DC + ON，并执行恢复等待。
7. R 标准差超过阈值时重采，保留所有尝试；达到次数上限仍不合格即停止，不用坏点继续拟合。DAQ 通信失败直接停止。
8. 每完成一条 Z 频扫保存汇总并更新完整曲线索引。进度显示已完成频点数及由实际耗时估算的 ETA。

正常结束、取消和配置/采集异常均调用公共安全恢复：Z、X/Y 归零关闭，未使用时序保持当前状态，温控恢复 5 V ON，Pump 门控与 锁相放大器 一起恢复本次零偏参考频率并保持占空比；保留主场、光功率与载波门控策略，仅断开 TEC。各恢复动作独立记录错误。恢复失败明确标记，不报告实验成功。

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

## GUI 使用

实验中心的“Bell Bloom Z 磁场频率标定”提供两个主 Tab：

- **运行**：编辑/保存参数和布局、仅预检、启动、取消、查看日志与 ETA；右侧显示最近保存运行的状态、带误差的 Hz/V、截距、R²、标定曲线，可展开各频扫及残差。
- **历史**：按完整开始时间倒序列出记录，支持分页。默认选中最新运行；查看参数快照、图像与结果文件，填回参数或重新分析。切换 Tab 保留未保存参数和活动任务。

重新进入页面从磁盘恢复最近结果；最新运行失败或尚未分析时显示该次状态与原因，不用更早结果替代。重新分析完成会刷新摘要与图像。分析执行完成和标定质量通过分开展示。历史只读取本实验的新格式，不迁移 Mx 或静磁场灵敏度数据。本实验没有架构 Tab。
