"""Pump 触发、主解调校相与恒定 XY 外部 AM 链路。"""
import math
from lockin_amplifier import demod
from ...steps import acquisition_window, calibrate_demod_phase, PhaseCalibrationConfig
from . import preparation
from .acquisition import wait


def pump_and_trigger(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in ("Pump_modulation", "Time_sequence", "Time_sequence_2"):
        sg.set_burst_state(False, channel=ch[key]); sg.set_mod_state(False, channel=ch[key])
    preparation.checked(ctx, "Pump_modulation", -p.pump_mod_amplitude, p.pump_mod_amplitude)
    sg.setup_sine(freq=100e6, amplitude=p.pump_mod_amplitude, offset=0, phase=0, channel=ch["Pump_modulation"])
    sg.set_output(True, channel=ch["Pump_modulation"])
    preparation.checked(ctx, "PUMP_MOD_DUTY", p.pump_mod_duty)
    preparation.checked(ctx, "Time_sequence", p.rf_gate_offset-p.rf_gate_amplitude/2, p.rf_gate_offset+p.rf_gate_amplitude/2)
    sg.setup_pulse(freq=p.pump_mod_freq, amplitude=p.rf_gate_amplitude, offset=p.rf_gate_offset,
        width=(p.pump_mod_duty/100)/p.pump_mod_freq, channel=ch["Time_sequence"])
    sg.set_pulse_dcycle(p.pump_mod_duty, channel=ch["Time_sequence"])
    sg.set_output(True, channel=ch["Time_sequence"])
    sg.set_sync_state(True, channel=ch["Time_sequence"])
    # 原协议的触发方波保持 10 Hz、0–5 V；接线仍须人工核对。
    preparation.checked(ctx, "Time_sequence_2", 0, 5)
    sg.setup_square(freq=10, amplitude=5, offset=2.5, channel=ch["Time_sequence_2"])
    sg.set_output(True, channel=ch["Time_sequence_2"])
    ctx.state["pump_modulation"] = dict(PUMP_MOD_FREQ_Hz=p.pump_mod_freq, PUMP_MOD_DUTY_pct=p.pump_mod_duty,
        PUMP_MOD_AMPLITUDE_Vpp=p.pump_mod_amplitude, RF_GATE_AMPLITUDE_Vpp=p.rf_gate_amplitude, RF_GATE_OFFSET_V=p.rf_gate_offset)
    ctx.outputs["Pump"] = "载波、脉冲门控及 SYNC ON"


def main_phase(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in ("X_magnetic_field", "Y_magnetic_field", "X_magnetic_field_AM", "Y_magnetic_field_AM", "Z_magnetic_field"):
        sg.set_output(False, channel=ch[key])
    preparation.configure_main(ctx, calibration=True)
    with acquisition_window(lambda enabled: preparation.switch(ctx, enabled), wait, p.system_settle_s):
        result = calibrate_demod_phase(ctx.devices["lockin"], PhaseCalibrationConfig(
            demod_idx=p.hf2_demod0_idx, tolerance_deg=1, max_attempts=5, settle_time=p.system_settle_s), settle_wait=wait)
    ctx.state["demod_phase_calibration"] = result.to_dict()
    ctx.state["demod_phase_deg"] = result.phase_shift_deg
    wait(p.temp_switch_on_lag)


def xy_field(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    preparation.configure_main(ctx, calibration=True)
    x_phase, y_phase = p.x_carrier_phase % 360, (90 if p.enable_xy_carrier_phase_cal else p.y_carrier_phase) % 360
    for axis, phase, amplitude, depth in (("X", x_phase, p.x_carrier_amplitude, p.x_am_depth), ("Y", y_phase, p.y_carrier_amplitude, p.y_am_depth)):
        carrier, envelope = axis+"_magnetic_field", axis+"_magnetic_field_AM"
        preparation.checked(ctx, carrier, -amplitude/2, amplitude/2)
        sg.set_burst_state(False, channel=ch[carrier]); sg.set_mod_state(False, channel=ch[carrier])
        sg.setup_sine(freq=p.xy_carrier_freq, amplitude=amplitude, offset=0, phase=phase, channel=ch[carrier])
        sg.set_mod_type("AM", channel=ch[carrier]); sg.set_mod_am_source("EXT", channel=ch[carrier])
        sg.set_mod_am_depth(depth, channel=ch[carrier]); sg.set_mod_state(True, channel=ch[carrier])
        preparation.checked(ctx, envelope, p.xy_dc_voltage)
        sg.set_burst_state(False, channel=ch[envelope]); sg.set_mod_state(False, channel=ch[envelope])
        sg.setup_dc(p.xy_dc_voltage, channel=ch[envelope])
        sg.set_output(True, channel=ch[carrier]); sg.set_output(True, channel=ch[envelope])
    result = dict(enabled=p.enable_xy_carrier_phase_cal, x_carrier_phase_initial_deg=x_phase,
        y_carrier_phase_initial_deg=y_phase, am_dc_V=p.xy_dc_voltage, target_xy_phase_diff_deg=90,
        x_carrier_phase_calibrated_deg=x_phase, y_carrier_phase_calibrated_deg=y_phase,
        phase_cal_success_x=False, phase_cal_success_y=False, n_iter_x=0, n_iter_y=0)
    ctx.state["xy_carrier_phase_calibration"] = result
    if p.enable_xy_carrier_phase_cal:
        sg.set_output(False, channel=ch["Y_magnetic_field"])
        sg.set_output(False, channel=ch["Y_magnetic_field_AM"])
        with acquisition_window(lambda enabled: preparation.switch(ctx, enabled), wait, p.system_settle_s):
            for axis, initial, iterations in (("X", x_phase, p.xy_phase_cal_max_iter_x), ("Y", y_phase, p.xy_phase_cal_max_iter_y)):
                if axis == "Y":
                    sg.set_output(True, channel=ch["Y_magnetic_field"])
                    sg.set_output(True, channel=ch["Y_magnetic_field_AM"])
                    sg.set_phase_adjust(initial, channel=ch["Y_magnetic_field"])
                    wait(p.system_settle_s)
                phase = initial
                for iteration in range(iterations):
                    sample = demod.read_demod_sample(ctx.devices["lockin"], demod_idx=p.hf2_demod0_idx)
                    if not all(math.isfinite(float(sample[key])) for key in ("x", "y", "r", "theta")):
                        raise ValueError("XY 校相读数非有限")
                    theta = math.degrees(sample["theta"])
                    result["n_iter_"+axis.lower()] = iteration+1
                    if abs(theta) < p.xy_phase_cal_tol_deg:
                        result["phase_cal_success_"+axis.lower()] = True
                        break
                    phase = (phase-theta) % 360
                    sg.set_phase_adjust(phase, channel=ch[axis+"_magnetic_field"])
                    wait(p.system_settle_s)
                result[axis.lower()+"_carrier_phase_calibrated_deg"] = phase
        wait(p.temp_switch_on_lag)
    result["xy_phase_diff_calibrated_deg"] = (result["y_carrier_phase_calibrated_deg"]-result["x_carrier_phase_calibrated_deg"]) % 360
    # 沿用原协议：迭代用尽记录不收敛诊断，不把目标 90° 当成新科学门槛。
    ctx.state["xy_carrier"] = dict(XY_CARRIER_FREQ_Hz=p.xy_carrier_freq, X_CARRIER_AMPLITUDE_Vpp=p.x_carrier_amplitude,
        Y_CARRIER_AMPLITUDE_Vpp=p.y_carrier_amplitude, X_CARRIER_PHASE_deg=result["x_carrier_phase_calibrated_deg"],
        Y_CARRIER_PHASE_deg=result["y_carrier_phase_calibrated_deg"], X_AM_DEPTH_pct=p.x_am_depth, Y_AM_DEPTH_pct=p.y_am_depth)
    ctx.outputs["XY"] = f"外部 AM 载波与 {p.xy_dc_voltage:g} V DC 包络 ON；0 Hz 基线保持此状态"


def measurement_mode(ctx):
    ctx.stream_rate = preparation.configure_main(ctx)
    if not math.isfinite(ctx.stream_rate) or ctx.stream_rate <= 0:
        raise ValueError("主解调实际采样率无效")
    ctx.state["measurement_ready"] = preparation.verify_routing(ctx)
    ctx.outputs["锁相"] = "HF2 DAQ／物理回环已准备并回读"
