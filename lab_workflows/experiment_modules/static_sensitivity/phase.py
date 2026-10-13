"""静磁场锁相配置、实际回读与 Bell-Bloom 校相。"""

import math

from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod

from ...steps import PhaseCalibrationConfig, calibrate_demod_phase
from ...steps.dispersion import dispersion_sample_count


def demod_config(params, phase, *, noise=False):
    return DemodulatorConfig(
        demod_index=params.hf2_demod_idx, enable=True,
        rate=params.hf2_noise_rate if noise else params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1,
        time_constant=params.hf2_noise_tc if noise else params.hf2_demod_tc,
        order=params.hf2_demod_order, phase=phase)


def hf2_snapshot(hf2, demod_idx):
    base = hf2.demod_path(demod_idx)
    return {
        "input": {"range": hf2.get_double(f"{hf2.sig_in_path(0)}/range"),
                  **{key: hf2.get_int(f"{hf2.sig_in_path(0)}/{key}") for key in ("ac", "diff", "imp50")}},
        "demod_idx": demod_idx,
        "demod": {**{key: hf2.get_double(f"{base}/{key}") for key in ("rate", "timeconstant", "phaseshift")},
                  **{key: hf2.get_int(f"{base}/{key}") for key in ("order", "oscselect", "harmonic", "enable", "adcselect")}},
        "oscillator_frequency_hz": hf2.get_double(f"{hf2.osc_path(0)}/freq")}


def require_mode(ctx, mode):
    if ctx.demod_mode != mode:
        raise ValueError("锁相当前模式不匹配，请显式重新执行配置与前置步骤")
    key = "hf2_noise_configuration" if mode == "noise" else "hf2_configuration"
    expected = ctx.state.get(key)
    actual = hf2_snapshot(ctx.devices["hf2"], ctx.params.hf2_demod_idx)
    if not expected or actual != expected:
        raise ValueError("锁相实际配置已改变，请重新执行锁相配置及后续准备步骤")


def configure_dispersion(ctx):
    hf2, params = ctx.devices["hf2"], ctx.params
    demod.configure_signal_input(hf2, SignalInputConfig(input_index=0, range=params.hf2_signal_range, diff=False))
    demod.configure_oscillator(hf2, OscillatorConfig(osc_index=0, frequency=params.pump_mod_freq, source="manual"))
    rate = float(demod.configure_demodulator(hf2, demod_config(params, 0.0)))
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("锁相实际色散采样率无效")
    count = dispersion_sample_count(rate, params.dispersion_duration_s)
    ctx.dispersion_rate = rate
    ctx.dispersion_params = params
    ctx.demod_mode = "dispersion"
    ctx.state["actual_rates"] = {"hf2_demod_rate_Sa_s": rate}
    ctx.state["dispersion_acquisition"] = {
        "method": "hf2_daq_exact", "raw_schema_version": 3,
        "requested_rate_sa_s": params.hf2_demod_rate, "actual_rate_sa_s": rate,
        "requested_duration_s": params.dispersion_duration_s,
        "actual_duration_s": count / rate, "samples_per_point": count}


def calibrate(ctx):
    from .acquisition import RuntimeCancellation, set_temp_switch, wait_for_settle

    temp, channel = ctx.devices["dg_temp"], ctx.channels["Temp_Switch"]
    set_temp_switch(temp, channel, False)
    wait_for_settle(ctx.params.dispersion_temp_switch_off_settle_s)
    result = calibrate_demod_phase(
        ctx.devices["hf2"], PhaseCalibrationConfig(demod_idx=ctx.params.hf2_demod_idx),
        cancellation=RuntimeCancellation(), settle_wait=wait_for_settle)
    ctx.calibrated_phase = result.phase_shift_deg
    ctx.state["phase_calibration"] = result.to_dict()
    ctx.state["hf2_configuration"] = hf2_snapshot(ctx.devices["hf2"], ctx.params.hf2_demod_idx)
    # 局部恢复失败交给统一收尾，保留校相的原始异常。
    set_temp_switch(temp, channel, True)
    wait_for_settle(ctx.params.dispersion_temp_switch_on_settle_s)
