---
title: XY DC 电压到控制场频率的标定
type: experiment_type
description: 在 ConstXY 链路下，通过扫描 X/Y AM 输入的共同 DC 电压，并对 Z 方向小信号频率响应取峰值位置，标定 XY_DC_VOLTAGE 与实际响应中心频率之间的关系。
keywords: [XY DC calibration, ConstXY, RF bandwidth, frequency response, offline lock-in, HF2 DAQ]
version: 1

scan_mode: nested_scan

defaults:
  XY_DC_VOLTAGE_LIST: [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
  Z_RF_FREQ_START_Hz: 500
  Z_RF_FREQ_STOP_Hz: 6000
  Z_RF_FREQ_STEP_Hz: 100
  Z_RF_FINE_HALF_WIDTH_Hz: 500
  Z_RF_FINE_STEP_Hz: 25
  Z_RF_AMPLITUDE_Vpp: 0.01
  POINT_DURATION_s: 1.0
  FREQ_SETTLE_TIME_s: 0.5
  ACQUISITION_MODE: daq_fft_y
  RESPONSE_METHOD: offline_lockin

mapping_keys:
  X_magnetic_field_AM:
    role: scan
    description: "DG4E231500376 CH1，输出 X 方向 AM 控制 DC 电压。"
  Y_magnetic_field_AM:
    role: scan
    description: "DG4E231500376 CH2，输出 Y 方向 AM 控制 DC 电压，与 X 通道使用相同 DC 电压。"
  X_magnetic_field:
    role: carrier
    description: "XY 载波发生器 CH1，AM EXT 模式，载波频率和幅度固定。"
  Y_magnetic_field:
    role: carrier
    description: "XY 载波发生器 CH2，AM EXT 模式，载波频率和幅度固定。"
  Z_magnetic_field:
    role: probe
    description: "Z 方向小信号驱动，固定幅度，扫描频率得到响应曲线。"
  Pump_modulation:
    role: pump
    description: "Pump 调制链路，保持与 ConstXY 带宽测量一致。"
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  main_magnetic_field:
    role: fixed
  temperature:
    role: fixed
  Temp_Switch:
    role: temp_gating
  lockin_r:
    role: detection

required_devices:
  - signal_generator
  - gs200
  - tec_controller
  - lockin_amplifier

learned_notes:
  - 当前 ConstXY 带宽数据中，旧公式给出的 XY_DC_VOLTAGE=0.1409 V 原本对应 2 kHz，但实际响应峰约为 3.6 kHz，因此应重新标定当前物理链路下的有效系数。
  - 标定目标是实际峰位关系 f_peak = K_eff * XY_DC_VOLTAGE + B_eff，而不是复用旧 AW 方案的 A_ENV_K/A_ENV_B。
  - 峰位标定优先使用保存的 Demod 0 sample.y 做 offline lock-in 或 nearest-bin FFT；hardware Demod 3 可作为验证，不必作为主结果。
  - hardware Demod 3 的绝对幅值可能因 Aux Out、50 Ω 负载和 Signal Input 量程产生比例缩放；只要归一化峰位一致，就不影响 XY DC 标定。
  - 频谱中可能存在固定窄带干扰线，峰位提取应优先取驱动频点处响应或使用拟合中心，避免 local peak 被干扰线带偏。
---

# XY DC 电压标定方法

## 目标

在当前 ConstXY 实验链路下，重新标定

```text
f_peak_Hz = K_eff * XY_DC_VOLTAGE_V + B_eff
```

其中 `f_peak_Hz` 是通过 Z 方向小信号频率扫描得到的响应峰值频率。标定完成后，后续 ConstXY 带宽测量应使用

```text
XY_DC_VOLTAGE_V = (TARGET_FREQ_Hz - B_eff) / K_eff
```

而不是继续使用旧 AW 方案的 `A_ENV_K=13724 Hz/V` 和 `A_ENV_B=66 Hz`。

## 推荐实验流程

1. 固定主磁场、Pump/Probe 光功率、温度、Pump 调制、XY 载波频率和幅度、AM 深度、Z RF 驱动幅度。
2. 设置 `X_magnetic_field_AM` 和 `Y_magnetic_field_AM` 为相同 DC 电压。
3. 对每个 `XY_DC_VOLTAGE` 扫描 Z RF 频率，保存 Demod 0 的 `sample.y` 时域数据。
4. 离线使用 `offline lock-in` 或 `nearest_fft_bin` 得到 `response(f_z)`。
5. 对每条响应曲线取峰值位置，优先使用拟合中心；若拟合不稳定，可使用最大点并记录频率步进误差。
6. 拟合 `f_peak_Hz` 与 `XY_DC_VOLTAGE_V` 的线性关系，得到 `K_eff` 和 `B_eff`。

## 扫描策略

推荐先使用粗扫，确认峰值大致位置：

```text
XY_DC_VOLTAGE_LIST = 0.04, 0.06, ..., 0.20 V
Z_RF_FREQ = 500 Hz 到 6000 Hz，步进 100 Hz
POINT_DURATION = 1 s
Z_RF_AMPLITUDE = 0.01 Vpp
```

如果粗扫峰位清晰，再对每个电压点做局部细扫：

```text
Z_RF_FREQ = f_peak_coarse ± 500 Hz，步进 25 Hz 或 50 Hz
```

粗扫用于确定线性区和峰位范围，细扫用于降低峰位离散误差。

## 主结果选择

标定只关心峰的位置，因此不要求硬件 Demod 3 的绝对幅值与离线幅值一致。

推荐主结果：

- `offline lock-in`：优先，直接来自保存的 Demod 0 `sample.y`，可重复离线计算。
- `nearest_fft_bin`：可作为并列主结果或交叉验证。

推荐辅助验证：

- `hardware demod_r vector`：用于确认物理回环链路与离线结果峰位一致。

不建议作为主结果：

- `hardware demod_r scalar`：适合诊断噪声偏置，不适合作为首选标定量。

## 成功判据

- 同一电压重复测量 2 到 3 次时，峰位变化小于细扫步进，建议 `< 50 Hz`。
- `offline lock-in` 与 `nearest_fft_bin` 的峰位一致。
- 若采集了 hardware Demod 3，其归一化响应峰位应与离线结果一致。
- 线性拟合残差应随机分布；若残差随电压系统性弯曲，应改用二次拟合或分段线性拟合。

## 结果保存建议

建议保存：

- 每个电压点的原始频率响应数据。
- `xy_dc_voltage_V`
- `f_peak_fft_Hz`
- `f_peak_offline_lockin_Hz`
- `f_peak_hardware_demod_r_Hz`
- `fit_center_Hz`
- `fit_width_Hz`
- `fit_success`
- `K_eff_Hz_per_V`
- `B_eff_Hz`
- `linear_fit_residual_Hz`

推荐图：

- `xy_dc_calibration_curve.png`：`f_peak_Hz` vs `XY_DC_VOLTAGE_V`，含线性拟合。
- `xy_dc_calibration_residual.png`：拟合残差。
- `frequency_response_examples.png`：选几个电压点展示响应曲线。
- `method_peak_comparison.png`：FFT、offline lock-in、hardware Demod 3 的峰位对比。

## 对应 prompt

```text
使用 expcodegen 生成一个 XY DC 电压标定实验，实验名称为 XY_DC_Voltage_Calibration。

目标：
在当前 ConstXY 链路下，标定 X/Y AM 控制 DC 电压 XY_DC_VOLTAGE 与实际响应中心频率 f_peak 的关系。不要复用旧的 A_ENV_K=13724 Hz/V、A_ENV_B=66 Hz 作为结论，只能作为历史参考。标定结果应输出新的 K_eff_Hz_per_V 和 B_eff_Hz，使后续可以用 XY_DC_VOLTAGE = (TARGET_FREQ_Hz - B_eff) / K_eff 设置目标控制频率。

实验链路：
- X_magnetic_field_AM 和 Y_magnetic_field_AM 分别为 X/Y AM 输入，二者设置为相同 DC 电压 XY_DC_VOLTAGE。
- X_magnetic_field 和 Y_magnetic_field 作为 XY 载波输出，使用 AM EXT，载波频率、载波幅度、AM 深度保持与 experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py 一致。
- Z_magnetic_field 输出固定幅度的 Z 方向小信号正弦驱动，扫描其频率。
- HF2 Demod 0 采集 sample.y 时域数据，作为主分析数据。
- hardware Demod 3 可选保留为验证：Demod0 Y -> Aux Out 2 -> physical cable -> Signal Input 2 DC coupled -> Demod 3，adcselect 必须为 Signal Input 2，不要使用 adcselect=2 当作 Demod0 Y 内部路由。

扫描参数：
- XY_DC_VOLTAGE_LIST 默认为 [0.04, 0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20] V。
- Z_RF_AMPLITUDE 默认 0.01 Vpp。
- 粗扫 Z_RF_FREQ 从 500 Hz 到 6000 Hz，步进 100 Hz。
- 每个频点采集 1 s。
- 采集模式默认 daq_fft_y；可保留 ACQUISITION_MODE = "both" 用于对比 hardware Demod 3。
- 每个 XY_DC_VOLTAGE 点先完成完整 Z 频率扫描，再切换到下一个 XY_DC_VOLTAGE。

采集脚本要求：
- 生成 experiments/XY_DC_Voltage_Calibration.py，使用 # %% Cell 结构。
- 参数在顶部集中定义，使用全大写命名。
- 读取 params/mapping.yaml 和 params/safety_limits.yaml。
- 所有实际输出量设置前调用 validate_safety_limit()；特别注意 X_magnetic_field_AM、Y_magnetic_field_AM、Z_magnetic_field、Pump/Probe、主磁场、温度开关。
- 连接设备后先把不用或危险输出置于安全状态。
- 扫描循环必须用 try/finally，异常或正常结束时关闭 Z 输出，恢复温度开关，关闭/归零 XY AM DC 输出。
- 正常结束只断开 TEC，其余设备保持连接。
- 保存到 data/XY_DC_Voltage_Calibration/<MMDD_HHMM_xy_dc_cal>/。
- 保存 experiment_config.yaml，包含 mapping/safety snapshot、XY_DC 列表、Z 频率列表、HF2 实际采样率、Demod 配置、物理链路说明。
- raw/ 中逐点或逐电压保存 npz，字段至少包含 xy_dc_voltage_V、z_freq_Hz、time_s、y_V、actual_rate_Sa_s、is_baseline，并保存 frequency_index.json。

分析脚本要求：
- 生成 experiments/XY_DC_Voltage_Calibration_plot.py。
- plot 脚本不连接任何仪器，不导入仪器控制类，只读取本地 data。
- 对每个 XY_DC_VOLTAGE 的 y_V 做 offline lock-in 和 nearest-bin FFT，提取 response(f_z)。
- 对每条 response(f_z) 取峰值频率，并尝试 Lorentzian 或 double-Lorentzian 拟合得到 fit_center_Hz 和 fit_width_Hz；拟合失败时退回最大点。
- 拟合 f_peak_Hz = K_eff * XY_DC_VOLTAGE_V + B_eff。
- 保存 results/analysis.yaml、analysis.json、xy_dc_calibration_results.npz。
- 绘图坐标轴、标题、图例全部使用英文。
- 至少保存 xy_dc_calibration_curve.png、xy_dc_calibration_residual.png、frequency_response_examples.png、method_peak_comparison.png。

验证：
- 如果 safety_limits.yaml 中缺少 X_magnetic_field_AM/Y_magnetic_field_AM 的安全限值，不要静默跳过，需在脚本启动时给出明确 WARNING，并建议补充限值。
```
