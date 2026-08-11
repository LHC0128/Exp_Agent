---
title: X/Y 交流控制噪声谱测量
type: Noise_Spectrum_XY_Ctrl
execution_mode: typed_workflow
description: 按目标控制频率范围反算 X/Y DirectAW 包络电压，测量功率谱密度 $S_{S_1}(\omega, \Omega_\text{Ctrl})$，并提取可控噪声谱 $S_\beta(\omega)$ 和不可控噪声 $N_{S_1}(\omega)$
keywords: [noise spectrum, DirectAW, XY control, PSD, Welch, Lorentzian, noise spectroscopy, HF2 DAQ]
version: 3
paper_ref: paper/optimal_control.tex

scan_mode: point_by_point

# ========== 默认参数 ==========
defaults:
  TARGET_NOISE_FREQ_START_HZ: 0.0
  TARGET_NOISE_FREQ_STOP_HZ: 50000.0
  TARGET_NOISE_FREQ_POINTS: 500
  XY_CTRL_K_HZ_PER_V: 15075.784562638912
  XY_CTRL_B_HZ: -218.47506313463893
  XY_CALIBRATION_MIN_ENVELOPE_V: 0.0
  XY_CALIBRATION_MAX_ENVELOPE_V: 4.0
  XY_AW_OUTPUT_VPP: 8.0
  XY_AW_OUTPUT_OFFSET_V: 0.0
  XY_AW_REPEAT_FREQ_HZ: 500.0
  XY_AW_POINTS: 10000
  XY_SETTLE_TIME: 0.5
  XY_CTRL_FREQ: 10000
  XY_CTRL_PHASE: 90
  XY_CTRL_QUAD: 90
  XY_CALIB_ENVELOPE_V: 1.5
  XY_PHASE_CAL_TOL_DEG: 1.0
  XY_PHASE_CAL_MAX_ITER: 10
  XY_PHASE_CAL_MIN_R_V: 1.0e-12
  XY_PHASE_CAL_MIN_R_RATIO: 0.1
  # ---- Pump 调制参数 ----
  PUMP_MOD_FREQ: 10000
  PUMP_MOD_DUTY: 5             # 脉冲占空比 (%)
  # ---- HF2 解调配置（相位校准用） ----
  HF2_DEMOD_IDX: 0             # 解调器索引
  HF2_OSC_FREQ: 10000          # 振荡器频率 (Hz)，与 Pump 调制频率一致
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
    role: direct_aw_x
    description: "DG4000 CH1，固定 Vpp/Offset 的 DirectAW X 波形"
  Y_magnetic_field:
    role: direct_aw_y
    description: "同一 DG4000 CH2，DirectAW Y 波形，与 X 正交 90°"
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
  - 目标 Omega_ctrl 使用 V_env = (Omega_ctrl - B) / K 反算
  - 目标范围反算后必须位于手动填写的标定有效电压范围内
  - 离线分析从 PSD 脊线重新标定 K/B，不使用采集配置中的预设 K/B
  - PSD 脊线标定用 Theil-Sen 初值和 3×MAD 迭代筛选，剔除明显偏离线性的峰簇
  - DirectAW 固定 Vpp/Offset，扫描只改变归一化上传数组
  - dg_am 在 DirectAW 校相前打开并同相初始化，之后保持连续输出；每轮先开 dg_comp Burst，再开 dg_comp Output
  - 每次目标频率对应的 DirectAW 波形重新上传后需等待稳定时间（0.5s）
  - 外部触发同步：dg_am CH1/CH2 同相方波 → dg_comp Ext Trig
  - Demod0 与 DirectAW 校相使用 lab_workflows.steps 中的共享算法，避免各实验的更新方向和终止条件漂移
  - DirectAW 校相拒绝低于绝对下限或历史最佳 R 的 10% 的样本，不用弱信号 theta 更新相位
  - 拟合前做预筛选（PSD 峰 > 2σ 再做拟合），失败点用插值填充
  - 用 .npz 替代 CSV 保存数据，保留 NaN 且 I/O 更快
  - 提取的噪声谱保存为 noise_spectra.npz，供最优控制设计 Notebook 加载
  - XY 控制频率为 10 kHz，与 Pump 调制频率同步
---

# X/Y 交流控制噪声谱测量

本实验已迁移到强类型新模式。显式参数模型、采集工作流和离线分析器位于
`lab_workflows/experiment_modules/noise_spectrum_xy/`；
`experiments/Noise_Spectrum_XY_Ctrl.py` 与对应 plot 文件仅为薄入口。GUI 表单字段
只来自模型声明，不会再因工作流中新增全大写运行时常量而发生变化；历史大写键和
`FIXED_PARAMS.*` 键继续兼容。

## 论文对照

本文档对应论文 `paper/optimal_control.tex` 的噪声谱估计实验部分：

| 论文中的符号 | 实验实现 | 说明 |
|-------------|---------|------|
| $\Omega_\text{Ctrl}$ | GUI 中设置的目标控制频率 | 采集时由手动参数反算 DirectAW 包络；分析时由 PSD 脊线重新标定 |
| $S_{S_1}(\omega, \Omega_\text{Ctrl})$ | Welch PSD of HF2 解调 Y 信号 | 每个目标控制频率点采集的噪声 PSD |
| $S_\beta(\omega)$ | 可调噪声（可控噪声） | 二维拟合提取的目标 1 |
| $N_{S_1}(\omega)$ | 不可调噪声（不可控噪声 + 电子噪声） | 二维拟合提取的目标 2 |
| $L(\omega, \Omega_\text{Ctrl})$ | 洛伦兹滤波函数 | 控制场调制的系统响应函数 |

## 原理

在 Bell-Bloom 磁力仪中，探测光 $S_1$ 的功率谱密度由可控噪声和不可控噪声共同贡献：

$$S_{S_1}(\omega) = N_{S_1}(\omega) + 4G^2 S_2^2 L(\omega, \Omega_\text{Ctrl}) S_\beta(\omega)$$

通过扫描目标控制频率 $\Omega_\text{Ctrl}$，在不同控制强度下 $S_\beta(\omega)$ 受到 $L(\omega, \Omega_\text{Ctrl})$ 的不同调制，而 $N_{S_1}(\omega)$ 保持不变。通过二维拟合同时提取二者。

### X/Y 交流控制信号

X/Y 两路使用固定 `8 Vpp / 0 V Offset` 的 DirectAW。每个扫描点只改变归一化数组中的包络，不修改仪器的 Vpp 和 Offset：

```
X 通道: V_env · sin(2π · 10k · t + θ)
Y 通道: V_env · sin(2π · 10k · t + θ + 90°)
```

`dg_am CH1/CH2` 先打开并同相初始化，之后在校相和扫描期间保持连续输出。每次重新上传 X/Y 波形时只操作 `dg_comp`：先打开 Burst，再打开 Output，等待后续外触发正沿。θ 在相位校准中迭代确定。

### 相位校准流程

分两步：

1. **HF2 Demod0 校相**：共享 `calibrate_demod_phase()` 读取 theta、更新 Demod0 phase shift，将信号旋转到 X 通道（Y ≈ 0）
2. **XY_CTRL_PHASE 迭代校准**：共享 `calibrate_direct_aw_phase()` 每次把修正后的相位写入 X/Y DirectAW 数组，重新上传后依次打开 Burst 和 Output，等待持续运行的外触发源给出后续正沿，直到 HF2 相位偏移收敛（|偏移| < 1°）

DirectAW 每次测量还会检查 `R`：阈值取 GUI 中的绝对下限
`XY_PHASE_CAL_MIN_R_V` 与“本轮历史最佳 R × XY_PHASE_CAL_MIN_R_RATIO”两者中的较大值。
低于阈值的 theta 会记录为 `low_r_rejected`，但不会用于更新相位。

### DirectAW 采集标定与离线重标定

DirectAW 包络电压绝对值与有效控制频率 $\Omega_\text{Ctrl}$（Hz）之间采用线性标定：

$$\Omega_\text{Ctrl} = K \cdot |V_\text{env}| + B$$

运行前在 GUI 中手动填写 `K`、`B` 和标定有效包络范围，用于生成采集扫描轴。目标轴按

$$|V_\text{env}| = (\Omega_\text{Ctrl}-B)/K$$

反算。任一点超出标定有效范围或固定 AW 可表达范围时，预检直接报错并提示修改目标噪声谱频率范围。

离线分析不把上述预设 `K/B` 当作最终标定。分析器对 PSD 矩阵逐频率列寻峰，
先按相同包络电压汇总峰频率中位数并用 Theil-Sen 得到鲁棒初值，再按
`3×MAD` 残差阈值迭代剔除明显偏离线性的峰，最终重新拟合
$\Omega_\text{Ctrl}=K|V_\text{env}|+B$。如果有效峰不足，分析直接报错，不回退到
预设斜率或截距。保留点、剔除点、筛选掩码和残差尺度均写入
`results/calibration.npz`。

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

默认按 0–50 kHz、500 点扫描目标 $\Omega_\text{Ctrl}$，每点反算 X/Y 包络并通过 HF2 DAQ 模块采集解调器 Y 信号。

### 信号链

```
HF2 解调器 Y 输出（digital）→ DAQ 模块 → 时域波形
```

采集 `sample.y`（校相 + 偏置后的正交分量），包含可控噪声信息。

### 扫描策略

每个目标频率点生成 X/Y 正交波形，按固定 Vpp/Offset 归一化后上传；`dg_am` 外触发保持常开，`dg_comp` 两路依次进入 Burst 并打开输出。

```python
# try/finally 确保异常时恢复温度开关
try:
    for i, (target_hz, envelope_v) in enumerate(scan_axes):
        upload_direct_aw(envelope_v, XY_CTRL_PHASE, outputs_on=True)

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
  CH2: 脉冲门控 (10 kHz, 5% duty) → RF 开关 CTRL

dg_am:
  CH1/CH2: 同相 100 Hz 方波 → dg_comp CH1/CH2 Ext Trig

dg_comp (DG4E234902522):
  Ext Trig 触发 → X/Y DirectAW Burst 同步启动
```

参考时钟目标以 `params/devices.yaml` 中当前绑定设备的 `reference_clock` 为准。
工作流统一设置并回读验证，
保证参考时钟配置与仓库声明一致。

## 硬件连接

| 信号 | 仪器 | 通道 | 说明 |
|------|------|------|------|
| X 控制 | DG4000 (DG4E234902522) | CH1 | 10 kHz DirectAW，固定 8 Vpp / 0 V Offset |
| Y 控制 | DG4000 (DG4E234902522) | CH2 | 10 kHz DirectAW，与 X 正交 90° |
| Pump 调制 | DG4000 (DG4E222800868) | CH1+CH2 | RF 开关：100MHz 正弦 + 脉冲门控 |
| X/Y 外触发 | dg_am | CH1/CH2 → dg_comp CH1/CH2 Ext Trig | 校相前打开并同相初始化，之后持续输出 |
| 主磁场 | GS200 | - | 恒流模式，~9.3 mA |
| Pump 光功率 | DG900 (DG9Q280100002) | CH1 DC | Pump 激光功率控制 |
| Probe 光功率 | DG900 (DG9Q280100002) | CH2 DC | Probe 激光功率控制 |
| 温度控制 | TEC103 | - | 气室温度控制 |
| 温度开关 | DG900 (DG9Q271200104) | CH2 | 5 V/0 V DC 控制，物理输出始终 ON |
| 噪声采集 + 相位校准 | HF2 锁相 | 信号输入 0 | 解调器 + DAQ 模块 |

## 实验流程

1. **连接所有设备**：GS200（主磁场）、DG4000×4（X/Y 控制、外触发、Z 场、Pump 调制）、DG900×2（光功率、温度开关）、TEC103（温度）、HF2（锁相 + DAQ）
2. **设置初始条件**：
   - 关闭 Z、X、Y 输出
   - Pump/Probe 光功率 DC，`validate_safety_limit()` 检查
   - 主磁场（GS200 恒流 ~9.3 mA），调用 `set_current_limit()`
   - 温度开关 ON → 设定温度（100°C）→ 等待稳定
3. **Pump 调制配置**：
   - CH1: 100MHz 连续正弦波 → RF 开关 IN
   - CH2: 脉冲门控（10 kHz, 5% duty）→ RF 开关 CTRL
4. **HF2 解调器配置与相位校准**：
   - 配置信号输入、振荡器、解调器（TC=0.692ms）
   - 调用共享 `calibrate_demod_phase()` 完成 Demod0 相位校准
5. **目标范围与标定预检**：
   - GUI 输入目标 $\Omega_\text{Ctrl}$ 范围和点数，默认 0–50 kHz、500 点
   - 使用手动填写的 `K/B` 反算包络电压
   - 校验包络是否位于标定有效范围及固定 8 Vpp / 0 V 可表达范围
6. **XY_CTRL_PHASE 迭代校准**：
   - 以 `XY_CALIB_ENVELOPE_V` 生成并上传 X/Y DirectAW
   - 关闭温控 → 读 HF2 相位偏移 → 修正数组中的载波相位
   - `dg_am` 保持常开，两路重新上传后依次打开 Burst 和 Output，重复直到 |偏移| < 1°
   - 低 R 样本只记录诊断信息，不参与相位修正
7. **数据采集扫描**（try/finally 保护）：
   - 逐点按目标频率反算包络，生成归一化 X/Y DirectAW 数组
   - 保持外触发连续运行 → 上传两路 → 打开 Burst → 打开 Output
   - 每点关闭温控 → 等待稳定 → HF2 DAQ 采集 Y 信号 → 恢复温控
8. **数据分析**（可离线执行）：
   - 加载原始波形，Welch 法计算 PSD 矩阵
   - 新旧数据都从 PSD 脊线重新标定 $K/B$，不使用采集配置中的预设值覆盖标定
   - 用 Theil-Sen 初值和 `3×MAD` 残差筛选去除明显偏离线性的峰簇
   - 逐频率点洛伦兹拟合 → 提取 S_beta, N_S1
   - 保存 `noise_spectra.npz` 供最优控制设计使用

## 输出数据

### 运行目录结构

```
data/Noise_Spectrum_XY_Ctrl/
  MMDD_HHMM_noise/
    experiment_config.yaml      # 实验完整配置
    raw/
      control_scan_axes.npz     # 目标频率轴、反算包络轴和手动 K/B
      waveform_C000.npy         # 每点原始波形（numpy 二进制）
      waveform_C001.npy
      ...
    results/
      psd_matrix.npz            # PSD 矩阵（amplitudes × frequencies）+ 频率轴
      calibration.npz           # PSD 脊线重标定的 k、b、筛选掩码、剔除点及 Omega_ctrl 轴
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

## 统一安全收尾

正常完成、异常、取消和 Ctrl+C 均调用共享 `run_safety_shutdown()`：归零并关闭
X/Y DirectAW、外触发和 Z 场通道，恢复温度开关，只断开 TEC；主磁场、Pump/Probe
光功率、Pump 调制和全部 HF2 设置保持不变。连接阶段失败则释放已建立的全部连接。
- [经验] 8 Vpp 重标定后须手动同步更新 `XY_CTRL_K_HZ_PER_V`、`XY_CTRL_B_HZ` 和标定有效包络范围
- [经验] 手动 K/B 只负责生成安全的采集扫描轴；离线分析使用 PSD 脊线鲁棒重标定结果
- [经验] 不允许在标定范围外外推；预检报错时应修改目标噪声谱频率范围或重新标定
- [经验] DirectAW 扫描保持 8 Vpp / 0 V Offset，只改变归一化波形数组
- [经验] dg_am 外触发在校相前打开并同相初始化，迭代和扫描期间保持常开
- [经验] XY 控制频率 = 10 kHz，与 Pump 调制频率一致
- [经验] 外部触发同步 + 设备库声明的 10 MHz 主从时钟配置保证长期相位稳定
- [经验] 固定输出上下限与每点实际波形极值均调用 `validate_safety_limit()` 做安全限值检查
- [经验] HF2 DAQ 采集时长和采样率的选择需权衡频率分辨率与采集时间
- [经验] 拟合前做预筛选，失败点用插值填充而非设为 NaN
- [经验] 用 `.npz` 替代 CSV 保存矩阵数据，用 `.npy` 替代 CSV 保存波形
- [经验] 保存 `noise_spectra.npz` 供最优控制设计 Notebook 直接加载
