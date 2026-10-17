"""无硬件预检、来源冻结与原 Mx 公共工作点。"""
from ...common import load_mapping
from ...experiments.requirements import OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import validate_z_trigger_mapping
from ..mx_z_optimal_control_xy_rf_sensitivity.preparation import (
    _load_control_sources, source_fingerprint, save_source, _connect_control_devices,
    _configure_common_outputs, _initial_state_snapshot)
from ..mx_z_optimal_control_rf_sensitivity.preparation import safe_shutdown
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


def initial_config(params, corrected, theory, calibration, applied):
    x_axis = params.x_axis()
    y_axis = params.y_axis()
    return dict(
        raw_format_version=RAW_FORMAT_VERSION,
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="xy_bias_noise_spectrum",
        scan_mode="nested_scan",
        scan_order="x_outer_y_inner_serpentine",
        objective="minimize_noise_median_r_asd_v_per_sqrt_hz",
        geometry={
            "control_field": "Z arbitrary waveform",
            "rf_field": "Y RF AC OFF with Y DC compensation",
            "x_field_output": "X_magnetic_field DC",
            "x_points": int(x_axis.size),
            "y_points": int(y_axis.size),
            "bias_units": "V",
        },
        noise_metric={
            "signal": "Demod0 R",
            "quantity": "ASD",
            "aggregation": "median",
            "psd_aggregation": "mean_psd_then_sqrt",
            "band_hz": [params.low_freq_skip_hz, params.noise_band_max_hz],
            "narrowband_lines_included": True,
        },
        scan_axes={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "order": "X outer, Y serpentine",
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
        },
        z_calibration=(
            {
                "source_run": calibration.run_name,
                "slope_hz_per_v": calibration.slope_hz_per_v,
                "intercept_hz": calibration.intercept_hz,
                "r_squared": calibration.r_squared,
                "analysis_sha256": calibration.analysis_sha256,
            }
            if calibration is not None
            else None
        ),
        acquisition_signals=["Demod0 R during each XY bias noise point"],
        rf_state_during_noise="burst_off_modulation_off_y_dc_on_if_nonzero",
        warnings=[],
    )
