---
title: Mx Y 向 RF 频率响应
type: Mx_Y_RF_Frequency_Response
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  RF_AMPLITUDE_START_VPP: 0.01
  RF_AMPLITUDE_STOP_VPP: 0.01
  RF_AMPLITUDE_POINTS: 1
  FREQUENCY_START_HZ: 8000.0
  FREQUENCY_STOP_HZ: 12000.0
  FREQUENCY_POINTS: 201
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
  - 工作点与 Mx Y RF 灵敏度实验完全一致：主场与 Pump 光沿 Z，Probe 光沿 X，待测 RF 场沿 Y。
  - 采集只使用 Demod0 R，振荡器跟随 Y RF 频率直接解调；R 是矢量幅度，与解调参考相位无关。
  - RF 幅度轴点数等于 1 且起止相等时只测单幅度（与模式 1 一致）；否则嵌套扫描多个幅度。
  - 弱驱动 S≤1 为单峰，HWHM=gamma；强驱动 S>1 为 Rabi 劈裂双峰，峰位 f0±gamma*sqrt(S-1)。
---

# Mx Y 向 RF 频率响应

测量 Mx 工作点下 Y 向 RF 场的频率响应曲线 R(f)。采集方式参考
`Mx_Y_RF_Sensitivity` 实验的模式 1（固定幅度、步进扫频、HF2 Demod0 直接解调、
std(R) 质量门控、温控开关时序），RF 幅度本身可作为可选的外层扫描轴，
一次运行即可得到弱驱动 Lorentzian 线形到强驱动 Rabi 劈裂双峰的完整演化。

## 构型与信号链

- 主磁场沿 Z，Pump 光沿 Z，Probe 光沿 X，rf_coil 产生 Y 向 RF 场；
  X_magnetic_field 和 Z_magnetic_field 始终归零并关闭。
- Pump AOM 使用 100 MHz、0.18 Vpp；Time_sequence 保持 5 V DC 且输出开启，使 Pump 光常开。
- HF2 Demod0 使用 Input 1、AC 耦合、50 Ω、2 V 量程，振荡器跟随当前 Y RF 频率直接解调；
  DAQ 只订阅 R。
- 参考时钟遵循 `params/devices.yaml` 中当前绑定设备的 `reference_clock`，
  实验在任何输出配置前设置并回读验证，任一设备不一致即停止。

## 理论模型与线形

在旋波近似下，主场 B₀ 沿 Z（Larmor 角频率 ω₀=2πf₀），Y 向 RF 场
B₁cos(ωt)，失谐 Δ=ω₀−ω，驱动 Rabi 角频率 Ω₁=γB₁/2。绕 Z 以 ω 旋转的
坐标系中，布洛赫方程稳态解为

- u = M₀ ΔΩ₁T₂² / (1 + Δ²T₂² + Ω₁²T₁T₂)
- v = M₀ Ω₁T₂ / (1 + Δ²T₂² + Ω₁²T₁T₂)
- w = M₀ (1 + Δ²T₂²) / (1 + Δ²T₂² + Ω₁²T₁T₂)

其中 T₁ 包含光泵浦的贡献，T₂ 为横向弛豫时间。HF2 Demod0 直接以 RF 频率解调
得到的矢量幅度与解调参考相位无关：

\[
R \propto \sqrt{u^2+v^2}
   = \frac{\Omega_1 T_2 M_0 \sqrt{1+\Delta^2 T_2^2}}{1+\Delta^2 T_2^2+\Omega_1^2 T_1 T_2}
\]

令 x=(f−f₀)/γ、γ=1/(2πT₂)（Hz）、S=Ω₁²T₁T₂，得到统一拟合模型

\[
R(f) = C + A\frac{\sqrt{1+x^2}}{1+x^2+S}
\]

两个极限：

- **弱驱动 S→0**：单峰，峰在 f₀，R≈C+A/√(1+x²)，HWHM=γ。该线形在小失谐下与
  Lorentzian 等价（两者在 x≪1 时均为 1−x²/2），远翼比 Lorentzian 衰减更慢。
- **强驱动 S>1**：中心 x=0 为局部极小（谷值 C+A/(1+S)），双峰位于
  f₀±γ√(S−1)，峰高 A/(2√S)，峰谷比 (1+S)/(2√S)；等效 Rabi 频率（Hz）为 γ√S。
  峰间距随驱动幅度近似线性增大，即 Rabi 劈裂。

驱动强度判据：S=(γB₁/2)²T₁T₂>1 时劈裂。由于 S∝B₁²∝Vpp²，多条幅度曲线的
S 对幅度平方应成线性关系，分析器用线性回归诊断并报告斜率
（正比于 (γ·c/2)²T₁T₂，c 为线圈 Vpp→B₁ 的标定系数）。

## 采集顺序

1. 连接设备并配置 Mx 工作点（与 Mx Y RF 灵敏度实验相同）。
2. 嵌套扫描：外层 RF 幅度轴（`RF_AMPLITUDE_START_VPP` 至
   `RF_AMPLITUDE_STOP_VPP`、`RF_AMPLITUDE_POINTS` 点；点数等于 1 时起止必须相等，
   即单幅度），内层频率轴（`FREQUENCY_START_HZ` 至 `FREQUENCY_STOP_HZ`、
   `FREQUENCY_POINTS` 点）。
3. 每个 (幅度, 频率) 点：DG 设置 Y RF 通道频率、相位 0°、幅度并开输出
   （初始正弦由工作点配置建立，之后只用 `set_frequency`/`set_amplitude`，
   不重绘波形），HF2 振荡器跟随频率；然后按“温控关→等待稳定→采集 R→温控开→等待”
   执行，std(R)≤0.01 V 才接受，超限时完整保存该次原始 R 并重新采集，
   最多 3 次，连续失败则停止实验。
4. 汇总保存 `raw/frequency_response.npz`：频率轴、幅度轴、R 均值/标准差矩阵、
   每点接受的 attempt 索引与文件名、实际采样率。
5. 所有路径（正常、取消、异常）都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并
   恢复温控 5 V；Pump 100 MHz 输出和 Time_sequence=5 V DC + ON 是本实验的显式
   安全例外；正常结束只断开 TEC。

## 离线分析

- 每条幅度曲线独立拟合统一布洛赫模型。拟合分两步：先固定 S=0 拟合 4 参数
  sqrt-Lorentzian（弱驱动解），再尝试多组 S 初值做 5 参数拟合；只有 5 参数解
  明确给出 S>1 时才采用强驱动解。S≈0 附近 S 与 γ 存在简并，直接 5 参数拟合
  会滑向 γ 偏小的局部解，因此弱驱动数据一律报告 S=0 的 4 参数解。
- 质量门控沿用 R²≥`FIT_R_SQUARED_MIN`、γ 相对不确定度≤
  `FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX`，并拒绝 γ 不小于扫描跨度一半的解。
- 结果：`results/bloch_fit.npz`（拟合参数矩阵、S、峰位、劈裂标志）、
  `analysis.yaml/json`（每条曲线的 HWHM、中心、S、劈裂判定、等效 Rabi 频率，
  多幅度时的 S–Vpp² 线性诊断）、`frequency_response.png`（各幅度数据点与拟合
  曲线叠加，paper 配置，英文标注）、`s_vs_amplitude.png`（多幅度时）。

原始数据位于 `data/Mx_Y_RF_Frequency_Response/<run>/raw/`，结果位于同一运行目录的
`results/`。采集和离线分析入口分别为 `experiments/Mx_Y_RF_Frequency_Response.py`
与 `experiments/Mx_Y_RF_Frequency_Response_plot.py`。

## 安全例外

正常、取消和异常路径都关闭并归零 Z 辅助场和 Y RF，保持 X 场关闭并恢复温控 5 V。
Pump 100 MHz 输出和 Time_sequence=5 V DC + ON 是本实验的显式安全例外，所有路径均保留；
正常结束只断开 TEC。
