"""T2 逐帧采集、自动量程与恢复后保存；完整与分步共用。"""
from __future__ import annotations
from lab_workflows.steps import initialize_lockin_inputs
from lab_workflows.devices import create_lockin
from lab_workflows.lockin_compatibility import require_lockin_compatibility
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import numpy as np
from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, HF2Instrument, SignalInputConfig, demod
from sds_acquisition import AcquisitionConfig, ChannelConfig, SDSAcquisition, SDSInstrument, TriggerConfig
from tec_controller import TECInstrument
from ...common import find_project_root, load_mapping, validate_safety_limit
from ...devices import create_signal_generator
from ...experiment_runtime import check_cancelled, load_runtime_params
from ...steps import DeferredAcquisitionWrites, acquisition_window, DGChannelShutdown, DeviceSession, DisconnectTarget, STANDARD_PRESERVED_OUTPUTS, SafetyShutdownReport, ShutdownAction, TemperatureSwitchRestore, configure_temperature_control, connect_signal_generator_routes, create_run_directory, run_safety_shutdown, set_temperature_switch, synchronize_connected_clocks, wait_for_temperature_stable
from .models import T2CalibrationParams

@dataclass(slots=True)
class _AutoRangeState:
    """在整个 Probe 功率扫描期间保持示波器自动量程状态。"""
    scale: float

def _acquire_fid(params: T2CalibrationParams, probe_power: float, devices: dict[str, Any], channels: dict[str, int], scope_config: AcquisitionConfig, auto_range: _AutoRangeState, *, recovery_dir: Path | None=None, wait, switch, cancel) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    cancel()
    laser = devices['laser']
    scope: SDSInstrument = devices['scope']
    acquirer: SDSAcquisition = devices['acquirer']
    temp_switch = devices['temp_switch']
    validate_safety_limit('Probe_laser_power', probe_power)
    laser.setup_dc(probe_power, channel=channels['probe'])
    if recovery_dir is not None:
        recovery_dir.mkdir(parents=True, exist_ok=True)
    waveforms: list[np.ndarray] = []
    scales: list[float] = []
    time_axis: np.ndarray | None = None
    current_scale = auto_range.scale
    for repeat in range(params.acq_repeats):
        cancel()
        result = None
        for retry in range(3):
            cancel()
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(lambda enabled: switch(temp_switch, enabled, channel=channels['temp_switch']), wait, params.system_settle_s):
                    if not scope.wait_for_trigger(timeout=10.0):
                        print(f'''第 {repeat + 1} 次触发超时，跳过''')
                        break
                    cancel()
                    wait(scope_config.acquire_delay)
                    scope.trigger_stop()
                    result = acquirer.acquire_channel(params.scope_pd_channel, scope_config.timebase_scale, scope_config.horizontal_divisions, trim_points=0)
                    frame_time = np.asarray(result.time, dtype=float).copy()
                    frame_voltage = np.asarray(result.voltage, dtype=float).copy()
                    if recovery_dir is not None:
                        pending.add(np.savez, recovery_dir / f'''frame_r{repeat:03d}_a{retry}.npz''', time_s=frame_time, waveform_v=frame_voltage, probe_power_v=probe_power, scale_v_div=current_scale, repeat_index=repeat, attempt_index=retry)
            abs_max = float(np.max(np.abs(frame_voltage)))
            full_scale = current_scale * params.vert_divs
            need_retry = False
            if abs_max < 0.4 * full_scale and current_scale > params.scale_min * 2:
                current_scale /= 2
                auto_range.scale = current_scale
                scope.set_channel_scale(params.scope_pd_channel, current_scale)
                need_retry = True
            if abs_max > 0.9 * full_scale and current_scale < params.scale_max / 2:
                current_scale *= 2
                auto_range.scale = current_scale
                scope.set_channel_scale(params.scope_pd_channel, current_scale)
                need_retry = True
            if need_retry and retry < 2:
                scope.trigger_run()
                wait(0.02)
                result = None
                continue
            break
        if result is None:
            scope.trigger_run()
            wait(0.02)
            continue
        waveforms.append(frame_voltage)
        scales.append(float(current_scale))
        if time_axis is None:
            time_axis = frame_time
        scope.trigger_run()
        wait(0.02)
    if not waveforms or time_axis is None:
        raise RuntimeError(f'''Probe={probe_power:.6g} V 未采集到有效 FID 波形''')
    waveform_array = np.asarray(waveforms)
    if time_axis.size < 2:
        raise RuntimeError(f'''Probe={probe_power:.6g} V 的示波器记录不含足够的时间轴数据''')
    sample_interval = float(np.median(np.diff(time_axis)))
    trigger_tolerance = abs(sample_interval) * 0.25
    mask = time_axis >= -trigger_tolerance
    if np.count_nonzero(mask) < 8:
        raise RuntimeError(f'''Probe={probe_power:.6g} V 的示波器记录不含足够的触发后 FID 数据''')
    fid_time = time_axis[mask].copy()
    fid_time[np.abs(fid_time) <= trigger_tolerance] = 0.0
    fid_waveforms = waveform_array[:, mask]
    return (fid_time, fid_waveforms, np.mean(fid_waveforms, axis=0), scales)

def acquire_scan(ctx):
    params, devices, channels = ctx.params, ctx.devices, ctx.channels
    scope_config, auto_range, run_dir = ctx.scope_config, ctx.auto_range, ctx.directory
    data_files = []
    if params.do_power_scan:
        records: list[dict[str, Any]] = []
        probe_power_values = params.probe_power_values
        for index, power in enumerate(probe_power_values):
            check_cancelled()
            print(f'''Probe 功率点 {index + 1}/{len(probe_power_values)}: {power:.3f} V''')
            time_fid, waveforms_fid, average, scales = ctx.acquire_fid(params, power, devices, channels, scope_config, auto_range, recovery_dir=run_dir.raw / 'recovery' / f'''p{index:02d}''')
            actual_sample_rate = 1.0 / float(np.median(np.diff(time_fid)))
            point_name = f'''fid_p{index:02d}.npz'''
            np.savez(run_dir.raw / point_name, time=time_fid, avg_waveform=average, waveforms=waveforms_fid, probe_power=power, scale_used=scales, sample_rate=actual_sample_rate, burst_duration=params.burst_duration, rf_gate_freq=params.rf_gate_freq, rf_gate_duty=params.rf_gate_duty, trigger_slope=params.scope_trig_slope)
            data_files.append(f'''raw/{point_name}''')
            records.append({'time': time_fid, 'average': average, 'power': power, 'scales': scales, 'sample_rate': actual_sample_rate})
        np.savez(run_dir.raw / 'power_scan_results.npz', probe_power=[item['power'] for item in records], scale_used=[item['scales'][-1] for item in records])
        np.savez(run_dir.raw / 'power_scan_waveforms.npz', t_axis=records[0]['time'], avg_stack=np.asarray([item['average'] for item in records]), probe_power=[item['power'] for item in records], sample_rate=records[0]['sample_rate'])
        data_files.extend(['raw/power_scan_results.npz', 'raw/power_scan_waveforms.npz'])
    else:
        time_fid, waveforms_fid, average, scales = ctx.acquire_fid(params, params.probe_power, devices, channels, scope_config, auto_range, recovery_dir=run_dir.raw / 'recovery' / 'single')
        actual_sample_rate = 1.0 / float(np.median(np.diff(time_fid)))
        np.savez(run_dir.raw / 'fid_waveforms.npz', waveforms=waveforms_fid, avg_waveform=average, time=time_fid, sample_rate=actual_sample_rate, burst_duration=params.burst_duration, rf_gate_freq=params.rf_gate_freq, rf_gate_duty=params.rf_gate_duty, trigger_slope=params.scope_trig_slope, acq_repeats=params.acq_repeats, scale_used=np.asarray(scales), pd_channel=params.scope_pd_channel, trig_channel=params.scope_trig_channel)
        data_files.append('raw/fid_waveforms.npz')
    return data_files
