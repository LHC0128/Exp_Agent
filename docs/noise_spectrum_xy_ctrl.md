---
title: X/Y 交流控制噪声谱测量
type: experiment_type
description: 扫描 X/Y 补偿线圈的交流控制场幅度，测量不同控制强度下的功率谱密度 $S_{S_1}(\omega, \Omega_\text{Ctrl})$，通过二维拟合同时提取可控噪声谱 $S_\beta(\omega)$ 和不可控噪声 $N_{S_1}(\omega)$
keywords: [noise spectrum, XY control, PSD, Welch, Lorentzian, burst mode, Floquet, noise spectroscopy, HF2 DAQ]
version: 2
paper_ref: paper/optimal_control.tex

scan_mode: point_by_point      # 逐点扫描 X/Y 幅度

# ========== 默认参数 ==========
defaults:
  # ---- 扫描参数 ----
  XY_AMP_START: 0.02           # X/Y 幅度扫描起始 (V)
  XY_AMP_STOP: 2.0             # X/Y 幅度扫描终止 (V)
  XY_AMP_POINTS: 100           # 扫描点数
  XY_SETTLE_TIME: 0.5          # 每点等待稳定时间 (s)
  # ---- X/Y 交流控制信号 ----
  XY_CTRL_FREQ: 90000          # X/Y 交流控制频率 (Hz)，等于 Larmor 频率
  XY_CTRL_PHASE: 90            # X/Y 控制信号相对 Pump 调制的相位延迟 (deg)
  XY_CTRL_QUAD: 90             # X 与 Y 之间的正交相位差 (deg)
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 90000         # Pump 调制频率 (Hz)，与 Larmor 频率一致
  PUMP_MOD_DUTY: 5             # 脉冲占空比 (%)
  # ---- HF2 解调配置（相位校准用） ----
  HF2_DEMOD_IDX: 0             # 解调器索引
  HF2_OSC_FREQ: 90000          # 振荡器频率 (Hz)，与 Larmor 频率一致
  HF2_SIGNAL_RANGE: 2.0        # 信号输入量程 (V)
  HF2_DEMOD_ORDER: 4           # 解调滤波器阶数
  HF2_DEMOD_TC: 0.000692       # 解调时间常数 (s)，相位校准用
  HF2_DEMOD_RATE: 100000       # 解调输出数据速率 (Sa/s)
  # ---- HF2 DAQ 采集配置（噪声采集用） ----
  HF2_DAQ_DURATION: 1.0        # DAQ 采集时长 (s)
  HF2_DAQ_TC: 7.85e-07         # 解调时间常数 (s)，噪声采集用（高带宽）
  HF2_DAQ_RATE: 100000         # DAQ 采样率 (Sa/s)
  HF2_NPERSEG: 10000           # Welch PSD 每段点数
  # ---- 固定参数 ----
  PUMP_LASER_POWER: 0.1        # Pump 光功率 DC (V)
  PROBE_LASER_POWER: 0.1       # Probe 光功率 DC (V)
  MAIN_FIELD_mA: 9.305         # 主磁场 (mA)

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  X_magnetic_field:
    role: scan_xy_amp
    description: "DG4000 (DG4E234902522) CH1，90 kHz Burst 正弦波，幅度扫描变量"
  Y_magnetic_field:
    role: scan_xy_amp
    description: "同一 DG4000 CH2，幅度与 X 同步扫描，相位 X+90°"
  Z_magnetic_field:
    role: off
    description: "本实验不使用，仅连接并关闭输出"
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
    role: phase_calibration + noise_acquisition
    description: "HF2 锁相：相位校准 + DAQ 模块采集解调输出 Y 信号"

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
  - 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
  - X/Y 幅度设置前调用 validate_safety_limit() 做安全限值检查
  - 幅度扫描中使用 set_amplitude() 而非 setup_sine()，避免重绘波形导致 Burst 模式退出和相位跳变
  - 每次幅度变化后需等待稳定时间（0.5s）让系统稳定
  - 外部触发同步：dg_mod CH2 SYNC → dg_comp Ext Trig，确保 Burst 与 Pump 调制固定相位关系
  - 相校准收敛后施加 +2° 偏置使少量信号混入 Y 通道，增强共振峰可见性
  - 使用 demod.auto_calibrate_phase() 做相位校准，无需复杂反馈闭环
  - 拟合前做预筛选（PSD 峰 > 2σ 再做拟合），失败点用插值填充
  - 用 .npz 替代 CSV 保存数据，保留 NaN 且 I/O 更快
  - 提取的噪声谱保存为 noise_spectra.npz，供最优控制设计 Notebook 加载
  - XY 控制频率为 90 kHz（Larmor 频率），Pump 调制频率与之同步
---

# X/Y 交流控制噪声谱测量

## 论文对照

本文档对应论文 `paper/optimal_control.tex` 的噪声谱估计实验部分：

| 论文中的符号 | 实验实现 | 说明 |
|-------------|---------|------|
| $\Omega_\text{Ctrl}$ | X/Y 交流控制幅度 → 有效 Rabi 频率 | 通过 X/Y 线圈施加交流磁场，幅度为扫描变量 |
| $S_{S_1}(\omega, \Omega_\text{Ctrl})$ | Welch PSD of HF2 解调 Y 信号 | 每个幅度点采集的噪声 PSD |
| $S_\beta(\omega)$ | 可调噪声（可控噪声） | 二维拟合提取的目标 1 |
| $N_{S_1}(\omega)$ | 不可调噪声（不可控噪声 + 电子噪声） | 二维拟合提取的目标 2 |
| $L(\omega, \Omega_\text{Ctrl})$ | 洛伦兹滤波函数 | 控制场调制的系统响应函数 |

## 原理

在 Bell-Bloom 磁力仪中，探测光 $S_1$ 的功率谱密度由可控噪声和不可控噪声共同贡献：

$$S_{S_1}(\omega) = N_{S_1}(\omega) + 4G^2 S_2^2 L(\omega, \Omega_\text{Ctrl}) S_\beta(\omega)$$

通过扫描控制场幅度 $\Omega_\text{Ctrl}$，在不同控制强度下 $S_\beta(\omega)$ 受到 $L(\omega, \Omega_\text{Ctrl})$ 的不同调制，而 $N_{S_1}(\omega)$ 保持不变。通过二维拟合同时提取二者。

### X/Y 交流控制信号

使用 DG4000 内置的 Burst 模式 + 外部触发同步：

```
dg_mod CH2 SYNC ──→ dg_comp Ext Trig  （硬件连接）
```

Pump 调制信号的 SYNC 输出触发 XY Burst 启动，确保固定相位关系：

```
X 通道: A · sin(2π · 90k · t + θ)      → Burst 模式，相位 θ
Y 通道: A · sin(2π · 90k · t + θ + 90°) → Burst 模式，相位 θ + 90°
```

其中 θ 在相校准中迭代确定。**Y 通道相位 = X 通道相位 + XY_CTRL_QUAD (≈ 90°)**。

### 相位校准流程

分两步：

1. **HF2 解调器自动相位校准**：`demod.auto_calibrate_phase()` 将信号旋转到 X 通道（Y ≈ 0）
2. **XY_CTRL_PHASE 迭代校准**：开启 XY 控制场后读取 HF2 相位偏移，迭代修正 XY Burst 相位直到收敛（|偏移| < 1°）
3. **施加 +2° 偏置**：收敛后故意偏移 2°，使少量信号混入 Y 通道以增强共振可见性

### 幅频标定

控制幅度（V）与有效控制频率 $\Omega_\text{Ctrl}$（Hz）之间为线性关系：

$$\Omega_\text{Ctrl} = k \cdot V + b$$

通过对 PSD 矩阵逐列寻峰 + 线性回归得到 k, b。

方法：对每个 PSD 分析频率 $\omega_j$，沿幅度轴找 PSD 峰值对应的电压 V_peak_j，然后拟合 (V_peak_j, $\omega_j$) 得到斜率 k。

### 噪声参数拟合

在每个频率点 $\omega$ 上对控制幅度做洛伦兹拟合：

$$S_{S_1}(\Omega_\text{Ctrl}; \omega) = N_{S_1}(\omega) + A \cdot \frac{\gamma^2 + \omega^2}{(\gamma^2 - \omega^2 + (\Omega_\text{Ctrl} + \Delta\omega)^2)^2 + 4\omega^2\gamma^2}$$

| 拟合参数 | 符号 | 含义 |
|---------|------|------|
| `gamma` | $\gamma$ | 洛伦兹线宽 (Hz) |
| `Amp` | $A$ | $\propto S_\beta(\omega)$，振幅 (V²/Hz) |
| `D` | $N_{S_1}(\omega)$ | 基线噪声 = 不可控噪声 + 电子噪声 (V²/Hz) |
| `dw` | $\Delta\omega$ | 控制频率偏移 (Hz) |

分析 Cell 自动保存 `results/noise_spectra.npz`，可直接被最优控制设计 Notebook 加载：

```python
data = np.load("results/noise_spectra.npz")
S_beta = data["S_beta"]          # 可控噪声谱 (V²/Hz)
N_S1 = data["N_S1"]              # 不可控噪声谱 (V²/Hz)
freq_axis = data["freq_axis"]    # 频率轴 (Hz)
```

## 数据采集方式

逐点扫描 X/Y 幅度，每点通过 HF2 DAQ 模块采集解调器 Y 信号后计算 PSD。

### 信号链

```
HF2 解调器 Y 输出（digital）→ DAQ 模块 → 时域波形
```

采集 `sample.y`（校相 + 偏置后的正交分量），包含可控噪声信息。

### 扫描策略

**关键**：幅度变化用 `set_amplitude()` 直接修改，禁止使用 `setup_sine()`（`:APPLy` 命令会退出 Burst 模式导致相位跳变）。

```python
# try/finally 确保异常时恢复温度开关
try:
    for i, amp in enumerate(amplitudes):
        # 只改幅度，不重绘波形，保持外部触发同步
        dg_comp.set_amplitude(amp, channel=1)
        dg_comp.set_amplitude(amp, channel=2)
        time.sleep(0.3)

        dg_temp.set_output(False, channel=2)   # 关闭温度开关
        time.sleep(XY_SETTLE_TIME)             # 等待系统稳定

        # HF2 DAQ 采集 Y 信号
        daq_cfg = DAQConfig(
            duration=HF2_DAQ_DURATION,
            signal_paths=["sample.y"],         # Y 通道（噪声 + 偏置信号分量）
        )
        results = daq.acquire_data(hfi, daq_cfg, demod_idx=0, ...)
        waveform = results[0].values
        np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

        dg_temp.set_output(True, channel=2)    # 恢复温度开关
finally:
    dg_temp.set_output(True, channel=2)        # 确保异常退出时温控恢复
```

### 外部触发同步

```
dg_mod (DG4E222800868):
  CH1: 100MHz 正弦波 → RF 开关 IN
  CH2: 脉冲门控 (90 kHz, 5% duty) → RF 开关 CTRL
  CH2 SYNC → dg_comp Ext Trig

dg_comp (DG4E234902522):
  Ext Trig 触发 → X/Y Burst 同步启动
```

所有 DG4000 共享外部 10 MHz 参考时钟（`set_ref_clock_source("EXTernal")`），保证长期相位稳定。

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4000 (DG4E234902522) | CH1 | 90 kHz Burst 正弦波，幅度扫场 |
| Y 控制 | DG4000 (DG4E234902522) | CH2 | 90 kHz Burst 正弦波，X+90°，幅度同步扫场 |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| SYNC 触发 | DG4000 (DG4E222800868) | CH2 SYNC → dg_comp Ext Trig | Pump 调制同步触发 XY Burst |
| 主磁场 | GS200 | - | 恒流模式，~9.3 mA |
| Pump 光功率 | DG4000 (DG4E231500376) | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | DG4000 (DG4E231500376) | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | DG4000 (DG4E271200104) | CH2 | TTL 电平控制温控通断 |
| 噪声采集 + 相位校准 | HF2 锁相 | 信号输入 0 | 解调器 + DAQ 模块 |

## 实验流程

1. **连接所有设备**：GS200（主磁场）、DG4000×4（X/Y 控制、Pump 调制、光功率、温度开关）、TEC103（温度）、HF2（锁相 + DAQ）
2. **设置初始条件**：
   - 关闭 Z、X、Y 输出
   - Pump/Probe 光功率 DC，`validate_safety_limit()` 检查
   - 主磁场（GS200 恒流 ~9.3 mA），调用 `set_current_limit()`
   - 温度开关 ON → 设定温度（100°C）→ 等待稳定
3. **Pump 调制配置**：
   - CH1: 100MHz 连续正弦波 → RF 开关 IN
   - CH2: 脉冲门控（90 kHz, 5% duty）→ RF 开关 CTRL
   - CH2 SYNC ON → 用作 dg_comp 外部触发
4. **HF2 解调器配置与相位校准**：
   - 配置信号输入、振荡器、解调器（TC=0.692ms）
   - `auto_calibrate_phase()` 一步完成解调器相位校准
5. **配置 X/Y 控制信号**：
   - `setup_sine()` 初始配置正弦波
   - Burst 模式：INFinity, 50000 cycles, 外部触发
   - X/Y 相位差 90°，整体相位待后续校准
6. **XY_CTRL_PHASE 迭代校准**：
   - 开启 XY 输出 → 关闭温控 → 读 HF2 相位偏移
   - 修正 XY Burst 相位 → 重复直到 |偏移| < 1°
   - 收敛后施加 +2° 偏置
7. **数据采集扫描**（try/finally 保护）：
   - `set_amplitude()` 逐点设置幅度（保持 Burst 模式 + 相位稳定性）
   - 每点关闭温控 → 等待稳定 → HF2 DAQ 采集 Y 信号 → 恢复温控
8. **数据分析**（可离线执行）：
   - 加载原始波形，Welch 法计算 PSD 矩阵
   - 逐列寻峰 → 线性回归 → 幅频标定
   - 逐频率点洛伦兹拟合 → 提取 S_beta, N_S1
   - 保存 `noise_spectra.npz` 供最优控制设计使用

## 输出数据

### 运行目录结构

```
data/Noise_Spectrum_XY_Ctrl/
  MMDD_HHMM_noise/
    experiment_config.yaml      # 实验完整配置
    raw/
      waveform_C000.npy         # 每点原始波形（numpy 二进制）
      waveform_C001.npy
      ...
    results/
      psd_matrix.npz            # PSD 矩阵（amplitudes × frequencies）+ 频率轴
      calibration.npz           # 幅频标定结果（k, b, 峰位置）
      popt_fit.npz              # 洛伦兹拟合参数（popt + perr + 拟合掩码）
      noise_spectra.npz         # ★ 提取的噪声谱（S_beta, N_S1, freq_axis）
      calibration.png           # 标定图（散点 + 拟合 + PSD 伪彩图 + 验证）
      noise_spectrum_2d.png     # 控制幅度 × 频率的 PSD 伪彩图
      noise_spectrum_lines.png  # 典型幅度的 PSD 线图
      noise_spectra_extracted.png # 提取的可控/不可控噪声谱
```

### 分析结果

每个频率点的 PSD 与控制强度的关系用洛伦兹函数拟合，输出参数：

| 参数 | 含义 |
|------|------|
| gamma | 洛伦兹线宽 (Hz) |
| Amp | $\propto S_\beta(\omega)$，振幅 (V²/Hz) |
| D | $N_{S_1}(\omega)$，基线噪声 (V²/Hz) |
| dw | 频率偏移 (Hz) |

## 注意事项

- [经验] 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
- [经验] 幅度变化**必须使用** `set_amplitude()`，绝对禁止 `setup_sine()`（:APPLy 命令会退出 Burst 模式，导致相位跳变数十度）
- [经验] 相校准收敛后施加 +2° 偏置让少量信号混入 Y 通道，否则 Y≈0 时噪声峰过多
- [经验] XY 控制频率 = 90 kHz（Larmor 频率），与 Pump 调制频率一致
- [经验] 外部触发同步 + 共享 10MHz 参考时钟保证长期相位稳定
- [经验] 每点 XY 幅度设置前调用 `validate_safety_limit()` 做安全限值检查
- [经验] HF2 DAQ 采集时长和采样率的选择需权衡频率分辨率与采集时间
- [经验] 拟合前做预筛选，失败点用插值填充而非设为 NaN
- [经验] 用 `.npz` 替代 CSV 保存矩阵数据，用 `.npy` 替代 CSV 保存波形
- [经验] 保存 `noise_spectra.npz` 供最优控制设计 Notebook 直接加载
