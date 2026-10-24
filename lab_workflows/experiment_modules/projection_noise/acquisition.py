"""可选 OFF→ON 采集、逐次温控门控、原子写盘与故障帧抢救。"""
from __future__ import annotations
from pathlib import Path
import time
from uuid import uuid4
import numpy as np
from sds_acquisition import save_to_npz
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import (
    DeferredAcquisitionWrites, acquisition_window, read_complete_scope_record,
    acquire_autoranged_waveform, set_temperature_switch,
)
from .preparation import _scope_settings, _sleep


def _atomic(path, writer):
    path = Path(path)
    temporary = path.with_name(f".pending_{path.stem}_{uuid4().hex}.npz")
    try:
        writer(temporary)
        temporary.replace(path)
    except BaseException as exc:
        if temporary.exists() and hasattr(exc, "add_note"):
            exc.add_note(f"未完成原子替换的文件保留在：{temporary}")
        raise


def _save_frame(path, payload, *, phase, frame_index, params, main_field_output_on, main_field_current_ma):
    result = payload["result"]
    metadata = dict(phase=phase, frame_index=int(frame_index), main_field_output_on=bool(main_field_output_on),
        main_field_current_ma=float(main_field_current_ma), requested_sample_rate_sa_s=float(params.scope_sample_rate_sa_s),
        actual_sample_rate_sa_s=float(payload["actual_rate_sa_s"]), requested_duration_s=float(params.scope_duration_s),
        actual_duration_s=float(payload["actual_duration_s"]), scale_used_v_div=float(payload["scale_used_v_div"]),
        offset_used_v=float(payload["offset_used_v"]), next_scale_v_div=float(payload["next_scale_v_div"]),
        next_offset_v=float(payload["next_offset_v"]), attempt_count=int(payload["attempt_count"]),
        attempt_scales_v_div=np.asarray(payload["attempt_scales_v_div"], dtype=float).tolist(),
        attempt_offsets_v=np.asarray(payload["attempt_offsets_v"], dtype=float).tolist(),
        temp_switch_during_acquisition="0 V DC + output ON", temp_switch_between_frames="5 V DC + output ON",
        timebase_scale=float(result.preamble_dict.get("horiz_interval", 0.0)) * max(1, len(result.raw_data)) / 10.0,
        horizontal_divisions=10)
    result.config_snapshot = metadata
    _atomic(path, lambda temporary: save_to_npz(str(temporary), [result], config_dict=metadata, save_mode="raw"))
    return str(path)


def _save_acquisition_index(run_dir, records):
    columns = {key: np.asarray([item[key] for item in records], dtype=dtype) for key, dtype in (
        ("phase", str), ("frame_index", int), ("main_field_output_on", bool), ("main_field_current_ma", float),
        ("actual_rate_sa_s", float), ("actual_duration_s", float), ("scale_used_v_div", float),
        ("offset_used_v", float), ("attempt_count", int))}
    columns["relative_file"] = np.asarray([item["file"] for item in records], dtype=str)
    _atomic(run_dir.raw / "scope_acquisition_index.npz", lambda temporary: np.savez(temporary, **columns))
    return "raw/scope_acquisition_index.npz"


def _save_recovered(path, result, *, phase, frame_index, reason):
    # 故障样本不叫 waveform，也不加入正式索引，不能冒充合格科学帧。
    _atomic(path, lambda temporary: np.savez(temporary, time_s=np.asarray(result.time),
        voltage_v=np.asarray(result.voltage), raw_data=np.asarray(result.raw_data),
        phase=np.asarray(phase), frame_index=np.int64(frame_index), accepted=np.bool_(False), reason=np.asarray(reason)))


def _temperature_gated_capture(params, devices, channels, scope_config, latest):
    def set_enabled(enabled):
        set_temperature_switch(devices["temp_switch"], enabled, channel=channels["temp_switch"],
            settle_time=params.temp_switch_on_settle_s if enabled else 0.0, cancellation=None)
    with acquisition_window(set_enabled, _sleep, params.system_settle_s):
        result = read_complete_scope_record(_scope_settings(params), devices, scope_config,
                                           check_cancelled=check_cancelled, sleep=_sleep)
        latest["result"] = result
        return result


def verify_scope(params, devices):
    scope = devices["scope"]
    if str(scope.get_memory_management()).strip().upper() != "FSRATE":
        raise ValueError("SDS 不再处于 FSRate，请重新执行 A06")
    if str(scope.get_trigger_mode()).strip().upper() != params.scope_trigger_mode:
        raise ValueError("SDS 触发模式已改变，请重新执行 A06")
    rate = float(scope.get_sampling_rate())
    if not np.isfinite(rate) or rate <= 2 * (params.target_larmor_frequency_hz + params.fit_half_width_hz):
        raise ValueError("SDS 实际采样率不足，请重新执行 A06")


def _acquire_phase(params, run_dir, devices, channels, scope_config, auto_range, *, phase,
                   main_field_output_on, main_field_current_ma, records, total, started):
    phase_dir = run_dir.raw / phase
    phase_dir.mkdir(exist_ok=False)
    for index in range(params.acq_repeats):
        check_cancelled()
        latest = {}
        relative = f"raw/{phase}/waveform_{index:04d}.npz"
        try:
            with DeferredAcquisitionWrites() as pending:
                payload = acquire_autoranged_waveform(_scope_settings(params), scope_config, devices["scope"], auto_range,
                    capture=lambda: _temperature_gated_capture(params, devices, channels, scope_config, latest),
                    check_cancelled=check_cancelled)
                pending.add(_save_frame, run_dir.root / relative, payload, phase=phase, frame_index=index,
                    params=params, main_field_output_on=main_field_output_on, main_field_current_ma=main_field_current_ma)
        except BaseException as exc:
            if latest:
                try:
                    _save_recovered(phase_dir / f"recovered_{index:04d}.npz", latest["result"],
                                    phase=phase, frame_index=index, reason=str(exc))
                except Exception as save_error:
                    if hasattr(exc, "add_note"):
                        exc.add_note(f"故障帧抢救另失败：{save_error}")
            raise
        records.append(dict(phase=phase, frame_index=index, file=relative,
            main_field_output_on=main_field_output_on, main_field_current_ma=main_field_current_ma,
            actual_rate_sa_s=float(payload["actual_rate_sa_s"]), actual_duration_s=float(payload["actual_duration_s"]),
            scale_used_v_div=float(payload["scale_used_v_div"]), offset_used_v=float(payload["offset_used_v"]),
            attempt_count=int(payload["attempt_count"])))
        _save_acquisition_index(run_dir, records)
        done = len(records)
        report_runtime_progress("A07", f"{phase} 帧 {index + 1}/{params.acq_repeats}", 100 * done / total,
            estimated_remaining_seconds=(time.monotonic() - started) / done * (total - done),
            data=dict(completed_frames=done, total_frames=total, phase=phase))


def acquire_scan(params, run_dir, devices, channels, scope_config, auto_range, records, target_current_ma):
    verify_scope(params, devices)
    total = params.acq_repeats * (2 if params.measure_field_off_control else 1)
    started = time.monotonic()
    report_runtime_progress("A07", "开始 OFF/ON 示波器采集", 0)
    gs = devices["gs200"]
    try:
        gs.set_output(False)
        if params.measure_field_off_control:
            _acquire_phase(params, run_dir, devices, channels, scope_config, auto_range, phase="field_off",
                main_field_output_on=False, main_field_current_ma=0.0, records=records, total=total, started=started)
        check_cancelled()
        validate_safety_limit("main_magnetic_field", target_current_ma)
        gs.set_output(False)
        gs.set_current(target_current_ma / 1000.0)
        gs.set_output(True)
        _sleep(params.main_field_settle_s)
        _acquire_phase(params, run_dir, devices, channels, scope_config, auto_range, phase="field_on",
            main_field_output_on=True, main_field_current_ma=target_current_ma, records=records, total=total, started=started)
    except BaseException as exc:
        try:
            _save_acquisition_index(run_dir, records)
        except Exception as save_error:
            if hasattr(exc, "add_note"):
                exc.add_note(f"采集索引抢救另失败：{save_error}")
        raise
    return ["raw/scope_acquisition_index.npz", *[item["file"] for item in records]]
