"""SDS 配置、逐帧温控窗口、自适应试探及原 NPZ 格式保存。"""
import time
from pathlib import Path
import numpy as np
from sds_acquisition import AcquisitionConfig, ChannelConfig, TriggerConfig
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import acquisition_window, DeferredAcquisitionWrites
from .preparation import checked, switch
from .excitation import verify_burst


def wait(seconds):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def save_npz(path, **values):
    path = Path(path)
    temporary = path.with_suffix(".npz.tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez(stream, **values)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_shot(path, **values):
    try:
        save_npz(path, **values)
    except Exception as error:
        try:
            path = Path(path)
            save_npz(path.with_name("recovery_"+path.name), **values)
        except Exception as rescue:
            if hasattr(error, "add_note"):
                error.add_note(f"抢救保存亦失败：{rescue}")
        raise


def prepare_scope(ctx):
    p = ctx.params
    ctx.scope_config = AcquisitionConfig(sampling_rate=p.sds_sample_rate, sampling_time=p.total_acq_s,
        acquire_type="NORMal", acquire_delay=0.1, timebase_delay=p.sds_timebase_delay_s,
        channels=[ChannelConfig(number=i, enabled=i in (p.sds_pd_channel, p.sds_trig_channel),
            scale=p.sds_volt_scale_pd if i == p.sds_pd_channel else p.sds_volt_scale_trig,
            offset=0, coupling="DC", impedance="ONEMeg", probe=1) for i in range(1, 5)],
        trigger=TriggerConfig(mode="NORMal", source=f"C{p.sds_trig_channel}", type="EDGE", slope=p.sds_trig_slope, level=p.sds_trig_level))
    ctx.devices["acquirer"].apply_config(ctx.scope_config)
    wait(0.1)
    scope = ctx.devices["scope"]
    pts, rate, scale = int(scope.get_actual_points()), float(scope.get_sampling_rate()), float(scope.query(":TIMebase:SCALe?"))
    if pts < 4 or not np.isfinite(rate) or rate <= 0 or not np.isfinite(scale) or scale <= 0:
        raise ValueError("示波器实际采样点数、采样率或时基无效")
    ctx.state["scope"].update(actual_points=pts, actual_sample_rate_Hz=rate,
        actual_timebase_scale_s_per_div=scale, actual_capture_duration_s=pts/rate)
    ctx.outputs["示波器"] = f"PD C{p.sds_pd_channel}，C{p.sds_trig_channel} 触发，{pts} 点 @ {rate:g} Sa/s（已回读）"


def auto_scale_signal(waveform, current_scale, params):
    peak = float(np.max(np.abs(waveform)))
    full_scale = current_scale * params.vert_divs
    new_scale = current_scale
    if peak < 0.4 * full_scale and current_scale > params.scale_min * 2:
        new_scale = current_scale / 2
    elif peak > 0.9 * full_scale and current_scale < params.scale_max / 2:
        new_scale = current_scale * 2
    return new_scale, new_scale != current_scale


def acquire_single_shot(ctx, pump_power_v, current_scale, path, *, rep_index=-1):
    p, scope, config = ctx.params, ctx.devices["scope"], ctx.scope_config
    checked(ctx, "Pump_laser_power", pump_power_v)
    ctx.devices["sg"].setup_dc(pump_power_v, channel=ctx.channels["Pump_laser_power"])
    ctx.outputs["Pump"] = f"{pump_power_v:g} V DC + ON"
    scope.trigger_run()
    wait(0.05)
    scale_at_acquire = next(c for c in config.channels if c.number == p.sds_pd_channel).scale
    with DeferredAcquisitionWrites() as pending:
        with acquisition_window(lambda enabled: switch(ctx, enabled), wait, p.system_settle_s):
            wait(p.burst_burst_period_s+0.05)
            # 保留旧入口的超时警告策略，但轮询期间也提供取消检查点。
            deadline = time.monotonic()+p.burst_burst_period_s+5
            triggered = False
            while time.monotonic() < deadline:
                check_cancelled()
                status = scope.trigger_status()
                if status == "Trig'd":
                    triggered = True
                    break
                if status == "Stop":
                    break
                wait(0.05)
            if not triggered:
                print(f"[警告] Pump={pump_power_v:g} V 触发超时，保留原采集策略并记录 trigger_timeout")
            wait(config.acquire_delay)
            scope.trigger_stop()
            wait(0.02)
            result = ctx.devices["acquirer"].acquire_channel(p.sds_pd_channel, config.timebase_scale,
                config.horizontal_divisions, trim_points=ctx.state["scope"]["actual_points"])
            shot = dict(time_s=result.time, signal=result.voltage, pump_power_voltage=pump_power_v,
                rep_index=rep_index, scale_used=scale_at_acquire, t_burst_start_s=0.0, t_burst_end_s=p.burst_duration_s,
                sample_rate_actual_Hz=ctx.state["scope"]["actual_sample_rate_Hz"],
                points_actual=ctx.state["scope"]["actual_points"], trigger_timeout=not triggered)
            # 立即冻结已读数组；恢复或后续取消失败时仍在退出窗口后抢救。
            pending.add(save_shot, path, **shot)
        wait(p.temp_switch_on_delay_s)
        scope.trigger_run()
        wait(0.05)
    # 沿用原双阈值调整及外层最多三次试探；不把调整后档位冒充读数时档位。
    new_scale, changed = auto_scale_signal(shot["signal"], current_scale, p)
    if changed:
        scope.set_channel_scale(p.sds_pd_channel, new_scale)
        next(c for c in config.channels if c.number == p.sds_pd_channel).scale = new_scale
    shot["scale_after_read"] = new_scale
    return shot


def _average(ctx, shots, voltage):
    p, audit = ctx.params, ctx.state["scope"]
    ref = shots[0]["time_s"]
    # 不插值、不伪造对齐；样本数量或实际网格改变时保留逐帧而拒绝平均。
    if any(s["signal"].shape != shots[0]["signal"].shape or s["time_s"].shape != ref.shape
           or not np.allclose(s["time_s"], ref, rtol=0, atol=1e-9) for s in shots):
        raise ValueError("重复帧时间轴不一致，逐帧已保留，拒绝不可靠平均")
    return dict(time_s=ref, signal=np.mean([s["signal"] for s in shots], axis=0), pump_power_voltage=voltage,
        duty_cycle=p.fixed_duty_cycle, modulation_frequency_hz=p.modulation_frequency_hz, burst_cycles=p.burst_cycles,
        burst_duration_s=p.burst_duration_s, pre_baseline_time_s=p.pre_baseline_time_s, post_dark_time_s=p.post_dark_time_s,
        z_field_setpoint=p.z_field_setpoint, acq_repeats=p.acq_repeats, sample_rate_requested_Hz=p.sds_sample_rate,
        sample_rate_actual_Hz=audit["actual_sample_rate_Hz"], timebase_scale_actual_s_per_div=audit["actual_timebase_scale_s_per_div"],
        capture_duration_actual_s=audit["actual_capture_duration_s"], points_actual=audit["actual_points"],
        t_burst_start_s=0.0, t_burst_end_s=p.burst_duration_s, scale_used=shots[-1]["scale_used"],
        rf_gate_high_v=p.pump_rf_gate_high_v, rf_gate_low_v=p.pump_rf_gate_low_v,
        trigger_timeout=any(s["trigger_timeout"] for s in shots))


def acquire_scan(ctx):
    p, raw = ctx.params, ctx.directory.raw
    verify_burst(ctx)
    scope = ctx.devices["scope"]
    if (scope.get_trigger_mode().upper() not in ("NORM", "NORMAL")
            or scope.get_trigger_source().upper() != f"C{p.sds_trig_channel}"
            or int(scope.get_actual_points()) != ctx.state["scope"]["actual_points"]
            or not np.isclose(float(scope.get_sampling_rate()), ctx.state["scope"]["actual_sample_rate_Hz"], rtol=1e-6)):
        raise ValueError("示波器模式已改变，请重跑 A06 后再采集")
    started, completed, files = time.monotonic(), [], []
    failure = None
    try:
        for idx, voltage in enumerate(p.pump_power):
            check_cancelled()
            current_scale = next(c for c in ctx.scope_config.channels if c.number == p.sds_pd_channel).scale
            for retry in range(3):
                shot = acquire_single_shot(ctx, voltage, current_scale, raw/f"probe_p{idx:02d}_{retry}.npz")
                new_scale, changed = auto_scale_signal(shot["signal"], current_scale, p)
                if not changed or retry >= 2:
                    ctx.devices["scope"].set_channel_scale(p.sds_pd_channel, current_scale)
                    next(c for c in ctx.scope_config.channels if c.number == p.sds_pd_channel).scale = current_scale
                    break
                current_scale = new_scale
            shots = [shot]
            save_npz(raw/f"shot_p{idx:02d}_r0.npz", **(shot | {"rep_index": 0}))
            for rep in range(1, p.acq_repeats):
                check_cancelled()
                shots.append(acquire_single_shot(ctx, voltage, current_scale, raw/f"shot_p{idx:02d}_r{rep}.npz", rep_index=rep))
            name = f"shot_p{idx:02d}_avg.npz"
            save_npz(raw/name, **_average(ctx, shots, voltage))
            completed.append(voltage); files.append(name)
            ctx.state["data_files"] = ["raw/"+name for name in files]
            ctx.save()
            report_runtime_progress("A07", f"已完成 {idx+1}/{len(p.pump_power)} 个功率点", (idx+1)/len(p.pump_power)*100,
                estimated_remaining_seconds=(time.monotonic()-started)/(idx+1)*(len(p.pump_power)-idx-1))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        # 即使某一重复帧失败，也保留先前完整点的索引，不把不完整平均算作完成。
        try:
            save_npz(raw/"power_scan_summary.npz", pump_power_voltage=completed, files=np.asarray(files, dtype=str), n_shots=len(files))
        except Exception as exc:
            if failure is None:
                raise
            if hasattr(failure, "add_note"):
                failure.add_note(f"扫描汇总保存亦失败：{exc}")
    return {"data_files": ["raw/"+name for name in files], "completed_power_points": len(files)}
