"""单层频率扫描；每点在恢复温控后原子保存，故障抢救已读分量。"""
import math
import time
import numpy as np
from lockin_amplifier import DAQConfig, DemodulatorConfig, OscillatorConfig, demod, daq, result_rate, acquisition_metadata
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import acquisition_window, DeferredAcquisitionWrites
from . import preparation
from .storage import save_npz, persist_point


def wait(seconds):
    check_cancelled()
    deadline = time.monotonic() + seconds
    while (remaining := deadline-time.monotonic()) > 0:
        check_cancelled()
        time.sleep(min(0.1, remaining))


def verify_ready(ctx):
    value = preparation.verify_routing(ctx)
    previous = ctx.state["measurement_ready"]
    for section in ("main", "inputs", "aux", "main_frequency"):
        if value[section] != previous[section]:
            raise ValueError("锁相测量模式或配置已改变，请重新准备")
    return value


def configure_point(ctx, frequency, record):
    p, h, sg, ch = ctx.params, ctx.devices["lockin"], ctx.devices["sg"], ctx.channels
    channel = ch["Z_magnetic_field"]
    if frequency == 0:
        preparation.checked(ctx, "Z_magnetic_field", 0)
        sg.set_burst_state(False, channel=channel); sg.set_mod_state(False, channel=channel)
        sg.setup_dc(0, channel=channel); sg.set_output(False, channel=channel)
        ctx.outputs["Z"] = "0 V DC + OFF（XY 保持 ON）"
    else:
        preparation.checked(ctx, "Z_magnetic_field", -p.z_rf_amplitude/2, p.z_rf_amplitude/2)
        sg.set_mod_state(False, channel=channel)
        sg.setup_sine(freq=float(frequency), amplitude=p.z_rf_amplitude, offset=0, phase=0, channel=channel)
        sg.set_burst_state(True, channel=channel); sg.set_burst_mode("INFinity", channel=channel)
        sg.set_burst_trigger_source("EXTernal", channel=channel); sg.set_output(True, channel=channel)
        ctx.outputs["Z"] = f"{frequency:g} Hz、{p.z_rf_amplitude:g} Vpp、无限 Burst／外触发 ON"
        if p.acquisition_mode in ("demod_r", "both"):
            demod.configure_oscillator(h, OscillatorConfig(osc_index=p.demod3_osc_idx, frequency=float(frequency)))
            demod.configure_demodulator(h, DemodulatorConfig(demod_index=p.demod3_idx, enable=True,
                rate=max(100, min(p.demod3_rate, int(max(frequency, 1)*50))), input_channel=p.sigin2_index,
                osc_select=p.demod3_osc_idx, harmonic=1, time_constant=max(p.demod3_tc_min_s, p.demod3_tc_period_fraction/frequency),
                order=p.demod3_order, phase=0))
            path = h.demod_path(p.demod3_idx)
            record.update(demod_r_actual_tc_s=h.get_double(path+"/timeconstant"),
                demod_r_actual_rate_Sa_s=h.get_double(path+"/rate"), demod_r_adcselect_actual=h.get_int(path+"/adcselect"))
            preparation.verify_routing(ctx)


def read_y(ctx, record):
    p = ctx.params
    results = daq.acquire_data(ctx.devices["lockin"], DAQConfig(device=ctx.mapping["lockin_r"]["device_id"],
        trigger_type=0, duration=p.point_duration_s, grid_cols=int(ctx.stream_rate*p.point_duration_s),
        grid_rows=1, grid_mode=2, signal_paths=["sample.y"]), demod_idx=p.hf2_demod0_idx,
        actual_rate=ctx.stream_rate, requested_rate=p.hf2_demod0_rate, cancellation_check=check_cancelled, timeout=p.point_duration_s+5)
    y = next((result for result in results if result.signal_name == "sample.y"), None)
    if y is None:
        raise ValueError("缺少主解调 Y 时域数据")
    rate = float(result_rate(results, ctx.stream_rate))
    values, axis = np.asarray(y.values).copy(), np.asarray(y.time).copy()
    if (values.ndim != 1 or len(values) < 4 or values.shape != axis.shape or not np.isfinite(rate) or rate <= 0
            or not np.all(np.isfinite(values)) or not np.all(np.isfinite(axis)) or np.any(np.diff(axis) <= 0)):
        raise ValueError("Y 时域数据、时间轴或最终实际率无效")
    record.update(y_V=values, time_s=axis, actual_rate_Sa_s=rate, acquisition_metadata=acquisition_metadata(results))


def read_r(ctx, record):
    p = ctx.params
    samples = []
    try:
        wait(p.system_settle_s)
        for _ in range(p.demod3_n_avg):
            check_cancelled()
            sample = demod.read_demod_sample(ctx.devices["lockin"], demod_idx=p.demod3_idx)
            if not all(math.isfinite(float(sample[key])) for key in ("x", "y", "r", "theta")):
                raise ValueError("RF 解调读数非有限")
            samples.append(sample)
            wait(p.demod3_avg_interval_s)
    finally:
        if samples:
            x, y, r, theta = (np.asarray([s[key] for s in samples]) for key in ("x", "y", "r", "theta"))
            xm, ym = float(np.mean(x)), float(np.mean(y))
            phase = math.atan2(ym, xm)
            record.update(demod_r_x_values_V=x, demod_r_y_values_V=y, demod_r_r_values_V=r,
                demod_r_theta_values_rad=theta, demod_r_theta_values_deg=np.degrees(theta),
                demod_r_x_mean_V=xm, demod_r_y_mean_V=ym, demod_r_r_vector_mean_V=math.hypot(xm, ym),
                demod_r_r_mean_scalar_V=float(np.mean(r)), demod_r_r_std_scalar_V=float(np.std(r)),
                demod_r_phase_vector_rad=phase, demod_r_phase_vector_deg=math.degrees(phase))


def acquire_scan(ctx):
    verify_ready(ctx)
    p, raw = ctx.params, ctx.directory.raw
    axis, records, started = p.frequency_axis(), [], time.monotonic()
    for index, frequency in enumerate(axis):
        check_cancelled()
        baseline = bool(frequency == 0)
        name = f"freq_{index:04d}_0Hz_BASELINE.npz" if baseline else f"freq_{index:04d}_{frequency:.3f}Hz.npz"
        record = dict(freq_Hz=float(frequency), is_baseline=np.uint8(baseline), z_drive_amplitude_Vpp=p.z_rf_amplitude,
            acquisition_mode=p.acquisition_mode, actual_rate_Sa_s=0.0, acquisition_complete=False)
        configure_point(ctx, frequency, record)
        try:
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(lambda enabled: preparation.switch(ctx, enabled), wait, p.system_settle_s):
                    try:
                        if p.acquisition_mode in ("daq_fft_y", "both"):
                            read_y(ctx, record)
                        if p.acquisition_mode in ("demod_r", "both") and not baseline:
                            read_r(ctx, record)
                        record["acquisition_complete"] = True
                    finally:
                        if "y_V" in record or "demod_r_x_values_V" in record or (baseline and p.acquisition_mode == "demod_r"):
                            records.append(dict(idx=index, freq_Hz=float(frequency), is_baseline=baseline, file=name))
                            pending.add(persist_point, raw, name, record, records)
                wait(p.temp_switch_on_lag)
        finally:
            # 恢复／抢救尝试结束后更新内存清单，配置由外层保存，不覆盖原始异常。
            ctx.state["data_files"] = ["raw/frequency_index.json", "raw/frequency_index.npz", *["raw/"+r["file"] for r in records]]
            ctx.state["freq_sweep"]["actual_freq_list_Hz"] = [r["freq_Hz"] for r in records]
        ctx.save()
        report_runtime_progress("A10", f"频率 {index+1}/{len(axis)}：{frequency:g} Hz", (index+1)/len(axis)*100,
            estimated_remaining_seconds=(time.monotonic()-started)/(index+1)*(len(axis)-index-1))
    return dict(acquired_points=len(records))
