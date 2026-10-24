"""正式 Python 实验的统一注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import CancellationToken, ProgressCallback
from ..experiment_modules.bb_power_scan_burst.definition import DEFINITION as BB_POWER_SCAN_BURST_DEFINITION
from ..experiment_modules.rf_sensitivity_constxy_frequency.definition import DEFINITION as RF_CONSTXY_FREQUENCY_DEFINITION
from ..experiment_modules.bb_duty_scan_burst.definition import DEFINITION as BB_DUTY_SCAN_BURST_DEFINITION
from ..experiment_modules.z_field_bandwidth.definition import DEFINITION as Z_FIELD_BANDWIDTH_DEFINITION
from ..experiment_modules.rf_sensitivity_direct_aw.definition import (
    DEFINITION as RF_SENSITIVITY_DIRECT_AW_DEFINITION,
)
from ..experiment_modules.rf_sensitivity_aw_known_noise.definition import DEFINITION as RF_KNOWN_NOISE_DEFINITION
from ..experiment_modules.rf_control_scale_sensitivity.definition import DEFINITION as RF_CONTROL_SCALE_DEFINITION
from ..experiment_modules.scope_capture.definition import DEFINITION as SCOPE_CAPTURE_DEFINITION
from ..experiment_modules.noise_spectrum_xy.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_known_noise.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_KNOWN_NOISE_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_probe_am.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_PROBE_AM_DEFINITION,
)
from ..experiment_modules.noise_spectrum_xy_demod3_r.definition import (
    DEFINITION as NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
)
from ..experiment_modules.photon_shot_noise.definition import DEFINITION as PHOTON_SHOT_NOISE_DEFINITION
from ..experiment_modules.projection_noise.definition import (
    DEFINITION as PROJECTION_NOISE_DEFINITION,
)
from ..experiment_modules.mx_y_rf_sensitivity.definition import (
    DEFINITION as MX_Y_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_y_rf_frequency_response.definition import (
    DEFINITION as MX_Y_RF_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_y_optimal_control_rf_frequency_response.definition import (
    DEFINITION as MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_y_rf_sensitivity_drift.definition import (
    DEFINITION as MX_Y_RF_SENSITIVITY_DRIFT_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_rf_sensitivity.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xy_rf_sensitivity.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XY_RF_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xy_noise_spectrum.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XY_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xy_leakage_response.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XY_LEAKAGE_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xy_rf_phase_response.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XY_RF_PHASE_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_xyz_balance.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_XYZ_BALANCE_DEFINITION,
)
from ..experiment_modules.mx_z_optimal_control_dg4000_bias_xyz_balance.definition import (
    DEFINITION as MX_Z_OPTIMAL_CONTROL_DG4000_BIAS_XYZ_BALANCE_DEFINITION,
)
from ..experiment_modules.z_aw_waveform_scope_check.definition import (
    DEFINITION as Z_AW_WAVEFORM_SCOPE_CHECK_DEFINITION,
)
from ..experiment_modules.z_coil_inductance_frequency_response.definition import (
    DEFINITION as Z_COIL_INDUCTANCE_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.z_coil_current_frequency_response.definition import (
    DEFINITION as Z_COIL_CURRENT_FREQUENCY_RESPONSE_DEFINITION,
)
from ..experiment_modules.mx_z_current_coupling_calibration.definition import (
    DEFINITION as MX_Z_CURRENT_COUPLING_CALIBRATION_DEFINITION,
)
from ..experiment_modules.z_aw_current_waveform_scope_check.definition import (
    DEFINITION as Z_AW_CURRENT_WAVEFORM_SCOPE_CHECK_DEFINITION,
)
from ..experiment_modules.z_aw_closed_loop_waveform_correction.definition import (
    DEFINITION as Z_AW_CLOSED_LOOP_WAVEFORM_CORRECTION_DEFINITION,
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
from ..experiment_modules.mx_main_field_scope_noise_spectrum.definition import (
    DEFINITION as MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
)
from ..experiment_modules.mx_z_field_calibration.definition import (
    DEFINITION as MX_Z_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.mx_xy_residual_field_calibration.definition import (
    DEFINITION as MX_XY_RESIDUAL_FIELD_CALIBRATION_DEFINITION,
)
from ..experiment_modules.rf_sensitivity_direct_aw_frequency.definition import (
    DEFINITION as RF_DIRECT_AW_FREQUENCY_DEFINITION,
)
from ..experiment_modules.t1_calibration.definition import DEFINITION as T1_CALIBRATION_DEFINITION
from ..experiment_modules.t2_calibration.definition import (
    DEFINITION as T2_CALIBRATION_DEFINITION,
)
from ..experiment_modules.xy_direct_aw_dc_calibration.definition import (
    DEFINITION as XY_DIRECT_AW_DC_DEFINITION,
)
from ..experiment_modules.static_sensitivity.definition import (
    DEFINITION as STATIC_SENSITIVITY_DEFINITION,
)
from ..experiment_modules.static_sensitivity_optimize_local.definition import DEFINITION as STATIC_LOCAL_DEFINITION
from .contracts import ExperimentDefinition
from .archived import ARCHIVED_EXPERIMENTS, load_archived_experiment
from ..experiment_modules.bell_bloom_z_field_calibration.definition import DEFINITION as BELL_BLOOM_Z_FIELD_CALIBRATION_DEFINITION
from ..experiment_modules.detection_chain_frequency_response.definition import (
    DEFINITION as DETECTION_CHAIN_FREQUENCY_RESPONSE_DEFINITION,
)
from .legacy import LegacyScriptAdapter
from ..acquisition_policy import ACQUISITION_EXCEPTIONS


_DEVICES = {
    "optical": ("DG900", "DG4000", "GS200", "HF2"),
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
        schema_version=1 if experiment_id in ACQUISITION_EXCEPTIONS else 2,
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


_DEFINITIONS = [
    BELL_BLOOM_Z_FIELD_CALIBRATION_DEFINITION,
    DETECTION_CHAIN_FREQUENCY_RESPONSE_DEFINITION,
    SCOPE_CAPTURE_DEFINITION,
    STATIC_SENSITIVITY_DEFINITION,
    MX_Y_RF_SENSITIVITY_DEFINITION,
    MX_Y_RF_FREQUENCY_RESPONSE_DEFINITION,
    MX_Y_OPTIMAL_CONTROL_RF_FREQUENCY_RESPONSE_DEFINITION,
    MX_Y_RF_SENSITIVITY_DRIFT_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_RF_SENSITIVITY_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XY_RF_SENSITIVITY_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XY_NOISE_SPECTRUM_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XY_LEAKAGE_RESPONSE_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XY_RF_PHASE_RESPONSE_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_XYZ_BALANCE_DEFINITION,
    MX_Z_OPTIMAL_CONTROL_DG4000_BIAS_XYZ_BALANCE_DEFINITION,
    Z_AW_WAVEFORM_SCOPE_CHECK_DEFINITION,
    Z_COIL_INDUCTANCE_FREQUENCY_RESPONSE_DEFINITION,
    Z_COIL_CURRENT_FREQUENCY_RESPONSE_DEFINITION,
    MX_Z_CURRENT_COUPLING_CALIBRATION_DEFINITION,
    Z_AW_CURRENT_WAVEFORM_SCOPE_CHECK_DEFINITION,
    Z_AW_CLOSED_LOOP_WAVEFORM_CORRECTION_DEFINITION,
    MX_Y_RF_POWER_OPTIMIZATION_DEFINITION,
    MX_Y_RF_PROBE_DETUNING_OPTIMIZATION_DEFINITION,
    MX_MAIN_FIELD_CALIBRATION_DEFINITION,
    MX_MAIN_FIELD_SCOPE_NOISE_SPECTRUM_DEFINITION,
    MX_Z_FIELD_CALIBRATION_DEFINITION,
    MX_XY_RESIDUAL_FIELD_CALIBRATION_DEFINITION,
    NOISE_SPECTRUM_XY_DEFINITION,
    NOISE_SPECTRUM_XY_KNOWN_NOISE_DEFINITION,
    NOISE_SPECTRUM_XY_PROBE_AM_DEFINITION,
    NOISE_SPECTRUM_XY_DEMOD3_R_DEFINITION,
    PHOTON_SHOT_NOISE_DEFINITION,
    PROJECTION_NOISE_DEFINITION,
    RF_SENSITIVITY_DIRECT_AW_DEFINITION,
    RF_KNOWN_NOISE_DEFINITION,
    RF_CONTROL_SCALE_DEFINITION,
    RF_DIRECT_AW_FREQUENCY_DEFINITION,
    RF_CONSTXY_FREQUENCY_DEFINITION,
    Z_FIELD_BANDWIDTH_DEFINITION,
    BB_DUTY_SCAN_BURST_DEFINITION,
    BB_POWER_SCAN_BURST_DEFINITION,
    T1_CALIBRATION_DEFINITION,
    T2_CALIBRATION_DEFINITION,
    XY_DIRECT_AW_DC_DEFINITION,
    STATIC_LOCAL_DEFINITION,
]

_REGISTRY = {definition.id: definition for definition in _DEFINITIONS}
if len(_REGISTRY) != len(_DEFINITIONS):
    raise RuntimeError("实验注册表中存在重复 ID")


def list_experiments(*, include_archived: bool = False) -> list[ExperimentDefinition]:
    """默认返回在维护的正式实验；目录可附加不加载实现的归档卡片。"""
    return [*_DEFINITIONS, *ARCHIVED_EXPERIMENTS.values()] if include_archived else list(_DEFINITIONS)


def get_experiment(experiment_id: str) -> ExperimentDefinition:
    """按稳定 ID 获取实验定义。"""
    try:
        return _REGISTRY[experiment_id]
    except KeyError as exc:
        if experiment_id in ARCHIVED_EXPERIMENTS:
            return load_archived_experiment(experiment_id)
        raise KeyError(f"未知实验: {experiment_id}") from exc
