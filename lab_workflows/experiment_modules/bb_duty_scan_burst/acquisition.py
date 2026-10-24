"""逐帧温控窗口、每点探测锁档、重复平均与故障数据保护。"""
import time
import numpy as np
from ...common import load_safety_limits, validate_safety_limit
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import acquisition_window, DeferredAcquisitionWrites
from . import preparation, excitation


class AcquisitionQualityError(ValueError):
    """真实采集不满足波形或触发资格，禁止当作有效平均。"""


def auto_scale_signal(signal, scale, p):
    peak = float(np.max(np.abs(signal)))
    full = scale * p.vert_divs
    if peak < 0.4 * full and scale > p.scale_min * 2:
        return max(p.scale_min, scale / 2)
    if peak > 0.9 * full and scale < p.scale_max / 2:
        return min(p.scale_max, scale * 2)
    return scale


def validate_record(time_s, signal, p):
    if time_s.ndim != 1 or signal.ndim != 1 or len(time_s) != len(signal) or len(time_s) < 2:
        raise AcquisitionQualityError("SDS 时间轴与 PD 波形必须是一维等长有效数组")
    dt = np.diff(time_s)
    if not np.all(np.isfinite(time_s)) or not np.all(np.isfinite(signal)) or np.any(dt <= 0):
        raise AcquisitionQualityError("SDS 波形包含非有限值或非递增时间轴")
    interval = float(np.median(dt))
    if not np.allclose(dt, interval, rtol=1e-4, atol=1e-12) or 1 / interval <= 2 * p.modulation_frequency_hz:
        raise AcquisitionQualityError("SDS 实际时间网格不均匀或 Nyquist 无法覆盖 Pump 频率")
    if time_s[0] > -p.pre_baseline_time_s + 2 * interval or time_s[-1] < p.burst_duration_s + p.post_dark_time_s - 2 * interval:
        raise AcquisitionQualityError("SDS 实际波形未覆盖请求的基线/Burst/暗弛豫窗口")
    return 1 / interval


def capture(ctx, duty, relative, *, rep_index=-1):
    p, sg, scope, cfg = ctx.params, ctx.devices["sg"], ctx.devices["scope"], ctx.scope_config
    check_cancelled()
    gate = ctx.channels["Time_sequence"]
    validate_safety_limit("PUMP_MOD_DUTY", duty * 100, load_safety_limits(ctx.root))
    sg.set_burst_state(False, channel=gate)
    sg.set_pulse_dcycle(duty * 100, channel=gate)
    sg.set_burst_state(True, channel=gate)
    # re-arm 和档位回读在温控开启时完成，不向关闭窗口塞入准备或写盘。
    scale = float(scope.get_channel_scale(p.sds_pd_channel))
    if not np.isfinite(scale) or scale <= 0:
        raise AcquisitionQualityError("SDS 实际 PD 档位无效，请重新执行 A06")
    scope.trigger_run()
    preparation.sleep(0.05)
    restored, accepted = False, False

    def set_enabled(enabled):
        nonlocal restored
        preparation.set_temp(ctx, enabled)
        restored = enabled

    def save_frame(payload):
        if accepted:
            payload["sample_rate_actual_Hz"] = 1 / float(np.median(np.diff(payload["time_s"])))
        np.savez(ctx.directory.root / relative, **payload, accepted=accepted, temperature_restored=restored)
        ctx.state["data_files"].append(relative)

    with DeferredAcquisitionWrites() as pending:
        with acquisition_window(set_enabled, preparation.sleep, p.system_settle_s):
            preparation.sleep(p.burst_burst_period_s + 0.05)
            deadline = time.monotonic() + p.burst_burst_period_s + 5
            while True:
                check_cancelled()
                if scope.wait_for_trigger(timeout=0.1):
                    break
                if time.monotonic() >= deadline:
                    raise AcquisitionQualityError("SDS 等待 SYNC 触发超时，不读取可能过期的波形")
            preparation.sleep(cfg.acquire_delay)
            scope.trigger_stop()
            preparation.sleep(0.02)
            record = ctx.devices["acquirer"].acquire_channel(p.sds_pd_channel, cfg.timebase_scale,
                cfg.horizontal_divisions, trim_points=ctx.state["scope_actual"]["points_actual"])
            payload = dict(time_s=np.asarray(record.time), signal=np.asarray(record.voltage), duty_cycle=duty,
                rep_index=rep_index, scale_used=scale, t_burst_start_s=0.0, t_burst_end_s=p.burst_duration_s,
                sample_rate_actual_Hz=ctx.state["scope_actual"]["sample_rate_actual_Hz"], points_actual=len(record.time))
            # 先冻结已读帧；恢复、取消或资格检查失败时仍由外层尽力保存。
            pending.add(save_frame, payload)
        check_cancelled()
        rate = validate_record(payload["time_s"], payload["signal"], p)
        payload["sample_rate_actual_Hz"] = rate
        accepted = True
    return payload


def _save_average(ctx, index, duty, shots):
    p = ctx.params
    reference = shots[0]["time_s"]
    if any(len(s["time_s"]) != len(reference) or not np.allclose(s["time_s"], reference, rtol=0, atol=1e-9) for s in shots):
        raise AcquisitionQualityError("重复帧时间轴不一致，不进行错位元素平均")
    if any(s["scale_used"] != shots[0]["scale_used"] for s in shots):
        raise AcquisitionQualityError("重复帧档位发生变化，请重新准备 SDS 后采集")
    relative = f"raw/shot_d{index:02d}_avg.npz"
    actual = dict(ctx.state["scope_actual"], points_actual=len(reference),
        sample_rate_actual_Hz=shots[0]["sample_rate_actual_Hz"],
        capture_duration_actual_s=len(reference) / shots[0]["sample_rate_actual_Hz"])
    np.savez(ctx.directory.root / relative, time_s=reference, signal=np.mean([s["signal"] for s in shots], axis=0),
        duty_cycle=duty, pump_power_voltage=p.fixed_pump_power_voltage, modulation_frequency_hz=p.modulation_frequency_hz,
        burst_cycles=p.burst_cycles, burst_duration_s=p.burst_duration_s, pre_baseline_time_s=p.pre_baseline_time_s,
        post_dark_time_s=p.post_dark_time_s, z_field_setpoint=p.z_field_setpoint, acq_repeats=len(shots),
        sample_rate_requested_Hz=p.sds_sample_rate, **actual, t_burst_start_s=0.0, t_burst_end_s=p.burst_duration_s,
        scale_used=shots[-1]["scale_used"], rf_gate_high_v=p.pump_rf_gate_high_v, rf_gate_low_v=p.pump_rf_gate_low_v)
    ctx.state["data_files"].append(relative)
    return relative


def acquire_scan(ctx):
    p, scope = ctx.params, ctx.devices["scope"]
    excitation.verify_ready(ctx)
    completed = []
    failure = None
    started = time.monotonic()
    report_runtime_progress("A07", "开始占空比扫描", 0, data={"estimated_remaining_seconds": None})
    try:
        for index, duty in enumerate(p.duty_cycle_list):
            check_cancelled()
            # 每点最多三次探测；记录实际采集档位，不把下一档写成 scale_used。
            for attempt in range(3):
                probe = capture(ctx, duty, f"raw/probe_d{index:02d}_a{attempt}.npz")
                scale = probe["scale_used"]
                next_scale = auto_scale_signal(probe["signal"], scale, p)
                if next_scale == scale or attempt == 2:
                    break
                scope.set_channel_scale(p.sds_pd_channel, next_scale)
            # 最后一次探测就是 r0；后续重复帧不再改变垂直档位。
            np.savez(ctx.directory.raw / f"shot_d{index:02d}_r0.npz", **(probe | {"rep_index": 0}), accepted=True, temperature_restored=True)
            ctx.state["data_files"].append(f"raw/shot_d{index:02d}_r0.npz")
            shots = [probe]
            for rep in range(1, p.acq_repeats):
                shots.append(capture(ctx, duty, f"raw/shot_d{index:02d}_r{rep}.npz", rep_index=rep))
            relative = _save_average(ctx, index, duty, shots)
            completed.append((duty, relative))
            elapsed = time.monotonic() - started
            report_runtime_progress("A07", f"占空比 {duty * 100:g}% 已恢复温控并保存", 100 * len(completed) / len(p.duty_cycle_list),
                estimated_remaining_seconds=elapsed / len(completed) * (len(p.duty_cycle_list) - len(completed)))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            np.savez(ctx.directory.raw / "duty_scan_summary.npz", duty_cycle=np.asarray([d for d, _ in completed]),
                files=np.asarray([path.removeprefix("raw/") for _, path in completed], dtype=str),
                fixed_pump_power_voltage=p.fixed_pump_power_voltage, n_shots=len(completed))
            ctx.state["data_files"].append("raw/duty_scan_summary.npz")
        except Exception as exc:
            if failure is None:
                raise
            if hasattr(failure, "add_note"):
                failure.add_note(f"保存部分扫描索引亦失败：{exc}")
    return ctx.state["data_files"]
