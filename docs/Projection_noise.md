---
title: 原子自旋投影噪声示波器测量
type: Projection_noise
scan_mode: point_by_point
defaults:
  TARGET_LARMOR_FREQUENCY_HZ: 90000
  MAIN_FIELD_CALIBRATION_SOURCE_RUN: 0720_124208_mx_main_field_cal
  SCOPE_SAMPLE_RATE: 500000
  SCOPE_DURATION: 1
  ACQ_REPEATS: 100
  MEASURE_FIELD_OFF_CONTROL: true
  WELCH_NPERSEG: 50000
  FIT_HALF_WIDTH_HZ: 5000
  FIXED_PARAMS.Pump_laser_power: 0.0
mapping_keys:
  main_magnetic_field:
    role: scan
  scope_waveform:
    role: detection
  Pump_laser_power:
    role: fixed
  Probe_laser_power:
    role: fixed
  Temp_Switch:
    role: temp_gating
  temperature:
    role: fixed
required_devices:
  - GS200
  - DG900
  - SDS
  - TEC103
learned_notes:
  - PD 原始信号接 SDS CH1，直接在 Larmor 频率附近观察自旋噪声峰。
  - `MEASURE_FIELD_OFF_CONTROL` 控制是否测量 GS200 输出关闭背景，默认开启。
  - 关闭对照组选项时，直接对 `field_on` PSD 做带常数背景的洛伦兹拟合。
  - 每帧采集期间温控开关为 0 V DC + output ON，帧间恢复 5 V DC + output ON。
---

# 原子自旋投影噪声示波器测量

## 实验入口

- 采集：`experiments/Projection_noise.py`
- 离线分析：`experiments/Projection_noise_plot.py <run_dir>`
- 稳定实验 ID：`projection-noise`
- 运行目录：`data/Projection_noise/<run>/`

该实验不再使用 HF2 DAQ。PD 原始输出接入 SDS CH1，直接采集时域电压并计算 RF PSD，在主场标定预测的 Larmor 频率附近拟合洛伦兹自旋噪声峰。

## 实验条件

- Pump 光：默认 `0 V` 且输出 OFF；可在 `0–1 V` 安全范围内设置，非零时输出 ON。
- Probe 光：`0.3 V`，输出 ON。
- 气室温度：`120 °C`。
- SDS：CH1、DC、1 MΩ、1×探头、AUTO 基础配置；每帧由 FTRIG 强制采集，不使用外部触发。
- 请求采样率：`500 kSa/s`。
- 每帧时长：`1 s`。
- 主场打开阶段：`100` 帧。
- 可选主场关闭对照阶段：`100` 帧。

## 主场标定与可选对照组流程

默认固定读取：

```text
data/Mx_Main_Field_Calibration/
  0720_124208_mx_main_field_cal/results/analysis.yaml
```

标定关系为：

```text
f_Hz = 9671.91741380711 × I_mA + 196.65637261343872
```

目标 `90 kHz` 对应：

```text
I = 9.28495765474467 mA
```

预检要求标定运行成功，并对反算电流执行 `main_magnetic_field` 全局安全校验。

GUI 和默认 YAML 中的 `MEASURE_FIELD_OFF_CONTROL` 决定是否采集关闭电流源的对照组，默认值为 `true`，因此旧配置保持原有两阶段行为。

开启对照组时，采集顺序为：

1. `field_off`：GS200 输出 OFF，采集 100 帧背景噪声。
2. 保持 GS200 输出 OFF，安全设置 `9.28495765474467 mA`。
3. 打开 GS200 输出，等待 `1 s`。
4. `field_on`：采集 100 帧包含自旋噪声的 PD 波形。

关闭对照组时，跳过第 1 步，安全设置并打开 GS200 后只采集 `field_on`。无论是否测量对照组，正常、异常、取消和 Ctrl+C 都恢复 GS200 的源模式、电流、输出、量程和限流到实验开始前状态。

## 每帧温控门控

每一次示波器尝试都执行完整门控，不是每个阶段只切换一次：

1. 将 `Temp_Switch` 设置为 `0 V DC + Output ON`。
2. 等待 `0.3 s`。
3. SDS 采集完整 `1 s` 波形；整个采集期间保持 `0 V DC + Output ON`。
4. 在 `finally` 中恢复 `5 V DC + Output ON`，即使采集失败或收到取消请求也执行恢复。
5. 恢复后等待 `1 s`，再开始下一帧或下一次自动量程尝试。

帧间、阶段切换期间以及实验结束后均保持 `5 V DC + Output ON`。

## 示波器完整帧与自动量程

- 固定采样率存储模式为 `FSRate`，配置后回读实际采样率和实际点数。
- 每帧执行 `RUN → FTRIG 强制提交一帧 → 等待 FTRIG 完成 → STOP → 读取波形`，不要求输入信号满足边沿触发条件。
- 返回点数少于 preamble 声明值、少于 `WELCH_NPERSEG` 或实际时长不足时重试，连续三次失败才终止。
- 初始量程为 `0.4 V/div`，范围限制为 `0.01–10 V/div`。
- `SCOPE_VERTICAL_DIVISIONS=8` 表示示波器垂直方向总共 8 格；自动量程使用的单侧范围为 `V/div × 8 / 2`。
- 使用波形中心调整 SDS offset；目标 offset 与当前值的差异不超过单侧显示范围的 `0.2` 时直接接受，超过该范围才重新居中。
- 使用相对中心的半峰峰值调整量程；首次波形同时是候选正式帧，量程和 offset 都满足要求时直接保存，否则才按新设置重新采集。每帧最多尝试三次，并且只保存最终接受的波形。
- 若测量两个阶段，量程和 offset 状态会跨阶段连续继承。

## 原始数据

```text
data/Projection_noise/<run>/
  experiment_config.yaml
  raw/
    scope_acquisition_index.npz
    field_off/                  # 仅 MEASURE_FIELD_OFF_CONTROL=true 时存在
      waveform_0000.npz
      ...
      waveform_0099.npz
    field_on/
      waveform_0000.npz
      ...
      waveform_0099.npz
  results/
```

波形采用紧凑 ADC NPZ，只保存原始 ADC 码、preamble 与配置快照；分析时无损重建电压和真实时间轴。索引保存阶段、帧号、实际采样率、实际时长、量程、offset、自动量程尝试次数和 GS200 状态。

## PSD 与洛伦兹拟合

每帧使用以下 Welch 参数：

- Hann 窗；
- `WELCH_NPERSEG=50000`；
- 50% overlap；
- constant detrend；
- 单边 PSD density，单位 `V²/Hz`。

开启对照组时，分别平均 `field_off` 和 `field_on` 的 PSD，并计算：

```text
ΔS(f) = S_field_on(f) - S_field_off(f)
```

关闭对照组时，不生成 `field_off/`，直接使用 `S_field_on(f)` 作为拟合输入。结果中的 `control_group_measured` 和 `analysis_mode` 会分别记录是否测量对照组以及使用 `field_on_minus_field_off` 还是 `field_on_only`；图表标签也会据此区分 PSD 差值和主场打开 PSD。

默认只在预测中心 `90 kHz ± 5 kHz` 内拟合：

$$
S_{\mathrm{fit}}(f)=C+\frac{A\gamma^2}{(f-f_0)^2+\gamma^2}.
$$

`A`、`γ`、`f₀` 和 `C` 均由数据决定；`f₀` 仅限制在拟合窗口内，使用稳健损失降低孤立技术尖峰影响。输出：

- 中心频率 `f₀`；
- HWHM `γ` 和 FWHM `2γ`；
- $T_2=1/(2\pi\gamma)$；
- 洛伦兹面积 $\pi A\gamma$；
- 参数不确定度、R² 和残差。

拟合失败时结果明确标记失败，不回退到旧脚本的固定 `328 Hz` 线宽，也不生成理论替代值。新 SDS 运行不计算 κ̃² 或 PNL。

## 分析结果

```text
results/
  psd_spectra.npz
  lorentzian_fit.npz
  lorentzian_fit.csv
  scope_psd_overview.png
  spin_noise_lorentzian_fit.png
  analysis.yaml
  analysis.json
```

所有图中坐标轴、图例、标题和注释使用英文。

## 历史数据兼容

若指定运行目录仍包含旧文件：

```text
raw/waveforms_phase1_light.npz
raw/waveforms_thermal.npz
```

离线入口会自动使用 HF2 X/Y 兼容分析分支，输出 `legacy_psd_spectra.npz` 和兼容图；不会把旧基带数据套入新的 90 kHz SDS 拟合模型。
