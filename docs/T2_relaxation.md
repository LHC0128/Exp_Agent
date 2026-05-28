---
title: 横向弛豫时间 T₂ 标定 (FID 时域法 + MORS 频域法)
type: experiment_type
description: 两种独立方法标定横向弛豫时间 T₂：FID 时域法（短 RF 脉冲激发横向自旋后采集 LIA 解调包络，指数衰减拟合）和 MORS 频域法（MORS 谱共振峰 Lorentzian 拟合得 FWHM，反推 T₂ = 1/(π·FWHM)）。Bell-Bloom 下 FWHM 可与 PSD 洛伦兹拟合结果交叉验证
keywords: [T2, transverse relaxation, FID, free induction decay, MORS, Lorentzian, FWHM, RF pulse, exponential fit, Bell-Bloom]
version: 1

scan_mode: single_point         # FID 单次采集 / MORS 频域依赖 MORS_polarization 扫频数据

# ========== 默认参数 ==========
defaults:
  # ---- Pump 光 ----
  PUMP_POWER: 0.1               # Pump 光功率 (V)，制备 CSS
  # ---- Probe 光 ----
  PROBE_POWER: 0.1              # Probe 光功率 (V)，Bell-Bloom CW 常开
  # ---- 主磁场 ----
  MAIN_FIELD_mA: 9.305          # 正常工作主磁场 (mA)，Ω_L/2π ≈ 90 kHz
  # ---- LIA 解调参数 ----
  HF2_DEMOD_IDX: 1              # 解调器索引
  HF2_OSC_FREQ: 90000           # 振荡器频率 (Hz)，固定为 Ω_L
  HF2_SIGNAL_RANGE: 2.0         # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4            # 解调滤波器阶数
  HF2_DEMOD_TC: 7.85e-07        # 解调时间常数 (s)，高带宽以捕捉指数衰减
  HF2_DEMOD_RATE: 100000        # 解调输出数据速率 (Sa/s)
  # ---- FID RF 脉冲参数 ----
  RF_PULSE_FREQ: 90000          # RF 脉冲频率 (Hz)，等于 Ω_L/2π
  RF_PULSE_AMPLITUDE: 0.1       # RF 脉冲幅度 (V)，对应 π/2 或小角度脉冲
  RF_PULSE_WIDTH: 5.0e-6        # RF 脉冲宽度 (s)，≪ T₂
  # ---- 采集参数 ----
  ACQ_DURATION: 0.05            # FID 采集时长 (s)，≥ 5×T₂
  ACQ_REPEATS: 20               # 重复次数
  # ---- 温度控制 ----
  TEC_TEMPERATURE: 85.0         # 气室温度 (°C)

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200               # 主磁场（正常值）
    role: main_field
  - instrument: signal_generator    # Probe 光功率 (DG912 Pro CH2)，CW 常开
    role: laser_probe
    channels: [2]
  - instrument: signal_generator    # Pump 光功率 (DG912 Pro CH1)，制备 CSS
    role: laser_pump
    channels: [1]
  - instrument: signal_generator    # RF 脉冲信号源（FID 激发），短脉冲输出
    role: rf_coil
    channels: [1]
  - instrument: lockin_amplifier    # 锁相放大器（固定 Ω_L 参考，DAQ 采集解调后 X 路包络）
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
    description: "Probe 光功率，Bell-Bloom CW 常开"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率，开启制备 CSS（FID 测量中 Pump 可开可关，需注明条件）"
  rf_pulse:
    role: trigger
    description: "RF 脉冲信号，频率 = Ω_L/2π，短脉宽 (≪ T₂)，激发横向自旋后立即关断"
  lockin_x:
    role: detection
    description: "LIA 解调后 X 路输出，记录 FID 指数衰减包络"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"

# ========== 固定参数 ==========
fixed_params:
  - main_magnetic_field
  - Probe_laser_power
  - temperature

# ========== 修复经验 ==========
learned_notes:
  - RF 线圈与 Y_magnetic_field 共用同一物理通道 (DG4E234902522 CH2)，代码中使用 mapping_key `rf_coil` 以语义区分
  - RF 脉冲宽度必须 ≪ T₂（T₂=3 ms 时脉宽 < 300 μs），推荐 ≤ 10 μs，否则脉宽展宽带来系统误差
  - RF 脉冲关断后须确认无残余 RF 泄漏，否则泄漏信号混入 LIA 输出破坏指数衰减包络
  - Pump 光在 FID 中可开可关，但两种条件得到的 T₂ 不同（开泵浦时自旋交换展宽增加），必须注明条件
  - 低极化度（~74%）时 FID 时域信号出现多频震荡（多个频率分量叠加），需用多频阻尼振荡模型拟合
  - 高极化度（~99%）时 FID 基本符合单指数衰减，拟合更可靠
  - 原始 PD 信号（解调前）为 e^{-t/T₂}·cos(Ω_L t + φ)，LIA 解调后振荡项被去除仅剩包络，直接采集 LIA 输出拟合即可
  - MORS 频域法依赖 MORS_polarization.md 的 MORS 谱数据，RF 驱动功率必须足够低（弱驱动），否则功率展宽使 FWHM 偏大、T₂ 被低估
  - Bell-Bloom 模式下 T₂ 标定流程与频闪 QND 模式完全一致，Probe 保持 CW 常开
  - Bell-Bloom 特有验证：PSD 洛伦兹拟合得到的 FWHM 应与 FID/MORS 法独立标定的 T₂ 一致（差异 < 20%），详见 Projection_noise.md
---

> **坐标系**：主磁场 $B$ 沿 **Z**，光沿 **X** 传播，RF 线圈沿 **Y**（垂直于二者，激发横向自旋）。

# 横向弛豫时间 T₂ 标定

## 方法一：FID 时域法

### 原理

CSS 态下施加短 RF 脉冲激发横向自旋，关断 RF 后自旋自由进动并指数衰减。LIA 解调去除载波，输出为 DC 附近的指数衰减包络：

$$
\boxed{X(t) \propto e^{-t/T_2}} \tag{3.20}
$$

> 原始 PD 信号（解调前）为 $\propto e^{-t/T_2} \cos(\Omega_L t + \phi_0)$，LIA 解调后振荡项被去除，仅剩包络。直接采集 LIA 输出拟合指数衰减即可。

### 时序示意

```
泵浦光:  ████████████████████████████（可关可不关，需注明条件）
RF脉冲:  ________________|██|_____________________
探测光:  ████████████████████████████████████████████（CW 持续）
采集:    ___________________|→ FID 信号采集 →.......
```

### 实验步骤

1. 开启泵浦光，制备 CSS（保持直到极化饱和）
2. 探测光保持 CW 常开（Bell-Bloom 模式）
3. **施加短 RF 脉冲**：信号源输出频率 $=\Omega_L/2\pi$ 的短脉冲
   - 脉宽 $\ll T_2$（如 5 μs，对应 T₂≈3 ms）
   - 脉冲角度：$\pi/2$ 或小角度脉冲
4. **立即关闭 RF**，同时开始采集 LIA 解调后的 X 路输出
5. 采集时长至少 $5\times T_2$，确保包络衰减至基线
6. 重复 ≥ 20 次取平均，提高信噪比

### 数据分析

对 LIA 解调后的 X(t) 包络做指数衰减拟合：

$$
X(t) = A e^{-t/T_2} + X_{\text{DC}}
$$

- $X_{\text{DC}}$：LIA 的 DC 偏置（热态残余）
- **低极化度（~74%）**：多频率分量导致震荡，需用多频阻尼振荡模型：
  $$
  X(t) = \sum_k A_k e^{-t/T_{2,k}} \cos(\omega_k t + \phi_k) + X_{\text{DC}}
  $$
- **高极化度（~99%）**：基本符合单指数衰减，拟合更可靠

---

## 方法二：MORS 频域法

### 原理

连续弱 RF 驱动下，MORS 谱共振峰的半高全宽 (FWHM) 由 $T_2$ 决定。从 MORS 谱中提取 $\Gamma$ 后：

$$
\boxed{T_2 = \frac{1}{\pi \Gamma}} \tag{3.21}
$$

> 使用 Hz 单位时：$T_2 = \dfrac{1}{\pi \cdot \mathrm{FWHM(Hz)}}$

### 实验步骤

1. 按 `MORS_polarization.md` 流程采集完整 MORS 谱
2. 选其中一个共振峰，用 Lorentzian 拟合：

   $$
   L(\omega) = \frac{A \Gamma/2\pi}{(\omega - \omega_0)^2 + (\Gamma/2)^2}
   $$

3. 提取半高全宽 $\Gamma$（FWHM，单位 rad/s）
4. 代入公式 (3.21) 计算 $T_2$

### 注意事项

- RF 驱动功率必须足够低（弱驱动），否则功率展宽使 $\Gamma$ 偏大，$T_2$ 被低估
- 高极化度（~99%）下共振峰近单 Lorentzian，拟合精度高
- 低极化度下多峰可能互相重叠，需谨慎区分各峰的 FWHM

---

## 两种方法比较

| 方法 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| FID 时域法 | 直观、物理图像清晰 | 需精确 RF 脉冲时序 | 高极化度、单分量衰减 |
| MORS 频域法 | 可同时获得极化度、不依赖时序 | 多峰拟合复杂、对磁场均匀性敏感 | 全极化度范围、参数综合标定 |

## Bell-Bloom 特有交叉验证

Bell-Bloom 磁力仪下 PSD 洛伦兹拟合的 FWHM 也给出 $T_2^{\text{PSD}}$。将此值与本节独立标定的 $T_2^{\text{FID}}$ 或 $T_2^{\text{MORS}}$ 交叉比对：

| 验证项 | 通过标准 | 参考 |
|--------|---------|------|
| $T_2^{\text{PSD}}$ vs $T_2^{\text{FID}}$ | 差异 < 20% | `Projection_noise.md` PSD 洛伦兹检验 |
| $T_2^{\text{FID}}$ vs $T_2^{\text{MORS}}$ | 差异 < 20% | 方法间交叉验证 |

三者一致则确认 PSD 中低频分量为纯净自旋投影噪声贡献。

## 实验注意事项

- 两种方法可在不同探测光功率与泵浦条件下交叉比较
- 时域法要求 RF 脉冲宽度 $\ll T_2$，且关断后无残余 RF 泄漏
- 频域法中 RF 驱动功率必须足够低（弱驱动），否则功率展宽会使 $\Gamma$ 偏大，$T_2$ 被低估
- FID 中 Pump 光开/关两种条件得到的 $T_2$ 不同（开泵浦时自旋交换碰撞增加展宽），必须注明条件

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| FID 包络有震荡 | 低极化度多频分量叠加 | 使用多频阻尼振荡模型拟合 |
| FID 衰减偏离指数 | RF 脉宽不满足 $\ll T_2$ 或残余 RF 泄漏 | 缩短脉宽，检查 RF 开关隔离度 |
| MORS FWHM 偏大 | RF 功率过大导致功率展宽 | 降低 RF 幅度，验证线性区 |
| MORS 拟合 T₂ 与 FID T₂ 不一致 | 两种方法对应不同物理条件 | 确认 Pump 条件和 Probe 功率一致 |
| PSD 洛伦兹 T₂ 与其他方法不一致 | 测量线路响应混入 | 检查 LIA LPF 设置，用更高 TC 排除线路展宽 |
