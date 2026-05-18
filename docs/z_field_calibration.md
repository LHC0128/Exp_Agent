---
title: Z 磁场频率标定
type: experiment_type
description: Bell-Bloom 磁力仪中，外扫 Z 电压、内扫 Pump 调制频率找共振峰，多点线性回归标定 V→B 转换系数
keywords: [calibration, Zeeman, Bell-Bloom, Larmor frequency, R signal, lock-in, RF switch]
version: 2

scan_mode: nested_scan           # 外层 Z 电压，内层频率

# ========== 默认参数 ==========
defaults:
  # ---- 外层：Z 电压扫描 ----
  Z_V_START: -0.3               # Z 电压扫描起始 (V)
  Z_V_STOP: 0.3                 # Z 电压扫描终止 (V)
  Z_V_NUM: 7                    # Z 电压扫描点数
  # ---- 内层：频率扫描 ----
  FREQ_START: 70e3              # 频率扫描起始 (Hz)
  FREQ_STOP: 110e3              # 频率扫描终止 (Hz)
  FREQ_POINTS: 50               # 频率扫描点数
  SETTLE_TIME: 0.5              # 每点等待稳定时间 (s)
  DEMOD_RATE: 1000              # 解调器输出速率 (Sa/s)
  DEMOD_TC: 0.001               # 解调器时间常数 (s)
  # ---- RF 开关控制信号 ----
  RF_GATE_AMPLITUDE: 5.0        # CH2 门控脉冲幅度 (Vpp)
  RF_GATE_OFFSET: 2.5           # CH2 门控脉冲偏置 (V)

# ========== RF 开关方案说明 ==========
# Pump 调制不再使用任意波, 改用 RF 开关:
#   dg_mod CH1: 100MHz 连续正弦波 → RF 开关 IN
#   dg_mod CH2: 脉冲方波 (TTL)    → RF 开关 CTRL
#   RF 开关 OUT → AOM (串 0.1μF 隔直电容)
#   扫描时只改变 CH2 脉冲频率, CH1 保持 100MHz 不变
#   10Hz 时序信号由 dg_sweep CH2 提供
# ======================================

# ========== mapping.yaml 中的 key ==========
mapping_keys:
  Z_magnetic_field:
    role: scan_outer             # 外层扫描变量！
    description: "Z 磁场电压，标定时逐点改变"
  Pump_laser_power:
    role: fixed
    description: "Pump 光功率"
  Probe_laser_power:
    role: fixed
    description: "Probe 光功率"
  Pump_modulation:
    role: scan_inner             # 内层扫描变量！
    description: "Pump 调制: CH1 100MHz 正弦 + CH2 脉冲门控 → RF 开关 → AOM"
  Temp_Switch:
    role: fixed
    description: "温度开关"
  main_magnetic_field:
    role: fixed
    description: "主磁场（GS200 恒流）"
  temperature:
    role: fixed
    description: "气室温度"
  lockin_r:
    role: detection
    description: "HF2 锁相，解调频率跟踪 Pump 调制频率"

# ========== 固定参数（在整个实验中不变的） ==========
fixed_params:
  - Pump_laser_power
  - Probe_laser_power
  - Temp_Switch
  - main_magnetic_field
  - temperature
  - X_magnetic_field
  - Y_magnetic_field
---

# Z 磁场频率标定（Bell-Bloom 磁力仪）

## 原理

### 外层扫描：Z 电压

要标定 Z 线圈的 **V→B 转换系数 $k_Z$**，需要在**多个不同的 Z 电压**下分别测量绝对磁场，做线性回归：

$$B_\text{abs}(V_Z) = k_Z \cdot V_Z + B_\text{offset}$$

```
B_abs
  ↑                  /
  |                /
  |              /
  |            /      斜率 = k_Z (nT/V)
  |          /
  |        /
  |  截距 = B_offset (本底磁场)
  |______/____________________→ V_Z
```

- **斜率 $k_Z$**：Z 线圈的 V→B 转换系数（要标定的目标）
- **截距 $B_\text{offset}$**：本底磁场（地磁、漏磁等，可用 X/Y 补偿抵消）

### 内层扫描：频率找共振

在每个 Z 电压 $V_Z$ 下，需要知道该点的绝对磁场 $B_\text{abs}$。方法是扫描 Pump 调制频率找共振峰。

Bell-Bloom 磁力仪中：
- **RF 开关方案**: DG4000 CH1 输出 100MHz 连续正弦波 → RF 开关 IN；CH2 输出脉冲方波 → RF 开关 CTRL
- 当脉冲重复频率 $f_\text{pulse}$ 等于 Zeeman 共振频率 $f_\text{Larmor}$ 时，R 信号最大
- HF2 锁相振荡器频率跟随 $f_\text{pulse}$，解调 R 信号
- 两者**必须同步**——锁相解调的是 Probe 光携带的原子响应，其频谱分量就在脉冲重复频率处

$$f_\text{Larmor} = \frac{g_F \mu_B}{h} \cdot B_\text{abs}$$

### 两层嵌套扫描结构

```
for each V_Z in [-0.3, -0.2, -0.1, 0, 0.1, 0.2, 0.3] V:    ← 外层
    设 DG4000 CH2 输出 = V_Z
    for each f in [70, 72, ..., 110] kHz:                   ← 内层
        同时设置：
          DG4000 CH2 (dg_mod): setup_pulse(freq=f, ...)   ← 改变门控频率
          HF2 osc/0:            set_freq(f)
        等稳定后读 R 信号
    找 R 峰值 → f_Larmor
    B_abs = f_Larmor / (γ/2π)
    (V_Z, B_abs)
    
线性拟合 B_abs = k_Z·V_Z + B_offset → 得到 k_Z
```

## 实验流程

### Phase 1: 准备
1. 连接所有设备，确认 RF 开关接线
2. 设置温度控制：
   - 打开 TEC103 温控器，设定目标温度
   - 打开温度开关（DG4000 CH2 输出 5V）
   - **等待温度稳定后再继续**（波动 < 0.05°C 后开始）
3. 设置主磁场（GS200 恒流模式）
4. 配置 RF 开关方案：
   - **dg_mod CH1**: `setup_sine(freq=100e6, amplitude=0.18Vpp)` → RF 开关 IN
   - **dg_mod CH2**: `setup_pulse(freq=90kHz, width=560ns, 5Vpp+2.5V)` → RF 开关 CTRL
   - RF 开关 OUT → AOM（需串 **0.1μF 隔直电容**）
5. 注意：10Hz 时序信号由 `dg_sweep` CH2 提供（不再占用 `dg_mod` CH2）
6. 相位校准
7. X/Y 补偿磁场输出关闭

### Phase 2: 嵌套扫描
```python
Z_voltages = np.linspace(Z_V_START, Z_V_STOP, Z_V_NUM)
scan_freqs = np.linspace(FREQ_START, FREQ_STOP, FREQ_POINTS)

results = {}  # V_Z → {freqs, r_values}

for V_Z in Z_voltages:
    # --- 外层：设置 Z 磁场 ---
    dg_sweep.setup_dc(V_Z, channel=1)
    dg_sweep.set_output(True, channel=1)
    time.sleep(0.3)

    r_list = []
    for f in scan_freqs:
        # 采集前关闭温度开关
        dg_temp.set_output(False, channel=2)

        # --- 内层：改变 RF 开关门控频率 (CH1 100MHz 保持不动) ---
        pulse_width = (PUMP_MOD_DUTY / 100.0) / f
        dg_mod.setup_pulse(freq=f, amplitude=RF_GATE_AMPLITUDE,
                           offset=RF_GATE_OFFSET, width=pulse_width, channel=2)
        # 同步 HF2 解调频率
        demod.set_reference_frequency(hfi, freq=f, osc_idx=0)
        time.sleep(SETTLE_TIME)

        sample = demod.read_demod_sample(hfi, demod_idx=0)
        r_list.append(sample["r"])

        # 采集结束立即恢复温度开关
        dg_temp.set_output(True, channel=2)

    results[V_Z] = {"freqs": scan_freqs, "r": np.array(r_list)}

    # 监测温度
    t_now = tec.get_temperature(channel=1)
    print(f"  V_Z={V_Z:+.2f}V 完成, 温度 {t_now:.2f}°C")
```

### Phase 3: 各 V_Z 下的共振峰拟合

```python
def lorentzian(x, A, x0, gamma, C):
    return A * gamma**2 / ((x - x0)**2 + gamma**2) + C

gamma_2pi = 7.0  # Hz/nT，按实际原子替换

B_list = []
V_list = []

for V_Z, data in results.items():
    popt, _ = curve_fit(lorentzian, data["freqs"], data["r"],
                        p0=[max(data["r"]), FREQ_START * 1.5, 5e3, min(data["r"])])
    f_larmor = popt[1]           # 共振峰中心频率
    B_abs = f_larmor / gamma_2pi # nT
    B_list.append(B_abs)
    V_list.append(V_Z)
    print(f"V_Z={V_Z:+.2f}V → f_Larmor={f_larmor/1e3:.1f}kHz → B={B_abs:.0f}nT")
```

### Phase 4: 线性回归得校准系数

```python
from scipy.stats import linregress
slope, intercept, r_value, p_value, std_err = linregress(V_list, B_list)

k_Z = slope                     # nT/V ← 这就是要标定的！
B_offset = intercept            # nT

print(f"k_Z = {k_Z:.1f} nT/V")
print(f"B_offset = {B_offset:.1f} nT (本底磁场)")
print(f"R² = {r_value**2:.4f}")
```

## 硬件连接注意事项
- RF 开关输出端需串 **0.1μF 隔直电容**再接 AOM
- DG4000 CH2 脉冲幅度 5Vpp、offset 2.5V → 0~5V TTL 电平
- 100MHz BNC 线尽量短，避免信号反射
- RF 开关需独立 DC 电源供电
