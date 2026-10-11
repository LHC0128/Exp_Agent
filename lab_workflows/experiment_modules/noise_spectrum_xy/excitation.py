"""Pump 载波、门控与两路共用外触发。"""
from ...common import validate_safety_limit
from .models import NoiseSpectrumXYParams

def validate_pump_levels(params: NoiseSpectrumXYParams, limits):
    """连接前及配置前校验 Pump 载波、占空比和门控电平。"""
    validate_safety_limit("Pump_modulation", params.pump_mod_amplitude, limits)
    validate_safety_limit("PUMP_MOD_DUTY", params.pump_mod_duty, limits)
    for level in (params.rf_gate_offset - params.rf_gate_amplitude / 2,
                  params.rf_gate_offset + params.rf_gate_amplitude / 2):
        validate_safety_limit("Time_sequence", level, limits)


def configure_pump(dg_mod, params: NoiseSpectrumXYParams, limits):
    """配置一次 Pump 载波和门控，后续校相和扫描保持运行。"""
    validate_pump_levels(params, limits)
    print("CH1: 配置 100MHz 连续正弦波...")
    dg_mod.setup_sine(freq=100e6, amplitude=params.pump_mod_amplitude,
                      offset=0.0, phase=0.0, channel=1)
    print(f"  100MHz, 幅度 {params.pump_mod_amplitude*1000:.0f} mVpp → RF 开关 IN")

    pulse_width = (params.pump_mod_duty / 100.0) / params.pump_mod_freq
    print(f"\nCH2: 配置 {params.pump_mod_freq/1e3:.0f} kHz 脉冲门控...")
    print(f"  脉宽: {pulse_width*1e6:.2f} μs ({params.pump_mod_duty}% duty)")
    dg_mod.setup_pulse(freq=params.pump_mod_freq, amplitude=params.rf_gate_amplitude,
                       offset=params.rf_gate_offset, width=pulse_width, channel=2)
    print(f"  {params.pump_mod_freq/1e3:.0f} kHz → RF 开关 CTRL")

    # 启用 Pump 调制输出；dg_comp 外触发由 Time_sequence_2 CH2 经三通提供。
    dg_mod.set_output(True, channel=1)
    dg_mod.set_output(True, channel=2)
    dg_mod.set_sync_state(False, channel=2)
    print("  dg_mod 输出: CH1(100MHz) ON, CH2(脉冲) ON")
    print("  dg_mod CH2 SYNC 未作为 dg_comp 触发源")


def validate_trigger_square_levels(params: NoiseSpectrumXYParams, limits):
    """检查 Time_sequence_2 方波触发电平。"""
    half_amp = params.xy_trigger_amplitude / 2.0
    low_level = params.xy_trigger_offset - half_amp
    high_level = params.xy_trigger_offset + half_amp
    validate_safety_limit("Time_sequence_2", low_level, limits)
    validate_safety_limit("Time_sequence_2", high_level, limits)


def configure_sequence_2_trigger(dg_sweep, params: NoiseSpectrumXYParams, limits):
    """设置 Time_sequence_2 CH2 为三通共用的固定方波触发源。"""
    validate_trigger_square_levels(params, limits)
    trigger_phase = params.xy_trigger_phase % 360
    dg_sweep.set_burst_state(False, channel=2)
    dg_sweep.set_mod_state(False, channel=2)
    dg_sweep.setup_square(
        freq=params.xy_trigger_freq,
        amplitude=params.xy_trigger_amplitude,
        offset=params.xy_trigger_offset,
        dcycle=params.xy_trigger_duty,
        phase=trigger_phase,
        channel=2,
    )
    dg_sweep.set_output(True, channel=2)
    dg_sweep.phase_init(channel=2)

    print(
        f"  Time_sequence_2 CH2: fixed SQUARE {params.xy_trigger_freq} Hz, "
        f"phase={trigger_phase:.2f}°, output ON -> splitter -> dg_comp CH1/CH2 Ext Trig"
    )


