---
title: Z 任意波线圈波形一致性验证
type: Z_AW_Waveform_Scope_Check
scan_mode: point_by_point
defaults:
  CONTROL_VERSION: v2
  Z_CALIBRATION_SOURCE_RUN: 0730_170249_mx_z_cal
  CONTROL_SCALE: 1.0
  Z_AW_OUTPUT_VPP: 6.0
  Z_AW_OUTPUT_OFFSET: 0.0
  SCOPE_CYCLES: 3
  SCOPE_REPEATS: 5
mapping_keys:
  Z_magnetic_field:
    role: optimal_control_output
  Time_sequence_2:
    role: external_burst_trigger
  scope_waveform:
    role: CH3_coil_measurement_and_CH4_trigger_reference
required_devices:
  - DG4000
  - SDS
learned_notes:
  - Time_sequence_2 CH2 需要接入 Z_magnetic_field CH1 Ext Trig，Z 任意波 Burst 使用外部下降沿。
  - SDS CH3 测量 Z 小线圈两端电压，CH4 接同一触发方波并使用下降沿 2.5 V 触发。
  - CH3/CH4 均配置为 1 MΩ、DC、1×；CH3 的量程按实测波形自动调整。
  - Time_sequence_2 显式使用 High-Z 负载标定，确保 SDS 高阻输入上实际为 0--5 V。
  - SDS 先确认 C4 下降沿触发，再等待完整记录窗；空帧或无沿帧会软复位并重试，最多三次。
  - 分析允许理论波形与 CH3 存在固定时间差，并保存最佳延迟和 CH4 触发时刻。
  - 正常、取消和异常结束均将 Z CH1 与 Time_sequence_2 CH2 归零并关闭。
---

# Z 任意波线圈波形一致性验证

稳定实验 ID 为 `z-aw-waveform-scope-check`。实验从
`CONTROL_RESULTS_ROOT/CONTROL_VERSION/` 读取最优控制波形，并从
`data/Mx_Z_Field_Calibration/<Z_CALIBRATION_SOURCE_RUN>/results/analysis.yaml`
读取 Z 频率标定，按照 Mx Z 最优控制实验的换算关系生成期望 Z 电压：

```text
V_Z(t) = CONTROL_SCALE * Omega_ctrl_Hz(t) / K_Z_Hz_per_V
```

## 接线

- `Z_magnetic_field` CH1：Z 小线圈驱动，配置为外部下降沿触发的无限 Burst。
- `Time_sequence_2` CH2：High-Z 负载标定的 100 Hz、5 Vpp、2.5 V offset 共同触发方波，接入 Z CH1 Ext Trig。
- SDS CH3：跨接 Z 小线圈两端，保存实际线圈电压。
- SDS CH4：接入共同触发方波，作为示波器 EDGE 下降沿触发源。

SDS CH3/CH4 使用 1 MΩ、DC、1×，CH4 触发电平为 2.5 V。运行前必须人工确认信号地和线圈接法不会造成短路或超出输入额定范围。

## 采集与分析

默认每次采集 3 个任意波周期，共重复 5 次。SDS 先进入单次模式并确认 C4 已发生下降沿触发，再等待完整记录窗提交，随后保存 C3/C4 的完整原始时间轴、实际采样率、垂直量程和触发参考波形。空帧或不含下降沿的帧不会进入分析；工作流会软复位 SDS、重放当前采集配置并自动重试，最多尝试三次。

每帧保存的垂直量程和偏置对应实际采集该帧时的设置；即使最后一次自动调整后未再采集，也不会把新设置写入旧帧元数据。

离线分析从 CH4 检测下降沿，以周期相关搜索处理 CH3 与理论波形的固定时间差，并报告归一化形状相关系数、归一化 NRMSE、最佳延迟、仿射增益/偏置和峰峰值比例。实验不设置自动通过阈值，只保存指标和诊断图。

采集和分析入口分别为：

```text
experiments/Z_AW_Waveform_Scope_Check.py
experiments/Z_AW_Waveform_Scope_Check_plot.py
```

每次运行保存：

```text
data/Z_AW_Waveform_Scope_Check/<run>/
  experiment_config.yaml
  raw/
    source_optimal_control_waveform.csv
    source_optimal_control_params.csv
    source_z_calibration_analysis.yaml
    source_manifest.yaml
    applied_control_waveform.npz
    scope_capture_000.npz ...
  results/
    aligned_waveforms.npz
    similarity_diagnostics.npz
    similarity_report.yaml
    similarity_report.json
    waveform_similarity.png
```

## 安全结束

正常、取消或异常结束时，程序停止 SDS，关闭 Z CH1 Burst 并写入 DC 0 V、Output OFF；共同触发 CH2 同样写入 DC 0 V、Output OFF，然后断开 DG4000 和 SDS。
