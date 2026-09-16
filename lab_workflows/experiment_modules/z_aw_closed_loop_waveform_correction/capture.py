"""闭环单帧采集与由理论电压初始化的量程管理。"""

from pathlib import Path

import numpy as np

from ...experiment_runtime import check_cancelled
from ...current_feedback import validate_current_power
from ..z_aw_waveform_scope_check.workflow import (
    _wait_for_scope_trigger, _wait_for_scope_stop, _scope_record_duration_s,
    _sleep_cancellable, SCOPE_CAPTURE_GUARD_S, SCOPE_STOP_TIMEOUT_S,
)
from .static_feedback import scope_range


def capture_frame(devices, config, params) -> dict:
    """读取同一次停止状态的 CH3/CH4，并复用带重试的 SDS 采集实现。"""
    from ..z_aw_waveform_scope_check.workflow import _capture_frame
    frame = _capture_frame(devices, config, params)
    # 通用采集器返回原始波形；闭环量程校验还需要保存采集瞬间的 CH3 档位。
    scope = devices["scope"]
    frame["scale_used_v_div"] = float(scope.get_channel_scale(3))
    frame["offset_used_v"] = float(scope.get_channel_offset(3))
    return frame


def validate_frame(frame: dict, params, *, check_range: bool = True) -> None:
    """只在原始帧入口检查时间、采样和本帧量程，不重复检查派生数组。"""
    for time_key, voltage_key in (("time_s", "measured_voltage_v"),
                                  ("trigger_time_s", "trigger_voltage_v")):
        time, voltage = frame[time_key], frame[voltage_key]
        if time.size < 2 or time.shape != voltage.shape:
            raise ValueError("SDS 时间轴与电压数组长度不一致或采样不足")
        if not np.all(np.isfinite(time)) or not np.all(np.isfinite(voltage)):
            raise ValueError("SDS 采样包含非有限值")
        steps = np.diff(time)
        if np.any(steps <= 0) or not np.allclose(steps, np.median(steps), rtol=1e-5, atol=1e-15):
            raise ValueError("SDS 时间轴必须递增且等间隔")
    actual_rate = 1 / float(np.median(np.diff(frame["time_s"])))
    if params.error_cutoff_hz > actual_rate / 2:
        raise ValueError("最高学习频率超过示波器实际采样奈奎斯特频率")
    frame["actual_rate_sa_s"] = actual_rate
    scale, offset = frame["scale_used_v_div"], frame["offset_used_v"]
    if not np.isfinite(scale) or scale <= 0 or not np.isfinite(offset):
        raise ValueError("SDS 量程回读无效")
    half = scale * params.scope_vertical_divisions / 2
    voltage = frame["measured_voltage_v"]
    if check_range and (voltage.min() < -offset - half or voltage.max() > -offset + half):
        raise ValueError("SDS CH3 电压超出本帧采集量程")


def capture_with_headroom(devices, config, params, path: Path, *, sense_resistor_ohm=None) -> dict:
    """实测偏离初始理论量程时放大并重采；每个尝试先保存原始帧。"""
    scope = devices["scope"]
    for attempt in range(3):
        check_cancelled()
        frame = capture_frame(devices, config, params)
        np.savez(path.with_name(f"{path.stem}_attempt_{attempt:02d}.npz"), **frame)
        validate_frame(frame, params, check_range=False)
        if sense_resistor_ohm is not None:
            validate_current_power(frame["measured_voltage_v"] / sense_resistor_ohm,
                                   sense_resistor_ohm, params.sense_resistor_power_rating_w,
                                   derating_fraction=params.sense_resistor_power_derating,
                                   maximum_current_a=params.maximum_current_a)
        scale, offset = scope_range(frame["measured_voltage_v"], params.scope_vertical_divisions,
                                    params.scope_headroom_factor)
        current_scale = frame["scale_used_v_div"]
        half = current_scale * params.scope_vertical_divisions / 2
        edge = float(np.max(np.abs(frame["measured_voltage_v"] + frame["offset_used_v"])))
        if scale <= current_scale and edge <= half / params.scope_headroom_factor:
            np.savez(path, **frame)
            return frame
        if attempt < 2:
            scope.set_channel_scale(3, max(scale, current_scale))
            scope.set_channel_offset(3, offset)
    raise RuntimeError("SDS 调整量程三次后仍未获得带余量的完整帧")
