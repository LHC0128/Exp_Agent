"""原 Pump 激励、时序与主解调校相；不引入 XY 载波或 Z Burst。"""
import math
from ...steps import acquisition_window, calibrate_demod_phase, PhaseCalibrationConfig
from . import preparation
from .acquisition import wait


class PhaseQualityError(RuntimeError):
    """已恢复温控的校相质量失败，允许原连接中重试。"""


def pump_and_trigger(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in ("Pump_modulation", "Time_sequence", "Time_sequence_2"):
        sg.set_burst_state(False, channel=ch[key])
        sg.set_mod_state(False, channel=ch[key])
    preparation.checked(ctx, "Pump_modulation", -p.pump_mod_amplitude, p.pump_mod_amplitude)
    sg.setup_sine(freq=100e6, amplitude=p.pump_mod_amplitude, offset=0, phase=0, channel=ch["Pump_modulation"])
    sg.set_output(True, channel=ch["Pump_modulation"])
    preparation.checked(ctx, "PUMP_MOD_DUTY", p.pump_mod_duty)
    preparation.checked(ctx, "Time_sequence", p.rf_gate_offset-p.rf_gate_amplitude/2, p.rf_gate_offset+p.rf_gate_amplitude/2)
    sg.setup_pulse(freq=p.pump_mod_freq, amplitude=p.rf_gate_amplitude, offset=p.rf_gate_offset,
        width=(p.pump_mod_duty/100)/p.pump_mod_freq, channel=ch["Time_sequence"])
    sg.set_output(True, channel=ch["Time_sequence"])
    # 原脚本误用 Time_sequence 的参数名表示 Z 同机 CH2；保留参数键，实际端点从映射解析。
    preparation.checked(ctx, "Time_sequence_2", -p.time_sequence/2, p.time_sequence/2)
    sg.setup_square(freq=10, amplitude=p.time_sequence, offset=0, dcycle=50, channel=ch["Time_sequence_2"])
    sg.set_output(True, channel=ch["Time_sequence_2"])
    ctx.state["pump_modulation"] = dict(PUMP_MOD_FREQ_Hz=p.pump_mod_freq, PUMP_MOD_DUTY_pct=p.pump_mod_duty,
        PUMP_MOD_AMPLITUDE_Vpp=p.pump_mod_amplitude, RF_GATE_AMPLITUDE_Vpp=p.rf_gate_amplitude, RF_GATE_OFFSET_V=p.rf_gate_offset)
    ctx.outputs["Pump"] = "连续载波、脉冲门控 ON；时序2为 10 Hz 零偏置方波"


def main_phase(ctx):
    p, sg, ch = ctx.params, ctx.devices["sg"], ctx.channels
    for key in ("X_magnetic_field", "Y_magnetic_field", "Z_magnetic_field"):
        sg.set_output(False, channel=ch[key])
    preparation.configure_main(ctx, calibration=True)
    try:
        with acquisition_window(lambda enabled: preparation.switch(ctx, enabled), wait, p.system_settle_s):
            result = calibrate_demod_phase(ctx.devices["lockin"], PhaseCalibrationConfig(
                demod_idx=p.hf2_demod0_idx, tolerance_deg=1, max_attempts=5, settle_time=p.system_settle_s), settle_wait=wait)
    except (ValueError, RuntimeError) as exc:
        if ctx.outputs.get("温控") == "5 V DC + ON" and any(term in str(exc) for term in ("无法可靠校相", "相位校准未在", "校相读数包含非有限", "校相初始读数包含非有限", "校相相位回读非有限")):
            ctx.state["demod_phase_calibration"] = dict(converged=False, error=str(exc))
            raise PhaseQualityError(str(exc)) from exc
        raise
    ctx.state["demod_phase_calibration"] = result.to_dict()
    ctx.state["demod_phase_deg"] = result.phase_shift_deg
    wait(p.temp_switch_on_lag)


def measurement_mode(ctx):
    ctx.stream_rate = preparation.configure_main(ctx)
    if not math.isfinite(ctx.stream_rate) or ctx.stream_rate <= 0:
        raise ValueError("主解调实际采样率无效")
    ctx.state["measurement_ready"] = preparation.verify_routing(ctx)
    ctx.state["hf2_demod0"].update(rate_Sa_s_actual=ctx.stream_rate, actual_time_constant_s=ctx.state["measurement_ready"]["main"]["timeconstant"],
        calibrated_phase_deg=ctx.state["demod_phase_deg"])
    ctx.outputs["锁相"] = "HF2 DAQ／物理回环已准备并回读"
