"""同向 XY 网格、逐次温控门控、自适应量程与故障帧抢救。"""
from __future__ import annotations
from pathlib import Path
import time
import numpy as np
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import DeferredAcquisitionWrites, acquisition_window, acquire_autoranged_waveform, read_complete_scope_record, set_temperature_switch
from .preparation import _scope_settings, _sleep, _set_main_field, _set_xy_scan_state
from .scan import build_xy_axes, iter_grid


def _save_capture(path, payload, *, phase, sequence_index, repeat_index, main_field_ma,
                  x_v, y_v, x_index=-1, y_index=-1):
    diagnostics = {name: value for name, value in payload.items() if name.startswith("attempt_")}
    np.savez_compressed(path,
        time_s=np.asarray(payload["time_s"], dtype=float), voltage_v=np.asarray(payload["voltage_v"], dtype=float),
        phase=np.array(phase), sequence_index=np.int64(sequence_index), repeat_index=np.int64(repeat_index),
        x_index=np.int64(x_index), y_index=np.int64(y_index), main_field_ma=np.float64(main_field_ma),
        x_field_v=np.float64(x_v), y_field_v=np.float64(y_v),
        x_output_on=np.bool_(phase == "grid"), y_output_on=np.bool_(phase == "grid"),
        capture_started_unix_s=np.float64(payload["capture_started_unix_s"]),
        capture_ended_unix_s=np.float64(payload["capture_ended_unix_s"]),
        capture_midpoint_unix_s=np.float64(payload["capture_midpoint_unix_s"]),
        pd_mean_v=np.float64(payload["pd_mean_v"]), pd_std_v=np.float64(payload["pd_std_v"]),
        actual_rate_sa_s=np.float64(payload["actual_rate_sa_s"]), actual_duration_s=np.float64(payload["actual_duration_s"]),
        scale_used_v_div=np.float64(payload["scale_used_v_div"]), offset_used_v=np.float64(payload["offset_used_v"]),
        next_scale_v_div=np.float64(payload["next_scale_v_div"]), next_offset_v=np.float64(payload["next_offset_v"]),
        **diagnostics)


def _save_recovered(path, time_s, voltage_v, metadata):
    """故障样本与已接受 grid 文件隔离，不冒充有效拟合输入。"""
    np.savez_compressed(path, time_s=time_s, voltage_v=voltage_v, **metadata)


def capture_point(params, run_dir, devices, channels, scope_config, auto_range, metadata):
    settings = _scope_settings(params)
    accepted, attempt = False, 0
    started = time.time()

    def rescue(path, time_s, voltage_v, details):
        if not accepted:
            _save_recovered(path, time_s, voltage_v, details)

    # 仅缓存当前点有限次量程尝试；先恢复温控，再在异常退出时抢救。
    with DeferredAcquisitionWrites() as pending:
        def set_enabled(enabled):
            set_temperature_switch(devices["temp_switch"], enabled, channel=channels["temp_switch"],
                settle_time=params.temp_switch_on_settle_s if enabled else 0.0, cancellation=None)

        def capture():
            nonlocal attempt
            attempt += 1
            with acquisition_window(set_enabled, _sleep, params.system_settle_s):
                result = read_complete_scope_record(settings, devices, scope_config,
                    check_cancelled=check_cancelled, sleep=_sleep)
                pending.add(rescue, run_dir.raw / f"recovered_X{metadata['x_index']:03d}_Y{metadata['y_index']:03d}_R{metadata['repeat_index']:03d}_attempt{attempt:02d}.npz",
                    np.asarray(result.time), np.asarray(result.voltage),
                    dict(metadata, attempt=attempt, accepted=False, scale_used_v_div=auto_range.scale_v_div,
                         offset_used_v=auto_range.offset_v))
            return result

        payload = acquire_autoranged_waveform(settings, scope_config, devices["scope"], auto_range,
            capture=capture, check_cancelled=check_cancelled)
        ended = time.time()
        voltage = np.asarray(payload["voltage_v"], dtype=float)
        payload.update(capture_started_unix_s=started, capture_ended_unix_s=ended,
            capture_midpoint_unix_s=0.5 * (started + ended),
            pd_mean_v=float(np.mean(voltage)), pd_std_v=float(np.std(voltage)))
        relative = f"raw/grid_X{metadata['x_index']:03d}_Y{metadata['y_index']:03d}_R{metadata['repeat_index']:03d}.npz"
        # 此时所有窗口均已恢复温控；正式写入失败仍由 pending 抢救已读样本。
        _save_capture(run_dir.root / relative, payload, phase="grid", **metadata)
        accepted = True
    return relative


def acquire_scan(params, run_dir, devices, channels, scope_config, auto_range):
    x_axis, y_axis = build_xy_axes(params)
    axes_relative = "raw/xy_residual_scan_axes.npz"
    np.savez(run_dir.root / axes_relative, x_field_v=x_axis, y_field_v=y_axis,
        order=np.array("x_outer_y_forward"), scan_main_field_ma=np.float64(params.scan_main_field_ma))
    files = [axes_relative]
    _set_main_field(devices["gs200"], params.scan_main_field_ma)
    total = params.grid_points * params.point_repeats
    started = time.monotonic()
    report_runtime_progress("A08", "开始 XY 同向网格采集", 0, data={"estimated_remaining_seconds": None})
    for point_index, (x_index, y_index, x_v, y_v) in enumerate(iter_grid(params)):
        for repeat in range(params.point_repeats):
            check_cancelled()
            _set_xy_scan_state(devices, channels, x_v, y_v)
            sequence_index = point_index * params.point_repeats + repeat
            files.append(capture_point(params, run_dir, devices, channels, scope_config, auto_range,
                dict(sequence_index=sequence_index, repeat_index=repeat, main_field_ma=params.scan_main_field_ma,
                     x_v=x_v, y_v=y_v, x_index=x_index, y_index=y_index)))
            completed = sequence_index + 1
            report_runtime_progress("A08", f"XY 网格 {point_index + 1}/{params.grid_points}，重复 {repeat + 1}/{params.point_repeats} 已恢复温控并保存",
                100 * completed / total, data={"estimated_remaining_seconds":
                    (time.monotonic() - started) / completed * (total - completed)})
    return files
