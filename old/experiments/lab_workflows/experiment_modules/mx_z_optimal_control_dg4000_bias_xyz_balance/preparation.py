"""本实验的无硬件预检、双来源冻结、连接与工作点准备。"""
import hashlib
import shutil
from pathlib import Path
from typing import Any
import numpy as np
import yaml
from ...common import load_mapping
from ...control_sources import build_applied_control, corrected_control_contract, load_theory_control, load_z_calibration
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled
from ...experiments.requirements import DG4000_BIAS_OPTIMAL_CONTROL
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import DeviceSession, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE, configure_mx_z_optimal_control_workpoint, validate_z_trigger_mapping, wait_for_temperature_stable
from ..mx_z_field_calibration.workflow import _connect_devices, _initial_state_snapshot, _configure_reference_clocks
from ..mx_y_rf_sensitivity.workflow import _set_pump_gate_on
from ..mx_z_optimal_control_xyz_balance.preparation import safe_shutdown
from ..mx_z_optimal_control_xy_rf_sensitivity.preparation import source_fingerprint, save_source as save_control_source
from .excitation import _configure_xy_idle
from .run_format import EXPERIMENT_ID, DATA_TYPE, RAW_FORMAT_VERSION
from .scan import build_xyz_axes

EXECUTION_MODE = "typed_workflow"


class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled():
        check_cancelled()


def preflight(params, root):
    validate_mapping_usage(DG4000_BIAS_OPTIMAL_CONTROL, root)
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    validate_z_trigger_mapping(mapping)
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    return params, mapping


def _connect_control_devices(
    mapping: dict[str, dict[str, Any]],
    session: DeviceSession,
) -> tuple[dict[str, Any], dict[str, int]]:
    """连接不含 GS200 的 Mx 设备，并确认 Z 控制与触发同机。"""
    devices, channels = _connect_devices(
        mapping,
        session,
        include_main_field=False,
    )
    channels["trigger"] = validate_z_trigger_mapping(mapping)
    return devices, channels


def load_source(params, root):
    calibration = load_z_calibration(root, params.z_calibration_source_run)
    corrected = None
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
        theory, applied = corrected_control_contract(corrected)
    else:
        theory = load_theory_control(Path(params.control_results_root), params.control_version)
        applied = build_applied_control(theory, calibration, params.control_scale,
            output_vpp=params.z_aw_output_vpp, output_offset_v=params.z_aw_output_offset_v)
    return corrected, theory, calibration, applied


def save_source(raw, source):
    files = save_control_source(raw, source)
    corrected, _, calibration, _ = source
    if corrected is not None:
        # 冻结闭环电压之外，静态偏置换算仍必须冻结独立的 K_Z 标定。
        path = raw / "source_z_calibration_analysis.yaml"
        shutil.copy2(calibration.analysis_path, path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != calibration.analysis_sha256:
            raise ValueError("复制的 Z 标定来源与 A01 冻结内容不一致")
        manifest_path = raw / "source_manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["z_bias_calibration"] = {
            "source_run": calibration.run_name, "analysis_sha256": calibration.analysis_sha256}
        manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
        files.append("raw/source_z_calibration_analysis.yaml")
    return files


def configure_workpoint(params, devices, channels, mapping, theory, applied):
    return configure_mx_z_optimal_control_workpoint(
        params, devices, channels, mapping, theory, applied,
        demod_frequency_hz=params.demod_frequency_hz,
        cancellation=_RuntimeCancellation(),
        xy_output_configurator=lambda: _configure_xy_idle(devices, channels),
        reference_clock_configurator=_configure_reference_clocks,
        pump_gate_setter=_set_pump_gate_on, temperature_stability_waiter=wait_for_temperature_stable,
        configure_main_field_output=False)


def initial_config(params, corrected, theory, calibration, applied):
    x_axis, y_axis, z_axis = build_xyz_axes(params)
    return dict(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        raw_format_version=RAW_FORMAT_VERSION,
        scan_mode="nested_scan",
        geometry={
            "optimal_control": "Z_magnetic_field DG4000 arbitrary waveform",
            "z_bias": "same DG4000 Z channel offset",
            "x_balance": "X_magnetic_field DG4000 DC",
            "y_balance": "Y_magnetic_field DG4000 DC",
            "gs200": "physically disconnected and not used",
        },
        scan={
            "x_field_v": x_axis.tolist(),
            "y_field_v": y_axis.tolist(),
            "z_bias_v": z_axis.tolist(),
            "canonical_shape": "Z bias, X, Y",
            "order": "Z bias outer, continuous X/Y serpentine",
            "points": int(x_axis.size * y_axis.size * z_axis.size),
            "single_axis_rule": "POINTS=1 requires START=STOP",
            "zero_output": "X/Y DC outputs remain ON at zero",
        },
        control_source={
            "mode": params.control_waveform_source,
            "version": theory.version,
            "waveform_sha256": theory.waveform_sha256,
            "parameter_sha256": theory.parameter_sha256,
            "repeat_frequency_hz": theory.repeat_frequency_hz,
            "scale": params.control_scale if corrected is None else None,
            "burst_phase_deg": params.control_burst_phase_deg,
            "corrected_run": corrected.run_name if corrected is not None else None,
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
        },
        current_feedback_calibrations=(
            {
                "current_coupling_run": corrected.coupling_calibration_run,
                "current_coupling_sha256": corrected.coupling_calibration_sha256,
                "frequency_response_run": corrected.frequency_response_run,
                "frequency_response_sha256": corrected.frequency_response_sha256,
            }
            if corrected is not None
            else None
        ),
        z_control_calibration={
            "source_run": calibration.run_name,
            "slope_hz_per_v": calibration.slope_hz_per_v,
            "intercept_hz": calibration.intercept_hz,
            "r_squared": calibration.r_squared,
            "analysis_sha256": calibration.analysis_sha256,
            "gamma_hz_per_nt": params.bz_gamma_hz_per_nt,
        },
        applied_control={
            "minimum_v": applied.minimum_v,
            "maximum_v": applied.maximum_v,
            "mean_v": float(np.mean(applied.voltage_v)),
            "amplitude_vpp": applied.amplitude_vpp,
            "offset_v": applied.offset_v,
            "max_abs_normalized": applied.max_abs_normalized,
            "points": int(theory.time_s.size),
        },
        trigger={
            "source": "Time_sequence_2",
            "frequency_hz": params.trigger_frequency_hz,
            "amplitude_vpp": params.trigger_amplitude_vpp,
            "offset_v": params.trigger_offset_v,
            "duty_percent": params.trigger_duty_percent,
            "slope": OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE,
            "wiring": "Time_sequence_2 CH2 to Z-control DG4000 Ext Trig",
            "physical_wiring_verified_by_software": False,
        },
        final_output_policy={
            "xyz_fields": "restore complete pre-run X/Y and Z DG4000 state",
            "optimal_control": "restore complete pre-run Z channel state",
            "trigger": "restore complete pre-run trigger channel state",
            "best_combination_remeasured": False,
            "gs200_used": False,
        },
        acquisition_signals=["HF2 Demod0 X", "HF2 Demod0 Y", "HF2 Demod0 R"],
        interpretation={
            "criterion": "robust complex Demod X/Y Bloch fit; measured R minimum is diagnostic",
            "bloch_fit_available": True,
            "periodic_bloch_required_for_model_fit": True,
        },
    )
