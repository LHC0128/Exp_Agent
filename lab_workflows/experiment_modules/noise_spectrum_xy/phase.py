"""Demod0 和 XY 正弦整体相位步骤。"""
import math
from functools import partial
from lockin_amplifier import SignalInputConfig, OscillatorConfig, DemodulatorConfig, demod
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import PhaseCalibrationConfig, calibrate_demod_phase, DirectAWPhaseCalibrationConfig as XYPhaseCalibrationConfig, calibrate_direct_aw_phase as calibrate_xy_phase
from .models import NoiseSpectrumXYParams
from ._helpers import _RuntimeCancellation, wait, set_temp_switch

def calibrate_hf2_phase(hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """关闭温控和 XY，完成 HF2 校相后恢复温控。"""
    # ---- HF2 解调器相位校准（设置 XY 磁场前执行） ----
    print("\n关闭温度开关（相位校准前，消除温控磁场干扰）...")
    set_temp_switch(dg_temp, False, params, limits)

    # 确认 X/Y 补偿磁场处于关闭状态（尚未配置）
    print("确认 X/Y 补偿磁场关闭...")
    dg_comp.set_output(False, channel=1)
    dg_comp.set_output(False, channel=2)

    # 配置信号输入
    sig_in_cfg = SignalInputConfig(
        input_index=0, range=params.hf2_signal_range,
        diff=False,
    )
    demod.configure_signal_input(hfi, sig_in_cfg)
    print(f"信号输入已配置: {params.hf2_signal_range} V range, AC coupled")

    # 配置振荡器
    osc_cfg = OscillatorConfig(
        osc_index=0, frequency=params.hf2_osc_freq, source="manual",
    )
    demod.configure_oscillator(hfi, osc_cfg)
    print(f"振荡器已配置: {params.hf2_osc_freq/1e3:.0f} kHz")

    # 配置解调器
    demod_cfg = DemodulatorConfig(
        demod_index=0, enable=True, rate=params.hf2_demod_rate,
        input_channel=0, osc_select=0, harmonic=1,
        time_constant=params.hf2_demod_tc, order=params.hf2_demod_order, phase=0.0,
    )
    actual_rate = demod.configure_demodulator(hfi, demod_cfg)
    print(f"解调器 0 已配置: rate={actual_rate:.0f} Sa/s, TC={params.hf2_demod_tc*1000:.3f} ms")

    # 自动相位校准
    print("正在进行相位校准...")
    demod0_phase_result = calibrate_demod_phase(
        hfi,
        PhaseCalibrationConfig(
            demod_idx=params.hf2_demod_idx,
            tolerance_deg=1.0,
            max_attempts=5,
            settle_time=0.2,
        ),
        cancellation=_RuntimeCancellation(),
    )
    calibrated_phase = demod0_phase_result.phase_shift_deg
    print(f"相位校准完成: {calibrated_phase:.2f}°")

    # 读一组样本验证
    sample = demod0_phase_result.after_sample
    print(f"校准后样本: R={sample['r']:.6f}, X={sample['x']:.6f}, Y={sample['y']:.6f}")

    wait(0.5)

    # 恢复温控
    print("恢复温度开关...")
    set_temp_switch(dg_temp, True, params, limits, restore_wait=True)
    return calibrated_phase


def validate_sine_peak(peak_v, limits):
    """校验正弦波的正负峰值。"""
    peak_v = abs(float(peak_v))
    for key in ("X_magnetic_field", "Y_magnetic_field"):
        validate_safety_limit(key, -peak_v, limits)
        validate_safety_limit(key, peak_v, limits)
    return peak_v


def verify_xy_mode(dg_comp, params):
    """分步等待允许现场查看设备，重跑前拒绝已改变的波形或触发模式。"""
    for channel in (1, 2):
        if (not dg_comp.get_shape(channel=channel).upper().startswith("SIN")
                or not dg_comp.get_burst_state(channel=channel)
                or not dg_comp.get_burst_mode(channel=channel).upper().startswith("INF")
                or not dg_comp.get_burst_trigger_source(channel=channel).upper().startswith("EXT")
                or not math.isclose(dg_comp.get_frequency(channel=channel), params.xy_ctrl_freq, rel_tol=1e-6)):
            raise RuntimeError(f"XY CH{channel} 模式已改变，请结束会话并重新初始化")


def set_xy_sine_phase(dg_comp, phase_deg, quad_deg, outputs_on=True):
    """用 OFF→相位→ON 使两路重新等待同一外触发沿。"""
    phases = (
        (1, float(phase_deg) % 360.0),
        (2, (float(phase_deg) + quad_deg) % 360.0),
    )
    for channel, _ in phases:
        dg_comp.set_output(False, channel=channel)
    for channel, phase in phases:
        dg_comp.set_burst_phase(phase, channel=channel)
    for channel, _ in phases:
        dg_comp.set_output(bool(outputs_on), channel=channel)


def configure_xy_sine(dg_comp, params: NoiseSpectrumXYParams, limits):
    """一次配置 X/Y 正弦模式与外触发无限 Burst。"""
    peak_v = validate_sine_peak(params.xy_calib_envelope_v, limits)
    if peak_v == 0.0:
        raise ValueError("初始正弦峰值必须大于 0")
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
        dg_comp.set_burst_state(False, channel=channel)
        dg_comp.set_mod_state(False, channel=channel)
        dg_comp.setup_sine(
            freq=params.xy_ctrl_freq,
            amplitude=2.0 * peak_v,
            offset=0.0,
            phase=0.0,
            channel=channel,
        )
        dg_comp.set_output(False, channel=channel)
        dg_comp.set_burst_state(True, channel=channel)
        dg_comp.set_burst_mode("INFinity", channel=channel)
        dg_comp.set_burst_trigger_source("EXTernal", channel=channel)
        dg_comp.set_burst_trigger_slope("POSitive", channel=channel)
    set_xy_sine_phase(dg_comp, params.xy_ctrl_phase, params.xy_ctrl_quad, outputs_on=False)


def set_xy_sine_peak(dg_comp, peak_v, phase_deg, quad_deg, limits):
    """关断两路后调幅，再按已校准相位重新等待共用外触发。"""
    peak_v = validate_sine_peak(peak_v, limits)
    for channel in (1, 2):
        dg_comp.set_output(False, channel=channel)
    if peak_v == 0.0:
        return
    for channel in (1, 2):
        dg_comp.set_amplitude(2.0 * peak_v, channel=channel)
    # 调幅可能改变实际响应相位；复用校相的重触发顺序，不重配正弦/Burst。
    set_xy_sine_phase(dg_comp, phase_deg, quad_deg, outputs_on=True)


def measure_xy_phase(phase_deg, *, hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """测量一次正弦响应相位；成功后恢复温控并等待。"""
    set_xy_sine_phase(dg_comp, phase_deg, params.xy_ctrl_quad, outputs_on=True)
    set_temp_switch(dg_temp, False, params, limits)
    wait(1.0)
    sample_data = demod.read_demod_sample(hfi, demod_idx=params.hf2_demod_idx)
    phase_offset = float(math.degrees(sample_data["theta"]))
    print(f"  phase={phase_deg:.2f}°, theta={phase_offset:+.2f}°")
    print(f"  样本: R={sample_data['r']:.6f}, X={sample_data['x']:.6f}, Y={sample_data['y']:.6f}")
    # 失败直接交给 run 的统一收尾，避免恢复等待覆盖原异常。
    set_temp_switch(dg_temp, True, params, limits, restore_wait=True)
    return sample_data


def calibrate_xy_control(hfi, dg_comp, dg_temp, params: NoiseSpectrumXYParams, limits):
    """使用已初始化的正弦 Burst，迭代校相并返回本次最终相位。"""
    print(f"  正弦载波: {params.xy_ctrl_freq:.3f} Hz, offset=0 V")
    print(
        f"  校相峰值: {params.xy_calib_envelope_v:.3f} V "
        f"({2.0 * params.xy_calib_envelope_v:.3f} Vpp), "
        f"X/Y 正交相位差={params.xy_ctrl_quad:.2f}°"
    )
    print("  X/Y 输出 OFF (等待正弦 Burst 整体相位校准)")
    print("硬件同步要求: Time_sequence_2 CH2 的 100 Hz 固定方波经三通 -> dg_comp CH1/CH2 Ext Trig")
    print(
        f"相位关系: Time_sequence_2 trigger={params.xy_trigger_phase:.2f}° @ "
        f"{params.xy_trigger_freq:.1f} Hz, sine burst phase={params.xy_ctrl_phase:.2f}°"
    )

    # XY_CTRL_PHASE 校准：保持 Time_sequence_2 基准不变，迭代正弦 Burst 相位
    print("=" * 60)
    print("校准 XY_CTRL_PHASE（Time_sequence_2 固定基准 + 正弦 Burst 相位迭代）")
    print("=" * 60)
    phase_result = calibrate_xy_phase(
        params.xy_ctrl_phase,
        partial(measure_xy_phase, hfi=hfi, dg_comp=dg_comp, dg_temp=dg_temp,
                params=params, limits=limits),
        XYPhaseCalibrationConfig(
            tolerance_deg=params.xy_phase_cal_tol_deg,
            max_measurements=params.xy_phase_cal_max_iter,
            minimum_r_v=params.xy_phase_cal_min_r_v,
            minimum_r_ratio=params.xy_phase_cal_min_r_ratio,
        ),
        cancellation_check=check_cancelled,
    )
    xy_phase = phase_result.final_phase_deg
    if not phase_result.converged:
        print(f"\n达到最大测量次数 {params.xy_phase_cal_max_iter}，校准未收敛")
    # 仅校相正常返回后重新启用 XY；失败由 run 统一关闭。
    set_temp_switch(dg_temp, True, params, limits)
    set_xy_sine_phase(dg_comp, xy_phase, params.xy_ctrl_quad, outputs_on=True)
    print("\n✅ XY_CTRL_PHASE 校准完成")
    print(f"  XY_CTRL_PHASE = {xy_phase:.2f}°")
    print(f"  XY_CTRL_QUAD = {params.xy_ctrl_quad:.2f}°")
    print(f"  Y_CTRL_PHASE = {(xy_phase + params.xy_ctrl_quad) % 360:.2f}°")
    return phase_result


