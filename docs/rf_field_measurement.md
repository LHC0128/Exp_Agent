---
title: 射频场测量
type: experiment_type
description: 利用 Bell-Bloom 磁力仪测量射频场的幅度和频率响应。通过 X/Y 方向线圈施加预计算的任意波形控制场，使用 HF2 双解调器级联结构（Demod 0 → Demod 3）提取射频场相关信息。
keywords: [RF field, Bell-Bloom, arbitrary waveform, double demodulation, cascaded demodulator, HF2 DAQ]
version: 1

scan_mode: point_by_point      # 每点加载任意波形文件并采集

# ========== 默认参数 ==========
defaults:
  # ---- 扫描参数 ----
  RF_AMP_START: 0.01           # 射频场幅度扫描起始 (V)
  RF_AMP_STOP: 5.0             # 射频场幅度扫描终止 (V)
  RF_AMP_POINTS: 200           # 扫描点数
  RF_SETTLE_TIME: 0.5          # 每点等待稳定时间 (s)
  # ---- 任意波形文件 ----
  ARB_WAVEFORM_DIR: "arb_waveforms/"  # 任意波形数据文件存放目录
  ARB_WAVEFORM_FILE: "rf_ctrl_waveform.csv"  # 默认波形文件名
  # ---- X/Y 控制信号 ----
  XY_CTRL_FREQ: 90000          # X/Y 控制载波频率 (Hz)，等于 Larmor 频率
  XY_CTRL_PHASE: 90            # X/Y 控制信号相对 Pump 调制的相位延迟 (deg)
  XY_CTRL_QUAD: 90             # X 与 Y 之间的正交相位差 (deg)
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 90000         # Pump 调制频率 (Hz)，与 Larmor 频率一致
  PUMP_MOD_DUTY: 5             # 脉冲占空比 (%)
  # ---- HF2 解调器 0 配置（主信号解调） ----
  DEMOD0_IDX: 0                # 解调器 0（主信号检波）
  DEMOD0_OSC_IDX: 0            # 振荡器 0，频率 = PUMP_MOD_FREQ
  DEMOD0_OSC_FREQ: 90000       # 振荡器 0 频率 (Hz)
  DEMOD0_SIGNAL_RANGE: 2.0     # 信号输入量程 (V)
  DEMOD0_ORDER: 4              # 解调滤波器阶数
  DEMOD0_TC: 0.000692          # 解调时间常数 (s)
  DEMOD0_RATE: 100000          # 解调输出数据速率 (Sa/s)
  # ---- HF2 解调器 3 配置（射频场解调） ----
  DEMOD3_IDX: 3                # 解调器 3（射频场检测）
  DEMOD3_OSC_IDX: 1            # 振荡器 1，频率 = 射频场频率
  DEMOD3_ADC_SELECT: 2         # 信号输入源: 2 = Demod 0 内部输出
  DEMOD3_ORDER: 8              # 解调滤波器阶数（射频场检测使用高阶滤波）
  DEMOD3_TC: 0.000692          # 解调时间常数 (s)
  DEMOD3_RATE: 1000            # 解调输出数据速率 (Sa/s)
  # ---- HF2 DAQ 采集配置 ----
  HF2_DAQ_DURATION: 1.0        # DAQ 采集时长 (s)
  HF2_DAQ_RATE: 1000           # DAQ 采样率 (Sa/s)
  # ---- 固定参数 ----
  PUMP_LASER_POWER: 0.1        # Pump 光功率 DC (V)
  PROBE_LASER_POWER: 0.1       # Probe 光功率 DC (V)
  MAIN_FIELD_mA: 9.305         # 主磁场 (mA)

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  X_magnetic_field:
    role: scan_rf_xy
    description: "DG4000 (DG4E234902522) CH1，任意波模式，加载预计算波形控制 X 方向射频场"
  Y_magnetic_field:
    role: scan_rf_xy
    description: "同一 DG4000 CH2，任意波模式，加载预计算波形控制 Y 方向射频场，相位 X+90°"
  Z_magnetic_field:
    role: off
    description: "本实验不使用，仅连接并关闭输出"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流 ~9.305 mA），实验过程中固定不变"
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
    description: "HF2 锁相：Demod 0 主信号解调 + Demod 3 射频场解调（输入来自 Demod 0 Y 输出）"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - main_magnetic_field
  - Pump_laser_power
  - Probe_laser_power
  - temperature
  - Temp_Switch
  - Z_magnetic_field

# ========== 修复经验 ==========
learned_notes:
  - 任意波形须提前计算并保存为数据文件（CSV 格式），实验时通过 DG4000 的 `setup_arb()` 加载
  - 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
  - 幅度变化使用 set_amplitude() 或重新加载波形文件，避免退出用户自定义模式
  - 每次幅度变化后需等待稳定时间（0.5s）让系统稳定
  - 外部触发同步：dg_mod CH2 SYNC → dg_comp Ext Trig，确保任意波与 Pump 调制固定相位关系
  - 解调器 3 的 `adcselect = 2` 将 Demod 0 的 Y 输出路由为 Demod 3 的信号输入（级联解调架构）
  - 射频场相位校准独立进行：配置好 Demod 3 后调用 `auto_calibrate_phase(demod_idx=3)` 
  - 双解调器架构：Demod 0 使用 OSC 0 在 Pump 频率解调，Demod 3 使用 OSC 1 在射频频率解调
  - Demod 0 的 output 通过 FPGA 内部路由至 Demod 3 的 input，无需外部跳线
  - 幅度扫描中使用 `set_amplitude()` 而非 `setup_sine()`，避免重绘波形导致相位跳变
---

# 射频场测量

## 论文对照

| 论文符号 | 实验实现 | 说明 |
|---------|---------|------|
| $B_\text{RF}$ | 射频场幅度 | X/Y 线圈产生的射频场，由任意波定义 |
| $S_{S_1}(\omega)$ | HF2 Demod 3 解调信号 | 双解调后的射频场响应信号 |
| $\Omega_\text{RF}$ | 射频 Rabi 频率 | 射频场有效驱动强度 |

## 原理

在 Bell-Bloom 磁力仪中，射频场通过 X/Y 补偿线圈施加到原子气室。与传统使用正弦 Burst 模式的噪声谱测量不同，本实验使用**预计算的任意波形**驱动 X/Y 线圈，实现更灵活的射频场控制。

### 双解调器级联架构

实验使用 HF2 锁相放大器的**双解调器级联**结构：

```
Signal Input 0 (光电探测器)
    │
    ▼
Demod 0 ─── 振荡器 0 (PUMP_MOD_FREQ ≈ 90 kHz)
    │         解调 Pump 调制信号，输出 X₀, Y₀
    │
    ├── X₀ → 实时幅频响应（主信号检波）
    │
    └── Y₀ → FPGA 内部路由 (adcselect=2)
                │
                ▼
            Demod 3 ─── 振荡器 1 (射频场频率 f_RF)
                         级联解调，提取射频场响应
                         输出 X₃, Y₃, R₃
```

**关键概念**：
- **Demod 0**：以 Pump 调制频率（~90 kHz）解调光电探测器信号，获取主 Bell-Bloom 信号
- **Demod 3**：将 Demod 0 的 Y 输出作为输入信号，以射频场频率 $f_\text{RF}$ 进行第二次解调，提取射频场响应

这种级联架构的优势在于，射频场对原子的影响表现为对 Bell-Bloom 信号的调制，通过二次解调可以高效提取该调制分量。

### 任意波形控制

X/Y 控制信号使用 DG4000 的**用户自定义任意波模式**（Arbitrary Waveform），波形数据预先计算并保存为文件：

1. **波形计算**（离线）：根据目标射频场特性，计算 X/Y 通道的任意波序列
2. **文件保存**：波形数据保存为 CSV 或二进制文件，存放在 `data/arb_waveforms/` 目录
3. **实验加载**：通过 `setup_arb()` 将波形文件加载到 DG4000，设置频率参数和幅度

```
任意波数据文件格式 (CSV):
    t(s), X_channel(V), Y_channel(V)
    0.0,  0.5,          0.0
    1e-6, 0.48,         0.02
    ...
```

波形文件命名规范：`<描述>_<频率>Hz_<幅度>V.csv`

### 相位校准流程

本实验包含两级相位校准：

**Phase 1 — Demod 0 自动相位校准**（与传统方案相同）：
1. 关闭温控、XYZ 磁场
2. 配置信号输入、振荡器 0、Demod 0
3. `auto_calibrate_phase(demod_idx=0)` 校准解调器 0 相位
4. 得到 `calibrated_phase_0`

**Phase 2 — Demod 3 射频场相位校准**（新增）：
1. 加载已知射频场任意波信号
2. 配置振荡器 1 频率 = 射频场指定频率
3. 配置 Demod 3（adcselect=2, osc_select=1）
4. 开启射频场输出
5. `auto_calibrate_phase(demod_idx=3)` 校准解调器 3 相位
6. 得到 `calibrated_phase_3`

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4000 (DG4E234902522) | CH1 | 任意波模式，加载预计算波形 |
| Y 控制 | DG4000 (DG4E234902522) | CH2 | 任意波模式，加载预计算波形，X+90° |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| SYNC 触发 | DG4000 (DG4E222800868) | CH2 SYNC → dg_comp Ext Trig | Pump 调制同步触发任意波 |
| 主磁场 | GS200 | - | 恒流模式，~9.3 mA |
| Pump 光功率 | DG4000 (DG4E231500376) | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | DG4000 (DG4E231500376) | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | DG4000 (DG4E271200104) | CH2 | TTL 电平控制温控通断 |
| 主信号解调 + 射频场解调 | HF2 锁相 | 信号输入 0 | Demod 0 + Demod 3 级联 |

## 实验流程

1. **连接所有设备**：GS200（主磁场）、DG4000×4（X/Y 控制、Pump 调制、光功率、温度开关）、TEC103（温度）、HF2（锁相）
2. **设置初始条件**：
   - 关闭 Z、X、Y 输出
   - Pump/Probe 光功率 DC，`validate_safety_limit()` 检查
   - 主磁场（GS200 恒流 ~9.3 mA），调用 `set_current_limit()`
   - 温度开关 ON → 设定温度（100°C）→ 等待稳定
3. **Pump 调制配置**：
   - CH1: 100MHz 连续正弦波 → RF 开关 IN
   - CH2: 脉冲门控（90 kHz, 5% duty）→ RF 开关 CTRL
   - CH2 SYNC ON → 用作 dg_comp 外部触发
4. **Demod 0 配置与相位校准**（Phase 1）：
   - 配置信号输入、振荡器 0、Demod 0（TC=0.692ms, rate=100kSa/s）
   - 关闭温控 → `auto_calibrate_phase(demod_idx=0)` → 恢复温控
5. **加载任意波形到 X/Y DG4000**：
   - `setup_arb()` 从预计算文件加载 X 通道波形
   - 加载 Y 通道波形（X+90° 相位）
   - 配置外部触发同步
6. **Demod 3 配置与射频场相位校准**（Phase 2）：
   - 配置振荡器 1 频率（射频场频率）
   - 配置 Demod 3（adcselect=2, osc_select=1, TC=0.692ms, rate=1kSa/s）
   - 开启 X/Y 输出 → 关闭温控 → `auto_calibrate_phase(demod_idx=3)` → 恢复温控
7. **数据采集扫描**（try/finally 保护）：
   - 按幅度序列逐点加载不同幅度的任意波形
   - 每点关闭温控 → 等待稳定 → 读取 Demod 3 的 X/Y/R → 保存数据 → 恢复温控
   - 可选：HF2 DAQ 采集 Demod 3 的时域波形
8. **数据分析**（可离线执行）：
   - 加载所有扫描点的 Demod 3 解调数据
   - 分析射频场响应曲线（幅度响应、相位响应）
   - 提取射频场参数（共振频率、幅度、线宽等）

## 输出数据

### 运行目录结构

```
data/RF_Field_Measurement/
  MMDD_HHMM_rf/
    experiment_config.yaml        # 实验完整配置
    arb_waveform_source/          # 使用的任意波形源文件
      rf_ctrl_waveform.csv
    raw/
      scan_data.npz               # Demod 3 逐点数据（X, Y, R, θ）
      waveform_D3_C000.npy        # 可选：Demod 3 DAQ 时域波形
      waveform_D3_C001.npy
      ...
    results/
      rf_response.npz             # 射频场响应曲线
      rf_peak_fit.npz             # 共振峰拟合参数
      analysis.yaml               # 分析结果汇总
      rf_response.png             # 射频场响应图
```

### 分析结果

| 参数 | 含义 |
|------|------|
| f_RF | 射频场共振频率 (Hz) |
| Amp_RF | 射频场共振幅度 (V) |
| gamma_RF | 射频场共振线宽 (Hz) |
| phase_RF | 射频场相位偏移 (deg) |

## 注意事项

- [经验] 任意波形需提前用外部脚本计算并保存，Notebook 仅负责加载和设置幅度
- [经验] 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
- [经验] 幅度变化后需重新加载波形或修改幅值系数，避免退出任意波模式
- [经验] Demod 3 的 `adcselect = 2` 将 Demod 0 输出路由至 Demod 3 输入
- [经验] 两级相位校准相互独立，先校准 Demod 0，再校准 Demod 3
- [经验] Demod 0 使用低 TC（高带宽）保留射频场调制信息，Demod 3 使用适当 TC 抑制噪声
- [经验] 外部触发同步 + 共享 10MHz 参考时钟保证任意波与 Pump 调制固定相位关系
- [经验] HF2 DAQ 采集可使用 demod_idx=3 的 sample.x, sample.y
- [经验] 实验中若更换任意波形文件，需重新执行 Demod 3 相位校准
