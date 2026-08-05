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
  Z_magnetic_field:
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
learned_notes:
  - TEC103 为可选控制设备；COM3 被外部温控软件占用时跳过设温和稳定等待。
  - 主场与 Pump 光沿 Z，Probe 光沿 X，待测 RF 场沿 Y。
  - 初始化先将 Z 辅助场归零并关闭，避免继承上一实验的残余输出。
  - Time_sequence 在所有结束路径都保持 5 V DC 和输出开启。
  - 采集与灵敏度分析只使用 Demod0 R，不采集 X/Y，也不执行相位校准。
  - 保留全局色散拟合导数法，并额外报告零点附近实测数据斜率法。
---

# Mx Y 向 RF 场灵敏度

## 构型与信号链

- 主磁场沿 Z，Pump 光沿 Z，Probe 光沿 X。
- rf_coil 产生 Y 向 RF 场；X_magnetic_field 和 Z_magnetic_field 始终归零并关闭。
- Pump AOM 使用 100 MHz、0.18 Vpp；Time_sequence 保持既有 50 Ω 设置，固定为 5 V DC 且输出开启，使 Pump 光常开。
- HF2 Demod0 使用 Input 1、AC 耦合、50 Ω、2 V 量程，并以 Y RF 频率直接解调；DAQ 只订阅 R。
- 参考时钟遵循 params/clock_sources.yaml：Y RF/X 场 DG4000 使用外部 10 MHz，Pump DG4000 使用内部时钟，HF2 使用外部 10 MHz。实验在任何输出配置前设置并回读验证，任一设备不一致即停止。

## 采集顺序

1. 模式 1 先扫描 8–12 kHz 并对 R 拟合带基线 Lorentzian；门控不合格时停止后续采集。
2. 扫描 -0.2 至 +0.2 Vpp；负幅度通过硬件幅度取绝对值并增加 180° 相位实现。
3. 每个幅度点和模式 1 频率点都要求 std(R) 不超过 0.01 V；超限时完整保存该次原始 R 并重新采集，最多 3 次，连续失败则停止实验。
4. 先关闭 Y RF 输出，再将通道切换为 0 V DC 并再次确认 `Output OFF`，然后采集 10 段 1 s 的 R 时间序列用于 PSD。每段噪声原始数据都记录 `y_rf_dc_v=0.0` 和 `y_rf_output_on=0`。

每个扫频点、幅度点和 PSD 段都按“设置 Y RF → 关闭温控开关 → 等待稳定 → 采集 → 恢复温控开关为 5 V DC + ON → 等待”的顺序执行。重采和异常路径也会恢复温控开关。

## 两种线宽

- frequency_sweep：由 R-Lorentzian 的 gamma 得到真实扫频 HWHM。
- amplitude_equivalent：由 R 的绝对值色散幅度响应拟合得到 gamma_Vpp。仅当 Y_RF_NT_PER_VPP > 0 时才换算为 Rabi/幅度等效 HWHM；该值不是扫频共振线宽。

幅度响应主拟合为

\[
R(V)=|A(V-V_0)/((V-V_0)^2+\gamma^2)+C|.
\]

拟合前按 std(R)>0.01 V 剔除旧数据中的坏点；新数据在采集阶段已通过相同门控重采。原有主斜率取 |A|/gamma²，同时在中心 ±0.25gamma 内对 R 与 |V-V0| 做线性拟合诊断；两者相差超过 20% 时给出警告。

分析器还并行计算 `adaptive_zero_point_absolute_linear` 方法：全局色散拟合只负责确定零点 V0，斜率不再取拟合曲线导数，而是在零点左右各选至少 2 个最近实测点，再按距离补足至少 5 点，对实测 R 与 |V-V0| 进行线性回归。局部回归的点数、左右点数、窗口、R² 和失败原因均写入结果；R² 低于全局拟合参考门槛时给出警告，但不会单独否决该方法。若全局零点不可信、零点任一侧点数不足、局部斜率不是正有限值或平坦段识别失败，则该方法无效。

## 灵敏度结果

主结果由零 Y RF 时的 R PSD 除以绝对值色散拟合得到的斜率幅值。若有有效 HWHM，则进行 Lorentzian 频率响应校正。单值灵敏度不再使用固定的 3 Hz 至 HWHM 频段，而是在校正后灵敏度谱上自动识别连续平坦段，再对该区间内未平滑的原始频谱值取中位数。`LOW_FREQ_SKIP_HZ` 只定义自动搜索允许使用的最低候选频率，不再直接作为平坦段下边界。

原方法继续写入 `sensitivity.npz` 和 `full_analysis.png`。零点实测数据斜率法独立写入 `sensitivity_zero_point.npz` 和 `full_analysis_zero_point.png`，并在 `analysis.yaml/json` 的 `zero_point_method` 下报告斜率、灵敏度单值、平坦段和有效性。两种方法共用同一份零 RF 噪声 PSD、HWHM 和平坦段识别算法，区别仅在响应斜率来源。

自动识别先对灵敏度取对数并做分箱中位数，以降低窄带尖峰的影响，再用带低频和高频非负铰链的鲁棒平台模型确定上下边界。分析器同时改变分箱数量和观察范围生成多组候选；只有候选中位数稳健 CV 不超过 3%、P10–P90 跨度不超过 8%、平台整体漂移不超过 20%、相对 MAD 不超过 15%，且平台后的高频上升证据不少于 3σ 时才报告单值。任一条件不满足时不回退到固定频段，而是在 `warnings` 中说明失败原因。检测边界、稳定性指标和判定结果写入 `analysis.yaml/json` 的 `flat_detection`，并以扁平字段写入 `sensitivity.npz`。

当 `Y_RF_NT_PER_VPP > 0` 时生成 `results/full_analysis.png`。图形使用项目统一的默认 `paper` 配置：上半部分绘制 Y RF 场幅度响应及绝对值色散拟合，下半部分绘制 `fT/√Hz` 原始与线宽校正灵敏度谱，并标出平坦频段、HWHM，以及以 flat 区间中位数绘制的实际灵敏度虚线；不再显示固定参考线。该中位数同时写入 `analysis.yaml/json` 和 `sensitivity.npz`。分析器不再生成 `sensitivity_voltage.png`；电压等效灵敏度仍保存在 `results/sensitivity.npz`，用于数值追溯。

线圈标定系数为 0 时仍保存 `Vpp/√Hz` 数值数组，但不生成磁场灵敏度图；模式 2 同时不报告等效 HWHM 和频段中位数。

原始数据位于 data/Mx_Y_RF_Sensitivity/<run>/raw/，结果位于同一运行目录的 results/。采集和离线分析入口分别为 experiments/Mx_Y_RF_Sensitivity.py 与 experiments/Mx_Y_RF_Sensitivity_plot.py。

## 安全例外

正常、取消和异常路径都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并恢复温控 5 V。Pump 100 MHz 输出和 Time_sequence=5 V DC + ON 是本实验的显式安全例外，所有路径均保留；正常结束只断开 TEC。
