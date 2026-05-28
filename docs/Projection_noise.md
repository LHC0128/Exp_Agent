---
title: 热态投影噪声标定 (PSD 法)
type: experiment_type
description: 采集热态（Pump 关闭）和纯光噪声（主磁场偏置）条件下的 LIA 解调 X/Y 时间序列，通过 PSD 频域分析剔除技术噪声后积分得方差，计算等效耦合强度 κ̃² 并反推投影噪声极限 PNL。支持 PSD 洛伦兹拟合交叉验证 T₂
keywords: [projection noise, PNL, thermal state, PSD, Welch, kappa_tilde, shot noise, lock-in, F=1 correction, T2 cross-validation]
version: 2
geometry:
  main_field: Z
  light_propagation: X

scan_mode: point_by_point      # 两阶段采集：纯光噪声 → 热态噪声

# ========== 默认参数 ==========
defaults:
  # ---- 探测光 ----
  PROBE_POWER: 0.1              # 探测光功率 (V)，应使用 Photon_shot_noise 标定得到的工作功率
  # ---- 主磁场 ----
  MAIN_FIELD_NORMAL_mA: 9.305   # 正常工作主磁场 (mA)，Ω_L/2π ≈ 90 kHz
  MAIN_FIELD_OFFSET_mA: 5.0     # 偏置主磁场 (mA)，使 Ω_L 移出 LIA 带宽
  # ---- 采集参数 ----
  ACQ_DURATION: 5.0             # DAQ 单次采集时长 (s)
  ACQ_REPEATS: 20               # 每阶段重复采集次数
  NPERSEG: 10000                # Welch PSD 每段点数
  # ---- HF2 DAQ 参数 ----
  HF2_DEMOD_IDX: 0              # 解调器索引
  HF2_OSC_FREQ: 90000           # 振荡器频率 (Hz)
  HF2_SIGNAL_RANGE: 2.0         # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4            # 解调滤波器阶数
  HF2_DEMOD_TC: 7.85e-07        # 解调时间常数 (s)，高带宽噪声采集
  HF2_DEMOD_RATE: 100000        # 解调输出速率 (Sa/s)
  # ---- PSD 积分参数 ----
  PSD_INTEG_FMIN: 10            # PSD 积分下限 (Hz)，排除 DC 附近漂移
  PSD_INTEG_FMAX: 2000          # PSD 积分上限 (Hz)，LIA LPF 带宽
  # ---- 技术噪声剔除 ----
  NOTCH_FREQS: [50, 100, 150]   # 需剔除的工频谐波 (Hz)
  NOTCH_WIDTH: 2                # 剔除窗口半宽 (Hz)
  # ---- F=1 修正因子 ----
  F1_CORRECTION: 0.833          # 5/6，远失谐等耦合近似；有 repump 时设为 1.0
  PNL_THERMAL_RATIO: 0.8        # 0.8 = 4/5，PNL/热态方差比

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200
    role: main_field
  - instrument: signal_generator
    role: laser_probe
    mapping_key: Probe_laser_power
    channels: [2]
  - instrument: signal_generator
    role: laser_pump
    mapping_key: Pump_laser_power
    channels: [1]
  - instrument: signal_generator
    role: temp_switch
    mapping_key: Temp_Switch
    channels: [2]
  - instrument: tec_controller
    role: temperature
  - instrument: lockin_amplifier
    role: detection
    demod_channels: 2
    has_daq: true

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  main_magnetic_field:
    role: scan
    description: "主磁场在两值间切换：OFFSET 值 (5 mA) 测纯光噪声，NORMAL 值 (9.305 mA) 测热态噪声"
  Probe_laser_power:
    role: fixed
    description: "探测光功率，固定为工作值（需先经 Photon_shot_noise 标定确认散粒噪声主导）"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率，全程关闭 (0 V)"
  lockin_xy:
    role: detection
    description: "HF2 DAQ 采集解调后 X/Y 时间序列，X 为主通道、Y 为验证通道"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，采集时关闭以消除温控 PWM 磁场干扰"

# ========== 固定参数 ==========
fixed_params:
  - Probe_laser_power
  - Pump_laser_power
  - temperature
  - Temp_Switch

# ========== 修复经验 ==========
learned_notes:
  - 采集前必须确认 LIA 解调相位已对准自旋进动方向（ϕ₀ 使 X 路信号最大化）
  - PSD 上 50 Hz 及其谐波的尖峰必须在积分前剔除，否则会高估 PNL
  - X 和 Y 的 PSD 在积分带宽内应基本平坦（白噪声），若出现明显滚降说明 LPF 带宽不足
  - 热态 X/Y 方差应大致相等（各向同性），差异 > 20% 需检查解调相位或偏振
  - F=1 到 F=2 的贡献不能在 PSD 上通过频率分离，须用理论耦合系数修正（因子 5/6）
  - 有 repump 时将 F1_CORRECTION 设为 1.0，无需理论修正
  - 温度开关在 DAQ 采集期间必须关闭，采集完成后立即恢复
---

# 热态投影噪声标定 (PSD 法)

## 原理

### 为什么要用 PSD 方法

LIA 输出的时域方差包含所有频率成分的技术噪声（50 Hz 工频、谐波、EMI 等），这些经典噪声若被计入"量子噪声基准"，会导致 PNL 被高估。

**推荐流程**：

```
X(t), Y(t) 时间序列 → Welch PSD → 剔除技术噪声尖峰 → 积分得 Var(X)
```

| | 直接用 X(t) 求方差 | 先 FFT 求 PSD 再积分 |
|---|---|---|
| 能否剔除技术噪声 | 不能 | **能** — 频谱上尖峰一目了然 |
| 能否验证白噪声假设 | 不能 | **能** — 检查 PSD 是否平坦 |
| 适用场景 | 快速估算 | **标定 PNL / κ̃²（推荐）** |

数学上二者等价（Parseval 定理）：

$$
\mathrm{Var}(X) = \int_{0}^{BW} \mathrm{PSD}(f) \, df
$$

### 两阶段测量

| 阶段 | 主磁场 | Pump 光 | 测量量 | 物理含义 |
|------|--------|---------|--------|---------|
| Phase 1 | **偏置** (5 mA，Ω_L 移出带宽) | OFF | $\mathrm{Var}(X^{\text{light}})$ | 纯光散粒噪声 + 电噪声 |
| Phase 2 | **正常** (9.305 mA，Ω_L 在带宽内) | OFF | $\mathrm{Var}(X^{\text{thermal}})$ | 光噪声 + 热态原子自旋噪声 |

### 核心公式

**等效耦合强度** $\tilde{\kappa}^2$：

$$
\boxed{\tilde{\kappa}^2 =
\frac{\mathrm{Var}(X^{\text{thermal}}) - \mathrm{Var}(X^{\text{light}})}
{\mathrm{Var}(X^{\text{light}})}
\times \underbrace{0.8}_{4/5}
\times \underbrace{\frac{5}{6}}_{F=2\text{ 占比}}
} \tag{3.8'}
$$

- 因子 **0.8**：$\mathrm{Var}(J_z^{PNL}) / \mathrm{Var}(J_z^{\text{thermal}}) = 4/5$
- 因子 **5/6**：远失谐等耦合近似下 $F{=}2$ 在总原子噪声中的占比；有 repump 时改为 1

**投影噪声极限**（归一化到光噪声）：

$$
\mathrm{Var}(X^{PNL}) = \mathrm{Var}(X^{\text{light}}) \times \left(1 + \tilde{\kappa}^2 \times \frac{6}{5}\right)
$$

## 实验配置

| 光路 | Phase 1 (纯光噪声) | Phase 2 (热态噪声) |
|------|:---:|:---:|
| 泵浦光 (Pump) | OFF | OFF |
| 探测光 (Probe) | 正常工作功率 | 同左 |
| 主磁场 $B_x$ | **偏置** (5 mA) | **正常** (9.305 mA) |
| 锁相放大器 | 正常设置 | 同左 |

## 实验步骤

### Phase 1：纯光噪声测量

1. Pump 光关闭（确保 AOM 关断隔离度足够）
2. 主磁场设为偏置值（如 5 mA），使 $\Omega_L$ 偏离 LIA 参考频率 90 kHz 几十 kHz
3. 关闭温度开关，等待 0.1 s
4. HF2 DAQ 采集 X、Y 时间序列（$N_{\text{repeat}} = 20$ 次，每次 5 s）
5. 恢复温度开关

### Phase 2：热态噪声测量

1. Pump 光保持关闭
2. 主磁场**恢复**到正常值（9.305 mA）
3. 等待原子回到热平衡（≥ 100 ms，确保 $T_1 \sim 30$ ms 弛豫完成）
4. 关闭温度开关，等待 0.1 s
5. HF2 DAQ 采集 X、Y 时间序列（$N_{\text{repeat}} = 20$ 次，每次 5 s）
6. 恢复温度开关

## 数据采集参数

| 参数 | 建议值 | 说明 |
|------|--------|------|
| 采集通道 | X 和 Y 双通道 | X 为主通道，Y 用于验证噪声各向同性 |
| 采样率 | ≥ 50 kSa/s | 用 100 kSa/s，确保 ~2 kHz 带宽 Nyquist |
| 单次采集时长 | 5 s | 兼顾统计精度和低频漂移 |
| 重复次数 | ≥ 20 次 | 确保方差/PSD 统计稳定 |
| Welch 段长 | 10000 点 | 频率分辨率 ~10 Hz @ 100 kSa/s |
| PSD 积分范围 | 10~2000 Hz | 排除 DC 漂移和超出 LPF 带宽的高频 |

## 数据分析流程

### Step 1：Welch PSD 计算

对每段 X(t)、Y(t) 分别做 Welch 周期图：

```
S_xx(f), S_yy(f) = welch(x, fs=rate, nperseg=NPERSEG)
```

### Step 2：技术噪声剔除

在 PSD 上标记并剔除已知技术噪声频率（50 Hz 工频谐波等）：

```python
mask = np.ones_like(freqs, dtype=bool)
for f0 in NOTCH_FREQS:
    mask[(freqs > f0 - width) & (freqs < f0 + width)] = False
S_clean = S[mask]
```

### Step 3：积分得方差

在剔除后的频段 [fmin, fmax] 内对 PSD 积分：

$$
\mathrm{Var} = \int_{f_{\min}}^{f_{\max}} S(f) \, df \approx \sum_i S(f_i) \cdot \Delta f
$$

### Step 4：计算 $\tilde{\kappa}^2$ 和 PNL

代入公式 (3.8')。

## 热态对称性检查

采集完成后的自动验证项：

| 检查项 | 判据 | 不通过时 |
|--------|------|---------|
| $\langle X \rangle \approx 0, \langle Y \rangle \approx 0$ | $|mean| < \sqrt{\mathrm{Var}}/\sqrt{N}$ | 检查 DC 偏置 |
| $\mathrm{Var}(X) \approx \mathrm{Var}(Y)$ | $|\mathrm{Var}_X - \mathrm{Var}_Y| / \mathrm{Var}_X < 0.2$ | 检查解调相位对准 |
| PSD 平坦性 | 带宽内起伏 $< \pm 3$ dB | 检查 LPF 设置 |
| 无窄带尖峰 | 超出白噪声基线 $> 10$ dB 的峰 | 标记并剔除 |

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| 热态噪声比预期小 | 泵浦光泄漏，原子被部分极化 | 检查 Pump AOM 关断隔离度 |
| 热态噪声比预期大 | 环境磁噪声、地回路 | 查看 PSD 有无技术噪声尖峰，检查接地 |
| 热态 X/Y 方差差异大 | 解调相位未对准 | 做数值正交旋转验证 |
| PSD 高频端滚降 | LIA LPF 带宽不足 | 检查/增大 DEMOD_RATE 或降低 TC |
| $\tilde{\kappa}^2$ 为负 | 热态噪声 < 纯光噪声 | 检查主磁场是否确实恢复了正常值 |
