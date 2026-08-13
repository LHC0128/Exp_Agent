---
title: Mx Z 最优控制 RF 灵敏度
type: Mx_Z_Optimal_Control_RF_Sensitivity
scan_mode: point_by_point
defaults:
  CONTROL_VERSION: v2
  Z_CALIBRATION_SOURCE_RUN: 0730_170249_mx_z_cal
  CONTROL_SCALE: 1.0
  Z_AW_OUTPUT_VPP: 6.0
  Z_AW_OUTPUT_OFFSET: 0.0
  Y_RF_FREQUENCY_HZ: 30000.0
  Y_RF_AMP_START_VPP: -0.1
  Y_RF_AMP_STOP_VPP: 0.1
  Y_RF_AMP_POINTS: 41
  PHASE_CAL_RF_AMPLITUDE_VPP: 0.01
  PHASE_OUTLIER_SIGMA_THRESHOLD: 6.0
  PHASE_OUTLIER_MAX_REACQUIRE_POINTS: 4
  Y_RF_NT_PER_VPP: 1517.79147
  FIXED_PARAMS.main_magnetic_field: 0.01
  FIXED_PARAMS.X_magnetic_field: 0.01
  FIXED_PARAMS.Y_magnetic_field: 0.01
mapping_keys:
  main_magnetic_field:
    role: configured_current_and_restored
  Z_magnetic_field:
    role: optimal_control_preserved_on_exit
  Time_sequence_2:
    role: common_trigger
  rf_coil:
    role: scanned_detection_drive_with_y_dc_compensation
  X_magnetic_field:
    role: fixed_x_dc_compensation
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: detection_rxy_phase_calibration_and_r_measurement
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
learned_notes:
  - GS200 主场配置为 0 mA 时归零关闭，非零时设定电流并开启；所有结束路径恢复完整运行前状态。
  - Z 控制只在开始时触发一次；校相和正式幅度扫描只重新触发 Y RF。
  - Y RF 非零校相同步采集 Demod0 R/X/Y，按 φ 与 φ+180° 紧邻配对，并丢弃首个 dummy 点。
  - X 剩磁补偿由 X_magnetic_field 输出固定 DC；Y 剩磁补偿作为 rf_coil 波形的 DC offset。
  - 校相 Y RF 幅度为 0 时 RFY 切换为纯 Y 补偿 DC，改为逐点重新触发并扫描 Z 控制相位。
  - 剩磁响应模式不要求相位拟合通过，扫描后不执行 RF 幅度和噪声采集。
  - 剩磁响应模式在所有退出路径恢复 CONTROL_BURST_PHASE_DEG。
  - 正常结束、取消或异常时均保持 Z 控制波形和输出状态不变，不归零、不关闭。
  - Z 控制和 Y RF 的 DG4000 Burst 均使用外部下降沿触发，与 Keithley 6221 Trigger Link 输入保持同沿。
  - 共同触发实体接线由操作者核对，软件只验证仪器配置。
  - 控制换算只使用 Z 标定斜率，不使用约 90 kHz 截距。
---

# Mx Z 最优控制 RF 灵敏度

## 实验构型

稳定实验 ID 为 `mx-z-optimal-control-rf-sensitivity`。GUI 参数
`FIXED_PARAMS.main_magnetic_field` 配置 GS200 主场电流：`0 mA` 时
写入零电流并关闭输出，非零时写入设定电流并开启输出。实验同时在
`Z_magnetic_field` 上施加周期最优控制；Y RF 由
`rf_coil` 产生，HF2 Demod0 的振荡器频率始终等于固定 Y RF 频率。
Y RF 校相同步采集 Demod0 R/X/Y；正式幅度响应、RF-off 噪声以及
`YRF=0` 的 Z 控制剩磁扫描仍采集 Demod0 R。

XY 剩磁场补偿沿用 Mx 主磁场示波器噪声谱实验的固定参数键：
`FIXED_PARAMS.X_magnetic_field` 由 `X_magnetic_field` 通道直接输出 DC；
`FIXED_PARAMS.Y_magnetic_field` 不占用额外通道，而是作为 `rf_coil`
正弦/Burst 的 DC offset。程序在每次输出前分别校验 X DC、Y offset
以及 Y 的完整电压包络 `offset ± Vpp/2`。

参数 schema 为 v3。读取 v1/v2 历史配置时，如果缺少上述两个键，迁移
逻辑会补为 X=`0 V`、Y=`0 V`，从而保持旧实验的横向场关闭行为；只有
新版默认配置明确启用补偿值。

最优控制按 `CONTROL_VERSION=vN` 从以下结构读取：

```text
D:\Code\theory_agent\simulate\results\oc_sens\vN\
  waveforms\optimal_control_waveform.csv
  parameters\optimal_control_params.csv
```

Z 标定从
`data/Mx_Z_Field_Calibration/<Z_CALIBRATION_SOURCE_RUN>/results/analysis.yaml`
读取。控制电压只按

\[
V_Z(t)=\mathrm{CONTROL\_SCALE}
\frac{\Omega_\mathrm{ctrl}(t)}{K_Z}
\]

换算；标定截距仅保存用于追溯。程序保留波形原始 DC 分量。DG4000 的
Vpp 和 offset 由 GUI 参数 `Z_AW_OUTPUT_VPP`、`Z_AW_OUTPUT_OFFSET`
固定给定，并按 DirectAW 方式计算上传数组：

\[
u(t)=\frac{V_Z(t)-\mathrm{Z\_AW\_OUTPUT\_OFFSET}}
{\mathrm{Z\_AW\_OUTPUT\_VPP}/2}.
\]

若任一点的 \(|u(t)|>1\)，或 GUI 给定的完整输出范围超出 Z 通道安全
限值，预检直接失败，不裁剪目标波形。默认使用 `6 Vpp / 0 V offset`。

## 共同触发与两种相位模式

`Time_sequence_2`（Z 控制 DG4000 CH2）输出 100 Hz、5 Vpp、
2.5 V offset、50% duty 方波。该信号必须经分配后同时接入 Z 控制
DG4000 和 Y RF DG4000 的 Ext Trig；两台仪器使用下降沿外触发
`Burst INFinity`，从而与 Keithley 6221 Trigger Link 的下降沿输入保持同沿。
软件不能判断实体 BNC 线是否接通，运行前必须人工核对。

当 `PHASE_CAL_RF_AMPLITUDE_VPP > 0` 时执行 RF 灵敏度模式。Z 控制被
触发后连续运行，校相保持 Z 控制相位和 Output 不变，只扫描 Y RF
Burst 相位。相位轴必须完整覆盖一个 360° 周期且不重复首尾点；默认
为 0–350°、步进 10°。实际采集顺序重排为
`0°, 180°, 10°, 190°, ...`，每点设置相位后对 Y RF 执行
Output OFF→ON。正式记录前先采集并丢弃一个首相位 dummy 点。每个相位
独立关闭温控、稳定 0.1 s、同步采集 0.2 s R/X/Y，并按
\(\sqrt{\sigma_X^2+\sigma_Y^2}\) 门槛重采。

首轮完整相位扫描结束后，程序用
\(Z(\phi)=c_0+c_1\cos\phi+c_2\sin\phi\) 拟合整条复数相位曲线，
再对复残差使用 MAD 稳健门槛识别“点内噪声很小、但稳定触发到错误
状态”的异常点。默认门槛为 6 个稳健标准差，且同时不低于点内复噪声
中位数的 10 倍。若首轮异常点不超过 4 个，程序只执行一轮定点修正，
每个异常相位重新采集一次并替换进入最终拟合；若重采后仍有异常，或
首轮异常点超过上限，则保留全部诊断数据并拒绝校相结果。这里的一轮
跨相位重采与单点内部的复噪声质量重试是两套独立机制。

当 `PHASE_CAL_RF_AMPLITUDE_VPP = 0` 时执行剩磁响应模式。程序不向
RFY 下发零幅正弦波，而是将其配置为
`FIXED_PARAMS.Y_magnetic_field` 对应的纯 DC 输出，并在每个采集点再次
确认该 Y 补偿场。相位轴改为 Z 控制 Burst 相位；
每点对 Z 控制执行 Output OFF→设置相位→Output ON，使其等待下一次
共同触发。完成、取消或异常退出时，Z 控制均恢复至 GUI 参数
`CONTROL_BURST_PHASE_DEG`，波形和 Output 保持开启。

Y RF 非零校相把 Demod0 输出写成复数 \(Z=X+iY\)。对每一对相差
180° 的相位计算

\[
Z_0=\left\langle\frac{Z(\phi)+Z(\phi+180^\circ)}{2}\right\rangle,
\qquad
\Delta Z(\phi)=\frac{Z(\phi)-Z(\phi+180^\circ)}{2}.
\]

再将差分旋转到平均基线方向：

\[
W(\phi)=\Delta Z(\phi)e^{-i\arg Z_0}.
\]

同相分量 \(P=\operatorname{Re}W\) 使用
\(P(\phi)=a\cos\phi+b\sin\phi\) 拟合；选择 \(P\) 正最大的位置作为
建设性 Y RF 相位。正交分量 \(Q=\operatorname{Im}W\) 同时拟合并保存，
用于诊断所选相位处的残余失配。由于计算发生在带符号 X/Y 上，即使
RF 响应幅度大于基线也不会发生 R 的绝对值折叠。正式正交模型要求
R²≥0.85、协方差有限且同相幅度超过 3σ；否则保留校相数据、拟合结果和
`results/phase_calibration.png` 后停止实验。该诊断图的上方面板同时绘制
Demod0 R/X/Y 的均值与误差条，下方面板绘制成对同相/正交响应及其拟合。

`YRF=0` 的剩磁扫描继续使用
\(R(\phi)=|C+A\sin(\phi-\phi_0)|\) 和
\(C+A|\sin(\phi-\phi_0)|\) 诊断，不以拟合是否通过作为成功条件：
弱响应或平坦响应仍标记为正常完成，并保存完整数据、拒绝原因和诊断图。

## 响应与灵敏度

本节只适用于 `PHASE_CAL_RF_AMPLITUDE_VPP > 0`。正式测量固定
Y RF/HF2 频率，只扫描带符号 Y RF 幅度。正幅度使用
校相结果，负幅度增加 180°；零点切换为纯 Y 补偿 DC，不向仪器设置
`0 Vpp` 正弦，下一个非零点完整恢复带偏置的外触发 Burst 正弦。每个幅度点都重新触发
Y RF，Z 控制始终连续运行。

色散、零点实测斜率、Welch PSD、幅度等效 HWHM、线宽校正和平坦频段
检测复用 Mx Y RF 灵敏度分析。噪声采集使用 5 段、每段 1 s 的
RF-off Demod0 R；Y RF 交流分量关闭，但 Y 补偿 DC 保持输出，Z 控制保持开启。

采集与分析入口分别为：

```text
experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py
experiments/Mx_Z_Optimal_Control_RF_Sensitivity_plot.py
```

每次运行保存：

```text
data/Mx_Z_Optimal_Control_RF_Sensitivity/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    phase_scan.npz              # 非零校相含 R/X/Y、成对复差分和投影
    phase_dummy_attempt_*.npz   # 非零校相首个丢弃点
    phase_*_cross_retry_attempt_*.npz  # 跨相位异常点的单轮定点重采
    amplitude_scan.npz
    noise_*.npz
  results/
    phase_calibration.yaml
    phase_calibration.png
    analysis.yaml
    analysis.json
    full_analysis.png
    full_analysis_zero_point.png
```

剩磁响应模式只保存 `phase_scan.npz`、`phase_calibration.yaml`、
`phase_calibration.png`、`analysis.yaml` 和 `analysis.json`，不会生成
`amplitude_scan.npz`、`noise_*.npz` 或 RF 灵敏度分析图。

## 安全结束状态

正常、取消和异常路径均保持 `Z_magnetic_field` 当前最优控制波形和
Output 开启，不发送归零或关闭命令。RF 灵敏度模式保持原 Burst 相位；
剩磁响应模式恢复 `CONTROL_BURST_PHASE_DEG`。共同触发、Y RF 与 X 场
仍关闭归零，温控恢复为 5 V DC + Output ON，GS200 恢复运行前源模式、
电流、量程、限流和输出状态。Pump/Probe、Pump 载波/门控和 HF2 设置
保留，正常结束只断开 TEC。
