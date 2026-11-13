"""无硬件预检、双来源冻结及实际工作点配置。"""
from typing import Any
from ...control_sources import TheoryControlSource, AppliedControlWaveform
from ...common import load_mapping
from ...experiments.requirements import OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import configure_fixed_dc_field, configure_mx_z_optimal_control_workpoint, validate_z_trigger_mapping
from ..mx_z_optimal_control_rf_sensitivity.preparation import (_connect_control_devices, _initial_state_snapshot, _RuntimeCancellation, _configure_reference_clocks, _set_pump_gate_on, wait_for_temperature_stable, safe_shutdown)
from ..mx_z_optimal_control_xy_rf_sensitivity.preparation import _load_control_sources, source_fingerprint, save_source
from .models import MxZOptimalControlXYRFPhaseResponseParams
from .scan import build_axes
from .excitation import _configure_y_rf_at_offset
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION

EXECUTION_MODE = "typed_workflow"


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def _configure_common_outputs(
    params: MxZOptimalControlXYRFPhaseResponseParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    mapping: dict[str, dict[str, Any]],
    theory: TheoryControlSource,
    applied: AppliedControlWaveform,
    initial_x_v: float,
    initial_y_v: float,
) -> tuple[float, float | None, dict[str, dict[str, str]], dict[str, Any]]:
    def configure_xy_outputs() -> None:
        xy_field = devices["xy_field"]
        configure_fixed_dc_field(
            xy_field,
            channels["x_field"],
            "X_magnetic_field",
            initial_x_v,
        )
        _configure_y_rf_at_offset(
            params,
            xy_field,
            channels["y_rf"],
            initial_y_v,
        )

    return configure_mx_z_optimal_control_workpoint(
        params,
        devices,
        channels,
        mapping,
        theory,
        applied,
        demod_frequency_hz=params.y_rf_frequency_hz,
        cancellation=_RuntimeCancellation(),
        xy_output_configurator=configure_xy_outputs,
        reference_clock_configurator=_configure_reference_clocks,
        pump_gate_setter=_set_pump_gate_on,
        temperature_stability_waiter=wait_for_temperature_stable,
    )


def initial_config(params, corrected, theory, calibration, applied):
    x_axis, y_axis, configured_phase, acquisition_phase = build_axes(params)
    return dict(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        raw_format_version=RAW_FORMAT_VERSION,
        scan_mode="nested_scan",
        final_output_policy={"trigger": "preserve current state on every exit path",
            "z_control": "preserve current state", "xy": "zero and output off"},
        measurement_mode="xy_grid_rf_phase_response",
        geometry={
                "x_field": "X_magnetic_field DC scan",
                "y_field": "rf_coil DC offset scan",
                "rf_phase_target": "Y RF Burst phase",
                "x_points": int(x_axis.size),
                "y_points": int(y_axis.size),
                "phase_points": int(acquisition_phase.size),
                "main_field_ma": params.main_magnetic_field_ma,
            },
        control_source={
                "mode": params.control_waveform_source,
                "version": theory.version,
                "waveform_sha256": theory.waveform_sha256,
                "parameter_sha256": theory.parameter_sha256,
                "repeat_frequency_hz": theory.repeat_frequency_hz,
                "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
                "scale": params.control_scale if corrected is None else None,
                "corrected_run": corrected.run_name if corrected is not None else None,
                "burst_phase_deg": params.control_burst_phase_deg,
            },
        z_calibration={
                    "source_run": calibration.run_name,
                    "slope_hz_per_v": calibration.slope_hz_per_v,
                    "intercept_hz": calibration.intercept_hz,
                    "r_squared": calibration.r_squared,
                    "analysis_sha256": calibration.analysis_sha256,
                }
                if calibration is not None
                else None,
        applied_control={
                "minimum_v": applied.minimum_v,
                "maximum_v": applied.maximum_v,
                "amplitude_vpp": applied.amplitude_vpp,
                "offset_v": applied.offset_v,
                "points": int(theory.time_s.size),
            },
        scan_axes={
                "x_field_v": x_axis.tolist(),
                "y_field_v": y_axis.tolist(),
                "configured_phase_deg": configured_phase.tolist(),
                "acquisition_phase_deg": acquisition_phase.tolist(),
                "order": "X outer, Y serpentine; phase paired phi then phi+180",
            },
        fixed_conditions={
                "main_magnetic_field_ma": params.main_magnetic_field_ma,
                "pump_laser_power_v": params.pump_laser_power_v,
                "probe_laser_power_v": params.probe_laser_power_v,
                "temperature_c": params.temperature_c,
                "y_rf_frequency_hz": params.y_rf_frequency_hz,
                "phase_cal_rf_amplitude_vpp": params.phase_cal_rf_amplitude_vpp,
                "demod_signal": "HF2 Demod0 R",
            },
        acquisition_signals=["Demod0 R"])
