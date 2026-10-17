"""无硬件预检、双来源冻结及沿用的 Mx 工作点准备。"""
from pathlib import Path
from typing import Any
import hashlib
import numpy as np
from ...common import load_mapping
from ...control_sources import (AppliedControlWaveform, TheoryControlSource, ZCalibrationSource,
    build_applied_control, corrected_control_contract, load_theory_control, load_z_calibration)
from ...current_feedback import load_corrected_control_waveform
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...steps import (validate_z_trigger_mapping, save_corrected_control_source_snapshot,
    save_optimal_control_source_snapshot)
from ..mx_z_optimal_control_rf_sensitivity.preparation import (
    _connect_control_devices, _configure_common_outputs,
    _initial_state_snapshot)
from .models import MxZOptimalControlXYRFSensitivityParams
from .run_format import EXPERIMENT_ID, DATA_TYPE

EXECUTION_MODE = "typed_workflow"


def preflight(params, root):
    validate_mapping_usage(OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility("mx-z-optimal-control-xy-rf-sensitivity", params.to_external(), mapping=mapping)
    return params, mapping


def source_fingerprint(source):
    corrected, theory, calibration, _ = source
    return (corrected.run_name if corrected else None, theory.waveform_sha256,
        theory.parameter_sha256, calibration.analysis_sha256 if calibration else None)


def save_source(raw, source):
    corrected, theory, calibration, applied = source
    files = (save_corrected_control_source_snapshot(raw, corrected, applied) if corrected else
        save_optimal_control_source_snapshot(raw, theory, calibration, applied))
    expected = ({"source_corrected_control_waveform.npz": corrected.waveform_sha256} if corrected else {
        "source_optimal_control_waveform.csv": theory.waveform_sha256,
        "source_optimal_control_params.csv": theory.parameter_sha256,
        "source_z_calibration_analysis.yaml": calibration.analysis_sha256})
    for name, signature in expected.items():
        if hashlib.sha256((raw / name).read_bytes()).hexdigest() != signature:
            raise ValueError("复制的控制来源与 A01 冻结内容不一致，拒绝配置输出")
    return files

def _load_control_sources(
    params: MxZOptimalControlXYRFSensitivityParams,
    root: Path,
) -> tuple[
    Any,
    TheoryControlSource,
    ZCalibrationSource | None,
    AppliedControlWaveform,
]:
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(
            root,
            params.corrected_control_source_run,
        )
        theory, applied = corrected_control_contract(corrected)
        return corrected, theory, None, applied
    theory = load_theory_control(
        Path(params.control_results_root),
        params.control_version,
    )
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    applied = build_applied_control(
        theory,
        calibration,
        params.control_scale,
        output_vpp=params.z_aw_output_vpp,
        output_offset_v=params.z_aw_output_offset_v,
    )
    return None, theory, calibration, applied



def initial_config(params, corrected, theory, calibration, applied):
    x_axis, y_axis = params.x_axis(), params.y_axis()
    center_x, center_y = params.phase_calibration_point()
    warnings = []
    if not np.isclose(params.y_rf_frequency_hz, theory.theory_rf_frequency_hz):
        warnings.append("Y RF 频率与理论控制频率不同；校相结果按共同触发起始相位复用")
    return dict(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        measurement_mode="xy_bias_grid_rf_sensitivity",
        scan_mode="nested_scan",
        scan_order="x_outer_y_inner_serpentine",
        objective="minimize flat_median_ft_per_sqrt_hz",
        geometry={
            "gs200_main_field_current_ma": params.main_magnetic_field_ma,
            "control_field": "Z arbitrary waveform",
            "rf_field": "Y triggered sine with per-point DC compensation",
            "x_field_output": "X_magnetic_field DC",
            "x_points": int(x_axis.size),
            "y_points": int(y_axis.size),
        },
        residual_field_compensation={
            "x_source": "X_magnetic_field DC",
            "y_source": "rf_coil waveform DC offset",
            "units": "V",
            "phase_reference": "arithmetic center of scan axes",
        },
        phase_calibration_reference={
            "x_field_v": center_x,
            "y_field_v": center_y,
            "selected_phase_reused_for_all_points": True,
        },
        control_source={
            "mode": params.control_waveform_source,
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "theory_rf_frequency_hz": theory.theory_rf_frequency_hz,
            "scale": params.control_scale if corrected is None else None,
            "burst_phase_deg": params.control_burst_phase_deg,
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
        scan_axes={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "order": "X outer, Y serpentine",
        },
        acquisition_signals=[
            "Demod0 R/X/Y at the single phase calibration point",
            "Demod0 R during each XY amplitude/noise point",
        ],
        linewidth_mode="amplitude_equivalent",
        warnings=warnings,
    )
