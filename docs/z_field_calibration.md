---
title: Z 磁场频率标定
type: experiment_type
description: Bell-Bloom 磁力仪中，外扫 Z 电压、内扫 Pump 调制频率找共振峰，多点线性回归标定 V→B 转换系数
keywords: [calibration, Zeeman, Bell-Bloom, Larmor frequency, R signal, lock-in]
version: 1

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
    description: "Pump 调制任意波，重复频率逐点改变找共振"
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
- DG4000 CH1 输出幅度调制的 Pump 光（USER 任意波）
- 当调制频率 $f_\text{pump}$ 等于 Zeeman 共振频率 $f_\text{Larmor}$ 时，R 信号最大
- HF2 锁相振荡器频率跟随 $f_\text{pump}$，解调 R 信号
- 两者**必须同步**——锁相解调的是 Probe 光携带的原子响应，其频谱分量就在调制频率处

$$f_\text{Larmor} = \frac{g_F \mu_B}{h} \cdot B_\text{abs}$$

### 两层嵌套扫描结构

```
for each V_Z in [-0.3, -0.2, -0.1, 0, 0.1, 0.2, 0.3] V:    ← 外层
    设 DG4000 CH2 输出 = V_Z
    for each f in [70, 72, ..., 110] kHz:                   ← 内层
        同时设置：
          DG4000 CH1: apply_wave("USER", freq=f)
          HF2 osc/0:  set_freq(f)
        等稳定后读 R 信号
    找 R 峰值 → f_Larmor
    B_abs = f_Larmor / (γ/2π)
    (V_Z, B_abs)
    
线性拟合 B_abs = k_Z·V_Z + B_offset → 得到 k_Z
```

## 实验流程

### Phase 1: 准备
1. 连接所有设备
2. 设置温度控制：
   - 打开 TEC103 温控器，设定目标温度
   - 打开温度开关（DG4000 CH2 输出 5V）
   - **等待温度稳定后再继续**（可通过 `tec.get_temperature()` 监测，波动 < 0.05°C 后开始实验）
3. 设置主磁场（GS200 恒流模式）
4. 生成 Pump 调制任意波（初始频率设为预期共振值附近）
5. 相位校准
6. X/Y 补偿磁场输出关闭

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

    # 采集前关闭温度开关，消除温控磁场干扰
    dg_temp.set_output(False, channel=2)
    time.sleep(0.2)

    r_list = []
    for f in scan_freqs:
        # --- 内层：同时设置调制频率和解调频率 ---
        dg_mod.apply_wave("USER", channel=1, freq=f, ...)
        demod.set_reference_frequency(hfi, freq=f, osc_idx=0)
        time.sleep(SETTLE_TIME)

        sample = demod.read_demod_sample(hfi, demod_idx=0)
        r_list.append(sample["r"])

    # 采集结束立即恢复温度开关
    dg_temp.set_output(True, channel=2)
    time.sleep(1.5)  # 等待温度稳定后再进行下一轮

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

# 更新灵敏度实验中的校准系数
Z_V_TO_NT = k_Z
Z_V_TO_FT = k_Z * 1e6
```

## 数据解析规则

### 各 V_Z 下的共振峰定位

```
R signal at V_Z = +0.1 V           R signal at V_Z = -0.1 V
  |                                  |
  |    /\                            |        /\
  |  /    \                          |      /    \
  |/______\____→ f_pump              |/____\______→ f_pump
    f_res                             f_res
  (偏大, 场大)                       (偏小, 场小)
```

### 校准曲线

最终的校准图有两张：
1. **各 V_Z 下的 R(f) 曲线**（子图阵列或叠加，直观看到共振峰偏移）
2. **B_abs vs V_Z 线性回归**（斜率即 $k_Z$)

## 注意事项
- 调制任意波的 USER 波形内容不变，只改变 `apply_wave` 的 `freq` 参数
- 每步需给 HF2 PLL 足够时间锁定新频率（`SETTLE_TIME` 建议 0.5s 以上）
- 也可考虑将 DG4000 CH1 的 SYNC 输出接 HF2 的参考输入，实现硬件自动同步（视硬件接线而定）
- 标定频率范围应覆盖共振峰两侧，以便准确拟合线宽和峰位
- **温度管理**：与静磁场灵敏度实验相同，每次频率扫描前关闭温度开关（消除温控磁场干扰），扫描后立即恢复。两次扫描之间等待 1.5s 让温度恢复稳定。温度波动会影响 Zeeman 共振频率测量结果。

```python
# 典型采集循环中的温度控制模式
dg_temp.set_output(False, channel=2)   # 关闭→采集
# ... 频率扫描 ...
dg_temp.set_output(True, channel=2)    # 恢复→等待稳定
time.sleep(1.5)
```
- 每轮外层 Z 电压更新后，等待 0.5~1s 让磁场稳定后再开始内层频率扫描
