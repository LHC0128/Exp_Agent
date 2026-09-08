---
title: Z 线圈实际电流频率响应
type: Z_Coil_Current_Frequency_Response
scan_mode: point_by_point
defaults:
  FREQUENCY_STOP_HZ: 120000.0
  FREQUENCY_SETTLE_S: 0.1
  DRIVE_AMPLITUDE_VPP: 0.1
  SCOPE_SAMPLE_RATE_SA_S: 10000000.0
  SCOPE_MIN_SAMPLE_RATE_SA_S: 100000.0
  SCOPE_SAMPLES_PER_CYCLE: 64
  SCOPE_CAPTURE_GUARD_S: 0.1
mapping_keys:
  Z_magnetic_field:
    role: sine_drive
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_low_side_sense_and_CH4_trigger
required_devices:
  - DG4000
  - SDS
learned_notes:
  - CH3 只测低端采样电阻，结果传递函数的输出量为实际线圈电流。
  - 阻值、额定功率和峰值电流上限为零时预检失败。
---

# Z 线圈实际电流频率响应

稳定实验 ID 为 `z-coil-current-frequency-response`。实验以固定低幅度正弦扫描 Z 通道，CH3 测量低端采样电阻电压并按 `I=V_R/R_s` 换算实际电流，CH4 提供共同下降沿触发。每帧检查峰值电流和采样电阻 RMS 功率。

离线分析保存 `frequency_response.npz`、`frequency_response.yaml` 和 `transfer_function.png`。结果包含 A/V 复数传递函数、实际采样率、幅值、相位、等效延迟、重复标准差、拟合残差和饱和诊断；只有未饱和、正弦拟合相关性足够且相位重复性合格的频点标记为可靠。

为避免低频点使用固定 `10 MHz` 采样率导致示波器深存储记录过大，当前实验按频率动态选择采样率：不低于 `SCOPE_MIN_SAMPLE_RATE_SA_S`，并尽量保持 `SCOPE_SAMPLES_PER_CYCLE` 点/周期，同时不超过 `SCOPE_SAMPLE_RATE_SA_S`。`SCOPE_CAPTURE_GUARD_S` 是 SDS 完成深存储帧后的保护等待；若实机出现空帧或不完整帧，应优先增大该参数。切频等待默认 `0.1 s`，如信号源切频后仍有瞬态可调大 `FREQUENCY_SETTLE_S`。

SDS 的每次记录窗口还会自动取不小于共同触发信号两个周期的长度。默认共同触发为 `100 Hz`，所以高频点的记录窗口至少约 `20 ms`，避免窗口只截到 C4 高电平而无法在下载波形中找到下降沿。

入口为 `experiments/Z_Coil_Current_Frequency_Response.py` 和 `experiments/Z_Coil_Current_Frequency_Response_plot.py`。
