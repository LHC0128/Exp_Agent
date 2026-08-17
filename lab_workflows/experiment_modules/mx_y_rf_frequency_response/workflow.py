"""Mx Y 向 RF 频率响应采集工作流。

工作点配置、设备连接与安全停机复用 mx_y_rf_sensitivity 工作流的公共
函数；本模块只负责模式 1 风格的固定幅度扫频，并把幅度作为可选的外层
扫描轴。每个 (幅度, 频率) 点按“设置 Y RF → 温控关 → 等待稳定 → 采集
R → 温控开 → 等待”的顺序执行，std(R) 超过阈值时保存后重采。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from lockin_amplifier import OscillatorConfig, demod

from ...common import find_project_root, load_mapping, validate_safety_limit
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import (
    DeviceSession,
    create_run_directory,
    set_temperature_switch,
)
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ..mx_y_rf_sensitivity.workflow import (
    RPointQualityError,
    configure_mx_y_rf_outputs,
    connect_mx_y_rf_devices,
    safe_shutdown,
    snapshot_mx_y_rf_state,
)
from .models import MxYRFFrequencyResponseParams


EXPERIMENT_ID = "mx-y-rf-frequency-response"
DATA_TYPE = "Mx_Y_RF_Frequency_Response"
EXECUTION_MODE = "typed_workflow"


def build_rf_amplitude_axis(params: MxYRFFrequencyResponseParams) -> np.ndarray:
    """构造 RF 幅度轴；点数等于 1 时退化为单幅度。"""
    if params.rf_amplitude_points == 1:
        return np.asarray([params.rf_amplitude_start_vpp], dtype=float)
    return np.linspace(
        params.rf_amplitude_start_vpp,
        params.rf_amplitude_stop_vpp,
        params.rf_amplitude_points,
        dtype=float,
    )


def build_frequency_axis(params: MxYRFFrequencyResponseParams) -> np.ndarray:
    """构造模式 1 风格的线性频率轴。"""
    return np.linspace(
        params.frequency_start_hz,
        params.frequency_stop_hz,
        params.frequency_points,
        dtype=float,
    )


def _sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _uncancellable_sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def _set_frequency_point(
    params: MxYRFFrequencyResponseParams,
    rf: Any,
    hf2: Any,
    channel: int,
    frequency_hz: float,
    amplitude_vpp: float,
) -> None:
    """设置 Y RF 通道频率、相位和幅度，并让 HF2 振荡器跟随频率。"""
    validate_safety_limit("rf_coil", float(amplitude_vpp))
    rf.set_frequency(float(frequency_hz), channel=channel)
    rf.set_phase_adjust(0.0, channel=channel)
    rf.set_amplitude(float(amplitude_vpp), channel=channel)
    rf.set_output(True, channel=channel)
    demod.configure_oscillator(
        hf2,
        OscillatorConfig(
            osc_index=params.demod_osc_idx,
            frequency=float(frequency_hz),
        ),
    )


def _temperature_gated_acquire(
    params: MxYRFFrequencyResponseParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    *,
    set_y_rf: Callable[[], None],
    settle_time_s: float,
    acquire: Callable[[], Any],
) -> Any:
    """按“设置 Y RF→温控关→等待→采集→温控开→等待”执行。"""
    check_cancelled()
    set_y_rf()
    set_temperature_switch(
        devices["temp_switch"],
        False,
        channel=channels["temp_switch"],
    )
    try:
        _sleep(params.temp_switch_off_lead_s)
        _sleep(settle_time_s)
        return acquire()
    finally:
        set_temperature_switch(
            devices["temp_switch"],
            True,
            channel=channels["temp_switch"],
        )
        _uncancellable_sleep(params.temp_switch_on_lag_s)


def _save_point(
    path: Path,
    payload: dict[str, np.ndarray],
    metadata: dict[str, Any],
    actual_rate: float,
) -> dict[str, float]:
    """保存一个频点的原始 R 记录和统计摘要。"""
    summary = summarize_r(payload)
    np.savez(
        path,
        time_s=payload["time_s"],
        r_v=payload["r"],
        actual_rate_sa_s=np.float64(actual_rate),
        **{key: value for key, value in metadata.items()},
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
    )
    return summary


def _acquire_valid_r_point(
    params: MxYRFFrequencyResponseParams,
    raw_dir: Path,
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
) -> tuple[dict[str, float], int, str]:
    """采集一个 R 点；标准差超限时完整保存并重新采集。"""
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        payload = _temperature_gated_acquire(
            params,
            devices,
            channels,
            set_y_rf=set_y_rf,
            settle_time_s=settle_time_s,
            acquire=lambda: acquire_r(
                hf2,
                device_id=device_id,
                demod_idx=params.demod_idx,
                actual_rate_sa_s=actual_rate,
                duration_s=duration_s,
            ),
        )
        summary = summarize_r(payload)
        accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
        filename = f"{file_stem}_attempt_{attempt:02d}.npz"
        _save_point(
            raw_dir / filename,
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
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return summary, attempt, filename
    raise RPointQualityError(
        f"{file_stem} 连续 {params.r_point_max_attempts} 次 "
        f"std(R) > {params.r_bad_point_std_threshold_v:.6g} V"
    )


def _acquire_frequency_response(
    params: MxYRFFrequencyResponseParams,
    raw_dir: Path,
    devices: dict[str, Any],
    channels: dict[str, int],
    actual_rate: float,
    device_id: str,
) -> None:
    """嵌套扫描幅度和频率，保存每点原始记录与汇总矩阵。"""
    amplitude_axis = build_rf_amplitude_axis(params)
    frequency_axis = build_frequency_axis(params)
    amplitude_count = int(amplitude_axis.size)
    frequency_count = int(frequency_axis.size)
    r_mean = np.full((amplitude_count, frequency_count), np.nan, dtype=float)
    r_std = np.full((amplitude_count, frequency_count), np.nan, dtype=float)
    accepted_attempt = np.full(
        (amplitude_count, frequency_count),
        -1,
        dtype=np.int64,
    )
    accepted_file = np.empty(
        (amplitude_count, frequency_count),
        dtype=object,
    )
    rf = devices["xy_field"]
    hf2 = devices["hf2"]
    for amplitude_index, amplitude in enumerate(amplitude_axis):
        for frequency_index, frequency in enumerate(frequency_axis):
            check_cancelled()
            print(
                f"幅度 {amplitude_index + 1}/{amplitude_count} "
                f"({amplitude:.6g} Vpp) "
                f"频率 {frequency_index + 1}/{frequency_count}: "
                f"{frequency:.3f} Hz"
            )
            summary, attempt, filename = _acquire_valid_r_point(
                params,
                raw_dir,
                devices,
                channels,
                file_stem=(
                    f"response_a{amplitude_index:03d}"
                    f"_f{frequency_index:04d}"
                ),
                metadata={
                    "amplitude_vpp": np.float64(amplitude),
                    "frequency_hz": np.float64(frequency),
                },
                set_y_rf=lambda amplitude=amplitude, frequency=frequency: (
                    _set_frequency_point(
                        params,
                        rf,
                        hf2,
                        channels["y_rf"],
                        float(frequency),
                        float(amplitude),
                    )
                ),
                settle_time_s=params.frequency_settle_time_s,
                duration_s=params.frequency_duration_s,
                actual_rate=actual_rate,
                device_id=device_id,
            )
            r_mean[amplitude_index, frequency_index] = summary[
                "r_scalar_mean_v"
            ]
            r_std[amplitude_index, frequency_index] = summary["r_std_v"]
            accepted_attempt[amplitude_index, frequency_index] = attempt
            accepted_file[amplitude_index, frequency_index] = filename
    np.savez(
        raw_dir / "frequency_response.npz",
        frequency_hz=frequency_axis,
        amplitude_vpp=amplitude_axis,
        r_mean_v=r_mean,
        r_scalar_mean_v=r_mean,
        r_std_v=r_std,
        accepted_attempt_index=accepted_attempt,
        accepted_file=accepted_file,
        r_std_threshold_v=np.float64(
            params.r_bad_point_std_threshold_v
        ),
        actual_rate_sa_s=np.float64(actual_rate),
    )


def run(params: MxYRFFrequencyResponseParams) -> Path:
    root = find_project_root()
    mapping = load_mapping(root)
    run_dir = create_run_directory(
        DATA_TYPE,
        params.run_tag,
        params.to_external(),
        schema_version=params.schema_version,
        project_root=root,
    )
    run_dir.update_config(
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        execution_mode=EXECUTION_MODE,
        geometry={
            "main_field": "Z",
            "pump": "Z",
            "probe": "X",
            "rf_field": "Y",
            "x_field_output": "OFF",
        },
        scan_mode=(
            "nested_scan"
            if params.rf_amplitude_points > 1
            else "point_by_point"
        ),
        amplitude_axis={
            "start_vpp": params.rf_amplitude_start_vpp,
            "stop_vpp": params.rf_amplitude_stop_vpp,
            "points": params.rf_amplitude_points,
        },
        acquisition_signals=["R"],
        r_point_quality={
            "criterion": "std(R) <= threshold",
            "std_threshold_v": params.r_bad_point_std_threshold_v,
            "max_attempts": params.r_point_max_attempts,
        },
    )
    session = DeviceSession()
    devices: dict[str, Any] = {}
    channels: dict[str, int] = {}
    completion_status = "failed"
    failure_reason: str | None = None
    try:
        check_cancelled()
        try:
            devices, channels = connect_mx_y_rf_devices(mapping, session)
        except Exception:
            session.cleanup_connection_failure()
            raise
        run_dir.update_config(
            device_snapshot=snapshot_mx_y_rf_state(devices, channels)
        )
        actual_response_rate, clock_sources = configure_mx_y_rf_outputs(
            params,
            devices,
            channels,
            mapping,
        )
        run_dir.update_config(clock_sources=clock_sources)
        device_id = str(mapping["lockin_r"]["device_id"])
        _acquire_frequency_response(
            params,
            run_dir.raw,
            devices,
            channels,
            actual_response_rate,
            device_id,
        )
        completion_status = "completed"
        run_dir.update_config(
            completion_status=completion_status,
            actual_rates={"response_sa_s": actual_response_rate},
            data_files=["raw/frequency_response.npz"],
        )
        print(f"Mx Y RF 频率响应采集完成: {run_dir.root}")
        return run_dir.root
    except Exception as exc:
        failure_reason = str(exc)
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status="failed",
                failure_reason=failure_reason,
            )
        raise
    finally:
        shutdown_report = safe_shutdown(devices, channels, params)
        if shutdown_report.errors:
            print("安全关闭警告: " + "；".join(shutdown_report.errors))
        if run_dir.config_path.exists():
            run_dir.update_config(
                completion_status=completion_status,
                failure_reason=failure_reason,
                safety_shutdown=shutdown_report.to_dict(),
                device_disconnect={
                    "tec_disconnected": not shutdown_report.disconnect_errors,
                    "other_devices_preserved": True,
                    "errors": list(shutdown_report.disconnect_errors),
                },
            )


def main() -> int:
    params = load_runtime_params(MxYRFFrequencyResponseParams)
    run(params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
