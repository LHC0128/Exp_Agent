"""DAQ 准备、逐点采集和解调器恢复。"""
from ...steps.acquisition_window import acquisition_window, DeferredAcquisitionWrites
from datetime import datetime
from pathlib import Path
import time
import numpy as np
from tqdm import tqdm
from lockin_amplifier import DAQConfig, DemodulatorConfig, daq, demod, result_rate, acquisition_metadata, save_acquisition_metadata, acquisition_module, nearest_scope_rate
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress, report_run_directory
from ...steps.run_directory import write_run_config
from ...steps.lockin import verify_lockin_acquisition_mode
from .models import welch_settings
from ._helpers import wait, set_temp_switch
from .phase import set_xy_sine_peak
from .preparation import build_run_config, EXPERIMENT_TYPE
from .run_format import RAW_FORMAT_VERSION


def restore_demod(devices, params, calibrated_phase):
    cfg = DemodulatorConfig(demod_index=params.hf2_demod_idx, enable=True, rate=params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1, time_constant=params.hf2_demod_tc,
        order=params.hf2_demod_order, phase=calibrated_phase)
    demod.configure_demodulator(devices["hf2"], cfg)
    wait(0.2)


def prepare_daq(devices, params, calibrated_phase):
    cfg = DemodulatorConfig(demod_index=params.hf2_demod_idx, enable=True, rate=params.hf2_daq_rate,
        input_channel=0, osc_select=0, harmonic=1, time_constant=params.hf2_daq_tc,
        order=params.hf2_demod_order, phase=calibrated_phase)
    rate = float(demod.configure_demodulator(devices["hf2"], cfg, for_acquisition=True))
    expected_rate = nearest_scope_rate(params.hf2_daq_rate) if acquisition_module(
        devices["hf2"].model, params.hf2_daq_rate, getattr(devices["hf2"], "acquisition_mode", "auto")) == "scope" else rate
    settings = welch_settings(expected_rate, params.hf2_daq_duration, params.analysis_bin_width_hz)
    return expected_rate, int(expected_rate * params.hf2_daq_duration), settings


def verify_daq_mode(ctx):
    verify_lockin_acquisition_mode(ctx.devices["hf2"],
        demod_index=ctx.params.hf2_demod_idx, requested_rate_hz=ctx.params.hf2_daq_rate,
        acquisition_rate_hz=ctx.daq_settings[0], time_constant_s=ctx.params.hf2_daq_tc,
        phase_deg=ctx.calibrated_phase)


def save_config(path, config):
    write_run_config(path, config)


def initialize_run_directory(ctx, data_type, *, for_scan=False):
    """首次配置前保存初始快照；第一次扫描复用，后续重扫创建独立目录。"""
    if ctx.run_dir is None or (for_scan and getattr(ctx, "scan_started", False)):
        timestamp = datetime.now().strftime("%m%d_%H%M")
        base = ctx.root / "data" / data_type
        base.mkdir(parents=True, exist_ok=True)
        suffix = 0
        while True:
            run_dir = base / (f"{timestamp}_{ctx.params.run_tag}" + (f"_{suffix:02d}" if suffix else ""))
            try:
                run_dir.mkdir()
                break
            except FileExistsError:
                suffix += 1
        (run_dir / "raw").mkdir()
        (run_dir / "results").mkdir()
        config = build_run_config(ctx.params, ctx.root, ctx.clock_sources, timestamp, *ctx.axes)
        config.update(experiment_id=ctx.experiment_id, experiment_type=data_type,
                      completion_status="initializing", initial_temperature_c=None,
                      workflow_session_id=ctx.session_id)
        if ctx.experiment_id == "noise-spectrum-xy":
            config["raw_format_version"] = RAW_FORMAT_VERSION
        save_config(run_dir / "experiment_config.yaml", config)
        ctx.run_dir = run_dir
        ctx.scan_started = False
        report_run_directory(run_dir, data_type)
    if for_scan:
        ctx.scan_started = True
    return ctx.run_dir


def acquire_scan(ctx):
    params, mapping, limits = ctx.params, ctx.mapping, ctx.limits
    hfi, tec, dg_comp, dg_temp = (ctx.devices[key] for key in ("hf2", "tec", "dg_comp", "dg_temp"))
    target_frequencies, envelopes = ctx.axes
    xy_phase = ctx.xy_result.final_phase_deg
    actual_rate_daq, grid_cols, settings = ctx.daq_settings
    run_dir = initialize_run_directory(ctx, EXPERIMENT_TYPE, for_scan=True)
    timestamp = run_dir.name[:9]
    raw_dir = run_dir / "raw"
    config_saved = build_run_config(params, ctx.root, ctx.clock_sources, timestamp, target_frequencies, envelopes)
    config_saved["raw_format_version"] = RAW_FORMAT_VERSION
    config_saved["completion_status"] = "running"
    config_saved["workflow_session_id"] = ctx.session_id
    config_saved["xy_ctrl"].update(XY_CTRL_PHASE_deg=float(xy_phase),
        Y_CTRL_PHASE_deg=float((xy_phase + params.xy_ctrl_quad) % 360),
        phase_cal_success=bool(ctx.xy_result.converged), phase_cal_best_r_V=float(ctx.xy_result.best_r_v),
        phase_cal_history=[item.to_dict() for item in ctx.xy_result.history])
    config_saved["hf2_daq"].update(actual_rate_Sa_s=actual_rate_daq, **settings)
    config_path = run_dir / "experiment_config.yaml"
    save_config(config_path, config_saved)
    pbar = None
    failure = None
    t_start = time.monotonic()
    try:
        np.savez(raw_dir / "control_scan_axes.npz", target_noise_frequency_Hz=target_frequencies,
            xy_envelope_voltage_V=envelopes, calibration_K_Hz_per_V=np.float64(params.xy_ctrl_k_hz_per_v),
            calibration_B_Hz=np.float64(params.xy_ctrl_b_hz))
        pbar = tqdm(total=params.target_noise_freq_points, desc="Scanning", unit="pt", mininterval=5.0)
        for i, (target_freq_hz, envelope_v) in enumerate(
            zip(
                target_frequencies,
                envelopes,
                strict=True,
            )
        ):
            check_cancelled()

            set_xy_sine_peak(dg_comp, envelope_v, xy_phase, params.xy_ctrl_quad, limits)
            temp_str = ""
            daq_cfg = DAQConfig(device=mapping["lockin_r"]["device_id"], trigger_type=0,
                duration=params.hf2_daq_duration, grid_cols=grid_cols, grid_rows=1,
                grid_mode=2, signal_paths=["sample.y"])
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(
                    lambda enabled: set_temp_switch(dg_temp, enabled, params, limits, restore_wait=enabled),
                    wait, params.system_settle_s,
                ):
                    try:
                        results = daq.acquire_data(
                            hfi, daq_cfg, demod_idx=params.hf2_demod_idx,
                            actual_rate=actual_rate_daq, requested_rate=params.hf2_daq_rate,
                            timeout=params.hf2_daq_duration + 10.0,
                            cancellation_check=check_cancelled, strict_cleanup=True,
                        )
                        waveform = results[0].values
                        capture_rate = result_rate(results, actual_rate_daq)
                        settings = welch_settings(capture_rate, len(waveform) / capture_rate, params.analysis_bin_width_hz)
                        config_saved["hf2_daq"].update(acquisition_metadata(results), actual_rate_Sa_s=capture_rate, **settings)
                        pending.add(save_acquisition_metadata, raw_dir / f"waveform_C{i:04d}.npy", results)
                    except WorkflowCancelled:
                        raise
                    except Exception as error:
                        tqdm.write(f"点 {i}: DAQ 采集失败: {error}")
                        waveform = np.array([np.nan])
                    pending.add(np.save, raw_dir / f"waveform_C{i:04d}.npy", waveform)

            pbar.set_postfix_str(
                f"Omega={target_freq_hz/1e3:.2f}kHz, V={envelope_v:.3f}{temp_str}"
            )
            pbar.update(1)
            elapsed = time.monotonic() - t_start
            report_runtime_progress("A12", f"扫描 {i + 1}/{params.target_noise_freq_points}",
                100 * (i + 1) / params.target_noise_freq_points,
                estimated_remaining_seconds=elapsed / (i + 1) * (params.target_noise_freq_points - i - 1))
        config_saved["completion_status"] = "completed"
    except BaseException as exc:
        failure = exc
        config_saved["completion_status"] = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
        config_saved["failure_reason"] = str(exc)
        raise
    finally:
        try:
            if pbar is not None:
                pbar.close()
        except BaseException as exc:
            config_saved["completion_status"] = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
            config_saved.setdefault("failure_reason", str(exc))
            if failure is None:
                failure = exc
                raise
        finally:
            config_saved["data_files"] = [str(path.relative_to(run_dir)).replace("\\", "/")
                for path in sorted(raw_dir.iterdir()) if path.is_file()]
            try:
                save_config(config_path, config_saved)
            except Exception:
                if failure is None:
                    raise
    return run_dir
