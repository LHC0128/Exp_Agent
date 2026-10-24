# 采集时序、系统稳定与 TEC 设置

普通气室实验统一执行：

1. 设置实际仪器配置。
2. 将 `Temp_Switch` 设置为 `0 V DC + Output ON`，关闭温控而保持信号源物理输出开启。
3. 等待 `SYSTEM_SETTLE_S`，覆盖配置和温控门控的共同稳定。
4. 将采集结果存于当前点、配对或既有分组的内存缓存。
5. 恢复 `Temp_Switch` 开启电平并保持 `Output ON`。
6. 保存波形、采集元数据、扫描快照及结果。

初始化先创建运行目录并保存初始配置。分组首点的配置在关闭前完成；组内后续变更后重新等待，末尾不足一组也先恢复再落盘。慢磁盘不延长正常的温控关闭窗口。缓冲限于现有采集组，进程崩溃仍可能丢失当前未保存组。

使用公共运行目录步骤时先原子创建新目录；同秒重跑或并发撞名追加序号，不复用已有目录，即使旧目录缺少 `raw/` 也不覆盖旧配置。目录序号不改变参数快照中的运行标签。

每个普通实验仅提供一个 `SYSTEM_SETTLE_S`。旧短等待按原默认补足缺项后取最大值；新键存在时优先，保存只使用新键，不重写历史运行。GUI 按 schema 的 `legacy_defaults` 填回旧参数。设备专用准备等待在温控开启时完成，温控恢复与触发协议等待继续保留。

TEC 只下发 UI 目标温度，不启动或停止 TEC，也不轮询温度达标。串口失败告警并跳过；快照保留目标温度，实测未知时为 `null`。系统稳定等待不表示气室温度已经达标。

取消、采集或配对第二态失败时先尝试恢复，再保存已采内存数据。恢复失败停止后继采集并报告错误，仍尽力保存已有数据；清理或磁盘异常不覆盖原始采集异常。公共入口位于 `lab_workflows/steps/acquisition_window.py`；假仪器回归位于 `lab_workflows/test_acquisition_order.py` 及各实验原有测试。

BB 占空比/功率扫描与 T1 在恢复或后续准备失败时，把已读帧抢救到 `raw/recovery_shot_*.npz` 或 `raw/recovery_t1_*.npz`；原有正常运行文件与离线分析入口保留。自适应量程调整在温控开启时完成，新尝试重新执行系统稳定与必要触发等待。

## 正式实验覆盖

这里记录采集规范的历史整改范围，包括已移至 `old/experiments/`、GUI 仅留卡片的归档项，不代表全部仍有运行入口；具体业务测试覆盖由验收报告单独列出，不能从本表推断测试场景齐全。

清单从注册表按稳定 ID 核对；整改包括时序、唯一等待参数和适用的 TEC 行为，各实验问题不相同。当前参数来源以各实验说明、GUI schema 与 `params/experiments/` 实际保存文件为准。

| 稳定实验 ID | 采集入口 | 当前说明 |
|---|---|---|
| `bell-bloom-z-field-calibration` | [Bell_Bloom_Z_Field_Calibration.py](../experiments/Bell_Bloom_Z_Field_Calibration.py) | [bell_bloom_z_field_calibration.md](bell_bloom_z_field_calibration.md) |
| `detection-chain-frequency-response` | [Detection_Chain_Frequency_Response.py](../experiments/Detection_Chain_Frequency_Response.py) | [detection_chain_frequency_response.md](detection_chain_frequency_response.md) |
| `scope-capture` | [Scope_Capture.py](../experiments/Scope_Capture.py) | [scope_capture.md](scope_capture.md) |
| `static-sensitivity` | [Static_Magnetic_Field_Sensitivity.py](../experiments/Static_Magnetic_Field_Sensitivity.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[plotting_audit_20260920.md](plotting_audit_20260920.md)、[static_mag_sens_v2.md](static_mag_sens_v2.md) |
| `mx-y-rf-sensitivity` | [Mx_Y_RF_Sensitivity.py](../experiments/Mx_Y_RF_Sensitivity.py) | [mx_y_rf_power_optimization.md](mx_y_rf_power_optimization.md)、[mx_y_rf_probe_detuning_optimization.md](mx_y_rf_probe_detuning_optimization.md)、[mx_y_rf_sensitivity.md](mx_y_rf_sensitivity.md)、[mx_y_rf_sensitivity_drift.md](mx_y_rf_sensitivity_drift.md) |
| `mx-y-rf-frequency-response` | [Mx_Y_RF_Frequency_Response.py](../experiments/Mx_Y_RF_Frequency_Response.py) | [mx_y_rf_frequency_response.md](mx_y_rf_frequency_response.md) |
| `mx-y-optimal-control-rf-frequency-response` | [Mx_Y_Optimal_Control_RF_Frequency_Response.py](../experiments/Mx_Y_Optimal_Control_RF_Frequency_Response.py) | [mx_y_optimal_control_rf_frequency_response.md](mx_y_optimal_control_rf_frequency_response.md) |
| `mx-y-rf-sensitivity-drift` | [Mx_Y_RF_Sensitivity_Drift.py](../experiments/Mx_Y_RF_Sensitivity_Drift.py) | [mx_y_rf_sensitivity_drift.md](mx_y_rf_sensitivity_drift.md) |
| `mx-z-optimal-control-rf-sensitivity` | [Mx_Z_Optimal_Control_RF_Sensitivity.py](../experiments/Mx_Z_Optimal_Control_RF_Sensitivity.py) | [mx_keithley_6221_optimal_control_rf_sensitivity.md](mx_keithley_6221_optimal_control_rf_sensitivity.md)、[mx_z_optimal_control_rf_sensitivity.md](mx_z_optimal_control_rf_sensitivity.md) |
| `mx-z-optimal-control-xy-rf-sensitivity` | [Mx_Z_Optimal_Control_XY_RF_Sensitivity.py](../experiments/Mx_Z_Optimal_Control_XY_RF_Sensitivity.py) | [mx_z_optimal_control_xy_rf_sensitivity.md](mx_z_optimal_control_xy_rf_sensitivity.md) |
| `mx-z-optimal-control-xy-noise-spectrum` | [Mx_Z_Optimal_Control_XY_Noise_Spectrum.py](../experiments/Mx_Z_Optimal_Control_XY_Noise_Spectrum.py) | [mx_z_optimal_control_xy_noise_spectrum.md](mx_z_optimal_control_xy_noise_spectrum.md) |
| `mx-z-optimal-control-xy-leakage-response` | [Mx_Z_Optimal_Control_XY_Leakage_Response.py](../experiments/Mx_Z_Optimal_Control_XY_Leakage_Response.py) | [mx_z_optimal_control_xy_leakage_response.md](mx_z_optimal_control_xy_leakage_response.md) |
| `mx-z-optimal-control-xy-rf-phase-response` | [Mx_Z_Optimal_Control_XY_RF_Phase_Response.py](../experiments/Mx_Z_Optimal_Control_XY_RF_Phase_Response.py) | [mx_z_optimal_control_xy_rf_phase_response.md](mx_z_optimal_control_xy_rf_phase_response.md) |
| `mx-z-optimal-control-xyz-balance` | [Mx_Z_Optimal_Control_XYZ_Balance.py](../experiments/Mx_Z_Optimal_Control_XYZ_Balance.py) | [mx_keithley_6221_optimal_control_xyz_balance.md](mx_keithley_6221_optimal_control_xyz_balance.md)、[mx_z_optimal_control_xyz_balance.md](mx_z_optimal_control_xyz_balance.md) |
| `mx-z-optimal-control-dg4000-bias-xyz-balance` | [Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py](../experiments/Mx_Z_Optimal_Control_DG4000_Bias_XYZ_Balance.py) | [mx_z_optimal_control_dg4000_bias_xyz_balance.md](mx_z_optimal_control_dg4000_bias_xyz_balance.md) |
| `mx-z-current-coupling-calibration` | [Mx_Z_Current_Coupling_Calibration.py](../experiments/Mx_Z_Current_Coupling_Calibration.py) | [mx_y_optimal_control_rf_frequency_response.md](mx_y_optimal_control_rf_frequency_response.md)、[mx_z_current_coupling_calibration.md](mx_z_current_coupling_calibration.md) |
| `mx-y-rf-power-optimization` | [Mx_Y_RF_Power_Optimization.py](../experiments/Mx_Y_RF_Power_Optimization.py) | [mx_y_rf_power_optimization.md](mx_y_rf_power_optimization.md) |
| `mx-y-rf-probe-detuning-optimization` | [Mx_Y_RF_Probe_Detuning_Optimization.py](../experiments/Mx_Y_RF_Probe_Detuning_Optimization.py) | [mx_y_rf_probe_detuning_optimization.md](mx_y_rf_probe_detuning_optimization.md) |
| `mx-main-field-calibration` | [Mx_Main_Field_Calibration.py](../experiments/Mx_Main_Field_Calibration.py) | [mx_main_field_calibration.md](mx_main_field_calibration.md) |
| `mx-keithley-6221-main-field-calibration` | [Mx_Keithley_6221_Main_Field_Calibration.py](../old/experiments/experiments/Mx_Keithley_6221_Main_Field_Calibration.py) | [keithley_6221.md](keithley_6221.md)、[mx_keithley_6221_main_field_calibration.md](mx_keithley_6221_main_field_calibration.md)、[mx_keithley_6221_optimal_control_balanced_sensitivity.md](mx_keithley_6221_optimal_control_balanced_sensitivity.md) |
| `mx-keithley-6221-optimal-control-rf-sensitivity` | [Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py](../old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_RF_Sensitivity.py) | [keithley_6221.md](keithley_6221.md)、[mx_keithley_6221_optimal_control_rf_sensitivity.md](mx_keithley_6221_optimal_control_rf_sensitivity.md) |
| `mx-keithley-6221-optimal-control-xyz-balance` | [Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py](../old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_XYZ_Balance.py) | [mx_keithley_6221_optimal_control_xyz_balance.md](mx_keithley_6221_optimal_control_xyz_balance.md) |
| `mx-keithley-6221-optimal-control-balanced-sensitivity` | [Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py](../old/experiments/experiments/Mx_Keithley_6221_Optimal_Control_Balanced_Sensitivity.py) | [mx_keithley_6221_optimal_control_balanced_sensitivity.md](mx_keithley_6221_optimal_control_balanced_sensitivity.md) |
| `mx-main-field-noise-spectrum` | [Mx_Main_Field_Noise_Spectrum.py](../old/experiments/experiments/Mx_Main_Field_Noise_Spectrum.py) | [mx_main_field_noise_spectrum.md](mx_main_field_noise_spectrum.md) |
| `mx-main-field-scope-noise-spectrum` | [Mx_Main_Field_Scope_Noise_Spectrum.py](../experiments/Mx_Main_Field_Scope_Noise_Spectrum.py) | [mx_main_field_scope_noise_spectrum.md](mx_main_field_scope_noise_spectrum.md) |
| `mx-z-field-calibration` | [Mx_Z_Field_Calibration.py](../experiments/Mx_Z_Field_Calibration.py) | [mx_z_current_coupling_calibration.md](mx_z_current_coupling_calibration.md)、[mx_z_field_calibration.md](mx_z_field_calibration.md) |
| `mx-z-noise-spectrum` | [Mx_Z_Noise_Spectrum.py](../old/experiments/experiments/Mx_Z_Noise_Spectrum.py) | [mx_z_noise_spectrum.md](mx_z_noise_spectrum.md) |
| `mx-xy-residual-field-calibration` | [Mx_XY_Residual_Field_Calibration.py](../experiments/Mx_XY_Residual_Field_Calibration.py) | [mx_xy_residual_field_calibration.md](mx_xy_residual_field_calibration.md) |
| `noise-spectrum-xy` | [Noise_Spectrum_XY_Ctrl.py](../experiments/Noise_Spectrum_XY_Ctrl.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[noise_spectrum_xy_ctrl.md](noise_spectrum_xy_ctrl.md)、[noise_spectrum_xy_ctrl_known_noise.md](noise_spectrum_xy_ctrl_known_noise.md)、[noise_spectrum_xy_ctrl_uncontrolled_probe_am.md](noise_spectrum_xy_ctrl_uncontrolled_probe_am.md)、[noise_spectrum_xy_demod3_r.md](noise_spectrum_xy_demod3_r.md) |
| `noise-spectrum-xy-known-noise` | [Noise_Spectrum_XY_Ctrl_Known_Noise.py](../experiments/Noise_Spectrum_XY_Ctrl_Known_Noise.py) | [noise_spectrum_xy_ctrl_known_noise.md](noise_spectrum_xy_ctrl_known_noise.md) |
| `noise-spectrum-xy-uncontrolled-probe-am` | [Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM.py](../experiments/Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM.py) | [noise_spectrum_xy_ctrl_uncontrolled_probe_am.md](noise_spectrum_xy_ctrl_uncontrolled_probe_am.md) |
| `noise-spectrum-xy-demod3-r` | [Noise_Spectrum_XY_Demod3_R.py](../experiments/Noise_Spectrum_XY_Demod3_R.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[noise_spectrum_xy_demod3_r.md](noise_spectrum_xy_demod3_r.md) |
| `noise-spectrum-xy-v2` | [Noise_Spectrum_XY_Ctrl_v2.py](../old/experiments/experiments/Noise_Spectrum_XY_Ctrl_v2.py) | [detection_chain_frequency_response.md](detection_chain_frequency_response.md)、[legacy_lockin_experiments.md](legacy_lockin_experiments.md) |
| `photon-shot-noise` | [Photon_shot_noise.py](../experiments/Photon_shot_noise.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[Photon_shot_noise.md](Photon_shot_noise.md) |
| `projection-noise` | [Projection_noise.py](../experiments/Projection_noise.py) | [Projection_noise.md](Projection_noise.md) |
| `rf-sensitivity-aw` | [RF_Field_Sensitivity_AW.py](../experiments/RF_Field_Sensitivity_AW.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[rf_field_measurement.md](rf_field_measurement.md)、[rf_field_measurement_known_noise.md](rf_field_measurement_known_noise.md)、[rf_field_measurement_r.md](rf_field_measurement_r.md) |
| `rf-sensitivity-aw-r` | [RF_Field_Sensitivity_AW_R.py](../old/experiments/experiments/RF_Field_Sensitivity_AW_R.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[rf_field_measurement_r.md](rf_field_measurement_r.md) |
| `rf-sensitivity-aw-known-noise` | [RF_Field_Sensitivity_AW_Known_Noise.py](../experiments/RF_Field_Sensitivity_AW_Known_Noise.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[rf_field_measurement_known_noise.md](rf_field_measurement_known_noise.md) |
| `rf-sensitivity-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep.py](../old/experiments/experiments/RF_Field_Sensitivity_AW_FreqSweep.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md) |
| `rf-sensitivity-direct-aw-frequency` | [RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py](../experiments/RF_Field_Sensitivity_AW_FreqSweep_DirectAW.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[rf_field_measurement.md](rf_field_measurement.md)、[rf_field_measurement_2.md](rf_field_measurement_2.md)、[xy_direct_aw_dc_calibration.md](xy_direct_aw_dc_calibration.md) |
| `rf-sensitivity-constxy-frequency` | [RF_Field_Sensitivity_ConstXY_FreqSweep.py](../experiments/RF_Field_Sensitivity_ConstXY_FreqSweep.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[xy_dc_voltage_calibration.md](xy_dc_voltage_calibration.md) |
| `z-field-bandwidth` | [Z_Field_Bandwidth_Measurement.py](../experiments/Z_Field_Bandwidth_Measurement.py) | [z_field_bandwidth.md](z_field_bandwidth.md) |
| `bb-duty-scan-burst` | [BB_Rpump_DutyScan_Burst.py](../experiments/BB_Rpump_DutyScan_Burst.py) | [bb_duty_scan_burst.md](bb_duty_scan_burst.md)：逐帧恢复后保存，重复锁档、取消与恢复失败抢救已读数据 |
| `bb-power-scan-burst` | [BB_Rpump_PowerScan_Burst.py](../experiments/BB_Rpump_PowerScan_Burst.py) | [bb_power_scan_burst.md](bb_power_scan_burst.md)：逐帧恢复后保存，结束保留门控/Burst/SYNC 与最后写入的 Pump 电压 |
| `t1-calibration` | [T1_Calibration.py](../experiments/T1_Calibration.py) | [T1_calibration.md](T1_calibration.md) |
| `t2-calibration` | [T2_Calibration.py](../experiments/T2_Calibration.py) | [T2_relaxation.md](T2_relaxation.md) |
| `xy-aw-voltage-calibration`（已归档） | [XY_AW_Voltage_Calibration.py](../old/experiments/experiments/XY_AW_Voltage_Calibration.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md) |
| `xy-dc-voltage-calibration`（已归档） | [XY_DC_Voltage_Calibration.py](../old/experiments/experiments/XY_DC_Voltage_Calibration.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[xy_dc_voltage_calibration.md](xy_dc_voltage_calibration.md)、[xy_direct_aw_dc_calibration.md](xy_direct_aw_dc_calibration.md) |
| `xy-direct-aw-dc-calibration` | [XY_DirectAW_DC_Calibration.py](../experiments/XY_DirectAW_DC_Calibration.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[xy_direct_aw_dc_calibration.md](xy_direct_aw_dc_calibration.md) |
| `aw-const-omega-check`（已归档） | [AW_ConstOmega_Calibration_Check.py](../old/experiments/experiments/AW_ConstOmega_Calibration_Check.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md) |
| `aw-const-omega-awscale-check` | [AW_ConstOmega_Calibration_Check_AWScale.py](../old/experiments/experiments/AW_ConstOmega_Calibration_Check_AWScale.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md) |
| `static-sensitivity-optimize`（已归档） | [Static_Magnetic_Field_Sensitivity_Optimize.py](../old/experiments/experiments/Static_Magnetic_Field_Sensitivity_Optimize.py) | [legacy_lockin_experiments.md](legacy_lockin_experiments.md)、[plotting_audit_20260920.md](plotting_audit_20260920.md)、[static_mag_sens_v2.md](static_mag_sens_v2.md) |
| `static-sensitivity-optimize-local` | [Static_Magnetic_Field_Sensitivity_Optimize_Local.py](../experiments/Static_Magnetic_Field_Sensitivity_Optimize_Local.py) | [局部优化 DC 逐点与分组温控](static_sensitivity_optimize_local.md)：每候选复用标准静磁场子运行，恢复后保存并即时分析，硬件错误停止后继 |

## 适用例外

- `aw-waveform-scope-check`：代码已归档、GUI 仅留卡片；纯电学或波形工具的历史例外和原输出策略保留，不因归档改写硬件逻辑。
- `dg-am-comp-transfer-check`：代码已归档、GUI 仅留卡片；作为纯电学或波形工具的历史例外规则与自身安全收尾保留，不因移出运行清单删除。
- `temperature-switch-pid-cycle`：代码已归档、GUI 仅留卡片；保留专用测温、TEC 启停与 PID 周期时序的历史例外，说明见 [TEC 模块](tec_controller.md#实验中的可选连接)。
- `z-aw-closed-loop-waveform-correction`：纯电学或波形工具，保留硬件用途和自身安全收尾。
- `z-aw-current-waveform-scope-check`：纯电学或波形工具，保留硬件用途和自身安全收尾。
- `z-aw-waveform-scope-check`：纯电学或波形工具，保留硬件用途和自身安全收尾。
- `z-coil-current-frequency-response`：纯电学或波形工具，保留硬件用途和自身安全收尾。
- `z-coil-inductance-frequency-response`：纯电学或波形工具，保留硬件用途和自身安全收尾。

## 防止时序回退

expcodegen 的单点、分组和 OFF/ON 配对模板均实际执行公共采集窗口与延迟保存；假仪器测试
调用真实采集与 NPZ 保存函数，覆盖慢磁盘、末组、取消、采集/恢复/保存失败和故障数据抢救。
具体实验接入后，测试须使用其真实配置及读数函数，不把模板 FakeContext 当作仪器业务证据。

正式测试通过 `ACQUISITION_CASES` 关联稳定 ID、模式、场景和具体节点。验证器默认检查 Git
工作区变更，也可指定 `--changed-file` 或 `--changed-since`；采集业务变更要求通用及相应分组/
配对场景齐全，动态源码通过 `sources` 补充。现有未完整关联的测试如实报告缺口，不把规范整改
或共享 helper 测试通过等同于全部实验业务验证完成。入口和命令见 [开发验证](../README.md#开发验证)。

`--plan-only --report .cache/expcodegen/plan.json` 导出计划，实际执行时去掉 `--plan-only`。
报告按实验 ID 区分场景声明、缺口、执行通过与未执行，导出模型 schema、步骤清单、参数来源和
文档入口供 GUI fixture 与文档复用；未映射关系仍人工核对。所有自动验证都隔离真实硬件。

新增格式独立记录 `raw_format_version`，参数 `schema_version` 升级不自动改变数据格式。
已有格式的历史资格继续按各实验实际规则读取，本次不批量改写历史运行或现有格式标记。
