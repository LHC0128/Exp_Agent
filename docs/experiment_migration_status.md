# 正式实验迁移状态

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
| `static-sensitivity` | `lab_workflows/static_sensitivity.py` | 新模式 |
| `noise-spectrum-xy` | `lab_workflows/experiment_modules/noise_spectrum_xy/` | 本轮迁移 |
| `noise-spectrum-xy-demod3-r` | `lab_workflows/experiment_modules/noise_spectrum_xy_demod3_r/` | 新模式 |
| `mx-y-rf-sensitivity` | `lab_workflows/experiment_modules/mx_y_rf_sensitivity/` | 新模式 |
| `mx-main-field-calibration` | `lab_workflows/experiment_modules/mx_main_field_calibration/` | 新模式 |
| `mx-main-field-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_noise_spectrum/` | 新模式 |
| `mx-main-field-scope-noise-spectrum` | `lab_workflows/experiment_modules/mx_main_field_scope_noise_spectrum/` | 新模式 |
| `mx-xy-residual-field-calibration` | `lab_workflows/experiment_modules/mx_xy_residual_field_calibration/` | 新模式 |
| `mx-z-field-calibration` | `lab_workflows/experiment_modules/mx_z_field_calibration/` | 新模式 |
| `mx-z-noise-spectrum` | `lab_workflows/experiment_modules/mx_z_noise_spectrum/` | 新模式 |
| `projection-noise` | `lab_workflows/experiment_modules/projection_noise/` | SDS 原始 PD 采集迁移 |
| `rf-sensitivity-direct-aw-frequency` | `lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/` | 本轮迁移 |
| `xy-direct-aw-dc-calibration` | `lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/` | 本轮迁移 |
| `t2-calibration` | `lab_workflows/experiment_modules/t2_calibration/` | 本轮迁移 |

`xy-direct-aw-dc-calibration` 当前保存的默认值为扫描 `-4 V` 到 `+4 V`，固定 AW 输出
为 `4 Vpp / 0 V offset`，其可表达范围只有 `[-2 V, +2 V]`。新模式预检会明确阻止
这组默认值启动；运行前应由实验负责人确认是缩小扫描范围，还是调整并重新核验 AW
输出幅度，迁移代码不会静默替换实验参数。

## 旧模式

| 稳定实验 ID | 实验 |
|---|---|
| `noise-spectrum-xy-v2` | XY 控制噪声谱 v2 |
| `photon-shot-noise` | 光子散粒噪声 |
| `rf-sensitivity-aw` | RF 灵敏度（任意波） |
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
