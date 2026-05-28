---
title: 光散粒噪声标定 (PSD 方法)
type: experiment_type
description: 改变主磁场将原子自旋噪声移出 LIA 探测带宽，扫描探测光功率，采用 Welch PSD + 工频陷波 + 积分得噪声功率，通过 Var(X) vs P_probe 线性拟合提取散粒噪声转换系数 α 和电子噪声底噪 β。支持 Bell-Bloom CW 常开探测和频闪 QND 两种模式。Bell-Bloom 模式下可进一步通过 PSD 洛伦兹拟合分离光噪声与原子噪声分量
keywords: [shot noise, calibration, probe power, PSD, welch, notch filter, lock-in, linear fit, bell-bloom, cw-probe, lorentzian fit, S_light decomposition]
version: 4
geometry:
  main_field: Z
  light_propagation: X

scan_mode: point_by_point      # 逐点扫描探测光功率

# ========== 默认参数 ==========
defaults:
  # ---- 探测光功率扫描 ----
  PROBE_POWER_START: 0.0        # 探测光功率起始 (V)
  PROBE_POWER_STOP: 0.1         # 探测光功率终止 (V)
  PROBE_POWER_POINTS: 11         # 扫描点数（如 0, 50, 100, 200, 400, 600 μW 对应电压）
  PROBE_SETTLE_TIME: 0.5        # 每点设置后等待稳定时间 (s)
  # ---- 采集参数 ----
  ACQ_DURATION: 5.0             # 每点采集时长 (s)
  ACQ_REPEATS: 20               # 每点采集重复次数（确保方差统计稳定）
  # ---- LIA 解调参数（与正式实验一致） ----
  HF2_DEMOD_IDX: 0              # 解调器索引
  HF2_OSC_FREQ: 90000           # 振荡器频率 (Hz)，即 Larmor 频率 Ω_L
  HF2_SIGNAL_RANGE: 2.0         # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4            # 解调滤波器阶数
  HF2_DEMOD_TC: 0.000692        # 解调时间常数 (s)
  HF2_DEMOD_RATE: 10000         # 解调输出数据速率 (Sa/s，≥5×LPF 带宽)
  # ---- 磁场偏移参数 ----
  MAIN_FIELD_OFFSET_mA: 5.0     # 主磁场偏移量 (mA)，使 Ω_L 偏离参考频率几十 kHz 以上
  # ---- PSD 分析参数（Bell-Bloom 模式） ----
  FFT_SEGMENT_LENGTH: 8192      # Welch PSD 分段长度
  FFT_OVERLAP: 0.5              # Welch PSD 分段重叠率
  NOTCH_HARMONICS: [50, 100, 150, 200, 250, 300, 350]  # 工频谐波陷波频率 (Hz)

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200               # 主磁场（大幅偏置，移开原子共振）
    role: main_field
  - instrument: signal_generator    # Probe 光功率 (DG912 Pro CH2)
    role: laser_probe
    mapping_key: Probe_laser_power
    channels: [2]
  - instrument: signal_generator    # Pump 光功率（关闭，DG912 Pro CH1）
    role: laser_pump
    mapping_key: Pump_laser_power
    channels: [1]
  - instrument: signal_generator    # 温度开关（采集时关闭，消除温控 PWM 磁场干扰）
    role: temp_switch
    mapping_key: Temp_Switch
    channels: [2]
  - instrument: lockin_amplifier    # 锁相放大器（DAQ 采集 X/Y 时间序列）
    role: detection
    demod_channels: 2
    has_daq: true
  - instrument: tec_controller      # 温控器（TEC103）

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  main_magnetic_field:
    role: fixed
    description: "主磁场大幅偏离正常值，使 Ω_L 移出 LIA 探测带宽，隔离原子自旋噪声"
  Probe_laser_power:
    role: scan
    description: "探测光功率，扫描变量。DC 电平控制，范围对应 0~600 μW。Bell-Bloom 模式下为 CW 常开，无频闪调制"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率，关闭（设为 0 或最小值），确保无极化"
  lockin_xy:
    role: detection
    description: "HF2 DAQ 采集解调后的 X、Y 时间序列。Welch PSD + 陷波积分得噪声功率，或直接洛伦兹拟合提取光噪声本底。X 为主通道"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，采集时关闭以消除温控磁场干扰"

# ========== 固定参数 ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - temperature
  - Temp_Switch

# ========== 修复经验 ==========
learned_notes:
  - 扫描前确认 Pump 光完全关闭（隔离度足够），避免残余泵浦引入原子噪声
  - 高功率点若偏离线性，可能是探测器饱和，需降低功率范围或检查 PD 工作点
  - 截距 β 过大说明电子噪声/地回路问题，参考接地排查（3.5 节）
  - 工作光功率应设定在 α·P_probe ≫ β 的范围，散粒噪声高出电子噪声 6~10 dB
  - 温度开关在采集时必须关闭，避免温控 PWM 磁场混入 LIA 信号
  - 采用 Welch PSD + 工频陷波积分法计算噪声功率，可剔除 50 Hz 谐波等窄带技术噪声
  - 通过 PSD 频谱平坦度可验证白噪声假设，若低频翘起则存在 1/f 噪声干扰
  - Bell-Bloom 常开探测：探测光为连续 CW，无频闪调制。热态下 ⟨J⟩=0 无反作用噪声，标定方法与频闪 QND 模式完全一致
  - S_light 的频域确定：取 S_thermal(f) 在 f > 5f_c 的高频平坦段平均值；当散粒噪声高出电噪声 6~10 dB 时，S_light ≈ S_shot，可近似互换
  - 若 DC 附近 (<10 Hz) 存在显著技术 1/f 噪声污染洛伦兹峰，可仅用 f > f_min 数据段拟合（洛伦兹尾部仍能约束 f_c 和 A）
---

# 光散粒噪声标定

## 原理

改变主磁场大小，将原子自旋的 Larmor 频率移出锁相放大器探测带宽，此时 LIA 输出 X 的 PSD 主要由光散粒噪声与电噪声构成。

**核心公式**：

$$
\mathrm{Var}(X) = \alpha P_{\text{probe}} + \beta \tag{3.1}
$$

- $\alpha$：散粒噪声转换系数
- $\beta$：系统电子噪声底噪截距

```
    方差
     ↑
     |         *  线性区（散粒噪声主导）
     |       *    ← 工作区间
     |     *      
     |   *        
     | *          

     |__________  截距 β = 电噪声底
     +------------→ 探测光功率
```

**验证标准**：工作光功率应设定在 $\alpha P_{probe} \gg \beta$ 的范围，使散粒噪声高出电子噪声 **6~10 dB**。

### S_light 的分解

LIA 输出的总光噪声本底 $S_{\text{light}}$ 由两部分构成：

$$
S_{\text{light}} = S_{\text{shot}} + S_{\text{elec}}
$$

| 分量 | 来源 | 频率特征 | 与光功率关系 |
|------|------|---------|------------|
| $S_{\text{shot}}$ | 光散粒噪声（量子噪声） | 白噪声（平坦） | $\propto P_{\text{probe}}$ |
| $S_{\text{elec}}$ | 电子学噪声（探测器暗电流、放大器噪底等） | 白噪声（平坦） | 与光功率无关 |

> **重要区分**：$\tilde{\kappa}^2$ 是相对于纯散粒噪声 $S_{\text{shot}}$ 定义的（$\tilde{\kappa}^2 = \Delta\mathrm{Var}/\mathrm{Var}(S_y^{\text{light}})$），乘法因子必须用 $S_{\text{shot}}$，因为电噪声不参与光-原子耦合。当散粒噪声高出电噪声 **6~10 dB** 时，$S_{\text{light}} \approx S_{\text{shot}}$，两值可近似互换。若电噪声不可忽略，需从 $S_{\text{light}}$ 中先减掉 $S_{\text{elec}}$ 得到 $S_{\text{shot}}$。

### Bell-Bloom 常开探测下的 PSD 谱形

在 Bell-Bloom 磁力仪中，探测光**连续 CW 照射**（无频闪调制）。当主磁场恢复正常、原子自旋噪声进入 LIA 带宽后，解调后的自旋噪声 PSD 呈以 DC 为中心的**洛伦兹线形**（非白噪声）：

$$
S_{\text{thermal}}(f) = S_{\text{light}} + \tilde{\kappa}^2 \cdot S_{\text{shot}} \cdot \frac{5}{4} \cdot L(f; T_2) \tag{3.6a}
$$

其中洛伦兹线形函数 $L(f; T_2) = \frac{2T_2 / \pi}{1 + (2\pi f T_2)^2}$，归一化积分为 1，FWHM = $1/(\pi T_2)$。

实测 PSD 谱线的物理解释：

| 分量 | 谱特征 | 物理解释 |
|------|--------|---------|
| $S_{\text{light}}$ | 平坦白噪声底 | 光散粒噪声 + 电噪声本底（与频率无关，常数） |
| 原子噪声洛伦兹 | 中心在 DC，FWHM ~ 1/(πT₂) | 热态投影噪声的频域映射 |
| 离散尖峰 | 窄带线（50 Hz 及谐波等） | 技术噪声，需在积分前剔除 |

光散粒噪声标定在此框架中的作用是**确定 $S_{\text{light}}$ 和 $S_{\text{shot}}$ 的绝对值**（通过功率扫描 + 线性拟合），为后续 PNL 和 $\tilde{\kappa}^2$ 标定提供噪声基准。

## 实验步骤

1. 关闭泵浦光和再泵浦光
2. 将主磁场 $B_x$ 大幅改变（使 $\Omega_L$ 偏离锁相参考频率几十 kHz 以上），原子自旋噪声移出 LIA 探测带宽
3. 保持探测光正常开启，探测模式（Bell-Bloom CW 常开或频闪 QND 脉冲）、失谐与正式实验一致
4. 设置一系列探测光功率 $P_{probe}$ 梯度（如 0, 50, 100, 200, 400, 600 μW 等）
5. 每个功率点采集 LIA 的 X、Y 双路输出时间序列（1~10 s），计算方差 $\mathrm{Var}(X)$
6. 作 $\mathrm{Var}(X)$ vs $P_{probe}$ 图，线性拟合得到 $\alpha$ 和 $\beta$

### Bell-Bloom 模式下的 Var(X^light) 测量细节

| 步骤 | 操作 |
|------|------|
| 泵浦光 | 关闭 |
| 探测光 | **CW 常开**，保持与正式实验相同的功率和失谐 |
| 主磁场 B_x | **大幅改变**，使 Ω_L 偏离 LIA 参考频率几十 kHz，原子噪声移出带宽 |
| 其他 | 等待热平衡（≥ 5×T₁）后采集 LIA X(t) 和 Y(t) 时间序列 |

> **Bell-Bloom CW 常开 vs 频闪 QND**：散粒噪声标定中，探测模式必须与正式实验一致。Bell-Bloom 磁力仪使用连续 CW 探测（无频闪调制），频闪 QND 使用脉冲探测（D≈0.1）。两种模式在此步骤的操作流程完全一致——唯一区别是探测光的调制方式。热态下（Pump 关闭）⟨J⟩=0，无反作用噪声，因此两种模式的标定结果可直接沿用。详见 `主要参数实验标定.md` 3.4.1c 节。

### 采集前确认解调相位

X 路应与 $\hat{J}_z'$ 对齐（$\phi_0$ 已调至使自旋进动信号最大化的方向）。采集后可通过**数值正交旋转**——在软件中对 (X,Y) 做线性组合旋转，遍历相位角找到使一方差最大的方向——来验证相位对准是否正确。

## 采集信号链路

```
平衡探测器 → LIA(混频+LPF, BW~2kHz) → X(t) → DAQ → 采集
                                     → Y(t) → DAQ → 采集
```

LIA 解调后信号已在 DC 附近（Larmor 分量被移至零频），频谱范围由 LIA 的 LPF 带宽决定（~2 kHz），采样率只需满足解调后带宽的 Nyquist 准则。

## 数据采集参数建议

| 参数 | 建议值 | 说明 |
|------|--------|------|
| 采集通道 | X 和 Y 双通道 | X 为主通道，Y 用于验证 |
| 采样率 | ≥ 5 × f_LPF（~10 kSa/s） | LIA 输出带宽 ~2 kHz |
| 单次采集时长 | 1~10 s | 兼顾统计精度和低频漂移 |
| 采集重复次数 | ≥ 20 次 | 确保方差的统计稳定性 |
| 探测光功率点数 | 6~8 点 | 覆盖从零到正常工作功率 |
| FFT 频率分辨率 | ≤ 5 Hz（Bell-Bloom 模式） | 需满足 Δf ≪ f_c（T₂=3 ms 时 f_c≈53 Hz），以分辨洛伦兹线形的低频结构 |

## 数据分析

### 方差 vs PSD

物理上两种方法等价（Parseval 定理：$\mathrm{Var}(X) = \int_{0}^{BW} \mathrm{PSD}(f) \, df$），但频域分析提供额外的诊断能力：

| | 直接用 X(t) 求方差 | 先 FFT 求 PSD 再积分 |
|---|---|---|
| **做法** | 对时间序列直接算 $\langle X^2 \rangle - \langle X \rangle^2$ | X(t) → FFT → S(f) → 选定频段积分 |
| **输出** | 一个数 | 一条频谱曲线 → 一个数 |
| **能否剔除技术噪声** | 不能——50 Hz、谐波、EMI 都统一计入方差 | 能——频谱上技术噪声尖峰一目了然，积分时可剔除 |
| **能否验证白噪声假设** | 不能 | 能——可检查 PSD 是否在带宽内平坦 |
| **计算量** | 极小（$O(N)$） | 需 FFT（$O(N\log N)$） |
| **适用场景** | 已知系统噪声纯净时快速估算 | **标定散粒噪声（推荐）** |

### 方法一：PSD 积分法

标准分析流程：

```
X(t) → Welch PSD → S(f)
                      ↓ 工频陷波（剔除 50 Hz 及谐波）
                   S_clean(f)
                      ↓ 频段积分
                   Var(X) = ∫ S_clean(f) df
```

对每个光功率点重复上述流程，得到 Var(X) vs P_probe 数据，再线性拟合。

### 方法二：PSD 直接拟合法（Bell-Bloom 推荐）

Bell-Bloom 模式下可直接从热态 PSD 出发，用洛伦兹拟合分离光噪声本底和原子噪声：

```
X(t) → FFT → S_thermal(f)
                ↓ 减去 S_light（平坦底，常数）
              ΔS(f) = κ̃² · S_shot · (5/4) · L(f; T₂)
                ↓ 洛伦兹拟合 ΔS(f) = A / [1 + (f/f_c)²] + S_offset
              A, f_c
                ↓
         S_PNL(f) = (4/5) · A / [1 + (f/f_c)²]
```

拟合参数与物理量的映射：

| 拟合量 | 物理含义 | 给出 |
|--------|---------|------|
| $f_c$ | 洛伦兹截止频率 $(2\pi T_2)^{-1}$ | $T_2 = 1/(2\pi f_c)$，与 3.4.4 节独立测量值交叉验证 |
| $A = \Delta S(0)$ | DC 处原子噪声峰值 | $\tilde{\kappa}^2 \cdot S_{\text{shot}} \cdot \frac{5}{4} \cdot \frac{2T_2}{\pi}$，含 κ̃² 信息 |
| $\int \Delta S(f)\,df$ | 热态总原子噪声光功率 | $A \cdot \pi f_c / 2 = \tilde{\kappa}^2 \cdot S_{\text{shot}} \cdot \frac{5}{4}$，与公式 (3.8') 自洽 |
| $S_{\text{offset}}$ | 拟合残差白噪声底 | 应 ≈ 0；若非零则存在未被减掉的额外白噪声源 |

**直接拟合法的优势**：

| | 积分法 | 直接拟合法 |
|---|---|---|
| 输出 | 一个数（Var） | **一整条洛伦兹谱** + 三个物理参数 |
| T₂ 验证 | 需额外独立测量 | **从 FWHM 同时获得**，一步交叉验证 |
| 技术噪声 | 需手动剔除尖峰后积分 | 白噪声底 $S_{\text{offset}}$ 作为拟合参数**自然分离** |
| 1/f 噪声判别 | 混入积分无法甄别 | 若低频段偏离洛伦兹即暴露，可仅用 $f > f_{\text{min}}$ 尾部数据拟合 |

### 线性拟合

1. 对每个功率点，计算 $\mathrm{Var}(X)$ 的均值和标准差（来自重复采集）
2. 作 $\mathrm{Var}(X)$ vs $P_{probe}$ 散点图
3. 线性拟合 $\mathrm{Var}(X) = \alpha P_{probe} + \beta$
4. 确认高功率点在线性区内，低功率点不抬升（无额外噪声源）

从拟合结果可直接得到 $S_{\text{shot}}$ 和 $S_{\text{elec}}$：
- **斜率 $\alpha$**：单位光功率的散粒噪声贡献 → $S_{\text{shot}} = \alpha \cdot P_{\text{probe}}$
- **截距 $\beta$**：零光功率时的电噪声 → $S_{\text{elec}}$

### S_light 的频域确定方法

在 Bell-Bloom 模式下，$S_{\text{light}}$ 可从 PSD 高频段直接读取：

1. 取 $S_{\text{thermal}}(f)$ 在 $f > 5f_c$ 的高频平坦段的平均值作为 $S_{\text{light}}$
2. 该值应与 3.4.1a 节单独测得的散粒噪声 PSD 本底一致——不一致则说明电噪声在两次测量间有漂移

### 操作注意点

1. **频率分辨率**：$\Delta f \ll f_c$。T₂=3 ms 时 $f_c$≈53 Hz，需 FFT 分辨率 ≤5 Hz，单次采集时长 ≥0.2 s
2. **DC 附近 1/f 噪声**：若系统在 <10 Hz 有显著技术 1/f 噪声，洛伦兹 DC 部分被污染。此时可仅用 $f > f_{\text{min}}$ 的数据段拟合（洛伦兹尾部仍能约束 $f_c$ 和 $A$）
3. **工频陷波**：Welch PSD 积分前需对 50 Hz 及谐波（100, 150, 200, 250, 300, 350 Hz）做陷波处理，剔除窄带技术噪声

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| 高功率时偏离线性 | 探测器饱和 | 降低光功率，检查 PD 工作点 |
| 截距过大 | 电噪声/地回路 | 检查接地（参考 3.5 节） |
| 斜率与理论预期不符 | 光路对准偏差 | 检查偏振纯度、光斑与气室匹配 |
| 低功率方差反而增大 | 锁相放大器噪底 | 确认 HF2 输入量程设置合理 |
| 热态噪声比预期小 | 泵浦光泄漏、原子未被完全去极化 | 检查泵浦 AOM 关断隔离度 |
| 热态噪声比预期大 | 环境磁噪声、地回路噪声混入 | 检查接地、磁屏蔽，查看 PSD 是否有技术噪声尖峰 |
| 热态 X/Y 方差差异大 | 解调相位未对准、或系统存在偏振相关噪声 | 检查解调相位，做数值正交旋转验证 |
| PSD 低频段偏离洛伦兹 | 1/f 技术噪声污染 | 仅用 f > f_min 尾部数据拟合，排查噪声源 |
| S_light 两次测量不一致 | 电噪声在两次测量间有漂移 | 检查电子学温度稳定性，缩短两次测量间隔 |
| 频谱上出现窄带尖峰 | 50 Hz 工频、地回路、EMI 耦合 | 检查接地和磁屏蔽，频域陷波剔除尖峰后积分 |

## Bell-Bloom 常开探测与频闪 QND 的本质差异

| | 频闪 QND（脉冲探测） | Bell-Bloom（连续探测） |
|---|---|---|
| 探测光 | 脉宽 ≪ T_L，D≈0.1 | 连续 CW |
| 反作用噪声 (BAN) | 被**主要**限制在正交分量 J_y'（C(D)≪1），不回流 J_z' | 通过 Larmor 进动**持续回流到被测分量** |
| BAN 尺寸 | ∝ C(D)，D→0 时 C→0（实验取 D≈0.1，C≈0.008） | 最大——连续探测等效于 D=1 |
| 对 PNL 标定的影响 | CSS 态下 BAN 混入 J_z' 测量，增大表现噪声 | 同上，BAN 混入更严重 |
| **热态下标定 PNL 仍然有效？** | **是**——关泵浦后无平均自旋，无 BAN | **是**——关泵浦后 ⟨J⟩=0，同样无 BAN |

> **关键结论**：热态下（泵浦光关断）原子平均自旋为零，不存在条件压缩/反压缩，**反作用噪声在这一状态下不会产生**。因此，热态噪声标定 PNL 的方法在两种探测模式下均可直接使用——这是测量流程完全一致的物理原因。Bell-Bloom 下 $\tilde{\kappa}^2$ 的含义是连续探测的有效耦合强度（等效于 D=1 的频闪探测），其值应显著大于脉冲模式。
