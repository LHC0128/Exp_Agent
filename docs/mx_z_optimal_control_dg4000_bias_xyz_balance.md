---
title: Mx Z 最优控制 DG4000 偏置 XYZ 平衡场
type: Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance
execution_mode: typed_workflow
scan_mode: nested_scan
defaults:
  X_FIELD_START_V: -0.05
  X_FIELD_STOP_V: 0.05
  X_FIELD_POINTS: 11
  Y_FIELD_START_V: -0.05
  Y_FIELD_STOP_V: 0.05
  Y_FIELD_POINTS: 11
  Z_BIAS_START_V: -0.05
  Z_BIAS_STOP_V: 0.05
  Z_BIAS_POINTS: 11
  CONTROL_WAVEFORM_SOURCE: corrected_run
  CORRECTED_CONTROL_SOURCE_RUN: 0824_094157_z_aw_closed_loop_waveform_correction
mapping_keys:
  Z_magnetic_field:
    role: optimal_control_and_z_bias
  Time_sequence_2:
    role: optimal_control_trigger
  X_magnetic_field:
    role: x_dc_balance_scan
  Y_magnetic_field:
    role: y_dc_balance_scan
  Temp_Switch:
    role: point_gate
  lockin_r:
    role: demod0_x_y_r_detection
required_devices:
  - DG900
  - DG4000
  - HF2
learned_notes:
  - GS200 必须与 Z 线圈物理断开；该实验不连接 GS200。
  - Z 总电压为最优控制波形电压加 DG4000 额外 offset。
  - 偏置到频率使用 K_Z_Hz_per_V，偏置到 Bz 使用 7.0 Hz/nT 报告。
  - 结果主判据为 Demod X/Y 复数稳健拟合；实测 Demod0 R 最小点只作为诊断保留。
  - 当前拟合是稳态 Bloch 型二维响应面，尚不是周期最优控制的严格 Bloch/Floquet 拟合。
---

# Mx Z 最优控制 DG4000 偏置 XYZ 平衡场

## 接线与计算

GS200 必须从该 Z 线圈断开。DG4000 的 `Z_magnetic_field` 通道同时承担
Z 周期最优控制任意波和额外 DC 偏置，`Time_sequence_2` 接该 DG4000 的
外部触发输入。

若最优控制波形为 `V_OC(t)`，扫描偏置为 `b`，则总输出为：

```text
V_Z(t; b) = V_OC(t) + b
f_Z(t; b) = f_0,Z + K_Z [V_OC(t) + b]
B_Z(t; b) = f_Z(t; b) / 7.0 Hz/nT
```

该换算只在同一线圈、线性输出、无削顶、无 AC 耦合且静态标定可代表工作频段时成立。
不能直接将 GS200 的 mA 与 DG4000 的 V 相加。

## 控制波形来源

参数 `CONTROL_WAVEFORM_SOURCE` 支持两种来源：

- `theory`：按 `CONTROL_VERSION` 读取理论 `Omega_ctrl` 波形，使用
  `K_Z_Hz_per_V` 换算 DG4000 电压；`CONTROL_SCALE`、`Z_AW_OUTPUT_VPP`
  和 `Z_AW_OUTPUT_OFFSET` 在此模式生效。
- `corrected_run`：读取
  `data/Z_AW_Closed_Loop_Waveform_Correction/<CORRECTED_CONTROL_SOURCE_RUN>/results/corrected_control_waveform.npz`，
  直接使用闭环冻结的电压波形、归一化数组、Vpp、offset 和重复频率，
  不再通过旧 Z 标定重新换算控制电压。

当前默认来源为
`0824_094157_z_aw_closed_loop_waveform_correction`。无论使用哪种来源，
`Z_CALIBRATION_SOURCE_RUN` 仍用于将扫描的 DG4000 偏置换算为频率和 Bz，
并用于验证叠加偏置后的完整输出包络。每次运行都会在 `raw/` 保存来源快照、
`source_manifest.yaml` 和实际下发的 `applied_control_waveform.npz`。

schema v1 历史配置缺少来源字段时自动迁移为 `theory`，保持原有实验行为。

## 扫描与结果

实验以 Z 偏置为外层，X/Y 为连续蛇形扫描。每个点关闭温控开关后同步采集
HF2 Demod0 的 X、Y、R，并保存原始时间序列、均值、标准差和复噪声。

离线结果对每个 Z 偏置平面同时拟合 Demod X/Y 的复数响应：两个分量共享 XY 平衡
中心和线宽，增益矩阵与输出偏置独立，并使用 soft-L1 损失降低孤立毛刺影响。各平面
中心的中位数作为 XY 拟合平衡点；实测网格 `mean(R)` 最小值仍输出在
`measured_grid_minimum` 中，供诊断和复核，不能直接当作真实磁场零点。

Z 方向的主结果仍由静态 `K_Z` 频率标定求零场，报告偏置电压、频率和绝对 Bz；这里的
偏置 Bz 是静态偏置坐标，不代表整个时变波形的瞬时 Bz。当前拟合属于稳态 Bloch 型二维
响应面，不是周期最优控制的严格 Bloch/Floquet 解；若要验证完整周期模型，需要进一步
实现周期稳态 Bloch 或 Floquet 求解器，并用同一套波形和线圈传递函数计算预测的 Demod X/Y。

结果文件包括 `bloch_fit_analysis.yaml/json/npz` 和 `bloch_fit_summary.png`，其中记录每个
Z 平面的拟合中心、复数 `R²`、RMSE、局部 R 诊断量和静态 Z 零场。

## 安全恢复

每个偏置点只改变 DG4000 Z 通道 offset，不覆盖任意波。完整任意波包络和固定
输出包络都要通过 `Z_magnetic_field` 安全限值。正常完成、取消、异常和 Ctrl+C
均恢复运行前的 Z、X、Y 波形、Burst、触发和 Output 状态。
