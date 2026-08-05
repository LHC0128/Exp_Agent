"""正式 Python 实验的统一注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import CancellationToken, ProgressCallback
from ..experiment_modules.noise_spectrum_xy.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_demod3_r.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
)
from ..experiment_modules.projection_noise.definition import (
    DEFINITION as PROJECTION_NOISE_DEFINITION,
)
from ..experiment_modules.mx_y_rf_sensitivity.definition import (
    DEFINITION as MX_Y_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_rf_sensitivity.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xy_leakage_response.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XY_LEAKAGE_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xyz_balance.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XYZ_BALANCE_DEFINITION,
)
from ..experiment_modules.mx_y_rf_power_optimization.definition import (
    DEFINITION as MX_Y_RF_POWER_OPTIMIZATION_DEFINITION,
)
from ..experiment_modules.mx_y_rf_probe_detuning_optimization.definition import (
    DEFINITION as MX_Y_RF_PROBE_DETUNING_OPTIMIZATION_DEFINITION,
)
from ..experiment_modules.mx_main_field_calibration.definition import (
    DEFINITION as MX_MAIN_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.mx_main_field_noise_spectrum.definition import (
    DEFINITION as MX_MAIN_FIELD_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_main_field_scope_noise_spectrum.definition import (
    DEFINITION as MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_z_field_calibration.definition import (
    DEFINITION as MX_Z_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.mx_z_noise_spectrum.definition import (
    DEFINITION as MX_Z_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_xy_residual_field_calibration.definition import (
    DEFINITION as MX_XY_RESIDUAL_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.rf_sensitivity_direct_aw_frequency.definition import (
    DEFINITION as RF_DIRECT_AW_FREQUENCY_DEFINITION,
)
from ..experiment_modules.t2_calibration.definition import (
    DEFINITION as T2_CALIBRATION_DEFINITION,
)
from ..experiment_modules.xy_direct_aw_dc_calibration.definition import (
    DEFINITION as XY_DIRECT_AW_DC_DEFINITION,
)
from ..static_sensitivity import (
    StaticSensitivityParams,
    preflight_static_sensitivity,
    run_static_sensitivity,
)
from .contracts import ExperimentDefinition
from .legacy import LegacyScriptAdapter


_DEVICES = {
    "optical": ("DG900", "DG4000", "GS200", "HF2"),
    "xy": ("DG900", "DG4000", "HF2"),
    "scope": ("DG900", "DG4000", "SDS", "HF2"),
}


def _legacy(
    experiment_id: str,
    title: str,
    category: str,
    family: str,
    variant: str,
    script: str,
    plot: str | None,
    description: str,
    devices: tuple[str, ...] = _DEVICES["optical"],
    field_metadata: dict[str, dict[str, Any]] | None = None,
) -> ExperimentDefinition:
    """注册尚未迁移的 AST 兼容实验。"""
    adapter = LegacyScriptAdapter(
        experiment_id,
        script,
        Path(script).stem,
        plot,
        field_metadata=field_metadata,
    )
    return ExperimentDefinition(
        id=experiment_id,
        title=title,
        category=category,
        family=family,
        variant=variant,
        description=description,
        data_type=Path(script).stem,
        required_devices=devices,
        execution_mode="legacy_script",
        acquisition_program=f"experiments/{script}",
        analysis_program=f"experiments/{plot}" if plot else None,
        wiring_notes=("运行前按该实验文档和映射表核对接线。",),
        safety_notes=("预检通过后才允许占用硬件并启动输出。",),
        schema_provider=adapter.schema,
        defaults_saver=adapter.save_defaults,
        preflight_runner=adapter.preflight,
        experiment_runner=adapter.run,
        analysis_runner=adapter.analyze if plot else None,
    )


def _static_schema() -> dict[str, Any]:
    return StaticSensitivityParams.schema(StaticSensitivityParams.from_yaml())


def _save_static(values: dict[str, Any]) -> None:
    params = StaticSensitivityParams.from_dict(values)
    errors = preflight_static_sensitivity(params)
    if errors:
        raise ValueError("；".join(errors))
    params.to_yaml()


def _preflight_static(values: dict[str, Any]) -> list[str]:
    return preflight_static_sensitivity(StaticSensitivityParams.from_dict(values))


def _run_static(
    values: dict[str, Any],
    progress: ProgressCallback | None,
    cancellation: CancellationToken | None,
) -> dict[str, Any]:
    return run_static_sensitivity(
        StaticSensitivityParams.from_dict(values), progress, cancellation
    )


_DEFINITIONS = [
    ExperimentDefinition(
        id="static-sensitivity",
        title="静磁场灵敏度",
        category="measurement",
        family="static-field",
        variant="standard",
        description="采集色散曲线和噪声并计算静磁场灵敏度。",
        data_type="Static_Magnetic_Field_Sensitivity",
        required_devices=_DEVICES["optical"],
        execution_mode="typed_workflow",
        acquisition_program="experiments/Static_Magnetic_Field_Sensitivity.py",
        analysis_program=None,
        wiring_notes=("确认 Pump、Probe、Z 场和 HF2 输入按 mapping.yaml 连接。",),
        safety_notes=("Z 场和光功率在写入前按安全限值校验。",),
        schema_provider=_static_schema,
        defaults_saver=_save_static,
        preflight_runner=_preflight_static,
        experiment_runner=_run_static,
        analysis_runner=None,
        auto_analyze=False,
    ),
    MX_Y_RF_SENSITIVITY_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XY_LEAKAGE_RESPONSE_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XYZ_BALANCE_DEFINITION,
    MX_Y_RF_POWER_OPTIMIZATION_DEFINITION,
    MX_Y_RF_PROBE_DETUNING_OPTIMIZATION_DEFINITION,
    MX_MAIN_FIELD_CALIBRATION_DEFINITION,
    MX_MAIN_FIELD_NOISE_SPECTRUM_DEFINITION,
    MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
    MX_Z_FIELD_CALIBRATION_DEFINITION,
    MX_Z_NOISE_SPECTRUM_DEFINITION,
    MX_XY_RESIDUAL_FIELD_CALIBRATION_DEFINITION,
    NOISE_SPECTRUM_XY_DEFINITION,
    NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
    _legacy("noise-spectrum-xy-v2", "XY 控制噪声谱 v2", "measurement", "noise", "v2", "Noise_Spectrum_XY_Ctrl_v2.py", "Noise_Spectrum_XY_Ctrl_v2_plot.py", "第二版 XY 控制噪声谱流程。", _DEVICES["xy"]),
    _legacy("photon-shot-noise", "光子散粒噪声", "measurement", "fundamental-noise", "photon", "Photon_shot_noise.py", "Photon_shot_noise_plot.py", "测量光子散粒噪声随实验参数的变化。"),
    PROJECTION_NOISE_DEFINITION,
    _legacy("rf-sensitivity-aw", "RF 灵敏度（任意波）", "measurement", "rf-sensitivity", "external-am", "RF_Field_Sensitivity_AW.py", "RF_Field_Sensitivity_AW_plot.py", "使用任意波与外部 AM 方案测量 RF 灵敏度。", _DEVICES["xy"]),
    _legacy("rf-sensitivity-aw-frequency", "RF 灵敏度频率扫描（外部 AM）", "measurement", "rf-sensitivity", "external-am", "RF_Field_Sensitivity_AW_FreqSweep.py", "RF_Field_Sensitivity_AW_FreqSweep_plot.py", "扫描 RF 频率的外部 AM 硬件方案。", _DEVICES["xy"]),
    RF_DIRECT_AW_FREQUENCY_DEFINITION,
    _legacy("rf-sensitivity-constxy-frequency", "RF 灵敏度频率扫描（ConstXY）", "measurement", "rf-sensitivity", "const-xy", "RF_Field_Sensitivity_ConstXY_FreqSweep.py", "RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py", "固定 XY 控制下扫描 RF 频率。", _DEVICES["xy"]),
    _legacy("z-field-bandwidth", "Z 场带宽", "measurement", "field-response", "standard", "Z_Field_Bandwidth_Measurement.py", "Z_Field_Bandwidth_Measurement_plot.py", "测量 Z 磁场控制链路带宽。"),
    _legacy("bb-duty-scan-burst", "BB Pump 占空比扫描", "measurement", "bb-scan", "burst", "BB_Rpump_DutyScan_Burst.py", "BB_Rpump_DutyScan_Burst_plot.py", "Burst 模式下扫描 Pump 占空比。"),
    _legacy("bb-power-scan-burst", "BB Pump 功率扫描", "measurement", "bb-scan", "burst", "BB_Rpump_PowerScan_Burst.py", "BB_Rpump_PowerScan_Burst_plot.py", "Burst 模式下扫描 Pump 功率。"),
    _legacy("t1-calibration", "T1 标定", "calibration", "relaxation", "t1", "T1_Calibration.py", "T1_Calibration_plot.py", "采集并拟合纵向弛豫时间 T1。"),
    T2_CALIBRATION_DEFINITION,
    _legacy("xy-aw-voltage-calibration", "XY 任意波电压标定", "calibration", "xy-calibration", "external-am", "XY_AW_Voltage_Calibration.py", "XY_AW_Voltage_Calibration_plot.py", "标定 XY 任意波控制电压。", _DEVICES["xy"]),
    _legacy("xy-dc-voltage-calibration", "XY DC 电压标定", "calibration", "xy-calibration", "const-xy", "XY_DC_Voltage_Calibration.py", "XY_DC_Voltage_Calibration_plot.py", "标定 XY 直流控制电压。", _DEVICES["xy"]),
    XY_DIRECT_AW_DC_DEFINITION,
    _legacy("aw-const-omega-check", "恒定 Omega 标定检查", "calibration", "omega-calibration", "direct-aw", "AW_ConstOmega_Calibration_Check.py", "AW_ConstOmega_Calibration_Check_plot.py", "检查恒定 Omega 任意波标定结果。", _DEVICES["xy"]),
    _legacy("aw-const-omega-awscale-check", "恒定 Omega 标定检查（AWScale）", "calibration", "omega-calibration", "aw-scale", "AW_ConstOmega_Calibration_Check_AWScale.py", "AW_ConstOmega_Calibration_Check_AWScale_plot.py", "使用 AWScale 策略检查恒定 Omega 标定。", _DEVICES["xy"]),
    _legacy("static-sensitivity-optimize", "静磁场灵敏度优化", "optimization", "static-field", "global", "Static_Magnetic_Field_Sensitivity_Optimize.py", None, "全局搜索静磁场灵敏度实验参数。"),
    _legacy("static-sensitivity-optimize-local", "静磁场灵敏度局部优化", "optimization", "static-field", "local", "Static_Magnetic_Field_Sensitivity_Optimize_Local.py", None, "在已有结果附近进行局部参数优化。"),
    _legacy("aw-waveform-scope-check", "任意波示波器检查", "verification", "hardware-chain", "direct-aw", "AW_Control_Waveform_Scope_Check.py", None, "通过示波器验证任意波控制波形。", _DEVICES["scope"]),
    _legacy("dg-am-comp-transfer-check", "DG AM 到 COMP 传递检查", "verification", "hardware-chain", "external-am", "DG_AM_to_DG_COMP_Transfer_Check.py", "DG_AM_to_DG_COMP_Transfer_Check_plot.py", "验证信号源 AM 到补偿链路的传递关系。", _DEVICES["scope"]),
    _legacy("temperature-switch-pid-cycle", "温控开关 PID 周期测试", "verification", "temperature", "cycle", "Temperature_Switch_PID_Cycle_Test.py", "Temperature_Switch_PID_Cycle_Test_plot.py", "验证温控开关与 PID 恢复过程。", ("DG4000", "TEC103")),
]

_REGISTRY = {definition.id: definition for definition in _DEFINITIONS}
if len(_REGISTRY) != len(_DEFINITIONS):
    raise RuntimeError("实验注册表中存在重复 ID")


def list_experiments() -> list[ExperimentDefinition]:
    """按注册顺序返回全部正式实验。"""
    return list(_DEFINITIONS)


def get_experiment(experiment_id: str) -> ExperimentDefinition:
    """按稳定 ID 获取实验定义。"""
    try:
        return _REGISTRY[experiment_id]
    except KeyError as exc:
        raise KeyError(f"未知实验: {experiment_id}") from exc
