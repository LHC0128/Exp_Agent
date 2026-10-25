# 数据分析绘图检查报告（2026-09-20）


## 范围与结果

本轮覆盖 `experiments/`、`lab_workflows/experiment_modules/`、`lab_workflows/analysis/`
及 `src/` 中的实际 Python 绘图程序。共盘点 60 个绘图文件，其中 24 个原有直接创建
或保存图片的入口，本轮修改 41 个文件；其余已通过公共接口绘图。公共样式模块另行
调整默认单面板高度及多行图高度。完整文件清单见文末。

所有受检 Python 绘图入口使用 `lab_workflows.plotting` 创建和保存图片，默认 paper
配置、英文标注、PNG/PDF 成对输出。保留原 PNG 文件名及数值结果契约；没有批量重画
历史目录中的结果。GUI 卡片、实验定义和参数目录的现有简介已核对，本轮未改变实验
目的、采集流程、参数、分析模型或 GUI 操作，因此无需为绘图变化修改这些简介。

Notebook 按用户要求只盘点；12 个含绘图的实验 Notebook 的 SHA-256 与本轮修改前
完全一致。`RF_Field_Sensitivity.ipynb` 在本轮开始前已有工作区改动，本轮保留。

## 已规范的图类与科学表达

| 图类 | 实际处理 |
|---|---|
| 频谱、频响及灵敏度 | 公共图幅/字号/导出；密集热图局部栅格化，文字保持矢量 |
| 光散粒噪声标定 | ASD 与 PSD 单位分开，横轴明确为控制电压，重复误差标为 SD，时域标准差标为 AC RMS |
| T₁ 标定 | 横轴明确为控制电压；衰减概览最多三列；数学下标及标题布局修正 |
| 静磁场优化 | 灵敏度、HWHM、斜率、R² 四个独立面板；有效/拒绝点以颜色和标记共同区分 |
| XYZ/Bloch 响应面 | R 顺序色图、X/Y 零中心发散色标；Local R 与 R² 独立轴；两张复残差幅值图共享非负色限 |
| RF 相位响应 | 周期相位色图固定 0–360°；参数地图三行两列；逐 X 曲线动态行数，修复超过 12 组时越界 |
| 二维噪声分离残差 | 不再对绘图数组作 ±0.5 截断；保留固定显示色限，以色条箭头提示超限 |
| BB 建立/衰减和泵浦率 | 低质量负速率保留原值；移除绘图层额外误差限制；完整对称残差显示范围 |
| 示波器/波形闭环/温控诊断 | 已有公共调用继续继承样式；旧入口接入统一创建与双格式保存 |

标准单面板为 3.4×2.5 in。未显式指定高度的非海报多行图自动增加行高，
避免全部内容挤在单面板高度。完整扫描图仍可能超过期刊主图高度限制，投稿应选取
面板或分页；本次没有为版面自动删掉测量条件。具体规则见[科研绘图规范](plotting_style.md)。

## 仍需审定的项目

1. **BB 占空比的上游误差截断**：`BB_Rpump_DutyScan_Burst_plot.py` 现为显式目录薄入口；原规则位于
   `lab_workflows/experiment_modules/bb_duty_scan_burst/analysis_steps.py` 的 `statistics()`，构造
   `e_GBB` / `e_GD` 时仍使用 `np.minimum(error, abs(rate))`，影响后续权重及输出。
   本轮只移除绘图层的额外限制，保留上游统计方法；投稿前需核对误差传播、拟合质量
   门槛和权重策略，不能把现有误差条当作未截断的标准不确定度。
2. **重复统计口径**：Photon 的 SD 沿用 `ddof=0`，不是 SEM；T₁ 的误差来自拟合
   协方差，不是重复测量误差。图注须写明这些差别。仅调整外观不会补足实验独立重复。
3. **控制电压与光功率**：Photon/T₁ 的旧字段名仍含 power，但数据坐标单位是 V。
   已纠正图中标签；若论文需 mW，必须使用独立的电压–光功率标定，不能直接改单位。
4. **历史 Notebook**：见下表，尚未迁移到公共接口。表内只是静态发现的规范问题，
   没有执行 Notebook 或确认全部历史分析的统计正确性。
5. **旧入口文档与投稿选图**：BB、AW 检查、DG 传递、温控等缺少完整的现行独立
   实验说明；已补[历史分析绘图入口](legacy_analysis_plotting.md)，但它不替代完整
   的实验原理与接线文档。默认样式也不代表已逐图通过 Nature、Science 或 PRL 审稿要求。

## Notebook 盘点（未修改）

Cell 按文件中全部单元从 1 开始计数，包含 Markdown 单元。自定义图幅不一定错误，
但需要改为最终物理尺寸；直接保存、局部样式及 tight 裁边会绕过公共规范。

| 文件 | 问题及 Cell |
|---|---|
| [MORS_feasibility_test.ipynb](../experiments/MORS_feasibility_test.ipynb) | 自定义图幅：7, 11；直接保存：7, 11；自动裁边：7, 11；局部样式：11 |
| [MORS_feasibility_test_v2.ipynb](../experiments/MORS_feasibility_test_v2.ipynb) | 局部样式：9；自定义图幅：9；直接保存：9；自动裁边：9 |
| [MORS_polarization_pulsed.ipynb](../experiments/MORS_polarization_pulsed.ipynb) | 局部样式：8；自定义图幅：8；直接保存：8；自动裁边：8 |
| [psd_debug.ipynb](../experiments/psd_debug.ipynb) | 自定义图幅：5 |
| [RF_Field_Sensitivity.ipynb](../experiments/RF_Field_Sensitivity.ipynb) | 自定义图幅：18；直接保存：18；自动裁边：18 |
| [RF_Field_Sensitivity_AW.ipynb](../experiments/RF_Field_Sensitivity_AW.ipynb) | 自定义图幅：14, 19, 20, 21, 26, 29, 36；直接保存：36；自动裁边：36 |
| [XY_AM_Transfer.ipynb](../experiments/XY_AM_Transfer.ipynb) | 自定义图幅：15, 16；直接保存：15, 16 |
| [XY_Channel_Calibration.ipynb](../experiments/XY_Channel_Calibration.ipynb) | 自定义图幅：18；直接保存：18 |
| [XY_Compensation_Calibration.ipynb](../experiments/XY_Compensation_Calibration.ipynb) | 自定义图幅：9, 11；直接保存：9, 11；自动裁边：9, 11 |
| [XY_MOD_ZeroOffset.ipynb](../experiments/XY_MOD_ZeroOffset.ipynb) | 自定义图幅：14；直接保存：14 |
| [XY_Output_Verification.ipynb](../experiments/XY_Output_Verification.ipynb) | 自定义图幅：12；直接保存：12 |
| [Z_Field_Calibration.ipynb](../experiments/Z_Field_Calibration.ipynb) | 自定义图幅：12, 22；直接保存：12, 22；自动裁边：12, 22 |

## 验证与复现记录

- 60 个绘图文件及公共模块共 61 个文件编译通过。
- Python 相关回归组：67 passed、8 subtests passed；新增迁移检查及样式/频响检查组：
  11 passed。两组有重叠，不相加为总测试数。
- 回归覆盖公共尺寸与双格式导出、频响曲线语义、示波器结果、XYZ/Bloch、相位扫描、
  投影噪声，以及超过 12 组相位曲线不遗漏、不同单位独立轴和比较热图共享色限。
- expcodegen 基础验证及 GUI 生产构建通过；构建保留既有 bundle 体积提示。
- 历史数据只读验证：T₁ `0529_1405_test`、Photon `0708_1735_shot_noise_cal`。
  对迁移前快照和迁移后脚本用 AST 将 `results_dir` 指向独立验证目录，强制无窗口绘图，
  未写入原运行目录。两组 `analysis.yaml` 加载后逐字段比较完全相同。
- 最终 PNG 已查看 T₁ 全条件衰减概览和 Photon X 标定图，未见标题重叠或标签裁切；
  新脚本验证日志无缺字或布局警告。没有全量运行所有历史实验，编译和接口检查不能
  替代每个数据集的人工科学审核。
- 本轮临时迁移及历史验证脚本验证后删除；保留正式回归测试、验证日志和前后样图。

执行过的主要命令：

```powershell
agent_exp_env\Scripts\python.exe -m pytest tests/lab_workflows/test_plotting_style.py tests/lab_workflows/test_rf_frequency_response_comparison.py tests/lab_workflows/test_scope_capture.py tests/lab_workflows/test_mx_z_optimal_control_dg4000_bias_xyz_balance.py tests/lab_workflows/test_mx_z_optimal_control_xy_rf_phase_response.py tests/test_experiment_projection_noise_scope.py -q
agent_exp_env\Scripts\python.exe -m pytest tests/lab_workflows/test_plotting_migration.py tests/lab_workflows/test_plotting_style.py tests/lab_workflows/test_rf_frequency_response_comparison.py -q
```

验证产物：

- [数值比较记录](../results/plotting_migration_review/numeric_comparison.json)
- [真实 Photon 样图 PNG](../results/plotting_migration_review/Photon_shot_noise/after/shot_noise_x.png)、[PDF](../results/plotting_migration_review/Photon_shot_noise/after/shot_noise_x.pdf)
- [真实 T₁ 衰减概览 PNG](../results/plotting_migration_review/T1_Calibration/after/T1_all_decay_curves.png)、[PDF](../results/plotting_migration_review/T1_Calibration/after/T1_all_decay_curves.pdf)
- 四类模拟样图仍通过 `python -m examples.publication_plot_gallery` 复现，明确标注 Synthetic。

## 文档同步

更新了公共规范、README 入口、以下实验文档中的分析/结果正文，以及示波器 CLI 预览说明。
`scope_capture.md` 已有本轮所需的 paper、PNG/PDF 输出说明，核对后无需重复增加段落。

- [mx_keithley_6221_optimal_control_balanced_sensitivity.md](experiments/mx_keithley_6221_optimal_control_balanced_sensitivity.md)
- [mx_keithley_6221_optimal_control_xyz_balance.md](experiments/mx_keithley_6221_optimal_control_xyz_balance.md)
- [mx_main_field_noise_spectrum.md](experiments/mx_main_field_noise_spectrum.md)
- [mx_main_field_scope_noise_spectrum.md](experiments/mx_main_field_scope_noise_spectrum.md)
- [mx_xy_residual_field_calibration.md](experiments/mx_xy_residual_field_calibration.md)
- [mx_y_optimal_control_rf_frequency_response.md](experiments/mx_y_optimal_control_rf_frequency_response.md)
- [mx_z_noise_spectrum.md](experiments/mx_z_noise_spectrum.md)
- [mx_z_optimal_control_dg4000_bias_xyz_balance.md](experiments/mx_z_optimal_control_dg4000_bias_xyz_balance.md)
- [mx_z_optimal_control_xy_leakage_response.md](experiments/mx_z_optimal_control_xy_leakage_response.md)
- [mx_z_optimal_control_xy_noise_spectrum.md](experiments/mx_z_optimal_control_xy_noise_spectrum.md)
- [mx_z_optimal_control_xy_rf_phase_response.md](experiments/mx_z_optimal_control_xy_rf_phase_response.md)
- [mx_z_optimal_control_xyz_balance.md](experiments/mx_z_optimal_control_xyz_balance.md)
- [noise_spectrum_xy_ctrl.md](experiments/noise_spectrum_xy_ctrl.md)
- [Photon_shot_noise.md](experiments/Photon_shot_noise.md)
- [projection_noise.md](experiments/Projection_noise.md)
- [scope_capture.md](experiments/scope_capture.md)
- [sds_acquisition.md](instruments/sds_acquisition.md)
- [static_mag_sens_v2.md](experiments/static_mag_sens_v2.md)
- [T1_calibration.md](experiments/T1_calibration.md)
- [xy_dc_voltage_calibration.md](experiments/xy_dc_voltage_calibration.md)
- [z_coil_inductance_frequency_response.md](experiments/z_coil_inductance_frequency_response.md)

## Python 覆盖清单

“本轮修改”相对于本轮开始时的文件快照，不包含任务开始前的其他工作区改动。
已接入者继续复用公共接口，不为凑齐文件数制造修改。公共样式模块不计入以下 60 个。

| 文件 | 状态 |
|---|---|
| [experiments/AW_ConstOmega_Calibration_Check_AWScale_plot.py](../old/experiments/experiments/AW_ConstOmega_Calibration_Check_AWScale_plot.py) | 本轮修改 |
| [AW_ConstOmega_Calibration_Check_plot.py](../old/experiments/experiments/AW_ConstOmega_Calibration_Check_plot.py)（已归档） | 本轮修改；后续按原字节归档 |
| [AW_Control_Waveform_Scope_Check.py](../old/experiments/experiments/AW_Control_Waveform_Scope_Check.py)（已归档） | 本轮修改；后续按原字节归档，绘图逻辑不变 |
| [experiments/BB_Rpump_DutyScan_Burst_plot.py](../experiments/BB_Rpump_DutyScan_Burst_plot.py) | 本轮修改 |
| [experiments/BB_Rpump_PowerScan_Burst_plot.py](../experiments/BB_Rpump_PowerScan_Burst_plot.py) | 本轮修改 |
| [experiments/DG_AM_to_DG_COMP_Transfer_Check.py](../old/experiments/experiments/DG_AM_to_DG_COMP_Transfer_Check.py) | 本轮修改 |
| [experiments/DG_AM_to_DG_COMP_Transfer_Check_plot.py](../old/experiments/experiments/DG_AM_to_DG_COMP_Transfer_Check_plot.py) | 本轮修改 |
| [experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py](../experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance_Bloch_fit.py) | 本轮修改 |
| [experiments/Noise_Spectrum_XY_Ctrl_v2_plot.py](../old/experiments/experiments/Noise_Spectrum_XY_Ctrl_v2_plot.py) | 本轮修改 |
| [experiments/Photon_shot_noise_plot.py](../experiments/Photon_shot_noise_plot.py) | 本轮修改 |
| [experiments/RF_Field_Sensitivity_AW_plot.py](../experiments/RF_Field_Sensitivity_AW_plot.py) | 本轮修改 |
| [experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py](../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py) | 本轮修改 |
| [experiments/Static_Magnetic_Field_Sensitivity.py](../experiments/Static_Magnetic_Field_Sensitivity.py) | 本轮修改 |
| [Static_Magnetic_Field_Sensitivity_Optimize.py](../old/experiments/experiments/Static_Magnetic_Field_Sensitivity_Optimize.py)（已归档） | 本轮修改；后续按原字节归档 |
| [experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py](../experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py) | 本轮修改 |
| [experiments/T1_Calibration_plot.py](../experiments/T1_Calibration_plot.py) | 本轮修改 |
| [experiments/Temperature_Switch_PID_Cycle_Test.py](../old/experiments/experiments/Temperature_Switch_PID_Cycle_Test.py) | 本轮修改；现已归档 |
| [experiments/Temperature_Switch_PID_Cycle_Test_plot.py](../old/experiments/experiments/Temperature_Switch_PID_Cycle_Test_plot.py) | 本轮修改；现已归档 |
| [XY_AW_Voltage_Calibration_plot.py](../old/experiments/experiments/XY_AW_Voltage_Calibration_plot.py)（已归档） | 本轮修改；后续按原字节归档 |
| [XY_DC_Voltage_Calibration_plot.py](../old/experiments/experiments/XY_DC_Voltage_Calibration_plot.py)（已归档） | 本轮修改；后续按原字节归档 |
| [experiments/Z_Field_Bandwidth_Measurement_plot.py](../experiments/Z_Field_Bandwidth_Measurement_plot.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_y_rf_grid_analysis.py](../lab_workflows/experiment_modules/mx_y_rf_grid_analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/analysis.py](../old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/analysis.py](../old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/phase_plot.py](../old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/phase_plot.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/analysis.py](../old/experiments/lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_main_field_calibration/analysis.py](../lab_workflows/experiment_modules/mx_main_field_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_main_field_noise_spectrum/analysis.py](../old/experiments/lab_workflows/experiment_modules/mx_main_field_noise_spectrum/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/analysis.py](../lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_xy_residual_field_calibration/analysis.py](../lab_workflows/experiment_modules/mx_xy_residual_field_calibration/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/analysis.py](../lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_y_rf_frequency_response/analysis.py](../lab_workflows/experiment_modules/mx_y_rf_frequency_response/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_y_rf_sensitivity/analysis.py](../lab_workflows/experiment_modules/mx_y_rf_sensitivity/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/analysis.py](../lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_z_current_coupling_calibration/analysis.py](../lab_workflows/experiment_modules/mx_z_current_coupling_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_z_field_calibration/analysis.py](../lab_workflows/experiment_modules/mx_z_field_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_z_noise_spectrum/analysis.py](../old/experiments/lab_workflows/experiment_modules/mx_z_noise_spectrum/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/analysis.py](../lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/phase_plot.py](../lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/phase_plot.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/analysis.py](../lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/analysis.py](../lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/analysis.py](../lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/analysis.py](../lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/noise_spectrum_xy/analysis.py](../lab_workflows/experiment_modules/noise_spectrum_xy/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/analysis.py](../lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/projection_noise/analysis.py](../lab_workflows/experiment_modules/projection_noise/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/projection_noise/legacy_analysis.py](../lab_workflows/experiment_modules/projection_noise/legacy_analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/analysis.py](../lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/scope_capture/analysis.py](../lab_workflows/experiment_modules/scope_capture/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/static_sensitivity/analysis.py](../lab_workflows/experiment_modules/static_sensitivity/analysis.py) | 本轮修改 |
| [lab_workflows/experiment_modules/t2_calibration/analysis.py](../lab_workflows/experiment_modules/t2_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/analysis.py](../lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/analysis.py](../lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/legacy_analysis.py](../lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/legacy_analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/analysis.py](../lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_aw_waveform_scope_check/analysis.py](../lab_workflows/experiment_modules/z_aw_waveform_scope_check/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_coil_current_frequency_response/analysis.py](../lab_workflows/experiment_modules/z_coil_current_frequency_response/analysis.py) | 已有公共接口 |
| [lab_workflows/experiment_modules/z_coil_inductance_frequency_response/analysis.py](../lab_workflows/experiment_modules/z_coil_inductance_frequency_response/analysis.py) | 本轮修改 |
| [lab_workflows/analysis/rf_frequency_response_comparison.py](../lab_workflows/analysis/rf_frequency_response_comparison.py) | 本轮修改 |
| [src/sds_acquisition/cli.py](../src/sds_acquisition/cli.py) | 本轮修改 |
