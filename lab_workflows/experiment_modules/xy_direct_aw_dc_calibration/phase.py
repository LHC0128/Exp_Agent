"""本实验锁相链路与 DirectAW 校相，完整与分步共用。"""
import math
from lab_workflows.steps import acquisition_window
from lab_workflows.experiment_runtime import check_cancelled
from lab_workflows.steps import DirectAWPhaseCalibrationConfig, PhaseCalibrationConfig, calibrate_demod_phase, calibrate_direct_aw_phase
from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from functools import partial
from lab_workflows.lockin_control import apply_settings
from .excitation import configure_trigger_source, upload_direct_aw, wrap_deg

def configure_lockin(ctx):
    ctx.sig0_cfg = SignalInputConfig(input_index=0, range=ctx.DEMOD0_SIGNAL_RANGE_V, diff=False)
    demod.configure_signal_input(ctx.hfi, ctx.sig0_cfg)
    demod.configure_oscillator(ctx.hfi, OscillatorConfig(osc_index=ctx.DEMOD0_OSC_IDX, frequency=ctx.PUMP_MOD_FREQ_Hz))
    ctx.actual_rate_d0_cal = demod.configure_demodulator(ctx.hfi, DemodulatorConfig(demod_index=ctx.DEMOD0_IDX, enable=True, rate=ctx.DEMOD0_RATE_Sa_s, input_channel=0, osc_select=ctx.DEMOD0_OSC_IDX, harmonic=1, time_constant=ctx.DEMOD0_TC_CALIB_s, order=ctx.DEMOD0_ORDER, phase=0.0))
    ctx.sig1_cfg = SignalInputConfig(input_index=ctx.SIGIN2_INDEX, range=ctx.SIGIN2_RANGE_V, diff=False)
    demod.configure_signal_input(ctx.hfi, ctx.sig1_cfg)
    ctx.auxout_path = ctx.hfi.aux_out_path(ctx.AUXOUT_INDEX)
    apply_settings(ctx.hfi, ctx.MAPPING['lockin_r']['device_library_id'], ctx.AUXOUT_SOURCE_DEMOD_IDX,
        {'aux_output': {'index': ctx.AUXOUT_INDEX, 'demod_index': ctx.AUXOUT_SOURCE_DEMOD_IDX,
            'output_select': ctx.AUXOUT_SOURCE_SELECT, 'scale': ctx.AUXOUT_SCALE, 'offset_v': ctx.AUXOUT_OFFSET_V}},
        project_root=ctx.root)
    ctx.initial_z_freq = float(ctx.Z_RF_FREQ_LIST_Hz[0])
    demod.configure_oscillator(ctx.hfi, OscillatorConfig(osc_index=ctx.DEMOD3_OSC_IDX, frequency=ctx.initial_z_freq))
    ctx.actual_rate_d3_initial = demod.configure_demodulator(ctx.hfi, DemodulatorConfig(demod_index=ctx.DEMOD3_IDX, enable=True, rate=ctx.DEMOD3_RATE_Sa_s, input_channel=ctx.DEMOD3_ADC_SELECT, osc_select=ctx.DEMOD3_OSC_IDX, harmonic=1, time_constant=max(ctx.DEMOD3_TC_MIN_s, ctx.DEMOD3_TC_PERIOD_FRACTION / ctx.initial_z_freq), order=ctx.DEMOD3_ORDER, phase=0.0))
    print('执行 Demod 0 自动校相...')
    with acquisition_window(lambda enabled: ctx.switch(enabled), ctx.wait, ctx.SYSTEM_SETTLE_S):
        ctx.demod0_phase_result = calibrate_demod_phase(ctx.hfi, PhaseCalibrationConfig(demod_idx=ctx.DEMOD0_IDX, tolerance_deg=1.0, max_attempts=5, settle_time=ctx.SYSTEM_SETTLE_S))
        ctx.calibrated_phase_0_deg = ctx.demod0_phase_result.phase_shift_deg
    ctx.wait(1.0)

def calibrate_xy(ctx):
    configure_trigger_source(ctx)
    ctx.xy_phase_deg = float(ctx.XY_CTRL_PHASE_INITIAL_deg)
    ctx.phase_history = []
    if ctx.ENABLE_XY_PHASE_CAL:
        upload_direct_aw(ctx, ctx.XY_PHASE_CAL_ENVELOPE_V, ctx.xy_phase_deg, output=True)
        ctx.first_phase_measurement = True
        with acquisition_window(lambda enabled: ctx.switch(enabled), ctx.wait, ctx.SYSTEM_SETTLE_S):

            def apply_xy_phase_and_measure(phase_deg):
                if ctx.first_phase_measurement:
                    ctx.first_phase_measurement = False
                else:
                    upload_direct_aw(ctx, ctx.XY_PHASE_CAL_ENVELOPE_V, phase_deg, output=True)
                    ctx.wait(ctx.SYSTEM_SETTLE_S)
                sample = demod.read_demod_sample(ctx.hfi, demod_idx=ctx.DEMOD0_IDX)
                theta_deg = wrap_deg(ctx, math.degrees(sample['theta']))
                print(f'''DirectAW 校相: phase={phase_deg:.3f}°, theta={theta_deg:+.3f}°, R={sample['r']:.4e} V''')
                return sample
            ctx.direct_aw_phase_result = calibrate_direct_aw_phase(ctx.xy_phase_deg, apply_xy_phase_and_measure, DirectAWPhaseCalibrationConfig(tolerance_deg=ctx.XY_PHASE_CAL_TOL_deg, max_measurements=ctx.XY_PHASE_CAL_MAX_ITER + 1, minimum_r_v=ctx.XY_PHASE_CAL_MIN_R_V, minimum_r_ratio=ctx.XY_PHASE_CAL_MIN_R_RATIO), cancellation_check=check_cancelled)
            ctx.xy_phase_deg = ctx.direct_aw_phase_result.final_phase_deg
            ctx.phase_history = [{'iteration': item.iteration, 'xy_phase_deg': item.phase_deg, 'theta_deg': item.theta_deg, 'r_V': item.r_v, 'minimum_r_V': item.minimum_r_v, 'accepted': item.accepted, 'mode': item.mode} for item in ctx.direct_aw_phase_result.history]
        ctx.wait(1.0)
    ctx.phase_cal_success = bool(ctx.direct_aw_phase_result.converged if ctx.ENABLE_XY_PHASE_CAL else False)
    ctx.actual_rate_d0_meas = demod.configure_demodulator(ctx.hfi, DemodulatorConfig(demod_index=ctx.DEMOD0_IDX, enable=True, rate=ctx.DEMOD0_RATE_Sa_s, input_channel=0, osc_select=ctx.DEMOD0_OSC_IDX, harmonic=1, time_constant=ctx.DEMOD0_TC_MEAS_s, order=ctx.DEMOD0_ORDER, phase=ctx.calibrated_phase_0_deg), for_acquisition=True)
