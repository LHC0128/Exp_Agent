---
title: 横向弛豫时间 T₂ 标定（光学 FID 时域法 + MORS 频域法）
type: T2_Calibration
experiment_id: t2-calibration
schema_version: 2
execution_mode: typed_workflow
description: 两种独立方法标定横向弛豫时间 T₂。方法一：RF 开关门控 Pump AOM 产生 Ω_L 频率调制 Pump 光 Burst 激发横向自旋相干，关断后示波器采集 PD 阻尼振荡信号，拟合得 T₂。方法二：MORS 谱共振峰 Lorentzian 拟合得 FWHM，反推 T₂ = 2/Γ。Bell-Bloom 下可与 PSD 洛伦兹拟合结果交叉验证
keywords: [T2, transverse relaxation, FID, free induction decay, optical FID, MORS, Lorentzian, FWHM, RF switch, AOM carrier, burst, damped oscillation fit, Bell-Bloom]
version: 3

scan_mode: point_by_point       # 默认逐点扫描 Probe 光功率，也支持单功率采集

# ========== 默认参数 ==========
defaults:
  # ---- Pump 光 ----
  PUMP_POWER: 0.1               # Pump 光 DC 功率 (V)，决定泵浦光强偏置
  # ---- RF 开关: AOM 载波 (CH1) ----
  AOM_CARRIER_FREQ: 100.0e6     # AOM 载波频率 (Hz)，固定 100 MHz
  AOM_CARRIER_AMPLITUDE: 0.1    # AOM 载波幅度 (V)，安全限值 ≤ 0.18V
  # ---- RF 开关: 门控脉冲 (CH2) ----
  RF_GATE_FREQ: 90000           # 门控脉冲频率 (Hz) = Ω_L/2π，等于 Larmor 进动频率
  RF_GATE_AMPLITUDE: 5.0        # 门控脉冲幅度 (Vpp)，5V TTL 电平驱动 RF 开关 CTRL
  RF_GATE_OFFSET: 2.5           # 门控脉冲 DC 偏置 (V)，5Vpp + 2.5V offset = 0~5V
  RF_GATE_DUTY: 5.0             # 门控脉冲占空比 (%)
  BURST_NCYCLES: 5000           # Burst 周期数，5000/90000 ≈ 55.6 ms
  BURST_PERIOD: 0.1             # Burst 周期 (s)
  # ---- Probe 光 ----
  DO_POWER_SCAN: true           # 默认执行 Probe 功率扫描
  PROBE_POWER: 0.1              # Probe 光功率 (V)，Bell-Bloom CW 常开
  PROBE_POWER_START: 0.01       # Probe 功率扫描起始值 (V)
  PROBE_POWER_STOP: 0.1         # Probe 功率扫描终止值 (V)
  PROBE_POWER_POINTS: 10        # 等间距扫描点数
  # ---- 主磁场 ----
  MAIN_FIELD_mA: 9.30           # 正常工作主磁场 (mA)，Ω_L/2π ≈ 90 kHz
  # ---- 示波器采集 ----
  SCOPE_SAMPLE_RATE: 1.0e6      # 示波器采样率 (Sa/s)，≥ 20×f_L 以分辨 90 kHz 载波
  SCOPE_DURATION: 0.05          # 采集时长 (s)，下降沿触发后只采 FID 衰减段
  SCOPE_PD_CHANNEL: 1           # PDB 输出接 CH1（隐藏接线参数）
  SCOPE_TRIG_CHANNEL: 4         # CH2 同步输出接 CH4（隐藏接线参数）
  SCOPE_TRIG_SLOPE: FALLing     # Burst 结束下降沿触发
  ACQ_REPEATS: 5                # 每个功率点重复次数
  # ---- LIA 监控参数 ----
  HF2_DEMOD_IDX: 1              # 解调器索引
  HF2_OSC_FREQ: 90000           # 旧兼容键；运行时自动等于 RF_GATE_FREQ
  HF2_SIGNAL_RANGE: 2.0         # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4            # 解调滤波器阶数
  HF2_DEMOD_TC: 0.001           # 解调时间常数 (s)，监控用，无需高带宽
  HF2_DEMOD_RATE: 10000         # 解调输出数据速率 (Sa/s)
  # ---- 温度控制 ----
  TEC_TEMPERATURE: 100.0        # 气室温度 (°C)

# ========== 所需设备 ==========
required_devices:
  - instrument: gs200               # 主磁场（正常值）
    role: main_field
  - instrument: signal_generator    # Probe 光功率 (DG912 Pro CH2)，CW 常开
    role: laser_probe
    channels: [2]
  - instrument: signal_generator    # Pump 光功率 DC 偏置 (DG912 Pro CH1)
    role: laser_pump
    channels: [1]
  - instrument: signal_generator    # RF 开关: CH1=100MHz AOM载波(CW) + CH2=90kHz方波Burst(门控,同步输出触发示波器) (DG4E222800868)
    role: rf_switch
    channels: [1, 2]
  - instrument: sds_acquisition     # 示波器采集 PD 原始信号（含 90 kHz 载波的 FID 阻尼振荡）
    role: detection
  - instrument: lockin_amplifier    # 锁相放大器（实时监控 PD 信号）
    role: monitor
    demod_channels: 1
    has_daq: false
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
    description: "Pump 光 DC 功率偏置，决定泵浦光强"
  Pump_modulation:
    role: carrier
    description: "RF 开关 CH1：100 MHz 正弦 CW → RF 开关 IN → AOM 驱动载波"
  Time_sequence:
    role: excitation
    description: "RF 开关 CH2：90 kHz 方波 Burst N 周期 → RF 开关 CTRL，门控 100 MHz 通断产生调制 Pump 光；同步输出连示波器外触发"
  scope_waveform:
    role: detection
    description: "示波器采集 PD 原始信号——含 90 kHz 载波的 FID 阻尼振荡"
  lockin_xy:
    role: monitor
    description: "LIA 实时监控 PD 信号幅度/相位"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，每点采集时关闭以消除温控磁场干扰"

# ========== 固定参数 ==========
fixed_params:
  - main_magnetic_field
  - Probe_laser_power
  - temperature

# ========== 修复经验 ==========
learned_notes:
  - RF 开关信号链路：CH1 (Pump_modulation) 输出 100 MHz 正弦 CW（AOM 载波）→ RF 开关 IN；CH2 (Time_sequence) 输出 90 kHz 方波 Burst（门控）→ RF 开关 CTRL；RF 开关 OUT → AOM
  - CH2 门控频率必须精确等于 Ω_L/2π (90 kHz)，频率偏差会导致 FID 阻尼振荡出现拍频，拟合 T₂ 偏小
  - Burst 模式使用 CH2 内部触发源，CH2 同步输出连接示波器 CH4，下降沿对准 Burst 结束时刻
  - Burst 结束后 CH2 输出停止 (0V)，RF 开关阻断 100 MHz 载波 → AOM 无驱动 → Pump 光等效关断，自旋自由进动衰减
  - AOM 始终工作在 100 MHz（标准 AOM 中心频率），90 kHz 调制由 RF 开关门控实现，无需改变 AOM 驱动频率
  - 低极化度（~74%）时 FID 出现多频阻尼振荡，需用多频阻尼振荡模型拟合；高极化度（~99%）时基本为单频阻尼振荡
  - 当前脚本默认示波器采样率为 1 MSa/s；预检强制高于 Nyquist 条件，提高到 ≥20×f_L 可进一步改善载波波形分辨率
  - 数据分析可直接拟合阻尼振荡 V(t) = A·exp(-(t-t₀)/T₂)·cos(2πf_L(t-t₀)+φ) + V_DC，也可先数字解调提取包络再拟合指数衰减
  - Pump 光 DC 功率 (PUMP_POWER) 影响极化度，不同 DC 功率下 T₂ 不同，必须注明条件
  - Bell-Bloom 特有验证：PSD 洛伦兹拟合得到的 FWHM 应与光学 FID/MORS 法独立标定的 T₂ 一致（差异 < 20%），详见 Projection_noise.md
  - LIA 在本实验中仅用于实时监控，不参与数据采集；示波器是主检测设备
  - 每次采集前（acquire data）关闭 Temp_Switch 以消除温控加热电流产生的磁场干扰，采集完成后恢复
  - MORS 频域法依赖 MORS_polarization.md 的 MORS 谱数据，RF 驱动功率必须足够低（弱驱动），否则功率展宽使 FWHM 偏大、T₂ 被低估
---

> **坐标系**：主磁场 $B$ 沿 **Z**，光沿 **X** 传播。

# 横向弛豫时间 T₂ 标定

## 方法一：光学 FID 时域法

### 原理

RF 开关门控 Pump AOM 产生调制 Pump 光：CH1 (Pump_modulation) 输出 100 MHz 正弦 CW 作为 AOM 载波，CH2 (Time_sequence) 输出 90 kHz 方波 Burst 作为 RF 开关门控信号。CH2 高电平时 100 MHz 通过 RF 开关驱动 AOM、Pump 光进入气室；低电平时阻断、Pump 光关断。由此产生 90 kHz 调制的 Pump 光，以 Larmor 频率（$\Omega_L/2\pi$ ≈ 90 kHz）激发横向自旋相干。Burst 结束后 CH2 回到 0V → RF 开关阻断 → AOM 无驱动 → Pump 光等效关断，自旋在 $B_z$ 中以 $\Omega_L$ 自由进动，Faraday 旋光信号为指数衰减的阻尼振荡：

$$
\boxed{V(t) = A e^{-(t-t_0)/T_2} \cos(2\pi f_L (t-t_0) + \phi) + V_{\text{DC}}} \qquad (t \geq t_0)
$$

其中 $t_0$ 为 Burst 结束时刻，$f_L = \Omega_L/2\pi$ 为 Larmor 频率，$V_{\text{DC}}$ 为 PD 直流偏置。

示波器直接采集 PD 原始信号（含载波），通过拟合阻尼振荡提取 $T_2$。

> 也可先做数字解调（混频 + 低通滤波）提取包络 $R(t) = \sqrt{I^2 + Q^2}$，再对 $R(t)$ 做单指数拟合 $R(t) = A e^{-(t-t_0)/T_2} + R_{\text{DC}}$。

### 时序示意

```
 CH1 (AOM载波): ████████████████████████████████████████████████ (100 MHz CW 持续)
 CH2 (RF门控):  ____|████████████████████████████|________________
                    ↑ Burst 5000 cycles @ 90 kHz  ↑ 0V, RF开关阻断
                    = 55.6 ms                       Pump 等效 OFF
 Pump光(等效):  ____|▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓|________________
                    90 kHz 调制 Pump ON            Pump OFF
 探测光:        ████████████████████████████████████████████████████ (CW)
 Pump DC:       ████████████████████████████████████████████████████ (常开)
 示波器:        ___________________________________|→ 采集 FID →...
                                                   ↑ CH2 同步输出下降沿
```

### 实验步骤

1. 开启 Pump DC 偏置和 Probe 光（CW），主磁场设为正常值
2. **RF 开关配置**（DG4E222800868 双通道）：
   - CH1 (Pump_modulation)：100 MHz 正弦 CW，幅度 ≤ 0.18V → RF 开关 IN（AOM 载波，持续输出）
   - CH2 (Time_sequence)：90 kHz 脉冲（5% 占空比），5 Vpp + 2.5V offset → RF 开关 CTRL
   - CH2 Burst 模式：N = 5000 周期 (~55.6 ms)，内部触发源
   - CH2 同步输出连接示波器 CH4
3. **示波器配置**：CH4 下降沿触发；读取示波器返回的完整实际记录，再按真实时间轴保留 `t >= 0` 的 Burst 结束后 FID。不得按请求采样率预先裁剪点数，因为示波器可能自动调整实际采样率
4. 关闭 Temp_Switch（消除温控磁场干扰）
5. CH2 按内部触发周期执行 Burst → RF 开关通断 5000 次 → 90 kHz 调制 Pump 光激发自旋相干
6. 示波器同步采集 PD 波形，每个功率点重复 5 次取平均；自动量程沿用上一功率点的最终档位，不在功率点之间重置
7. 恢复 Temp_Switch
8. 无论正常完成、异常、取消或 Ctrl+C，都将温度开关恢复为 5 V DC 且保持 Output ON；保持主磁场、Pump/Probe、Pump 调制（AOM 载波）、RF 门控 CH2 Output ON 和全部 HF2 设置。RF 门控仅关闭 Burst，保留波形、同步和调制配置；其他辅助 DG 通道关闭 Burst/同步/调制、归零为 0 V DC 后关闭输出，同时停止示波器触发。正常结束只断开 TEC 通信，温控硬件继续运行，其他设备保持连接

### 新模式入口与参数

- 采集薄入口：`experiments/T2_Calibration.py`
- 分析薄入口：`experiments/T2_Calibration_plot.py [run_dir]`
- 默认参数：`params/experiments/t2-calibration.yaml`
- 强类型模块：`lab_workflows/experiment_modules/t2_calibration/`

GUI 显示运行标签、Pump/AOM 幅度、门控频率/占空比/Burst、Probe 单点或起始值/终止值/点数、
主磁场、采样率/时长/重复次数、HF2 量程与滤波参数、温度。接线通道、固定 AOM
载波、TTL 门控幅度/偏置、HF2 索引/参考频率和自动量程边界仍完整保存到配置，但不显示。
旧配置中的 `PROBE_POWER_LIST` 仍可加载：等间距列表自动迁移为三字段，非等间距列表
作为隐藏兼容覆盖保留并按原顺序执行。
`HF2_OSC_FREQ` 仅作为旧配置兼容键保留；运行时始终令
`HF2_OSC_FREQ = RF_GATE_FREQ`，GUI 无需也不能单独设置 HF2 参考频率。

### 数据分析

**直接拟合法**（推荐）：对 Burst 结束后的 PD 波形做非线性最小二乘拟合：

$$
V(t) = A e^{-(t-t_0)/T_2} \cos(2\pi f_L (t-t_0) + \phi) + V_{\text{DC}}, \quad t > t_0
$$

拟合参数：$A$（初始幅度）、$T_2$（横向弛豫时间）、$f_L$（Larmor 频率）、$\phi$（初始相位）、$V_{\text{DC}}$（直流偏置）。

分析器先对 Hilbert 包络按约三个载波周期平滑，比较首尾区间的包络幅度。若末段/首段比值大于
0.9，则判定没有可测 FID 衰减并返回 `NaN`。完成阻尼振荡拟合后，还会拒绝落在 T₂ 上下界、
相对不确定度大于 50% 或 $R^2 < 0.8$ 的结果；`analysis.yaml` 保存每个功率点的
`fit_reason`、`envelope_tail_ratio` 和 `r_squared`，无效点不参与 $T_2^{-1}$ 线性拟合。
拟合仍使用完整的触发后 FID 数据；功率扫描时同时生成 `0–2 ms` 和 `0–10 ms`
两张时域总览图，分别用于观察初始衰减细节和完整衰减趋势。

**数字解调法**（备选）：软件混频 + LPF 提取包络，再指数拟合。

- **低极化度（~74%）**：多频率分量 → 阻尼振荡含多个频率，需用多频模型：
  $$
  V(t) = \sum_k A_k e^{-(t-t_0)/T_{2,k}} \cos(2\pi f_{L,k} (t-t_0) + \phi_k) + V_{\text{DC}}
  $$
- **高极化度（~99%）**：基本为单频阻尼振荡，单频拟合可靠

---

## 方法二：MORS 频域法

### 原理

连续弱 RF 驱动下，MORS 谱共振峰的半高全宽 (FWHM) 由 $T_2$ 决定。从 MORS 谱中提取线宽 $\Gamma$（FWHM，单位 rad/s）后：

$$
\boxed{T_2 = \frac{2}{\Gamma}} \tag{3.21}
$$

> 若 $\Gamma$ 单位为 Hz：$T_2 = \dfrac{1}{\pi \cdot \Gamma_\text{Hz}}$

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
| 光学 FID 时域法 | 无需 RF 线圈、光路简单、时序直观 | 需精确 Burst 时序、示波器采样率要求高 | 高极化度、单分量衰减 |
| MORS 频域法 | 可同时获得极化度、不依赖时序 | 多峰拟合复杂、对磁场均匀性敏感 | 全极化度范围、参数综合标定 |

## Bell-Bloom 特有交叉验证

Bell-Bloom 磁力仪下 PSD 洛伦兹拟合的 FWHM 也给出 $T_2^{\text{PSD}}$。将此值与本节独立标定的 $T_2^{\text{FID}}$ 或 $T_2^{\text{MORS}}$ 交叉比对：

| 验证项 | 通过标准 | 参考 |
|--------|---------|------|
| $T_2^{\text{PSD}}$ vs $T_2^{\text{FID}}$ | 差异 < 20% | `Projection_noise.md` PSD 洛伦兹检验 |
| $T_2^{\text{FID}}$ vs $T_2^{\text{MORS}}$ | 差异 < 20% | 方法间交叉验证 |

三者一致则确认 PSD 中低频分量为纯净自旋投影噪声贡献。

## 实验注意事项

- Pump 调制频率必须精确等于 $\Omega_L/2\pi$，频率偏差导致 FID 出现拍频，拟合 $T_2$ 偏小
- Burst 结束后 Pump 光等效关断（AOM 偏转）；若 AOM 在 90 kHz 驱动下效率不足，需确认 Pump 光是否真正关断
- 示波器采样率应 ≥ 20×f_L（≥ 1.8 MSa/s）以分辨载波波形
- 两种方法可在不同探测光功率与 Pump DC 功率条件下交叉比较
- MORS 频域法中 RF 驱动功率必须足够低（弱驱动），否则功率展宽使 $\Gamma$ 偏大

## 常见问题

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| FID 阻尼振荡有拍频 | Pump 调制频率与 $\Omega_L$ 有偏差 | 微调 Pump_mod 频率使拍频消失 |
| FID 含多个频率分量 | 低极化度多 Zeeman 子能级 | 使用多频阻尼振荡模型拟合 |
| FID 衰减偏离指数 | Burst 结束后 Pump 光未完全关断 | 检查 AOM 偏转效率，确认 Pump 残余光功率 |
| 示波器波形信噪比差 | 采样率不足或未取平均 | 提高采样率，增加重复次数取平均 |
| MORS FWHM 偏大 | RF 功率过大导致功率展宽 | 降低 RF 幅度，验证线性区 |
| MORS 拟合 T₂ 与光学 FID T₂ 不一致 | Pump 条件、温度或 Probe 功率不同 | 确认实验条件一致 |
| PSD 洛伦兹 T₂ 与其他方法不一致 | 测量线路响应混入 | 检查 LIA LPF 设置，用更高 TC 排除线路展宽 |
