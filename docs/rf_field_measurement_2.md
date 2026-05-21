---
title: 射频场灵敏度测量（直接任意波方案）
type: experiment_type
description: 利用 Bell-Bloom 磁力仪测量 Z 方向射频场灵敏度。X/Y 控制场使用 DG4000 直接任意波模式，预计算 X(t)=A(t)·cos(ω_L·t) 和 Y(t)=A(t)·sin(ω_L·t) 加载到 Burst 模式。扫描 Z 射频场幅度获得响应曲线，结合 Demod 3 噪声 PSD 计算灵敏度。
keywords: [RF field, sensitivity, direct arbitrary waveform, Burst mode, cascaded demodulator, HF2 DAQ]
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
  # ---- X/Y 任意波参数 ----
  XY_CARRIER_FREQ: 90000       # X/Y 载波频率 (Hz)，等于 Larmor 频率
  XY_CTRL_PHASE: 0             # X/Y 控制信号相对 Pump 调制的相位延迟 (deg)
  XY_CTRL_QUAD: 90             # X 与 Y 之间的正交相位差 (deg)
  ARB_WAVEFORM_DIR: "arb_waveforms/"  # 任意波文件存放目录
  ARB_WAVEFORM_FILE: "xy_waveform.csv"  # 默认波形文件名
  ARB_SAMPLE_RATE: 500000      # 任意波采样率 (Sa/s)
  ARB_BUFFER_SIZE: 16000       # DG4000 内存点数（标准 16kpts）
  # ---- A(t) 包络参数 ----
  A_ENV_FREQ: 1000             # A(t) 包络频率 (Hz)
  A_ENV_AMPLITUDE: 1.0         # A(t) 包络幅度 (V)
  # ---- Z 射频场参数（dg_sweep CH1 Burst）----
  Z_RF_FREQ: 10000             # Z 射频场频率 (Hz)
  Z_RF_AMPLITUDE_INIT: 1.0     # Z 射频场幅度初始值 (Vpp)
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 90000         # Pump 调制频率 (Hz)，与 Larmor 频率一致
  PUMP_MOD_DUTY: 5             # 脉冲占空比 (%)
  # ---- HF2 解调器 0 配置（主信号解调，需尽可能小的 TC）----
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
    role: rf_xy_arb
    description: "DG4000 (DG4E234902522) CH1，直接任意波模式，输出 X(t)=A(t)·cos(ω_L·t)，Burst INFINITY + 外部触发"
  Y_magnetic_field:
    role: rf_xy_arb
    description: "同一 DG4000 CH2，直接任意波模式，输出 Y(t)=A(t)·sin(ω_L·t)，Burst INFINITY + 外部触发"
  Z_magnetic_field:
    role: rf_source
    description: "DG4000 (DG4E242401288) CH1，Burst 正弦波，输出 Z 方向射频场"
  Time_sequence_2:
    role: idle
    description: "本方案不使用，预留 DG4000 (DG4E242401288) CH2"
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
  - X(t) 和 Y(t) 在离线脚本中预计算为完整波形，直接输出到线圈，不含 AM 调制环节
  - A(t) 的信息已编码在 X(t)/Y(t) 任意波数据中
  - 需保证 16kpts 缓冲区内 A(t) 和载波都是整数周期，确保循环边界连续
  - 外部触发同步：dg_mod CH2 SYNC → dg_comp/dg_sweep Ext Trig（BNC 三通），Burst 与 Pump 相位对齐
  - 幅度变化使用 set_amplitude() 而非 setup_sine()，避免退出 Burst 模式导致相位跳变
  - Demod 0 的 TC 需尽可能小（≤10μs），使其带宽足够宽以响应射频场
  - 噪声测量后必须恢复解调器配置，否则第二次运行异常
  - 每次噪声采集完立即恢复温度开关，防止温度漂移
  - 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
  - 两级相位校准相互独立，先 Demod 0 再 Demod 3
  - Demod 3 的 adcselect=2 将 Demod 0 的 Y 输出路由为输入（级联解调）
---

# 射频场灵敏度测量（直接任意波方案）

## 原理

本实验与 AM 方案物理原理相同，区别在于 X/Y 控制使用**直接任意波**而非 AM 调制。X(t) = A(t)·cos(ω_L·t) 和 Y(t) = A(t)·sin(ω_L·t) 在离线脚本中预计算为完整波形，加载到 dg_comp CH1/CH2，Burst INFINITY + 外部触发实现相位同步。

灵敏度测量分为两个阶段：

**Phase A — 响应曲线测量**：扫描 Z 射频场幅度 → 读取 Demod 3 响应 → 拟合得到斜率 dR/dB_RF

**Phase B — 噪声测量与灵敏度计算**：在最优工作点采集 Demod 3 噪声 → Welch PSD → 灵敏度 δB_RF(f) = √PSD / |dR/dB_RF|

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

## 触发同步

```
dg_mod CH2 SYNC ──BNC三通──→ dg_comp Ext Trig  (Burst 触发)
                           ──→ dg_sweep Ext Trig (Z RF Burst 触发)
```

所有 DG4000 共享 10MHz 外部参考时钟，频率锁死，初始相位由触发沿对齐。

## 波形预计算

```python
"""离线波形生成脚本"""
import numpy as np

F_LARMOR = 90e3        # Larmor 频率 (Hz)
F_ENV = 1000.0         # A(t) 包络频率 (Hz)
FS = 500_000           # 采样率 (Sa/s)
N_PTS = 16000          # 缓冲区点数
PHASE = 0.0            # 初始相位 (deg)

T_BUFFER = N_PTS / FS  # 32ms @500kSa/s
t = np.linspace(0, T_BUFFER, N_PTS, endpoint=False)

# 包络 A(t) — 用户自定义
A_t = ...  # 由实验方案决定

# X/Y 波形
phi = np.deg2rad(PHASE)
X_t = A_t * np.cos(2*np.pi * F_LARMOR * t + phi)
Y_t = A_t * np.sin(2*np.pi * F_LARMOR * t + phi)

# 归一化到 [-1, 1]
X_norm = X_t / np.max(np.abs(X_t))
Y_norm = Y_t / np.max(np.abs(Y_t))
```

**A(t) 频率约束**：$f_\text{env} = N / T_\text{buffer}$，N 为正整数（500kSa/s 下 T_buffer=32ms）。

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4E234902522 (dg_comp) | CH1 | 任意波 X(t)，Burst INFINITY + 外部触发 |
| Y 控制 | DG4E234902522 (dg_comp) | CH2 | 任意波 Y(t)，Burst INFINITY + 外部触发 |
| Z 射频场（扫描变量） | DG4E242401288 (dg_sweep) | CH1 | Burst 正弦波，幅度为扫描变量 |
| DG_sweep CH2 | DG4E242401288 (dg_sweep) | CH2 | 本方案不使用，直流 0V 输出 |
| Pump 调制 | DG4E222800868 (dg_mod) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| SYNC 触发 | DG4E222800868 (dg_mod) | CH2 SYNC → BNC 三通 | 触发 dg_comp + dg_sweep Burst |
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

### Phase 2 — X/Y 任意波校相（Burst 相位对齐 Pump）
1. 加载 X(t)/Y(t) 任意波到 dg_comp
2. 配置 Burst INFINITY + 外部触发
3. 关闭温控，读 HF2 Demod 0 相位偏移
4. 迭代修正 `XY_CTRL_PHASE`（更新 `set_burst_phase`）直到收敛

### Phase 3 — Demod 3 校相（射频场对齐）
1. 开启 Z 射频场 Burst（dg_sweep CH1，小幅度）
2. 配置振荡器 1、Demod 3（adcselect=2, osc_select=1）
3. 关闭温控 → `auto_calibrate_phase(demod_idx=3)` → 恢复温控

## 实验流程

### 准备阶段
1. **连接所有设备**，设置初始条件
2. **Pump 调制配置**：CH1 100MHz 正弦 + CH2 脉冲门控，CH2 SYNC ON
3. **Phase 1**: Demod 0 自动相位校准
4. **离线波形预计算**：生成 X(t)/Y(t) 波形文件
5. **加载任意波到 dg_comp**：`send_arbitrary_waveform()` + `setup_arbitrary()` + Burst INFINITY + 外部触发
6. **Phase 2**: X/Y 任意波相位校准
7. **Phase 3**: Demod 3 射频场相位校准

### Phase A — 响应曲线测量
1. Demod 3 使用响应曲线参数（TC=~1ms, rate=~1000Sa/s）
2. 逐点扫描 Z 射频场幅度（RF_AMP_START → RF_AMP_STOP）
3. try/finally 保护，每点：设置幅度 → 关闭温控 → 读 Demod 3 → 恢复温控
4. 色散拟合得到斜率 dR/dB_RF

### Phase B — 噪声测量与灵敏度计算
1. 在响应曲线斜率最大点（或 B_RF=0 附近）关闭 Z 射频场
2. 切换 Demod 3 至噪声采集参数（更小 TC, 更高 rate）
3. 采集 NOISE_N_AVG 次噪声，Welch PSD 等权平均
4. **恢复 Demod 3 配置**
5. 灵敏度：δB_RF(f) = √PSD_avg(f) / |dR/dB_RF|

## 输出数据

```
data/RF_Field_Sensitivity/
  MMDD_HHMM_rf/
    experiment_config.yaml
    arb_waveform_source/
      xy_waveform.csv
    raw/
      response_data.npz
      noise_D3_0000.npy
      ...
    results/
      response_fit.npz
      psd_avg.npz
      sensitivity.npz
      response_curve.png
      sensitivity.png
```

## AM 方案 vs 直接任意波方案对比

| 方面 | AM 外部调制方案 | 直接任意波方案 |
|:---|:---|:---|
| 波形生成 | dg_comp 硬件 AM 调制，A(t) 来自外部 | 离线预计算 X(t)/Y(t) 整段波形 |
| A(t) 自由度 | 标准波形或 USER 任意波 | 任意（受 16kpts 限制） |
| 边界连续性 | ✅ 无边界问题（AM 本质连续） | ⚠️ 需 A(t) 和载波均整数周期 |
| 触发同步 | ❌ 无触发（AM 连续输出） | ✅ 外部触发 Burst，相位确定 |
| 幅度扫描 | 需重载 A(t) 波形 | ✅ `set_amplitude()` |
| CH1/CH2 相差 90° | ⚠️ 首次需示波器验证 | ✅ 已在数据文件中编码 |
| 额外设备占用 | 需 dg_sweep CH2 产生 A(t) | 无需（全部在 dg_comp） |
| 适用场景 | A(t) 为简单周期函数 | A(t) 复杂、需触发同步 |

## 注意事项

- [经验] 离线波形计算时必须确保缓冲区内 A(t) 和载波均为整数周期
- [经验] **Demod 0 的 TC 必须尽可能小**（10μs 或更小），确保带宽覆盖射频场频率
- [经验] 幅度变化只能使用 `set_amplitude()`，禁止 `setup_sine()`（会退出 Burst 模式）
- [经验] 外部触发同步 + 共享 10MHz 参考保证 Burst 与 Pump 固定相位关系
- [经验] 噪声测量后**必须恢复** Demod 3 的解调器配置
- [经验] 每次噪声采集完立即恢复温度开关，防止温度漂移
- [经验] 扫描循环须用 try/finally 包裹，异常时恢复温度开关
