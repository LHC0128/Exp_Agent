"""无硬件预检、设备连接、激励与主磁场处置、安全收尾；导入不连接设备。

Z 通道链路与 ``pi_pulse_calibration`` 完全一致（DG → 1200B → 阻尼电阻 → Z 线圈 → 采样电阻），
差别有两处：Z 信号源由 ``Time_sequence_2`` 外触发做 burst 门控，判据改由 MFLI 第 0 路
解调器的 R 给出。器件操作复用 ``lab_workflows.steps`` 的公共步骤，本模块只保留本实验的
预检、激励配置与收尾清单。
"""
from __future__ import annotations

import math

from aigtek_1200b import Aigtek1200BInstrument
from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from oscilloscope import create_oscilloscope
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin, signal_generator_max_arb_points
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
                      TemperatureSwitchRestore, connect_signal_generator_routes, disconnect_device_mapping,
                      initialize_lockin_inputs, restore_main_field_state, run_safety_shutdown,
                      set_temperature_switch, snapshot_main_field_state, synchronize_connected_clocks)
from ...steps.coil_drive_chain import SafetyAbort, current_limit, set_amp_output, sleep
from .models import ARB_POINTS, PiWidthScanParams
from .run_format import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, EXPERIMENT_ID, LOCKIN_KEY, MAIN_FIELD_KEY,
                         REQUIRED_MAPPING_KEYS, RF_KEY, SCOPE_KEY, SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY,
                         TRIGGER_KEY, X_KEY)

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "amplifier_safety_check",
    "clocks", "configure_chain", "configure_lockin", "configure_rf", "configure_trigger", "configure_x_bias",
    "configure_z_idle", "connect", "current_limit", "disable_main_field", "enable_temperature_switch",
    "plan", "preflight", "set_rf_phase", "shutdown", "sleep",
]

# 任意波点数：与 pi_pulse_calibration 相同，DG4000 的上限正好是 16384 点。
# 任意波播放采样率上限（DG4000 为 500 MSa/s）；用于预检脉冲沿是否被采样率截断。
ARB_SAMPLE_RATE_CHECK = 500e6
# 射频与 Z 都用无限 burst，靠共同触发启动；NCYCles 在 INFinity 下被忽略，写上限值。
BURST_NCYCLES = 50000
# 共同触发的固定接线电平：5 Vpp 方波、2.5 V 偏置、50% 占空比，频率跟随 Y_RF_FREQ_HZ。
TRIGGER_VPP = 5.0
TRIGGER_OFFSET_V = 2.5
TRIGGER_DUTY = 50.0
# 温度开关（Temp_Switch）开/关电平，与 set_temperature_switch 的默认值一致。
TEMP_SWITCH_ON_V = 5.0


def plan(p: PiWidthScanParams) -> dict:
    """计划中的电平、峰值电流与波形参数；预检与执行共用。"""
    vpp = p.pulse_vpp
    return dict(pulse_vpp=vpp,
                pulse_offset_v=0.0,
                pulse_max_v=vpp / 2.0,
                pulse_min_v=-vpp / 2.0,
                pulse_peak_current_a=p.z_peak_current_ma * 1e-3,
                pulse_period_us=p.pulse_period_us,
                arb_sample_rate_sa_s=ARB_POINTS / (p.pulse_period_us * 1e-6))


def amplifier_safety_check(ctx):
    """1200B 安全回调：只放行关闭输出与本次参数里的增益/阻抗写入，其余一律拒绝。"""
    def check(operation, value):
        if operation == "output":
            if value is not False:
                preflight(ctx.params, ctx.root)
            return
        if operation not in {"gain_db", "input_impedance_ohms", "output_impedance_ohms"}:
            raise ValueError(f"本实验不修改功放 {operation}")
        if ctx.state.get("amp_output_on"):
            raise ValueError("功放输出开启时不允许修改增益或阻抗；先关闭输出并确认面板")
        preflight(ctx.params, ctx.root)
    return check


def preflight(params: PiWidthScanParams, root):
    """连接前预检：参数、映射、安全限值与锁相能力；不连接任何设备。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    expected = {
        SOURCE_KEY: ("signal_generator", None),
        X_KEY: ("signal_generator", None),
        RF_KEY: ("signal_generator", None),
        TRIGGER_KEY: ("signal_generator", None),
        TEMP_SWITCH_KEY: ("signal_generator", None),
        LOCKIN_KEY: ("lockin_amplifier", {"MFLI"}),
        AMPLIFIER_KEY: ("power_amplifier", {"1200B"}),
        SCOPE_KEY: ("oscilloscope", {"DHO900", "DHO914", "DHO914S", "DHO924", "DHO924S"}),
        MAIN_FIELD_KEY: ("gs200", None),
        TEC_KEY: ("tec_controller", None),
    }
    for key, (kind, models) in expected.items():
        config = mapping[key]
        if config.get("instrument") != kind or (models and str(config.get("model", "")).upper() not in models):
            raise ValueError(f"{key} 必须映射到 {kind}{'（' + '/'.join(sorted(models)) + '）' if models else ''}")
    limits = load_safety_limits(root)
    current_limit(limits)
    for key in (SOURCE_KEY, X_KEY, RF_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, MAIN_FIELD_KEY):
        rule = limits.get(key) or {}
        if rule.get("min") is None or rule.get("max") is None:
            raise ValueError(f"{key} 缺少明确安全上下限")
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)

    levels = plan(params)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    validate_safety_limit(X_KEY, 0.0, limits)
    validate_safety_limit(X_KEY, params.x_bias_v, limits)
    for value in (0.0, params.y_rf_vpp / 2.0, -params.y_rf_vpp / 2.0):
        validate_safety_limit(RF_KEY, value, limits)
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, limits)
    validate_safety_limit(TEMP_SWITCH_KEY, TEMP_SWITCH_ON_V, limits)
    for current_a in (levels["pulse_peak_current_a"], -levels["pulse_peak_current_a"]):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_a, limits)

    widths = params.widths_us()
    duty = 2.0 * max(widths) / params.pulse_period_us
    resistor = max(params.sense_resistor_ohm, params.damping_resistor_ohm)
    power = levels["pulse_peak_current_a"] ** 2 * duty * resistor
    if power > params.resistor_rated_w / 2:
        raise ValueError(f"电阻平均功耗 {power:.3g} W 超过额定功率 {params.resistor_rated_w:g} W 的一半")
    if levels["arb_sample_rate_sa_s"] > ARB_SAMPLE_RATE_CHECK:
        raise ValueError(f"任意波采样率 {levels['arb_sample_rate_sa_s']:.3g} Sa/s 超过信号源上限")

    source = mapping[SOURCE_KEY]
    capabilities = source.get("capabilities") or {}
    if not capabilities.get("supports_arbitrary", False):
        raise ValueError(f"{SOURCE_KEY} 当前设备缺少任意波能力 supports_arbitrary")
    if not capabilities.get("supports_infinite_burst", False):
        raise ValueError(f"{SOURCE_KEY} 当前设备缺少 burst 能力 supports_infinite_burst")
    if signal_generator_max_arb_points(source) < ARB_POINTS:
        raise ValueError(f"{SOURCE_KEY} 的任意波点数上限小于 {ARB_POINTS} 点")
    return params, mapping


def connect(ctx):
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
        require_lockin_compatibility(EXPERIMENT_ID, ctx.params.to_external(), mapping=actual_mapping)
        initialize_lockin_inputs(mfli)
        amp = mapping[AMPLIFIER_KEY]
        devices["amp"] = session.connect("amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
            amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
            safety_check=amplifier_safety_check(ctx)))
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
    """写 1200B 增益与阻抗、配平信号源负载；1200B 无回读，写入值即预测依据。"""
    p = ctx.params
    signals, channels, amp = ctx.devices["signals"], ctx.devices["channels"], ctx.devices["amp"]
    signals.set_output(False, channel=channels["z"])
    signals.set_burst_state(False, channel=channels["z"])
    signals.set_mod_state(False, channel=channels["z"])
    set_amp_output(ctx, False)
    if p.amplifier_write_settings:
        amp.set_input_impedance(p.amplifier_input_impedance_ohm)
        amp.set_output_impedance(p.amplifier_output_impedance_ohm)
        amp.set_gain_db(p.amplifier_gain_db)
    load = "INF" if p.amplifier_input_impedance_ohm >= 1e6 else 50
    signals.set_output_load(load, channel=channels["z"])
    written = "已写入，未回读" if p.amplifier_write_settings else "未写入，需面板一致"
    ctx.state["amplifier"] = dict(gain_db=p.amplifier_gain_db,
                                  input_impedance_ohm=p.amplifier_input_impedance_ohm,
                                  output_impedance_ohm=p.amplifier_output_impedance_ohm,
                                  source_output_load=load,
                                  written=bool(p.amplifier_write_settings),
                                  loop_resistance_ohm=p.loop_resistance_ohm,
                                  predicted_amps_per_volt=p.amps_per_drive_volt)
    ctx.outputs["功放"] = (f"{p.amplifier_gain_db:g} dB、输入 {p.amplifier_input_impedance_ohm:g} Ω、"
                           f"输出 {p.amplifier_output_impedance_ohm:g} Ω（{written}）")
    ctx.outputs["Z 通道"] = f"输出 OFF，负载 {load}"


def configure_lockin(ctx):
    """按参数配置 MFLI 第 0 路解调器：输入量程、参考频率与解调滤波。"""
    p = ctx.params
    mfli = ctx.devices["hf2"]
    demod.configure_signal_input(mfli, SignalInputConfig(input_index=0, range=p.demod0_signal_range_v, diff=False))
    demod.configure_oscillator(mfli, OscillatorConfig(osc_index=0, frequency=p.y_rf_freq_hz))
    config = DemodulatorConfig(demod_index=p.demod0_idx, rate=p.demod0_rate_sa_s, input_channel=0,
                               osc_select=0, time_constant=p.demod0_tc_s, order=p.demod0_order)
    actual_rate = float(demod.configure_demodulator(mfli, config, for_acquisition=True))
    if not math.isfinite(actual_rate) or actual_rate <= 0:
        raise RuntimeError("MFLI 第 0 路解调器的实际采样率无效")
    ctx.state["actual_rates"]["demod0_Sa_s"] = actual_rate
    # 解调相位沿用库默认行为（configure_demodulator 按 DemodulatorConfig 的默认值写入），
    # 本实验不另外设置：读出的模量 R 与参考相位无关，θ 的绝对值依赖它、但不参与判据。
    # 离散档位由设备取整，只记录回读值作为诊断，不作为启动门槛。
    ctx.state["lockin"] = dict(model=mfli.model, device_id=mfli.device_id, demod_idx=p.demod0_idx,
                               requested_rate_Sa_s=p.demod0_rate_sa_s, actual_rate_Sa_s=actual_rate,
                               time_constant_s=p.demod0_tc_s, order=p.demod0_order,
                               signal_range_v=p.demod0_signal_range_v, rf_frequency_Hz=p.y_rf_freq_hz,
                               readback={node: float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/{node}"))
                                         for node in ("rate", "timeconstant", "phaseshift")},
                               phase_deg=float(mfli.get_double(f"{mfli.demod_path(p.demod0_idx)}/phaseshift")),
                               capabilities=dict(mfli.capabilities))
    ctx.outputs["MFLI"] = (f"Demod{p.demod0_idx} 请求 {p.demod0_rate_sa_s:g} Sa/s、TC {p.demod0_tc_s * 1e3:g} ms、"
                           f"参考 {p.y_rf_freq_hz:g} Hz（传输率回读 {actual_rate:g} Sa/s）")


def configure_rf(ctx):
    """Y 向射频：5 kHz 正弦，无限 burst 由共同触发启动，相位由 burst 相位决定。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    for value in (0.0, p.y_rf_vpp / 2.0, -p.y_rf_vpp / 2.0):
        validate_safety_limit(RF_KEY, value, load_safety_limits(ctx.root))
    signals.set_burst_state(False, channel=channels["y"])
    signals.set_mod_state(False, channel=channels["y"])
    signals.setup_sine(freq=p.y_rf_freq_hz, amplitude=p.y_rf_vpp, offset=0.0, phase=0.0, channel=channels["y"])
    # 射频与 Z 共用 Time_sequence_2：无限 burst 让射频只在第一个触发沿起步，
    # 之后相位由 burst 相位唯一决定，A03 才能只改相位、不动波形。
    signals.set_burst_mode("INFinity", channel=channels["y"])
    signals.set_burst_ncycles(BURST_NCYCLES, channel=channels["y"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["y"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["y"])
    signals.set_burst_phase(p.y_rf_phase_deg, channel=channels["y"])
    signals.set_burst_state(True, channel=channels["y"])
    signals.set_output(True, channel=channels["y"])
    ctx.outputs["Y 射频"] = (f"无限 burst 正弦 {p.y_rf_freq_hz:g} Hz、{p.y_rf_vpp:g} Vpp、"
                             f"相位 {p.y_rf_phase_deg:g}°")


def set_rf_phase(ctx, phase_deg):
    """按 DirectAW 校相的顺序改射频相位：关 burst → 写 burst 相位 → 开 burst → 开 output。

    不能在输出开启时直接改相位（会打断波形并留下未定义的起始相位）；幅度不动，
    所以只需在重新打开输出前复核射频电平。
    """
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    for value in (0.0, p.y_rf_vpp / 2.0, -p.y_rf_vpp / 2.0):
        validate_safety_limit(RF_KEY, value, load_safety_limits(ctx.root))
    signals.set_output(False, channel=channels["y"])
    signals.set_burst_state(False, channel=channels["y"])
    signals.set_burst_phase(float(phase_deg), channel=channels["y"])
    signals.set_burst_state(True, channel=channels["y"])
    signals.set_output(True, channel=channels["y"])
    ctx.state["rf_phase_deg"] = float(phase_deg)
    ctx.state["rf_phase_offset_us"] = p.rf_phase_offset_us(phase_deg)
    ctx.outputs["Y 射频"] = (f"无限 burst 正弦 {p.y_rf_freq_hz:g} Hz、{p.y_rf_vpp:g} Vpp、"
                             f"相位 {float(phase_deg):g}°（{p.rf_phase_offset_us(phase_deg):g} µs）")


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


def configure_x_bias(ctx):
    """按 X_BIAS_V 在 X 通道加静态偏置；0 V 时明确关输出，避免沿用上一次的偏置。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    limits = load_safety_limits(ctx.root)
    validate_safety_limit(X_KEY, 0.0, limits)
    validate_safety_limit(X_KEY, p.x_bias_v, limits)
    signals.set_burst_state(False, channel=channels["x"])
    signals.set_mod_state(False, channel=channels["x"])
    signals.setup_dc(float(p.x_bias_v), channel=channels["x"])
    signals.set_output(abs(p.x_bias_v) > 0.0, channel=channels["x"])
    ctx.state["x_bias_v"] = float(p.x_bias_v)
    ctx.outputs["X 偏置"] = (f"DC {p.x_bias_v:+.3f} V（输出 ON）" if p.x_bias_v else "0 V（输出 OFF）")


def disable_main_field(ctx):
    """关闭 GS200 主磁场输出并回读；本实验不需要主场，关闭是 A02 的质量门槛。"""
    gs200 = ctx.devices["gs200"]
    if not ctx.mapping[MAIN_FIELD_KEY].get("resource"):
        raise ValueError(f"{MAIN_FIELD_KEY} 缺少 GS200 resource")
    state = snapshot_main_field_state(gs200)
    ctx.main_field_state = state
    gs200.set_output(False)
    if gs200.get_output():
        raise SafetyAbort("GS200 主磁场输出未关闭；拒绝开始采集")
    ctx.state["main_field"] = dict(before=dict(source_function=state.source_function,
                                               current_a=state.current_a, output_on=state.output_on,
                                               current_range_a=state.current_range_a,
                                               current_limit_a=state.current_limit_a),
                                   output_after=False, restored_on_shutdown=True)
    ctx.outputs["主磁场"] = "OFF（已回读）"


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
    """先断线圈电流，再把 Z 归零并关输出，随后恢复温控与 GS200 原状态并断开全部设备。"""
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
    if signals is not None and "x" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["x"], X_KEY, "X 静态偏置"))
    if signals is not None and "y" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["y"], RF_KEY, "Y 射频"))
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
    ctx.outputs.update({"功放": "OFF（已发送 AOF）", "X 偏置": "OFF，已归零",
                        "Z 通道": "DC 0 V + OFF，burst/sync OFF",
                        "Y 射频": "OFF", "温控": "ON（已恢复）", "MFLI": "已停止解调传输",
                        "DHO900": "STOP，已断开", **({"主磁场": "已恢复原状态"} if main_field_state else {})})
    return first.action_errors + second.action_errors, second.disconnect_errors
