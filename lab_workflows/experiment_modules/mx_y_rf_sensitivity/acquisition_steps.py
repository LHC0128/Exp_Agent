"""扫频、带符号幅度与零 RF 噪声的采集、质量重采和恢复后写盘。"""
from __future__ import annotations
from typing import Any, Callable
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import yaml
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from .models import MxYRFParams
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from ._helpers import _sleep, _uncancellable_sleep
from .acquisition import acquire_r, summarize_r
from .analysis_core import fit_lorentzian_response
from .scan import build_amplitude_axis, build_frequency_axis, signed_amplitude_hardware
from .excitation import (_set_signed_rf, _restore_response_acquisition_state, _set_y_rf_zero_off,
    _set_y_rf_zero_off_with_temperature_sequence)
from ...steps import set_temperature_switch

class RPointQualityError(RuntimeError):
    """单个 R 点在限定次数内始终未通过质量门限。"""

def _save_point(path: Path, payload: dict[str, np.ndarray], metadata: dict[str, Any], actual_rate: float) -> dict[str, float]:
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        actual_rate_sa_s=payload.get("actual_rate_Sa_s", np.float64(actual_rate)),
        **{key: value for key, value in metadata.items()},
        **{key: np.float64(value) if isinstance(value, float) else value for key, value in summary.items()},
     **{key: value for key, value in payload.items() if key not in {"time_s", "r", "x", "y"}})
    return summary

def _temperature_gated_acquire(
    params: MxYRFParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    set_y_rf: Callable[[], None],
    settle_time_s: float,
    acquire: Callable[[], Any],
    switch=None, wait=None,
) -> Any:
    """配置后关温控、统一等待、读入内存，任何退出路径先恢复温控。"""
    switch = switch or set_temperature_switch
    wait = wait or _sleep
    check_cancelled()
    set_y_rf()
    def set_enabled(enabled):
        switch(devices["temp_switch"], enabled, channel=channels["temp_switch"])
        if enabled:
            _uncancellable_sleep(params.temp_switch_on_lag_s)
    with acquisition_window(set_enabled, wait, params.system_settle_s):
        return acquire()

def _acquire_valid_r_point(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    file_stem: str,
    metadata: dict[str, Any],
    set_y_rf: Callable[[], None],
    settle_time_s: float,
    duration_s: float,
    actual_rate: float,
    device_id: str,
    reader=None, switch=None, wait=None, gated_acquire=None,
) -> tuple[dict[str, float], int, str]:
    """采集一个 R 点；标准差超限时完整保存并重新采集。"""
    reader = reader or acquire_r
    gated_acquire = gated_acquire or _temperature_gated_acquire
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        checker = devices.get("_compliance_checker")
        if checker is not None:
            checker(f"{file_stem} 采集前")
        with DeferredAcquisitionWrites() as pending:
            def acquire_and_buffer():
                payload = reader(
                        hf2,
                        device_id=device_id,
                        demod_idx=params.demod_idx,
                        actual_rate_sa_s=actual_rate, requested_rate_sa_s=params.response_rate_sa_s,
                        duration_s=duration_s,
                    )
                summary = summarize_r(payload)
                accepted = (
                    summary["r_std_v"]
                    <= params.r_bad_point_std_threshold_v
                )
                filename = f"{file_stem}_attempt_{attempt:02d}.npz"
                pending.add(_save_point,
                    run_dir.raw / filename,
                    payload,
                    {
                        **metadata,
                        "attempt_index": np.int64(attempt),
                        "quality_accepted": np.uint8(accepted),
                        "r_std_threshold_v": np.float64(
                            params.r_bad_point_std_threshold_v
                        ),
                    },
                    actual_rate,
                )
                return payload
            payload = gated_acquire(
                params,
                devices,
                channels,
                set_y_rf=set_y_rf,
                settle_time_s=settle_time_s,
                acquire=acquire_and_buffer, switch=switch, wait=wait,
            )
        summary = summarize_r(payload)
        if checker is not None:
            checker(f"{file_stem} 采集后")
        accepted = (
            summary["r_std_v"]
            <= params.r_bad_point_std_threshold_v
        )
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return {**summary, "actual_rate_sa_s": float(payload.get("actual_rate_Sa_s", actual_rate))}, attempt, filename
    raise RPointQualityError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"std(R) > {params.r_bad_point_std_threshold_v:.6g} V"
    )

def _acquire_frequency_scan(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> dict[str, Any]:
    axis = build_frequency_axis(params)
    summaries: list[dict[str, float]] = []
    accepted_attempts: list[int] = []
    accepted_files: list[str] = []
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    for index, frequency in enumerate(axis):
        check_cancelled()
        print(f"模式1频率点 {index + 1}/{len(axis)}: {frequency:.3f} Hz")

        def set_frequency_point() -> None:
            validate_safety_limit(
                "rf_coil",
                params.frequency_rf_amplitude_vpp,
            )
            rf.set_frequency(float(frequency), channel=channels["y_rf"])
            rf.set_phase_adjust(0.0, channel=channels["y_rf"])
            rf.set_amplitude(
                params.frequency_rf_amplitude_vpp,
                channel=channels["y_rf"],
            )
            rf.set_output(True, channel=channels["y_rf"])
            demod.configure_oscillator(
                hf2,
                OscillatorConfig(
                    osc_index=params.demod_osc_idx,
                    frequency=float(frequency),
                ),
            )

        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"frequency_{index:04d}",
            metadata={
                "frequency_hz": np.float64(frequency),
                "rf_amplitude_vpp": np.float64(
                    params.frequency_rf_amplitude_vpp
                ),
            },
            set_y_rf=set_frequency_point,
            settle_time_s=params.system_settle_s,
            duration_s=params.frequency_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        accepted_attempts.append(attempt)
        accepted_files.append(filename)
    r_values = np.asarray([item["r_scalar_mean_v"] for item in summaries])
    np.savez(
        run_dir.raw / "frequency_scan.npz",
        frequency_hz=axis,
        r_mean_v=r_values,
        r_scalar_mean_v=r_values,
        r_std_v=[item["r_std_v"] for item in summaries],
        accepted_attempt_index=accepted_attempts,
        accepted_file=accepted_files,
        actual_rate_sa_s=np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
    )
    fit = fit_lorentzian_response(
        axis,
        r_values,
        r_squared_min=params.fit_r_squared_min,
        relative_gamma_uncertainty_max=params.fit_relative_gamma_uncertainty_max,
    )
    with (run_dir.results / "frequency_gate.yaml").open("w", encoding="utf-8") as stream:
        yaml.safe_dump(fit.to_dict(), stream, allow_unicode=True, sort_keys=False)
    if not fit.success:
        raise RPointQualityError("模式1 R-Lorentzian 拟合质量不合格: " + "；".join(fit.rejection_reasons))
    return fit.to_dict()

def _acquire_amplitude_scan(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    expected_actual_rate: float,
    device_id: str,
) -> float:
    axis = build_amplitude_axis(params)
    summaries: list[dict[str, float]] = []
    hardware_records: list[dict[str, Any]] = []
    accepted_attempts: list[int] = []
    accepted_files: list[str] = []
    rf = devices["xy_field"]
    actual_rate = _restore_response_acquisition_state(
        params,
        devices,
        channels,
    )
    if not np.isclose(actual_rate, expected_actual_rate):
        print(
            "HF2 响应采样率回读已更新: "
            f"{expected_actual_rate:.3f} -> {actual_rate:.3f} Sa/s"
        )
    for index, amplitude in enumerate(axis):
        check_cancelled()
        print(f"Y RF 幅度点 {index + 1}/{len(axis)}: {amplitude:+.6f} Vpp")
        hardware_amplitude, hardware_phase, output_on = (
            signed_amplitude_hardware(float(amplitude))
        )
        hardware = {
            "hardware_amplitude_vpp": hardware_amplitude,
            "hardware_phase_deg": hardware_phase,
            "output_on": output_on,
        }
        summary, attempt, filename = _acquire_valid_r_point(
            params,
            run_dir,
            devices,
            channels,
            file_stem=f"amplitude_{index:04d}",
            metadata={
                "signed_amplitude_vpp": np.float64(amplitude),
                **hardware,
            },
            set_y_rf=lambda amplitude=amplitude: _set_signed_rf(
                rf,
                channels["y_rf"],
                float(amplitude),
            ),
            settle_time_s=params.system_settle_s,
            duration_s=params.response_duration_s,
            actual_rate=actual_rate,
            device_id=device_id,
        )
        summaries.append(summary)
        hardware_records.append(hardware)
        accepted_attempts.append(attempt)
        accepted_files.append(filename)
    np.savez(
        run_dir.raw / "amplitude_scan.npz",
        signed_amplitude_vpp=axis,
        hardware_amplitude_vpp=[item["hardware_amplitude_vpp"] for item in hardware_records],
        hardware_phase_deg=[item["hardware_phase_deg"] for item in hardware_records],
        output_on=[item["output_on"] for item in hardware_records],
        r_mean_v=[item["r_mean_v"] for item in summaries],
        r_scalar_mean_v=[item["r_scalar_mean_v"] for item in summaries],
        r_std_v=[item["r_std_v"] for item in summaries],
        accepted_attempt_index=accepted_attempts,
        accepted_file=accepted_files,
        r_std_threshold_v=np.float64(
            params.r_bad_point_std_threshold_v
        ),
        actual_rate_sa_s=np.float64(summaries[0].get("actual_rate_sa_s", actual_rate)),
    )
    return actual_rate

def _acquire_noise(
    params: MxYRFParams,
    run_dir: Any,
    devices: dict[str, Any],
    channels: dict[str, int],
    device_id: str,
    *,
    set_y_rf_off_state: Callable[[], None] | None = None,
    y_rf_dc_v: float = 0.0,
    y_rf_output_on: bool = False,
    noise_metadata: dict[str, Any] | None = None,
    noise_label: str = "零 Y RF 噪声",
    settle_time_s: float = 0.0,
) -> float:
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.demod_idx,
            enable=True,
            rate=params.noise_rate_sa_s,
            input_channel=0,
            osc_select=params.demod_osc_idx,
            harmonic=1,
            time_constant=params.noise_time_constant_s,
            order=params.noise_demod_order,
            phase=float(hf2.get_double(f"{hf2.demod_path(params.demod_idx)}/phaseshift")),
        ),
     for_acquisition=True)
    rf_off_setter = set_y_rf_off_state or (
        lambda: _set_y_rf_zero_off(rf, channels["y_rf"])
    )
    for index in range(params.noise_n_avg):
        check_cancelled()
        checker = devices.get("_compliance_checker")
        if checker is not None:
            checker(f"{noise_label} {index + 1} 采集前")
        print(f"{noise_label} {index + 1}/{params.noise_n_avg}")
        with DeferredAcquisitionWrites() as pending:
            def acquire_and_buffer():
                payload = acquire_r(
                        hf2,
                        device_id=device_id,
                        demod_idx=params.demod_idx,
                        actual_rate_sa_s=float(actual_rate), requested_rate_sa_s=params.noise_rate_sa_s,
                        duration_s=params.noise_duration_s,
                    )
                metadata = {
                    "y_rf_dc_v": np.float64(y_rf_dc_v),
                    "y_rf_output_on": np.uint8(y_rf_output_on),
                }
                if noise_metadata:
                    metadata.update(noise_metadata)
                pending.add(_save_point,
                    run_dir.raw / f"noise_{index:03d}.npz",
                    payload,
                    metadata,
                    float(actual_rate),
                )
                return payload
            payload = _temperature_gated_acquire(
                params,
                devices,
                channels,
                set_y_rf=rf_off_setter,
                settle_time_s=settle_time_s,
                acquire=acquire_and_buffer,
            )
        if checker is not None:
            checker(f"{noise_label} {index + 1} 采集后")
        metadata = {
            "y_rf_dc_v": np.float64(y_rf_dc_v),
            "y_rf_output_on": np.uint8(y_rf_output_on),
        }
        if noise_metadata:
            metadata.update(noise_metadata)
    return float(payload.get("actual_rate_Sa_s", actual_rate))

def acquire_mx_y_rf_point(
    params: MxYRFParams,
    raw_dir: Path,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_response_rate: float,
    device_id: str,
    *,
    results_dir: Path | None = None,
) -> dict[str, Any]:
    """把一个完整工作点的原始数据写入指定目录。"""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target_results = Path(results_dir) if results_dir is not None else raw_dir
    target_results.mkdir(parents=True, exist_ok=True)
    point_run_dir = SimpleNamespace(raw=raw_dir, results=target_results)
    frequency_fit = None
    primary_failure = None
    try:
        if params.linewidth_mode == "frequency_sweep":
            frequency_fit = _acquire_frequency_scan(
                params,
                point_run_dir,
                devices,
                channels,
                actual_response_rate,
                device_id,
            )
        actual_response_rate = _acquire_amplitude_scan(
            params,
            point_run_dir,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        actual_noise_rate = _acquire_noise(
            params,
            point_run_dir,
            devices,
            channels,
            device_id,
        )
    except BaseException as exc:
        primary_failure = exc
        raise
    finally:
        try:
            _set_y_rf_zero_off_with_temperature_sequence(params, devices, channels)
        except BaseException as cleanup_error:
            if primary_failure is None:
                raise
            primary_failure.safety_cleanup_errors = (
                *getattr(primary_failure, "safety_cleanup_errors", ()), cleanup_error
            )
            message = f"Y RF 归零与温控恢复失败: {cleanup_error}"
            if hasattr(primary_failure, "add_note"):
                primary_failure.add_note(message)
            print(message)
    return {
        "actual_response_rate_sa_s": actual_response_rate,
        "actual_noise_rate_sa_s": actual_noise_rate,
        "frequency_gate_fit": frequency_fit,
    }
