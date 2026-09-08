---
title: Z 线圈电感效应频率响应
type: Z_Coil_Inductance_Frequency_Response
scan_mode: point_by_point
defaults:
  FREQUENCY_START_HZ: 10.0
  FREQUENCY_STOP_HZ: 10000.0
  FREQUENCY_POINTS: 31
  DRIVE_AMPLITUDE_VPP: 1.0
  DRIVE_OFFSET_V: 0.0
  FREQUENCY_SETTLE_S: 0.5
  SCOPE_CYCLES: 3
  SCOPE_REPEATS: 5
  SCOPE_SAMPLE_RATE_SA_S: 500000.0
mapping_keys:
  Z_magnetic_field:
    role: sine_drive
  Time_sequence_2:
    role: external_burst_trigger
  scope_waveform:
    role: CH3_coil_voltage_and_CH4_trigger_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - Time_sequence_2 CH2 接 Z_magnetic_field CH1 Ext Trig，Z 正弦使用外部下降沿 Burst。
  - SDS CH3 测量 Z 线圈两端电压，CH4 接共同触发参考，并以 2.5 V 下降沿触发。
  - CH3/CH4 使用 1 MOhm、DC、1x；CH3 量程按实测波形自动调整。
  - 当前接线没有独立电流测量，因此结果用于观察端电压频率响应，不能单独反演唯一电感值。
---

# Z 线圈电感效应频率响应

## 实验目的

使用固定 `1.0 Vpp`、`0 V offset` 的 Z 方向正弦驱动，在 `10--10000 Hz` 之间线性扫描 31 个频率点。每个频率点采集 3 个周期并重复 5 次，用于观察线圈端电压相对于理想 DG 设定正弦的幅值、相位和波形差异。

SDS 的每次记录窗口取 `max(3/f, 2/f_trigger)`，其中 `f_trigger` 是共同触发频率。这样在高频扫描时仍能保证 C4 记录中至少包含共同触发的下降沿；否则窗口可能只截到方波高电平，示波器虽已触发，离线仍无法确定相位零点。

`SCOPE_SAMPLE_RATE_SA_S` 是 GUI 中可调的基础参数，默认值为 `500000 Sa/s`。该参数不再与终止频率按固定倍数绑定，实验会按设置值配置 SDS，并把仪器实际采样率保存到运行配置中。

理论值是 DG 的理想设定值，不是独立测量到的 DG 输出端电压。由于当前只测量线圈端电压，实验不能把信号源输出误差、线圈电阻和电感效应唯一分离，也不直接报告绝对电感值。

## 接线

- `Z_magnetic_field` CH1：驱动 Z 线圈。
- `Time_sequence_2` CH2：共同触发方波，接 Z CH1 外部触发输入。
- SDS CH3：跨接 Z 线圈两端，保存线圈端电压。
- SDS CH4：接共同触发方波，作为示波器下降沿触发参考。
- SDS CH3/CH4：1 MOhm、DC、1x，CH4 触发电平 2.5 V。

运行前应人工确认接地、线圈和示波器输入不会短路，且驱动端电压在实验允许范围内。

## 数据和分析

采集入口为：

```text
experiments/Z_Coil_Inductance_Frequency_Response.py
```

离线分析入口为：

```text
experiments/Z_Coil_Inductance_Frequency_Response_plot.py
```

每次运行保存：

```text
data/Z_Coil_Inductance_Frequency_Response/<run>/
  experiment_config.yaml
  raw/
    scope_f000_r00.npz ...
    frequency_index.npz
    frequency_index.json
  results/
    frequency_response.npz
    frequency_response.yaml
    frequency_response.json
    waveform_overview.png
    frequency_response.png
```

`waveform_overview.png` 按频率分别绘制子图，低频点优先保留，不再把不同频率叠加到同一坐标轴；长记录仅在绘图时均匀抽点，原始数据和拟合不变。

每帧使用

```text
v(t) = offset + a sin(2 pi f t) + b cos(2 pi f t)
```

拟合幅值、相位、偏置、残差 RMS 和相关系数，并与理想幅值 `DRIVE_AMPLITUDE_VPP / 2` 比较。报告中的幅值比、dB、相位差和重复标准差用于判断频率相关的线圈端电压变化。

对幅值数据同时拟合一阶端电压响应模型：

```text
A(f) = sqrt((A0^2 + Ainf^2 * (f/fc)^2) / (1 + (f/fc)^2))
```

拟合结果（`A0`、`Ainf`、`fc`、`R2` 和残差 RMS）写入 `frequency_response.yaml/json/npz`，并叠加到 `frequency_response.png`。其中 `A0` 是拟合的零频幅值；报告还给出每个频率相对 `A0` 的归一化幅值、百分比变化、带符号的幅值下降百分比和 dB 变化。最低实测频率归一化结果仍单独保留，便于区分“10 Hz 参考”与“拟合零频外推”。

## 安全收尾

正常结束、取消或异常时都会停止 SDS，将 Z CH1 和共同触发 CH2 设置为 DC 0 V、Output OFF，并断开本实验连接的设备。
