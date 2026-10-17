"""逐 Z 电压、逐频点采集；每次恢复温控后保存原始尝试。"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
import numpy as np
from lockin_amplifier import OscillatorConfig, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ._helpers import _sleep, _uncancellable_sleep
from .models import MxZFieldCalibrationParams
from .scan import build_frequency_axis, build_z_axis, predicted_center_hz
from .excitation import _set_z_dc_bias
from ...current_feedback import validate_current_power
from ...steps.scope_waveform import (ScopeAutoRangeState, ScopeCaptureSettings, acquire_autoranged_waveform, configure_fixed_rate_scope, read_complete_scope_record)
import time


class RPointQualityError(RuntimeError):
    """所有重采尝试均未通过现有标准差门槛。"""


def _temperature_gated_acquire(
    params: MxZFieldCalibrationParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    acquire: Callable[[], dict[str, np.ndarray]],
    writer=None,
    switch=None,
    wait=None,
    recovery_wait=None,
) -> dict[str, np.ndarray]:
    """配置后关温控、统一等待、读入内存，任何退出路径先恢复温控。"""
    check_cancelled()
    switch = switch or set_temperature_switch
    def set_enabled(enabled):
        switch(devices["temp_switch"], enabled, channel=channels["temp_switch"])
        if enabled:
            (recovery_wait or _uncancellable_sleep)(params.temp_switch_on_lag_s)
    with DeferredAcquisitionWrites() as writes:
        with acquisition_window(set_enabled, wait or _sleep, params.system_settle_s):
            payload = acquire()
            if writer is not None:
                writes.add(writer, payload)
        return payload


def _save_point(
    path: Path,
    payload: dict[str, np.ndarray],
    metadata: dict[str, Any],
    actual_rate: float,
) -> dict[str, float]:
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        actual_rate_sa_s=payload.get("actual_rate_Sa_s", np.float64(actual_rate)),
        **metadata,
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
     **{key: value for key, value in payload.items() if key not in {"time_s", "r", "x", "y"}})
    return summary


def _acquire_valid_r_point(
    params: MxZFieldCalibrationParams,
    point_dir: Path,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    file_stem: str,
    metadata: dict[str, Any],
    actual_rate: float,
    device_id: str,
    gated=None,
    reader=None,
) -> tuple[dict[str, float], int, str]:
    gated, reader = gated or _temperature_gated_acquire, reader or acquire_r
    for attempt in range(params.r_point_max_attempts):
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        saved = []
        def save(payload):
            summary = summarize_r(payload)
            result = _save_point(point_dir / filename, payload, {
                **metadata, "attempt_index": np.int64(attempt),
                "quality_accepted": np.uint8(summary["r_std_v"] <= params.r_bad_point_std_threshold_v),
                "r_std_threshold_v": np.float64(params.r_bad_point_std_threshold_v),
                "temp_switch_off_voltage_v": np.float64(0.0),
                "temp_switch_on_voltage_v": np.float64(5.0),
                "temp_switch_off_lead_s": np.float64(params.system_settle_s),
                "temp_switch_on_lag_s": np.float64(params.temp_switch_on_lag_s),
            }, actual_rate)
            saved.append(True)
            return result
        payload = gated(params, devices, channels,
            acquire=lambda: reader(devices["hf2"], device_id=device_id,
                demod_idx=params.demod_idx, actual_rate_sa_s=actual_rate,
                requested_rate_sa_s=params.response_rate_sa_s, duration_s=params.frequency_duration_s),
            writer=save)
        # 旧入口的自定义门控回调可能只执行 acquire，仍在返回后保存。
        if not saved:
            save(payload)
        summary = summarize_r(payload)
        accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V，"
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return {**summary, "actual_rate_sa_s": float(payload.get("actual_rate_Sa_s", actual_rate))}, attempt, filename
    raise RPointQualityError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"std(R) > {params.r_bad_point_std_threshold_v:.6g} V"
    )



def _configure_sense_scope(
    params: Any,
    devices: dict[str, Any],
) -> tuple[Any, ScopeCaptureSettings, ScopeAutoRangeState, dict[str, Any]] | None:
    """为电流耦合标定配置 SDS CH3 的自由触发采集。"""
    if "scope" not in devices:
        return None
    settings = ScopeCaptureSettings(
        sample_rate_sa_s=float(params.sense_scope_sample_rate_sa_s),
        duration_s=float(params.sense_scope_duration_s),
        pd_channel=int(params.sense_scope_channel),
        trigger_mode="AUTO",
        initial_scale_v_div=float(params.sense_scope_initial_scale_v_div),
        offset_v=0.0,
        vertical_divisions=int(params.sense_scope_vertical_divisions),
        scale_min_v_div=float(params.sense_scope_scale_min_v_div),
        scale_max_v_div=float(params.sense_scope_scale_max_v_div),
        auto_range_low_fraction=float(params.sense_scope_auto_range_low_fraction),
        auto_range_high_fraction=float(params.sense_scope_auto_range_high_fraction),
        auto_offset_tolerance_fraction=float(
            params.sense_scope_auto_offset_tolerance_fraction
        ),
        auto_range_max_attempts=int(params.sense_scope_auto_range_max_attempts),
        welch_nperseg=64,
        maximum_frequency_hz=1.0 / max(float(params.sense_scope_duration_s), 1e-6),
        auto_offset_enabled=True,
    )
    config, snapshot = configure_fixed_rate_scope(
        settings,
        devices,
        sleep=time.sleep,
    )
    auto_range = ScopeAutoRangeState(
        scale_v_div=float(snapshot["actual_initial_scale_v_div"]),
        offset_v=float(snapshot["actual_initial_offset_v"]),
        # 直流信号被 offset 居中后只剩噪声，默认不跨工作点缩小量程。
        allow_shrink=bool(
            getattr(params, "sense_scope_auto_range_allow_shrink", False)
        ),
    )
    return config, settings, auto_range, snapshot


def _capture_sense_point(
    params: Any,
    devices: dict[str, Any],
    scope_config: Any,
    settings: ScopeCaptureSettings,
    auto_range: ScopeAutoRangeState,
) -> dict[str, Any]:
    """采集一个 DC 工作点的采样电阻电压并换算平均电流。"""
    waveform = acquire_autoranged_waveform(
        settings,
        scope_config,
        devices["scope"],
        auto_range,
        capture=lambda: read_complete_scope_record(
            settings,
            devices,
            scope_config,
            check_cancelled=check_cancelled,
            sleep=time.sleep,
        ),
        check_cancelled=check_cancelled,
    )
    time_s = np.asarray(waveform["time_s"], dtype=float).reshape(-1)
    voltage_v = np.asarray(waveform["voltage_v"], dtype=float).reshape(-1)
    if voltage_v.size < 4 or not np.all(np.isfinite(voltage_v)):
        raise RuntimeError("SDS 采样电阻电压为空或包含非有限值")
    current_a = voltage_v / float(params.sense_resistor_ohm)
    display_edge_fraction = float(waveform["attempt_display_edge_fraction"][-1])
    if display_edge_fraction >= 0.98:
        attempt_scales = np.asarray(
            waveform["attempt_scales_v_div"], dtype=float
        ).tolist()
        raise RuntimeError(
            "SDS 采样电阻电压到达显示边界，无法排除饱和："
            f"最后量程={float(waveform['scale_used_v_div']):.6g} V/div，"
            f"offset={float(waveform['offset_used_v']):.6g} V，"
            f"电压范围=[{float(np.min(voltage_v)):.6g}, "
            f"{float(np.max(voltage_v)):.6g}] V，"
            f"贴边比例={display_edge_fraction:.6g}，"
            f"尝试量程={attempt_scales}"
        )
    if hasattr(params, "maximum_current_a"):
        validate_current_power(
            current_a,
            float(params.sense_resistor_ohm),
            float(params.sense_resistor_power_rating_w),
            derating_fraction=float(params.sense_resistor_power_derating),
            maximum_current_a=float(params.maximum_current_a),
        )
    return {
        "time_s": time_s,
        "sense_voltage_v": voltage_v,
        "current_a": current_a,
        "sense_voltage_mean_v": float(np.mean(voltage_v)),
        "sense_voltage_std_v": float(np.std(voltage_v)),
        "current_mean_a": float(np.mean(current_a)),
        "current_std_a": float(np.std(current_a)),
        "actual_rate_sa_s": float(1.0 / np.median(np.diff(time_s))),
        "scale_used_v_div": float(waveform["scale_used_v_div"]),
        "offset_used_v": float(waveform["offset_used_v"]),
        "auto_range_attempt_count": int(waveform["attempt_count"]),
        "display_edge_fraction": display_edge_fraction,
    }


def _acquire_scan(
    params: MxZFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
    sense_scope: tuple[
        Any,
        ScopeCaptureSettings,
        ScopeAutoRangeState,
        dict[str, Any],
    ] | None = None,
) -> list[str]:
    forward_axis = build_z_axis(params)
    if bool(getattr(params, "bidirectional_scan", False)):
        z_axis = np.concatenate((forward_axis, forward_axis[-2::-1]))
        scan_directions = np.asarray(
            ["forward"] * forward_axis.size
            + ["reverse"] * max(0, forward_axis.size - 1),
            dtype="U8",
        )
    else:
        z_axis = forward_axis
        scan_directions = np.asarray(["forward"] * forward_axis.size, dtype="U8")
    z_field = devices["z_field"]
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    summary_files: list[str] = []
    auxiliary_files: list[str] = []
    predicted_centers: list[float] = []
    commanded_z_biases: list[float] = []

    for z_index, z_bias in enumerate(z_axis):
        check_cancelled()
        predicted = predicted_center_hz(params, float(z_bias))
        frequency_axis = build_frequency_axis(params, float(z_bias))
        predicted_centers.append(predicted)
        print(
            f"Z 偏置 {z_index + 1}/{len(z_axis)}: {z_bias:+.3f} V，"
            f"预测中心 {predicted:.3f} Hz"
        )
        commanded_z_bias = _set_z_dc_bias(
            z_field,
            channels["z_field"],
            float(z_bias),
            output=True,
        )
        commanded_z_biases.append(commanded_z_bias)
        current_settle_time_s = float(getattr(params, "current_settle_time_s", 0.0))
        if current_settle_time_s > 0.0:
            _sleep(current_settle_time_s)

        point_dir = run_dir.raw / f"z_{z_index:03d}"
        point_dir.mkdir()
        sense_record: dict[str, Any] | None = None
        if sense_scope is not None:
            sense_config, sense_settings, sense_auto_range, _sense_snapshot = sense_scope
            sense_record = _capture_sense_point(
                params,
                devices,
                sense_config,
                sense_settings,
                sense_auto_range,
            )
            np.savez(
                point_dir / "sense_current.npz",
                time_s=sense_record["time_s"],
                sense_voltage_v=sense_record["sense_voltage_v"],
                current_a=sense_record["current_a"],
                sense_voltage_mean_v=np.float64(sense_record["sense_voltage_mean_v"]),
                sense_voltage_std_v=np.float64(sense_record["sense_voltage_std_v"]),
                current_mean_a=np.float64(sense_record["current_mean_a"]),
                current_std_a=np.float64(sense_record["current_std_a"]),
                actual_rate_sa_s=np.float64(sense_record["actual_rate_sa_s"]),
                scale_used_v_div=np.float64(sense_record["scale_used_v_div"]),
                offset_used_v=np.float64(sense_record["offset_used_v"]),
                auto_range_attempt_count=np.int64(
                    sense_record["auto_range_attempt_count"]
                ),
                display_edge_fraction=np.float64(
                    sense_record["display_edge_fraction"]
                ),
            )
        summaries: list[dict[str, float]] = []
        accepted_attempts: list[int] = []
        accepted_files: list[str] = []
        for frequency_index, frequency in enumerate(frequency_axis):
            check_cancelled()
            print(
                f"  频率点 {frequency_index + 1}/{len(frequency_axis)}: "
                f"{frequency:.3f} Hz"
            )
            validate_safety_limit("rf_coil", params.y_rf_amplitude_vpp)
            rf.set_frequency(float(frequency), channel=channels["y_rf"])
            rf.set_phase_adjust(0.0, channel=channels["y_rf"])
            rf.set_amplitude(params.y_rf_amplitude_vpp, channel=channels["y_rf"])
            rf.set_output(True, channel=channels["y_rf"])
            demod.configure_oscillator(
                hf2,
                OscillatorConfig(
                    osc_index=params.demod_osc_idx, frequency=float(frequency)
                ),
            )
            summary, attempt, filename = _acquire_valid_r_point(
                params,
                point_dir,
                devices,
                channels,
                file_stem=f"frequency_{frequency_index:04d}",
                metadata={
                    "z_index": np.int64(z_index),
                    "z_bias_v": np.float64(z_bias),
                    "z_bias_commanded_v": np.float64(commanded_z_bias),
                    "predicted_center_hz": np.float64(predicted),
                    "frequency_index": np.int64(frequency_index),
                    "frequency_hz": np.float64(frequency),
                    "rf_amplitude_vpp": np.float64(params.y_rf_amplitude_vpp),
                },
                actual_rate=actual_rate,
                device_id=device_id,
            )
            summaries.append(summary)
            accepted_attempts.append(attempt)
            accepted_files.append(filename)
        rf.set_output(False, channel=channels["y_rf"])

        summary_path = point_dir / "frequency_scan.npz"
        np.savez(
            summary_path,
            z_index=np.int64(z_index),
            z_bias_v=np.float64(z_bias),
            z_bias_commanded_v=np.float64(commanded_z_bias),
            scan_direction=np.asarray(scan_directions[z_index]),
            predicted_center_hz=np.float64(predicted),
            frequency_hz=frequency_axis,
            r_mean_v=[item["r_mean_v"] for item in summaries],
            r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
            r_std_v=[item["r_std_v"] for item in summaries],
            sense_voltage_mean_v=np.float64(
                sense_record["sense_voltage_mean_v"] if sense_record else np.nan
            ),
            sense_voltage_std_v=np.float64(
                sense_record["sense_voltage_std_v"] if sense_record else np.nan
            ),
            current_mean_a=np.float64(
                sense_record["current_mean_a"] if sense_record else np.nan
            ),
            current_std_a=np.float64(
                sense_record["current_std_a"] if sense_record else np.nan
            ),
            sense_scale_used_v_div=np.float64(
                sense_record["scale_used_v_div"] if sense_record else np.nan
            ),
            sense_offset_used_v=np.float64(
                sense_record["offset_used_v"] if sense_record else np.nan
            ),
            sense_display_edge_fraction=np.float64(
                sense_record["display_edge_fraction"] if sense_record else np.nan
            ),
            accepted_attempt_index=accepted_attempts,
            accepted_file=np.asarray(accepted_files, dtype=str),
            actual_rate_sa_s=np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
        )
        summary_files.append(str(summary_path.relative_to(run_dir.root)).replace("\\", "/"))
        if sense_record is not None:
            auxiliary_files.append(
                str((point_dir / "sense_current.npz").relative_to(run_dir.root)).replace("\\", "/")
            )

    rf.set_output(False, channel=channels["y_rf"])
    np.savez(
        run_dir.raw / "z_scan_index.npz",
        z_bias_v=z_axis,
        z_bias_commanded_v=np.asarray(commanded_z_biases, dtype=float),
        predicted_center_hz=np.asarray(predicted_centers, dtype=float),
        scan_direction=scan_directions,
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return ["raw/z_scan_index.npz", *summary_files, *auxiliary_files]
