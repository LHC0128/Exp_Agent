---
title: 射频场灵敏度测量（AM 外部调制方案）
type: experiment_type
description: 利用 Bell-Bloom 磁力仪测量 Z 方向射频场灵敏度。通过 DG4000 AM 外部调制模式产生 X/Y 旋转控制场，dg_sweep CH1 Burst 正弦波输出 Z 射频场，扫描 Z 射频场幅度获得响应曲线，结合 Demod 3 的噪声 PSD 计算灵敏度。参照静磁场灵敏度测量流程分为响应曲线测量和灵敏度测量两部分。
keywords: [RF field, sensitivity, AM external modulation, cascaded demodulator, HF2 DAQ, DG4000, dispersion, PSD]
version: 2

scan_mode: point_by_point      # 逐点扫描 Z 射频场幅度

# ========== 默认参数 ==========
defaults:
  # ---- 响应曲线扫描参数（Phase A）----
  RF_AMP_START: 0.01           # Z 射频场幅度扫描起始 (V)
  RF_AMP_STOP: 5.0             # Z 射频场幅度扫描终止 (V)
  RF_AMP_POINTS: 200           # 扫描点数
  RF_SETTLE_TIME: 0.3          # 每点等待稳定时间 (s)
  # ---- 噪声采集参数（Phase B）----
  NOISE_N_AVG: 10              # 噪声采集平均次数
  NOISE_DURATION: 1.0          # 每次噪声采集时长 (s)
  NOISE_RATE: 100000           # 噪声采集采样率 (Sa/s)
  NOISE_NPERSEG: 10000         # Welch PSD 每段点数
  # ---- 包络 A(t) 参数 ----
  # [经验] DG4000 MOD Input 满量程 1.3V，外部 AM 线性响应。
  #       方波 A(t)=0V → AM 输出=0（自然载波抑制）
  #       方波 A(t)=1.3V → AM 输出=载波满幅度
  A_ENV_FREQ: 1000             # A(t) 包络频率 (Hz)
  A_ENV_AMPLITUDE: 1.3         # A(t) 包络幅度 (Vpp)
  A_ENV_OFFSET: 0.65           # A(t) DC 偏置 (V)，0~1.3V 方波
  A_ENV_SHAPE: "SQUare"        # 包络波形：方波 0V ↔ 1.3V
  # ---- X/Y 载波参数（dg_comp AM 模式）----
  XY_CARRIER_FREQ: 90000       # X/Y 载波频率 (Hz)，等于 Larmor 频率
  XY_CARRIER_AMPLITUDE: 5.0   # 载波幅度 (Vpp)
  XY_CARRIER_PHASE: 0          # CH1 载波初始相位 (deg)，校相后确定
  XY_CARRIER_QUAD: 90          # CH2 相对 CH1 的相位差 (deg)
  XY_AM_DEPTH: 100             # AM 调制深度 (%)
  # ---- Z 射频场参数（dg_sweep CH1 Burst）----
  Z_RF_FREQ: 10000             # Z 射频场频率 (Hz)
  Z_RF_AMPLITUDE_INIT: 1.0     # Z 射频场幅度初始值 (Vpp)，响应曲线扫描变量
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 90000         # Pump 调制频率 (Hz)，与 Larmor 频率一致
  PUMP_MOD_DUTY: 5             # 脉冲占空比 (%)
  # ---- HF2 解调器 0 配置（主信号解调，需尽可能小的 TC 以响应射频场）----
  DEMOD0_IDX: 0                # 解调器 0（主信号检波）
  DEMOD0_OSC_IDX: 0            # 振荡器 0，频率 = PUMP_MOD_FREQ
  DEMOD0_OSC_FREQ: 90000       # 振荡器 0 频率 (Hz)
  DEMOD0_SIGNAL_RANGE: 2.0     # 信号输入量程 (V)
  DEMOD0_ORDER: 4              # 解调滤波器阶数
  DEMOD0_TC: 1e-5              # 解调时间常数 (s)，尽可能小以保留射频场调制信息
  DEMOD0_RATE: 100000          # 解调输出数据速率 (Sa/s)
  # ---- HF2 解调器 3 配置（射频场解调）----
  DEMOD3_IDX: 3                # 解调器 3（射频场检测）
  DEMOD3_OSC_IDX: 1            # 振荡器 1，频率 = Z_RF_FREQ
  DEMOD3_ADC_SELECT: 2         # 信号输入源: 2 = Demod 0 内部 Y 输出
  DEMOD3_ORDER: 8              # 解调滤波器阶数
  DEMOD3_TC: 0.001             # 解调时间常数 (s)，响应曲线测量时使用
  DEMOD3_RATE: 1000            # 解调输出数据速率 (Sa/s)，响应曲线测量时使用
  # ---- 固定参数 ----
  PUMP_LASER_POWER: 0.1        # Pump 光功率 DC (V)
  PROBE_LASER_POWER: 0.1       # Probe 光功率 DC (V)
  MAIN_FIELD_mA: 9.305         # 主磁场 (mA)

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  X_magnetic_field:
    role: rf_xy_carrier
    description: "DG4000 (DG4E234902522) CH1，AM 模式，载波 90kHz，载波相位 θ，调制源 = EXT"
  Y_magnetic_field:
    role: rf_xy_carrier
    description: "同一 DG4000 CH2，AM 模式，载波 90kHz，载波相位 θ+90°，调制源 = EXT"
  Z_magnetic_field:
    role: rf_source_sweep
    description: "DG4000 (DG4E242401288) CH1，Burst 正弦波，幅度为扫描变量，输出 Z 方向射频场"
  Time_sequence_2:
    role: am_modulation_source
    description: "DG4000 (DG4E242401288) CH2，任意波模式输出 A(t) → BNC → dg_comp MOD Input"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流 ~9.3 mA），实验过程中固定不变"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率 DC 电平控制"
  Probe_laser_power:
    role: fixed
    description: "Probe 光功率 DC 电平控制"
  temperature:
    role: fixed
    description: "气室温度（TEC103 控制）"
  Temp_Switch:
    role: temp_gating
    description: "温度开关，每点采集时关闭以消除温控磁场干扰"
  Pump_modulation:
    role: pump
    description: "DG4000 (DG4E222800868)：CH1 100MHz 正弦，CH2 脉冲门控"
  lockin_r:
    role: primary_demodulation + rf_demodulation
    description: "HF2 锁相：Demod 0 主信号解调 + Demod 3 射频场解调（adcselect=2 级联）"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - temperature
  - Temp_Switch

# ========== 修复经验 ==========
learned_notes:
  - A(t) 通过 dg_sweep CH2 任意波输出→BNC→dg_comp MOD Input，作为外部 AM 调制源
  - dg_comp CH1/CH2 共享同一外部调制信号，保证 X/Y 同步
  - ⚠️ DG4000 在 AM 模式下调制器会额外引入通道间相位延迟差异，直接设 PHASe:ADJust 90° 时实际输出 ≠ 90°。必须采用分通道校相方法：先关 CH2 单独校准 CH1 相位，再开 CH2 以 R 最大化为目标梯度上升搜索 CH2 最优相位
  - AM 模式连续输出，无外部触发同步，载波与 Pump 的相位关系通过迭代校相确定
  - 时钟主从关系以 `params/clock_sources.yaml` 为准，频率锁定后相位不随时间漂移
  - Demod 0 的 TC 需尽可能小（≤10μs），使其带宽足够宽以响应射频场对原子的调制
  - Demod 3 在响应曲线采集时使用较大 TC（如 1ms）以抑制噪声，在噪声采集时可改用更小 TC
  - 噪声采集后必须恢复解调器配置，否则第二次运行异常
  - 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
  - 噪声测量每次采集完立即恢复温度开关，防止温度漂移
  - 两级相位校准相互独立：先 Demod 0 校相（主信号对齐），再 Demod 3 校相（射频场对齐）
---

# 射频场灵敏度测量（AM 外部调制方案）

## 原理

本实验测量 Bell-Bloom 磁力仪对 Z 方向射频场的灵敏度。通过 X/Y 线圈施加 AM 调制的旋转控制场使和场沿原子自旋方向，Z 射频场（待测）由 dg_sweep CH1 Burst 正弦波产生。

Z 射频场灵敏度的测量分为两个阶段：

**Phase A — 响应曲线测量**：
扫描 Z 射频场幅度 $B_\text{RF}$，读取 HF2 Demod 3 的输出（$X_3, Y_3, R_3$），获得射频场响应曲线。对响应曲线做色散拟合得到斜率 $\mathrm{d}R/\mathrm{d}B_\text{RF}$。

**Phase B — 噪声测量与灵敏度计算**：
在响应曲线斜率最大点（灵敏度最优工作点），关闭 Z 射频场，利用 Demod 3 采集噪声信号，计算功率谱密度 PSD。灵敏度计算公式为：

$$\delta B_\text{RF}(f) = \frac{\sqrt{\text{PSD}_{\text{Demod3}}(f)}}{|\mathrm{d}R/\mathrm{d}B_\text{RF}|}$$

## 双解调器级联架构

```
Signal Input 0 (光电探测器)
    │
    ▼
Demod 0 ─── 振荡器 0 (PUMP_MOD_FREQ ≈ 90 kHz)
    │         解调 Pump 调制信号
    │         TC 尽可能小 (≤10μs) → 带宽足够宽以响应射频场
    │
    └── Y₀ → FPGA 内部路由 (adcselect=2)
                │
                ▼
            Demod 3 ─── 振荡器 1 (Z_RF_FREQ)
                         二次解调，提取射频场响应
                         输出 X₃, Y₃, R₃
```

**关键**：Demod 0 的 TC 必须尽可能小（如 10μs 或更小），使其解调带宽覆盖可能的射频场频率范围。Demod 3 的 TC 可独立选择（响应曲线测量时 1ms 以滤波，噪声采集时可改用高速率）。

## 硬件连接与信号路由

```
┌────────────────────────────────────────────────────────────────────┐
│                       10MHz 外部参考时钟总线                        │
│                                                                   │
│  ┌─dg_mod──────┐  ┌─dg_comp──────┐  ┌─dg_sweep──────┐            │
│  │CH1:100MHz   │  │CH1:X AM载波  │  │CH1:Z RF Burst│←幅度扫描   │
│  │CH2:脉冲门控  │  │CH2:Y AM载波  │  │CH2:A(t) 任意波│──BNC──┐   │
│  │SYNC → Trig  │  │MOD Input ◄───┼──┼──────────────┘       │   │
│  └─────────────┘  └──────────────┘  └───────────────────────┘   │
│                                                                   │
│  ┌─dg_laser────┐  ┌─温控DG4000───┐  ┌─HF2──────────┐           │
│  │CH1:Pump DC  │  │CH2:Temp开关  │  │Sig In 0      │           │
│  │CH2:Probe DC │  │              │  │Demod 0+3     │           │
│  └─────────────┘  └──────────────┘  └──────────────┘           │
└────────────────────────────────────────────────────────────────────┘
```

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4E234902522 (dg_comp) | CH1 | AM 模式，载波 90kHz，相位 θ |
| Y 控制 | DG4E234902522 (dg_comp) | CH2 | AM 模式，载波 90kHz，相位 θ+90° |
| AM 调制源 | DG4E242401288 (dg_sweep) | CH2 → dg_comp MOD Input | A(t) 任意波，BNC 连接 |
| Z 射频场（扫描变量） | DG4E242401288 (dg_sweep) | CH1 | Burst 正弦波，幅度为扫描变量 |
| Pump 调制 | DG4E222800868 (dg_mod) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| SYNC 触发 | DG4E222800868 (dg_mod) | CH2 SYNC → dg_sweep Ext Trig | 触发 Z 射频场 Burst |
| 主磁场 | GS200 | - | 恒流模式，~9.3 mA |
| Pump 光功率 | DG4E231500376 (dg_laser) | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | DG4E231500376 (dg_laser) | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | DG9Q271200104 | CH2 | TTL 电平控制温控通断 |
| 信号检测 | HF2 锁相 | 信号输入 0 | Demod 0 + Demod 3 级联 |

## 相位校准流程

### Phase 1 — Demod 0 校相（主信号对齐）
1. 关闭温控、XY/Z 输出
2. 配置 HF2 信号输入、振荡器 0、Demod 0（TC=DEMOD0_TC=10μs）
3. `auto_calibrate_phase(demod_idx=0)` → `calibrated_phase_0`

### Phase 2 — X/Y 载波校相（对齐 Pump 调制 + 修正 AM 相位偏差）
**⚠️ AM 模式下 CH1/CH2 的 90° 相位差不再是精确的**，必须通过分通道校相确定：
1. 加载 A(t) 到 dg_sweep CH2，开启连续输出
2. 启动 dg_comp AM 模式，**先只开 CH1**，关闭 CH2
3. 关闭温控，读 HF2 Demod 0 相位偏移
4. 迭代修正 `XY_CARRIER_PHASE`（CH1 相位）直到收敛
5. **开启 CH2**，以 R 最大化为目标，梯度上升搜索 CH2 最优相位
6. 计算实际相位差 `actual_quad = (CH2_phase - CH1_phase) % 360`
7. 记录该偏差用于后续实验

### Phase 3 — Demod 3 校相（射频场对齐）
1. 预开启 Z 射频场（小幅度，如 0.5Vpp）
2. 配置振荡器 1 频率 = Z_RF_FREQ，Demod 3（adcselect=2, osc_select=1, TC=0.001, rate=1000）
3. 关闭温控 → `auto_calibrate_phase(demod_idx=3)` → 恢复温控
4. 得到 `calibrated_phase_3`

## 实验流程

### 准备阶段
1. **连接所有设备**，设置初始条件（光功率、主磁场、温度）
2. **Pump 调制配置**：CH1 100MHz 正弦 + CH2 脉冲门控，CH2 SYNC ON
3. **Phase 1**: Demod 0 自动相位校准（TC=10μs）
4. **加载 A(t) 到 dg_sweep CH2**：标准波形用 `setup_sine()` 等，USER 波形用 `send_arbitrary_waveform()`
5. **配置 X/Y AM 调制（dg_comp）**：
   - CH1: `setup_sine(freq=XY_CARRIER_FREQ, phase=0)` → `set_mod_type("AM")` → `set_mod_am_source("EXT")` → `set_mod_am_depth(100)` → `set_mod_state(ON)`
   - CH2: 同上，phase=90
6. **Phase 2**: X/Y 载波相位校准
7. **配置 Demod 3**：`DemodulatorConfig(demod_index=3, adcselect=2, osc_select=1, ...)`
8. **Phase 3**: Demod 3 射频场相位校准

### Phase A — 响应曲线测量（幅度扫描）
1. 将 Demod 3 配置为适合响应曲线测量的参数（TC=~1ms, rate=~1000Sa/s）
2. 幅度序列：`amplitudes = np.linspace(RF_AMP_START, RF_AMP_STOP, RF_AMP_POINTS)`
3. try/finally 保护扫描循环：
   ```python
   try:
       for i, amp in enumerate(amplitudes):
           # 设置 Z 射频场幅度
           dg_sweep.set_amplitude(amp, channel=1)
           time.sleep(RF_SETTLE_TIME)
           # 关闭温控
           dg_temp.set_output(False, channel=2)
           time.sleep(0.3)
           # 读取 Demod 3 的 X/Y/R
           sample = demod.read_demod_sample(hfi, demod_idx=3)
           # 保存数据
           # 恢复温控
           dg_temp.set_output(True, channel=2)
           time.sleep(1)
   finally:
       dg_temp.set_output(True, channel=2)
   ```
4. 绘制响应曲线 $R_3$ vs $B_\text{RF}$，色散拟合得到斜率 $\mathrm{d}R/\mathrm{d}B_\text{RF}$

### Phase B — 噪声测量与灵敏度计算
1. 确定最优工作点（响应曲线斜率最大处对应的 $B_\text{RF}=0$ 或接近 0）
2. 将 Demod 3 切换至噪声采集参数（更小 TC, 更高 rate）：
   ```python
   noise_demod_cfg = DemodulatorConfig(
       demod_index=3, enable=True, rate=NOISE_RATE,
       input_channel=2, osc_select=1,
       time_constant=1e-5, order=4,  # 小 TC 高带宽
       phase=calibrated_phase_3,
   )
   demod.configure_demodulator(hfi, noise_demod_cfg)
   ```
3. 关闭 Z 射频场输出，关闭温度开关
4. 采集 NOISE_N_AVG 次 Demod 3 噪声，逐次保存：
   ```python
   for n in range(NOISE_N_AVG):
       dg_temp.set_output(False, channel=2)
       time.sleep(0.3)
       # Demod 3 DAQ 采集或直接轮询读取
       noise = demod.read_demod_samples(...)  # 采集 Y 或 R 信号
       np.save(raw_dir / f"noise_D3_{n:04d}.npy", noise)
       dg_temp.set_output(True, channel=2)
       time.sleep(2)  # 恢复稳定
   ```
5. Welch 法计算各次 PSD，等权平均得 PSD_avg
6. **恢复 Demod 3 至测量配置**
7. 灵敏度计算：$\delta B_\text{RF}(f) = \sqrt{\text{PSD}_\text{avg}(f)} / |\mathrm{d}R/\mathrm{d}B_\text{RF}|$
8. 取色散线宽内低频段中位数作为灵敏度

## 输出数据

```
data/RF_Field_Sensitivity/
  MMDD_HHMM_rf/
    experiment_config.yaml        # 实验完整配置
    arb_waveform_source/          # 使用的任意波源文件
    raw/
      response_data.npz           # 响应曲线逐点数据（X₃, Y₃, R₃ vs B_RF）
      noise_D3_0000.npy           # Demod 3 噪声波形
      noise_D3_0001.npy
      ...
    results/
      response_fit.npz            # 响应曲线拟合参数（斜率等）
      psd_avg.npz                 # 平均 PSD + 频率轴
      sensitivity.npz             # 灵敏度曲线 δB(f)
      response_curve.png          # 响应曲线图
      sensitivity.png             # 灵敏度图
```

## 注意事项

- [经验] AM 外部调制源（dg_sweep CH2）必须与载波（dg_comp）共享 10MHz 参考时钟
- [经验] CH1/CH2 载波相位差 90° 通过 `set_phase_adjust()` 设置，首次实验建议用示波器验证
- [经验] **Demod 0 的 TC 必须尽可能小**（如 10μs 或更小），确保带宽覆盖射频场频率
- [经验] Demod 3 在响应曲线测量时可用较大 TC（~1ms）抑制噪声；噪声采集时改用更小 TC
- [经验] 噪声测量后**必须恢复** Demod 3 的解调器配置，否则第二次运行异常
- [经验] 响应曲线扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
- [经验] 每次噪声采集完立即恢复温度开关，防止温度漂移
- [经验] Demod 3 的 `adcselect = 2` 将 Demod 0 的 Y 输出路由为输入
- [经验] 两级解调相位校准相互独立，先 Demod 0 再 Demod 3
