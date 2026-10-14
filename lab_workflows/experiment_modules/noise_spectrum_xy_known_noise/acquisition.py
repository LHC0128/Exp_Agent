"""本实验成对采集步骤；接收已连接设备，导入不访问硬件。"""
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from ..noise_spectrum_xy.acquisition import initialize_run_directory
import json
import time
from datetime import datetime

import numpy as np

from ...experiment_runtime import check_cancelled
from ...common import validate_safety_limit
from ...steps import DGChannelShutdown, TemperatureSwitchRestore, run_safety_shutdown, set_temperature_switch
from ...steps.dispersion import acquire_dispersion_point, save_scan
from .dispersion import fit_dispersion_curve
from ..noise_spectrum_xy.acquisition import prepare_daq, restore_demod, verify_daq_mode, save_config
from ..noise_spectrum_xy.preparation import build_run_config
from ..noise_spectrum_xy.phase import set_xy_sine_peak
from ..noise_spectrum_xy._helpers import set_temp_switch
from ...common import WorkflowCancelled
from ...experiment_runtime import report_runtime_progress
from lockin_amplifier import DAQConfig, daq, result_rate, acquisition_metadata, save_acquisition_metadata

DATA_TYPE = "Noise_Spectrum_XY_Ctrl_Known_Noise"


def acquire_scan(ctx):
    """每次扫描独立目录；一对波形先缓存，恢复温控后保存。"""
    params = ctx.params
    targets, envelopes = ctx.axes
    rate, cols, settings = ctx.daq_settings
    run_dir = initialize_run_directory(ctx, DATA_TYPE, for_scan=True)
    timestamp = run_dir.name[:9]
    raw = run_dir / "raw"
    config = build_run_config(params, ctx.root, ctx.clock_sources, timestamp, targets, envelopes)
    waveform, calibration = ctx.noise_waveform, ctx.z_calibration
    config.update(experiment_id="noise-spectrum-xy-known-noise", experiment_type=DATA_TYPE,
                  purpose="known_noise_verification", completion_status="running", workflow_session_id=ctx.session_id)
    config["xy_ctrl"].update(XY_CTRL_PHASE_deg=float(ctx.xy_result.final_phase_deg),
        Y_CTRL_PHASE_deg=float((ctx.xy_result.final_phase_deg + params.xy_ctrl_quad) % 360),
        phase_cal_success=True, phase_cal_history=[item.to_dict() for item in ctx.xy_result.history])
    config["hf2_daq"].update(actual_rate_Sa_s=rate, **settings)
    config["known_noise"] = dict(waveform_file="raw/known_noise_waveform.npy", points=len(waveform.normalized),
        repeat_frequency_hz=params.noise_repeat_freq_hz, sample_rate_sa_s=waveform.sample_rate_sa_s,
        line_spacing_hz=waveform.line_spacing_hz, amplitude_vpp=params.noise_amplitude_vpp, offset_v=0.0,
        seed=params.noise_seed, design_peak_v=waveform.design_peak_v, rms_v=waveform.rms_v,
        realized_psd_scale=(params.noise_amplitude_vpp / 2 / waveform.design_peak_v)**2,
        bands=[dict(start_hz=start, stop_hz=stop, design_psd_v2_per_hz=psd) for start, stop, psd in params.noise_bands()],
        playback="continuous_no_burst", readback=ctx.noise_readback)
    config["truth_chain"] = dict(z_calibration_source="manual", z_calibration_run=calibration.source_run, K_Z_Hz_per_V=calibration.k_hz_per_v,
        K_Z_uncertainty_Hz_per_V=calibration.uncertainty_hz_per_v, coil_frequency_response_applied=False)
    config["paired_acquisition"] = dict(order="alternating_off_on_on_off", first_point="off_on",
        settle_s=params.system_settle_s, temperature_policy="one_off_window_per_pair")
    path = run_dir / "experiment_config.yaml"
    failure = None
    started = time.monotonic()
    try:
        save_config(path, config)
        np.save(raw / "known_noise_waveform.npy", waveform.normalized)
        np.savez(raw / "control_scan_axes.npz", target_noise_frequency_Hz=targets, xy_envelope_voltage_V=envelopes,
                 calibration_K_Hz_per_V=params.xy_ctrl_k_hz_per_v, calibration_B_Hz=params.xy_ctrl_b_hz)
        for index, envelope in enumerate(envelopes):
            check_cancelled()
            set_xy_sine_peak(ctx.devices["dg_comp"], envelope, ctx.xy_result.final_phase_deg, params.xy_ctrl_quad, ctx.limits)
            ctx.devices["dg_sweep"].set_output(bool(index % 2), channel=1)
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(
                    lambda enabled: set_temp_switch(ctx.devices["dg_temp"], enabled, params, ctx.limits, restore_wait=enabled),
                    wait_for_settle, params.system_settle_s,
                ):
                    cfg = DAQConfig(device=ctx.mapping["lockin_r"]["device_id"], trigger_type=0,
                        duration=params.hf2_daq_duration, grid_cols=cols, grid_rows=1, grid_mode=2, signal_paths=["sample.y"])
                    def acquire():
                        results = daq.acquire_data(ctx.devices["hf2"], cfg, demod_idx=params.hf2_demod_idx,
                            actual_rate=rate, requested_rate=params.hf2_daq_rate, timeout=params.hf2_daq_duration + 10,
                            cancellation_check=check_cancelled, strict_cleanup=True)
                        config["hf2_daq"].update(acquisition_metadata(results))
                        return results
                    acquire_pair(index, raw, ctx.devices["dg_sweep"], 1, acquire, params.system_settle_s, pending=pending, first_prepared=True)
            report_runtime_progress("A13", f"配对扫描 {index+1}/{len(envelopes)}", 100*(index+1)/len(envelopes),
                estimated_remaining_seconds=(time.monotonic()-started)/(index+1)*(len(envelopes)-index-1))
        config["completion_status"] = "completed"
    except BaseException as exc:
        failure = exc
        config.update(completion_status="cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                      failure_reason=str(exc))
        raise
    finally:
        config["data_files"] = [str(file.relative_to(run_dir)).replace("\\", "/") for file in sorted(raw.rglob("*")) if file.is_file()]
        try:
            save_config(path, config)
        except Exception:
            if failure is None:
                raise
    return run_dir


def wait_for_settle(seconds):
    """稳定等待期间响应取消，异常由工作流统一安全收尾。"""
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _append_pair_timing(path, record):
    """由恢复温控后的保存阶段追加主机时间记录。"""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def acquire_pair(index, raw_dir, generator, channel, acquire, settle_s, *, pending, first_prepared=False):
    """相邻点交替两态顺序；缓存每态数据，调用方恢复温控后统一保存。"""
    states = (False, True) if index % 2 == 0 else (True, False)
    for order, enabled in enumerate(states):
        check_cancelled()
        state = "on" if enabled else "off"
        if order or not first_prepared:
            generator.set_output(enabled, channel=channel)
            wait_for_settle(settle_s)
        started = time.time()
        acquired = acquire()
        if isinstance(acquired, list):
            waveform = acquired[0].values
        else:
            waveform = acquired
        finished = time.time()
        name = f"waveform_C{index:04d}_{state}.npy"
        pending.add(np.save, raw_dir / name, waveform)
        if isinstance(acquired, list):
            pending.add(save_acquisition_metadata, raw_dir / name, acquired)
        waveform = np.asarray(waveform)
        nonfinite = int((~np.isfinite(waveform)).sum())
        valid = bool(waveform.ndim == 1 and waveform.size >= 2 and nonfinite == 0
                     and np.std(waveform) > 0)
        # 主机时间标记 DAQ 调用边界，不冒充仪器触发时间。
        pending.add(_append_pair_timing, raw_dir / "pair_timing.jsonl",
            dict(point=index, order=order, state=state, file=name,
                 started_unix_s=started, finished_unix_s=finished,
                 settle_s=settle_s, samples=int(waveform.size),
                 nonfinite_samples=nonfinite, waveform_valid=valid))
        if not valid:
            print(f"警告: {name} 波形无效（非有限样本 {nonfinite}）；保留原文件，离线排除该数据对。", flush=True)


def acquire_dispersion_scan(params, *, z, z_channel, temp, temp_channel, hf2,
                            raw_dir, k_z_hz_per_v):
    """Z 纯 DC 色散扫描；单点连续采集，成功或故障均归零 Z 并恢复温控。"""
    offsets = np.linspace(-abs(params.dispersion_span_v), abs(params.dispersion_span_v),
                          params.dispersion_steps)
    data = {
        "schema_version": np.array(3), "offsets_v": offsets,
        "completed_mask": np.zeros(offsets.size, dtype=bool),
        "sample_counts": np.zeros(offsets.size, dtype=int),
        "point_started_unix_s": np.full(offsets.size, np.nan),
        "point_finished_unix_s": np.full(offsets.size, np.nan),
        "requested_rate_sa_s": np.array(params.hf2_demod_rate),
        "requested_duration_s": np.array(params.dispersion_duration_s),
        "actual_rate_sa_s": np.full(offsets.size, np.nan),
        "actual_duration_s": np.full(offsets.size, np.nan),
        "k_z_hz_per_v": np.array(k_z_hz_per_v),
        **{f"{key}_median": np.full(offsets.size, np.nan) for key in ("x", "y", "r")},
    }
    path = raw_dir / "dispersion_scan.npz"
    interval = params.dispersion_temp_switch_interval_points
    with DeferredAcquisitionWrites() as pending:
        failed = False
        try:
            z.set_output(False, channel=z_channel)
            validate_safety_limit("Z_magnetic_field", 0.0)
            z.setup_dc(0.0, channel=z_channel)
            for index, offset in enumerate(offsets):
                check_cancelled()
                data["point_started_unix_s"][index] = time.time()
                validate_safety_limit("Z_magnetic_field", float(offset))
                z.setup_dc(float(offset), channel=z_channel)
                z.set_output(True, channel=z_channel)
                if index == 0 or (interval > 0 and index % interval == 0):
                    validate_safety_limit("Temp_Switch", 0.0)
                    temp.set_output(False, channel=temp_channel)
                wait_for_settle(params.system_settle_s)
                point = acquire_dispersion_point(
                    hf2, demod_idx=params.hf2_demod_idx, duration_s=params.dispersion_duration_s,
                    requested_rate=params.hf2_demod_rate, offset_v=float(offset),
                    path=raw_dir / "dispersion_steps" / f"point_{index:04d}.npz",
                    cancellation_check=check_cancelled, pending=pending,
                )
                for key in ("x", "y", "r"):
                    data[f"{key}_median"][index] = point[f"{key}_median"]
                data["sample_counts"][index] = point["sample_count"]
                data["actual_rate_sa_s"][index] = point["actual_rate_sa_s"]
                data["actual_duration_s"][index] = point["actual_duration_s"]
                data["point_finished_unix_s"][index] = time.time()
                data["completed_mask"][index] = True
                z.set_output(False, channel=z_channel)
                pending.add(save_scan, path, data)
                if interval > 0 and (index + 1) % interval == 0 and index + 1 < offsets.size:
                    set_temperature_switch(temp, True, channel=temp_channel, on_voltage=params.temp_switch)
                    wait_for_settle(params.temp_switch_on_settle_s)
                    pending.flush()
            set_temperature_switch(temp, True, channel=temp_channel, on_voltage=params.temp_switch)
            pending.flush()
            fit = fit_dispersion_curve(
                offsets, data["y_median"], k_z_hz_per_v=k_z_hz_per_v,
            )
            data.update(fit, fit_valid=fit["is_valid"])
            pending.add(save_scan, path, data)
        except BaseException:
            failed = True
            try:
                pending.add(save_scan, path, data)
            except Exception as exc:
                print(f"色散部分数据保存失败：{exc}", flush=True)
            raise
        finally:
            report = run_safety_shutdown(
                dg_channels=(DGChannelShutdown(z, z_channel, "Z_magnetic_field", "Z 色散偏置"),),
                temperature_switch=TemperatureSwitchRestore(temp, temp_channel),
            )
            if report.errors:
                print("色散安全恢复失败：" + "；".join(report.errors), flush=True)
                if not failed:
                    raise RuntimeError("色散安全恢复失败：" + "；".join(report.errors))
        wait_for_settle(params.temp_switch_on_settle_s)
        return data
