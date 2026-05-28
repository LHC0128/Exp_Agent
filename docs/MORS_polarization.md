---
title: 自旋极化度标定 (MORS 谱法)
type: experiment_type
description: CSS 态连续泵浦下，Y 方向 RF 线圈在 Ω_L 附近扫频激发横向自旋相干，LIA 以固定 Ω_L 参考解调得 MORS 频谱，多峰非线性拟合得各 Zeeman 子能级布居 ρ_{m,m}，计算极化度 P = (1/F) Σ m·ρ_{m,m}
keywords: [MORS, polarization, spin, RF spectroscopy, Zeeman, nonlinear fit, CSS, P, density matrix]
version: 2
geometry:
  main_field: Z
  light_propagation: X
  rf_coil: Y

scan_mode: continuous_ramp      # RF 信号源在 Ω_L 附近扫频，LIA 逐点记录

# ========== 默认参数 ==========
defaults:
  # ---- Pump 光 ----
  PUMP_POWER: 0.1               # Pump 光功率 (V)，连续开启维持 CSS
  # ---- Probe 光 ----
  PROBE_POWER: 0.1              # Probe 光功率 (V)，Bell-Bloom CW 常开
  # ---- 主磁场 ----
  MAIN_FIELD_mA: 9.305          # 正常工作主磁场 (mA)，Ω_L/2π ≈ 90 kHz
  # ---- LIA 解调参数 ----
  HF2_DEMOD_IDX: 1              # 解调器索引
  HF2_OSC_FREQ: 90000           # 振荡器频率 (Hz)，固定为 Ω_L
  HF2_SIGNAL_RANGE: 2.0         # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4            # 解调滤波器阶数
  HF2_DEMOD_TC: 0.001           # 解调时间常数 (s)，需兼顾 SNR 与扫频速度
  HF2_DEMOD_RATE: 10000         # 解调输出数据速率 (Sa/s)
  # ---- RF 扫频参数 ----
  RF_FREQ_START: 60000          # RF 起始频率 (Hz)，Ω_L/2π - 30 kHz
  RF_FREQ_STOP: 120000          # RF 终止频率 (Hz)，Ω_L/2π + 30 kHz
  RF_AMPLITUDE: 0.01            # RF 幅度 (V)，弱驱动避免功率展宽
  RF_SWEEP_POINTS: 600          # 扫频点数
  RF_DWELL_TIME: 0.05           # 每频点驻留时间 (s)，需 > HF2_DEMOD_TC × 5
  # ---- 温度控制 ----
  TEC_TEMPERATURE: 85.0         # 气室温度 (°C)

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200               # 主磁场（正常值）
    role: main_field
  - instrument: signal_generator    # Probe 光功率 (DG912 Pro CH2)，CW 常开
    role: laser_probe
    channels: [2]
  - instrument: signal_generator    # Pump 光功率 (DG912 Pro CH1)，连续开启
    role: laser_pump
    channels: [1]
  - instrument: signal_generator    # RF 线圈驱动（Y 方向，垂直于 B_z 和光传播 X），连续弱正弦波扫频
    role: rf_coil
    mapping_key: rf_coil
    channels: [2]
  - instrument: lockin_amplifier    # 锁相放大器（固定 Ω_L 参考，DAQ 采集幅度或 X/Y）
    role: detection
    demod_channels: 1
    has_daq: true
  - instrument: tec_controller      # 温控器（TEC103）
    role: temperature

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  main_magnetic_field:
    role: fixed
    description: "主磁场保持正常值，Ω_L 不变"
  Probe_laser_power:
    role: fixed
    description: "Probe 光功率，Bell-Bloom CW 常开，与正式实验一致"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率，连续开启维持 CSS 极化"
  rf_coil_sweep:
    role: scan
    description: "RF 线圈驱动信号，正弦波，频率在 Ω_L/2π ± 30 kHz 范围内扫描，幅度弱驱动"
  lockin_amplitude:
    role: detection
    description: "LIA 固定 Ω_L 参考解调，输出幅度 R 或 X/Y 随 RF 频率变化 = MORS 谱"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"

# ========== 固定参数 ==========
fixed_params:
  - main_magnetic_field
  - Probe_laser_power
  - Pump_laser_power
  - temperature

# ========== 修复经验 ==========
learned_notes:
  - RF 功率必须足够小（弱驱动），确认工作在线性响应区间（峰高正比于 RF 功率、线宽不变），否则功率展宽会破坏谱形
  - LIA 参考频率固定为 Ω_L，RF 信号源在 Ω_L 附近扫频。LIA 解调输出的是 RF 响应与原子的差频信号幅度
  - 低极化度（~74%）时 MORS 谱多峰分立，需用完整多峰公式 (3.18) 拟合；高极化度（~99%）时近单峰，可用简化 Lorentzian 拟合
  - 即使 5% 的非极化也可能使 CSS 噪声相对 PNL 增加十几个百分点，每次压缩度评估前应重测 P
  - 每次测量前需确认 LIA 解调相位已对准自旋进动方向
  - Bell-Bloom CW 探测模式下 MORS 谱测量流程与频闪 QND 模式完全一致，Probe 保持 CW 常开
  - RF 线圈与 Y_magnetic_field 共用同一物理通道 (DG4E234902522 CH2)，代码中使用 mapping_key `rf_coil` 以语义区分
  - 若使用多台信号发生器（RF 线圈独立于 Pump/Probe），注意共地避免地回路噪声耦合至 LIA
---

> **坐标系**：主磁场 $B$ 沿 **Z**，光沿 **X** 传播，RF 线圈沿 **Y**（垂直于二者，激发横向自旋）。

# 自旋极化度标定 (MORS 谱法)

## 原理

通过射频线圈施加 Larmor 频率附近的**连续微弱横向 RF 磁场**，激发相邻 Zeeman 子能级间的横向自旋相干。二阶 Zeeman 效应导致不同相邻能级对的能级差略有不同，各横向自旋相干以略有差别的频率进动，在 MORS 信号中表现为可分辨的频谱分量。

### MORS 谱拟合公式

$$
MORS(\omega) \propto \sum_{m=-F}^{F-1} \frac{[F(F-1) - m(m-1)] \cdot [\rho_{m+1,m+1} - \rho_{m,m}]}{i[\omega_c + \omega_s(m+\frac{1}{2}) - \omega] - \frac{\Gamma}{2}} \tag{3.18}
$$

- $\omega_c$：中心频率
- $\omega_s$：相邻跃迁频差（二阶 Zeeman 分裂）
- $\Gamma$：线宽
- $\rho_{m,m}$：各 Zeeman 子能级布居（拟合目标）

### 极化度计算

从拟合得到的 $\rho_{m,m}$ 计算极化度：

$$
\boxed{P = \frac{1}{F} \sum_m m \rho_{m,m}} \quad (F=2) \tag{3.19}
$$

### 低极化 vs 高极化 MORS 谱特征

| 极化度 | 谱特征 | 时域特征 | 拟合策略 |
|--------|--------|---------|---------|
| 低（~74%） | 多峰分立、各峰幅度不均 | FID 信号出现震荡 | 多峰拟合反推各 $\rho_{m,m}$ |
| 高（~99%） | 近单峰、对称 Lorentzian | FID 基本指数衰减 | 单峰拟合，半高全宽法即可 |

## 实验配置

```
信号源(RF, Y方向) → RF线圈 → 横向RF磁场施加于铷泡（⊥ B_z, ⊥ 光 X）
                          ↓
探测光(X) → 铷泡 → 平衡探测器 → LIA → 频谱采集
                ↑
泵浦光（圆偏振，正常开启）
   主磁场 B_z (Z方向)
```

| 光路/线圈 | 状态 | 说明 |
|------|------|------|
| 泵浦光 (Pump) | **开启**，圆偏振 | 连续泵浦维持 CSS 极化 |
| 探测光 (Probe) | **CW 常开** | Bell-Bloom 连续探测，与正式实验一致 |
| RF 线圈 | 连续弱正弦波扫频 | **沿 Y 方向**，频率扫过 Ω_L ± 30 kHz |
| 主磁场 $B_z$ | 正常值 (9.305 mA) | Ω_L 不变，沿 Z 方向 |
| LIA | 参考频率固定 Ω_L | 解调输出幅度随 RF 频率变化 |

### LIA 模式说明

LIA 参考频率设定为 $\Omega_L$（固定），RF 信号源在 $\Omega_L$ 附近扫频。LIA 解调后输出的是 RF 响应经原子在 $\Omega_L$ 处的差频信号的幅度，扫频即得 MORS 谱。

若 LIA 支持外部参考跟踪，也可让 LIA 参考始终与 RF 扫频同频——此时输出为 DC 附近的 Lorentzian。

## 实验步骤

1. 开启泵浦光，制备 CSS（保持连续泵浦）
2. 开启探测光，Bell-Bloom CW 常开模式
3. **确认 RF 线性响应区**：
   - 固定一个 RF 频率，改变 RF 幅度（3~5 个梯度），记录 LIA 输出幅度
   - 确认输出正比于 RF 幅度（线性区），线宽不随 RF 功率变化
4. **频谱扫描**：
   - RF 信号源输出连续弱正弦波，频率从 $\Omega_L/2\pi - 30$ kHz 扫到 $\Omega_L/2\pi + 30$ kHz
   - 每频点驻留时间 $\geq 5 \times \tau_{LIA}$（确保 LIA 稳定）
   - 记录 LIA 输出的幅度-频率曲线 = MORS 谱
5. 重复采集 ≥ 3 次取平均，提高信噪比

## 数据分析

### 多峰拟合（低极化）

对 $F=2$ 的 $^{87}\mathrm{Rb}$，$m$ 从 $-2$ 到 $+1$（5 个可能的跃迁），用公式 (3.18) 做非线性最小二乘拟合：

拟合参数：
- 各能级布居 $\rho_{-2,-2}, \rho_{-1,-1}, \rho_{0,0}, \rho_{1,1}, \rho_{2,2}$（归一化约束 $\sum \rho_{m,m} = 1$）
- 中心频率 $\omega_c$
- 二阶 Zeeman 分裂 $\omega_s$
- 线宽 $\Gamma$

### 极化度验证

#### 极化度不足的影响

- 完美极化 $(P=1)$ 的 CSS：$\mathrm{Var}(J_z) = N_A$
- 即使 **5% 的非极化**，CSS 噪声也可能相对 PNL **增加十几个百分点**
- **每次压缩度评估前**都应通过 MORS 测量当前极化度，以此修正理论 PNL

#### 标定 $\mathcal{G}$ 因子

结合 3.4.3 节 Faraday 旋光 $\theta_F$ 和本节极化度 $P$：

$$
\mathcal{G} = \frac{|\theta_F(0)|}{N_A F \cdot P}
$$

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| MORS 谱峰宽于预期 | RF 功率过大导致功率展宽 | 降低 RF 幅度，确认工作在线性区 |
| 多峰无法分辨 | 二阶 Zeeman 分裂过小或磁场不均匀 | 检查主磁场均匀性 |
| 拟合不收敛 | 初值不当 | 用峰值位置估算 $\omega_c$、相邻峰间距估算 $\omega_s$ |
| 极化度小于预期 | Pump 光功率不足或偏振不纯 | 检查 Pump 光圆偏振度、AOM 耦合效率 |
| LIA 输出有偏置 | 解调相位未对准或存在 DC 泄漏 | 校准 LIA 相位，检查输入 AC 耦合 |
