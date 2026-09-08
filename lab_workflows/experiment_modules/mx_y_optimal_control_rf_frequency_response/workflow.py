"""Mx Y 最优控制 RF 频率响应观察工作流。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import numpy as np
from lockin_amplifier import OscillatorConfig, demod
from ...common import WorkflowCancelled, find_project_root, load_mapping, validate_safety_limit
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeviceSession, create_run_directory, OPTIMAL_CONTROL_BURST_TRIGGER_SLOPE
from ...steps.run_finish import finalize_run_safety
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _connect_control_devices, _configure_common_outputs,
    _rearm_y_rf, _save_corrected_source_snapshot, _corrected_control_contract,
    safe_shutdown,
)
from ..mx_y_rf_sensitivity.acquisition import summarize_r
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point
from .models import MxYOptimalControlRFFrequencyResponseParams

EXPERIMENT_ID = "mx-y-optimal-control-rf-frequency-response"
DATA_TYPE = "Mx_Y_Optimal_Control_RF_Frequency_Response"
EXECUTION_MODE = "typed_workflow"

def build_frequency_axis(params: MxYOptimalControlRFFrequencyResponseParams) -> np.ndarray:
    return np.linspace(params.frequency_start_hz, params.frequency_stop_hz, params.frequency_points, dtype=float)

def build_phase_axis(params: MxYOptimalControlRFFrequencyResponseParams) -> np.ndarray:
    return params.phase_axis_deg()

def _save_r_point(path: Path, payload: dict[str, np.ndarray], metadata: dict[str, Any], actual_rate: float) -> dict[str, float]:
    summary = summarize_r(payload)
    np.savez(path, time_s=payload["time_s"], r_v=payload["r"], actual_rate_sa_s=np.float64(actual_rate), **metadata, **{k: np.float64(v) if isinstance(v, float) else v for k, v in summary.items()})
    return summary

def _scan(params, run_dir, devices, channels, actual_rate, device_id):
    frequencies = build_frequency_axis(params); phases = build_phase_axis(params)
    shape = (frequencies.size, phases.size)
    arrays = {name: np.full(shape, np.nan) for name in ("r_mean_v", "r_std_v")}
    attempts = np.full(shape, -1, dtype=int); files = np.empty(shape, dtype=object)
    rf = devices["xy_field"]; hf2 = devices["hf2"]; channel = channels["y_rf"]
    validate_safety_limit("rf_coil", params.frequency_rf_amplitude_vpp)
    for fi, frequency in enumerate(frequencies):
        check_cancelled()
        rf.set_frequency(float(frequency), channel=channel)
        demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
        for pi, phase in enumerate(phases):
            check_cancelled()
            def set_point(frequency=frequency, phase=phase):
                rf.set_frequency(float(frequency), channel=channel)
                _rearm_y_rf(rf, channel, amplitude_vpp=params.frequency_rf_amplitude_vpp, phase_deg=float(phase), frequency_hz=float(frequency), offset_v=params.y_rf_offset_v, waveform_state={"mode": "burst_sine"})
                demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
            summary, attempt, filename = _acquire_valid_r_point(params, run_dir, devices, channels, file_stem=f"frequency_{fi:04d}_phase_{pi:03d}", metadata={"frequency_hz": np.float64(frequency), "phase_deg": np.float64(phase), "y_rf_amplitude_vpp": np.float64(params.frequency_rf_amplitude_vpp), "y_rf_output_on": np.uint8(True)}, set_y_rf=set_point, settle_time_s=params.frequency_settle_time_s, duration_s=params.frequency_duration_s, actual_rate=actual_rate, device_id=device_id)
            for key in arrays:
                arrays[key][fi, pi] = summary[key]
            attempts[fi, pi] = attempt; files[fi, pi] = filename
    np.savez(run_dir.raw / "phase_frequency_scan.npz", frequency_hz=frequencies, phase_deg=phases, **arrays, accepted_attempt_index=attempts, accepted_file=files, actual_rate_sa_s=np.float64(actual_rate), r_std_threshold_v=np.float64(params.r_bad_point_std_threshold_v))

def run(params: MxYOptimalControlRFFrequencyResponseParams) -> Path:
    root = find_project_root(); mapping = load_mapping(root)
    corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
    theory, applied = _corrected_control_contract(corrected)
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version, project_root=root)
    run_dir.update_config(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE, scan_mode="frequency_phase_observation", geometry={"main_field":"Z", "pump":"Z", "probe":"X", "rf_field":"Y", "optimal_control":"Z"}, frequency_axis={"start_hz":params.frequency_start_hz,"stop_hz":params.frequency_stop_hz,"points":params.frequency_points}, phase_axis={"start_deg":params.phase_scan_start_deg,"stop_deg":params.phase_scan_stop_deg,"step_deg":params.phase_scan_step_deg}, acquisition_signals=["Demod0 R"], control_source={"mode":"corrected_run","run":corrected.run_name,"waveform_sha256":corrected.waveform_sha256})
    session = DeviceSession(); devices: dict[str, Any] = {}; channels: dict[str, int] = {}; status = "failed"; failure = None
    try:
        devices, channels = _connect_control_devices(mapping, session)
        run_dir.update_config(device_snapshot={})
        actual_rate, _, clocks, hf2_snapshot = _configure_common_outputs(params, devices, channels, mapping, theory, applied)
        run_dir.update_config(clock_sources=clocks, hf2_configuration=hf2_snapshot)
        source_files = _save_corrected_source_snapshot(run_dir.raw, corrected, theory, applied)
        _scan(params, run_dir, devices, channels, actual_rate, str(mapping["lockin_r"]["device_id"]))
        status = "completed"; run_dir.update_config(completion_status=status, actual_rates={"response_sa_s":actual_rate}, data_files=[*source_files, "raw/phase_frequency_scan.npz"])
        return run_dir.root
    except WorkflowCancelled as exc:
        status = "cancelled"; failure = str(exc); raise
    except Exception as exc:
        failure = str(exc); raise
    finally:
        finish = finalize_run_safety(shutdown=lambda: safe_shutdown(devices, channels, params), completion_status=status, failure_reason=failure)
        if run_dir.config_path.exists(): run_dir.update_config(completion_status=finish.completion_status, failure_reason=finish.failure_reason, safety_shutdown=finish.shutdown_report.to_dict())

def main() -> int:
    run(load_runtime_params(MxYOptimalControlRFFrequencyResponseParams)); return 0

if __name__ == "__main__": raise SystemExit(main())
