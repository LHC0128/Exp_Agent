"""本实验嵌套扫描与恢复后保存，保留既有原始数据键。"""
from lab_workflows.steps import acquisition_window, DeferredAcquisitionWrites
import numpy as np
from lab_workflows.steps import acquisition_window
from lab_workflows.steps.run_directory import write_run_config
import math
import json
from tqdm import tqdm
from lab_workflows.experiment_runtime import check_cancelled
from lockin_amplifier import DAQConfig, OscillatorConfig, DemodulatorConfig, demod, daq
from lockin_amplifier import result_rate, acquisition_metadata
from functools import partial
from lab_workflows.common import validate_safety_limit
from .excitation import apply_z_rf_phase

def acquire_scan(ctx):
    print('\n' + '=' * 60)
    print('Phase 3: 频率 × 相位 嵌套扫描')
    print('=' * 60)
    print(f'''采集模式: {ctx.ACQUISITION_MODE}''')
    print(f'''固定 Z 射频场幅度: {ctx.Z_RF_AMPLITUDE} Vpp''')
    print(f'''频率扫描: {ctx.freq_list[0]:.1f} ~ {ctx.freq_list[-1]:.1f} Hz, {len(ctx.freq_list)} 点''')
    if ctx.has_baseline:
        print(f'''  包含 0 Hz 基线点''')
    print(f'''相位扫描: {ctx.phase_list[0]:.1f} ~ {ctx.phase_list[-1]:.1f} deg, {len(ctx.phase_list)} 点''')
    print(f'''总计: {len(ctx.freq_list) * len(ctx.phase_list)} 个测量点''')
    ctx.r_matrix = np.full((len(ctx.freq_list), len(ctx.phase_list)), np.nan)
    ctx.x_matrix = np.full((len(ctx.freq_list), len(ctx.phase_list)), np.nan)
    ctx.y_matrix = np.full((len(ctx.freq_list), len(ctx.phase_list)), np.nan)
    ctx.theta_matrix = np.full((len(ctx.freq_list), len(ctx.phase_list)), np.nan)
    ctx.freq_index_records = []
    for ctx.fi, ctx.z_rf_freq in enumerate(tqdm(ctx.freq_list, desc='频率扫描')):
        check_cancelled()
        ctx.is_baseline = ctx.z_rf_freq == 0.0
        ctx.record = {'idx': ctx.fi, 'freq_Hz': float(ctx.z_rf_freq), 'is_baseline': bool(ctx.is_baseline), 'z_drive_amplitude_Vpp': ctx.Z_RF_AMPLITUDE, 'acquisition_mode': ctx.ACQUISITION_MODE}
        if ctx.is_baseline:
            validate_safety_limit('Z_magnetic_field', 0.0, ctx.LIMITS)
            ctx.dg_sweep.setup_dc(0.0, channel=1)
            ctx.dg_sweep.set_burst_state(False, channel=1)
            ctx.dg_sweep.set_output(False, channel=1)
        else:
            ctx.dg_sweep.set_burst_state(False, channel=1)
            ctx.dg_sweep.set_mod_state(False, channel=1)
            ctx.dg_sweep.setup_sine(freq=ctx.z_rf_freq, amplitude=ctx.Z_RF_AMPLITUDE, offset=0.0, phase=0.0, channel=1)
            ctx.dg_sweep.set_phase_adjust(0.0, channel=1)
            ctx.dg_sweep.set_output(True, channel=1)
        if ctx.ACQUISITION_MODE in ('demod_rxy', 'both') and (not ctx.is_baseline):
            ctx.osc1_cfg = OscillatorConfig(osc_index=ctx.DEMOD3_OSC_IDX, frequency=ctx.z_rf_freq)
            demod.configure_oscillator(ctx.hfi, ctx.osc1_cfg)
            ctx.tc = max(ctx.DEMOD3_TC_MIN_s, ctx.DEMOD3_TC_PERIOD_FRACTION / max(ctx.z_rf_freq, 1.0))
            ctx.actual_demod3_rate = min(ctx.DEMOD3_RATE, int(max(ctx.z_rf_freq, 1.0) * 50))
            ctx.demod3_cfg = DemodulatorConfig(demod_index=ctx.DEMOD3_IDX, enable=True, rate=max(100, ctx.actual_demod3_rate), input_channel=ctx.DEMOD3_ADC_SELECT, osc_select=ctx.DEMOD3_OSC_IDX, harmonic=1, time_constant=ctx.tc, order=ctx.DEMOD3_ORDER, phase=0.0)
            demod.configure_demodulator(ctx.hfi, ctx.demod3_cfg)
            ctx.point_rate = ctx.hfi.get_double(f'''{ctx.demod_r_actual_path}/rate''')
            ctx.point_tc = ctx.hfi.get_double(f'''{ctx.demod_r_actual_path}/timeconstant''')
            ctx.point_adcselect = ctx.hfi.get_int(f'''{ctx.demod_r_actual_path}/adcselect''')
            ctx.record['demod_r_actual_tc_s'] = float(ctx.point_tc)
            ctx.record['demod_r_actual_rate_Sa_s'] = float(ctx.point_rate)
            ctx.record['demod_r_adcselect_actual'] = int(ctx.point_adcselect)
        for ctx.pi, ctx.phase_deg in enumerate(ctx.phase_list):
            check_cancelled()
            if not ctx.is_baseline:
                apply_z_rf_phase(ctx, ctx.dg_sweep, ctx.phase_deg)
            ctx.x_list, ctx.y_list, ctx.r_list, ctx.theta_list = ([], [], [], [])
            for key in list(ctx.record):
                if key in ('time_s', 'y_V', 'actual_rate_Sa_s') or (key.startswith('demod_r_') and key not in ('demod_r_actual_tc_s', 'demod_r_actual_rate_Sa_s', 'demod_r_adcselect_actual')):
                    del ctx.record[key]
            with DeferredAcquisitionWrites() as pending:
                with acquisition_window(lambda enabled: ctx.switch(enabled), ctx.wait, ctx.SYSTEM_SETTLE_S):
                    try:
                        if ctx.ACQUISITION_MODE in ('yfft', 'both'):
                            ctx.daq_cfg = DAQConfig(device=ctx.MAPPING['lockin_r']['device_id'], trigger_type=0, duration=ctx.POINT_DURATION_s, grid_cols=int(ctx.actual_rate_d0_meas * ctx.POINT_DURATION_s), grid_rows=1, grid_mode=2, signal_paths=['sample.y'])
                            ctx.daq_results = daq.acquire_data(ctx.hfi, config=ctx.daq_cfg, demod_idx=ctx.DEMOD0_IDX, actual_rate=ctx.actual_rate_d0_meas, cancellation_check=check_cancelled, requested_rate=ctx.DEMOD0_RATE, timeout=ctx.POINT_DURATION_s + 5.0)
                            ctx.actual_rate_d0_meas = result_rate(ctx.daq_results, ctx.actual_rate_d0_meas)
                            ctx.record.update(acquisition_metadata(ctx.daq_results))
                            ctx.y_signal = next((r.values for r in ctx.daq_results if r.signal_name == 'sample.y'), None)
                            ctx.t_signal = ctx.daq_results[0].time if ctx.daq_results else np.array([])
                            if ctx.y_signal is not None and len(ctx.y_signal) > 0:
                                ctx.record['time_s'] = ctx.t_signal
                                ctx.record['y_V'] = ctx.y_signal
                                ctx.record['actual_rate_Sa_s'] = float(ctx.actual_rate_d0_meas)
                        if ctx.ACQUISITION_MODE in ('demod_rxy', 'both') and (not ctx.is_baseline):
                            ctx.x_list, ctx.y_list, ctx.r_list, ctx.theta_list = ([], [], [], [])
                            for ctx._ in range(ctx.DEMOD3_N_AVG):
                                ctx.s = demod.read_demod_sample(ctx.hfi, demod_idx=ctx.DEMOD3_IDX)
                                ctx.x_list.append(ctx.s['x'])
                                ctx.y_list.append(ctx.s['y'])
                                ctx.r_list.append(ctx.s['r'])
                                ctx.theta_list.append(ctx.s['theta'])
                                ctx.wait(ctx.DEMOD3_AVG_INTERVAL_s)
                            ctx.x_arr = np.asarray(ctx.x_list, dtype=float)
                            ctx.y_arr = np.asarray(ctx.y_list, dtype=float)
                            ctx.r_arr = np.asarray(ctx.r_list, dtype=float)
                            ctx.theta_arr = np.asarray(ctx.theta_list, dtype=float)
                            ctx.x_mean = float(np.mean(ctx.x_arr))
                            ctx.y_mean = float(np.mean(ctx.y_arr))
                            ctx.r_vec = float(math.hypot(ctx.x_mean, ctx.y_mean))
                            ctx.phase_vec_rad = float(math.atan2(ctx.y_mean, ctx.x_mean))
                            ctx.phase_vec_deg = float(math.degrees(ctx.phase_vec_rad))
                            ctx.r_scalar_mean = float(np.mean(ctx.r_arr))
                            ctx.r_scalar_std = float(np.std(ctx.r_arr))
                            ctx.r_matrix[ctx.fi, ctx.pi] = ctx.r_vec
                            ctx.x_matrix[ctx.fi, ctx.pi] = ctx.x_mean
                            ctx.y_matrix[ctx.fi, ctx.pi] = ctx.y_mean
                            ctx.theta_matrix[ctx.fi, ctx.pi] = ctx.phase_vec_deg
                            ctx.record['demod_r_x_values_V'] = ctx.x_arr
                            ctx.record['demod_r_y_values_V'] = ctx.y_arr
                            ctx.record['demod_r_r_values_V'] = ctx.r_arr
                            ctx.record['demod_r_theta_values_rad'] = ctx.theta_arr
                            ctx.record['demod_r_theta_values_deg'] = np.degrees(ctx.theta_arr)
                            ctx.record['demod_r_x_mean_V'] = ctx.x_mean
                            ctx.record['demod_r_y_mean_V'] = ctx.y_mean
                            ctx.record['demod_r_r_vector_mean_V'] = ctx.r_vec
                            ctx.record['demod_r_r_mean_scalar_V'] = ctx.r_scalar_mean
                            ctx.record['demod_r_r_std_scalar_V'] = ctx.r_scalar_std
                            ctx.record['demod_r_phase_vector_rad'] = ctx.phase_vec_rad
                            ctx.record['demod_r_phase_vector_deg'] = ctx.phase_vec_deg
                    finally:
                        if ctx.x_list:
                            ctx.record.update(demod_r_x_values_V=np.asarray(ctx.x_list), demod_r_y_values_V=np.asarray(ctx.y_list), demod_r_r_values_V=np.asarray(ctx.r_list), demod_r_theta_values_rad=np.asarray(ctx.theta_list))
                        if 'y_V' in ctx.record or 'demod_r_x_values_V' in ctx.record:
                            pending.add(np.savez, ctx.raw_dir / 'recovery' / f'''freq_{ctx.fi:04d}_phase_{ctx.pi:03d}.npz''', **ctx.record)
            ctx.wait(ctx.TEMP_SWITCH_ON_LAG)
        ctx.fname = f'''freq_{ctx.fi:04d}_{ctx.z_rf_freq:.3f}Hz'''
        if ctx.is_baseline:
            ctx.fname = f'''freq_{ctx.fi:04d}_0Hz_BASELINE'''
        ctx.save_dict = {'freq_Hz': np.float64(ctx.z_rf_freq), 'is_baseline': np.uint8(int(ctx.is_baseline)), 'z_drive_amplitude_Vpp': np.float64(ctx.Z_RF_AMPLITUDE), 'acquisition_mode': ctx.ACQUISITION_MODE}
        if 'y_V' in ctx.record:
            ctx.save_dict['time_s'] = ctx.record['time_s']
            ctx.save_dict['y_V'] = ctx.record['y_V']
            ctx.save_dict['actual_rate_Sa_s'] = np.float64(ctx.record['actual_rate_Sa_s'])
        if 'demod_r_r_vector_mean_V' in ctx.record:
            ctx.save_dict['demod_r_x_values_V'] = ctx.record['demod_r_x_values_V']
            ctx.save_dict['demod_r_y_values_V'] = ctx.record['demod_r_y_values_V']
            ctx.save_dict['demod_r_r_values_V'] = ctx.record['demod_r_r_values_V']
            ctx.save_dict['demod_r_theta_values_rad'] = ctx.record['demod_r_theta_values_rad']
            ctx.save_dict['demod_r_theta_values_deg'] = ctx.record['demod_r_theta_values_deg']
            ctx.save_dict['demod_r_x_mean_V'] = np.float64(ctx.record['demod_r_x_mean_V'])
            ctx.save_dict['demod_r_y_mean_V'] = np.float64(ctx.record['demod_r_y_mean_V'])
            ctx.save_dict['demod_r_r_vector_mean_V'] = np.float64(ctx.record['demod_r_r_vector_mean_V'])
            ctx.save_dict['demod_r_r_mean_scalar_V'] = np.float64(ctx.record['demod_r_r_mean_scalar_V'])
            ctx.save_dict['demod_r_r_std_scalar_V'] = np.float64(ctx.record['demod_r_r_std_scalar_V'])
            ctx.save_dict['demod_r_phase_vector_rad'] = np.float64(ctx.record['demod_r_phase_vector_rad'])
            ctx.save_dict['demod_r_phase_vector_deg'] = np.float64(ctx.record['demod_r_phase_vector_deg'])
            if 'demod_r_actual_tc_s' in ctx.record:
                ctx.save_dict['demod_r_actual_tc_s'] = np.float64(ctx.record['demod_r_actual_tc_s'])
                ctx.save_dict['demod_r_actual_rate_Sa_s'] = np.float64(ctx.record['demod_r_actual_rate_Sa_s'])
                ctx.save_dict['demod_r_adcselect_actual'] = np.int32(ctx.record['demod_r_adcselect_actual'])
        ctx.fpath = ctx.raw_dir / f'''{ctx.fname}.npz'''
        np.savez(ctx.fpath, **ctx.save_dict)
        ctx.freq_index_records.append({'idx': ctx.fi, 'freq_Hz': float(ctx.z_rf_freq), 'is_baseline': bool(ctx.is_baseline), 'file': ctx.fpath.name})
        if (ctx.fi + 1) % 10 == 0 or ctx.fi == 0 or ctx.fi == len(ctx.freq_list) - 1:
            ctx.extra = ''
            if ctx.ACQUISITION_MODE in ('demod_rxy', 'both') and (not ctx.is_baseline):
                ctx.r_vec_val = ctx.r_matrix[ctx.fi, :]
                if not np.all(np.isnan(ctx.r_vec_val)):
                    ctx.extra = f''', R_vec={np.nanmean(ctx.r_vec_val):.4e} V, φ={np.nanmean(ctx.theta_matrix[ctx.fi, :]):.1f}°'''
            ctx.y_n = len(ctx.record.get('y_V', []))
            ctx.extra += f''', y_n={ctx.y_n}'''
            ctx.tag = ' (BASELINE)' if ctx.is_baseline else ''
            print(f'''  [{ctx.fi + 1}/{len(ctx.freq_list)}] f={ctx.z_rf_freq:.2f} Hz{ctx.tag}{ctx.extra}''')
    np.savez(ctx.raw_dir / 'frequency_index.npz', **{'records': np.array(ctx.freq_index_records, dtype=object)})
    print(f'''\n频点索引已保存: {ctx.raw_dir / 'frequency_index.npz'}''')
    with open(ctx.raw_dir / 'frequency_index.json', 'w', encoding='utf-8') as ctx.f:
        json.dump(ctx.freq_index_records, ctx.f, indent=2, ensure_ascii=False)
    print(f'''频点索引 (JSON): {ctx.raw_dir / 'frequency_index.json'}''')
    ctx.freq_resp_data = {'freq_Hz': ctx.freq_list, 'phase_deg': ctx.phase_list, 'r_matrix_V': ctx.r_matrix, 'x_matrix_V': ctx.x_matrix, 'y_matrix_V': ctx.y_matrix, 'theta_matrix_deg': ctx.theta_matrix}
    np.savez(ctx.raw_dir / 'freq_phase_scan.npz', **ctx.freq_resp_data)
    print(f'''数据已保存: {ctx.raw_dir / 'freq_phase_scan.npz'}''')
    print(f'''  频率: {len(ctx.freq_list)} 点, 相位: {len(ctx.phase_list)} 点''')
    print(f'''  R 矩阵形状: {ctx.r_matrix.shape}''')
    print(f'''  R 范围: [{np.nanmin(ctx.r_matrix):.4e}, {np.nanmax(ctx.r_matrix):.4e}] V''')
    ctx.config['freq_sweep']['actual_freq_list_Hz'] = ctx.freq_list.tolist()
    ctx.config['phase_sweep']['actual_phase_list_deg'] = ctx.phase_list.tolist()
    write_run_config(ctx.config_path, ctx.config)
    print('\n✅ 频率 × 相位 嵌套扫描完成')
