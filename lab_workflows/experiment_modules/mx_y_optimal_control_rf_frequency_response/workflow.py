"""Mx Y 最优控制 RF 频率响应采集工作流（含可选常数 Z 控制对照）。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable
import numpy as np
from lockin_amplifier import OscillatorConfig, demod
from ...common import WorkflowCancelled, find_project_root, load_mapping, validate_safety_limit
from ...control_sources import applied_control_from_corrected
from ...current_feedback import load_corrected_control_waveform
from ...experiment_runtime import check_cancelled, load_runtime_params, report_runtime_progress
from ...steps import DeviceSession, create_run_directory, save_corrected_control_source_snapshot
from ...steps.run_finish import finalize_run_safety
from ..mx_z_optimal_control_rf_sensitivity.workflow import (
    _connect_control_devices, _configure_common_outputs,
    _rearm_y_rf, safe_shutdown,
)
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point
from .comparison import ConstantControlPlan, build_constant_control_plan
from .models import MxYOptimalControlRFFrequencyResponseParams

EXPERIMENT_ID = "mx-y-optimal-control-rf-frequency-response"
DATA_TYPE = "Mx_Y_Optimal_Control_RF_Frequency_Response"
EXECUTION_MODE = "typed_workflow"

OPTIMAL_CONTROL_DATA_FILE = "raw/optimal_control_phase_frequency_scan.npz"
CONSTANT_CONTROL_DATA_FILE = "raw/constant_control_frequency_scan.npz"
CONSTANT_CONTROL_PHASE_DEG = 0.0

# 采集阶段占用总进度的前 90%，剩余留给自动分析。
ACQUISITION_PERCENT_BUDGET = 90.0


def scan_point_counts(params: MxYOptimalControlRFFrequencyResponseParams) -> tuple[int, int]:
    """返回（最优控制点数, 常数对照点数），用于进度与 ETA 估算。"""
    frequency_count = int(build_frequency_axis(params).size)
    optimal_count = frequency_count * int(build_phase_axis(params).size)
    constant_count = frequency_count if params.comparison_enabled else 0
    return optimal_count, constant_count


@dataclass(slots=True)
class ScanProgress:
    """按已完成点的累计平均耗时估算剩余时间；坏点重采耗时自然计入。"""

    total: int
    completed: int = 0
    started_at: float | None = None
    clock: Callable[[], float] = monotonic

    def __post_init__(self) -> None:
        if self.started_at is None:
            self.started_at = self.clock()

    def advance(self, stage: str, description: str) -> None:
        self.completed += 1
        elapsed = self.clock() - self.started_at
        remaining = elapsed / self.completed * (self.total - self.completed)
        report_runtime_progress(
            stage,
            f"{description}；总进度 {self.completed}/{self.total}",
            percent=ACQUISITION_PERCENT_BUDGET * self.completed / self.total,
            estimated_remaining_seconds=remaining,
        )

def build_frequency_axis(params: MxYOptimalControlRFFrequencyResponseParams) -> np.ndarray:
    return np.linspace(params.frequency_start_hz, params.frequency_stop_hz, params.frequency_points, dtype=float)

def build_phase_axis(params: MxYOptimalControlRFFrequencyResponseParams) -> np.ndarray:
    return params.phase_axis_deg()

def _point_metadata(frequency: float, phase: float, amplitude_vpp: float) -> dict[str, Any]:
    """两个阶段共用的单点原始文件元数据。"""
    return {
        "frequency_hz": np.float64(frequency),
        "phase_deg": np.float64(phase),
        "y_rf_amplitude_vpp": np.float64(amplitude_vpp),
        "y_rf_output_on": np.uint8(True),
    }

def _scan_optimal_control(params, run_dir, devices, channels, actual_rate, device_id, progress: ScanProgress):
    """完整扫描频率 × Burst 相位的最优控制响应。"""
    frequencies = build_frequency_axis(params); phases = build_phase_axis(params)
    shape = (frequencies.size, phases.size)
    arrays = {name: np.full(shape, np.nan) for name in ("r_mean_v", "r_std_v")}
    attempts = np.full(shape, -1, dtype=int); files = np.empty(shape, dtype=object)
    rf = devices["xy_field"]; hf2 = devices["hf2"]; channel = channels["y_rf"]
    for fi, frequency in enumerate(frequencies):
        for pi, phase in enumerate(phases):
            check_cancelled()
            def set_point(frequency=frequency, phase=phase):
                rf.set_frequency(float(frequency), channel=channel)
                _rearm_y_rf(rf, channel, amplitude_vpp=params.frequency_rf_amplitude_vpp, phase_deg=float(phase), offset_v=params.y_rf_offset_v)
                demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
            summary, attempt, filename = _acquire_valid_r_point(params, run_dir, devices, channels, file_stem=f"optimal_frequency_{fi:04d}_phase_{pi:03d}", metadata=_point_metadata(frequency, phase, params.frequency_rf_amplitude_vpp), set_y_rf=set_point, settle_time_s=params.frequency_settle_time_s, duration_s=params.frequency_duration_s, actual_rate=actual_rate, device_id=device_id)
            for key in arrays:
                arrays[key][fi, pi] = summary[key]
            attempts[fi, pi] = attempt; files[fi, pi] = filename
            progress.advance(
                "optimal_control_scan",
                f"最优控制扫描：频率 {fi + 1}/{frequencies.size}，相位 {pi + 1}/{phases.size}",
            )
    np.savez(run_dir.raw / "optimal_control_phase_frequency_scan.npz", frequency_hz=frequencies, phase_deg=phases, **arrays, accepted_attempt_index=attempts, accepted_file=files, actual_rate_sa_s=np.float64(actual_rate), r_std_threshold_v=np.float64(params.r_bad_point_std_threshold_v))

def _configure_constant_control_z(params, devices, channels, plan: ConstantControlPlan) -> None:
    """把 Z 通道从最优控制任意波切为已验证的恒定 DC 输出。"""
    z_field = devices["z_field"]
    channel = channels["z_field"]
    voltage = plan.target_voltage_v
    validate_safety_limit("Z_magnetic_field", voltage)
    z_field.set_output(False, channel=channel)
    z_field.set_burst_state(False, channel=channel)
    z_field.set_mod_state(False, channel=channel)
    z_field.setup_dc(voltage, channel=channel)
    z_field.set_output(True, channel=channel)
    if str(z_field.get_shape(channel=channel)).upper() != "DC":
        raise RuntimeError(f"Z 通道切换恒定 DC 失败：波形形状回读为 {z_field.get_shape(channel=channel)}")
    if not bool(z_field.get_output(channel=channel)):
        raise RuntimeError("Z 通道切换恒定 DC 失败：输出状态回读为 OFF")

def _restore_optimal_control_z(params, devices, channels, control_source, applied) -> list[str]:
    """常数扫描结束后把 Z 通道恢复为最优控制任意波并重新开启输出。"""
    from ...z_arbitrary_control import configure_z_optimal_control_output

    try:
        configure_z_optimal_control_output(params, devices["z_field"], channels["z_field"], control_source, applied)
    except Exception as exc:
        return [f"恢复 Z 最优控制任意波失败: {exc}"]
    return []

def _scan_constant_control(params, run_dir, devices, channels, actual_rate, device_id, plan: ConstantControlPlan, progress: ScanProgress):
    """Z 恒定 DC 下逐频率采集单个 0° 相位响应点。"""
    frequencies = build_frequency_axis(params)
    row = {name: np.full(frequencies.size, np.nan) for name in ("r_mean_v", "r_std_v")}
    attempts = np.full(frequencies.size, -1, dtype=int); files = np.empty(frequencies.size, dtype=object)
    rf = devices["xy_field"]; hf2 = devices["hf2"]; channel = channels["y_rf"]
    for fi, frequency in enumerate(frequencies):
        check_cancelled()
        def set_point(frequency=frequency):
            rf.set_frequency(float(frequency), channel=channel)
            _rearm_y_rf(rf, channel, amplitude_vpp=params.frequency_rf_amplitude_vpp, phase_deg=CONSTANT_CONTROL_PHASE_DEG, offset_v=params.y_rf_offset_v)
            demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
        summary, attempt, filename = _acquire_valid_r_point(params, run_dir, devices, channels, file_stem=f"constant_frequency_{fi:04d}", metadata=_point_metadata(frequency, CONSTANT_CONTROL_PHASE_DEG, params.frequency_rf_amplitude_vpp), set_y_rf=set_point, settle_time_s=params.frequency_settle_time_s, duration_s=params.frequency_duration_s, actual_rate=actual_rate, device_id=device_id)
        for key in row:
            row[key][fi] = summary[key]
        attempts[fi] = attempt; files[fi] = filename
        progress.advance(
            "constant_control_scan",
            f"常数控制对照：频率 {fi + 1}/{frequencies.size}",
        )
    np.savez(run_dir.raw / "constant_control_frequency_scan.npz", frequency_hz=frequencies, phase_deg=np.full(frequencies.size, CONSTANT_CONTROL_PHASE_DEG), **row, accepted_attempt_index=attempts, accepted_file=files, actual_rate_sa_s=np.float64(actual_rate), r_std_threshold_v=np.float64(params.r_bad_point_std_threshold_v), constant_z_voltage_v=np.float64(plan.target_voltage_v), constant_z_extrapolated=np.uint8(plan.extrapolated))

def run(params: MxYOptimalControlRFFrequencyResponseParams) -> Path:
    root = find_project_root(); mapping = load_mapping(root)
    corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
    applied = applied_control_from_corrected(corrected)
    plan = (
        build_constant_control_plan(root, calibration_run=params.constant_control_calibration_source_run, target_larmor_frequency_hz=params.constant_control_larmor_frequency_hz)
        if params.comparison_enabled
        else None
    )
    run_dir = create_run_directory(DATA_TYPE, params.run_tag, params.to_external(), schema_version=params.schema_version, project_root=root)
    run_dir.update_config(experiment_id=EXPERIMENT_ID, data_type=DATA_TYPE, execution_mode=EXECUTION_MODE, scan_mode="optimal_control_with_constant_control_comparison" if plan else "optimal_control_phase_frequency", geometry={"main_field":"Z", "pump":"Z", "probe":"X", "rf_field":"Y", "optimal_control":"Z"}, frequency_axis={"start_hz":params.frequency_start_hz,"stop_hz":params.frequency_stop_hz,"points":params.frequency_points}, phase_axis={"start_deg":params.phase_scan_start_deg,"stop_deg":params.phase_scan_stop_deg,"step_deg":params.phase_scan_step_deg}, acquisition_signals=["Demod0 R"], control_source={"mode":"corrected_run","run":corrected.run_name,"waveform_sha256":corrected.waveform_sha256}, comparison=(plan.to_metadata() if plan else {"enabled": False}))
    session = DeviceSession(); devices: dict[str, Any] = {}; channels: dict[str, int] = {}; status = "failed"; failure = None; data_files: list[str] = []
    try:
        devices, channels = _connect_control_devices(mapping, session)
        actual_rate, _, clocks, hf2_snapshot = _configure_common_outputs(params, devices, channels, mapping, corrected, applied)
        run_dir.update_config(clock_sources=clocks, hf2_configuration=hf2_snapshot)
        source_files = save_corrected_control_source_snapshot(run_dir.raw, corrected, applied)
        optimal_count, constant_count = scan_point_counts(params)
        progress = ScanProgress(total=optimal_count + constant_count)
        _scan_optimal_control(params, run_dir, devices, channels, actual_rate, str(mapping["lockin_r"]["device_id"]), progress)
        data_files = [*source_files, OPTIMAL_CONTROL_DATA_FILE]
        if plan is not None:
            _configure_constant_control_z(params, devices, channels, plan)
            _scan_constant_control(params, run_dir, devices, channels, actual_rate, str(mapping["lockin_r"]["device_id"]), plan, progress)
            data_files.append(CONSTANT_CONTROL_DATA_FILE)
        status = "completed"; run_dir.update_config(completion_status=status, actual_rates={"response_sa_s":actual_rate}, data_files=data_files)
        return run_dir.root
    except WorkflowCancelled as exc:
        status = "cancelled"; failure = str(exc); raise
    except Exception as exc:
        failure = str(exc); raise
    finally:
        restore_actions = (
            (("恢复 Z 最优控制任意波", lambda: _restore_optimal_control_z(params, devices, channels, corrected, applied)),)
            if plan is not None and devices.get("z_field") is not None and "z_field" in channels
            else ()
        )
        finish = finalize_run_safety(shutdown=lambda: safe_shutdown(devices, channels, params), completion_status=status, failure_reason=failure, extra_restores=restore_actions)
        run_dir.update_config(completion_status=finish.completion_status, failure_reason=finish.failure_reason, safety_shutdown=finish.shutdown_report.to_dict())
        if finish.completion_status != "completed" and not finish.original_exception_pending:
            raise RuntimeError(f"实验结束但安全恢复失败: {finish.failure_reason}")

def main() -> int:
    run(load_runtime_params(MxYOptimalControlRFFrequencyResponseParams)); return 0

if __name__ == "__main__": raise SystemExit(main())
