"""本实验 DirectAW 合成、上传与连续外触发。"""
import numpy as np
import math
from lab_workflows.current_feedback import load_corrected_control_waveform
from lockin_amplifier import demod
from functools import partial
from lab_workflows.common import validate_safety_limit

def wrap_deg(ctx, angle_deg):
    """把角度压到 [-180, 180) 区间。"""
    return (float(angle_deg) + 180.0) % 360.0 - 180.0

def build_aw_params(ctx, v_waveform, vpp):
    """按自动满量程把物理电压波形转换为 DG arbitrary 参数。

    vpp 由调用方按包络峰值统一计算（2 × max|envelope_v|，X/Y 共用、
    与校相相位无关），offset 恒为 0。
    """
    v_waveform = np.asarray(v_waveform, dtype=float)
    v_min = float(np.min(v_waveform))
    v_max = float(np.max(v_waveform))
    half_range = 0.5 * float(vpp)
    if half_range < ctx.XY_AW_AUTO_FLOOR_VPP / 2.0:
        raise ValueError(f'''自动 AW 满量程 {vpp:.6f} Vpp 低于仪器最小幅度 {ctx.XY_AW_AUTO_FLOOR_VPP} Vpp；请更换控制包络或检查 XY 标定''')
    normalized = v_waveform / half_range
    return {'waveform_V': v_waveform, 'normalized': normalized, 'vpp': float(vpp), 'offset': 0.0, 'min': v_min, 'max': v_max, 'output_min': -half_range, 'output_max': half_range, 'max_abs_normalized': float(np.max(np.abs(normalized)))}

def load_control_envelope(ctx):
    """加载 Omega_ctrl(t)，支持 CSV 或闭环冻结波形。"""
    source = str(ctx.ARB_WAVEFORM_SOURCE)
    if source == 'corrected_run':
        corrected = load_corrected_control_waveform(ctx.project_root, ctx.CORRECTED_CONTROL_SOURCE_RUN)
        time_s = np.asarray(corrected.time_s, dtype=float)
        omega_ctrl_hz = np.asarray(corrected.omega_ctrl_hz, dtype=float)
        time_unit = 's'
        waveform_path = corrected.waveform_path
    else:
        waveform_path = ctx.project_root / 'experiments' / ctx.ARB_WAVEFORM_FILE
        with open(waveform_path, encoding='utf-8') as f:
            header = f.readline().strip().lower()
        data = np.loadtxt(waveform_path, delimiter=',', skiprows=1)
        if data.ndim != 2 or data.shape[1] < 2:
            raise ValueError(f'''{waveform_path} 需要至少两列: time, Omega_ctrl_Hz''')
        if len(data) > ctx.MAX_ARB_POINTS:
            raise ValueError(f'''AW 点数 {len(data)} 超出当前信号源限制 {ctx.MAX_ARB_POINTS}''')
        time_raw = data[:, 0].astype(float)
        if 'time_ms' in header:
            time_s = time_raw / 1000.0
            time_unit = 'ms'
        else:
            time_s = time_raw
            time_unit = 's'
        omega_ctrl_hz = data[:, 1].astype(float)
    if len(time_s) < 2:
        raise ValueError('控制波形至少需要 2 个采样点')
    dt = float(np.median(np.diff(time_s)))
    period_s = dt * len(time_s)
    repeat_freq_hz = 1.0 / period_s
    requested_period_s = 1.0 / ctx.A_ENV_FREQ
    if abs(period_s - requested_period_s) / requested_period_s > 0.001:
        raise ValueError(f'''CSV 周期 {period_s:.9g} s 与 A_ENV_FREQ={ctx.A_ENV_FREQ:.9g} Hz 对应周期 {requested_period_s:.9g} s 不一致''')
    larmor_cycles = ctx.PUMP_MOD_FREQ / repeat_freq_hz
    if abs(larmor_cycles - round(larmor_cycles)) > 1e-06:
        raise ValueError(f'''Pump/Larmor 频率 {ctx.PUMP_MOD_FREQ:.9g} Hz 不是 AW_REPEAT_FREQ={repeat_freq_hz:.9g} Hz 的整数倍''')
    envelope_v = (omega_ctrl_hz - ctx.XY_CTRL_B_HZ) / ctx.XY_CTRL_K_HZ_PER_V
    return {'path': waveform_path, 'source': source, 'source_run': ctx.CORRECTED_CONTROL_SOURCE_RUN if source == 'corrected_run' else None, 'time_s': time_s, 'time_unit': time_unit, 'omega_ctrl_hz': omega_ctrl_hz, 'envelope_v': envelope_v, 'dt_s': dt, 'period_s': period_s, 'repeat_freq_hz': repeat_freq_hz, 'requested_repeat_freq_hz': ctx.A_ENV_FREQ, 'larmor_cycles_per_aw': int(round(larmor_cycles))}

def synthesize_xy_waveforms(ctx, phase_deg):
    """按固定 90 deg 正交关系生成 X/Y 直接任意波，满量程自动跟随包络峰值。"""
    aw = load_control_envelope(ctx)
    phase_rad = math.radians(float(phase_deg))
    quad_rad = math.radians(ctx.XY_CTRL_QUAD)
    carrier_phase = 2.0 * np.pi * ctx.PUMP_MOD_FREQ * aw['time_s'] + phase_rad
    x_wave = aw['envelope_v'] * np.cos(carrier_phase)
    y_wave = aw['envelope_v'] * np.cos(carrier_phase + quad_rad)
    auto_vpp = 2.0 * float(np.max(np.abs(aw['envelope_v'])))
    x_aw = build_aw_params(ctx, x_wave, auto_vpp)
    y_aw = build_aw_params(ctx, y_wave, auto_vpp)
    return (aw, x_aw, y_aw)

def validate_xy_waveform_safety(ctx, x_aw, y_aw):
    """检查直接输出到 X/Y 线圈的 AW 电压范围。"""
    for value in (x_aw['min'], x_aw['max'], x_aw['offset'], x_aw['output_min'], x_aw['output_max']):
        validate_safety_limit('X_magnetic_field', value, ctx.LIMITS)
    for value in (y_aw['min'], y_aw['max'], y_aw['offset'], y_aw['output_min'], y_aw['output_max']):
        validate_safety_limit('Y_magnetic_field', value, ctx.LIMITS)

def upload_xy_aw(ctx, phase_deg, outputs_on=True):
    """重新合成并上传 X/Y direct AW，返回保存用的波形参数。"""
    aw, x_aw, y_aw = synthesize_xy_waveforms(ctx, phase_deg)
    validate_xy_waveform_safety(ctx, x_aw, y_aw)
    for ch in (1, 2):
        ctx.dg_comp.set_burst_state(False, channel=ch)
        ctx.dg_comp.set_mod_state(False, channel=ch)
        ctx.dg_comp.set_output(False, channel=ch)
    ctx.dg_comp.setup_arbitrary(x_aw['normalized'].copy(), freq=aw['repeat_freq_hz'], amplitude=x_aw['vpp'], offset=x_aw['offset'], phase=0.0, channel=1, output=False)
    ctx.dg_comp.setup_arbitrary(y_aw['normalized'].copy(), freq=aw['repeat_freq_hz'], amplitude=y_aw['vpp'], offset=y_aw['offset'], phase=0.0, channel=2, output=False)
    for ch in (1, 2):
        ctx.dg_comp.set_burst_state(True, channel=ch)
        ctx.dg_comp.set_burst_mode('INFinity', channel=ch)
        ctx.dg_comp.set_burst_trigger_source('EXTernal', channel=ch)
        ctx.dg_comp.set_burst_trigger_slope('POSitive', channel=ch)
        ctx.dg_comp.set_burst_phase(0.0, channel=ch)
        ctx.dg_comp.set_output(bool(outputs_on), channel=ch)
    print(f'''X/Y direct AW 已上传: repeat={aw['repeat_freq_hz']:.3f} Hz, phase={phase_deg:.2f} deg, X/Y={x_aw['vpp']:.3f} Vpp, offset={x_aw['offset']:.3f} V''')
    return (aw, x_aw, y_aw)

def save_xy_aw_waveforms(ctx, path, aw, x_aw, y_aw, phase_deg):
    """保存 direct-AW 波形快照，便于离线检查。"""
    np.savez(path, time_s=aw['time_s'], omega_ctrl_Hz=aw['omega_ctrl_hz'], envelope_V=aw['envelope_v'], x_waveform_V=x_aw['waveform_V'], y_waveform_V=y_aw['waveform_V'], x_normalized=x_aw['normalized'], y_normalized=y_aw['normalized'], x_vpp=np.float64(x_aw['vpp']), y_vpp=np.float64(y_aw['vpp']), x_offset=np.float64(x_aw['offset']), y_offset=np.float64(y_aw['offset']), x_output_min=np.float64(x_aw['output_min']), x_output_max=np.float64(x_aw['output_max']), y_output_min=np.float64(y_aw['output_min']), y_output_max=np.float64(y_aw['output_max']), x_max_abs_normalized=np.float64(x_aw['max_abs_normalized']), y_max_abs_normalized=np.float64(y_aw['max_abs_normalized']), repeat_freq_Hz=np.float64(aw['repeat_freq_hz']), xy_ctrl_phase_deg=np.float64(phase_deg), xy_ctrl_quad_deg=np.float64(ctx.XY_CTRL_QUAD), xy_ctrl_k_Hz_per_V=np.float64(ctx.XY_CTRL_K_HZ_PER_V), xy_ctrl_b_Hz=np.float64(ctx.XY_CTRL_B_HZ))

def validate_trigger_square_levels(ctx):
    half_amp = ctx.XY_TRIGGER_AMPLITUDE / 2.0
    low_level = ctx.XY_TRIGGER_OFFSET - half_amp
    high_level = ctx.XY_TRIGGER_OFFSET + half_amp
    validate_safety_limit('X_magnetic_field_AM', low_level, ctx.LIMITS)
    validate_safety_limit('X_magnetic_field_AM', high_level, ctx.LIMITS)
    validate_safety_limit('Y_magnetic_field_AM', low_level, ctx.LIMITS)
    validate_safety_limit('Y_magnetic_field_AM', high_level, ctx.LIMITS)

def configure_dg_am_reference_trigger(ctx):
    """设置 dg_am CH1/CH2 为固定同相方波，作为 dg_comp 外触发。"""
    validate_trigger_square_levels(ctx)
    for ch in (1, 2):
        ctx.dg_am.set_burst_state(False, channel=ch)
        ctx.dg_am.set_mod_state(False, channel=ch)
        ctx.dg_am.setup_square(freq=ctx.XY_TRIGGER_FREQ, amplitude=ctx.XY_TRIGGER_AMPLITUDE, offset=ctx.XY_TRIGGER_OFFSET, dcycle=ctx.XY_TRIGGER_DUTY, phase=ctx.XY_TRIGGER_PHASE, channel=ch)
        ctx.dg_am.set_output(True, channel=ch)
    for ch in (1, 2):
        ctx.dg_am.phase_init(channel=ch)
    print(f'''dg_am CH1/CH2: fixed square {ctx.XY_TRIGGER_FREQ} Hz, outputs ON -> dg_comp Ext Trig''')

def apply_z_rf_phase(ctx, dg_sweep_inst, phase_deg):
    """调整连续 Z 射频正弦波相位，不使用 Burst 或外触发。"""
    dg_sweep_inst.set_burst_state(False, channel=1)
    dg_sweep_inst.set_mod_state(False, channel=1)
    dg_sweep_inst.set_phase_adjust(float(phase_deg), channel=1)

def read_demod_theta_deg(ctx, demod_idx=None):
    if demod_idx is None:
        demod_idx = ctx.DEMOD0_IDX
    sample = demod.read_demod_sample(ctx.hfi, demod_idx=demod_idx)
    theta_deg = float(math.degrees(sample['theta']))
    return (wrap_deg(ctx, theta_deg), sample)

def apply_phase_and_measure(ctx, phase_deg):
    if ctx.first_phase_measurement:
        ctx.first_phase_measurement = False
    else:
        upload_xy_aw(ctx, phase_deg, outputs_on=True)
        ctx.wait(ctx.SYSTEM_SETTLE_S)
    theta_deg, sample = read_demod_theta_deg(ctx, ctx.DEMOD0_IDX)
    print(f'''  phase={phase_deg:.2f} deg, theta={theta_deg:+.2f} deg, R={sample['r']:.6e}''')
    return sample
