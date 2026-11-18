"""无硬件预检、设备连接、X/Y 直流偏置、脉冲串与安全收尾；导入不连接设备。

与 ``pi_width_scan`` 的差别有三处：
- 不配置 Y 向射频；Y 通道与 X 通道一起作为静态横向场线圈出 DC；
- MFLI 用两个解调器：第 0 路谐波 1 读 5 kHz，第 1 路谐波 2 读 10 kHz，共用振荡器 0；
- 判据是 10 kHz 复振幅的零点，不再扫脉宽或射频相位。
"""
from __future__ import annotations

import math

from aigtek_1200b import Aigtek1200BInstrument
from gs200 import GS200Instrument
from lockin_amplifier import DemodulatorConfig, OscillatorConfig, SignalInputConfig, demod
from tec_controller import TECInstrument

from ...common import load_mapping, load_safety_limits, validate_safety_limit
from ...devices import create_lockin, signal_generator_max_arb_points
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...steps import (DGChannelShutdown, DisconnectTarget, ShutdownAction, STANDARD_PRESERVED_OUTPUTS,
                      TemperatureSwitchRestore, configure_fixed_dc_field, connect_signal_generator_routes,
                      disconnect_device_mapping, initialize_lockin_inputs, restore_main_field_state,
                      run_safety_shutdown, set_temperature_switch, snapshot_main_field_state,
                      synchronize_connected_clocks)
from ...steps.coil_drive_chain import SafetyAbort, current_limit, set_amp_output, sleep
from . import z_waveform
from .models import ARB_POINTS, BperpNullParams
from .run_format import (AMPLIFIER_KEY, CURRENT_LIMIT_KEY, EXPERIMENT_ID, LOCKIN_KEY, MAIN_FIELD_KEY,
                         REQUIRED_MAPPING_KEYS, SOURCE_KEY, TEC_KEY, TEMP_SWITCH_KEY, TRIGGER_KEY, X_KEY, Y_KEY)

__all__ = [
    "ARB_POINTS", "ARB_SAMPLE_RATE_CHECK", "BURST_NCYCLES", "SafetyAbort", "amplifier_safety_check",
    "arm_pulse", "clocks", "configure_bias", "configure_chain", "configure_lockin", "configure_trigger",
    "configure_main_field", "configure_z_idle", "connect", "current_limit", "disable_main_field", "enable_temperature_switch",
    "plan", "preflight", "resistor_power_w", "shutdown", "sleep", "z_output_text", "z_wave",
]

ARB_SAMPLE_RATE_CHECK = 500e6
BURST_NCYCLES = 50000
TRIGGER_VPP = 5.0
TRIGGER_OFFSET_V = 2.5
TRIGGER_DUTY = 50.0
TEMP_SWITCH_ON_V = 5.0


def plan(p: BperpNullParams) -> dict:
    vpp = p.pulse_vpp
    return dict(pulse_vpp=vpp,
                pulse_offset_v=0.0,
                pulse_max_v=vpp / 2.0,
                pulse_min_v=-vpp / 2.0,
                pulse_peak_current_a=p.z_peak_current_ma * 1e-3,
                pulse_period_us=p.pulse_period_us,
                arb_sample_rate_sa_s=ARB_POINTS / (p.pulse_period_us * 1e-6))


def z_wave(p: BperpNullParams, root):
    """本轮要上传的一个周期 Z 波形（归一化 ±1）与记录；波形文件在此处做边界校验。"""
    from ..z_chain_stability import processing as pulse_processing
    if not p.z_waveform_from_file:
        wave = pulse_processing.pulse_waveform(p.pulse_period_us, p.z_pulse_width_us, p.z_pulse_edge_us,
                                               points=ARB_POINTS)
        return wave, dict(source="builtin", width_us=p.z_pulse_width_us, edge_us=p.z_pulse_edge_us,
                          mean_square=float((wave ** 2).mean()))
    loaded = z_waveform.load_z_waveform(z_waveform.waveform_path(root, p.z_waveform_file),
                                        points=ARB_POINTS, period_us=p.pulse_period_us)
    if "width_us" in loaded.header:
        file_width = float(loaded.header["width_us"])
        if abs(file_width - p.z_pulse_width_us) > 1e-6:
            raise ValueError(f"Z_PULSE_WIDTH_US={p.z_pulse_width_us:g} µs 与波形文件 width_us={file_width:g} µs 不一致")
    return loaded.values, loaded.record()


def resistor_power_w(p: BperpNullParams, weight: float) -> float:
    """较大电阻上的平均功耗：I_peak² × weight × R；weight 为周期内归一化电流平方的均值（或其上界）。"""
    resistor = max(p.sense_resistor_ohm, p.damping_resistor_ohm)
    return (p.z_peak_current_ma * 1e-3) ** 2 * weight * resistor


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


def _daq_selected(params: BperpNullParams, mapping) -> bool:
    """两路解调器必须在同一 DAQ 采集里同时读；请求速率落在 Scope 区间时预检拒绝。"""
    from lockin_amplifier import acquisition_module, lockin_capabilities
    config = mapping[LOCKIN_KEY]
    caps = lockin_capabilities(str(config.get("model", "MFLI")),
                               (config.get("capabilities") or {}).get("options", []))
    if caps["demodulators"] < 2:
        raise ValueError("MFLI 需要 MF-MD 选件才能同时读 1f 与 2f")
    return acquisition_module(str(config.get("model", "MFLI")), params.demod_rate_sa_s,
                              config.get("acquisition_mode", "auto")) == "daq"


def preflight(params: BperpNullParams, root):
    """连接前预检：参数、映射、安全限值与锁相能力；不连接任何设备。"""
    if errors := params.validate(root):
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    for key in REQUIRED_MAPPING_KEYS:
        if key not in mapping:
            raise ValueError(f"缺少设备映射：{key}")
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    expected = {
        SOURCE_KEY: ("signal_generator", None), X_KEY: ("signal_generator", None),
        Y_KEY: ("signal_generator", None), TRIGGER_KEY: ("signal_generator", None),
        TEMP_SWITCH_KEY: ("signal_generator", None),
        LOCKIN_KEY: ("lockin_amplifier", {"MFLI"}),
        AMPLIFIER_KEY: ("power_amplifier", {"1200B"}),
        MAIN_FIELD_KEY: ("gs200", None), TEC_KEY: ("tec_controller", None),
    }
    for key, (kind, models) in expected.items():
        config = mapping[key]
        if config.get("instrument") != kind or (models and str(config.get("model", "")).upper() not in models):
            raise ValueError(f"{key} 必须映射到 {kind}{'（' + '/'.join(sorted(models)) + '）' if models else ''}")
    limits = load_safety_limits(root)
    current_limit(limits)
    for key in (SOURCE_KEY, X_KEY, Y_KEY, TRIGGER_KEY, TEMP_SWITCH_KEY, MAIN_FIELD_KEY):
        rule = limits.get(key) or {}
        if rule.get("min") is None or rule.get("max") is None:
            raise ValueError(f"{key} 缺少明确安全上下限")
    require_lockin_compatibility(EXPERIMENT_ID, params.to_external(), mapping=mapping)
    if not _daq_selected(params, mapping):
        raise ValueError("DEMOD_RATE 落在 Scope 区间；本实验要求两路解调器走 DAQ 同时采集，请降低采样率")

    levels = plan(params)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    for key in (X_KEY, Y_KEY):
        validate_safety_limit(key, 0.0, limits)
    for _x_mv, _y_mv, x_v, y_v in params.scan_voltages():
        validate_safety_limit(X_KEY, x_v, limits)
        validate_safety_limit(Y_KEY, y_v, limits)
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, limits)
    validate_safety_limit(TEMP_SWITCH_KEY, TEMP_SWITCH_ON_V, limits)
    for current_a in (levels["pulse_peak_current_a"], -levels["pulse_peak_current_a"]):
        validate_safety_limit(CURRENT_LIMIT_KEY, current_a, limits)
    if params.main_field_enabled:
        validate_safety_limit(MAIN_FIELD_KEY, params.main_field_current_ma, limits)

    _wave, wave_record = z_wave(params, root)
    # 内置脉冲沿用按占空比（平顶近似，略偏保守）的原判据；波形文件按实际 mean(v²)。
    weight = (2.0 * params.z_pulse_width_us / params.pulse_period_us if wave_record["source"] == "builtin"
              else wave_record["mean_square"])
    power = resistor_power_w(params, weight)
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


def _connect_labeled(session, label, resource, factory):
    """按设备标签连接；失败时把设备名与资源写进异常，便于定位间歇性 VISA 故障。"""
    try:
        return session.connect(label, resource, factory)
    except BaseException as exc:
        raise RuntimeError(f"{label} 连接失败（{resource}）：{exc}") from exc


def connect(ctx):
    """连接所有设备；失败时断开已连接设备，不留半开会话。"""
    mapping, devices, session = ctx.mapping, ctx.devices, ctx.device_session
    try:
        try:
            signals, channels = connect_signal_generator_routes(session, "signals", {
                name: (key, mapping[key]) for name, key in (
                    ("z", SOURCE_KEY), ("x", X_KEY), ("y", Y_KEY), ("trigger", TRIGGER_KEY),
                    ("temp", TEMP_SWITCH_KEY),
                )
            })
        except BaseException as exc:
            raise RuntimeError(f"信号源路由连接失败：{exc}") from exc
        devices["signals"], devices["channels"] = signals, channels
        lockin_config = mapping[LOCKIN_KEY]
        devices["hf2"] = _connect_labeled(
            session, "hf2", f"mfli://{lockin_config.get('host')}:{lockin_config.get('port')}/{lockin_config['device_id']}",
            lambda: create_lockin(lockin_config))
        mfli = devices["hf2"]
        if str(mfli.model).upper() != str(lockin_config.get("model", "MFLI")).upper():
            raise ValueError("已连接锁相型号与设备库不一致，禁止启动输出")
        actual_mapping = mapping | {LOCKIN_KEY: lockin_config | {"model": mfli.model,
                                                                "capabilities": mfli.capabilities}}
        require_lockin_compatibility(EXPERIMENT_ID, ctx.params.to_external(), mapping=actual_mapping)
        initialize_lockin_inputs(mfli)
        amp = mapping[AMPLIFIER_KEY]
        devices["amp"] = _connect_labeled(session, "amp", str(amp["resource"]), lambda: Aigtek1200BInstrument(
            amp["resource"], serial_number=str((amp.get("connection") or {}).get("serial_number", "0001")),
            safety_check=amplifier_safety_check(ctx)))
        main_field = mapping[MAIN_FIELD_KEY]
        devices["gs200"] = _connect_labeled(session, "gs200", str(main_field["resource"]),
                                            lambda: GS200Instrument(main_field["resource"]))
        tec_config = mapping[TEC_KEY]
        try:
            devices["tec"] = session.connect_optional(
                "tec", tec_config["resource"], lambda: TECInstrument(tec_config["resource"]), device_label="TEC")
        except BaseException as exc:
            raise RuntimeError(f"TEC 连接失败（{tec_config['resource']}）：{exc}") from exc
        devices["optional_connection_errors"] = session.optional_connection_errors()
    except BaseException:
        session.cleanup_connection_failure()
        raise


def clocks(ctx):
    """DG 与 MFLI 的参考时钟严格回读。"""
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
    ctx.state["amplifier"] = dict(gain_db=p.amplifier_gain_db,
                                  input_impedance_ohm=p.amplifier_input_impedance_ohm,
                                  output_impedance_ohm=p.amplifier_output_impedance_ohm,
                                  source_output_load=load)
    ctx.outputs["功放"] = f"{p.amplifier_gain_db:g} dB，Z 通道负载 {load}"


def configure_lockin(ctx):
    """配置 MFLI 两个解调器：共用振荡器 0（5 kHz），第 0 路谐波 1、第 1 路谐波 2。"""
    p = ctx.params
    mfli = ctx.devices["hf2"]
    demod.configure_signal_input(mfli, SignalInputConfig(input_index=0, range=p.demod_signal_range_v, diff=False))
    demod.configure_oscillator(mfli, OscillatorConfig(osc_index=0, frequency=p.pulse_freq_hz))
    rates = {}
    for index, harmonic in ((p.demod0_idx, 1), (p.demod1_idx, p.demod_harmonic_2f)):
        config = DemodulatorConfig(demod_index=index, rate=p.demod_rate_sa_s, input_channel=0, osc_select=0,
                                   harmonic=harmonic, time_constant=p.demod_tc_s, order=p.demod_order,
                                   phase=p.demod_phase_deg)
        actual_rate = float(demod.configure_demodulator(mfli, config, for_acquisition=True))
        if not math.isfinite(actual_rate) or actual_rate <= 0:
            raise RuntimeError(f"MFLI 第 {index} 路解调器的实际采样率无效")
        rates[index] = actual_rate
        ctx.state["actual_rates"][f"demod{index}_Sa_s"] = actual_rate
    osc_freq = float(mfli.get_double(f"{mfli.osc_path(0)}/freq"))
    readback = {}
    for index in (p.demod0_idx, p.demod1_idx):
        base = mfli.demod_path(index)
        readback[index] = dict(harmonic=int(mfli.get_int(f"{base}/harmonic")),
                               rate=float(mfli.get_double(f"{base}/rate")),
                               phaseshift=float(mfli.get_double(f"{base}/phaseshift")))
    if readback[p.demod1_idx]["harmonic"] != 2:
        raise RuntimeError("MFLI 第 1 路解调器的谐波回读不是 2，拒绝继续")
    if abs(osc_freq - p.pulse_freq_hz) > abs(p.pulse_freq_hz) * 1e-6 + 1e-3:
        raise RuntimeError("MFLI 振荡器 0 的频率回读与脉冲频率不一致")
    ctx.state["lockin"] = dict(model=mfli.model, device_id=mfli.device_id, osc0_hz=osc_freq,
                               demod0=p.demod0_idx, demod1=p.demod1_idx, harmonic_2f=2,
                               rate_Sa_s=p.demod_rate_sa_s, time_constant_s=p.demod_tc_s,
                               order=p.demod_order, phase_deg=p.demod_phase_deg,
                               signal_range_v=p.demod_signal_range_v, readback=readback,
                               capabilities=dict(mfli.capabilities))
    ctx.outputs["MFLI"] = (f"Demod{p.demod0_idx} @ {p.pulse_freq_hz:g} Hz（谐波 1）、"
                           f"Demod{p.demod1_idx} @ {p.pulse_freq_hz * 2:g} Hz（谐波 2），"
                           f"TC {p.demod_tc_s * 1e3:g} ms、{p.demod_rate_sa_s:g} Sa/s")


def configure_trigger(ctx):
    """Time_sequence_2：与脉冲同频的方波，作为 Z 通道 burst 的外触发。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    for value in (0.0, TRIGGER_OFFSET_V - TRIGGER_VPP / 2.0, TRIGGER_OFFSET_V + TRIGGER_VPP / 2.0):
        validate_safety_limit(TRIGGER_KEY, value, load_safety_limits(ctx.root))
    signals.setup_square(freq=p.pulse_freq_hz, amplitude=TRIGGER_VPP, offset=TRIGGER_OFFSET_V,
                         dcycle=TRIGGER_DUTY, phase=0.0, channel=channels["trigger"])
    signals.phase_init(channel=channels["trigger"])
    signals.set_output(True, channel=channels["trigger"])
    signals.set_sync_state(True, channel=channels["trigger"])
    ctx.outputs["共同触发"] = f"{p.pulse_freq_hz:g} Hz 方波（{TRIGGER_VPP:g} Vpp）"


def configure_z_idle(ctx):
    """Z 通道停在 DC 0 V 且关闭输出；A03 上传脉冲串前不会打脉冲。"""
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    validate_safety_limit(SOURCE_KEY, 0.0, load_safety_limits(ctx.root))
    signals.set_burst_state(False, channel=channels["z"])
    signals.set_mod_state(False, channel=channels["z"])
    signals.setup_dc(0.0, channel=channels["z"])
    signals.set_output(False, channel=channels["z"])
    ctx.outputs["Z 通道"] = "DC 0 V，输出 OFF（等待上传脉冲串）"


def configure_bias(ctx):
    """按 X_BIAS_MV / Y_BIAS_MV（mV）换算成 V，给 X、Y 线圈加直流偏置；Y 通道不配射频。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    vx = configure_fixed_dc_field(signals, channels["x"], X_KEY, p.x_bias_v)
    vy = configure_fixed_dc_field(signals, channels["y"], Y_KEY, p.y_bias_v)
    ctx.state["x_bias_v"], ctx.state["y_bias_v"] = vx, vy
    ctx.outputs["X 偏置"] = f"DC {vx:+.6f} V（{'ON' if vx else 'OFF'}）"
    ctx.outputs["Y 偏置"] = f"DC {vy:+.6f} V（{'ON' if vy else 'OFF'}）"


def disable_main_field(ctx):
    """关闭 GS200 主磁场输出并回读；残余 Bz 保持原样，不补到零。"""
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


MAIN_FIELD_READBACK_TOL_A = 1e-6


def configure_main_field(ctx):
    """A02 的主磁场：MAIN_FIELD_ENABLED 关闭时关断输出（原行为）；开启时设定直流电流、打开输出并回读。

    两种情况都先快照运行前状态，A04 由 restore_main_field_state 复原。
    """
    p = ctx.params
    if not p.main_field_enabled:
        disable_main_field(ctx)
        return
    gs200 = ctx.devices["gs200"]
    if not ctx.mapping[MAIN_FIELD_KEY].get("resource"):
        raise ValueError(f"{MAIN_FIELD_KEY} 缺少 GS200 resource")
    validate_safety_limit(MAIN_FIELD_KEY, p.main_field_current_ma, load_safety_limits(ctx.root))
    state = snapshot_main_field_state(gs200)
    ctx.main_field_state = state
    gs200.set_output(False)
    gs200.set_current(p.main_field_current_ma / 1000.0)
    readback_a = float(gs200.get_current())
    if abs(readback_a - p.main_field_current_ma / 1000.0) > MAIN_FIELD_READBACK_TOL_A:
        raise SafetyAbort(f"GS200 电流回读 {readback_a * 1e3:.6g} mA 与设定 {p.main_field_current_ma:g} mA 不一致；输出保持关闭")
    gs200.set_output(True)
    if not gs200.get_output():
        raise SafetyAbort("GS200 主磁场输出未能打开；拒绝开始采集")
    ctx.state["main_field"] = dict(before=dict(source_function=state.source_function,
                                               current_a=state.current_a, output_on=state.output_on,
                                               current_range_a=state.current_range_a,
                                               current_limit_a=state.current_limit_a),
                                   set_current_ma=p.main_field_current_ma, readback_current_ma=readback_a * 1e3,
                                   output_after=True, restored_on_shutdown=True)
    ctx.outputs["主磁场"] = f"ON，{p.main_field_current_ma:+g} mA（已回读）"


def enable_temperature_switch(ctx):
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    set_temperature_switch(signals, True, channel=channels["temp"])
    ctx.outputs["温控"] = "ON（5 V）"


def arm_pulse(ctx):
    """上传本轮 Z 任意波（内置 ±π 双脉冲或波形文件）并 arm 无限 burst；打开 1200B 输出。"""
    p = ctx.params
    signals, channels = ctx.devices["signals"], ctx.devices["channels"]
    levels = plan(p)
    limits = load_safety_limits(ctx.root)
    for value in (0.0, levels["pulse_max_v"], levels["pulse_min_v"]):
        validate_safety_limit(SOURCE_KEY, value, limits)
    wave, record = z_wave(p, ctx.root)
    ctx.state["z_waveform"] = record
    signals.set_output(False, channel=channels["z"])
    signals.setup_arbitrary(wave, p.pulse_freq_hz, p.pulse_vpp, 0.0, output=False, channel=channels["z"])
    signals.set_burst_mode("INFinity", channel=channels["z"])
    signals.set_burst_ncycles(BURST_NCYCLES, channel=channels["z"])
    signals.set_burst_trigger_source("EXTernal", channel=channels["z"])
    signals.set_burst_trigger_slope("POSitive", channel=channels["z"])
    signals.set_burst_phase(0.0, channel=channels["z"])
    signals.set_burst_state(True, channel=channels["z"])
    signals.set_output(True, channel=channels["z"])
    set_amp_output(ctx, True)
    ctx.outputs["Z 通道"] = z_output_text(p, record)


def z_output_text(p: BperpNullParams, record: dict) -> str:
    shape = (f"波形文件 {record['file']}" if record.get("source") == "file"
             else f"±π 双脉冲，宽 {p.z_pulse_width_us:g} µs")
    return (f"{shape}连续输出，周期 {p.pulse_period_us:g} µs、峰值 {p.z_peak_current_ma:g} mA、"
            f"外触发 {p.pulse_freq_hz:g} Hz")


def _unique_source_routes(ctx):
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
        actions.append(ShutdownAction("停止 MFLI 第 2 路解调传输",
                                      lambda: demod.set_demod_enable(devices["hf2"], params.demod1_idx, False)))
    dg_channels = []
    if signals is not None and "z" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["z"], SOURCE_KEY, "Z 线圈驱动", disable_sync=True))
    if signals is not None and "x" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["x"], X_KEY, "X 静态偏置"))
    if signals is not None and "y" in channels:
        dg_channels.append(DGChannelShutdown(signals, channels["y"], Y_KEY, "Y 静态偏置"))
    disconnect_targets = [
        DisconnectTarget("1200B", devices.get("amp")), DisconnectTarget("MFLI", devices.get("hf2")),
        DisconnectTarget("GS200", devices.get("gs200")), DisconnectTarget("TEC", devices.get("tec")),
        *[DisconnectTarget(label, device) for label, device in _unique_source_routes(ctx)],
    ]
    second = run_safety_shutdown(
        dg_channels=dg_channels,
        temperature_switch=(TemperatureSwitchRestore(signals, channels["temp"])
                            if signals is not None and "temp" in channels else None),
        extra_actions=actions,
        disconnect_targets=disconnect_targets,
        preserved_outputs=(*STANDARD_PRESERVED_OUTPUTS, TRIGGER_KEY))
    ctx.outputs.update({"功放": "OFF（已发送 AOF）", "X 偏置": "OFF，已归零", "Y 偏置": "OFF，已归零",
                        "Z 通道": "DC 0 V + OFF，burst/sync OFF", "温控": "ON（已恢复）",
                        "MFLI": "已停止解调传输", "Y 射频": "OFF（本实验未使用）",
                        **({"主磁场": "已恢复原状态"} if main_field_state else {})})
    return first.action_errors + second.action_errors, second.disconnect_errors
