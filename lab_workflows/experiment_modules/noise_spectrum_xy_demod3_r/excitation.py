"""Pump/触发、锁相回环与 DirectAW 校相；输出前统一校验。"""
from __future__ import annotations
import math
import time
import numpy as np
from lockin_amplifier import DAQConfig, DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...lockin_control import apply_settings
from ...steps import (
    ArbitraryWaveformSpec, upload_arbitrary, acquisition_window, set_temperature_switch,
    PhaseCalibrationConfig, calibrate_demod_phase, DirectAWPhaseCalibrationConfig, calibrate_direct_aw_phase,
)

AW_AMPLITUDE_FLOOR_VPP = 0.001


def sleep(seconds):
    end = time.monotonic() + max(0, seconds)
    while (remaining := end - time.monotonic()) > 0:
        check_cancelled()
        time.sleep(min(0.1, remaining))
    check_cancelled()


def set_temp(ctx, enabled):
    return set_temperature_switch(ctx.devices["sg"], enabled, channel=ctx.channels["Temp_Switch"],
        on_voltage=ctx.params.temp_switch, off_voltage=0)


def pump_and_triggers(ctx):
    p, sg, c, limits = ctx.params, ctx.devices["sg"], ctx.channels, ctx.limits
    validate_safety_limit("Pump_modulation", p.pump_mod_amplitude, limits)
    sg.setup_sine(100e6, p.pump_mod_amplitude, offset=0, phase=0, channel=c["Pump_modulation"])
    validate_safety_limit("PUMP_MOD_DUTY", p.pump_mod_duty, limits)
    for value in (p.rf_gate_offset - p.rf_gate_amplitude / 2, p.rf_gate_offset + p.rf_gate_amplitude / 2):
        validate_safety_limit("Time_sequence", value, limits)
    sg.setup_pulse(p.pump_mod_freq, p.rf_gate_amplitude, offset=p.rf_gate_offset,
        width=p.pump_mod_duty / 100 / p.pump_mod_freq, channel=c["Time_sequence"])
    sg.set_sync_state(False, channel=c["Time_sequence"])
    for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
        for value in (p.xy_trigger_offset - p.xy_trigger_amplitude / 2, p.xy_trigger_offset + p.xy_trigger_amplitude / 2):
            validate_safety_limit(key, value, limits)
        channel = c[key]
        sg.set_burst_state(False, channel=channel)
        sg.set_mod_state(False, channel=channel)
        sg.setup_square(p.xy_trigger_freq, p.xy_trigger_amplitude, offset=p.xy_trigger_offset,
            dcycle=p.xy_trigger_duty, phase=p.xy_trigger_phase % 360, channel=channel)
    for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
        sg.phase_init(channel=c[key])


def demod0(ctx):
    p, hfi = ctx.params, ctx.devices["lockin"]
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        ctx.devices["sg"].set_output(False, channel=ctx.channels[key])
    demod.configure_signal_input(hfi, SignalInputConfig(input_index=0, range=p.demod0_signal_range_v, diff=False))
    demod.configure_oscillator(hfi, OscillatorConfig(osc_index=p.demod0_osc_idx, frequency=p.pump_mod_freq))
    rate = demod.configure_demodulator(hfi, DemodulatorConfig(demod_index=p.demod0_idx, enable=True,
        rate=p.demod0_rate_sa_s, input_channel=0, osc_select=p.demod0_osc_idx, harmonic=1,
        time_constant=p.demod0_tc_calib_s, order=p.demod0_order, phase=0), for_acquisition=False)
    with acquisition_window(lambda enabled: set_temp(ctx, enabled), sleep, p.system_settle_s):
        result = calibrate_demod_phase(hfi, PhaseCalibrationConfig(demod_idx=p.demod0_idx,
            tolerance_deg=1, max_attempts=5, settle_time=p.system_settle_s), settle_wait=sleep)
    ctx.state["demod0_phase"] = result.to_dict()
    ctx.state.setdefault("actual_rates", {})["demod0_calibration_Sa_s"] = float(rate)


def loopback(ctx):
    p, hfi = ctx.params, ctx.devices["lockin"]
    demod.configure_signal_input(hfi, SignalInputConfig(input_index=p.sigin2_index, range=p.sigin2_range_v, diff=False))
    apply_settings(hfi, ctx.mapping["lockin_r"]["device_library_id"], p.demod0_idx,
        {"aux_output": dict(index=p.auxout_index, demod_index=p.auxout_source_demod_idx,
            output_select=p.auxout_source_select, scale=p.auxout_scale, offset_v=p.auxout_offset_v)},
        project_root=ctx.root)


def build_waveforms(params, envelope_v, phase_deg, *, limits=None):
    """逐点自动满量程，零包络上传全零但不启动输出。"""
    t = np.arange(params.xy_aw_points, dtype=float) / (params.xy_aw_points * params.xy_aw_repeat_freq_hz)
    phase = 2 * np.pi * params.xy_ctrl_freq * t + math.radians(phase_deg)
    values = (envelope_v * np.cos(phase), envelope_v * np.cos(phase + math.radians(params.xy_ctrl_quad)))
    for key, array in zip(("X_magnetic_field", "Y_magnetic_field"), values):
        validate_safety_limit(key, float(np.min(array)), limits)
        validate_safety_limit(key, float(np.max(array)), limits)
    vpp = 2 * abs(envelope_v)
    if vpp == 0:
        return np.zeros_like(t), np.zeros_like(t), AW_AMPLITUDE_FLOOR_VPP
    if vpp < AW_AMPLITUDE_FLOOR_VPP:
        raise ValueError("非零 DirectAW 包络低于仪器最小幅度")
    return values[0] / (vpp / 2), values[1] / (vpp / 2), vpp


def upload(ctx, envelope_v, phase_deg):
    p, sg, c = ctx.params, ctx.devices["sg"], ctx.channels
    x, y, vpp = build_waveforms(p, envelope_v, phase_deg, limits=ctx.limits)
    for key, values in (("X_magnetic_field", x), ("Y_magnetic_field", y)):
        sg.set_output(False, channel=c[key])
        upload_arbitrary(sg, ArbitraryWaveformSpec(values=values, frequency=p.xy_aw_repeat_freq_hz,
            amplitude=vpp, offset=0, phase=0, channel=c[key], output=False))
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        channel = c[key]
        sg.set_burst_state(True, channel=channel)
        sg.set_burst_mode("INFinity", channel=channel)
        sg.set_burst_trigger_source("EXTernal", channel=channel)
        sg.set_burst_trigger_slope("POSitive", channel=channel)
        sg.set_burst_phase(0, channel=channel)
        sg.set_output(envelope_v != 0, channel=channel)
    return vpp


def xy_phase(ctx):
    p = ctx.params
    def measure(phase):
        upload(ctx, p.xy_calib_envelope_v, phase)
        with acquisition_window(lambda enabled: set_temp(ctx, enabled), sleep, p.system_settle_s):
            result = demod.read_demod_sample(ctx.devices["lockin"], demod_idx=p.demod0_idx)
        sleep(1.0)
        return result
    result = calibrate_direct_aw_phase(p.xy_ctrl_phase, measure, DirectAWPhaseCalibrationConfig(
        tolerance_deg=p.xy_phase_cal_tol_deg, max_measurements=p.xy_phase_cal_max_iter,
        minimum_r_v=p.xy_phase_cal_min_r_v, minimum_r_ratio=p.xy_phase_cal_min_r_ratio),
        cancellation_check=check_cancelled)
    ctx.state["xy_phase_calibration"] = result.to_dict()
    if not result.converged:
        raise ValueError("DirectAW 校相未收敛")
    ctx.xy_phase_deg = result.final_phase_deg


def verify_ready(ctx):
    """扫描前核对实际锁相链和连续触发；XY 波形由每个外层点重新上传。"""
    p, hfi, sg, c = ctx.params, ctx.devices["lockin"], ctx.devices["sg"], ctx.channels
    d0, aux = hfi.demod_path(p.demod0_idx), hfi.aux_out_path(p.auxout_index)
    integers = {d0 + "/enable": 1, d0 + "/adcselect": 0, d0 + "/oscselect": p.demod0_osc_idx,
                d0 + "/harmonic": 1, d0 + "/order": p.demod0_order,
                aux + "/demodselect": p.auxout_source_demod_idx, aux + "/outputselect": p.auxout_source_select}
    doubles = {d0 + "/phaseshift": ctx.state["demod0_phase"]["phase_shift_deg"],
               d0 + "/timeconstant": p.demod0_tc_calib_s,
               hfi.osc_path(p.demod0_osc_idx) + "/freq": p.pump_mod_freq,
               aux + "/scale": p.auxout_scale, aux + "/offset": p.auxout_offset_v}
    if any(hfi.get_int(path) != value for path, value in integers.items()) or any(
            not math.isclose(hfi.get_double(path), value, rel_tol=1e-9, abs_tol=1e-9) for path, value in doubles.items()):
        raise ValueError("锁相实际模式与有效校相不一致，请重新准备和校相")
    for key in ("X_magnetic_field_AM", "Y_magnetic_field_AM"):
        channel = c[key]
        if (not sg.get_output(channel=channel) or not str(sg.get_shape(channel=channel)).upper().startswith("SQU")
                or sg.get_burst_state(channel=channel)
                or not math.isclose(float(sg.get_frequency(channel=channel)), p.xy_trigger_freq, rel_tol=1e-9)):
            raise ValueError("外触发实际模式已改变，请重新执行 A06 并校相")


def configure_demod3(ctx, frequency):
    p, hfi = ctx.params, ctx.devices["lockin"]
    demod.configure_oscillator(hfi, OscillatorConfig(osc_index=p.demod3_osc_idx, frequency=frequency))
    tc = max(p.demod3_tc_min_s, p.demod3_tc_period_fraction / frequency)
    rate = demod.configure_demodulator(hfi, DemodulatorConfig(demod_index=p.demod3_idx, enable=True,
        rate=p.demod3_rate_sa_s, input_channel=p.demod3_adc_select, osc_select=p.demod3_osc_idx,
        harmonic=1, time_constant=tc, order=p.demod3_order, phase=0), for_acquisition=True)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Demod3 实际采样率必须为有限正数")
    config = DAQConfig(device=ctx.mapping["lockin_r"]["device_id"], trigger_type=0,
        duration=p.demod3_acquisition_duration_s, grid_cols=max(1, round(rate * p.demod3_acquisition_duration_s)),
        grid_rows=1, grid_mode=2, signal_paths=["sample.r"])
    return rate, config
