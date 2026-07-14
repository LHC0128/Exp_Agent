---
title: XY DirectAW 恒定包络电压标定
type: XY_DirectAW_DC_Calibration
scan_mode: nested_scan
defaults:
  XY_ENV_VOLTAGE_START_V: -2.0
  XY_ENV_VOLTAGE_STOP_V: 2.0
  XY_ENV_VOLTAGE_POINTS: 17
  XY_AW_OUTPUT_VPP: 4.0
  XY_AW_OUTPUT_OFFSET_V: 0.0
  Z_RF_FREQ_START_Hz: 500.0
  Z_RF_FREQ_STOP_Hz: 35000.0
  Z_RF_FREQ_STEP_Hz: 500.0
  ACQUISITION_MODE: both
mapping_keys:
  X_magnetic_field:
    role: scan
  Y_magnetic_field:
    role: scan
  Z_magnetic_field:
    role: detection
  X_magnetic_field_AM:
    role: fixed
  Y_magnetic_field_AM:
    role: fixed
required_devices:
  - GS200
  - DG4000
  - DG900
  - TEC103
  - HF2
learned_notes:
  - DirectAW 由 dg_comp CH1/CH2 直接输出，不经过 dg_am 外部 AM 调制。
  - 负包络电压等效于 X/Y 正交旋转场整体翻转 180 度，分析时应同时检查有符号和绝对值映射。
  - 任意波的 amplitude 与 offset 固定为 4 Vpp 和 0 V，扫描只改变上传数组。
  - 0713 数据中 20 kHz 为固定相干杂散；离线标定默认屏蔽 19--21 kHz，并将邻近峰标记为低可信度。
---

# XY DirectAW 恒定包络电压标定

## 目的

在 `RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py` 的物理链路下，标定 DirectAW
恒定包络电压与 Z 向小信号响应峰位之间的关系。实验区别于既有
`XY_DC_Voltage_Calibration.py`：既有脚本通过 `dg_am → dg_comp AM EXT` 施加控制，
本实验由 `dg_comp` 直接输出 X/Y 正交任意波。

## DirectAW 定义

```text
X(t) = V_env cos(2π f_L t + φ)
Y(t) = V_env cos(2π f_L t + φ + 90°)
```

- `V_env`：扫描变量，默认 −2 V 到 +2 V，共 17 点。
- AW 固定输出：4 Vpp、0 V offset。
- AW 重复频率：500 Hz；每周期包含 20 个 10 kHz 载波周期。
- `dg_trigger`：100 Hz 同相方波外触发，两次触发之间恰好包含 5 个 AW 周期。

## 数据与分析

每个包络电压下扫描 Z RF 频率并同时保存：

- Demod 0 `sample.y` 时域数据，用于离线锁相和 FFT；
- Demod 3 的 X/Y/R 采样，用于交叉验证；
- 当前上传的 DirectAW X/Y 波形快照。

离线分析输出四类模型：正电压支路、负电压支路、有符号全局直线以及
`f_peak = K_abs |V_env| + B_abs`。应根据 R²、残差和 `f(+V)-f(-V)` 的对称性判断
最终用于波形换算的模型，不应只根据单一线性拟合下结论。

当前主标定优先使用 Demod3 硬件复矢量均值，并在峰值拟合前屏蔽已确认的
19--21 kHz 固定相干杂散。屏蔽前最大频点、实际删除频点和邻近屏蔽带标记会同步
保存到 `analysis.yaml` / `analysis.json`，以便追溯。该处理可用于当前数据的初步标定，
但真实峰经过屏蔽带时仍会丢失信息，最终标定应增加 Z-RF OFF 背景并对复数
`X+iY` 做 ON-OFF 扣除。

## 入口

- 采集：`experiments/XY_DirectAW_DC_Calibration.py`
- 分析：`experiments/XY_DirectAW_DC_Calibration_plot.py`
