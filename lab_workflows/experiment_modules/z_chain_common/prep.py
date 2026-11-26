"""Z 脉冲链公共硬件编排：连接、时钟、激励、主磁场处置与安全收尾；导入不连接设备。

Z 通道默认链路与 ``pi_pulse_calibration`` 一致（DG → 1200B → 阻尼电阻 → Z 线圈 → 采样电阻）；
参数模型声明 ``z_drive_amplifier_enabled = False`` 时功放已物理旁路，DG4000 直驱 Z 线圈，
本模块不连接、不配置也不开关 1200B。差别另有一处：Z 信号源由 ``Time_sequence_2`` 外触发做
burst 门控，判据改由 MFLI 第 0 路解调器的 R 给出。器件操作复用 ``lab_workflows.steps`` 的
公共步骤，本模块只保留公共的激励配置与收尾清单；实验专属预检与参数模型留在各实验包。
"""
from __future__ import annotations

import math

import numpy as np
from aigtek_1200b import Aigtek1200BInstrument
from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from oscilloscope import create_oscilloscope
from tec_controller import TECInstrument

from ...common import load_safety_limits, validate_safety_limit
from ...devices import create_lockin
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
                      TemperatureSwitchRestore, connect_signal_generator_routes, disconnect_device_mapping,
                      initialize_lockin_inputs, restore_main_field_state, run_safety_shutdown,
                      set_temperature_switch, snapshot_main_field_state, synchronize_connected_clocks)
from ...steps.coil_drive_chain import SafetyAbort, current_limit, set_amp_output, sleep
from ..bperp_null import z_waveform as tail_waveform
from ..z_chain_stability import processing as pulse_processing
from .params import (ARB_POINTS, PulseChainParams, amplifier_enabled, amps_per_drive_volt,
                     estimated_peak_current_ma, loop_resistance_ohm, pulse_vpp)
from .keys import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, LOCKIN_KEY, MAIN_FIELD_KEY,
                   REQUIRED_MAPPING_KEYS, RF_KEY, SCOPE_KEY, SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY,
                   TRIGGER_KEY, X_KEY)

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "amplifier_safety_check",
    "channel_bias_v", "channel_key", "clocks", "configure_chain", "configure_dc_bias",
    "configure_dc_side", "configure_lockin", "configure_main_field", "configure_rf",
    "configure_trigger", "configure_x_bias", "rf_role",
    "configure_z_idle", "connect", "current_limit", "disable_main_field", "enable_temperature_switch",
    "phase_drift_deg", "plan", "pulse_wave", "rf_levels", "set_rf_phase", "shutdown", "sleep",
]

# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
# 任意波播放采样率上限（DG4000 为 500 MSa/s）；用于预检脉冲沿是否被采样率截断。
ARB_SAMPLE_RATE_CHECK = 500e6
# 射频与 Z 都用无限 burst，靠共同触发启动；NCYCles 在 INFinity 下被忽略，写上限值。
BURST_NCYCLES = 50000
# 解调相位回读容差：设备按 1e-6 分辨率写，超过这个差别即视为设定未生效。
PHASE_READBACK_TOLERANCE_DEG = 0.5
# 共同触发的固定接线电平：5 Vpp 方波、2.5 V 偏置、50% 占空比，频率跟随 Y_RF_FREQ_HZ。
TRIGGER_VPP = 5.0
TRIGGER_OFFSET_V = 2.5
TRIGGER_DUTY = 50.0
# 温度开关（Temp_Switch）开/关电平，与 set_temperature_switch 的默认值一致。
TEMP_SWITCH_ON_V = 5.0


def plan(p: PulseChainParams) -> dict:
    """计划中的电平、估算峰值电流与波形参数；预检与执行共用。

    有放大器时峰值电流就是参数目标值，链路与历史行为一致；DG 直驱时按
    ``Z_PULSE_VPP / 2`` 与已知回路电阻估算，预检、功耗核算和报告都用这个估算值。
    """
    vpp = pulse_vpp(p)
    peak_ma = estimated_peak_current_ma(p)
    return dict(pulse_vpp=vpp,
                pulse_offset_v=0.0,
                pulse_max_v=vpp / 2.0,
                pulse_min_v=-vpp / 2.0,
                pulse_peak_current_a=peak_ma * 1e-3,
                estimated_peak_current_ma=peak_ma,
                pulse_period_us=p.pulse_period_us,
                arb_sample_rate_sa_s=ARB_POINTS / (p.pulse_period_us * 1e-6))


def pulse_wave(p: PulseChainParams, width_us: float) -> tuple[np.ndarray, dict]:
    """写入 Z 通道的一个周期归一化电流（最大绝对值 1 ↔ 估算峰值电流）与该脉宽的记录。

    关闭拖尾补偿时为原升余弦 ±π 双脉冲；开启时每个脉冲后接精确反号指数尾，尾幅 A 相对主脉冲平顶，
    按周期延拓叠加后整体归一化，平顶电流 = 估算峰值电流 / scale。
    """
    peak_ma = estimated_peak_current_ma(p)
    if not p.z_tail_comp_enabled:
        wave = pulse_processing.pulse_waveform(p.pulse_period_us, width_us, p.z_pulse_edge_us, points=ARB_POINTS)
        return wave, dict(tail_comp=False, width_us=float(width_us), estimated_peak_current_ma=peak_ma,
                          mean_square=float(np.mean(np.square(wave))))
    tail_a, tau_c = tail_waveform.exact_tail(width_us, p.z_pulse_edge_us, p.z_tail_s_f, p.z_tail_tau_e_us)
    wave, scale = tail_waveform.tail_compensated_waveform(p.pulse_period_us, width_us, p.z_pulse_edge_us,
                                                          tail_a, tau_c, ARB_POINTS)
    half = ARB_POINTS // 2
    return wave, dict(tail_comp=True, width_us=float(width_us), tail_a=float(tail_a), tau_c_us=float(tau_c),
                      scale=float(scale), plateau_current_ma=peak_ma / scale,
                      tail_start_current_ma=-tail_a * peak_ma / scale,
                      estimated_peak_current_ma=peak_ma,
                      mean_square=float(np.mean(np.square(wave))),
                      antisymmetry_error=float(np.max(np.abs(wave[:half] + wave[half:2 * half]))))


def rf_role(p: PulseChainParams) -> str:
    """射频所在通道：RF_DIRECTION=X 时用 X 通道，否则 Y（默认，保持历史行为）。"""
    return "x" if str(getattr(p, "rf_direction", "Y")).upper() == "X" else "y"


def channel_bias_v(p: PulseChainParams, role: str) -> float:
    """该方向的静态偏置；射频通道把它作为正弦 offset 承载，另一方向用 DC。"""
    return float(p.x_bias_v if role == "x" else p.y_bias_v)


def channel_key(role: str) -> str:
    """通道角色对应的映射键与安全限值键。"""
    return X_KEY if role == "x" else RF_KEY


def rf_levels(p: PulseChainParams, role: str | None = None) -> tuple[float, ...]:
    """射频通道需在限内的电平：0、静态偏置与射频叠加后的上下峰值。"""
    role = role or rf_role(p)
    bias = channel_bias_v(p, role)
    return (0.0, bias, bias + p.y_rf_vpp / 2.0, bias - p.y_rf_vpp / 2.0)


def amplifier_safety_check(ctx, preflight_fn):
    """1200B 安全回调：只放行关闭输出与本次参数里的增益/阻抗写入，其余一律拒绝。"""
    def check(operation, value):
        if operation == "output":
            if value is not False:
                preflight_fn(ctx.params, ctx.root)
            return
        if operation not in {"gain_db", "input_impedance_ohms", "output_impedance_ohms"}:
            raise ValueError(f"本实验不修改功放 {operation}")
        if ctx.state.get("amp_output_on"):
            raise ValueError("功放输出开启时不允许修改增益或阻抗；先关闭输出并确认面板")
        preflight_fn(ctx.params, ctx.root)
    return check



def connect(ctx, *, experiment_id, preflight_fn):
    """连接所有设备；失败时断开已连接设备，不留半开会话。"""
    mapping, devices, session = ctx.mapping, ctx.devices, ctx.device_session
    try:
        signals, channels = connect_signal_generator_routes(session, "signals", {
            name: (key, mapping[key]) for name, key in (
                ("z", SOURCE_KEY), ("x", X_KEY), ("y", RF_KEY), ("trigger", TRIGGER_KEY),
                ("temp", TEMP_SWITCH_KEY),
            )
        })
        devices["signals"], devices["channels"] = signals, channels
        lockin_config = mapping[LOCKIN_KEY]
        devices["hf2"] = session.connect(
            "hf2", f"mfli://{lockin_config.get('host')}:{lockin_config.get('port')}/{lockin_config['device_id']}",
            lambda: create_lockin(lockin_config))
        mfli = devices["hf2"]
        if str(mfli.model).upper() != str(lockin_config.get("model", "MFLI")).upper():
            raise ValueError("已连接锁相型号与设备库不一致，禁止启动输出")
        actual_mapping = mapping | {LOCKIN_KEY: lockin_config | {"model": mfli.model,
                                                                "capabilities": mfli.capabilities}}
        require_lockin_compatibility(experiment_id, ctx.params.to_external(), mapping=actual_mapping)
        initialize_lockin_inputs(mfli)
        if amplifier_enabled(ctx.params):
            amp = mapping[AMPLIFIER_KEY]
            devices["amp"] = session.connect("amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
                amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
                safety_check=amplifier_safety_check(ctx, preflight_fn)))
        devices["scope"] = session.connect("scope", str(mapping[SCOPE_KEY]["resource"]),
                                           lambda: create_oscilloscope(mapping[SCOPE_KEY]))
        main_field = mapping[MAIN_FIELD_KEY]
        devices["gs200"] = session.connect("gs200", str(main_field["resource"]),
                                           lambda: GS200Instrument(main_field["resource"]))
        tec_config = mapping[TEC_KEY]
        devices["tec"] = session.connect_optional(
            "tec", tec_config["resource"], lambda: TECInstrument(tec_config["resource"]), device_label="TEC")
        devices["optional_connection_errors"] = session.optional_connection_errors()
    except BaseException:
        session.cleanup_connection_failure()
        raise


def clocks(ctx):
    """DG（三台 Z/Y/温控）与 MFLI 的参考时钟严格回读。"""
    return synchronize_connected_clocks(
        {"signals": ctx.devices["signals"], "hf2": ctx.devices["hf2"]}, ctx.mapping,
        {"signals": SOURCE_KEY, "hf2": LOCKIN_KEY}, settle_wait=sleep)


def configure_chain(ctx):
    """配平 Z 信号源负载；经功放时另写 1200B 增益与阻抗（无回读，写入值即预测依据）。

    DG 直驱时功放已旁路：不改功放、不发 AON，Z 通道按高阻档写入（写入幅度即开路电动势），
    回路电阻额外计入 DG4000 源阻抗，记录电流估算依据。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    signals.set_output(False, channel=channels["z"])
    signals.set_burst_state(False, channel=channels["z"])
    signals.set_mod_state(False, channel=channels["z"])
    if amplifier_enabled(p):
        amp = ctx.devices["amp"]
        set_amp_output(ctx, False)
        if p.amplifier_write_settings:
            amp.set_input_impedance(p.amplifier_input_impedance_ohm)
            amp.set_output_impedance(p.amplifier_output_impedance_ohm)
            amp.set_gain_db(p.amplifier_gain_db)
        load = "INF" if p.amplifier_input_impedance_ohm >= 1e6 else 50
        written = "已写入，未回读" if p.amplifier_write_settings else "未写入，需面板一致"
        ctx.state["amplifier"] = dict(gain_db=p.amplifier_gain_db,
                                      input_impedance_ohm=p.amplifier_input_impedance_ohm,
                                      output_impedance_ohm=p.amplifier_output_impedance_ohm,
                                      source_output_load=load,
                                      written=bool(p.amplifier_write_settings),
                                      loop_resistance_ohm=loop_resistance_ohm(p),
                                      predicted_amps_per_volt=amps_per_drive_volt(p))
        ctx.outputs["功放"] = (f"{p.amplifier_gain_db:g} dB、输入 {p.amplifier_input_impedance_ohm:g} Ω、"
                               f"输出 {p.amplifier_output_impedance_ohm:g} Ω（{written}）")
    else:
        # 直驱必须用高阻档：写入的 Vpp 是开路电动势，真实电流由回路电阻决定。
        load = "INF"
        ctx.state["amplifier"] = dict(enabled=False, gain_db=0.0, source_output_load=load,
                                      loop_resistance_ohm=loop_resistance_ohm(p),
                                      predicted_amps_per_volt=amps_per_drive_volt(p),
                                      estimated_peak_current_ma=estimated_peak_current_ma(p))
        ctx.outputs["功放"] = "DG 直驱（无放大器）"
    signals.set_output_load(load, channel=channels["z"])
    ctx.outputs["Z 通道"] = f"输出 OFF，负载 {load}"


def phase_drift_deg(actual_deg: float, requested_deg: float) -> float:
    """两个角度在圆周上的最小差值（度），用于校验解调相位回读。"""
    return abs(((float(actual_deg) - float(requested_deg) + 180.0) % 360.0) - 180.0)


def configure_lockin(ctx):
    """按参数配置 MFLI 第 0 路解调器：输入量程、参考频率与解调滤波。"""
    p = ctx.params
    mfli = ctx.devices["hf2"]
    demod.configure_signal_input(mfli, SignalInputConfig(input_index=0, range=p.demod0_signal_range_v, diff=False))
    demod.configure_oscillator(mfli, OscillatorConfig(osc_index=0, frequency=p.y_rf_freq_hz))
    # 解调相位由 DEMOD0_PHASE_DEG 给定（默认 0°，与非相位扫描实验的历史行为一致）；
    # 它让测到的相量整体旋转，模量与斜率与之无关，只有最佳读相位随之平移。
    configured_phase = float(getattr(p, "demod0_phase_deg", 0.0))
    config = DemodulatorConfig(demod_index=p.demod0_idx, rate=p.demod0_rate_sa_s, input_channel=0,
                               osc_select=0, time_constant=p.demod0_tc_s, order=p.demod0_order,
                               phase=configured_phase)
    actual_rate = float(demod.configure_demodulator(mfli, config, for_acquisition=True))
    if not math.isfinite(actual_rate) or actual_rate <= 0:
        raise RuntimeError("MFLI 第 0 路解调器的实际采样率无效")
    ctx.state["actual_rates"]["demod0_Sa_s"] = actual_rate
    # 相位回读必须与设定一致：相位本身是扫描自变量时，读错一位整批数据就废了。
    readback_phase = float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/phaseshift"))
    drift = phase_drift_deg(readback_phase, configured_phase)
    if drift > PHASE_READBACK_TOLERANCE_DEG:
        raise RuntimeError(f"MFLI 解调相位回读 {readback_phase:g}° 与设定 {configured_phase:g}° 相差 {drift:.3g}°")
    ctx.state["lockin"] = dict(model=mfli.model, device_id=mfli.device_id, demod_idx=p.demod0_idx,
                               requested_rate_Sa_s=p.demod0_rate_sa_s, actual_rate_Sa_s=actual_rate,
                               time_constant_s=p.demod0_tc_s, order=p.demod0_order,
                               signal_range_v=p.demod0_signal_range_v, rf_frequency_Hz=p.y_rf_freq_hz,
                               readback={node: float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/{node}"))
                                         for node in ("rate", "timeconstant", "phaseshift")},
                               phase_deg=readback_phase, phase_requested_deg=configured_phase,
                               capabilities=dict(mfli.capabilities))
    ctx.outputs["MFLI"] = (f"Demod{p.demod0_idx} 请求 {p.demod0_rate_sa_s:g} Sa/s、TC {p.demod0_tc_s * 1e3:g} ms、"
                           f"参考 {p.y_rf_freq_hz:g} Hz、相位 {readback_phase:g}°（传输率回读 {actual_rate:g} Sa/s）")


def configure_rf(ctx):
    """射频：5 kHz 正弦叠加所在通道的静态偏置 offset，无限 burst 由共同触发启动，相位由 burst 相位决定。

    通道由 ``rf_role`` 按 RF_DIRECTION 决定（默认 Y，与历史行为一致）；静态偏置作为正弦
    offset 一起写入，因此该通道的电平上限是 offset ± vpp/2。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    role = rf_role(p)
    key, bias = channel_key(role), channel_bias_v(p, role)
    for value in rf_levels(p, role):
        validate_safety_limit(key, value, load_safety_limits(ctx.root))
    signals.set_burst_state(False, channel=channels[role])
    signals.set_mod_state(False, channel=channels[role])
    signals.setup_sine(freq=p.y_rf_freq_hz, amplitude=p.y_rf_vpp, offset=float(bias), phase=0.0,
                       channel=channels[role])
    # 射频与 Z 共用 Time_sequence_2：无限 burst 让射频只在第一个触发沿起步，
    # 之后相位由 burst 相位唯一决定，A03 才能只改相位、不动波形。
    signals.set_burst_mode("INFinity", channel=channels[role])
    signals.set_burst_ncycles(BURST_NCYCLES, channel=channels[role])
    signals.set_burst_trigger_source("EXTernal", channel=channels[role])
    signals.set_burst_trigger_slope("POSitive", channel=channels[role])
    signals.set_burst_phase(p.y_rf_phase_deg, channel=channels[role])
    signals.set_burst_state(True, channel=channels[role])
    signals.set_output(True, channel=channels[role])
    ctx.state[f"{role}_bias_v"] = float(bias)
    ctx.outputs[f"{role.upper()} 射频"] = (f"无限 burst 正弦 {p.y_rf_freq_hz:g} Hz、{p.y_rf_vpp:g} Vpp、"
                                           f"offset {bias:+.4f} V、相位 {p.y_rf_phase_deg:g}°")


def set_rf_phase(ctx, phase_deg):
    """按 DirectAW 校相的顺序改射频相位：关 burst → 写 burst 相位 → 开 burst → 开 output。

    不能在输出开启时直接改相位（会打断波形并留下未定义的起始相位）；幅度不动，
    所以只需在重新打开输出前复核射频电平。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    role = rf_role(p)
    key, bias = channel_key(role), channel_bias_v(p, role)
    for value in rf_levels(p, role):
        validate_safety_limit(key, value, load_safety_limits(ctx.root))
    signals.set_output(False, channel=channels[role])
    signals.set_burst_state(False, channel=channels[role])
    signals.set_burst_phase(float(phase_deg), channel=channels[role])
    signals.set_burst_state(True, channel=channels[role])
    signals.set_output(True, channel=channels[role])
    ctx.state["rf_phase_deg"] = float(phase_deg)
    ctx.state["rf_phase_offset_us"] = p.rf_phase_offset_us(phase_deg)
    ctx.outputs[f"{role.upper()} 射频"] = (f"无限 burst 正弦 {p.y_rf_freq_hz:g} Hz、{p.y_rf_vpp:g} Vpp、"
                                           f"offset {bias:+.4f} V、相位 {float(phase_deg):g}°"
                                           f"（{p.rf_phase_offset_us(phase_deg):g} µs）")


def configure_trigger(ctx):
    """Time_sequence_2：与射频同频的方波，作为 Z 通道 burst 的外触发。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, load_safety_limits(ctx.root))
    signals.setup_square(freq=p.y_rf_freq_hz, amplitude=TRIGGER_VPP, offset=TRIGGER_OFFSET_V,
                         dcycle=TRIGGER_DUTY, phase=0.0, channel=channels["trigger"])
    signals.phase_init(channel=channels["trigger"])
    signals.set_output(True, channel=channels["trigger"])
    # 波形切回方波时通道 SYNC 不会自动恢复，需显式打开；主输出与 SYNC 持续到安全收尾之后。
    signals.set_sync_state(True, channel=channels["trigger"])
    ctx.outputs["共同触发"] = f"{p.y_rf_freq_hz:g} Hz 方波（{TRIGGER_VPP:g} Vpp）"


def configure_z_idle(ctx):
    """Z 通道停在 DC 0 V 且关闭输出；A03/A04 上传脉冲串前不会打脉冲。"""
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    validate_safety_limit(SOURCE_KEY, 0.0, load_safety_limits(ctx.root))
    signals.set_burst_state(False, channel=channels["z"])
    signals.set_mod_state(False, channel=channels["z"])
    signals.setup_dc(0.0, channel=channels["z"])
    signals.set_output(False, channel=channels["z"])
    ctx.outputs["Z 通道"] = "DC 0 V，输出 OFF（等待上传脉冲串）"


def configure_dc_bias(ctx, role: str = "x"):
    """把静态偏置 DC 写到指定通道；0 V 时明确关输出，避免沿用上一次的偏置。

    射频所在通道不调用本函数：它的静态偏置作为正弦 offset 由 ``configure_rf`` 一起写。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits = load_safety_limits(ctx.root)
    key, bias = channel_key(role), channel_bias_v(p, role)
    validate_safety_limit(key, 0.0, limits)
    if bias:
        validate_safety_limit(key, bias, limits)
    signals.set_burst_state(False, channel=channels[role])
    signals.set_mod_state(False, channel=channels[role])
    signals.setup_dc(float(bias), channel=channels[role])
    signals.set_output(abs(bias) > 0.0, channel=channels[role])
    ctx.state[f"{role}_bias_v"] = float(bias)
    ctx.outputs[f"{role.upper()} 偏置"] = (f"DC {bias:+.3f} V（输出 ON）" if bias else "0 V（输出 OFF）")


def configure_x_bias(ctx):
    """按 X_BIAS_V 在 X 通道加静态偏置（历史调用点，等价于 ``configure_dc_bias(ctx, "x")``）。"""
    configure_dc_bias(ctx, "x")


def configure_dc_side(ctx):
    """把静态偏置写到非射频的那一路；射频一路的偏置由 ``configure_rf`` 一起写。"""
    configure_dc_bias(ctx, "y" if rf_role(ctx.params) == "x" else "x")


def configure_main_field(ctx, current_ma):
    """按需设置 GS200 主磁场：0 表示关闭输出；非零时按 mA 写电流并回读。

    运行前状态先快照，收尾由 ``shutdown`` 恢复；写入前按 ``main_magnetic_field``
    安全限值校验，回读电流或输出状态不符即 SafetyAbort。
    """
    gs200 = ctx.devices["gs200"]
    if not ctx.mapping[MAIN_FIELD_KEY].get("resource"):
        raise ValueError(f"{MAIN_FIELD_KEY} 缺少 GS200 resource")
    state = snapshot_main_field_state(gs200)
    ctx.main_field_state = state
    before = dict(source_function=state.source_function, current_a=state.current_a,
                  output_on=state.output_on, current_range_a=state.current_range_a,
                  current_limit_a=state.current_limit_a)
    value_ma = float(current_ma)
    if value_ma == 0.0:
        gs200.set_output(False)
        if gs200.get_output():
            raise SafetyAbort("GS200 主磁场输出未关闭；拒绝开始采集")
        ctx.state["main_field"] = dict(before=before, output_after=False,
                                       restored_on_shutdown=True)
        ctx.outputs["主磁场"] = "OFF（已回读）"
        return
    limits = load_safety_limits(ctx.root)
    validate_safety_limit(MAIN_FIELD_KEY, value_ma, limits)
    value_a = value_ma / 1000.0
    limit_a = float(limits[MAIN_FIELD_KEY]["max"]) / 1000.0
    gs200.set_output(False)
    gs200.set_source_function(state.source_function)
    if state.current_limit_a < abs(value_a):
        gs200.set_current_limit(limit_a)
    if state.current_range_a < abs(value_a):
        gs200.set_current_range(0.01)
    gs200.set_current(value_a)
    gs200.set_output(True)
    actual_a = float(gs200.get_current())
    if not gs200.get_output() or abs(actual_a - value_a) > max(1e-6, abs(value_a) * 0.01):
        raise SafetyAbort(f"GS200 主磁场回读不符：设定 {value_ma:g} mA，回读 {actual_a * 1000:g} mA")
    ctx.state["main_field"] = dict(before=before, output_after=True, current_after_ma=value_ma,
                                   restored_on_shutdown=True)
    ctx.outputs["主磁场"] = f"ON {value_ma:+.3f} mA（已回读）"


def disable_main_field(ctx):
    """关闭 GS200 主磁场输出并回读；本实验不需要主场，关闭是 A02 的质量门槛。"""
    configure_main_field(ctx, 0.0)


def enable_temperature_switch(ctx):
    """把温控开关打到开启电平；A03 逐组关闭再恢复。"""
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    set_temperature_switch(signals, True, channel=channels["temp"])
    ctx.outputs["温控"] = "ON（5 V）"


def _unique_source_routes(ctx):
    """按物理设备去重，返回 [(标签, 设备对象)]；路由代理可能跨多台信号源。"""
    signals = ctx.devices.get("signals")
    if signals is None:
        return []
    return [(f"{route.mapping_key} 信号源", route.device) for route in signals.iter_physical_routes()]


def shutdown(ctx):
    """先断线圈电流，再把 Z 归零并关输出，随后恢复温控与 GS200 原状态并断开全部设备。

    DG 直驱时没有 1200B 可关，Z 通道归零即切断线圈电流。
    """
    devices, params = ctx.devices, ctx.params
    if not devices:
        return list(disconnect_device_mapping({str(index): device
                                               for index, device in enumerate(ctx.device_session.unique_devices())}))
    channels = devices.get("channels") or {}
    signals = devices.get("signals")
    # 第一段只关功放：先去掉线圈电流，再让 Z 通道归零。
    first = run_safety_shutdown(
        extra_actions=([ShutdownAction("发送 1200B 关闭命令", lambda: devices["amp"].set_output(False))]
                       if devices.get("amp") else ()),
        preserved_outputs=())
    actions = []
    main_field_state = getattr(ctx, "main_field_state", None)
    if devices.get("gs200") and main_field_state is not None:
        actions.append(ShutdownAction("恢复 GS200 主磁场原状态",
                                      lambda: restore_main_field_state(devices["gs200"], main_field_state)))
    if devices.get("hf2"):
        actions.append(ShutdownAction("停止 MFLI 解调传输",
                                      lambda: demod.set_demod_enable(devices["hf2"], params.demod0_idx, False)))
    if devices.get("scope"):
        actions.append(ShutdownAction("停止 DHO900 触发", devices["scope"].trigger_stop))
    dg_channels = []
    if signals is not None and "z" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["z"], SOURCE_KEY, "Z 线圈驱动", disable_sync=True))
    role = rf_role(ctx.params)
    if signals is not None and "x" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["x"], X_KEY,
                                            "X 射频" if role == "x" else "X 静态偏置"))
    if signals is not None and "y" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["y"], RF_KEY,
                                            "Y 射频" if role == "y" else "Y 静态偏置"))
    disconnect_targets = [
        DisconnectTarget("1200B", devices.get("amp")), DisconnectTarget("MFLI", devices.get("hf2")),
        DisconnectTarget("DHO900", devices.get("scope")), DisconnectTarget("GS200", devices.get("gs200")),
        DisconnectTarget("TEC", devices.get("tec")),
        *[DisconnectTarget(label, device) for label, device in _unique_source_routes(ctx)],
    ]
    second = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=(TemperatureSwitchRestore(signals, channels["temp"])
                            if signals is not None and "temp" in channels else None),
        extra_actions=actions,
        disconnect_targets=disconnect_targets,
        # 时序信号2 是共同触发：保持运行（Z burst 已关闭，不会再有脉冲），与 DirectAW 一致。
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, TRIGGER_KEY))
    ctx.outputs.update({"功放": "OFF（已发送 AOF）" if amplifier_enabled(params) else "DG 直驱（无放大器）",
                        "X 偏置": "OFF，已归零",
                        "Z 通道": "DC 0 V + OFF，burst/sync OFF",
                        "Y 射频": "OFF", "温控": "ON（已恢复）", "MFLI": "已停止解调传输",
                        "DHO900": "STOP，已断开", **({"主磁场": "已恢复原状态"} if main_field_state else {})})
    return first.action_errors + second.action_errors, second.disconnect_errors
