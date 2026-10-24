"""主场扫描、逐次温控门控、量程继承和故障原始帧抢救。"""
from __future__ import annotations
from pathlib import Path
import time
from typing import Any, Callable
import numpy as np
from sds_acquisition import AcquisitionConfig, SDSInstrument
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import ScopeAutoRangeState, DeferredAcquisitionWrites, acquisition_window, read_complete_scope_record, set_temperature_switch
from .models import MxMainFieldScopeNoiseSpectrumParams
from .scan import build_scan_axes
from .preparation import _scope_settings, _sleep, _validate_main_field_current, _next_auto_range_scale, _next_auto_offset


class ScopeQualityError(ValueError):
    """SDS 波形或实际回读不符合科学采集资格。"""


def _read_scope_record(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    scope_config: AcquisitionConfig,
) -> Any:
    """等待示波器提交完整帧后读取，必要时自动恢复波形导出状态。"""
    return read_complete_scope_record(
        _scope_settings(params),
        devices,
        scope_config,
        check_cancelled=check_cancelled,
        sleep=_sleep,
    )


def _acquire_scope_point(
    params: MxMainFieldScopeNoiseSpectrumParams,
    devices: dict[str, Any],
    channels: dict[str, int],
    scope_config: AcquisitionConfig,
    auto_range: ScopeAutoRangeState,
    *, capture: Callable[[], Any],
) -> dict[str, Any]:
    """联合调整量程和偏置，返回最终接受的 PD 波形。"""
    scope: SDSInstrument = devices["scope"]
    attempt_scales: list[float] = []
    attempt_offsets: list[float] = []
    attempt_abs_max: list[float] = []
    attempt_min: list[float] = []
    attempt_max: list[float] = []
    attempt_center: list[float] = []
    attempt_peak_deviation: list[float] = []
    attempt_display_edge_fraction: list[float] = []
    result: Any | None = None
    accepted_scale = float(auto_range.scale_v_div)
    accepted_offset = float(auto_range.offset_v)

    for attempt in range(params.scope_auto_range_max_attempts):
        check_cancelled()
        scale_used = float(auto_range.scale_v_div)
        offset_used = float(auto_range.offset_v)
        attempt_scales.append(scale_used)
        attempt_offsets.append(offset_used)
        result = capture()
        voltage = np.asarray(result.voltage, dtype=float).reshape(-1)
        if voltage.size < params.welch_nperseg:
            raise ScopeQualityError(
                f"示波器有效样本数 {voltage.size} 少于 WELCH_NPERSEG={params.welch_nperseg}"
            )
        if not np.all(np.isfinite(voltage)):
            raise ScopeQualityError("示波器 PD 波形包含非有限值")
        waveform_min_v = float(np.min(voltage))
        waveform_max_v = float(np.max(voltage))
        waveform_center_v = 0.5 * (waveform_min_v + waveform_max_v)
        peak_deviation_v = 0.5 * (waveform_max_v - waveform_min_v)
        display_center_v = -offset_used
        peak_from_display_center_v = max(
            abs(waveform_min_v - display_center_v),
            abs(waveform_max_v - display_center_v),
        )
        half_span_v = (
            scale_used * params.scope_vertical_divisions / 2.0
        )
        abs_max_v = float(np.max(np.abs(voltage)))
        attempt_abs_max.append(abs_max_v)
        attempt_min.append(waveform_min_v)
        attempt_max.append(waveform_max_v)
        attempt_center.append(waveform_center_v)
        attempt_peak_deviation.append(peak_deviation_v)
        attempt_display_edge_fraction.append(
            peak_from_display_center_v / half_span_v
        )
        next_scale = _next_auto_range_scale(
            params, scale_used, peak_deviation_v
        )
        next_offset = _next_auto_offset(
            params,
            scale_used,
            offset_used,
            waveform_min_v,
            waveform_max_v,
        )
        # 波形靠近当前显示边界时可能已经截顶；先平移到中心，不能依据截顶后
        # 偏小的峰峰值继续缩小量程。
        if (
            next_offset != offset_used
            and next_scale < scale_used
            and peak_from_display_center_v
            >= params.scope_auto_range_high_fraction * half_span_v
        ):
            next_scale = scale_used
        accepted_scale = scale_used
        accepted_offset = offset_used
        scale_changed = next_scale != scale_used
        offset_changed = next_offset != offset_used
        if not scale_changed and not offset_changed:
            break

        if scale_changed:
            scope.set_channel_scale(params.scope_pd_channel, next_scale)
            actual_scale_v_div = float(
                scope.get_channel_scale(params.scope_pd_channel)
            )
            if not np.isfinite(actual_scale_v_div) or actual_scale_v_div <= 0.0:
                raise ScopeQualityError("示波器回读的通道量程无效")
        else:
            actual_scale_v_div = scale_used
        auto_range.scale_v_div = actual_scale_v_div
        # 改变量程后再次显式写入 offset，避免示波器垂直参考策略改变其数值。
        scope.set_channel_offset(params.scope_pd_channel, next_offset)
        actual_offset_v = float(scope.get_channel_offset(params.scope_pd_channel))
        if not np.isfinite(actual_offset_v):
            raise ScopeQualityError("示波器回读的通道偏置不是有限值")
        auto_range.offset_v = actual_offset_v
        for channel_config in scope_config.channels:
            if channel_config.number == params.scope_pd_channel:
                channel_config.scale = actual_scale_v_div
                channel_config.offset = actual_offset_v
                break
        if attempt + 1 < params.scope_auto_range_max_attempts:
            result = None
            continue
        break

    if result is None:
        raise ScopeQualityError("示波器自动量程未获得有效波形")
    time_s = np.asarray(result.time, dtype=float).reshape(-1)
    voltage_v = np.asarray(result.voltage, dtype=float).reshape(-1)
    if time_s.size != voltage_v.size or time_s.size < 2:
        raise ScopeQualityError("示波器时间轴与 PD 波形长度不一致")
    sample_interval_s = float(np.median(np.diff(time_s)))
    if not np.isfinite(sample_interval_s) or sample_interval_s <= 0.0:
        raise ScopeQualityError("示波器真实采样间隔无效")
    actual_rate_sa_s = 1.0 / sample_interval_s
    if actual_rate_sa_s <= 2.0 * params.control_frequency_stop_hz:
        raise ScopeQualityError(
            "示波器波形真实采样率不足："
            f"{actual_rate_sa_s:.9g} Sa/s 的 Nyquist 不能覆盖 "
            f"{params.control_frequency_stop_hz:.9g} Hz"
        )
    return {
        "time_s": time_s,
        "voltage_v": voltage_v,
        "actual_rate_sa_s": actual_rate_sa_s,
        "actual_duration_s": sample_interval_s * max(0, time_s.size - 1),
        "scale_used_v_div": accepted_scale,
        "offset_used_v": accepted_offset,
        "next_scale_v_div": float(auto_range.scale_v_div),
        "next_offset_v": float(auto_range.offset_v),
        "attempt_count": len(attempt_scales),
        "attempt_scales_v_div": np.asarray(attempt_scales, dtype=float),
        "attempt_offsets_v": np.asarray(attempt_offsets, dtype=float),
        "attempt_abs_max_v": np.asarray(attempt_abs_max, dtype=float),
        "attempt_min_v": np.asarray(attempt_min, dtype=float),
        "attempt_max_v": np.asarray(attempt_max, dtype=float),
        "attempt_center_v": np.asarray(attempt_center, dtype=float),
        "attempt_peak_deviation_v": np.asarray(
            attempt_peak_deviation, dtype=float
        ),
        "attempt_display_edge_fraction": np.asarray(
            attempt_display_edge_fraction, dtype=float
        ),
    }


def _save_point(
    path: Path,
    payload: dict[str, Any],
    *,
    point_index: int,
    control_frequency_hz: float,
    main_field_current_ma: float,
    params: MxMainFieldScopeNoiseSpectrumParams,
) -> None:
    np.savez(
        path,
        time_s=np.asarray(payload["time_s"], dtype=float),
        voltage_v=np.asarray(payload["voltage_v"], dtype=float),
        point_index=np.int64(point_index),
        control_frequency_hz=np.float64(control_frequency_hz),
        larmor_frequency_hz=np.float64(control_frequency_hz),
        main_field_current_ma=np.float64(main_field_current_ma),
        requested_rate_sa_s=np.float64(params.scope_sample_rate_sa_s),
        actual_rate_sa_s=np.float64(payload["actual_rate_sa_s"]),
        requested_duration_s=np.float64(params.scope_duration_s),
        actual_duration_s=np.float64(payload["actual_duration_s"]),
        scale_used_v_div=np.float64(payload["scale_used_v_div"]),
        offset_used_v=np.float64(payload["offset_used_v"]),
        next_scale_v_div=np.float64(payload["next_scale_v_div"]),
        next_offset_v=np.float64(payload["next_offset_v"]),
        attempt_count=np.int64(payload["attempt_count"]),
        attempt_scales_v_div=np.asarray(
            payload["attempt_scales_v_div"], dtype=float
        ),
        attempt_offsets_v=np.asarray(payload["attempt_offsets_v"], dtype=float),
        attempt_abs_max_v=np.asarray(payload["attempt_abs_max_v"], dtype=float),
        attempt_min_v=np.asarray(payload["attempt_min_v"], dtype=float),
        attempt_max_v=np.asarray(payload["attempt_max_v"], dtype=float),
        attempt_center_v=np.asarray(payload["attempt_center_v"], dtype=float),
        attempt_peak_deviation_v=np.asarray(
            payload["attempt_peak_deviation_v"], dtype=float
        ),
        attempt_display_edge_fraction=np.asarray(
            payload["attempt_display_edge_fraction"], dtype=float
        ),
        temp_switch_off_settle_s=np.float64(params.system_settle_s),
        temp_switch_on_settle_s=np.float64(params.temp_switch_on_settle_s),
    )


def _save_recovered(path, time_s, voltage_v, metadata):
    """故障帧与正式 waveform 文件隔离，不能作为已接受的科学数据。"""
    np.savez(path, time_s=time_s, voltage_v=voltage_v, **metadata)


def _acquire_scan(params, run_dir, devices, channels, scope_config, auto_range):
    axes = build_scan_axes(params)
    control_axis = axes["control_frequency_hz"]
    current_axis = axes["main_field_current_ma"]
    axes_path = run_dir.raw / "main_field_scope_noise_scan_axes.npz"
    np.savez(
        axes_path,
        control_frequency_hz=control_axis,
        larmor_frequency_hz=axes["larmor_frequency_hz"],
        main_field_current_ma=current_axis,
        main_field_calibration_hz_per_ma=np.float64(
            params.main_field_calibration_hz_per_ma
        ),
        main_field_calibration_intercept_hz=np.float64(
            params.main_field_calibration_intercept_hz
        ),
        calibration_extrapolation_enabled=np.bool_(True),
    )

    files = ["raw/main_field_scope_noise_scan_axes.npz"]
    report_runtime_progress("A08", "开始主场示波器扫描", 0,
        data={"estimated_remaining_seconds": None})
    started = time.monotonic()
    for index, (control_hz, current_ma) in enumerate(zip(control_axis, current_axis, strict=True)):
        check_cancelled()
        _validate_main_field_current(float(current_ma))
        devices["gs200"].set_current(float(current_ma) / 1000.0)
        devices["gs200"].set_output(True)
        accepted = False
        attempt = 0

        def rescue(path, time_s, voltage_v, metadata):
            if not accepted:
                _save_recovered(path, time_s, voltage_v, metadata)

        # 仅冻结当前点的有限次量程尝试，不深拷贝设备或整个会话。
        with DeferredAcquisitionWrites() as pending:
            def set_enabled(enabled):
                set_temperature_switch(devices["temp_switch"], enabled,
                    channel=channels["temp_switch"],
                    settle_time=params.temp_switch_on_settle_s if enabled else 0.0,
                    cancellation=None)

            def capture():
                nonlocal attempt
                attempt += 1
                with acquisition_window(set_enabled, _sleep, params.system_settle_s):
                    result = _read_scope_record(params, devices, scope_config)
                    pending.add(rescue, run_dir.raw / f"recovered_I{index:04d}_attempt{attempt:02d}.npz",
                        np.asarray(result.time), np.asarray(result.voltage),
                        dict(point_index=index, control_frequency_hz=float(control_hz),
                             main_field_current_ma=float(current_ma), attempt=attempt,
                             scale_used_v_div=auto_range.scale_v_div, offset_used_v=auto_range.offset_v,
                             accepted=False))
                return result

            payload = _acquire_scope_point(params, devices, channels, scope_config, auto_range, capture=capture)
            relative = f"raw/waveform_I{index:04d}.npz"
            pending.add(_save_point, run_dir.root / relative, payload, point_index=index,
                control_frequency_hz=float(control_hz), main_field_current_ma=float(current_ma), params=params)
            accepted = True
        files.append(relative)
        elapsed = time.monotonic() - started
        report_runtime_progress("A08", f"主场示波器点 {index + 1}/{len(control_axis)} 已恢复温控并保存",
            100.0 * (index + 1) / len(control_axis),
            data={"estimated_remaining_seconds": elapsed / (index + 1) * (len(control_axis) - index - 1)})
    return files
