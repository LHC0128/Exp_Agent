"""本实验 DirectAW 合成、上传与连续外触发。"""
import json
import math
import numpy as np
from functools import partial
from lab_workflows.common import validate_safety_limit

def reset_dg_channels(ctx, inst):
    """把 DG4000 双通道复位到安全关闭状态。"""
    for ch in (1, 2):
        inst.set_burst_state(False, channel=ch)
        inst.set_mod_state(False, channel=ch)
        inst.setup_dc(0.0, channel=ch)
        inst.set_output(False, channel=ch)

def wrap_deg(ctx, angle_deg):
    return (float(angle_deg) + 180.0) % 360.0 - 180.0

def build_direct_aw(ctx, envelope_v, phase_deg):
    """生成恒定包络的 X/Y 正交 DirectAW 波形，满量程自动跟随包络峰值。

    vpp = 2 × |envelope_v|，offset 恒为 0；零包络点返回全零波形和下限幅度。
    """
    envelope_v = float(envelope_v)
    phase_rad = math.radians(float(phase_deg))
    quad_rad = math.radians(ctx.XY_CTRL_QUAD_deg)
    t_s = np.arange(ctx.XY_AW_POINTS, dtype=float) / (ctx.XY_AW_POINTS * ctx.XY_AW_REPEAT_FREQ_Hz)
    carrier_phase = 2.0 * np.pi * ctx.XY_CARRIER_FREQ_Hz * t_s + phase_rad
    x_v = envelope_v * np.cos(carrier_phase)
    y_v = envelope_v * np.cos(carrier_phase + quad_rad)
    vpp = 2.0 * abs(envelope_v)
    if vpp == 0.0:
        x_norm = np.zeros_like(x_v)
        y_norm = np.zeros_like(y_v)
        vpp = ctx.AW_AMPLITUDE_FLOOR_VPP
    elif vpp < ctx.AW_AMPLITUDE_FLOOR_VPP:
        raise ValueError(f'''envelope={envelope_v:.6f} V 过小，自动 AW 幅度 {vpp:.6f} Vpp 低于仪器最小幅度 {ctx.AW_AMPLITUDE_FLOOR_VPP} Vpp''')
    else:
        x_norm = x_v / (vpp / 2.0)
        y_norm = y_v / (vpp / 2.0)
    return {'time_s': t_s, 'envelope_V': envelope_v, 'phase_deg': float(phase_deg), 'aw_vpp': float(vpp), 'aw_offset': 0.0, 'x_waveform_V': x_v, 'y_waveform_V': y_v, 'x_normalized': x_norm, 'y_normalized': y_norm}

def configure_trigger_source(ctx):
    """打开并同相初始化 dg_trigger，之后在 DirectAW 迭代中保持连续输出。"""
    low = ctx.XY_TRIGGER_OFFSET_V - ctx.XY_TRIGGER_AMPLITUDE_Vpp / 2.0
    high = ctx.XY_TRIGGER_OFFSET_V + ctx.XY_TRIGGER_AMPLITUDE_Vpp / 2.0
    for key in ('X_magnetic_field_AM', 'Y_magnetic_field_AM'):
        validate_safety_limit(key, low, ctx.LIMITS)
        validate_safety_limit(key, high, ctx.LIMITS)
    for ch in (1, 2):
        ctx.dg_trigger.set_burst_state(False, channel=ch)
        ctx.dg_trigger.set_mod_state(False, channel=ch)
        ctx.dg_trigger.setup_square(freq=ctx.XY_TRIGGER_FREQ_Hz, amplitude=ctx.XY_TRIGGER_AMPLITUDE_Vpp, offset=ctx.XY_TRIGGER_OFFSET_V, dcycle=ctx.XY_TRIGGER_DUTY_pct, phase=ctx.XY_TRIGGER_PHASE_deg, channel=ch)
        ctx.dg_trigger.set_output(True, channel=ch)
    for ch in (1, 2):
        ctx.dg_trigger.phase_init(channel=ch)

def upload_direct_aw(ctx, envelope_v, phase_deg, output=True):
    """保持 dg_trigger 连续运行，重传两路 AW 后依次打开 Burst 和输出。

    满量程自动跟随包络峰值；零包络点保持输出 OFF。
    """
    wave = build_direct_aw(ctx, envelope_v, phase_deg)
    for key in ('X_magnetic_field', 'Y_magnetic_field'):
        for value in (-wave['aw_vpp'] / 2, wave['aw_vpp'] / 2):
            validate_safety_limit(key, value, ctx.LIMITS)
    nonzero = float(envelope_v) != 0.0
    for ch in (1, 2):
        ctx.dg_comp.set_burst_state(False, channel=ch)
        ctx.dg_comp.set_mod_state(False, channel=ch)
        ctx.dg_comp.set_output(False, channel=ch)
    for ch, values in ((1, wave['x_normalized']), (2, wave['y_normalized'])):
        ctx.dg_comp.setup_arbitrary(values.copy(), freq=ctx.XY_AW_REPEAT_FREQ_Hz, amplitude=wave['aw_vpp'], offset=0.0, phase=0.0, channel=ch, output=False)
    for ch in (1, 2):
        ctx.dg_comp.set_burst_state(True, channel=ch)
        ctx.dg_comp.set_burst_mode('INFinity', channel=ch)
        ctx.dg_comp.set_burst_trigger_source('EXTernal', channel=ch)
        ctx.dg_comp.set_burst_trigger_slope('POSitive', channel=ch)
        ctx.dg_comp.set_burst_phase(0.0, channel=ch)
        ctx.dg_comp.set_output(bool(output) and nonzero, channel=ch)
    return wave

def save_waveform_snapshot(ctx, path, wave):
    np.savez(path, time_s=wave['time_s'], envelope_V=np.float64(wave['envelope_V']), phase_deg=np.float64(wave['phase_deg']), x_waveform_V=wave['x_waveform_V'], y_waveform_V=wave['y_waveform_V'], x_normalized=wave['x_normalized'], y_normalized=wave['y_normalized'], aw_vpp_V=np.float64(wave['aw_vpp']), aw_offset_V=np.float64(wave['aw_offset']), repeat_freq_Hz=np.float64(ctx.XY_AW_REPEAT_FREQ_Hz), carrier_freq_Hz=np.float64(ctx.XY_CARRIER_FREQ_Hz), quadrature_deg=np.float64(ctx.XY_CTRL_QUAD_deg))

def update_json_index(ctx, path, records):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
