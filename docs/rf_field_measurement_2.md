---
title: 射频场灵敏度测量（直接任意波方案）
type: experiment_type
execution_mode: typed_workflow
description: 利用 Bell-Bloom 磁力仪测量 Z 方向射频场灵敏度。X/Y 控制场使用 DG4000 直接任意波模式，预计算 X(t)=A(t)·cos(ω_L·t) 和 Y(t)=A(t)·sin(ω_L·t) 加载到 Burst 模式。扫描 Z 射频场幅度获得响应曲线，结合 Demod 3 噪声 PSD 计算灵敏度。 锁相输入初始化统一关闭 50 Ω 与 AC 耦合，后续配置保持高阻和 DC 耦合。
keywords: [RF field, sensitivity, direct arbitrary waveform, Burst mode, cascaded demodulator, HF2 DAQ]
version: 2

scan_mode: point_by_point      # 逐点扫描 Z 射频场幅度

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
    description: "HF2 锁相：Demod 0 主信号解调 + Demod 3 射频场解调（AuxOut2 → Signal Input2 物理回采）"

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
  - A(t) 的信息已编码在 X(t)/Y(t) 任意波数据中；AW 满量程自动跟随包络峰值（vpp = 2 × max|A|，offset 恒 0），旧固定满量程参数键已废弃并在加载时剥离
  - 需保证 16kpts 缓冲区内 A(t) 和载波都是整数周期，确保循环边界连续
  - 外部触发同步：dg_mod CH2 SYNC → dg_comp/dg_sweep Ext Trig（BNC 三通），Burst 与 Pump 相位对齐
  - 幅度变化使用 set_amplitude() 而非 setup_sine()，避免退出 Burst 模式导致相位跳变
  - Demod 0 的 TC 需尽可能小（≤10μs），使其带宽足够宽以响应射频场
  - 噪声测量后必须恢复解调器配置，否则第二次运行异常
  - 每次噪声采集完立即恢复温度开关，防止温度漂移
  - 扫描循环须用 try/finally 包裹，确保异常时恢复温度开关
  - 两级相位校准相互独立，先 Demod 0 再 Demod 3
  - Demod 0 与 DirectAW 校相调用 lab_workflows.steps 共享算法；DirectAW 的低 R 样本不参与相位更新
  - Demod 3 的 adcselect=2 将 Demod 0 的 Y 输出路由为输入（级联解调）
experiment_id: rf-sensitivity-direct-aw-frequency
defaults_file: params/experiments/rf-sensitivity-direct-aw-frequency.yaml
---

# 射频场灵敏度测量（直接任意波方案）

## 锁相采集方式与实际速率

RF 扫频使用 `DEMOD0_RATE` 请求率、`POINT_DURATION_s` 时长和 `DEMOD0_IDX` 解调器。单次样本读取保持原行为；有可选阶段时，仅预检本轮启用的时序采集。

时序复用设备库全局 `acquisition_mode`：自动时 HF2 用 DAQ，MFLI 请求 ≤70 kSa/s 用 DAQ、>70 kSa/s 用 Scope；显式模式优先。解调 Scope 需要 MF-DIG，限无触发、单解调器、单段及设备内存；不支持的要求在输出启动前拒绝。Scope 档位按最近值选择，分析使用返回的最终实际率和真实时间轴。原文件名、信号键和目录保留，NPZ 新增模块/速率/时长/派生关系快照，`.npy` 配套 `_acquisition.npz` 旁文件；旧数据缺新字段时沿用原运行快照。预检与日志显示模块及速率，停流恢复和量程规则见[公共采集说明](lockin_amplifier.md#解调时序的模块选择)。

当前默认参数：[参数文件](../params/experiments/rf-sensitivity-direct-aw-frequency.yaml)。GUI「保存为默认」会更新该文件；本文不重复记录可变默认数值。单次运行参数以对应运行目录的 `experiment_config.yaml`（或旧入口实际保存的参数快照）为准。

锁相输入初始化复用 `lab_workflows.steps.initialize_lockin_inputs()`：连接后统一关闭全部主电压输入的 50 Ω 终端和 AC 耦合，使用高阻、DC 耦合；量程与差分设置按实验参数配置。后续校相、回环和采集配置保持这两项关闭。旧耦合/阻抗参数键仍可读取，但统一归一到关闭值，GUI 不再提供开启选项。

## 锁相设备与启动检查

锁相设备在「仪器控制 → 设备库 → 锁相放大器」中全系统统一选择。**本实验当前接线需要两路独立主电压输入，暂时只支持 HF2；选用 MFLI 时预检禁止启动。**MFLI 的 Aux Input 回采尚未验证，程序不会自动改接线、降频或回退 HF2。该限制不影响已有 HF2 数据的离线分析。接口差异见 [锁相接口说明](lockin_amplifier.md)。

## 当前频率扫描入口

稳定 ID `rf-sensitivity-direct-aw-frequency` 已迁移为强类型新模式。参数模型、采集
工作流和分析器位于
`lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/`；
`experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py` 和共享 plot 文件为薄入口。
GUI/YAML 仍使用原有大写键，任意波 CSV 选项由模型显式声明并动态读取
`experiments/*.csv`；同时可选择 `Z_AW_Closed_Loop_Waveform_Correction` 运行目录中的闭环冻结波形，具体来源见参数文件。频率偏移由高级参数 `Z_RF_FREQ_OFFSET_HZ` 指定，实际频点由扫描轴加偏移得到，是否包含 0 Hz 由实际参数决定。本文档中其他 RF 方案仍可能处于旧模式，以 GUI 卡片标记和
`docs/experiment_migration_status.md` 为准。

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

参考时钟以 `params/devices.yaml` 中当前绑定设备的 `reference_clock` 为准；频率锁定后，初始相位由触发沿对齐。

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
3. 调用共享 `calibrate_demod_phase()` → `calibrated_phase_0`

### Phase 2 — X/Y 任意波校相（Burst 相位对齐 Pump）
1. 加载 X(t)/Y(t) 任意波到 dg_comp
2. 配置 Burst INFINITY + 外部触发
3. 关闭温控，读 HF2 Demod 0 相位偏移
4. 调用共享 `calibrate_direct_aw_phase()` 迭代修正 `XY_CTRL_PHASE`，直到收敛；
   低于绝对下限或历史最佳 R 的 10% 的样本只记录、不更新相位

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

## 跨运行幅频响应对比

`experiments/RF_Field_Sensitivity_AW_FreqSweep_compare.py` 用于叠加比较多个
DirectAW 频率扫描运行的 `freq_response_amplitude`。程序读取各运行目录中的
`results/analysis.json`，使用与单次运行图一致的 `R_V_median`，不连接仪器，也不
改写原始数据。

比较对象与输出位置请查看[比较脚本参数区](../experiments/RF_Field_Sensitivity_AW_FreqSweep_compare.py)，本文不维护另一份运行列表。

## 统一安全收尾

正常完成、异常、取消和 Ctrl+C 均调用共享 `run_safety_shutdown()`：归零并关闭
Z 射频场、DirectAW 和非时序触发通道，恢复温度开关；`Time_sequence`、`Time_sequence_2`
保持当前状态，只断开 TEC；主磁场、Pump/Probe 光功率、Pump 调制和全部 HF2 设置保持不变。
连接阶段失败则释放已建立的全部连接。
