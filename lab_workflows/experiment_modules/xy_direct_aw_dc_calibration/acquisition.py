"""本实验嵌套扫描与恢复后保存，保留既有原始数据键。"""
from lab_workflows.steps import acquisition_window, DeferredAcquisitionWrites
import math
import numpy as np
from lab_workflows.steps.run_directory import write_run_config
from tqdm import tqdm
from lab_workflows.experiment_runtime import check_cancelled
from lockin_amplifier import DAQConfig, OscillatorConfig, DemodulatorConfig, demod, daq
from lockin_amplifier import result_rate, acquisition_metadata
from functools import partial
from lab_workflows.common import validate_safety_limit
from .excitation import save_waveform_snapshot, update_json_index, upload_direct_aw

def acquire_scan(ctx):
    ctx.global_index_records = []
    ctx.index_path = ctx.raw_dir / 'frequency_index.json'
    for ctx.v_idx, ctx.envelope_v in enumerate(tqdm(ctx.XY_ENV_VOLTAGE_LIST_V, desc='DirectAW envelope scan')):
        check_cancelled()
        ctx.wave = upload_direct_aw(ctx, float(ctx.envelope_v), ctx.xy_phase_deg, output=True)
        ctx.wait(ctx.SYSTEM_SETTLE_S)
        ctx.sign_tag = 'm' if ctx.envelope_v < 0 else 'p'
        ctx.voltage_tag = f'''env_v{ctx.v_idx:02d}_{ctx.sign_tag}{abs(ctx.envelope_v):.4f}V'''
        ctx.voltage_dir = ctx.raw_dir / ctx.voltage_tag
        ctx.voltage_dir.mkdir(exist_ok=True)
        save_waveform_snapshot(ctx, ctx.voltage_dir / 'direct_aw_waveform.npz', ctx.wave)
        ctx.voltage_records = []
        print(f'''\n[{ctx.v_idx + 1}/{len(ctx.XY_ENV_VOLTAGE_LIST_V)}] DirectAW envelope={ctx.envelope_v:+.4f} V''')
        for ctx.z_idx, ctx.z_freq in enumerate(ctx.Z_RF_FREQ_LIST_Hz):
            check_cancelled()
            ctx.dg_sweep.set_burst_state(False, channel=1)
            ctx.dg_sweep.set_mod_state(False, channel=1)
            ctx.dg_sweep.setup_sine(freq=float(ctx.z_freq), amplitude=ctx.Z_RF_AMPLITUDE_Vpp, offset=0.0, phase=0.0, channel=1)
            ctx.dg_sweep.set_phase_adjust(0.0, channel=1)
            ctx.dg_sweep.set_output(True, channel=1)
            ctx.point_rate = np.nan
            ctx.point_tc = np.nan
            ctx.point_adcselect = -1
            if ctx.ACQUISITION_MODE in ('demod_rxy', 'both'):
                demod.configure_oscillator(ctx.hfi, OscillatorConfig(osc_index=ctx.DEMOD3_OSC_IDX, frequency=float(ctx.z_freq)))
                demod.configure_demodulator(ctx.hfi, DemodulatorConfig(demod_index=ctx.DEMOD3_IDX, enable=True, rate=max(100.0, min(ctx.DEMOD3_RATE_Sa_s, float(ctx.z_freq) * 50.0)), input_channel=ctx.DEMOD3_ADC_SELECT, osc_select=ctx.DEMOD3_OSC_IDX, harmonic=1, time_constant=max(ctx.DEMOD3_TC_MIN_s, ctx.DEMOD3_TC_PERIOD_FRACTION / float(ctx.z_freq)), order=ctx.DEMOD3_ORDER, phase=0.0))
                ctx.demod3_path = ctx.hfi.demod_path(ctx.DEMOD3_IDX)
                ctx.point_rate = ctx.hfi.get_double(f'''{ctx.demod3_path}/rate''')
                ctx.point_tc = ctx.hfi.get_double(f'''{ctx.demod3_path}/timeconstant''')
                ctx.point_adcselect = ctx.hfi.get_int(f'''{ctx.demod3_path}/adcselect''')
            ctx.point = {}
            ctx.samples = []
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(ctx.switch, ctx.wait, ctx.SYSTEM_SETTLE_S):
                    try:
                        if ctx.ACQUISITION_MODE in ('daq_y', 'both'):
                            ctx.daq_cfg = DAQConfig(device=ctx.MAPPING['lockin_r']['device_id'], trigger_type=0, duration=ctx.POINT_DURATION_s, grid_cols=int(ctx.actual_rate_d0_meas * ctx.POINT_DURATION_s), grid_rows=1, grid_mode=2, signal_paths=['sample.y'])
                            ctx.daq_results = daq.acquire_data(ctx.hfi, config=ctx.daq_cfg, demod_idx=ctx.DEMOD0_IDX, actual_rate=ctx.actual_rate_d0_meas, cancellation_check=check_cancelled, requested_rate=ctx.DEMOD0_RATE_Sa_s, timeout=ctx.POINT_DURATION_s + 5.0)
                            ctx.actual_rate_d0_meas = result_rate(ctx.daq_results, ctx.actual_rate_d0_meas)
                            ctx.point.update(acquisition_metadata(ctx.daq_results))
                            ctx.y_signal = next((res.values for res in ctx.daq_results if res.signal_name == 'sample.y'), None)
                            if ctx.y_signal is not None:
                                ctx.point['y_V'] = np.asarray(ctx.y_signal, dtype=float)
                                ctx.point['time_s'] = np.asarray(ctx.daq_results[0].time, dtype=float)
                        if ctx.ACQUISITION_MODE in ('demod_rxy', 'both'):
                            ctx.samples = []
                            for ctx._ in range(ctx.DEMOD3_N_AVG):
                                ctx.samples.append(demod.read_demod_sample(ctx.hfi, demod_idx=ctx.DEMOD3_IDX))
                                ctx.wait(ctx.DEMOD3_AVG_INTERVAL_s)
                            ctx.x_arr = np.asarray([sample['x'] for sample in ctx.samples], dtype=float)
                            ctx.y_arr = np.asarray([sample['y'] for sample in ctx.samples], dtype=float)
                            ctx.r_arr = np.asarray([sample['r'] for sample in ctx.samples], dtype=float)
                            ctx.theta_arr = np.asarray([sample['theta'] for sample in ctx.samples], dtype=float)
                            ctx.x_mean = float(np.mean(ctx.x_arr))
                            ctx.y_mean = float(np.mean(ctx.y_arr))
                            ctx.point.update({'demod3_x_values_V': ctx.x_arr, 'demod3_y_values_V': ctx.y_arr, 'demod3_r_values_V': ctx.r_arr, 'demod3_theta_values_rad': ctx.theta_arr, 'demod3_x_mean_V': np.float64(ctx.x_mean), 'demod3_y_mean_V': np.float64(ctx.y_mean), 'demod3_r_vector_mean_V': np.float64(math.hypot(ctx.x_mean, ctx.y_mean)), 'demod3_r_scalar_mean_V': np.float64(np.mean(ctx.r_arr)), 'demod3_r_scalar_std_V': np.float64(np.std(ctx.r_arr)), 'demod3_phase_vector_deg': np.float64(math.degrees(math.atan2(ctx.y_mean, ctx.x_mean)))})
                    finally:
                        if ctx.samples:
                            ctx.point.update(demod3_x_values_V=np.asarray([s['x'] for s in ctx.samples]), demod3_y_values_V=np.asarray([s['y'] for s in ctx.samples]), demod3_r_values_V=np.asarray([s['r'] for s in ctx.samples]), demod3_theta_values_rad=np.asarray([s['theta'] for s in ctx.samples]))
                        if ctx.point:
                            pending.add(np.savez, ctx.raw_dir / 'recovery' / f'''env_{ctx.v_idx:03d}_freq_{ctx.z_idx:04d}.npz''', xy_envelope_voltage_V=ctx.envelope_v, z_freq_Hz=ctx.z_freq, **ctx.point)
            ctx.wait(ctx.TEMP_SWITCH_ON_LAG_s)
            ctx.filename = f'''zfreq_{ctx.z_idx:04d}_{ctx.z_freq:.3f}Hz.npz'''
            ctx.save_dict = {'xy_envelope_voltage_V': np.float64(ctx.envelope_v), 'z_freq_Hz': np.float64(ctx.z_freq), 'z_drive_amplitude_Vpp': np.float64(ctx.Z_RF_AMPLITUDE_Vpp), 'acquisition_mode': np.array(ctx.ACQUISITION_MODE), 'actual_rate_Sa_s': np.float64(ctx.actual_rate_d0_meas), 'demod3_actual_rate_Sa_s': np.float64(ctx.point_rate), 'demod3_actual_tc_s': np.float64(ctx.point_tc), 'demod3_adcselect_actual': np.int32(ctx.point_adcselect), **ctx.point}
            np.savez(ctx.voltage_dir / ctx.filename, **ctx.save_dict)
            ctx.record = {'v_idx': int(ctx.v_idx), 'xy_envelope_voltage_V': float(ctx.envelope_v), 'z_idx': int(ctx.z_idx), 'z_freq_Hz': float(ctx.z_freq), 'subdir': ctx.voltage_tag, 'file': ctx.filename}
            ctx.voltage_records.append(ctx.record)
            ctx.global_index_records.append(ctx.record)
            update_json_index(ctx, ctx.voltage_dir / 'frequency_index.json', ctx.voltage_records)
            update_json_index(ctx, ctx.index_path, ctx.global_index_records)
            if ctx.z_idx == 0 or (ctx.z_idx + 1) % 10 == 0 or ctx.z_idx == len(ctx.Z_RF_FREQ_LIST_Hz) - 1:
                ctx.hw_r = ctx.point.get('demod3_r_vector_mean_V', np.nan)
                print(f'''  [{ctx.z_idx + 1}/{len(ctx.Z_RF_FREQ_LIST_Hz)}] f={ctx.z_freq:.0f} Hz, HW R={float(ctx.hw_r):.4e} V''')
        np.savez(ctx.voltage_dir / 'frequency_index.npz', records=np.array(ctx.voltage_records, dtype=object))
    np.savez(ctx.raw_dir / 'frequency_index.npz', records=np.array(ctx.global_index_records, dtype=object))
    ctx.config['data_files'] = ['raw/frequency_index.json', 'raw/frequency_index.npz']
    write_run_config(ctx.config_path, ctx.config)
    print('DirectAW DC 标定采集完成')
