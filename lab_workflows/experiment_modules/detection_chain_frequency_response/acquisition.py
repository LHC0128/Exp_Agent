"""HF2/AOM 配置与逐频点温控门控扫描采集。"""

from __future__ import annotations

import time

import numpy as np
import yaml
from lockin_amplifier import (
    DAQConfig,
    DemodulatorConfig,
    SignalInputConfig,
    OscillatorConfig,
    acquisition_metadata,
    daq,
    demod,
    result_rate,
    save_acquisition_metadata,
)
from scipy import signal

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, report_runtime_progress
from ...steps import (
    create_run_directory,
    set_temperature_switch,
    temperature_gated_acquire,
)

from .preparation import EXPERIMENT_TYPE, _RuntimeCancellation, wait


def configure_hf2(devices, params):
    """配置信号输入、振荡器与采集解调器；返回 (实际采样率, 配置快照)。"""
    hf2 = devices["hf2"]
    demod.configure_signal_input(
        hf2, SignalInputConfig(
            input_index=0, range=params.hf2_signal_range_v,
            diff=False,
        ),
    )
    demod.configure_oscillator(
        hf2, OscillatorConfig(osc_index=0, frequency=params.hf2_osc_freq_hz, source="manual"),
    )
    phase_shift_deg = float(hf2.get_double(f"{hf2.demod_path(params.hf2_demod_idx)}/phaseshift"))
    actual_rate = demod.configure_demodulator(
        hf2,
        DemodulatorConfig(
            demod_index=params.hf2_demod_idx, enable=True,
            rate=params.hf2_daq_rate_sa_s, input_channel=0,
            osc_select=0, harmonic=1,
            time_constant=params.hf2_daq_tc_s,
            order=params.hf2_demod_order,
            phase=phase_shift_deg,
        ),
        for_acquisition=True,
    )
    if actual_rate <= 0:
        raise RuntimeError("HF2 返回无效采样率")
    configuration = {
        "demod_idx": params.hf2_demod_idx,
        "osc_freq_hz": params.hf2_osc_freq_hz,
        "input_range_v": params.hf2_signal_range_v,
        "input_ac_coupling": False,
        "input_differential": False,
        "input_impedance_ohm": 1_000_000,
        "input_channel": 0,
        "oscillator_index": 0,
        "oscillator_source": "manual",
        "harmonic": 1,
        "phase_shift_deg": phase_shift_deg,
        "demod_order": params.hf2_demod_order,
        "demod_time_constant_s": params.hf2_daq_tc_s,
        "requested_rate_sa_s": params.hf2_daq_rate_sa_s,
        "actual_rate_sa_s": actual_rate,
    }
    return float(actual_rate), configuration


def configure_aom_carrier(devices, channels, params):
    """配置 Probe AOM 载波并启用外部 AM 调制链。"""
    aom = devices["aom"]
    aom.set_output(False, channel=channels["carrier"])
    aom.set_output(False, channel=channels["am"])
    aom.setup_sine(
        params.probe_aom_carrier_freq_hz, params.probe_aom_carrier_amplitude_vpp,
        channel=channels["carrier"],
    )
    aom.set_mod_type("AM", channel=channels["carrier"])
    aom.set_mod_am_source("EXT", channel=channels["carrier"])
    aom.set_mod_state(True, channel=channels["carrier"])


def acquire_scan(ctx):
    """逐频点在温控断开窗口内采集 Demod0 Y 并提取基带谱峰；每轮扫描独立目录。"""
    params, mapping = ctx.params, ctx.mapping
    aom, temp_switch, hf2 = (ctx.devices[key] for key in ("aom", "temp_switch", "hf2"))
    hf2_cfg = mapping["lockin_r"]
    actual_rate = ctx.actual_rate
    max_baseband = max(
        abs(params.am_freq_start_hz - params.hf2_osc_freq_hz),
        abs(params.am_freq_stop_hz - params.hf2_osc_freq_hz),
    )
    if max_baseband >= actual_rate / 2:
        raise ValueError("HF2 实际采样率不足以覆盖扫描范围的基带频率")

    directory = None
    suffix = 0
    while directory is None:
        # 同一秒内重跑同一标签时追加序号，保证每轮扫描独立目录。
        tag = params.run_tag if suffix == 0 else f"{params.run_tag}_{suffix:02d}"
        try:
            directory = create_run_directory(
                EXPERIMENT_TYPE, tag, params.to_external(),
                schema_version=params.schema_version, project_root=ctx.root,
            )
        except FileExistsError:
            suffix += 1
    ctx.run_dir = directory.root
    ctx.run_directory = directory
    directory.update_config(
        experiment_id="detection-chain-frequency-response",
        execution_mode="typed_workflow",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        completion_status="running",
        workflow_session_id=ctx.session_id,
        clock_sources=ctx.clock_sources,
        temperature_switch={
            "gated_per_point": True,
            "mapping_key": "Temp_Switch",
            "off_lead_s": params.system_settle_s,
            "on_lag_s": params.temp_switch_on_lag_s,
        },
        temperature_control=ctx.temperature_control,
        tec_connection_error=ctx.tec_connection_error,
        actual_rates={"hf2_demod_sa_s": actual_rate},
        hf2_configuration=ctx.hf2_configuration,
    )

    freqs = np.linspace(params.am_freq_start_hz, params.am_freq_stop_hz, params.am_freq_points)
    peaks, peak_freqs = np.full(freqs.size, np.nan), np.full(freqs.size, np.nan)
    started = time.monotonic()
    try:
        for index, freq in enumerate(freqs):
            check_cancelled()
            report_runtime_progress("acquire", f"频率点 {index + 1}/{freqs.size}: {freq:g} Hz",
                                    15 + 75 * index / freqs.size)
            if index == 0:
                aom.setup_sine(float(freq), params.probe_aom_am_amplitude_vpp,
                               channel=ctx.channels["am"])
            else:
                aom.set_frequency(float(freq), channel=ctx.channels["am"])
            config = DAQConfig(
                device=hf2_cfg["device_id"], trigger_type=0,
                duration=params.hf2_daq_duration_s,
                grid_cols=max(8, int(round(actual_rate * params.hf2_daq_duration_s))),
                grid_rows=1, grid_mode=2, signal_paths=["sample.y"],
            )

            def acquire_point():
                return daq.acquire_data(
                    hf2, config=config, demod_idx=params.hf2_demod_idx,
                    actual_rate=actual_rate, cancellation_check=check_cancelled,
                    requested_rate=params.hf2_daq_rate_sa_s,
                    timeout=max(5.0, params.hf2_daq_duration_s + 3.0),
                )

            results = temperature_gated_acquire(
                temp_switch=temp_switch, temp_channel=ctx.channels["temp_switch"],
                off_settle_s=params.system_settle_s,
                on_settle_s=params.temp_switch_on_lag_s,
                acquire=acquire_point, check_cancelled=check_cancelled,
                sleep=wait, set_temperature_switch=set_temperature_switch,
                cancellation=_RuntimeCancellation(),
            )
            y = next((
                np.asarray(item.values, dtype=float).reshape(-1)
                for item in results
                if str(item.signal_name).lower() == "y"
                or str(item.signal_name).lower().endswith(".y")
            ), None)
            if y is None or y.size < 8:
                raise RuntimeError(f"频率点 {index + 1} 未取得足够的 HF2 Y 数据")
            np.save(directory.raw / f"waveform_{index:04d}.npy", y)
            actual_rate = result_rate(results, actual_rate)
            save_acquisition_metadata(directory.raw / f"waveform_{index:04d}.npy", results)
            directory.update_config(acquisition=acquisition_metadata(results))
            psd_freq, psd = signal.welch(
                y, fs=actual_rate, nperseg=min(params.psd_nperseg, y.size),
                scaling="density",
            )
            baseband = abs(float(freq) - params.hf2_osc_freq_hz)
            window = np.abs(psd_freq - baseband) <= 2000
            # 零基带信号是直流，Welch 默认去均值后无法用谱峰表示。
            if baseband > 0 and np.any(window):
                peak = np.flatnonzero(window)[int(np.argmax(psd[window]))]
                peaks[index] = float(np.sqrt(psd[peak]))
                peak_freqs[index] = float(psd_freq[peak])
            remaining = (time.monotonic() - started) / (index + 1) * (freqs.size - index - 1)
            report_runtime_progress(
                "acquire", f"已完成 {index + 1}/{freqs.size} 个频率点",
                15 + 75 * (index + 1) / freqs.size,
                estimated_remaining_seconds=remaining,
            )

        np.savez(
            directory.raw / "frequency_response.npz",
            am_frequencies_hz=freqs, peak_amplitudes=peaks,
            peak_frequencies_hz=peak_freqs, actual_rate=actual_rate,
        )
        (directory.results / "results_summary.yaml").write_text(
            yaml.safe_dump({
                "am_frequencies_hz": freqs.tolist(),
                "peak_amplitudes": peaks.tolist(),
                "peak_frequencies_hz": peak_freqs.tolist(),
                "actual_rate_sa_s": float(actual_rate),
            }, allow_unicode=True),
            encoding="utf-8",
        )
        directory.update_config(
            data_files=[f"raw/waveform_{i:04d}.npy" for i in range(freqs.size)]
            + ["raw/frequency_response.npz"],
            completion_status="completed",
        )
        report_runtime_progress("complete", "采集完成", 100)
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, (WorkflowCancelled, KeyboardInterrupt)) else "failed"
        directory.update_config(completion_status=status, failure_reason=str(exc))
        raise
