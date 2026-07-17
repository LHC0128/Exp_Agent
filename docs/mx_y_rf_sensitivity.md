---
title: Mx Y 向 RF 场灵敏度
type: Mx_Y_RF_Sensitivity
scan_mode: point_by_point
defaults:
  LINEWIDTH_MODE: amplitude_equivalent
  Y_RF_FREQUENCY_HZ: 10000.0
  Y_RF_AMP_START_VPP: -0.2
  Y_RF_AMP_STOP_VPP: 0.2
  Y_RF_AMP_POINTS: 81
  Y_RF_NT_PER_VPP: 0.0
mapping_keys:
  main_magnetic_field:
    role: fixed
  rf_coil:
    role: scan
  X_magnetic_field:
    role: fixed
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Pump_modulation:
    role: fixed
  Time_sequence:
    role: fixed
  lockin_r:
    role: detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
  - TEC103
learned_notes:
  - 主场与 Pump 光沿 Z，Probe 光沿 X，待测 RF 场沿 Y。
  - Time_sequence 在所有结束路径都保持 5 V DC 和输出开启。
  - 采集与灵敏度分析只使用 Demod0 R，不采集 X/Y，也不执行相位校准。
---

# Mx Y 向 RF 场灵敏度

## 构型与信号链

- 主磁场沿 Z，Pump 光沿 Z，Probe 光沿 X。
- rf_coil 产生 Y 向 RF 场；X_magnetic_field 始终归零并关闭。
- Pump AOM 使用 100 MHz、0.18 Vpp；Time_sequence 保持既有 50 Ω 设置，固定为 5 V DC 且输出开启，使 Pump 光常开。
- HF2 Demod0 使用 Input 1、AC 耦合、50 Ω、2 V 量程，并以 Y RF 频率直接解调；DAQ 只订阅 R。
- 参考时钟遵循 params/clock_sources.yaml：Y RF/X 场 DG4000 使用外部 10 MHz，Pump DG4000 使用内部时钟，HF2 使用外部 10 MHz。实验在任何输出配置前设置并回读验证，任一设备不一致即停止。

## 采集顺序

1. 模式 1 先扫描 8–12 kHz 并对 R 拟合带基线 Lorentzian；门控不合格时停止后续采集。
2. 扫描 -0.2 至 +0.2 Vpp；负幅度通过硬件幅度取绝对值并增加 180° 相位实现。
3. 每个幅度点和模式 1 频率点都要求 std(R) 不超过 0.01 V；超限时完整保存该次原始 R 并重新采集，最多 3 次，连续失败则停止实验。
4. 先关闭 Y RF 输出，再将通道切换为 0 V DC 并再次确认 `Output OFF`，然后采集 10 段 1 s 的 R 时间序列用于 PSD。每段噪声原始数据都记录 `y_rf_dc_v=0.0` 和 `y_rf_output_on=0`。

每个扫频点、幅度点和 PSD 段都单独关闭温控开关进行采集，采集后恢复 5 V DC 和输出开启。

## 两种线宽

- frequency_sweep：由 R-Lorentzian 的 gamma 得到真实扫频 HWHM。
- amplitude_equivalent：由 R 的绝对值色散幅度响应拟合得到 gamma_Vpp。仅当 Y_RF_NT_PER_VPP > 0 时才换算为 Rabi/幅度等效 HWHM；该值不是扫频共振线宽。

幅度响应主拟合为

\[
R(V)=|A(V-V_0)/((V-V_0)^2+\gamma^2)+C|.
\]

拟合前按 std(R)>0.01 V 剔除旧数据中的坏点；新数据在采集阶段已通过相同门控重采。主斜率取 |A|/gamma²，同时在中心 ±0.25gamma 内对 R 与 |V-V0| 做线性拟合诊断；两者相差超过 20% 时给出警告。

## 灵敏度结果

主结果由零 Y RF 时的 R PSD 除以绝对值色散拟合得到的斜率幅值。若有有效 HWHM，则进行 Lorentzian 频率响应校正，并取 3 Hz 至 HWHM 内校正后灵敏度的中位数。

当 `Y_RF_NT_PER_VPP > 0` 时生成 `results/full_analysis.png`。图形版式参照静磁场灵敏度实验的 `full_analysiswithoutpump.png`：上半部分绘制 Y RF 场幅度响应及绝对值色散拟合，下半部分绘制 `fT/√Hz` 原始与线宽校正灵敏度谱，并标出平坦频段、中位数和 HWHM。分析器不再生成 `sensitivity_voltage.png`；电压等效灵敏度仍保存在 `results/sensitivity.npz`，用于数值追溯。

线圈标定系数为 0 时仍保存 `Vpp/√Hz` 数值数组，但不生成磁场灵敏度图；模式 2 同时不报告等效 HWHM 和频段中位数。

原始数据位于 data/Mx_Y_RF_Sensitivity/<run>/raw/，结果位于同一运行目录的 results/。采集和离线分析入口分别为 experiments/Mx_Y_RF_Sensitivity.py 与 experiments/Mx_Y_RF_Sensitivity_plot.py。

## 安全例外

正常、取消和异常路径都关闭并归零 Y RF，保持 X 场关闭并恢复温控 5 V。Pump 100 MHz 输出和 Time_sequence=5 V DC + ON 是本实验的显式安全例外，所有路径均保留；正常结束只断开 TEC。
