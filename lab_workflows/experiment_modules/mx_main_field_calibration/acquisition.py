"""逐电流、逐频点采集；每次恢复温控后保存原始尝试。"""
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
from .models import MxMainFieldCalibrationParams
from .scan import build_frequency_axis, build_main_field_axis, predicted_center_hz


class RPointQualityError(RuntimeError):
    """所有重采尝试均未通过现有标准差门槛。"""


def _temperature_gated_acquire(
    params: MxMainFieldCalibrationParams,
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
    params: MxMainFieldCalibrationParams,
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
                "settle_time_s": np.float64(params.system_settle_s),
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


def _acquire_scan(
    params: MxMainFieldCalibrationParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> list[str]:
    current_axis = build_main_field_axis(params)
    gs200 = devices["gs200"]
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    summary_files: list[str] = []
    predicted_centers: list[float] = []

    for current_index, current_ma in enumerate(current_axis):
        check_cancelled()
        predicted = predicted_center_hz(params, float(current_ma))
        frequency_axis = build_frequency_axis(params, float(current_ma))
        predicted_centers.append(predicted)
        print(
            f"主场电流 {current_index + 1}/{len(current_axis)}: {current_ma:.3f} mA，"
            f"预测中心 {predicted:.3f} Hz"
        )
        validate_safety_limit("main_magnetic_field", float(current_ma))
        gs200.set_current(float(current_ma) / 1000.0)
        gs200.set_output(True)

        point_dir = run_dir.raw / f"current_{current_index:03d}"
        point_dir.mkdir()
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
                    "current_index": np.int64(current_index),
                    "main_field_current_ma": np.float64(current_ma),
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
            current_index=np.int64(current_index),
            main_field_current_ma=np.float64(current_ma),
            predicted_center_hz=np.float64(predicted),
            frequency_hz=frequency_axis,
            r_mean_v=[item["r_mean_v"] for item in summaries],
            r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
            r_std_v=[item["r_std_v"] for item in summaries],
            accepted_attempt_index=accepted_attempts,
            accepted_file=np.asarray(accepted_files, dtype=str),
            actual_rate_sa_s=np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
        )
        summary_files.append(
            str(summary_path.relative_to(run_dir.root)).replace("\\", "/")
        )

    rf.set_output(False, channel=channels["y_rf"])
    np.savez(
        run_dir.raw / "main_field_scan_index.npz",
        main_field_current_ma=current_axis,
        predicted_center_hz=np.asarray(predicted_centers, dtype=float),
        summary_file=np.asarray(summary_files, dtype=str),
    )
    return ["raw/main_field_scan_index.npz", *summary_files]
