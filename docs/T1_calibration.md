---
title: Faraday 旋光角与纵向弛豫时间 T₁ 标定
type: experiment_type
description: CSS 制备后通过 Bell-Bloom 方法（关断 Pump_modulation 的 Time_sequence 门控）停止泵浦，示波器采集 PD1/PD2 直流光强波形，计算 Faraday 旋光角 θ_F 的指数衰减曲线并拟合得 T₁。支持多探测光功率扫描，线性拟合 T₁⁻¹ vs P_probe 分离本征弛豫率与光致退相干系数
keywords: [T1, longitudinal relaxation, Faraday rotation, spin polarization, PD DC, exponential fit, probe power dependence, Bell-Bloom, oscilloscope]
version: 2
geometry:
  main_field: X
  light_propagation: X

scan_mode: point_by_point      # 多功率扫描（单功率 T₁ 测量时只取一个功率点即可）

# ========== 默认参数 ==========
defaults:
  # ---- Bell-Bloom Pump 控制 ----
  PUMP_MOD_FREQ: 90000         # Pump 调制频率 (Hz)，即 Time_sequence 方波频率
  PUMP_MOD_AMPLITUDE: 0.18     # Pump_modulation 100MHz 载波幅度 (V)
  PUMP_MOD_DUTY: 5.0           # Time_sequence 占空比 (%)
  PUMP_PREP_TIME: 0.5          # Pump 开启时长 (s)，确保极化饱和 (>> T₁)
  PUMP_RAMP_TIME: 1.0e-4       # Pump 关断缓变沿时间常数 (s)，50~100 μs（通过 Pump_modulation 幅度包络实现）
  # ---- Probe 光 ----
  PROBE_POWER: 0.05             # 探测光功率 (V)，弱探测以减小对 T₁ 的干扰
  PROBE_POWER_LIST: [0.01, 0.02, 0.04, 0.06, 0.08, 0.1]  # 多功率扫描用 (V)
  # ---- 主磁场 ----
  MAIN_FIELD_mA: 9.305          # T₁ 测量时磁场沿 X 方向 (mA)，Ω_L/2π ≈ 90 kHz
  # ---- 示波器采集参数 ----
  SCOPE_TIMEBASE: 0.05          # 示波器时基 (s/div)，覆盖 3~5×T₁
  SCOPE_SAMPLE_RATE: 10000      # 示波器采样率 (Sa/s)
  ACQ_DURATION: 0.3             # 单次采集时长 (s)，至少 3~5 倍 T₁（T₁≈30 ms 时取 ~150 ms）
  ACQ_REPEATS: 20               # 每点重复次数（多次测量取平均降低拟合噪声）
  # ---- λ/2 波片 ----
  HALF_WAVE_ANGLE: 0.0          # λ/2 波片补偿角 (°)，CSS 态下使 PD1/PD2 接近平衡
  # ---- 温度控制 ----
  TEC_TEMPERATURE: 85.0         # 气室温度 (°C)

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200               # 主磁场 (正常值，Ω_L 在 LIA 带宽内)
    role: main_field
  - instrument: signal_generator    # Probe 光功率 (DG912 Pro CH2)，CW 常开
    role: laser_probe
    mapping_key: Probe_laser_power
    channels: [2]
  - instrument: signal_generator    # Pump_modulation 100MHz 载波 (DG4000 CH1)，幅度包络控制缓变沿关断
    role: pump_mod_carrier
    mapping_key: Pump_modulation
    channels: [1]
  - instrument: signal_generator    # Time_sequence 门控方波 (DG4000 CH2)，控制 RF 开关通断
    role: pump_mod_gate
    mapping_key: Time_sequence
    channels: [2]
  - instrument: sds_acquisition     # 示波器采集 PD1/PD2 直流电压波形
    role: pd_dc_reader
    mapping_key: scope_waveform
  - instrument: tec_controller      # 温控器（TEC103）
    role: temperature

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  main_magnetic_field:
    role: fixed
    description: "主磁场保持正常值，Ω_L 不变（T₁ 测量不需要偏置磁场）"
  Probe_laser_power:
    role: scan
    description: "探测光功率扫描变量（CW 常开），用于标定 T₁ 对光功率的依赖"
  Pump_modulation:
    role: fixed
    description: "100MHz 载波正弦波，幅度可包络调制以实现缓变沿关断"
  Time_sequence:
    role: fixed
    description: "门控方波 → RF 开关 CTRL，关断即停止 Pump 光"
  scope_waveform:
    role: detection
    description: "示波器采集 PD1(V_x)、PD2(V_y) 直流电压波形，计算 θ_F"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制），温度漂移会导致 T₁₀ 变化"

# ========== 固定参数 ==========
fixed_params:
  - main_magnetic_field
  - temperature

# ========== 修复经验 ==========
learned_notes:
  - Bell-Bloom 模式下 Pump 光通过 Pump_modulation (100MHz) + Time_sequence (门控) + RF 开关 + AOM 实现，关断 Pump = 将 Time_sequence 输出置为 0V DC（RF 开关断开，AOM 无光）
  - 缓变沿关断通过 Pump_modulation 的幅度包络实现：将末尾 50~100 μs 的 100MHz 正弦幅度线性或指数 ramp down，避免方波关断激发横向自旋
  - PD1/PD2 差模输出已接入示波器（SDS），通过 scope_waveform 采集双通道波形。也可用 HF2 AUXIN 采集（备选方案）
  - Probe 光功率尽量低（弱探测），避免 α·P_probe 项主导导致 T₁ 过短拟合不准
  - PD1/PD2 DC 信号采集前，需调节 λ/2 波片使 CSS 态下两路光强尽量接近（DC 差分接近零）
  - 采集时长至少 3~5 倍 T₁，确保指数衰减尾部有足够信噪比
  - 多次测量取平均后再拟合，降低 PD 电噪声对指数拟合的扰动
  - 气室温度漂移会导致 T₁₀ 变化，长时间测量前应重新标定
  - T₁ 测量通过 Faraday 旋光角 θ_F（DC 量）的衰减，而非 LIA 的 AC 解调输出
  - Bell-Bloom CW 探测模式下 T₁ 标定流程与频闪 QND 模式完全一致，探测光保持 CW 常开即可
---

> **坐标系**：主磁场 $B$ 沿 **X**（与探测光同向），光沿 **X** 传播，RF 线圈沿 **Y**。T₁ 测量时磁场沿 X 使得 $\langle J_x \rangle$ 为纵向分量，避免 Larmor 进动。

# Faraday 旋光角与纵向弛豫时间 T₁ 标定

## 原理

### Faraday 旋光角 θ_F 的测量

探测光（线偏振）沿 $x$ 方向穿过铷泡后，原子自旋极化引起的圆双折射使偏振面旋转，旋转角 $\theta_F$ 正比于平均自旋 $\langle J_x \rangle$：

$$
\theta_F(t) = -\mathcal{G} \langle J_x(t) \rangle \tag{3.14}
$$

$\mathcal{G}$ 为由失谐 $\Delta$、原子密度 $\rho$、光路长度 $L$ 等决定的几何因子。

### 纵向弛豫时间 T₁ 的提取

CSS 制备好后关闭泵浦光，仅保留弱探测光，$\langle J_x \rangle$ 随时间指数衰减：

$$
\langle J_x(t) \rangle = \langle J_x(0) \rangle e^{-t/T_1} \tag{3.15}
$$

对应 Faraday 旋光角也指数衰减：

$$
\boxed{\theta_F(t) = \theta_F(0) e^{-t/T_1}} \tag{3.16}
$$

### T₁ 对探测光功率的依赖

改变探测光功率，测量对应的 $T_1$，作 $T_1^{-1}$ vs $P_{\text{probe}}$ 图并线性拟合：

$$
\boxed{T_1^{-1}(P_{\text{probe}}) = T_{1,0}^{-1} + \alpha P_{\text{probe}}} \tag{3.17}
$$

| 参数 | 物理含义 | 来源 |
|------|---------|------|
| $T_{1,0}^{-1}$ | 本征弛豫率（壁碰撞 + 自旋交换碰撞） | 截距 |
| $\alpha$ | 探测光致退相干系数（自发辐射散射） | 斜率 |

## 实验配置

```
Pump_modulation(100MHz CW) ──→ RF Switch IN ──→ RF Switch OUT ──→ AOM ──→ Pump 光
                                      ↑
Time_sequence(90kHz 方波) ────────────┘ (CTRL 门控)

探测光(线偏振, CW) → 铷泡 → λ/2波片 → PBS → PD1(V_x) ──→ 示波器 CH1
                                            → PD2(V_y) ──→ 示波器 CH2
B_x (主磁场, X方向，与探测光同轴)
```

| 光路/信号 | 状态 | 说明 |
|------|------|------|
| Pump 光 | Bell-Bloom 调制，Time_sequence 门控 | 100MHz 载波经 RF 开关 + AOM 耦合入光纤。关断 = Time_sequence 置 0V DC |
| 探测光 (Probe) | **CW 常开**，弱功率 | Bell-Bloom 连续 CW，功率尽量低以减小测量干扰 |
| 主磁场 $B_x$ | 正常值 | Ω_L 不变，沿 X 方向（与探测光同轴） |
| λ/2 波片 | 已补偿 | CSS 态下 PD1/PD2 光强尽量接近 |
| PD1/PD2 → 示波器 | DC 耦合采集 | 示波器双通道同步采集，记录衰减波形 |

### Bell-Bloom Pump 关断方式

与直接控制 DG912 DC 输出不同，Bell-Bloom 模式下 Pump 光的通断由 **Time_sequence** 门控 RF 开关实现：

```
Pump ON:  Time_sequence = 90kHz 方波 (5% 占空比, 5Vpp+2.5V offset) → RF 开关周期性导通 → AOM 有光
Pump OFF: Time_sequence = 0V DC                                      → RF 开关断开 → AOM 无光
```

**缓变沿关断**（推荐）：通过 Pump_modulation (100MHz 载波) 的幅度包络实现。将载波末尾 50~100 μs 的正弦幅度线性或指数 ramp down 至 0，避免 Time_sequence 方波关断的陡峭边沿激发横向自旋分量。

**简化关断**（备选）：直接将 Time_sequence 从方波切换为 0V DC。Bell-Bloom 调制本身已将 Pump 分散为短脉冲序列（占空比 5%），单个脉冲能量有限，对纵向衰减的干扰通常可接受。

## 实验步骤

### 单功率基本 T₁ 测量

**时序设置**：

```
Pump_modulation(100MHz): |████████████████████████|██████|___________（幅度 ramp down）
                          ←── CSS 制备期 ──→      ←缓变沿→
Time_sequence(90kHz方波): |████████████████████████|_______（→ 0V DC）
探测光(CW):               |████████████████████████████████████████████|
示波器采集:               |    ...  制备期  ...   |  t₀  →  t₀ + T_measure  |
```

1. **调节 λ/2 波片**：开启 Pump，制备 CSS，调 λ/2 波片角度使示波器上 PD1 与 PD2 DC 电压尽量接近
2. **制备 CSS**：Pump 持续开启足够长时间（$\gg T_1$，如 0.5 s），确保极化饱和
3. **缓变沿关断 Pump**：
   - 方案 A（推荐）：Pump_modulation 100MHz 载波末尾 50~100 μs 幅度指数 ramp down，同时 Time_sequence 保持方波
   - 方案 B（简化）：Time_sequence 从方波切换为 0V DC
   - 记录关断时刻 $t_0$
4. **示波器采集衰减曲线**：从 $t_0$ 前 ~10 ms 开始触发，采集 PD1(CH1)、PD2(CH2) 直流波形时长 $3\sim5$ 倍 $T_1$
5. **重复采集**：多次重复（≥ 20 次）取平均后拟合，降低 PD 电噪声影响

> **采集方式**：PD1/PD2 的差模输出已同时接入锁相放大器（HF2）和示波器（SDS）。T₁ 测量使用**示波器**采集双通道 DC 耦合波形，采样率 ≥ 10 kSa/s 即满足 T₁≈30 ms 的时间分辨率需求。备选方案可用 HF2 DAQ 的 `subscribe_raw()` 订阅 AUXIN 节点。

### 多功率 T₁ 扫描

1. 设定探测光功率 $P_{\text{probe}}$ 为功率列表中第一个值
2. 执行上述单功率 T₁ 测量流程，提取 $T_1(P_{\text{probe}})$
3. 遍历所有功率点，得到 $T_1^{-1}$ vs $P_{\text{probe}}$ 数据
4. 线性拟合得截距 $T_{1,0}^{-1}$ 和斜率 $\alpha$

## 数据分析

### θ_F 计算

平衡偏振探测中，$\lambda/2$ 波片 + PBS 将 Faraday 旋光角转换为两路光强差：

$$
\theta_F = \frac{1}{2}\arcsin\left(\frac{V_x - V_y}{V_x + V_y}\right) - \theta_F^{(0)}
$$

小角度近似下：

$$
\theta_F \approx \frac{V_x - V_y}{2(V_x + V_y)} - \theta_F^{(0)}
$$

其中 $\theta_F^{(0)}$ 为热态残余旋光角（Pump 关闭后长时间等待至热平衡测得的偏置）。

### T₁ 指数拟合

对 $\theta_F(t)$ 做单指数衰减拟合：

$$
\theta_F(t) = A e^{-t/T_1} + \theta_F^{(\infty)}
$$

- $\theta_F^{(\infty)}$：热态残余旋光角（应接近 0）
- 拟合区间从 $t_0$ 开始，持续到信噪比不足为止

### T₁⁻¹ vs P_probe 线性拟合

例（论文图 3-15）：

$$
T_1^{-1}(P_{\text{probe}}) = 23.26 + 0.0293 P_{\text{probe}}
$$

| 参数 | 数值示例 | 物理机制 |
|------|---------|---------|
| 本征弛豫率 $T_{1,0}^{-1}$ | 23.26 s⁻¹ | 壁碰撞 + 自旋交换碰撞 |
| $T_{1,0}$ | ~43 ms | — |
| 光致退相干系数 $\alpha$ | 0.0293 | 探测光自发辐射散射 |

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| $\theta_F(t)$ 衰减有震荡 | Pump 方波关断激发横向分量 | 改用缓变沿关断（时间常数 50~100 μs） |
| 指数拟合残差大 | T₁ 太短（光致退相干主导） | 降低 Probe 功率重新测量 |
| $\theta_F(0)$ 随重复漂移 | 温度变化导致原子数变化 | 检查温控 PID，延长热平衡等待 |
| $T_{1,0}^{-1}$ 截距为负 | 低功率段数据不足或拟合外推不准 | 增加低功率测量点 |
| PD 信号偏置漂移 | λ/2 波片温漂或机械位移 | 每次测量前重新调零 |
