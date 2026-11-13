"""逐 Z 先采 SDS 电流，再逐频点采 R；所有波形先恢复温控再写盘。"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import numpy as np
from lockin_amplifier import OscillatorConfig, demod
from ...common import validate_safety_limit
from ...current_feedback import validate_current_power
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from ...steps.scope_waveform import (
    ScopeCaptureSettings, ScopeAutoRangeState, acquire_autoranged_waveform, read_complete_scope_record,
)
from ..mx_z_field_calibration import acquisition as r_points
from ..mx_z_field_calibration._helpers import _sleep, _uncancellable_sleep
from ..mx_z_field_calibration.scan import build_z_axis, build_frequency_axis, predicted_center_hz
from .excitation import _set_z_dc_bias
from .models import MxZCurrentCouplingCalibrationParams

RPointQualityError = r_points.RPointQualityError


class SensePointQualityError(RuntimeError):
    """电阻电压饱和或电流/功率超限，保留诊断且阻止后继频扫。"""


def _temperature_gated_acquire(*args, **kwargs):
    return r_points._temperature_gated_acquire(*args, **kwargs,
        switch=set_temperature_switch, wait=_sleep, recovery_wait=_uncancellable_sleep)


def _acquire_valid_r_point(*args, **kwargs):
    return r_points._acquire_valid_r_point(*args, **kwargs,
        gated=_temperature_gated_acquire, reader=r_points.acquire_r)


def _sense_record(params, time_s, voltage_v, scale, offset, attempts):
    time_s = np.asarray(time_s, dtype=float).reshape(-1)
    voltage_v = np.asarray(voltage_v, dtype=float).reshape(-1)
    current_a = voltage_v / float(params.sense_resistor_ohm)
    return dict(time_s=time_s, sense_voltage_v=voltage_v, current_a=current_a,
        sense_voltage_mean_v=float(np.mean(voltage_v)), sense_voltage_std_v=float(np.std(voltage_v)),
        current_mean_a=float(np.mean(current_a)), current_std_a=float(np.std(current_a)),
        actual_rate_sa_s=float(1.0 / np.median(np.diff(time_s))),
        scale_used_v_div=float(scale), offset_used_v=float(offset), auto_range_attempt_count=int(attempts),
        display_edge_fraction=float(np.max(np.abs(voltage_v + offset)) /
            (scale * params.sense_scope_vertical_divisions / 2.0)))


def _validate_sense_record(params, record):
    voltage = record["sense_voltage_v"]
    if voltage.size < 4 or not np.all(np.isfinite(voltage)):
        raise SensePointQualityError("SDS 采样电阻电压为空或包含非有限值")
    if record["display_edge_fraction"] >= 0.98:
        raise SensePointQualityError(
            "SDS 采样电阻电压到达显示边界，无法排除饱和："
            f"最后量程={record['scale_used_v_div']:.6g} V/div，"
            f"offset={record['offset_used_v']:.6g} V，"
            f"贴边比例={record['display_edge_fraction']:.6g}")
    try:
        validate_current_power(record["current_a"], float(params.sense_resistor_ohm),
            float(params.sense_resistor_power_rating_w),
            derating_fraction=float(params.sense_resistor_power_derating),
            maximum_current_a=float(params.maximum_current_a))
    except ValueError as exc:
        raise SensePointQualityError(str(exc)) from exc


def capture_sense_point(params, path, devices, channels, scope_config, settings, auto_range):
    """每个 Z 点独立门控；自动量程中断也抢救最后已完整读回的一帧。"""
    check_cancelled()
    last = None
    attempts = 0
    queued = False

    def set_enabled(enabled):
        set_temperature_switch(devices["temp_switch"], enabled, channel=channels["temp_switch"])
        if enabled:
            _uncancellable_sleep(params.temp_switch_on_lag_s)

    def capture():
        nonlocal last, attempts
        scale, offset = auto_range.scale_v_div, auto_range.offset_v
        result = read_complete_scope_record(settings, devices, scope_config,
            check_cancelled=check_cancelled, sleep=_sleep)
        attempts += 1
        last = _sense_record(params, result.time, result.voltage, scale, offset, attempts)
        return result

    with DeferredAcquisitionWrites() as pending:
        with acquisition_window(set_enabled, _sleep, params.system_settle_s):
            try:
                waveform = acquire_autoranged_waveform(settings, scope_config, devices["scope"],
                    auto_range, capture=capture, check_cancelled=check_cancelled)
                record = _sense_record(params, waveform["time_s"], waveform["voltage_v"],
                    waveform["scale_used_v_div"], waveform["offset_used_v"], waveform["attempt_count"])
                _validate_sense_record(params, record)
                pending.add(np.savez, path, **record, quality_accepted=np.uint8(1))
                queued = True
            except BaseException as exc:
                if last is not None and not queued:
                    pending.add(np.savez, path, **last, quality_accepted=np.uint8(0),
                        acquisition_error=np.asarray(str(exc)))
                raise
        return record


def _acquire_scan(
    params: MxZCurrentCouplingCalibrationParams,
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
            sense_record = capture_sense_point(
                params, point_dir / "sense_current.npz", devices, channels,
                sense_config, sense_settings, sense_auto_range,
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
