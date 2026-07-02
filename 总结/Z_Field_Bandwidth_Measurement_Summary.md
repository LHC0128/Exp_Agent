# Z 方向磁场频率响应 / 带宽测量 — 实验总结

> 适用实验：`experiments/Z_Field_Bandwidth_Measurement.py` + `Z_Field_Bandwidth_Measurement_plot.py`
> 数据来源：`data/Z_Field_Bandwidth_Measurement/` 下 5 次独立采集（1039 / 1101 / 1143 / 1323 / 1343）

---

## 1. 实验目的

测量 Bell-Bloom 磁力仪对 Z 方向低频正弦扰动磁场的频率响应，
根据幅频响应曲线确定 -3 dB 高频截止频率 $f_{\text{high}}$ 与 -3 dB 带宽 $\text{BW} = f_{\text{high}} - f_{\text{low}}$。

Z 方向磁场通过 DG4000 (`Z_magnetic_field` = `DG4E242401288` CH1) 施加，
固定幅度正弦扫描频率；
HF2 Demod 0（90 kHz 载波解调）读取 sample.y 时域信号。

---

## 2. 测量方法

### 2.1 Z 场驱动

固定 $Z_{\text{drive}} = 0.01\ \text{Vpp}$（小信号近似，对应约 35 nT p-p），
按 `np.linspace(0, f_stop, 101)` 扫描 100 + 1 个频点（含 0 Hz 作为 baseline）。

### 2.2 y_V 采集

HF2 Demod 0 配置：90 kHz 参考、order = 4、TC = 1 µs、rate = 50000 Sa/s。
每个频点连续采集 1 s，得到 `y_V` 时域数组（df = 1 Hz）。

### 2.3 三种响应提取（同一段 y_V）

| 方法 | 提取方式 | 适用信号 |
|---|---|---|
| **direct FFT** | 整段 N 点 Hann FFT，取最接近 drive_freq 的 bin（df = 1 Hz） | y_V |
| **offline lock-in** | 软件 IQ 解调 $X=2\langle y\cos\rangle$, $Y=2\langle y\sin\rangle$, $R=\sqrt{X^2+Y^2}$；仅用整数周期 | y_V |
| **hardware demod_r** | HF2 Demod 3（Osc 1 = drive_freq），读 X/Y/R/θ，vector mean = $\sqrt{\langle X\rangle^2+\langle Y\rangle^2}$ | y_V 经物理链路 |

### 2.4 硬件物理链路与耦合模式

```
Demod0 sample.y → Aux Out 2 (auxouts/1, Y signal)
                 → 物理线
                 → Signal Input 2 (sigins/1, coupling 由实际配置决定, range≈1.0V)
                 → Demod 3 (adcselect=1, osc=1)
```

各次实验的硬件读出链路本质一致，主要差别来自 Signal Input 2 的耦合模式。
当 Signal Input 2 为 DC coupling 时，硬件链路只引入近似常数增益；当 Signal Input 2 为 AC coupling 时，输入端高通效应会压低低频分量，导致 hardware demod_r 的频率响应形状被改变。

每次扫描读回实际配置写入 `experiment_config.yaml` 的
`hf2_signal_input_2` / `hf2_aux_out_2` / `hf2_demod_r` 三张子表。

### 2.5 0 Hz baseline 处理

0 Hz 不作为正弦驱动点：
- Z 场输出 `setup_dc(0.0)` + `set_output(False)`
- 采集 1 s y_V 仅用于背景估计（10–5000 Hz 段中位 FFT 振幅）
- baseline 点不参与 peak 和 -3 dB 带宽计算

### 2.6 带宽定义

峰值 $R_{\max}$ → 阈值 $R_{\text{th}} = R_{\max}/\sqrt{2}$（即 -3 dB）。
扫描区间 $[f_{\text{low}}, f_{\text{high}}]$ 中响应 ≥ 阈值的连续频段即为 -3 dB 带宽。
若响应在低频端始终高于阈值（且 $f_{\text{low}}$ 已贴近扫描起点），则报告 `bandwidth_is_censored: true`，
表示真实 -3 dB 低截止频率在扫描起点之下。

---

## 3. 数据与分析流程

1. 采集脚本连接仪器、设置温控/光场/Pump调制、配置 Signal Input 2 / Aux Out 2 / Demod 3，写入 `experiment_config.yaml`。
2. Z 场频率扫描主循环（try/finally 包裹）：f==0 关闭输出采基线，f>0 关闭温度开关采 y_V + 读 Demod 3。
3. 每个频点保存 `raw/freq_NNNN_FREQHz.npz`，含完整 X/Y/R/θ 序列与 vector/scalar mean。
4. `Z_Field_Bandwidth_Measurement_plot.py` 离线分析：
   - 对 y_V 做 `nearest_fft_bin` FFT → amp_fft
   - 对 y_V 做 `offline_lockin_response` → R_li
   - 读 `demod_r_r_vector_mean_V` → R_hw_vec
   - 三种响应归一化比较、绝对幅值比较、FFT/LI 比值、HW/LI 比值、HW vector vs scalar、HW phase
5. 计算 -3 dB 带宽（自动标注 censored），保存到 `results/analysis.yaml/json` 与 `results/frequency_response.npz`。

---

## 4. 多次实验结果对比

5 次独立 run 的关键参数（实测值，全部来自各 run 的 `experiment_config.yaml` 与 `analysis.json`）：

| Run | 扫描范围 | Z Vpp | 直接 FFT peak | Offline LI peak | HW vec peak | HW scalar peak | FFT/LI median | HW/LI median |
|---|---|---|---|---|---|---|---|---|
| 0701_1039_z_bw | 100 – 10000 Hz | 0.01 | 0.151 V @ 100 Hz | 0.151 V @ 100 Hz | 0.0954 V @ 300 Hz | — | 0.995 [0.97, 2.60] | 1.59 [0.29, 3.38] |
| 0701_1101_z_bw | 100 – 10000 Hz | 0.01 | 0.152 V @ 100 Hz | 0.152 V @ 100 Hz | 0.0888 V @ 400 Hz | — | 0.996 [0.43, 1.01] | 1.60 [0.28, 1.82] |
| 0701_1143_z_bw | 100 – 10000 Hz | 0.01 | 0.157 V @ 100 Hz | 0.157 V @ 100 Hz | 0.0911 V @ 400 Hz | — | 0.996 [0.30, 1.03] | 1.60 [0.28, 1.81] |
| **0701_1323_z_bw** | **10 – 1000 Hz** | **0.01** | **0.1647 V @ 20 Hz** | **0.1647 V @ 40 Hz** | **0.0577 V @ 20 Hz** | **0.0577 V @ 20 Hz** | **0.9996 [0.999, 1.001]** | **0.350 [0.348, 0.352]** |
| 0701_1343_z_bw | 10 – 1000 Hz | 0.01 | 0.1676 V @ 40 Hz | 0.1675 V @ 40 Hz | 0.0213 V @ 400 Hz | 0.0213 V @ 400 Hz | 0.9996 [0.998, 1.000] | 0.233 [0.007, 0.304] |

### 4.1 早期结果 (0701_1039 / 1101 / 1143_z_bw) — 硬件链路受耦合/配置影响

- **direct FFT 与 offline lock-in 一致**（median ≈ 0.995–0.996），三种方法在 y_V 数值上吻合；
- **hardware demod_r 频响曲线与 FFT/lock-in 不重合**，峰值位置不同（HW @ 300-400 Hz，FFT/LI @ 100 Hz），ratio 中位数 1.59 而非 1；
- HW bandwidth (700-800 Hz) 显著宽于 FFT/LI bandwidth (200 Hz censored)。

这些 run 的离线结果仍然可信地反映了保存的 `y_V` 频率响应；硬件 demod_r 与离线结果不一致，主要说明硬件读出链路当时并不是一个频率平坦的常数增益通道。结合后续 0701_1323 / 0701_1343 的对比，最需要优先核对的是 Signal Input 2 的 AC/DC coupling、输入范围、AuxOut2 连接与 Demod3 实际读回配置。

### 4.2 0701_1323_z_bw — 链路可信、三种方法一致

在相同物理读出链路下，0701_1323 的 Signal Input 2 实际为 DC coupling。此时硬件链路只表现为近似常数增益，三种方法归一化响应曲线在 `frequency_response_comparison.png` 中基本重合。

#### 关键诊断图

**频率响应三组对比（归一化）：**

![frequency_response_comparison](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/frequency_response_comparison.png)

**FFT / offline lock-in 比值（接近 1.0，常数）：**

![fft_vs_offline_lockin_ratio](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/fft_vs_offline_lockin_ratio.png)

**HW / offline lock-in 比值（常数 ≈ 0.35，固定增益）：**

![hardware_vs_offline_lockin_ratio](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/hardware_vs_offline_lockin_ratio.png)

**HW vector mean vs scalar mean（几乎重合，噪声正偏置不明显）：**

![hardware_vector_vs_scalar](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/hardware_vector_vs_scalar.png)

**典型频点谱（drive 红线、picked 紫线，确认无干扰选峰）：**

![response_spectrum_examples](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/response_spectrum_examples.png)

**HW vector 相位：**

![hardware_phase_vector](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/hardware_phase_vector.png)

**绝对幅值对比（log scale）：**

![frequency_response_absolute](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/frequency_response_absolute.png)

**HW scalar / vector 比值（scalar 正偏置诊断）：**

![hardware_scalar_over_vector_ratio](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/hardware_scalar_over_vector_ratio.png)

**Offline lock-in 相位：**

![offline_lockin_phase](../data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/results/offline_lockin_phase.png)

### 4.3 0701_1343_z_bw — AC coupling 反例

与 0701_1323 使用同样的物理链路和扫描范围 (10–1000 Hz)，但 0701_1343 的实际读回配置显示：

```text
hf2_signal_input_2.ac_coupling_actual: true
```

因此 HW vec peak 从 0.0577 V 降到 0.0213 V，HW/LI median 从 0.35 降到 0.233，并且 HW bandwidth 给出 840 Hz，明显宽于 0701_1323 的 310 Hz。这个结果不是优先归因于接触不良，而是说明 AC coupling 会把 Signal Input 2 变成带高通特性的测量链路，低频分量被压低后，硬件锁相曲线峰值上移、带宽被人为放宽。

本次 run 的 offline FFT / offline lock-in 仍然一致；但 hardware demod_r 受 AC coupling 影响，**不作为推荐带宽结论**。最终以 Signal Input 2 为 DC coupling 的 0701_1323 为主。

### 4.4 AC/DC coupling 对比：为什么耦合模式是关键变量

0701_1323 与 0701_1343 的对比最清楚地说明：物理链路本身不是主要矛盾，Signal Input 2 的耦合模式才是决定 hardware demod_r 是否可信的关键变量。两次 run 的离线 FFT 与离线 lock-in 都高度一致，说明保存的 `y_V` 数据和离线算法是稳定的；差异主要出现在硬件 Demod3 支路。

| 对比项 | 0701_1323_z_bw | 0701_1343_z_bw |
|---|---|---|
| Signal Input 2 coupling | **DC** (`ac_coupling_actual=False`) | **AC** (`ac_coupling_actual=True`) |
| 扫描范围 | 10–1000 Hz | 10–1000 Hz |
| FFT / offline lock-in median | 0.9996 | 0.9996 |
| offline lock-in peak | 0.1647 V @ 40 Hz | 0.1675 V @ 40 Hz |
| offline lock-in grid f_high | 320 Hz | 320 Hz |
| HW / offline lock-in median | **0.3497** | **0.2329** |
| HW / offline lock-in range | 0.3481–0.3517 | 0.0066–0.3043 |
| hardware peak | 0.0577 V @ 20 Hz | 0.0213 V @ 400 Hz |
| hardware grid f_high | 320 Hz | 1000 Hz (censored) |
| 结论用途 | 推荐主结论 | AC coupling 反例 |

这个对比的物理含义是：

- **DC coupling**：硬件链路近似为常数增益，HW/LI ratio 在 10–1000 Hz 内几乎水平，归一化响应与 FFT / offline lock-in 重合。
- **AC coupling**：Signal Input 2 引入高通特性，低频硬件幅值被压低，HW/LI ratio 不再是常数；硬件曲线峰值上移到 400 Hz，并把高频截止误判到扫描上限附近。
- **离线 FFT 与离线 lock-in 在两次 run 中都一致**：说明问题不是 `y_V` 的离线分析算法，而是硬件二级锁相支路的输入耦合模式。

**0701_1343 归一化响应：AC coupling 下，hardware demod_r 与离线曲线明显分离。**

![frequency_response_comparison_1343](../data/Z_Field_Bandwidth_Measurement/0701_1343_z_bw/results/frequency_response_comparison.png)

**0701_1343 HW / offline lock-in ratio：比值不再是常数，低频被强烈压低。**

![hardware_vs_offline_lockin_ratio_1343](../data/Z_Field_Bandwidth_Measurement/0701_1343_z_bw/results/hardware_vs_offline_lockin_ratio.png)

---

## 5. 最新结果详细分析（0701_1323_z_bw）

数据目录：`data/Z_Field_Bandwidth_Measurement/0701_1323_z_bw/`

### 5.1 扫描参数

| 项目 | 值 |
|---|---|
| 时间戳 | 0701_1323 |
| 频率扫描范围 | 0 – 1000 Hz（含 0 Hz baseline） |
| 有效信号点数 | 100 |
| 基线点数 | 1 |
| Z 驱动幅度 | 0.01 Vpp |
| POINT_DURATION | 1.0 s |
| FREQ_SETTLE_TIME | 0.5 s |
| Demod 0 实际 rate | 57565.79 Sa/s |
| Demod 3 实际 rate | 3597.86 Sa/s |
| Signal Input 2 实际 range | 1.017 V |
| Signal Input 2 实际耦合 | DC |
| Aux Out 2 实际 scale | 0.9973 |
| Aux Out 2 实际 outputselect | 1（Demod 0 Y） |
| Aux Out 2 实际 offset | 0.0 V |

### 5.2 三种方法的峰值与带宽

| 方法 | Peak 频率 | Peak 幅值 | f_low | f_high | -3 dB BW | censored |
|---|---|---|---|---|---|---|
| direct FFT (background-corrected) | 20 Hz | 0.1647 V | 10 Hz | 320 Hz | 310 Hz | True |
| offline lock-in | 40 Hz | 0.1647 V | 10 Hz | 320 Hz | 310 Hz | True |
| hardware demod_r vector | 20 Hz | 0.0577 V | 10 Hz | 320 Hz | 310 Hz | True |
| hardware demod_r scalar | 20 Hz | 0.0577 V | 10 Hz | 320 Hz | 310 Hz | True |

三种方法给出 **完全相同的网格点结果 $f_{\text{high}} = 320\ \text{Hz}$**。由于频率步进为 10 Hz，若对 320 Hz 与 330 Hz 附近的交叉点做线性插值，高频 -3 dB 截止约为 **325 Hz**。

### 5.3 关键比值

| 比值 | median | min | max | 物理意义 |
|---|---|---|---|---|
| FFT / offline lock-in | 0.9996 | 0.9986 | 1.0010 | 两种 y_V 处理方法在数值上一致（≤ ±0.15% 偏差） |
| hardware / offline lock-in | **0.3497** | 0.3481 | 0.3517 | 硬件链路引入固定增益 ≈ 0.35 |

HW ratio 在整个 10–1000 Hz 扫描范围内的标准差仅 ±0.002，
说明该比值是**常数增益**，不改变频率响应形状，**不影响归一化 -3 dB 带宽判断**。

### 5.4 关于硬件幅度比 ≈ 0.35 的可能来源（推测）

可能贡献项（叠加或单因素）：
- Aux Out 2 scale ≈ 0.997（实测），接近 1，不是主要因素；
- Signal Input 2 range 实际 1.017 V，HF2 内部量化/校准引入小幅增益差；
- Aux Out / Signal Input 之间 50 Ω 阻抗匹配差异；
- HF2 `get_sample` 路径的 RMS vs peak 定义（取 √2 倍差异常见于该路径）；
- Demod 3 N_avg=50 内部平均后的尺度归一化。

具体是哪一个或哪些项叠加 = 0.35，需在后续用单点校准测量（如静态已知 Y 输入）确认。
但因为比值是**常数**，对归一化带宽判断没有实质影响。

### 5.5 Vector vs Scalar mean

| Run | HW vector peak | HW scalar peak | 差异 |
|---|---|---|---|
| 0701_1323 | 0.05770 V @ 20 Hz | 0.05770 V @ 20 Hz | < 1e-5，相对差 < 0.02% |

vector mean 与 scalar mean 几乎重合，说明这次实验的测量噪声远低于信号，
噪声的"取模正偏置"对 mean(|Z|) 的影响可以忽略。
（早期 run 的 mean(R) 偏高 60% 是因为读数方差大，与链路无关。）

### 5.6 -3 dB 带宽结论

**推荐报告：Z 方向磁场响应 -3 dB 高频截止频率 $f_{\text{high}} \approx 325\ \text{Hz}$（网格法为 320 Hz，三种方法一致）。**

由于扫描起点 $Z_{\text{FREQ\_START}} = 10\ \text{Hz}$ 限制，$f_{\text{low}}$ 仍被 censored 在 10 Hz。
这意味着：
- **-3 dB 低截止频率 < 10 Hz**
- **完整双侧 -3 dB 带宽尚未被完整测出**
- **本实验可靠给出的结论是高频侧 -3 dB 截止约 325 Hz**

---

## 6. 误差来源与注意事项

1. **f_low censored**：本批 run 均未完整扫到 -3 dB 低频端。要拿到完整带宽需要把 `Z_FREQ_START` 调小到 ≤ 1 Hz 或更低，并对超低频段使用更长 `POINT_DURATION`（保证 FFT 1 Hz 分辨率足够）。

2. **HW 链路固定增益 ≈ 0.35 的来源未量化**：建议在某个已知恒定 Y 输入下做单点定标，验证是 AuxOut、50 Ω 匹配、ADC 量化、还是 RMS/peak 归一化引入。

3. **0701_1343 run 的主要差异是 AC coupling**：实际读回 `hf2_signal_input_2.ac_coupling_actual: true`，低频硬件响应被输入端高通压低，导致 HW/LI median 降到 0.233，硬件曲线峰值上移并显得更宽。本次 run 可作为 AC coupling 影响的反例，但不用于最终带宽结论。

4. **Demod 3 TC_MIN 主要影响响应时间与平均方式**：`DEMOD_R_TC_MIN = 0.01 s` 不宜简单等同为会衰减低频稳态幅值的 16 Hz 高通/低通截止。若等待时间足够，Demod3 应能给出目标频率解调后的稳态幅值；当前硬件曲线形状差异优先归因于 Signal Input 2 的 AC/DC coupling。

5. **Picked frequency diagnostic**：0701_1323 由于 drive_freq 全部精确对齐 1 Hz FFT bin，`freq_error_Hz = 0`，0 个 suspicious 点。`daq_picked_frequency_error.png` 显示三条线（median / ±threshold）全部贴在 0。

6. **离线 lock-in 整数周期限制**：`OFFLINE_LOCKIN_USE_INTEGER_CYCLES = True` 会在极低频（f < 1 Hz / POINT_DURATION = 1 s）时退化为 n_cycles < 1 → invalid。0701_1323 起点 10 Hz，n_cycles = 10，全部 valid。

---

## 7. 后续建议

1. **完整带宽测量**：将 `Z_FREQ_START` 调到 0.1 Hz 或 1 Hz，扫描区间改为 `np.logspace(-1, 3, 81)`，即可同时获得 -3 dB 高、低截止频率。

2. **HW 链路定标**：在已知恒定 Y 直流输入（Demod 0 Y）下，测量 `Demod3_r_vector` 与 `Demod0_sample.y` 的比值，作为链路总增益的实测校准。

3. **对比 AC/DC coupling**：若要定量确认耦合模式的影响，可在完全相同连接下重复两次扫描，只切换 Signal Input 2 的 coupling：
   - DC coupling：应复现 0701_1323，HW/LI ratio 近似常数，归一化响应与离线结果重合
   - AC coupling：应复现 0701_1343 的低频压低与峰值上移

4. **旧数据格式差异**：1039/1101/1143 三次 run 只有 `demod_r_mean_V`（scalar mean），与新格式不完全兼容。它们可用于定性比较硬件曲线与离线曲线是否一致，但不能跨字段计算 vector vs scalar。

5. **数据复用**：新数据全部格式自洽，可直接用 `Z_Field_Bandwidth_Measurement_plot.py` 重跑得到标准化分析结果。

---

## 8. 附录：引用的 Run

| 序号 | Run 目录 | 状态 |
|---|---|---|
| 1 | 0701_1039_z_bw | 早期，100-10000 Hz，硬件曲线与离线曲线不一致 |
| 2 | 0701_1101_z_bw | 早期，100-10000 Hz，硬件曲线与离线曲线不一致 |
| 3 | 0701_1143_z_bw | 早期，100-10000 Hz，硬件曲线与离线曲线不一致 |
| 4 | **0701_1323_z_bw** | **推荐主结论 run，10-1000 Hz，Signal Input 2 = DC coupling** |
| 5 | 0701_1343_z_bw | AC coupling 对照 run，HW 低频被压低，不用于最终带宽 |

所有 5 次 run 均包含完整 `raw/freq_*.npz`、`results/analysis.yaml/json`、`results/frequency_response.npz`、11+ 张 PNG 图。
其中 0701_1323_z_bw 与 0701_1343_z_bw 额外包含 3 张与 vector/scalar mean 相关的诊断图
（`hardware_vector_vs_scalar.png` / `hardware_scalar_over_vector_ratio.png` / `hardware_phase_vector.png`）。
