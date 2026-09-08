---
title: Z 任意波实际电流波形验证
type: Z_AW_Current_Waveform_Scope_Check
scan_mode: point_by_point
defaults:
  SCOPE_CYCLES: 3
  SCOPE_REPEATS: 5
  CONTROL_WAVEFORM_SOURCE: theory
mapping_keys:
  Z_magnetic_field:
    role: arbitrary_output
  Time_sequence_2:
    role: common_trigger
  scope_waveform:
    role: CH3_low_side_sense_and_CH4_trigger
required_devices:
  - DG4000
  - SDS
learned_notes:
  - 命令电压仍由独立的历史 Z 电压标定生成，比较目标则由实际电流耦合标定生成。
  - CH3 采样电阻电压是闭环判据；线圈端电压实验仅保留为诊断。
---

# Z 任意波实际电流波形验证

稳定实验 ID 为 `z-aw-current-waveform-scope-check`。选择 `theory` 时目标电流为 `CONTROL_SCALE * Omega_ctrl / K_Z_Hz_per_A`；选择 `corrected_run` 时直接加载闭环冻结运行中的目标电流和命令波形，不再经过旧电压标定。实测电流为 `V_R/R_s`。分析报告固定延迟对齐后的形状相关系数、NRMSE、仿射增益和偏置、峰峰值比例、频谱 NRMSE、重复标准差以及 CH3 饱和诊断。

该实验用于闭环前基线、每轮快速验证和最终冻结波形确认。入口为 `experiments/Z_AW_Current_Waveform_Scope_Check.py` 和 `experiments/Z_AW_Current_Waveform_Scope_Check_plot.py`。

通过 GUI 运行采集时会自动调用离线分析器。分析成功后，运行配置写入
`analysis_status: completed`；如果分析失败，任务会明确标记为失败，并在
`experiment_config.yaml` 中保存 `analysis_status: failed` 和 `analysis_error`，原始采集数据仍保留，可从运行记录中重新分析。
