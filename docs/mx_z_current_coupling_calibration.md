---
title: Mx Z 实际电流-耦合强度标定
type: Mx_Z_Current_Coupling_Calibration
scan_mode: nested_scan
defaults:
  BIDIRECTIONAL_SCAN: true
  CURRENT_SETTLE_TIME_S: 0.2
  SENSE_SCOPE_SAMPLE_RATE_SA_S: 500000.0
  SENSE_SCOPE_DURATION_S: 0.02
  SENSE_SCOPE_INITIAL_SCALE_V_DIV: 1.0
  SENSE_SCOPE_SCALE_MIN_V_DIV: 0.01
  SENSE_SCOPE_SCALE_MAX_V_DIV: 10.0
  SENSE_SCOPE_VERTICAL_DIVISIONS: 8
  SENSE_SCOPE_AUTO_RANGE_LOW_FRACTION: 0.4
  SENSE_SCOPE_AUTO_RANGE_HIGH_FRACTION: 0.9
  SENSE_SCOPE_AUTO_OFFSET_TOLERANCE_FRACTION: 0.05
  SENSE_SCOPE_AUTO_RANGE_MAX_ATTEMPTS: 3
  SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK: false
mapping_keys:
  Z_magnetic_field:
    role: dc_scan
  rf_coil:
    role: resonance_scan
  scope_waveform:
    role: CH3_low_side_sense
  lockin_r:
    role: resonance_detection
required_devices:
  - GS200
  - DG900
  - DG4000
  - HF2
  - SDS
learned_notes:
  - 电流标定绑定主场、温度、光功率、采样电阻和示波器配置。
  - 默认执行正向后反向扫描，用于报告斜率和零电流中心磁滞。
---

# Mx Z 实际电流-耦合强度标定

稳定实验 ID 为 `mx-z-current-coupling-calibration`。每个 Z DC 工作点稳定后，SDS CH3 记录采样电阻电压，再执行 Y RF 共振频扫。离线分析拟合共振中心与实际电流和采样电阻电压的关系，保存 `K_Z_Hz_per_A`、`K_Z_Hz_per_Vsense`、零电流中心、线性度、重复性和正反向磁滞。

该实验不会覆盖历史 `mx-z-field-calibration` 的 `K_Z_Hz_per_V`。入口为 `experiments/Mx_Z_Current_Coupling_Calibration.py` 和 `experiments/Mx_Z_Current_Coupling_Calibration_plot.py`。

## SDS CH3 设置

已有 `Z_Coil_Current_Frequency_Response` 运行表明，当前接线下采样电阻电压最大约为 ±0.82 V。因此 GUI 默认采用：CH3、DC 耦合、1 MΩ、1x，采样率 500 kSa/s，记录时长 20 ms，初始量程 1.0 V/div，垂直 8 格。工作流会在每个 Z 直流点自动居中偏置，并在需要时放大量程，范围为 0.01–10 V/div，超过 0.9 个半屏时放大，最多尝试 3 次。跨 Z 直流工作点默认不缩小量程；只有明确打开 `SENSE_SCOPE_AUTO_RANGE_ALLOW_SHRINK` 才会按 0.4 个半屏阈值缩小。

若更换采样电阻、线圈或驱动范围，应先在示波器上确认波形没有贴边，再在 GUI 中调整 `SENSE_SCOPE_INITIAL_SCALE_V_DIV`。初始量程应覆盖预计峰值，且必须位于 `SENSE_SCOPE_SCALE_MIN_V_DIV` 与 `SENSE_SCOPE_SCALE_MAX_V_DIV` 之间；不要把初始量程重新设为 0.01 V/div。
