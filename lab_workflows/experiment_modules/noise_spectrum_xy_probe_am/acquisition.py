"""Probe AM 成对采集；接收已连接设备，导入不访问硬件。"""
import time
from datetime import datetime
from ...steps.acquisition_window import DeferredAcquisitionWrites, acquisition_window

import numpy as np
from lockin_amplifier import DAQConfig, acquisition_metadata, daq

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ..noise_spectrum_xy._helpers import set_temp_switch
from ..noise_spectrum_xy.acquisition import save_config, initialize_run_directory
from ..noise_spectrum_xy.phase import set_xy_sine_peak
from ..noise_spectrum_xy.preparation import build_run_config
from ..noise_spectrum_xy_known_noise.acquisition import acquire_pair, wait_for_settle
from . import excitation

DATA_TYPE = "Noise_Spectrum_XY_Ctrl_Uncontrolled_Probe_AM"
EXPERIMENT_ID = "noise-spectrum-xy-uncontrolled-probe-am"


def acquire_scan(ctx):
    """每次扫描独立目录；一对波形先缓存，恢复温控后保存。"""
    params, mapping = ctx.params, ctx.mapping
    targets, envelopes = ctx.axes
    rate, cols, settings = ctx.daq_settings
    run_dir = initialize_run_directory(ctx, DATA_TYPE, for_scan=True)
    timestamp = run_dir.name[:9]
    raw = run_dir / "raw"
    config = build_run_config(params, ctx.root, ctx.clock_sources, timestamp, targets, envelopes)
    waveform, controlled = ctx.noise_waveform, ctx.z_waveform
    config.update(experiment_id=EXPERIMENT_ID, experiment_type=DATA_TYPE,
                  purpose="probe_am_uncontrolled_noise", completion_status="running",
                  workflow_session_id=ctx.session_id)
    config["xy_ctrl"].update(XY_CTRL_PHASE_deg=float(ctx.xy_result.final_phase_deg),
        Y_CTRL_PHASE_deg=float((ctx.xy_result.final_phase_deg + params.xy_ctrl_quad) % 360),
        phase_cal_success=True, phase_cal_history=[item.to_dict() for item in ctx.xy_result.history])
    config["hf2_daq"].update(actual_rate_Sa_s=rate, **settings)
    config["probe_am_noise"] = dict(waveform_file="raw/probe_am_noise_waveform.npy",
        points=len(waveform.normalized), repeat_frequency_hz=params.noise_repeat_freq_hz,
        sample_rate_sa_s=waveform.sample_rate_sa_s, amplitude_vpp=params.noise_amplitude_vpp,
        offset_v=0.0, seed=params.noise_seed, design_peak_v=waveform.design_peak_v,
        rms_v=waveform.rms_v,
        realized_psd_scale=(params.noise_amplitude_vpp / 2 / waveform.design_peak_v) ** 2,
        bands=[dict(start_hz=start, stop_hz=stop, design_psd_v2_per_hz=psd)
               for start, stop, psd in params.noise_bands()],
        readback=ctx.noise_readback,
        carrier_frequency_hz=params.carrier_frequency_hz, carrier_amplitude_vpp=params.carrier_amplitude_vpp,
        carrier_load_ohm=50, am_depth_percent=params.am_depth_percent,
        actual_waveform_psd_source="Welch PSD computed from scaled saved waveform",
        measurement_units="HF2 demodulator PSD; not calibrated to optical power")
    config["controlled_z_noise"] = dict(enabled=controlled is not None, **(
        dict(waveform_file="raw/controlled_z_noise_waveform.npy", points=len(controlled.normalized),
             repeat_frequency_hz=params.z_noise_repeat_freq_hz, sample_rate_sa_s=controlled.sample_rate_sa_s,
             amplitude_vpp=params.z_noise_amplitude_vpp, seed=params.z_noise_seed,
             design_peak_v=controlled.design_peak_v,
             bands=[dict(start_hz=start, stop_hz=stop, design_psd_v2_per_hz=psd)
                    for start, stop, psd in params.z_noise_bands()],
             readback=ctx.z_readback) if controlled is not None else {}))
    config["paired_acquisition"] = dict(order="alternating_off_on_on_off", first_point="off_on",
        settle_each_state_s=params.system_settle_s, timing_file="raw/pair_timing.jsonl",
        z_injection="continuous_during_scan" if controlled is not None else "disabled",
        temperature_policy="one_off_window_per_pair")
    path = run_dir / "experiment_config.yaml"
    failure = None
    started = time.monotonic()
    try:
        save_config(path, config)
        np.save(raw / "probe_am_noise_waveform.npy", waveform.normalized)
        if controlled is not None:
            np.save(raw / "controlled_z_noise_waveform.npy", controlled.normalized)
        np.savez(raw / "control_scan_axes.npz", target_noise_frequency_Hz=targets,
                 xy_envelope_voltage_V=envelopes, calibration_K_Hz_per_V=params.xy_ctrl_k_hz_per_v,
                 calibration_B_hz=params.xy_ctrl_b_hz)
        # Z 可控噪声在整段主扫描保持开启；输出回读不一致立即拒绝。
        z = ctx.devices["dg_sweep"]
        z_channel = int(mapping["Z_magnetic_field"]["channel"])
        z.set_output(controlled is not None, channel=z_channel)
        if bool(z.get_output(channel=z_channel)) != (controlled is not None):
            raise RuntimeError("Z 可控噪声输出状态回读与请求不一致")
        if controlled is not None:
            wait_for_settle(params.system_settle_s)
        probe_channel = int(mapping["Probe_AOM_AM"]["channel"])
        cfg = DAQConfig(device=mapping["lockin_r"]["device_id"], trigger_type=0,
            duration=params.hf2_daq_duration, grid_cols=cols, grid_rows=1, grid_mode=2,
            signal_paths=["sample.y"])

        def acquire():
            results = daq.acquire_data(ctx.devices["hf2"], cfg, demod_idx=params.hf2_demod_idx,
                actual_rate=rate, requested_rate=params.hf2_daq_rate,
                timeout=params.hf2_daq_duration + 10, cancellation_check=check_cancelled,
                strict_cleanup=True)
            config["hf2_daq"].update(acquisition_metadata(results))
            return results

        for index, envelope in enumerate(envelopes):
            check_cancelled()
            set_xy_sine_peak(ctx.devices["dg_comp"], envelope, ctx.xy_result.final_phase_deg,
                             params.xy_ctrl_quad, ctx.limits)
            ctx.devices["dg_probe_am"].set_output(bool(index % 2), channel=probe_channel)
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(
                    lambda enabled: set_temp_switch(ctx.devices["dg_temp"], enabled, params, ctx.limits, restore_wait=enabled),
                    wait_for_settle, params.system_settle_s,
                ):
                    acquire_pair(index, raw, ctx.devices["dg_probe_am"], probe_channel, acquire,
                                 params.system_settle_s, pending=pending, first_prepared=True)
            report_runtime_progress("A14", f"配对扫描 {index + 1}/{len(envelopes)}",
                100 * (index + 1) / len(envelopes),
                estimated_remaining_seconds=(time.monotonic() - started) / (index + 1) * (len(envelopes) - index - 1))
        config["completion_status"] = "completed"
    except BaseException as exc:
        failure = exc
        config.update(completion_status="cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed",
                      failure_reason=str(exc))
        raise
    finally:
        config["data_files"] = [str(file.relative_to(run_dir)).replace("\\", "/")
                                for file in sorted(raw.rglob("*")) if file.is_file()]
        try:
            save_config(path, config)
        except Exception:
            if failure is None:
                raise
    return run_dir
