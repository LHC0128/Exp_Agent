"""频率×Burst相位扫描和恒定Z对照；沿用单点温控门控与真实写盘。"""
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable
import numpy as np
from lockin_amplifier import OscillatorConfig, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..mx_z_optimal_control_rf_sensitivity.workflow import _rearm_y_rf
from ..mx_y_rf_sensitivity.workflow import _acquire_valid_r_point, RPointQualityError
from .comparison import ConstantControlPlan
from .models import MxYOptimalControlRFFrequencyResponseParams

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

def _scan_optimal_control(params, run_dir, devices, channels, actual_rate, device_id, progress: ScanProgress, *, point_acquirer=None):
    point_acquirer = point_acquirer or _acquire_valid_r_point
    """完整扫描频率 × Burst 相位的最优控制响应。"""
    frequencies = build_frequency_axis(params); phases = build_phase_axis(params)
    shape = (frequencies.size, phases.size)
    arrays = {name: np.full(shape, np.nan) for name in ("r_mean_v", "r_std_v")}
    attempts = np.full(shape, -1, dtype=int); files = np.full(shape, "", dtype=object)
    rf = devices["xy_field"]; hf2 = devices["hf2"]; channel = channels["y_rf"]
    for fi, frequency in enumerate(frequencies):
        for pi, phase in enumerate(phases):
            check_cancelled()
            def set_point(frequency=frequency, phase=phase):
                rf.set_frequency(float(frequency), channel=channel)
                _rearm_y_rf(rf, channel, amplitude_vpp=params.frequency_rf_amplitude_vpp, phase_deg=float(phase), offset_v=params.y_rf_offset_v)
                demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
            summary, attempt, filename = point_acquirer(params, run_dir, devices, channels, file_stem=f"optimal_frequency_{fi:04d}_phase_{pi:03d}", metadata=_point_metadata(frequency, phase, params.frequency_rf_amplitude_vpp), set_y_rf=set_point, settle_time_s=params.system_settle_s, duration_s=params.frequency_duration_s, actual_rate=actual_rate, device_id=device_id)
            for key in arrays:
                arrays[key][fi, pi] = summary[key]
            attempts[fi, pi] = attempt; files[fi, pi] = filename
            progress.advance(
                "optimal_control_scan",
                f"最优控制扫描：频率 {fi + 1}/{frequencies.size}，相位 {pi + 1}/{phases.size}",
            )
    np.savez(run_dir.raw / "optimal_control_phase_frequency_scan.npz", frequency_hz=frequencies, phase_deg=phases, **arrays, accepted_attempt_index=attempts, accepted_file=files, actual_rate_sa_s=np.float64(summary.get("actual_rate_sa_s", actual_rate)), r_std_threshold_v=np.float64(params.r_bad_point_std_threshold_v))

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

def _scan_constant_control(params, run_dir, devices, channels, actual_rate, device_id, plan: ConstantControlPlan, progress: ScanProgress, *, point_acquirer=None):
    point_acquirer = point_acquirer or _acquire_valid_r_point
    """Z 恒定 DC 下逐频率采集单个 0° 相位响应点。"""
    frequencies = build_frequency_axis(params)
    row = {name: np.full(frequencies.size, np.nan) for name in ("r_mean_v", "r_std_v")}
    attempts = np.full(frequencies.size, -1, dtype=int); files = np.full(frequencies.size, "", dtype=object)
    rf = devices["xy_field"]; hf2 = devices["hf2"]; channel = channels["y_rf"]
    for fi, frequency in enumerate(frequencies):
        check_cancelled()
        def set_point(frequency=frequency):
            rf.set_frequency(float(frequency), channel=channel)
            _rearm_y_rf(rf, channel, amplitude_vpp=params.frequency_rf_amplitude_vpp, phase_deg=CONSTANT_CONTROL_PHASE_DEG, offset_v=params.y_rf_offset_v)
            demod.configure_oscillator(hf2, OscillatorConfig(osc_index=params.demod_osc_idx, frequency=float(frequency)))
        summary, attempt, filename = point_acquirer(params, run_dir, devices, channels, file_stem=f"constant_frequency_{fi:04d}", metadata=_point_metadata(frequency, CONSTANT_CONTROL_PHASE_DEG, params.frequency_rf_amplitude_vpp), set_y_rf=set_point, settle_time_s=params.system_settle_s, duration_s=params.frequency_duration_s, actual_rate=actual_rate, device_id=device_id)
        for key in row:
            row[key][fi] = summary[key]
        attempts[fi] = attempt; files[fi] = filename
        progress.advance(
            "constant_control_scan",
            f"常数控制对照：频率 {fi + 1}/{frequencies.size}",
        )
    np.savez(run_dir.raw / "constant_control_frequency_scan.npz", frequency_hz=frequencies, phase_deg=np.full(frequencies.size, CONSTANT_CONTROL_PHASE_DEG), **row, accepted_attempt_index=attempts, accepted_file=files, actual_rate_sa_s=np.float64(summary.get("actual_rate_sa_s", actual_rate)), r_std_threshold_v=np.float64(params.r_bad_point_std_threshold_v), constant_z_voltage_v=np.float64(plan.target_voltage_v), constant_z_extrapolated=np.uint8(plan.extrapolated))
