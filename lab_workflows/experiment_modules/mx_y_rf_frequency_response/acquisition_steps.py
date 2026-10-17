"""嵌套 RF 幅度-频率扫描的采集、质量重采和恢复后写盘。

工作点准备、激励与 R 读取复用 mx_y_rf_sensitivity 的职责模块；本模块只
负责频率响应扫描本身：每个 (幅度, 频率) 点设置 Y RF → 关温控 →
SYSTEM_SETTLE_S → 采集 R 到内存 → 恢复温控 → 真实写盘，恢复失败时同样
抢救已读数据并保留原始异常。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
from lockin_amplifier import OscillatorConfig, demod

from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from ...steps.acquisition_window import DeferredAcquisitionWrites, acquisition_window
from ..mx_y_rf_sensitivity.acquisition import acquire_r, summarize_r
from ..mx_y_rf_sensitivity.workflow import RPointQualityError
from .models import MxYRFFrequencyResponseParams


def _sleep(seconds: float) -> None:
    """可取消等待；由测试注入检查点时覆盖。"""
    import time

    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _uncancellable_sleep(seconds: float) -> None:
    import time

    time.sleep(max(0.0, float(seconds)))


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


def _set_frequency_point(
    params: MxYRFFrequencyResponseParams,
    rf: Any,
    hf2: Any,
    channel: int,
    frequency_hz: float,
    amplitude_vpp: float,
) -> None:
    """设置 Y RF 通道频率、相位和幅度，并让锁相振荡器跟随频率。"""
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
    switch=None,
    wait=None,
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
        actual_rate_sa_s=payload.get("actual_rate_Sa_s", np.float64(actual_rate)),
        **{key: value for key, value in metadata.items()},
        **{
            key: np.float64(value) if isinstance(value, float) else value
            for key, value in summary.items()
        },
        **{key: value for key, value in payload.items() if key not in {"time_s", "r", "x", "y"}},
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
    reader=None,
    switch=None,
    wait=None,
    gated_acquire=None,
) -> tuple[dict[str, float], int, str]:
    """采集一个 R 点；标准差超限时完整保存并重新采集。"""
    reader = reader or acquire_r
    gated_acquire = gated_acquire or _temperature_gated_acquire
    hf2 = devices["hf2"]
    for attempt in range(params.r_point_max_attempts):
        with DeferredAcquisitionWrites() as pending:

            def acquire_and_buffer() -> dict[str, np.ndarray]:
                payload = reader(
                    hf2,
                    device_id=device_id,
                    demod_idx=params.demod_idx,
                    actual_rate_sa_s=actual_rate,
                    requested_rate_sa_s=params.response_rate_sa_s,
                    duration_s=duration_s,
                )
                summary = summarize_r(payload)
                accepted = (
                    summary["r_std_v"] <= params.r_bad_point_std_threshold_v
                )
                pending.add(
                    _save_point,
                    raw_dir / f"{file_stem}_attempt_{attempt:02d}.npz",
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
                acquire=acquire_and_buffer,
                switch=switch,
                wait=wait,
            )
        summary = summarize_r(payload)
        accepted = summary["r_std_v"] <= params.r_bad_point_std_threshold_v
        print(
            f"  R 质量 {attempt + 1}/{params.r_point_max_attempts}: "
            f"std={summary['r_std_v']:.6g} V, "
            f"{'接受' if accepted else '拒绝并重采'}"
        )
        if accepted:
            return (
                {
                    **summary,
                    "actual_rate_sa_s": float(
                        payload.get("actual_rate_Sa_s", actual_rate)
                    ),
                },
                attempt,
                f"{file_stem}_attempt_{attempt:02d}.npz",
            )
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
    *,
    reader=None,
    switch=None,
    wait=None,
    gated_acquire=None,
    amplitude_axis=None,
    frequency_axis=None,
    point_acquirer=None,
) -> float:
    """嵌套扫描幅度和频率，保存每点原始记录与汇总矩阵。"""
    amplitude_axis = build_rf_amplitude_axis(params) if amplitude_axis is None else amplitude_axis
    frequency_axis = build_frequency_axis(params) if frequency_axis is None else frequency_axis
    point_acquirer = point_acquirer or _acquire_valid_r_point
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
    last_actual_rate = actual_rate
    for amplitude_index, amplitude in enumerate(amplitude_axis):
        for frequency_index, frequency in enumerate(frequency_axis):
            check_cancelled()
            print(
                f"幅度 {amplitude_index + 1}/{amplitude_count} "
                f"({amplitude:.6g} Vpp) "
                f"频率 {frequency_index + 1}/{frequency_count}: "
                f"{frequency:.3f} Hz"
            )
            summary, attempt, filename = point_acquirer(
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
                settle_time_s=params.system_settle_s,
                duration_s=params.frequency_duration_s,
                actual_rate=last_actual_rate,
                device_id=device_id,
                reader=reader,
                switch=switch,
                wait=wait,
                gated_acquire=gated_acquire,
            )
            r_mean[amplitude_index, frequency_index] = summary["r_scalar_mean_v"]
            r_std[amplitude_index, frequency_index] = summary["r_std_v"]
            accepted_attempt[amplitude_index, frequency_index] = attempt
            accepted_file[amplitude_index, frequency_index] = filename
            last_actual_rate = summary.get("actual_rate_sa_s", last_actual_rate)
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
        actual_rate_sa_s=np.float64(last_actual_rate),
    )
    return float(last_actual_rate)
