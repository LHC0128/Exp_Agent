---
title: X/Y 交流控制噪声谱测量
type: experiment_type
description: 扫描 X/Y 补偿线圈的交流控制场幅度，测量不同控制强度下的功率谱密度 $S_{S_1}(\omega, \Omega_\text{Ctrl})$，通过二维拟合同时提取原子噪声谱 $S_\beta(\omega)$ 和光子噪声 $N_{S_1}(\omega)$，为 Floquet 最优控制提供输入
keywords: [noise spectrum, XY control, PSD, Welch, Lorentzian, oscilloscope, burst mode, Floquet, optimal control, noise spectroscopy]
version: 1
paper_ref: paper/optimal_control.tex

scan_mode: point_by_point      # 逐点扫描 X/Y 幅度

# ========== 默认参数 ==========
defaults:
  # ---- 扫描参数 ----
  XY_AMP_START: 0.01           # X/Y 幅度扫描起始 (V)
  XY_AMP_STOP: 7.0             # X/Y 幅度扫描终止 (V)
  XY_AMP_POINTS: 700           # 扫描点数
  XY_SETTLE_TIME: 5.0          # 每点等待稳定时间 (s)
  # ---- X/Y 交流控制信号 ----
  XY_CTRL_FREQ: 9000           # X/Y 交流控制频率 (Hz)
  XY_CTRL_PHASE: 100           # X/Y 控制信号相位 (deg)，Y 相位 = X 相位 + 90°
  # ---- HF2 解调配置（相位校准用） ----
  HF2_DEMOD_IDX: 0             # 解调器索引
  HF2_DEMOD_TC: 0.000692       # 解调时间常数 (s)
  # ---- HF2 DAQ 采集配置（噪声采集用） ----
  HF2_DAQ_DURATION: 1.0        # DAQ 采集时长 (s)
  HF2_DEMOD_RATE: 500000       # 解调输出数据速率 (Sa/s)，实际值从设备回读
  HF2_NPERSEG: 20000           # Welch PSD 每段点数
  # ---- 固定参数 ----
  PUMP_LASER_POWER: 1.0        # Pump 光功率 DC (V)
  PROBE_LASER_POWER: 1.0       # Probe 光功率 DC (V)
  MAIN_FIELD_mA: 9.285         # 主磁场 (mA)

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  X_magnetic_field:
    role: scan_xy_amp
    description: "DG4000 输出 9kHz 突发正弦波，幅度扫描变量，与 Y 相位差 90°"
  Y_magnetic_field:
    role: scan_xy_amp
    description: "DG4000 输出 9kHz 突发正弦波，幅度与 X 同步扫描，相位 X+90°"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流），实验过程中固定不变"
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
    description: "温度开关，初始化和相位校准时管理开关状态"
  lockin_r:
    role: phase_calibration
    description: "HF2 锁相，用于 X/Y 相位自动校准反馈"
  scope_waveform:
    role: noise_acquisition
    description: "HF2 DAQ 模块采集解调输出时域信号，计算 PSD"

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
  - 每次幅度变化后需等待足够时间（5s）让系统稳定
  - 优先使用 DG4000 内置 Burst 模式（setup_burst）而非 draw_waveform 下载波形
  - 使用 demod.auto_calibrate_phase() 做相位校准，无需复杂反馈闭环
  - 拟合前做预筛选（PSD 峰 > 2σ 再做拟合），失败点用插值填充
  - 用 .npz 替代 CSV 保存数据，保留 NaN 且 I/O 更快
  - 提取的噪声谱保存为 noise_spectra.npz，供最优控制设计 Notebook 加载
---

# X/Y 交流控制噪声谱测量

## 论文对照

本文档对应论文 `paper/optimal_control.tex` 的噪声谱估计实验部分（Figure 3 / NoiseSpecm）：

| 论文中的符号 | 实验实现 | 说明 |
|-------------|---------|------|
| $\Omega_\text{Ctrl}$ | X/Y 交流控制幅度 | 通过 X/Y 线圈施加交流磁场，幅度为扫描变量 |
| $S_{S_1}(\omega, \Omega_\text{Ctrl})$ | Welch PSD of HF2 解调信号 | 每个幅度点采集的噪声 PSD |
| $S_\beta(\omega)$ | 可调噪声（原子噪声） | 二维拟合提取的目标 1 |
| $N_{S_1}(\omega)$ | 不可调噪声（光子噪声 + 电子噪声） | 二维拟合提取的目标 2 |
| $L(\omega, \Omega_\text{Ctrl})$ | 洛伦兹滤波函数 | 控制场调制的系统响应函数 |

## 原理

在 Bell-Bloom 磁力仪中，探测光 $S_1$ 的功率谱密度由原子噪声和光子噪声共同贡献[论文 Eq. (5)]：

$$S_{S_1}(\omega) = N_{S_1}(\omega) + 4G^2 S_2^2 L(\omega, \Omega_\text{Ctrl}) S_\beta(\omega)$$

其中 $L(\omega, \Omega_\text{Ctrl})$ 是控制场调制的滤波函数。当 $\Omega_\text{Ctrl}=0$ 时退化为洛伦兹函数。由于 $\lim_{\omega\to\infty} L(\omega,0) \to 0$，不施加控制时高频段的 $S_\beta(\omega)$ 信息被 $N_{S_1}(\omega)$ 掩盖。

**核心思想**：通过扫描控制场幅度 $\Omega_\text{Ctrl}$，在不同控制强度下 $S_\beta(\omega)$ 受到 $L(\omega, \Omega_\text{Ctrl})$ 的不同调制，而 $N_{S_1}(\omega)$ 保持不变。因此可以通过二维拟合同时提取二者，实现宽带噪声谱重建。

### X/Y 交流控制信号

使用 DG4000 内置的 Burst 模式（`setup_burst()`）在 X/Y 线圈上施加正交交流磁场，替代通过 `draw_waveform()` 从 Python 生成波形再下载的方式：

```
X 通道: A · sin(2π · 9000 · t + θ)      → 硬件 Burst 模式生成
Y 通道: A · sin(2π · 9000 · t + θ + 90°) → 硬件 Burst 模式生成，正交分量
```

其中 A 为扫描变量（`XY_AMP_START` → `XY_AMP_STOP`），θ 为初始相位。

> 优势：硬件直接生成，频率/幅度/相位更精确，无需 Python 逐点计算并下载波形

### 可调 vs 不可调噪声分离

当 $|\omega - \Omega_\text{Ctrl}| \gg R + 1/T_1$ 时，$S_{S_1}(\omega)$ 主要包含 $N_{S_1}(\omega)$ 信息；
当 $\omega \approx \Omega_\text{Ctrl}$ 时，$S_{S_1}(\omega)$ 主要包含 $S_\beta(\omega)$ 信息。

二维拟合策略：
$$S_{S_1}(\omega, \Omega_\text{Ctrl}) = N_{S_1}(\omega) + 4G^2 S_2^2 L_{\text{Lorentz}}(\omega, \Omega_\text{Ctrl}) S_\beta(\omega)$$

### 幅频标定

控制幅度（V）与有效控制频率 $\Omega_\text{Ctrl}$（Hz）之间为线性关系：

$$\Omega_\text{Ctrl} = k \cdot V + b$$

通过峰检测 + 线性回归得到 $k, b$（对应旧代码中的 `kxbfitpopt`），将电压幅度转换为控制频率。标定结果用于后续洛伦兹拟合和最优控制优化。

### 噪声参数拟合

将 PSD 矩阵 $S_{S_1}[\text{幅度索引}, \text{频率索引}]$ 在每个频率点 $\omega$ 上对控制幅度做洛伦兹拟合（对应旧代码中的 `two_stage_fit`），提取参数：

$$S_{S_1}(\Omega_\text{Ctrl}; \omega) = N_{S_1}(\omega) + A \cdot \frac{\gamma^2 + \omega^2}{(\gamma^2 - \omega^2 + (\Omega_\text{Ctrl} + \Delta\omega)^2)^2 + 4\omega^2\gamma^2}$$

| 拟合参数 | 符号 | 论文对应 | 含义 |
|---------|------|---------|------|
| `gamma` | $\gamma$ | - | 洛伦兹线宽 (Hz) |
| `Amp` | $A$ | $4G^2 S_2^2 S_\beta(\omega)$ | 振幅 (V²/Hz) |
| `D` | $D$ | $N_{S_1}(\omega)$ | 基线噪声 = 光子噪声 + 电子噪声 (V²/Hz) |
| `dw` | $\Delta\omega$ | - | 控制频率偏移 (Hz) |

扫描完成后，提取的 $S_\beta(\omega)$ 和 $N_{S_1}(\omega)$ 作为 Floquet 最优控制优化（论文 Sec. Theoretical Model, Eq. (6)）的输入，用于设计最优控制波形。

分析 Cell 自动保存 `results/noise_spectra.npz`，可直接被"最优控制设计"Notebook 加载：

```python
# 在最优控制 Notebook 中加载噪声谱
data = np.load("results/noise_spectra.npz")
S_beta = data["S_beta"]          # 原子噪声谱 (V²/Hz)
N_S1 = data["N_S1"]              # 光子噪声谱 (V²/Hz)
freq_axis = data["freq_axis"]    # 频率轴 (Hz)
```

## 数据采集方式

逐点扫描 X/Y 幅度，每点通过 HF2 DAQ 模块采集解调信号后计算 PSD。

### 扫描策略

安全保护（与 XY 校准 notebook 一致）：逐点扫描 X/Y 幅度，每点通过 try/finally 确保异常时温度开关能恢复。

```python
# try/finally 确保异常时恢复温度开关
try:
    for i, amp in enumerate(amplitudes):
        amp = validate_safety_limit("X_magnetic_field", amp)   # 安全限值检查
        dg_comp.setup_burst(amp, freq=XY_CTRL_FREQ, ...)       # 设置 X/Y 幅度
        dg_temp.set_output(False)                               # 关闭温度开关
        time.sleep(XY_SETTLE_TIME)                              # 等待系统稳定
        
        # 配置 HF2 DAQ 采集参数（每点独立配置）
        daq_cfg = DAQConfig(duration=HF2_DAQ_DURATION, 
                           signal_paths=["sample.r"])
        
        # 采集解调时域信号并保存
        results = daq.acquire_data(instr, daq_cfg, 
                                   demod_idx=0, actual_rate=HF2_DEMOD_RATE)
        waveform = results[0].values
        np.save(raw_dir / f"waveform_C{i:03d}.npy", waveform)   # 二进制保存
        
        dg_temp.set_output(True)                                # 恢复温度开关
finally:
    dg_temp.set_output(True, channel=2)  # 确保异常退出时温控恢复
```

### HF2 DAQ 采集

HF2 的 DAQ（Data Acquisition）模块直接采集解调器输出的数字信号，无需像示波器那样逐点调节垂直量程：

1. **信号输入量程**：在初始化时通过 `SignalInputConfig.range` 一次配置，后续不再调整
2. **采样参数**：`HF2_DEMOD_RATE` 决定采样率，`HF2_DAQ_DURATION` 决定采集时长，总点数 = 采样率 × 时长
3. **采集执行**：通过 `daq.acquire_data()` 一键采集，返回结构化 `DAQResult`（含时间轴）

```python
from lockin_amplifier import DAQConfig, daq

daq_cfg = DAQConfig(
    duration=HF2_DAQ_DURATION,
    signal_paths=["sample.r"],       # 采集幅值信号
)
results = daq.acquire_data(
    instr, daq_cfg, demod_idx=0,
    actual_rate=HF2_DEMOD_RATE,
)
waveform = results[0].values         # 时域信号
time_axis = results[0].time          # 时间轴 (s)
```

> 优势：无示波器垂直刻度调节开销；HF2 直接输出数字信号，信噪比更高；接口统一为 `DAQResult` 格式，与后续 PSD 计算无缝衔接。

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4000 (DG4E234902522) | CH1 | 9kHz Burst 正弦波，幅度扫场（`setup_burst`） |
| Y 控制 | DG4000 (DG4E234902522) | CH2 | 9kHz Burst 正弦波，X+90°，幅度同步扫场（`setup_burst`） |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | RF 开关方案：100MHz 正弦 + 脉冲门控 |
| 主磁场 | GS200 | - | 恒流模式，~9.3 mA |
| Pump 光功率 | DG4000 (DG4E231500376) | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | DG4000 (DG4E231500376) | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | DG4000 (DG4E271200104) | CH2 | TTL 电平控制温控通断 |
| 噪声采集 | HF2 锁相 | 信号输入 0 | DAQ 模块采集解调信号（与锁相检测共用输入） |
| 相位校准 | HF2 锁相 | 解调器 0 | `auto_calibrate_phase()` 一步完成 |

## 实验流程

1. **连接所有设备**：GS200（主磁场）、DG4000×3（X/Y 控制、Pump 调制、光功率/温度开关）、TEC103（温度）、HF2（锁相 + DAQ 采集）
2. **设置初始条件**（try/finally 包裹）：
   - 关闭 Z、X、Y 输出
   - Pump/Probe 光功率 DC，`validate_safety_limit()` 检查
   - 主磁场（GS200 恒流 9.285 mA），调用 `set_current_limit()`
   - 温度开关 ON → 设定温度 → 等待稳定
3. **Pump 调制配置**（统一接口）：
   - `dg_mod.setup_pulse(freq=PUMP_MOD_FREQ, ...)` 替代旧 `BellBloom.initialize()`
   - `demod.configure_demodulator()` + `demod.auto_calibrate_phase()` 替代旧 `BellBloom.PhaseAdjust()`
4. **配置 X/Y 控制信号**：
   - 使用 `DG4000Instrument.setup_burst()` 硬件 Burst 模式
   - X/Y 相位差 90°，初始幅度设为最小值
5. **相位校准**：
   - 使用 `demod.auto_calibrate_phase()` 一步完成相位校准
6. **数据采集扫描**：见下方 "扫描策略" 代码示例（try/finally 保护温度开关 + safety check）
7. **数据分析 & 绘图**（可离线执行，采集与分析分离）：
   - 加载原始波形，计算每点 PSD（Welch 法）
   - 预筛选：仅在 PSD 峰 > 2σ 的频率点做洛伦兹拟合
   - 失败点用插值填充，避免绘图断点
   - 保存结果为 `.npz`（保留 NaN 类型信息）
   - 保存 `noise_spectra.npz`（含 $S_\beta(\omega)$ 和 $N_{S_1}(\omega)$），供最优控制设计使用

## 输出数据

### 运行目录结构

```
data/Noise_Spectrum_XY_Ctrl/
  MMDD_HHMM_noise/
    experiment_config.yaml      # 实验完整配置（mapping + 参数 + 扫描范围）
    raw/
      waveform_C000.npy         # 每点原始波形（numpy 二进制，比 CSV 快 10×）
      waveform_C001.npy
      ...
    results/
      psd_matrix.npz            # PSD 矩阵（amplitudes × frequencies）+ 频率轴
      calibration.npz           # 幅频标定结果（k, b, 峰位置等）
      popt_fit.npz              # 洛伦兹拟合参数（popt + perr + 拟合掩码）
      noise_spectra.npz         # ★ 提取的噪声谱（S_beta, N_S1, freq_axis）
      noise_spectrum_2d.png     # 控制幅度 × 频率的 PSD 伪彩图
      noise_spectrum_fit.png    # 洛伦兹拟合示例图
```

### 数据分析效率改进

- **预筛选**：只在 PSD 有明显共振峰的频率点做拟合（`max_val - median_val > 2 * std_val`）
- **失败填充**：拟合失败的数据点用相邻频率点的插值填充，避免绘图断点
- **二进制保存**：用 `.npz` 替代 CSV 保存 `poptlist` / `perrlist`，保留 NaN 且 I/O 更快

### 分析结果

每个频率点的 PSD 与控制强度的关系用洛伦兹函数拟合，输出参数：

| 参数 | 含义 |
|------|------|
| gamma | 洛伦兹线宽 (Hz) |
| Amp | 振幅 (V²/Hz) |
| D | 基线噪声 (V²/Hz) |
| dw | 频率偏移 (Hz) |

## 注意事项

- [经验] 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
- [经验] 每点 X/Y 幅度设置前调用 `validate_safety_limit()` 做安全限值检查
- [经验] HF2 DAQ 采集时长和采样率的选择需权衡频率分辨率与采集时间
- [经验] 信号输入量程（`SignalInputConfig.range`）一次配置即可，无需逐点调整
- [经验] 优先使用 DG4000 内置 Burst 模式（`setup_burst()`）替代 `draw_waveform()` 下载波形
- [经验] 使用 `demod.auto_calibrate_phase()` 实现相位校准，无需额外闭环
- [经验] 已改用 HF2 DAQ 模块采集噪声，无需示波器
- [经验] X/Y 幅度变化后需足够稳定时间（5s），避免瞬态效应影响噪声测量
- [经验] 拟合前做预筛选，失败点用插值填充而非设为 NaN
- [经验] 用 `.npz` 替代 CSV 保存矩阵数据，用 `.npy` 替代 CSV 保存波形
- [经验] 保存 `noise_spectra.npz` 供最优控制设计 Notebook 直接加载

