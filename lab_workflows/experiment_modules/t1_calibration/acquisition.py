"""CH1 差分重复采集；每次试探均重新配置并独立恢复温控后写盘。"""
import time
import numpy as np
from sds_acquisition import AcquisitionConfig, ChannelConfig, TriggerConfig
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import acquisition_window, DeferredAcquisitionWrites
from . import preparation
from .fitting import fit_decay
from .storage import save_npz, save_shot


def wait(seconds):
    deadline = time.monotonic()+seconds
    while True:
        check_cancelled()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def build_scope_config(p, scale):
    return AcquisitionConfig(sampling_rate=p.scope_sampling_rate, sampling_time=p.acq_duration,
        acquire_delay=p.acq_duration+0.5,
        channels=[ChannelConfig(number=i, enabled=i in (1, 4), scale=scale if i == 1 else 2,
            coupling="DC", impedance="ONEMeg", probe=1) for i in range(1, 5)],
        trigger=TriggerConfig(mode="SINGle", source=p.trigger_source, type="EDGE", slope="FALLing", level=p.trigger_level))


def prepare_frame(ctx, config):
    inst = ctx.devices["scope"]
    inst.trigger_stop()
    wait(.05)
    inst.set_sampling_rate(config.sampling_rate)
    inst.set_acquire_type(config.acquire_type, config.acquire_type_param)
    inst.set_timebase_scale(config.timebase_scale)
    inst.set_timebase_delay(config.timebase_delay)
    for ch in config.channels:
        inst.set_channel_state(ch.number, ch.enabled)
        if ch.enabled:
            for name in ("scale", "offset", "coupling", "impedance", "probe"):
                getattr(inst, "set_channel_"+name)(ch.number, getattr(ch, name))
    for name in ("mode", "type", "source", "slope", "level"):
        getattr(inst, "set_trigger_"+name)(getattr(config.trigger, name))
    wait(.05)


def prepare_scope(ctx):
    ctx.scope_config = build_scope_config(ctx.params, ctx.params.scope_volt_scale)
    prepare_frame(ctx, ctx.scope_config)
    scope = ctx.devices["scope"]
    points, rate = int(scope.get_actual_points()), float(scope.get_sampling_rate())
    if points < 20 or not np.isfinite(rate) or rate <= 0:
        raise ValueError("示波器实际点数或采样率无效")
    ctx.state["scope"].update(actual_points=points, actual_sample_rate_Hz=rate)
    ctx.outputs["示波器"] = f"CH1 PDB；CH4 下降沿触发；{points} 点 @ {rate:g} Sa/s（已回读）"


def read_frame(ctx, config):
    scope = ctx.devices["scope"]
    scope.trigger_run()
    deadline = time.monotonic()+10
    while True:
        status = scope.trigger_status()
        if status == "Trig'd":
            break
        if status == "Stop":
            raise RuntimeError("示波器停止但未确认触发，拒绝读取旧波形")
        if time.monotonic() >= deadline:
            raise TimeoutError("示波器未收到 CH4 下降沿，拒绝读取旧波形")
        wait(.05)
    wait(config.acquire_delay)
    scope.trigger_stop()
    return ctx.devices["acquirer"].acquire_channel(1, config.timebase_scale, config.horizontal_divisions, trim_points=0)


def auto_scale_signal(waveform, scale, params):
    peak = float(np.max(np.abs(waveform)))
    if peak < .4*scale*params.vert_divs and scale > params.scale_min*2:
        return scale/2, True
    if peak > .9*scale*params.vert_divs and scale < params.scale_max/2:
        return scale*2, True
    return scale, False


def acquire_frame(ctx, voltage, scale, path):
    p = ctx.params
    config = build_scope_config(p, scale)
    prepare_frame(ctx, config)
    with DeferredAcquisitionWrites() as pending:
        with acquisition_window(lambda enabled: preparation.switch(ctx, enabled), wait, p.system_settle_s):
            wait(p.pump_prep_time+0.5/p.pump_switch_freq)
            result = read_frame(ctx, config)
            shot = dict(time_s=result.time, signal_v=result.voltage, probe_power=voltage, scale_used=scale)
            pending.add(save_shot, path, **shot)
        wait(p.temp_switch_on_lag_s)
    t, y = np.asarray(shot["time_s"]), np.asarray(shot["signal_v"])
    if t.ndim != 1 or t.size < 10 or t.shape != y.shape or not np.isfinite(t).all() or not np.isfinite(y).all() or not np.all(np.diff(t) > 0):
        raise ValueError("示波器波形或时间轴无效；原始帧已保存")
    return shot


def measure_single(ctx, voltage, point_index):
    p = ctx.params
    preparation.checked(ctx, "Probe_laser_power", voltage)
    ctx.devices["sg"].setup_dc(voltage, channel=ctx.channels["Probe_laser_power"])
    ctx.outputs["Probe"] = f"{voltage:g} V DC + ON"
    accepted, scale = [], p.scope_volt_scale
    for rep in range(p.acq_repeats):
        check_cancelled()
        for retry in range(3):
            shot = acquire_frame(ctx, voltage, scale, ctx.directory.raw/f"frame_p{point_index:02d}_r{rep:02d}_a{retry}.npz")
            new_scale, changed = auto_scale_signal(shot["signal_v"], scale, p)
            if changed and retry < 2:
                scale = new_scale
                continue
            break
        if accepted and (shot["time_s"].shape != accepted[0]["time_s"].shape
                or not np.allclose(shot["time_s"], accepted[0]["time_s"], rtol=0, atol=1e-9)):
            raise ValueError("重复帧时间轴不一致；逐帧保留，拒绝插值平均")
        accepted.append(shot)
    t = accepted[0]["time_s"]
    avg = np.mean([s["signal_v"] for s in accepted], axis=0)
    return dict(probe_power=voltage, t_axis=t, vdiff_avg=avg, t0=0,
        n_acquired=len(accepted), scale_used=scale, **fit_decay(t, avg, p.t1_bounds))


def save_compatible(raw, records, scan):
    if not records:
        return
    if scan:
        save_npz(raw/"power_scan_results.npz", **{key: [r[key] for r in records] for key in
            ("probe_power", "T1", "T1_err", "scale_used", "n_acquired")})
        ref = records[0]["t_axis"]
        if any(r["t_axis"].shape != ref.shape or not np.allclose(r["t_axis"], ref, rtol=0, atol=1e-9) for r in records):
            raise ValueError("跨功率时间轴不一致；逐点已保留，拒绝旧堆叠格式")
        save_npz(raw/"power_scan_waveforms.npz", t_axis=ref, vdiff_stack=[r["vdiff_avg"] for r in records],
                 popt_stack=[r["popt"] for r in records], probe_power=[r["probe_power"] for r in records])
    else:
        data = dict(records[0])
        mask = data["t_axis"] >= 0
        data["t_axis"], data["vdiff_avg"] = data["t_axis"][mask], data["vdiff_avg"][mask]
        save_npz(raw/"t1_measurement.npz", **data)


def acquire_scan(ctx):
    preparation.verify_pump(ctx)
    scope, p = ctx.devices["scope"], ctx.params
    if scope.get_trigger_mode().upper() not in ("SING", "SINGLE") or scope.get_trigger_source().upper() != p.trigger_source:
        raise ValueError("示波器模式已改变，请重跑 A06")
    started, records, failure = time.monotonic(), [], None
    try:
        for index, voltage in enumerate(p.probe_values):
            check_cancelled()
            record = measure_single(ctx, voltage, index)
            save_npz(ctx.directory.raw/f"point_p{index:02d}.npz", **record)
            records.append(record)
            save_compatible(ctx.directory.raw, records, p.do_power_scan)
            ctx.state["data_files"] = [str(path.relative_to(ctx.directory.root)) for path in sorted(ctx.directory.raw.glob("*.npz"))]
            ctx.state["completed_probe_points"] = len(records)
            ctx.save()
            report_runtime_progress("A07", f"完成 {index+1}/{len(p.probe_values)} 个 Probe 点",
                100*(index+1)/len(p.probe_values),
                estimated_remaining_seconds=(time.monotonic()-started)/(index+1)*(len(p.probe_values)-index-1))
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            save_compatible(ctx.directory.raw, records, p.do_power_scan)
        except Exception as error:
            if failure is None:
                raise
            if hasattr(failure, "add_note"):
                failure.add_note(f"部分扫描汇总保存亦失败：{error}")
    return {"data_files": [str(path.relative_to(ctx.directory.root)) for path in sorted(ctx.directory.raw.glob("*.npz"))],
            "completed_probe_points": len(records)}
