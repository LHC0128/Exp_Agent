# 正式实验迁移状态

本清单于 2026-10-11 按 `lab_workflows/experiments/registry.py` 核对：共 60 个正式实验，
其中 43 个为新模式、17 个为旧模式。后续新增或迁移实验时同步更新本表；运行时以注册表为准。

实验注册表用 `execution_mode` 区分两种执行方式：

- `typed_workflow`（新模式）：GUI 字段由强类型参数模型显式声明；采集与分析位于
  `lab_workflows/experiment_modules/`，`experiments/` 只保留薄入口。
- `legacy_script`（旧模式）：暂时通过 `LegacyScriptAdapter` 读取和覆盖旧脚本常量，
  用于保持现有实验可运行，后续按维护优先级逐项迁移。

模型内部字段统一使用 `snake_case`。外部 GUI/YAML 键通过 `external_name` 保持稳定，
因此已有的全大写键和 `FIXED_PARAMS.*` 键不需要随迁移改名。

## 新模式

| 稳定实验 ID | 模块 | 状态 |
|---|---|---|
| `bell-bloom-z-field-calibration` | `lab_workflows/experiment_modules/bell_bloom_z_field_calibration/` | 新模式 |
| `detection-chain-frequency-response` | `lab_workflows/experiment_modules/detection_chain_frequency_response/` | 新模式 |
| `scope-capture` | `lab_workflows/experiment_modules/scope_capture/` | 新模式 |
| `static-sensitivity` | `lab_workflows/experiment_modules/static_sensitivity/` | 新模式 |
| `mx-y-rf-sensitivity` | `lab_workflows/experiment_modules/mx_y_rf_sensitivity/` | 新模式 |
| `mx-y-rf-frequency-response` | `lab_workflows/experiment_modules/mx_y_rf_frequency_response/` | 新模式 |
| `mx-y-optimal-control-rf-frequency-response` | `lab_workflows/experiment_modules/mx_y_optimal_control_rf_frequency_response/` | 新模式 |
| `mx-y-rf-sensitivity-drift` | `lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/` | 新模式 |
| `mx-z-optimal-control-rf-sensitivity` | `lab_workflows/experiment_modules/mx_z_optimal_control_rf_sensitivity/` | 新模式 |
| `mx-z-optimal-control-xy-rf-sensitivity` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_sensitivity/` | 新模式 |
| `mx-z-optimal-control-xy-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_noise_spectrum/` | 新模式 |
| `mx-z-optimal-control-xy-leakage-response` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_leakage_response/` | 新模式 |
| `mx-z-optimal-control-xy-rf-phase-response` | `lab_workflows/experiment_modules/mx_z_optimal_control_xy_rf_phase_response/` | 新模式 |
| `mx-z-optimal-control-xyz-balance` | `lab_workflows/experiment_modules/mx_z_optimal_control_xyz_balance/` | 新模式 |
| `mx-z-optimal-control-dg4000-bias-xyz-balance` | `lab_workflows/experiment_modules/mx_z_optimal_control_dg4000_bias_xyz_balance/` | 新模式 |
| `z-aw-waveform-scope-check` | `lab_workflows/experiment_modules/z_aw_waveform_scope_check/` | 新模式 |
| `z-coil-inductance-frequency-response` | `lab_workflows/experiment_modules/z_coil_inductance_frequency_response/` | 新模式 |
| `z-coil-current-frequency-response` | `lab_workflows/experiment_modules/z_coil_current_frequency_response/` | 新模式 |
| `mx-z-current-coupling-calibration` | `lab_workflows/experiment_modules/mx_z_current_coupling_calibration/` | 新模式 |
| `z-aw-current-waveform-scope-check` | `lab_workflows/experiment_modules/z_aw_current_waveform_scope_check/` | 新模式 |
| `z-aw-closed-loop-waveform-correction` | `lab_workflows/experiment_modules/z_aw_closed_loop_waveform_correction/` | 新模式 |
| `mx-y-rf-power-optimization` | `lab_workflows/experiment_modules/mx_y_rf_power_optimization/` | 新模式 |
| `mx-y-rf-probe-detuning-optimization` | `lab_workflows/experiment_modules/mx_y_rf_probe_detuning_optimization/` | 新模式 |
| `mx-main-field-calibration` | `lab_workflows/experiment_modules/mx_main_field_calibration/` | 新模式 |
| `mx-keithley-6221-main-field-calibration` | `lab_workflows/experiment_modules/mx_keithley_6221_main_field_calibration/` | 新模式 |
| `mx-keithley-6221-optimal-control-rf-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_rf_sensitivity/` | 新模式 |
| `mx-keithley-6221-optimal-control-xyz-balance` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_xyz_balance/` | 新模式 |
| `mx-keithley-6221-optimal-control-balanced-sensitivity` | `lab_workflows/experiment_modules/mx_keithley_6221_optimal_control_balanced_sensitivity/` | 新模式 |
| `mx-main-field-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_noise_spectrum/` | 新模式 |
| `mx-main-field-scope-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/` | 新模式 |
| `mx-z-field-calibration` | `lab_workflows/experiment_modules/mx_z_field_calibration/` | 新模式 |
| `mx-z-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_noise_spectrum/` | 新模式 |
| `mx-xy-residual-field-calibration` | `lab_workflows/experiment_modules/mx_xy_residual_field_calibration/` | 新模式 |
| `noise-spectrum-xy` | `lab_workflows/experiment_modules/noise_spectrum_xy/` | 新模式 |
| `noise-spectrum-xy-known-noise` | `lab_workflows/experiment_modules/noise_spectrum_xy_known_noise/` | 新模式 |
| `noise-spectrum-xy-uncontrolled-probe-am` | `lab_workflows/experiment_modules/noise_spectrum_xy_probe_am/` | 新模式 |
| `noise-spectrum-xy-demod3-r` | `lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/` | 新模式 |
| `projection-noise` | `lab_workflows/experiment_modules/projection_noise/` | SDS 原始 PD 采集 |
| `rf-sensitivity-direct-aw-frequency` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/` | 新模式 |
| `rf-sensitivity-aw` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw/` | schema v2，DirectAW；16 个采集 / 4 个分析节点的分步会话，XYZ 共用时序信号2主输出触发，历史仅接纳新格式 |
| `rf-sensitivity-aw-r` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_r/` | 新模式 |
| `t2-calibration` | `lab_workflows/experiment_modules/t2_calibration/` | 新模式 |
| `xy-direct-aw-dc-calibration` | `lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/` | 新模式 |

`xy-direct-aw-dc-calibration` 的当前参数见[默认参数文件](../params/experiments/xy-direct-aw-dc-calibration.yaml)。
扫描电压必须落在 AW 输出幅度与 offset 可表达的范围内；超限时预检阻止启动。
迁移代码不会静默替换用户参数。

## 旧模式

| 稳定实验 ID | 实验 |
|---|---|
| `noise-spectrum-xy-v2` | XY 控制噪声谱 v2 |
| `photon-shot-noise` | 光子散粒噪声 |
| `rf-sensitivity-aw-frequency` | RF 灵敏度频率扫描（外部 AM） |
| `rf-sensitivity-constxy-frequency` | RF 灵敏度频率扫描（ConstXY） |
| `z-field-bandwidth` | Z 场带宽 |
| `bb-duty-scan-burst` | BB Pump 占空比扫描 |
| `bb-power-scan-burst` | BB Pump 功率扫描 |
| `t1-calibration` | T1 标定 |
| `xy-aw-voltage-calibration` | XY 任意波电压标定 |
| `xy-dc-voltage-calibration` | XY DC 电压标定 |
| `aw-const-omega-check` | 恒定 Omega 标定检查 |
| `aw-const-omega-awscale-check` | 恒定 Omega 标定检查（AWScale） |
| `static-sensitivity-optimize` | 静磁场灵敏度优化 |
| `static-sensitivity-optimize-local` | 静磁场灵敏度局部优化 |
| `aw-waveform-scope-check` | 任意波示波器检查 |
| `dg-am-comp-transfer-check` | DG AM 到 COMP 传递检查 |
| `temperature-switch-pid-cycle` | 温控开关 PID 周期测试 |

迁移旧实验时保持实验 ID、`data_type`、历史运行目录和已发布参数键不变，并依次补齐
强类型模型、无副作用预检、取消检查点、异常安全恢复、离线分析入口和契约测试。
