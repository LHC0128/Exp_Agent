"""本实验锁相链路与 DirectAW 校相，完整与分步共用。"""
from lab_workflows.lockin_control import apply_settings
from lab_workflows.steps import acquisition_window
from lab_workflows.steps.run_directory import write_run_config
from lab_workflows.experiment_runtime import check_cancelled
from lab_workflows.steps import DirectAWPhaseCalibrationConfig, PhaseCalibrationConfig, calibrate_demod_phase, calibrate_direct_aw_phase
from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from functools import partial
from lab_workflows.common import validate_safety_limit
from .excitation import apply_phase_and_measure, configure_dg_am_reference_trigger, read_demod_theta_deg, save_xy_aw_waveforms, upload_xy_aw

def configure_lockin(ctx):
    print('=' * 60)
    print('Phase 1: HF2 配置 — Demod 0 校相 + Demod 0 Y → AuxOut2 → SigIn2 → Demod 3')
    print('=' * 60)
    print('关闭所有磁场...')
    print('  温度开关: OFF')
    ctx.dg_comp.set_output(False, channel=1)
    ctx.dg_comp.set_output(False, channel=2)
    print('  X/Y 场: OFF')
    ctx.dg_am.set_output(False, channel=1)
    ctx.dg_am.set_output(False, channel=2)
    print('  X/Y direct AW: OFF')
    ctx.dg_sweep.set_output(False, channel=1)
    print('  Z 场: OFF')
    ctx.wait(0.5)
    ctx.sig_cfg = SignalInputConfig(input_index=0, range=ctx.DEMOD0_SIGNAL_RANGE, diff=False)
    demod.configure_signal_input(ctx.hfi, ctx.sig_cfg)
    print(f'''信号输入 0: range={ctx.DEMOD0_SIGNAL_RANGE} V, AC 耦合''')
    ctx.osc0_cfg = OscillatorConfig(osc_index=ctx.DEMOD0_OSC_IDX, frequency=ctx.DEMOD0_OSC_FREQ)
    demod.configure_oscillator(ctx.hfi, ctx.osc0_cfg)
    print(f'''振荡器 0: {ctx.DEMOD0_OSC_FREQ} Hz''')
    ctx.demod0_cfg = DemodulatorConfig(demod_index=ctx.DEMOD0_IDX, enable=True, rate=ctx.DEMOD0_RATE, input_channel=0, osc_select=ctx.DEMOD0_OSC_IDX, harmonic=1, time_constant=ctx.DEMOD0_TC_CALIB, order=ctx.DEMOD0_ORDER, phase=0.0)
    ctx.actual_rate_d0 = demod.configure_demodulator(ctx.hfi, ctx.demod0_cfg)
    print(f'''Demod 0: TC={ctx.DEMOD0_TC_CALIB:.0e} s (校相), rate={ctx.actual_rate_d0:.0f} Sa/s''')
    ctx.sig_in2_cfg = SignalInputConfig(input_index=ctx.SIGIN2_INDEX, range=ctx.SIGIN2_RANGE, diff=False)
    demod.configure_signal_input(ctx.hfi, ctx.sig_in2_cfg)
    print(f'''信号输入 {ctx.SIGIN2_INDEX} (Signal Input 2, 物理回环接收端): range={ctx.SIGIN2_RANGE} V, {('AC' if ctx.SIGIN2_AC_COUPLING else 'DC')} 耦合, imp={ctx.SIGIN2_IMPEDANCE}Ω''')
    if ctx.CONFIGURE_AUXOUT2_TO_DEMOD0_Y:
        ctx.auxout_path = ctx.hfi.aux_out_path(ctx.AUXOUT_INDEX)
        apply_settings(ctx.hfi, ctx.MAPPING['lockin_r']['device_library_id'], ctx.AUXOUT_SOURCE_DEMOD_IDX,
            {'aux_output': {'index': ctx.AUXOUT_INDEX, 'demod_index': ctx.AUXOUT_SOURCE_DEMOD_IDX,
                'output_select': ctx.AUXOUT_SOURCE_SELECT, 'scale': ctx.AUXOUT_SCALE, 'offset_v': ctx.AUXOUT_OFFSET}},
            project_root=ctx.root)
        ctx.sig_type_str = {0: 'X', 1: 'Y', 2: 'R', 3: 'Theta'}[ctx.AUXOUT_SOURCE_SELECT]
        print(f'''Aux Out {ctx.AUXOUT_INDEX + 1} (auxouts/{ctx.AUXOUT_INDEX}): Demod{ctx.AUXOUT_SOURCE_DEMOD_IDX} {ctx.sig_type_str}, scale={ctx.AUXOUT_SCALE}, offset={ctx.AUXOUT_OFFSET} V''')
        ctx.auxout_actual_outputselect = ctx.hfi.get_int(f'''{ctx.auxout_path}/outputselect''')
        ctx.auxout_actual_scale = ctx.hfi.get_double(f'''{ctx.auxout_path}/scale''')
        ctx.auxout_actual_offset = ctx.hfi.get_double(f'''{ctx.auxout_path}/offset''')
        ctx.auxout_actual_demod = ctx.hfi.get_int(f'''{ctx.auxout_path}/demodselect''')
        ctx.auxout_actual_sig = ctx.auxout_actual_outputselect
        ctx.auxout_actual_sig_str = {0: 'X', 1: 'Y', 2: 'R', 3: 'Theta'}[ctx.auxout_actual_sig]
        print(f'''  读回: outputselect={ctx.auxout_actual_outputselect} (Demod{ctx.auxout_actual_demod} {ctx.auxout_actual_sig_str}), scale={ctx.auxout_actual_scale}, offset={ctx.auxout_actual_offset}''')
    else:
        ctx.auxout_actual_outputselect = None
        ctx.auxout_actual_scale = None
        ctx.auxout_actual_offset = None
        ctx.auxout_actual_demod = None
        ctx.auxout_actual_sig_str = None
    ctx.osc3_initial_freq = 1000.0
    ctx.osc1_initial_cfg = OscillatorConfig(osc_index=ctx.DEMOD3_OSC_IDX, frequency=ctx.osc3_initial_freq)
    demod.configure_oscillator(ctx.hfi, ctx.osc1_initial_cfg)
    print(f'''振荡器 1 (Demod 3 参考): 初值 {ctx.osc3_initial_freq} Hz''')
    ctx.demod3_initial_tc = max(ctx.DEMOD3_TC_MIN_s, ctx.DEMOD3_TC_PERIOD_FRACTION / ctx.osc3_initial_freq)
    ctx.demod3_initial_cfg = DemodulatorConfig(demod_index=ctx.DEMOD3_IDX, enable=True, rate=ctx.DEMOD3_RATE, input_channel=ctx.DEMOD3_ADC_SELECT, osc_select=ctx.DEMOD3_OSC_IDX, harmonic=1, time_constant=ctx.demod3_initial_tc, order=ctx.DEMOD3_ORDER, phase=0.0)
    ctx.actual_rate_d3 = demod.configure_demodulator(ctx.hfi, ctx.demod3_initial_cfg)
    print(f'''Demod {ctx.DEMOD3_IDX} (射频场, 初值): rate={ctx.actual_rate_d3:.0f} Sa/s, TC={ctx.demod3_initial_tc * 1000.0:.2f} ms, adcselect={ctx.DEMOD3_ADC_SELECT} (= Signal Input 2, 物理回环)''')
    print('\n正在进行 Demod 0 相位校准...')
    ctx.wait(0.5)
    with acquisition_window(lambda enabled: ctx.switch(enabled), ctx.wait, ctx.SYSTEM_SETTLE_S):
        ctx.demod0_phase_result = calibrate_demod_phase(ctx.hfi, PhaseCalibrationConfig(demod_idx=ctx.DEMOD0_IDX, tolerance_deg=1.0, max_attempts=5, settle_time=ctx.SYSTEM_SETTLE_S))
        ctx.calibrated_phase_0 = ctx.demod0_phase_result.phase_shift_deg
        print(f'''Demod 0 校准完成: phaseshift = {ctx.calibrated_phase_0:.2f}°''')
        ctx.sample = ctx.demod0_phase_result.after_sample
        print(f'''  校准后样本: R={ctx.sample['r']:.6f}, X={ctx.sample['x']:.6f}, Y={ctx.sample['y']:.6f}''')
    print('温度开关已恢复')
    ctx.demod0_calib_hold_cfg = DemodulatorConfig(demod_index=ctx.DEMOD0_IDX, enable=True, rate=ctx.DEMOD0_RATE, input_channel=0, osc_select=ctx.DEMOD0_OSC_IDX, harmonic=1, time_constant=ctx.DEMOD0_TC_CALIB, order=ctx.DEMOD0_ORDER, phase=ctx.calibrated_phase_0)
    ctx.actual_rate_d0_calib = demod.configure_demodulator(ctx.hfi, ctx.demod0_calib_hold_cfg)
    print(f'''Demod 0 (保持校相 TC): rate={ctx.actual_rate_d0_calib:.0f} Sa/s, TC={ctx.DEMOD0_TC_CALIB * 1000.0:.1f} ms, phase={ctx.calibrated_phase_0:.2f}°''')
    ctx.sigin2_actual_path = ctx.hfi.sig_in_path(ctx.SIGIN2_INDEX)
    ctx.sigin2_actual_range = ctx.hfi.get_double(f'''{ctx.sigin2_actual_path}/range''')
    ctx.sigin2_actual_ac = ctx.hfi.get_int(f'''{ctx.sigin2_actual_path}/ac''')
    ctx.sigin2_actual_imp50 = ctx.hfi.get_int(f'''{ctx.sigin2_actual_path}/imp50''')
    ctx.demod_r_actual_path = ctx.hfi.demod_path(ctx.DEMOD3_IDX)
    ctx.demod_r_actual_adcselect = ctx.hfi.get_int(f'''{ctx.demod_r_actual_path}/adcselect''')
    ctx.demod_r_actual_rate = ctx.hfi.get_double(f'''{ctx.demod_r_actual_path}/rate''')
    ctx.demod_r_actual_tc = ctx.hfi.get_double(f'''{ctx.demod_r_actual_path}/timeconstant''')
    ctx.demod_r_actual_order = ctx.hfi.get_int(f'''{ctx.demod_r_actual_path}/order''')
    ctx.demod_r_actual_osc = ctx.hfi.get_int(f'''{ctx.demod_r_actual_path}/oscselect''')
    print(f'''读回 Demod {ctx.DEMOD3_IDX}: adcselect={ctx.demod_r_actual_adcselect} (期望 {ctx.DEMOD3_ADC_SELECT} = Signal Input 2), oscselect={ctx.demod_r_actual_osc}, rate={ctx.demod_r_actual_rate:.0f} Sa/s, TC={ctx.demod_r_actual_tc * 1000.0:.2f} ms, order={ctx.demod_r_actual_order}''')
    print(f'''读回 Signal Input {ctx.SIGIN2_INDEX}: range={ctx.sigin2_actual_range} V, AC={('ON' if ctx.sigin2_actual_ac else 'OFF')} (期望 {('AC' if ctx.SIGIN2_AC_COUPLING else 'DC')}), imp50={('ON' if ctx.sigin2_actual_imp50 else 'OFF')}''')
    ctx.hf2_actual = {'demod0': {'rate_Sa_s': float(ctx.actual_rate_d0_calib), 'TC_s': float(ctx.DEMOD0_TC_CALIB), 'phase_deg': float(ctx.calibrated_phase_0), 'mode': 'calibration_hold_for_xy_direct_aw_phase_cal'}, 'demod3': {'adcselect': int(ctx.demod_r_actual_adcselect), 'oscselect': int(ctx.demod_r_actual_osc), 'rate_Sa_s': float(ctx.demod_r_actual_rate), 'TC_s': float(ctx.demod_r_actual_tc), 'order': int(ctx.demod_r_actual_order)}, 'sigin2': {'range_V': float(ctx.sigin2_actual_range), 'ac_coupling': bool(ctx.sigin2_actual_ac), 'imp50': bool(ctx.sigin2_actual_imp50)}, 'auxout2': {'outputselect': int(ctx.auxout_actual_outputselect) if ctx.auxout_actual_outputselect is not None else None, 'scale': float(ctx.auxout_actual_scale) if ctx.auxout_actual_scale is not None else None, 'offset': float(ctx.auxout_actual_offset) if ctx.auxout_actual_offset is not None else None, 'demod_source': int(ctx.auxout_actual_demod) if ctx.auxout_actual_demod is not None else None, 'signal_type': str(ctx.auxout_actual_sig_str) if ctx.auxout_actual_sig_str is not None else None}}
    ctx.config['hf2_actual_config'] = ctx.hf2_actual
    write_run_config(ctx.config_path, ctx.config)
    print('HF2 实际配置已保存到 experiment_config.yaml')

def calibrate_xy(ctx):
    configure_dg_am_reference_trigger(ctx)
    ctx.aw, ctx.x_aw, ctx.y_aw = upload_xy_aw(ctx, ctx.XY_CTRL_PHASE, outputs_on=False)
    ctx.config['xy_direct_aw']['XY_AW_OUTPUT_VPP_auto'] = float(ctx.x_aw['vpp'])
    write_run_config(ctx.config_path, ctx.config)
    save_xy_aw_waveforms(ctx, ctx.raw_dir / 'xy_aw_waveforms_initial.npz', ctx.aw, ctx.x_aw, ctx.y_aw, ctx.XY_CTRL_PHASE)
    ctx.phase_history = []
    ctx.phase_sign = None
    if ctx.ENABLE_XY_CTRL_PHASE_CAL:
        print('\n执行 XY_CTRL_PHASE 校准...')
        upload_xy_aw(ctx, ctx.XY_CTRL_PHASE, outputs_on=True)
        ctx.first_phase_measurement = True
        with acquisition_window(lambda enabled: ctx.switch(enabled), ctx.wait, ctx.SYSTEM_SETTLE_S):
            ctx.direct_aw_phase_result = calibrate_direct_aw_phase(ctx.XY_CTRL_PHASE, partial(apply_phase_and_measure, ctx), DirectAWPhaseCalibrationConfig(tolerance_deg=ctx.XY_CTRL_PHASE_TOL_DEG, max_measurements=ctx.XY_CTRL_PHASE_MAX_ITER + 1, minimum_r_v=ctx.XY_CTRL_PHASE_MIN_R_V, minimum_r_ratio=ctx.XY_CTRL_PHASE_MIN_R_RATIO), cancellation_check=check_cancelled)
            ctx.XY_CTRL_PHASE = ctx.direct_aw_phase_result.final_phase_deg
            ctx.theta_now = ctx.direct_aw_phase_result.final_theta_deg
            ctx.phase_sign = -1.0
            ctx.phase_history = [{'iteration': item.iteration, 'xy_ctrl_phase_deg': item.phase_deg, 'theta_deg': item.theta_deg, 'r': item.r_v, 'minimum_r_V': item.minimum_r_v, 'accepted': item.accepted, 'mode': item.mode if not item.accepted or item.iteration == 0 else 'fixed_minus' if item.iteration == 1 else 'iterate'} for item in ctx.direct_aw_phase_result.history]
        ctx.wait(0.5)
    else:
        print('XY_CTRL_PHASE 校准已禁用，直接使用初始相位')
        ctx.theta_now, ctx.sample_now = read_demod_theta_deg(ctx, ctx.DEMOD0_IDX)
        ctx.phase_sign = 0.0
        ctx.phase_history.append({'iteration': 0, 'xy_ctrl_phase_deg': float(ctx.XY_CTRL_PHASE), 'theta_deg': float(ctx.theta_now), 'r': float(ctx.sample_now['r']), 'mode': 'disabled'})
    ctx.phase_cal_success = bool(ctx.direct_aw_phase_result.converged if ctx.ENABLE_XY_CTRL_PHASE_CAL else abs(ctx.theta_now) < ctx.XY_CTRL_PHASE_TOL_DEG)
    print(f'''XY_CTRL_PHASE 校准完成: phase={ctx.XY_CTRL_PHASE:.2f} deg, theta={ctx.theta_now:+.2f} deg, success={ctx.phase_cal_success}''')
    ctx.aw, ctx.x_aw, ctx.y_aw = upload_xy_aw(ctx, ctx.XY_CTRL_PHASE, outputs_on=True)
    save_xy_aw_waveforms(ctx, ctx.raw_dir / 'xy_aw_waveforms.npz', ctx.aw, ctx.x_aw, ctx.y_aw, ctx.XY_CTRL_PHASE)
    ctx.demod0_meas_cfg = DemodulatorConfig(demod_index=ctx.DEMOD0_IDX, enable=True, rate=ctx.DEMOD0_RATE, input_channel=0, osc_select=ctx.DEMOD0_OSC_IDX, harmonic=1, time_constant=ctx.DEMOD0_TC_MEAS, order=ctx.DEMOD0_ORDER, phase=ctx.calibrated_phase_0)
    ctx.actual_rate_d0_meas = demod.configure_demodulator(ctx.hfi, ctx.demod0_meas_cfg, for_acquisition=True)
    print(f'''\nDemod 0: TC={ctx.DEMOD0_TC_MEAS * 1000:.1f} ms (数据采集), rate={ctx.actual_rate_d0_meas:.0f} Sa/s''')
    ctx.calib_results = {'calibrated_phase_0_deg': float(ctx.calibrated_phase_0), 'XY_CTRL_PHASE_deg': float(ctx.XY_CTRL_PHASE), 'XY_CTRL_QUAD_deg': float(ctx.XY_CTRL_QUAD), 'xy_phase_cal_success': bool(ctx.phase_cal_success), 'final_theta_deg': float(ctx.theta_now), 'phase_update_sign': float(ctx.phase_sign), 'minimum_r_V': float(ctx.XY_CTRL_PHASE_MIN_R_V), 'minimum_r_ratio': float(ctx.XY_CTRL_PHASE_MIN_R_RATIO), 'best_r_V': float(ctx.direct_aw_phase_result.best_r_v) if ctx.ENABLE_XY_CTRL_PHASE_CAL else float(ctx.sample_now['r']), 'history': ctx.phase_history}
    ctx.config['calibration'] = ctx.calib_results
    ctx.config['xy_direct_aw']['XY_CTRL_PHASE_calibrated_deg'] = float(ctx.XY_CTRL_PHASE)
    ctx.config['xy_direct_aw']['XY_CTRL_QUAD_deg'] = float(ctx.XY_CTRL_QUAD)
    ctx.config.setdefault('hf2_actual_config', {})['demod0_meas_after_xy_cal'] = {'rate_Sa_s': float(ctx.actual_rate_d0_meas), 'TC_s': float(ctx.DEMOD0_TC_MEAS), 'phase_deg': float(ctx.calibrated_phase_0), 'mode': 'measurement_after_xy_direct_aw_phase_cal'}
    ctx.config.setdefault('actual_rates', {})['demod0_meas_Sa_s'] = float(ctx.actual_rate_d0_meas)
    ctx.config.setdefault('data_files', []).extend(['raw/xy_aw_waveforms_initial.npz', 'raw/xy_aw_waveforms.npz'])
    write_run_config(ctx.config_path, ctx.config)
    print('\n校准结果已保存到配置')
