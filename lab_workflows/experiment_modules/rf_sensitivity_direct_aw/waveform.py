"""保留旧脚本的秒单位 CSV 和公共周期重采样规则。"""

from __future__ import annotations

import math

import numpy as np

from ...common import validate_safety_limit
from ...control_sources import resolve_control_results_root
from .models import RFSensitivityDirectAWParams


def load_envelope(params: RFSensitivityDirectAWParams, max_points: int) -> dict:
    if not params.arb_waveform_file:
        raise ValueError("请选择 oc_sens 中的最优控制波形版本")
    base = resolve_control_results_root("oc_sens").resolve()
    path = (base / params.arb_waveform_file).resolve()
    if base not in path.parents:
        raise ValueError("最优控制波形 CSV 必须位于 oc_sens 目录内")
    with path.open(encoding="utf-8-sig") as stream:
        header = stream.readline().lower()
    if "time_ms" in header:
        raise ValueError("控制包络时间列必须为 time_s，不能把 time_ms 当作秒")
    data = np.loadtxt(path, delimiter=",", skiprows=1)
    if data.ndim != 2 or data.shape[1] < 2 or len(data) < 2 or not np.all(np.isfinite(data[:, :2])):
        raise ValueError("控制包络需要至少两个有限采样点：time_s, Omega_ctrl_Hz")
    source_time, omega = data[:, 0], data[:, 1]
    intervals = np.diff(source_time)
    dt = float(np.median(intervals))
    if dt <= 0 or not np.allclose(intervals, dt, rtol=1e-6, atol=dt * 1e-6):
        raise ValueError("控制包络时间轴必须严格递增且等间隔")
    source_period = float(source_time[-1] - source_time[0] + dt)
    points = int(round(1 / params.aw_repeat_freq / dt))
    if not 2 <= points <= max_points:
        source_points = int(round(source_period / dt))
        source_freq = 1.0 / source_period
        max_cycles = max_points // source_points
        if max_cycles >= 1:
            hint = (
                f"该包络每个源周期 {source_points} 点，AW_REPEAT_FREQ 须为 "
                f"{source_freq:.6g}/N Hz 且 N≤{max_cycles}，"
                f"最低可选 {source_freq / max_cycles:.6g} Hz"
            )
        else:
            hint = "该包络单个源周期点数已超出上限，无可用重复频率，需更换更短包络"
        raise ValueError(f"AW 公共周期点数 {points} 超出当前信号源限制 2~{max_points}；{hint}")
    repeat_freq = 1 / (points * dt)
    if abs(repeat_freq - params.aw_repeat_freq) / params.aw_repeat_freq > 1e-6:
        raise ValueError("AW_REPEAT_FREQ 与 CSV dt 不能形成整数点周期")
    for name, cycles in (("控制包络", 1 / source_period / params.aw_repeat_freq), ("Pump/Larmor", params.pump_mod_freq / params.aw_repeat_freq)):
        # 下限同样使用容差：CSV 时间轴的浮点噪声可能让比值落在整数下方仅 1e-16 处，
        # 严格比较会误拒合法配置（如 30 MHz 栅格的 v9 @3000 Hz）。
        if cycles < 1 - 1e-6 or abs(cycles - round(cycles)) > 1e-6:
            raise ValueError(f"{name}频率必须是 AW_REPEAT_FREQ 的正整数倍")
    time_s = np.arange(points) * dt
    target = np.interp(time_s % source_period, np.r_[source_time - source_time[0], source_period], np.r_[omega, omega[0]])
    return {
        "source_waveform_path": str(path),
        "time_s": time_s, "omega_ctrl_Hz": target,
        "envelope_V": (target - params.xy_ctrl_b_hz) / params.xy_ctrl_k_hz_per_v,
        "source_time_s": source_time, "source_omega_ctrl_Hz": omega,
        "repeat_freq_Hz": float(repeat_freq), "source_repeat_freq_Hz": 1 / source_period,
    }


def synthesize_xy(params: RFSensitivityDirectAWParams, envelope: dict, phase_deg: float) -> dict:
    angle = 2 * np.pi * params.pump_mod_freq * envelope["time_s"] + math.radians(phase_deg)
    result = dict(envelope, xy_ctrl_phase_deg=float(phase_deg), xy_ctrl_quad_deg=params.xy_ctrl_quad)
    for axis, phase in (("x", 0.0), ("y", math.radians(params.xy_ctrl_quad))):
        wave = envelope["envelope_V"] * np.cos(angle + phase)
        normalized = (wave - params.xy_aw_output_offset) / (params.xy_aw_output_vpp / 2)
        if not np.all(np.isfinite(normalized)) or np.max(np.abs(normalized)) > 1 + 1e-12:
            raise ValueError("控制包络超出固定 XY AW 输出范围，不能静默裁剪")
        for value in (float(wave.min()), float(wave.max()), params.xy_aw_output_offset - params.xy_aw_output_vpp / 2, params.xy_aw_output_offset + params.xy_aw_output_vpp / 2):
            validate_safety_limit(f"{axis.upper()}_magnetic_field", value)
        result[f"{axis}_waveform_V"] = wave
        result[f"{axis}_normalized"] = normalized
        result[f"{axis}_vpp"] = params.xy_aw_output_vpp
        result[f"{axis}_offset"] = params.xy_aw_output_offset
    return result
