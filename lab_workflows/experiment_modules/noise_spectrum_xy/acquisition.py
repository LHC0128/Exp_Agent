"""DAQ 准备、逐点采集和解调器恢复。"""
from datetime import datetime
from pathlib import Path
import time
import math
import numpy as np
import yaml
from tqdm import tqdm
from lockin_amplifier import DAQConfig, DemodulatorConfig, daq, demod
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress
from .models import welch_settings
from ._helpers import wait, set_temp_switch
from .phase import set_xy_sine_peak
from .preparation import build_run_config, EXPERIMENT_TYPE


def restore_demod(devices, params, calibrated_phase):
    cfg = DemodulatorConfig(demod_index=0, enable=True, rate=params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1, time_constant=params.hf2_demod_tc,
        order=params.hf2_demod_order, phase=calibrated_phase)
    demod.configure_demodulator(devices["hf2"], cfg)
    wait(0.2)


def prepare_daq(devices, params, calibrated_phase):
    cfg = DemodulatorConfig(demod_index=0, enable=True, rate=params.hf2_daq_rate,
        input_channel=0, osc_select=0, harmonic=1, time_constant=params.hf2_daq_tc,
        order=params.hf2_demod_order, phase=calibrated_phase)
    rate = float(demod.configure_demodulator(devices["hf2"], cfg))
    settings = welch_settings(rate, params.hf2_daq_duration, params.analysis_bin_width_hz)
    wait(0.2)
    return rate, int(rate * params.hf2_daq_duration), settings


def verify_daq_mode(ctx):
    hfi = ctx.devices["hf2"]
    base = hfi.demod_path(0)
    expected = {"rate": ctx.daq_settings[0], "timeconstant": ctx.params.hf2_daq_tc,
        "phaseshift": ctx.calibrated_phase}
    for field, value in expected.items():
        if not math.isclose(float(hfi.get_double(f"{base}/{field}")), value, rel_tol=1e-6, abs_tol=1e-9):
            raise RuntimeError("解调器实际模式已改变，请结束会话并重新准备")


def save_config(path, config):
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def acquire_scan(ctx):
    params, mapping, limits = ctx.params, ctx.mapping, ctx.limits
    hfi, tec, dg_comp, dg_temp = (ctx.devices[key] for key in ("hf2", "tec", "dg_comp", "dg_temp"))
    target_frequencies, envelopes = ctx.axes
    xy_phase = ctx.xy_result.final_phase_deg
    actual_rate_daq, grid_cols, settings = ctx.daq_settings
    timestamp = datetime.now().strftime("%m%d_%H%M")
    base = ctx.root / "data" / EXPERIMENT_TYPE
    base.mkdir(parents=True, exist_ok=True)
    name = f"{timestamp}_{params.run_tag}"
    suffix = 0
    while True:
        run_dir = base / (name if suffix == 0 else f"{name}_{suffix:02d}")
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            suffix += 1
    ctx.run_dir = run_dir
    raw_dir = run_dir / "raw"
    raw_dir.mkdir()
    (run_dir / "results").mkdir()
    config_saved = build_run_config(params, ctx.root, ctx.clock_sources, timestamp, target_frequencies, envelopes)
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
            wait(0.3)

            # 关闭温度开关（消除温控磁场干扰）
            set_temp_switch(dg_temp, False, params, limits)

            # 等待系统稳定
            wait(params.xy_settle_time)

            # 读取当前温度；失败不回刷进度，留到循环末 update 前一次性刷新
            temp_str = ""
            try:
                temp_now = tec.get_temperature(channel=1)
                temp_str = f", T={temp_now:.1f}°C"
            except WorkflowCancelled:
                raise
            except Exception:
                pass

            # 配置 HF2 DAQ 采集（每点独立配置）
            daq_cfg = DAQConfig(
                device=mapping["lockin_r"]["device_id"],
                trigger_type=0,                  # 连续模式
                duration=params.hf2_daq_duration,
                grid_cols=grid_cols,
                grid_rows=1,
                grid_mode=2,
                signal_paths=["sample.y"],       # 采集幅值信号
            )

            # 采集解调时域信号
            try:
                results = daq.acquire_data(
                    hfi, daq_cfg, demod_idx=0,
                    actual_rate=actual_rate_daq, timeout=params.hf2_daq_duration + 10.0,
                )
                waveform = results[0].values
            except WorkflowCancelled:
                raise
            except Exception as e:
                tqdm.write(
                    f"  [{i:4d}/{params.target_noise_freq_points}] "
                    f"Omega={target_freq_hz:.3f}Hz, V_env={envelope_v:.6f}V: "
                    f"DAQ 采集失败: {e}"
                )
                waveform = np.array([np.nan])

            check_cancelled()
            # 保存原始波形（.npy 二进制格式）
            np.save(raw_dir / f"waveform_C{i:04d}.npy", waveform)

            # 恢复温度开关
            set_temp_switch(dg_temp, True, params, limits, restore_wait=True)

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
