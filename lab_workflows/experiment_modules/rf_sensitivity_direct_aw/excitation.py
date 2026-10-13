"""Pump、共同触发与 DirectAW / Z RF 激励。"""
from __future__ import annotations
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import ArbitraryWaveformSpec, upload_arbitrary
from ._helpers import validate_levels, wait
from .models import RFSensitivityDirectAWParams
from .waveform import synthesize_xy

def upload_xy(signals, channels: dict, params: RFSensitivityDirectAWParams, envelope: dict, phase: float) -> dict:
    waveforms = synthesize_xy(params, envelope, phase)
    vpp = waveforms["xy_aw_vpp"]
    for axis in ("x", "y"):
        check_cancelled()
        channel = channels[axis]
        validate_levels(f"{axis.upper()}_magnetic_field", vpp, 0.0)
        signals.set_output(False, channel=channel)
        upload_arbitrary(signals, ArbitraryWaveformSpec(waveforms[f"{axis}_normalized"], envelope["repeat_freq_Hz"], vpp, 0.0, channel=channel))
        signals.set_burst_mode("INFinity", channel=channel)
        signals.set_burst_ncycles(50000, channel=channel)
        signals.set_burst_trigger_source("EXTernal", channel=channel)
        signals.set_burst_trigger_slope("POSitive", channel=channel)
        signals.set_burst_phase(0.0, channel=channel)
        signals.set_burst_state(True, channel=channel)
    for axis in ("x", "y"):
        signals.set_output(True, channel=channels[axis])
    return waveforms


def arm_z(signals, channel: int, params: RFSensitivityDirectAWParams, amplitude: float, phase_base: float | None = None) -> float:
    """调幅只调用 set_amplitude；正负号由已校准的 Burst 相差表示。

    ``phase_base`` 为正 RF 的相位基准；缺省时用参数中的初始基准，
    RF 相位扫描完成后改用扫描选定的相位。
    """
    validate_levels("Z_magnetic_field", abs(amplitude))
    validate_safety_limit("Z_magnetic_field", abs(amplitude))
    base = params.z_burst_phase_init if phase_base is None else float(phase_base)
    phase = (base + (180 if amplitude < 0 else 0)) % 360
    signals.set_output(False, channel=channel)
    signals.set_burst_state(False, channel=channel)
    signals.set_burst_phase(phase, channel=channel)
    if abs(amplitude) > 1e-6:
        signals.set_amplitude(abs(amplitude), channel=channel)
        signals.set_burst_state(True, channel=channel)
        signals.set_output(True, channel=channel)
        wait(1.5 / params.xy_trigger_freq)
    return phase

def configure_pump(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    validate_levels("Pump_modulation", params.pump_mod_amplitude)
    signals.set_burst_state(False, channel=channels["pump_rf"])
    signals.set_mod_state(False, channel=channels["pump_rf"])
    signals.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude, channel=channels["pump_rf"])
    signals.set_output(True, channel=channels["pump_rf"])
    validate_levels("Time_sequence", params.rf_gate_amplitude, params.rf_gate_offset)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty)
    signals.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude, offset=params.rf_gate_offset, width=params.pump_mod_duty / 100 / params.pump_mod_freq, channel=channels["pump_gate"])
    signals.set_pulse_dcycle(params.pump_mod_duty, channel=channels["pump_gate"])
    signals.set_output(True, channel=channels["pump_gate"])
    signals.set_sync_state(True, channel=channels["pump_gate"])


def configure_trigger(ctx):
    params, devices, state, directory = ctx.params, ctx.devices, ctx.state, ctx.directory
    signals, channels, hf2 = (devices[key] for key in ("signals", "channels", "hf2"))
    validate_levels("Time_sequence_2", params.xy_trigger_amplitude, params.xy_trigger_offset)
    signals.setup_square(freq=params.xy_trigger_freq, amplitude=params.xy_trigger_amplitude, offset=params.xy_trigger_offset, dcycle=params.xy_trigger_duty, phase=params.xy_trigger_phase, channel=channels["trigger"])
    signals.phase_init(channel=channels["trigger"])
    signals.set_output(True, channel=channels["trigger"])
    # 仪器在波形为 DC 时会自动关闭通道 SYNC，切回方波不会自动恢复，需显式重新打开；
    # 时序信号2 的主输出与 SYNC 均持续运行，含安全收尾之后。
    signals.set_sync_state(True, channel=channels["trigger"])
    # 此后仅操作被触发通道，共同触发持续运行到安全收尾。

